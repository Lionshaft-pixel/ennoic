import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import account_store
import app
import billing
import entitlements
import task_store


class EntitlementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.dict(
            os.environ,
            {"ENNOIC_TASK_DB": os.path.join(self.temp_dir.name, "entitlements.sqlite3")},
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
            "message": "Summarize this idea",
            "request_id": "retryable-request-identifier-01",
        }
        with patch("app.process_message_with_metadata", side_effect=[
            RuntimeError("temporary model failure"),
            {"reply": "Recovered", "truncated": False, "task_confirmations": []},
        ]):
            failed = client.post("/api/chat", json=request)
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"], 0)
            retried = client.post("/api/chat", json=request)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(entitlements.account_entitlements(user["id"])["usage"]["ai_requests"]["used"], 1)

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

    def test_checkout_or_client_supplied_plan_does_not_grant_pro(self):
        user = self.create_user()
        client = self.logged_in_client(user)
        response = client.post("/api/checkout", json={"plan": "pro", "paid": True})
        self.assertEqual(response.status_code, 501)
        self.assertEqual(client.get("/api/entitlements?plan=pro").get_json()["plan"], "free")
        with self.assertRaises(billing.PaymentProviderUnavailable):
            billing.subscription_service.create_checkout_session(
                user_id=user["id"], plan="pro", return_url="https://ennoic.example.invalid/pricing"
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
