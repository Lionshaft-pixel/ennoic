import os
import re
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import Mock, patch

import account_store
import app
import billing
import entitlements
import main
import task_store


class EntitlementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.dict(
            os.environ,
            {
                "ENNOIC_TASK_DB": os.path.join(self.temp_dir.name, "entitlements.sqlite3"),
                "PADDLE_ENVIRONMENT": "",
                "PADDLE_CLIENT_TOKEN": "",
                "PADDLE_API_KEY": "",
                "PADDLE_WEBHOOK_SECRET": "",
                "PADDLE_PRICE_ID_SANDBOX": "",
                "PADDLE_PRICE_ID_LIVE": "",
                "PADDLE_LIVE_CHECKOUT_ENABLED": "false",
            },
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        app._initialized_databases.clear()
        app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
        app.limiter.enabled = False
        account_store.initialize()

    def create_user(self, email="plan@example.com"):
        user = account_store.create_user(email, "correct-horse-battery-7")
        self.assertIsNotNone(user)
        account_store.verify_email(user["id"])
        return account_store.get_user(user["id"])

    def logged_in_client(self, user):
        client = app.app.test_client()
        response = client.post(
            "/auth/login",
            data={"email": user["email"], "password": "correct-horse-battery-7"},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def apply_pro_event(self, user, *, period_end=None, event_id="evt-1", event_type="activated"):
        period_end = period_end or (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        class TestProvider:
            name = "test-provider"

            def create_checkout_session(self, **kwargs):
                return {"url": "https://payments.example.invalid/checkout"}

            def verify_event(self, payload, signature):
                return billing.VerifiedSubscriptionEvent(
                    event_id=event_id,
                    user_id=user["id"],
                    event_type=event_type,
                    subscription_id="sub-test",
                    period_start=datetime.now(timezone.utc).isoformat(),
                    period_end=period_end,
                )

            def cancel_subscription(self, *, subscription_id):
                return None

        service = billing.SubscriptionService(TestProvider())
        return service.process_provider_event(b"signed-by-test-provider", "valid-test-signature")

    def test_new_accounts_default_to_free(self):
        user = self.create_user()
        subscription = account_store.get_account_subscription(user["id"])
        view = entitlements.account_entitlements(user["id"])
        self.assertEqual((subscription["plan"], subscription["status"]), ("free", "free"))
        self.assertEqual(view["plan"], "free")
        self.assertEqual(view["usage"]["ai_requests"]["limit"], 40)
        self.assertEqual(view["usage"]["memories"]["limit"], 40)
        self.assertEqual(view["usage"]["conversations"]["limit"], 10)

    def test_free_ai_limit_is_server_enforced_and_retry_is_idempotent(self):
        user = self.create_user()
        client = self.logged_in_client(user)
        conversation = account_store.create_conversation(user["id"], "conversation-1", "AI usage")
        request = {
            "conversation_id": conversation["id"],
            "message": "What should I do next?",
            "request_id": "stable-request-identifier-001",
        }
        with patch.dict(os.environ, {"ENNOIC_FREE_AI_REQUESTS_MONTHLY": "1"}), patch(
            "app.process_message_with_metadata",
            return_value={"reply": "A useful answer", "truncated": False, "task_confirmations": []},
        ) as process:
            first = client.post("/api/chat", json=request)
            retry = client.post("/api/chat", json=request)
            limited = client.post(
                "/api/chat",
                json={**request, "request_id": "another-request-identifier-002"},
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.get_json(), first.get_json())
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()["code"], "ai_usage_limit")
        self.assertEqual(limited.get_json()["upgrade_url"], "/pricing")
        process.assert_called_once()
        self.assertEqual(entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"], 1)

    def test_failed_ai_attempt_releases_usage_and_same_request_can_retry(self):
        user = self.create_user()
        client = self.logged_in_client(user)
        conversation = account_store.create_conversation(user["id"], "retry-chat", "Retry")
        request = {
            "conversation_id": conversation["id"],
            "message": "Summarize this private-marker-should-not-be-logged",
            "request_id": "retryable-request-identifier-01",
        }
        with patch("app.process_message_with_metadata", side_effect=[
            RuntimeError("private-provider-response-marker"),
            {"reply": "Recovered", "truncated": False, "task_confirmations": []},
        ]), patch("app.notify_discord") as notify, self.assertLogs(app.app.logger, level="ERROR") as captured:
            failed = client.post("/api/chat", json=request)
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"], 0)
            retried = client.post("/api/chat", json=request)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], "chat_error")
        self.assertEqual(notify.call_args.kwargs["error_type"], "RuntimeError")
        self.assertNotIn("private-provider-response-marker", str(notify.call_args))
        self.assertNotIn("private-marker-should-not-be-logged", str(notify.call_args))
        self.assertNotIn("private-provider-response-marker", "\n".join(captured.output))
        self.assertNotIn("private-marker-should-not-be-logged", "\n".join(captured.output))
        self.assertIn("error_type=RuntimeError", "\n".join(captured.output))
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"], 1)

    def test_post_classifier_task_validation_error_logs_safe_code_location_and_releases_quota(self):
        user = self.create_user("task-error@example.com")
        client = self.logged_in_client(user)
        conversation = account_store.create_conversation(user["id"], "task-error-chat", "Task error")
        request = {
            "conversation_id": conversation["id"],
            "message": "Create a task to review this private-task-marker",
            "request_id": "task-validation-error-request-01",
        }
        classifier_response = (
            '{"action":"create","tasks":[{"title":"Review the release",'
            '"due_date":"not-a-date"}]}'
        )
        trace_output = StringIO()
        with patch("task_management.ask_cloud_model", return_value=classifier_response), self.assertLogs(
            app.app.logger, level="ERROR"
        ) as captured, redirect_stdout(trace_output):
            response = client.post("/api/chat", json=request)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"],
            0,
        )
        logs = "\n".join(captured.output)
        traces = trace_output.getvalue()
        self.assertIn("error_type=ValueError", logs)
        self.assertRegex(logs, r"error_location=task_store\.py:_validate_date:\d+")
        self.assertNotIn("private-task-marker", logs)
        self.assertNotIn(classifier_response, logs)
        self.assertIn("error_location=task_store.py:_validate_date:", traces)
        self.assertNotIn("private-task-marker", traces)
        self.assertNotIn(classifier_response, traces)

    def test_chat_task_update_accepts_classifier_deadline_alias(self):
        user = self.create_user("deadline-alias@example.com")
        task = task_store.create_task(
            {"title": "Review the launch plan", "due_date": "2026-11-28"},
            user_id=user["id"],
        )
        client = self.logged_in_client(user)
        conversation = account_store.create_conversation(user["id"], "deadline-alias-chat", "Update task")
        request = {
            "conversation_id": conversation["id"],
            "message": "Move the launch plan deadline to December 1.",
            "request_id": "deadline-alias-request-identifier",
        }
        classifier_response = (
            '{"action":"update","target":"Review the launch plan",'
            '"updates":{"deadline":"2026-12-01"}}'
        )
        with patch("task_management.ask_cloud_model", return_value=classifier_response):
            response = client.post("/api/chat", json=request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Updated", response.get_json()["reply"])
        self.assertEqual(task_store.get_task(task["id"], user_id=user["id"])["due_date"], "2026-12-01")
        self.assertEqual(
            entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"],
            1,
        )

    def test_invalid_classifier_update_field_returns_clarification_without_mutation(self):
        user = self.create_user("invalid-task-update@example.com")
        task = task_store.create_task(
            {"title": "Review the launch plan", "due_date": "2026-11-28"},
            user_id=user["id"],
        )
        client = self.logged_in_client(user)
        conversation = account_store.create_conversation(user["id"], "invalid-update-chat", "Update task")
        request = {
            "conversation_id": conversation["id"],
            "message": "Change the status of the launch plan task.",
            "request_id": "invalid-task-update-request-001",
        }
        classifier_response = (
            '{"action":"update","target":"Review the launch plan",'
            '"updates":{"status":"completed"}}'
        )
        with patch("task_management.ask_cloud_model", return_value=classifier_response):
            response = client.post("/api/chat", json=request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("couldn't determine a valid task change", response.get_json()["reply"])
        self.assertEqual(task_store.get_task(task["id"], user_id=user["id"])["status"], "open")
        self.assertEqual(
            entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"],
            1,
        )

    def test_ordinary_chat_request_bypasses_task_classifier(self):
        user = self.create_user("ordinary-chat@example.com")
        client = self.logged_in_client(user)
        conversation = account_store.create_conversation(user["id"], "ordinary-chat", "Ordinary chat")
        request = {
            "conversation_id": conversation["id"],
            "message": "Can you help me plan my day?",
            "request_id": "ordinary-chat-request-identifier",
        }
        with patch("task_management.ask_cloud_model") as task_classifier, patch(
            "main.ask_cloud_model",
            return_value={"content": "Let's plan the day together.", "finish_reason": "stop"},
        ) as chat_model:
            response = client.post("/api/chat", json=request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "Let's plan the day together.")
        task_classifier.assert_not_called()
        chat_model.assert_called_once()
        self.assertEqual(
            entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"],
            1,
        )

    def test_ai_answer_is_not_written_to_stdout(self):
        secret_answer = "private-ai-answer-marker"
        stdout = StringIO()
        with patch("main.maybe_handle_task_message", return_value=None), patch(
            "main.ask_cloud_model",
            return_value={"content": secret_answer, "finish_reason": "stop"},
        ), redirect_stdout(stdout):
            result = main.process_message_with_metadata(
                "Can you help me plan a weekend?", user_id="test-user"
            )
        self.assertEqual(result["reply"], secret_answer)
        self.assertNotIn(secret_answer, stdout.getvalue())

    def test_deleting_conversation_removes_cached_ai_response_but_keeps_usage(self):
        user = self.create_user()
        conversation = account_store.create_conversation(user["id"], "cache-delete", "Cache delete")
        request_id = "cached-request-identifier-123"
        period_start = "2026-11-01T00:00:00+00:00"
        reserved = account_store.reserve_ai_request(
            user["id"], request_id, "cached-hash", period_start,
            "2026-12-01T00:00:00+00:00", 40, conversation_id=conversation["id"],
        )
        self.assertEqual(reserved["state"], "reserved")
        account_store.complete_ai_request(user["id"], request_id, {"reply": "sensitive-cached-reply"})
        self.assertEqual(account_store.get_usage(user["id"], "ai_requests", period_start), 1)

        self.assertTrue(account_store.delete_conversation(user["id"], conversation["id"]))

        with account_store.connection() as db:
            row = db.execute(
                "SELECT response_json FROM ai_requests WHERE user_id = ? AND request_id = ?",
                (user["id"], request_id),
            ).fetchone()
        self.assertIsNone(row)
        self.assertEqual(account_store.get_usage(user["id"], "ai_requests", period_start), 1)

    def test_legacy_unassociated_ai_cache_is_cleared_without_changing_usage(self):
        user = self.create_user()
        period_start = "2026-11-01T00:00:00+00:00"
        account_store.reserve_ai_request(
            user["id"], "orphan-cache-request-01", "orphan-hash", period_start,
            "2026-12-01T00:00:00+00:00", 40,
        )
        account_store.complete_ai_request(
            user["id"], "orphan-cache-request-01", {"reply": "old-sensitive-cache"}
        )
        account_store.initialize()
        with account_store.connection() as db:
            row = db.execute(
                "SELECT response_json, state FROM ai_requests WHERE user_id = ? AND request_id = ?",
                (user["id"], "orphan-cache-request-01"),
            ).fetchone()
        self.assertIsNone(row["response_json"])
        self.assertEqual(row["state"], "completed")
        self.assertEqual(account_store.get_usage(user["id"], "ai_requests", period_start), 1)

    def test_memory_limit_blocks_new_entries_but_preserves_and_updates_existing(self):
        user = self.create_user()
        with patch.dict(os.environ, {"ENNOIC_FREE_MEMORY_CAPACITY": "1"}):
            self.assertEqual(account_store.remember(user["id"], "Ari", "keys", "today"), "Stored new memory")
            self.assertEqual(account_store.remember(user["id"], "Ari", "keys", "tomorrow"), "Updated existing memory")
            with self.assertRaises(account_store.MemoryCapacityReached):
                account_store.remember(user["id"], "Bo", "badge", "Friday", max_memories=1)
        self.assertEqual(account_store.list_memories(user["id"]), [{
            "id": account_store.list_memories(user["id"])[0]["id"],
            "person": "Ari",
            "object": "keys",
            "time": "tomorrow",
        }])

    def test_concurrent_ai_reservations_cannot_consume_the_last_quota_slot_more_than_once(self):
        user = self.create_user()
        period_start = "2026-11-01T00:00:00+00:00"
        period_end = "2026-12-01T00:00:00+00:00"
        quota = 5
        for index in range(quota - 1):
            result = account_store.reserve_ai_request(
                user["id"], f"prior-request-{index}", f"hash-{index}",
                period_start, period_end, quota,
            )
            self.assertEqual(result["state"], "reserved")

        workers = 8
        start = threading.Barrier(workers)

        def reserve(index):
            start.wait(timeout=5)
            return account_store.reserve_ai_request(
                user["id"], f"parallel-request-{index}", f"parallel-hash-{index}",
                period_start, period_end, quota,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(reserve, range(workers)))

        self.assertEqual(sum(result["state"] == "reserved" for result in results), 1)
        self.assertEqual(sum(result["state"] == "limit" for result in results), workers - 1)
        self.assertEqual(account_store.get_usage(user["id"], "ai_requests", period_start), quota)

    def test_concurrent_memory_creation_cannot_exceed_account_capacity(self):
        user = self.create_user()
        workers = 8
        start = threading.Barrier(workers)

        def remember(index):
            start.wait(timeout=5)
            try:
                return account_store.remember(
                    user["id"], f"Person {index}", f"fact {index}", "today", max_memories=1,
                )
            except account_store.MemoryCapacityReached:
                return "limit"

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(remember, range(workers)))

        self.assertEqual(results.count("Stored new memory"), 1)
        self.assertEqual(results.count("limit"), workers - 1)
        self.assertEqual(account_store.count_memories(user["id"]), 1)

    def test_postgres_write_guard_locks_only_the_target_users_row(self):
        db = Mock()
        with patch("account_store.database_layer.uses_postgres", return_value=True):
            account_store._begin_user_write(db, "target-user")

        db.execute.assert_any_call("BEGIN IMMEDIATE")
        db.execute.assert_any_call(
            "SELECT id FROM users WHERE id = ? FOR UPDATE", ("target-user",)
        )

    def test_legacy_memory_import_uses_the_same_account_write_guard(self):
        user = self.create_user()
        with patch.object(
            account_store,
            "_begin_user_write",
            wraps=account_store._begin_user_write,
        ) as begin_user_write:
            imported = account_store.import_legacy_data(user["id"], [{
                "person": "Ari", "object": "badge", "time": "Friday",
            }])

        begin_user_write.assert_called_once()
        self.assertEqual(begin_user_write.call_args.args[1], user["id"])
        self.assertEqual(imported["memories"], 1)
        self.assertEqual(account_store.count_memories(user["id"]), 1)

    def test_memory_limit_response_is_actionable_and_keeps_saved_memory(self):
        user = self.create_user()
        account_store.remember(user["id"], "Ari", "keys", "tomorrow")
        client = self.logged_in_client(user)
        conversation = account_store.create_conversation(user["id"], "memory-limit-chat", "Memory")
        with patch.dict(os.environ, {"ENNOIC_FREE_MEMORY_CAPACITY": "1"}), patch(
            "app.process_message_with_metadata",
            side_effect=account_store.MemoryCapacityReached(1, 1),
        ) as process:
            response = client.post("/api/chat", json={
                "conversation_id": conversation["id"],
                "message": "Remember a new fact",
                "request_id": "memory-request-identifier-01",
            })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "memory_capacity_limit")
        self.assertEqual(response.get_json()["upgrade_url"], "/pricing")
        process.assert_called_once_with(
            "Remember a new fact",
            [],
            conversation["id"],
            user_id=user["id"],
            max_memories=1,
        )
        self.assertEqual(len(account_store.list_memories(user["id"])), 1)

    def test_pricing_uses_central_limits_and_localizes_without_advertising_unavailable_features(self):
        client = app.app.test_client()
        page = client.get("/pricing")
        spanish = client.get("/api/pricing?lang=es").get_json()
        french = client.get("/api/pricing?lang=fr").get_json()
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"account-summary", page.data)
        self.assertEqual(spanish["prices"]["INR"]["display"], "₹179")
        self.assertEqual(spanish["prices"]["USD"]["display"], "$4.99")
        self.assertEqual(spanish["language"], "es")
        self.assertEqual(french["language"], "fr")
        self.assertFalse(spanish["checkout_available"])
        self.assertTrue(all("automation runs" not in item.lower() for item in spanish["features"]["pro"]))
        self.assertTrue(all("file" not in item.lower() for item in spanish["features"]["pro"]))

    def test_pricing_page_loads_notice_hidden_until_existing_region_data_resolves(self):
        client = app.app.test_client()
        page_response = client.get("/pricing")
        page = page_response.get_data(as_text=True)
        india_pricing = client.get("/api/pricing", headers={"Accept-Language": "en-IN,en;q=0.9"}).get_json()
        international_pricing = client.get(
            "/api/pricing", headers={"Accept-Language": "en-US,en;q=0.9"}
        ).get_json()

        self.assertIn('<details class="upi-notice" id="india-upi-autopay-notice" hidden>', page)
        self.assertIn("₹179/month with UPI AutoPay", page)
        self.assertIn(
            "Your subscription is ₹179/month. UPI payments are processed by Paddle. "
            "Your UPI app may display 'Paddlecom' and a maximum authorization limit of ₹15,000. "
            "This is not an upfront ₹15,000 charge.",
            page,
        )
        self.assertLess(page.index('id="upgrade"'), page.index('id="india-upi-autopay-notice"'))
        self.assertIn("/static/pricing-region.js", page)
        self.assertEqual(india_pricing["selected_currency"], "INR")
        self.assertEqual(international_pricing["selected_currency"], "USD")

        region_script = re.search(
            r'<script src="/static/pricing-region\.js" nonce="([^"]+)"></script>', page
        )
        self.assertIsNotNone(region_script)
        csp = page_response.headers["Content-Security-Policy"]
        self.assertIn(f"'nonce-{region_script.group(1)}'", csp)
        self.assertIn("'strict-dynamic'", csp)

    def test_verified_provider_event_grants_configured_pro_entitlements(self):
        user = self.create_user()
        with patch.dict(os.environ, {
            "ENNOIC_PRO_AI_REQUESTS_MONTHLY": "7",
            "ENNOIC_PRO_MEMORY_CAPACITY": "250",
            "ENNOIC_PRO_CONVERSATION_HISTORY": "0",
        }):
            self.assertTrue(self.apply_pro_event(user))
            view = entitlements.account_entitlements(user["id"])
        self.assertEqual(view["plan"], "pro")
        self.assertEqual(view["usage"]["ai_requests"]["limit"], 7)
        self.assertEqual(view["usage"]["memories"]["limit"], 250)
        self.assertIsNone(view["usage"]["conversations"]["limit"])
        self.assertFalse(self.apply_pro_event(user))  # duplicate verified event is idempotent

    def test_pro_badge_and_account_plan_refresh_from_authenticated_entitlements(self):
        user = self.create_user()
        client = self.logged_in_client(user)

        free_home = client.get("/").get_data(as_text=True)
        self.assertIn('id="account-pro-badge"', free_home)
        self.assertRegex(free_home, r'id="account-pro-badge"[^>]*hidden')
        free_settings = client.get("/settings").get_data(as_text=True)
        self.assertIn("Free Plan", free_settings)
        self.assertNotIn("Ennoic Pro</h3>", free_settings)
        self.assertEqual(client.get("/api/entitlements").get_json()["plan"], "free")

        self.assertTrue(self.apply_pro_event(user))
        pro_home = client.get("/").get_data(as_text=True)
        self.assertRegex(pro_home, r'id="account-pro-badge"[^>]*>PRO</span>')
        pro_settings = client.get("/settings").get_data(as_text=True)
        self.assertIn("Ennoic Pro</h3>", pro_settings)
        self.assertIn(">Active</span>", pro_settings)
        self.assertEqual(client.get("/api/entitlements").get_json()["plan"], "pro")

    def test_checkout_or_client_supplied_plan_does_not_grant_pro(self):
        user = self.create_user()
        client = self.logged_in_client(user)
        response = client.post("/api/checkout", json={"plan": "pro", "paid": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(client.get("/api/entitlements?plan=pro").get_json()["plan"], "free")
        with self.assertRaises(billing.PaymentProviderUnavailable):
            billing.subscription_service.create_checkout_session(
                user_id=user["id"], plan="pro", return_url="https://ennoic.example.invalid/pricing",
                contact={"full_name": "Test User", "email": "test@example.com", "phone": "1234567890"},
            )

    def test_unverified_provider_event_cannot_grant_pro(self):
        user = self.create_user()

        class UnverifiedProvider:
            name = "unverified"

            def verify_event(self, payload, signature):
                return {"event": "activated", "plan": "pro"}

        service = billing.SubscriptionService(UnverifiedProvider())
        with self.assertRaises(ValueError):
            service.process_provider_event(b"untrusted", None)
        self.assertEqual(entitlements.account_entitlements(user["id"])["plan"], "free")

    def test_usage_resets_in_new_month(self):
        user = self.create_user()
        account_store.reserve_ai_request(
            user["id"], "old-period-request-identifier", "request-hash", "2020-01-01T00:00:00+00:00",
            "2020-02-01T00:00:00+00:00", 100,
        )
        current_start, _ = entitlements.current_period()
        self.assertEqual(account_store.get_usage(user["id"], "ai_requests", "2020-01-01T00:00:00+00:00"), 1)
        self.assertEqual(account_store.get_usage(user["id"], "ai_requests", current_start), 0)

    def test_free_history_is_limited_without_deleting_or_crossing_accounts(self):
        user = self.create_user()
        other = self.create_user("other@example.com")
        old = account_store.create_conversation(user["id"], "old-conversation", "Older chat")
        account_store.create_conversation(user["id"], "new-conversation", "Newer chat")
        own_client = self.logged_in_client(user)
        other_client = self.logged_in_client(other)
        with patch.dict(os.environ, {"ENNOIC_FREE_CONVERSATION_HISTORY": "1"}), patch(
            "app.process_message_with_metadata"
        ) as process:
            history = own_client.get("/api/conversations").get_json()
            blocked = own_client.post("/api/chat", json={
                "conversation_id": old["id"], "message": "Continue this", "request_id": "history-request-id-001",
            })
            foreign = other_client.post("/api/chat", json={
                "conversation_id": old["id"], "message": "Read it", "request_id": "foreign-request-id-001",
            })
        self.assertEqual(len(history["conversations"]), 1)
        self.assertTrue(history["has_more"])
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.get_json()["code"], "conversation_history_limit")
        self.assertEqual(foreign.status_code, 404)
        process.assert_not_called()
        self.assertIsNotNone(account_store.get_conversation(user["id"], old["id"]))
        self.apply_pro_event(user, event_id="history-upgrade")
        restored = own_client.get("/api/conversations").get_json()
        self.assertEqual(len(restored["conversations"]), 2)
        self.assertFalse(restored["has_more"])

    def test_expiration_reverts_to_free_without_deleting_account_data(self):
        user = self.create_user()
        conversation = account_store.create_conversation(user["id"], "kept-conversation", "Saved history")
        account_store.remember(user["id"], "Ari", "keys", "tomorrow")
        task_store.create_task({"title": "Keep this task"}, user_id=user["id"])
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.apply_pro_event(user, period_end=past)
        view = entitlements.account_entitlements(user["id"])
        self.assertEqual(view["plan"], "free")
        self.assertEqual(account_store.get_account_subscription(user["id"])["status"], "expired")
        self.assertIsNotNone(account_store.get_conversation(user["id"], conversation["id"]))
        self.assertEqual(len(account_store.list_memories(user["id"])), 1)
        self.assertEqual(task_store.list_tasks(view="all", user_id=user["id"])[0]["title"], "Keep this task")


if __name__ == "__main__":
    unittest.main()
