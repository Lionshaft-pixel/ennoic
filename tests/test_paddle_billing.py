import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import account_store
import app as app_module
import billing
import entitlements
import paddle_billing


class PaddleBillingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(os.environ, {
            "ENNOIC_TASK_DB": os.path.join(self.temp_dir.name, "paddle.sqlite3"),
            "PADDLE_ENVIRONMENT": "sandbox",
            "PADDLE_CLIENT_TOKEN_SANDBOX": "test_client_token",
            "PADDLE_API_KEY_SANDBOX": "sandbox_api_key",
            "PADDLE_WEBHOOK_SECRET_SANDBOX": "whsec_test_secret",
            "PADDLE_PRICE_ID_SANDBOX": "pri_sandbox_monthly",
            "PADDLE_PRICE_ID_LIVE": "",
            "PADDLE_LIVE_CHECKOUT_ENABLED": "false",
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        app_module._initialized_databases.clear()
        app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
        app_module.limiter.enabled = False
        account_store.initialize()
        self.user = account_store.create_user("paddle@example.com", "correct-horse-battery-7")
        account_store.verify_email(self.user["id"])
        self.user = account_store.get_user(self.user["id"])

    def signed(self, event, *, secret="whsec_test_secret", timestamp=None):
        timestamp = int(time.time()) if timestamp is None else timestamp
        body = json.dumps(event, separators=(",", ":")).encode()
        digest = hmac.new(secret.encode(), str(timestamp).encode() + b":" + body, hashlib.sha256).hexdigest()
        return body, f"ts={timestamp};h1={digest}"

    def subscription_event(self, *, event_id="evt_created", event_type="subscription.created",
                           occurred_at=None, status="active", ends_at=None, intent=None,
                           subscription_id="sub_paddle_test", scheduled_cancel_at=None):
        now = datetime.now(timezone.utc)
        occurred_at = occurred_at or now.isoformat()
        ends_at = ends_at or (now + timedelta(days=30)).isoformat()
        custom_data = {"ennoic_checkout_intent": intent} if intent else None
        data = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": status,
            "items": [{"price": {"id": "pri_sandbox_monthly"}, "quantity": 1}],
            "current_billing_period": {"starts_at": now.isoformat(), "ends_at": ends_at},
            "updated_at": occurred_at,
        }
        if custom_data:
            data["custom_data"] = custom_data
        if scheduled_cancel_at:
            data["scheduled_change"] = {"action": "cancel", "effective_at": scheduled_cancel_at}
        return {"event_id": event_id, "event_type": event_type, "occurred_at": occurred_at, "data": data}

    def live_environment(self, *, checkout_enabled="false"):
        return {
            "PADDLE_ENVIRONMENT": "production",
            "PADDLE_CLIENT_TOKEN_LIVE": "live_client_token",
            "PADDLE_API_KEY_LIVE": "live_api_key",
            "PADDLE_WEBHOOK_SECRET_LIVE": "whsec_live_secret",
            "PADDLE_PRICE_ID_LIVE": "pri_live_monthly",
            "PADDLE_LIVE_CHECKOUT_ENABLED": checkout_enabled,
        }

    def provision_active_subscription(self, *, scheduled_cancel_at=None):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        event = self.subscription_event(intent=token, scheduled_cancel_at=scheduled_cancel_at)
        body, signature = self.signed(event)
        self.assertTrue(paddle_billing.process_webhook(body, signature))
        return event["data"]["id"]

    @staticmethod
    def paddle_api_response(data):
        response = Mock()
        response.json.return_value = {"data": data}
        response.raise_for_status.return_value = None
        return response

    def authenticated_client(self):
        client = app_module.app.test_client()
        response = client.post(
            "/auth/login",
            data={"email": self.user["email"], "password": "correct-horse-battery-7"},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_signature_requires_exact_raw_body_and_recent_timestamp(self):
        event = self.subscription_event()
        body, signature = self.signed(event)
        self.assertTrue(paddle_billing.verify_webhook_signature(body, signature))
        with self.assertRaises(ValueError):
            paddle_billing.verify_webhook_signature(body + b" ", signature)
        stale = int(time.time()) - 600
        _, stale_signature = self.signed(event, timestamp=stale)
        with self.assertRaises(ValueError):
            paddle_billing.verify_webhook_signature(body, stale_signature)

    def test_webhook_endpoint_verifies_raw_body_and_success_page_does_not_grant_pro(self):
        client = app_module.app.test_client()
        body, signature = self.signed(self.subscription_event())
        bad = client.post("/api/paddle/webhook", data=body + b" ", headers={"Paddle-Signature": signature})
        self.assertEqual(bad.status_code, 400)
        success_page = client.get("/checkout/success")
        self.assertEqual(success_page.status_code, 200)
        self.assertIn("Your Ennoic Pro subscription is being activated.", success_page.get_data(as_text=True))
        self.assertNotIn("This page does not activate Pro by itself", success_page.get_data(as_text=True))
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "free")

    def test_paddle_webhook_updates_badge_and_settings_after_refresh(self):
        client = app_module.app.test_client()
        client.post("/auth/login", data={"email": self.user["email"], "password": "correct-horse-battery-7"})
        initial_home = client.get("/").get_data(as_text=True)
        self.assertRegex(initial_home, r'id="account-pro-badge"[^>]*hidden')
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        body, signature = self.signed(self.subscription_event(intent=token))
        webhook = client.post("/api/paddle/webhook", data=body, headers={"Paddle-Signature": signature})
        self.assertEqual(webhook.status_code, 200)
        self.assertTrue(webhook.get_json()["processed"])
        refreshed_home = client.get("/").get_data(as_text=True)
        self.assertRegex(refreshed_home, r'id="account-pro-badge"[^>]*>PRO</span>')
        refreshed_settings = client.get("/settings").get_data(as_text=True)
        self.assertIn("Ennoic Pro</h3>", refreshed_settings)
        self.assertIn(">Active</span>", refreshed_settings)

    def test_upgrade_controls_start_hidden_until_entitlements_are_known(self):
        client = app_module.app.test_client()
        client.post("/auth/login", data={"email": self.user["email"], "password": "correct-horse-battery-7"})
        home = client.get("/").get_data(as_text=True)
        self.assertRegex(home, r'id="app-upgrade"[^>]*hidden')
        self.assertIn('id="app-entitlements-loading" role="status"', home)
        self.assertIn('id="app-entitlements-retry" type="button" hidden', home)

        pricing = client.get("/pricing").get_data(as_text=True)
        self.assertRegex(pricing, r'id="upgrade"[^>]*hidden')
        self.assertIn('id="entitlements-retry" type="button" hidden', pricing)
        legacy_checkout = client.get("/checkout")
        self.assertEqual(legacy_checkout.status_code, 302)
        self.assertEqual(legacy_checkout.headers["Location"], "/pricing")

        settings = client.get("/settings").get_data(as_text=True)
        self.assertIn('id="account-entitlements-retry" type="button" hidden', settings)
        self.assertRegex(settings, r'id="account-upgrade-prompt" hidden')

    def test_active_subscription_grants_pro_only_from_verified_subscription_event(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        event = self.subscription_event(intent=token)
        body, signature = self.signed(event)
        self.assertTrue(paddle_billing.process_webhook(body, signature))
        self.assertFalse(paddle_billing.process_webhook(body, signature))
        view = entitlements.account_entitlements(self.user["id"])
        self.assertEqual((view["plan"], view["provider"]), ("pro", "paddle"))
        self.assertEqual(entitlements.chat_limits(self.user["id"])["plan"], "pro")

    def test_paid_account_deletion_cancels_with_paddle_before_disabling_account(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }
        effective_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        scheduled = {
            **active,
            "scheduled_change": {"action": "cancel", "effective_at": effective_at},
        }

        with patch("paddle_billing.requests.request") as no_request:
            settings = client.get("/settings")
            self.assertIn("remaining paid time", settings.get_data(as_text=True))
            invalid = client.delete("/api/account", json={"email": "wrong@example.com"})
            self.assertEqual(invalid.status_code, 400)
            no_request.assert_not_called()

        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active),
            self.paddle_api_response(scheduled),
        ]) as provider_request:
            response = client.delete("/api/account", json={"email": self.user["email"]})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(provider_request.call_count, 2)
        self.assertEqual(provider_request.call_args_list[0].args[0], "GET")
        self.assertEqual(provider_request.call_args_list[1].args[0], "POST")
        self.assertEqual(provider_request.call_args_list[1].kwargs["json"], {
            "effective_from": "next_billing_period",
        })
        stored_user = account_store.get_user(self.user["id"])
        self.assertIsNotNone(stored_user["deleted_at"])
        stored_subscription = account_store.get_paddle_subscription(self.user["id"], "sandbox")
        self.assertEqual(stored_subscription["status"], "canceled")
        self.assertEqual(stored_subscription["cancel_at"], effective_at)

    def test_paddle_subscription_longer_than_grace_is_canceled_before_purge(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        effective_at = (datetime.now(timezone.utc) + timedelta(days=45)).isoformat()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }
        scheduled = {
            **active,
            "scheduled_change": {"action": "cancel", "effective_at": effective_at},
        }
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active), self.paddle_api_response(scheduled),
        ]):
            response = client.delete("/api/account", json={"email": self.user["email"]})
        self.assertEqual(response.status_code, 202)
        stored = account_store.get_paddle_subscription(self.user["id"], "sandbox")
        self.assertEqual(stored["status"], "canceled")
        self.assertEqual(stored["cancel_at"], effective_at)

        # A scheduled cancellation is not mistaken for an ended subscription:
        # Pro remains available through the provider-confirmed effective date.
        self.assertEqual(entitlements.account_subscription_status(self.user["id"])["status"], "canceled")
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")
        with account_store.connection() as db:
            db.execute(
                "UPDATE users SET purge_after = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), self.user["id"]),
            )
        with patch("paddle_billing.requests.request", return_value=self.paddle_api_response(scheduled)):
            self.assertEqual(app_module._purge_due_account_deletions(), 1)

    def test_deletion_reconciles_subscription_when_paddle_webhook_is_delayed(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        client = self.authenticated_client()
        now = datetime.now(timezone.utc)
        effective_at = (now + timedelta(days=45)).isoformat()
        paddle_subscription = {
            "id": "sub_delayed_webhook",
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "items": [{"price": {"id": "pri_sandbox_monthly"}, "quantity": 1}],
            "current_billing_period": {
                "starts_at": now.isoformat(),
                "ends_at": effective_at,
            },
            "updated_at": now.isoformat(),
            "custom_data": {"ennoic_checkout_intent": token},
            "scheduled_change": None,
        }
        scheduled = {
            **paddle_subscription,
            "scheduled_change": {"action": "cancel", "effective_at": effective_at},
        }
        with patch("paddle_billing.requests.get", side_effect=[
            self.paddle_api_response([{"id": "ctm_paddle_test", "email": self.user["email"]}]),
            self.paddle_api_response([paddle_subscription]),
        ]) as reconcile_request, patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(paddle_subscription),
            self.paddle_api_response(scheduled),
        ]) as cancellation_request:
            response = client.delete("/api/account", json={"email": self.user["email"]})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(reconcile_request.call_count, 2)
        self.assertEqual(cancellation_request.call_count, 2)
        self.assertEqual(
            account_store.get_paddle_subscription(self.user["id"], "sandbox")["status"],
            "canceled",
        )

    def test_provider_outage_at_purge_defers_permanent_deletion(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }
        scheduled = {
            **active,
            "scheduled_change": {
                "action": "cancel",
                "effective_at": (datetime.now(timezone.utc) + timedelta(days=45)).isoformat(),
            },
        }
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active), self.paddle_api_response(scheduled),
        ]):
            self.assertEqual(client.delete("/api/account", json={"email": self.user["email"]}).status_code, 202)
        with account_store.connection() as db:
            db.execute(
                "UPDATE users SET purge_after = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), self.user["id"]),
            )

        with patch(
            "paddle_billing.requests.request",
            side_effect=paddle_billing.requests.Timeout("simulated provider outage"),
        ):
            self.assertEqual(app_module._purge_due_account_deletions(), 0)
        self.assertIsNotNone(account_store.get_user(self.user["id"]))

    def test_purge_reapplies_provider_cancellation_if_schedule_was_removed(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }
        scheduled = {
            **active,
            "scheduled_change": {
                "action": "cancel",
                "effective_at": (datetime.now(timezone.utc) + timedelta(days=45)).isoformat(),
            },
        }
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active), self.paddle_api_response(scheduled),
        ]):
            self.assertEqual(client.delete("/api/account", json={"email": self.user["email"]}).status_code, 202)
        with account_store.connection() as db:
            db.execute(
                "UPDATE users SET purge_after = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), self.user["id"]),
            )

        # The customer's/provider-side scheduled cancellation was removed. The
        # purge worker must schedule it again and verify it before erasing data.
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active),
            self.paddle_api_response(scheduled),
        ]) as provider_request:
            self.assertEqual(app_module._purge_due_account_deletions(), 1)
        self.assertEqual([call.args[0] for call in provider_request.call_args_list], ["GET", "POST"])

    def test_restoring_paddle_account_shows_scheduled_cancellation(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        effective_at = (datetime.now(timezone.utc) + timedelta(days=45)).isoformat()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }
        scheduled = {
            **active,
            "scheduled_change": {"action": "cancel", "effective_at": effective_at},
        }
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active), self.paddle_api_response(scheduled),
        ]):
            deletion = client.delete("/api/account", json={"email": self.user["email"]})
        self.assertEqual(deletion.status_code, 202)
        restored = client.post(
            "/auth/login",
            data={"email": self.user["email"], "password": "correct-horse-battery-7"},
        )
        self.assertEqual(restored.status_code, 302)
        entitlement_response = client.get("/api/entitlements").get_json()
        self.assertEqual((entitlement_response["plan"], entitlement_response["status"]), ("pro", "canceled"))
        settings = client.get("/settings").get_data(as_text=True)
        self.assertIn("Ends this billing period", settings)

    def test_provider_cancellation_failure_keeps_account_enabled_and_retryable(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }

        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active),
            paddle_billing.requests.Timeout("simulated timeout"),
            self.paddle_api_response(active),
        ]):
            response = client.delete("/api/account", json={"email": self.user["email"]})

        self.assertEqual(response.status_code, 503)
        self.assertIn("was not scheduled for deletion", response.get_json()["error"])
        self.assertIsNone(account_store.get_user(self.user["id"])["deleted_at"])
        self.assertIsNone(account_store.get_scheduled_deletion(self.user["id"]))
        self.assertEqual(client.get("/api/entitlements").status_code, 200)
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")

    def test_already_scheduled_paddle_cancellation_is_verified_without_reposting(self):
        effective_at = (datetime.now(timezone.utc) + timedelta(days=18)).isoformat()
        subscription_id = self.provision_active_subscription(scheduled_cancel_at=effective_at)
        client = self.authenticated_client()
        provider_state = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": {"action": "cancel", "effective_at": effective_at},
        }

        with patch("paddle_billing.requests.request", return_value=self.paddle_api_response(provider_state)) as provider_request:
            response = client.delete("/api/account", json={"email": self.user["email"]})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(provider_request.call_count, 1)
        self.assertEqual(provider_request.call_args.args[0], "GET")
        self.assertIsNotNone(account_store.get_user(self.user["id"])["deleted_at"])

    def test_already_canceled_paddle_subscription_is_confirmed_without_reposting(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        provider_state = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "canceled",
            "canceled_at": datetime.now(timezone.utc).isoformat(),
            "scheduled_change": None,
        }

        with patch("paddle_billing.requests.request", return_value=self.paddle_api_response(provider_state)) as provider_request:
            response = client.delete("/api/account", json={"email": self.user["email"]})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(provider_request.call_count, 1)
        self.assertEqual(provider_request.call_args.args[0], "GET")

    def test_paddle_success_response_without_cancel_state_is_not_accepted(self):
        subscription_id = "sub_cancel_not_confirmed"
        active = {"id": subscription_id, "customer_id": "ctm_paddle_test",
                  "status": "active", "scheduled_change": None}
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active),
            self.paddle_api_response(active),
            self.paddle_api_response(active),
        ]):
            with self.assertRaises(paddle_billing.PaddleUnavailable):
                paddle_billing.PaddleAPI().ensure_subscription_canceled(
                    subscription_id, expected_customer_id="ctm_paddle_test"
                )

    def test_paddle_customer_mapping_mismatch_stops_before_cancel_request(self):
        subscription_id = "sub_wrong_customer"
        provider_state = {"id": subscription_id, "customer_id": "ctm_someone_else",
                          "status": "active", "scheduled_change": None}
        with patch("paddle_billing.requests.request", return_value=self.paddle_api_response(provider_state)) as provider_request:
            with self.assertRaises(paddle_billing.PaddleUnavailable):
                paddle_billing.PaddleAPI().ensure_subscription_canceled(
                    subscription_id, expected_customer_id="ctm_paddle_test"
                )
        self.assertEqual(provider_request.call_count, 1)
        self.assertEqual(provider_request.call_args.args[0], "GET")

    def test_paddle_timeout_after_cancel_is_recovered_from_provider_state(self):
        subscription_id = "sub_cancel_timeout_recovered"
        active = {"id": subscription_id, "customer_id": "ctm_paddle_test",
                  "status": "active", "scheduled_change": None}
        scheduled = {
            **active,
            "scheduled_change": {
                "action": "cancel",
                "effective_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            },
        }
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active),
            paddle_billing.requests.Timeout("simulated response timeout"),
            self.paddle_api_response(scheduled),
        ]) as provider_request:
            result = paddle_billing.PaddleAPI().ensure_subscription_canceled(
                subscription_id, expected_customer_id="ctm_paddle_test"
            )
        self.assertTrue(result["confirmed"])
        self.assertEqual([call.args[0] for call in provider_request.call_args_list], ["GET", "POST", "GET"])

    def test_repeated_deletion_request_checks_paddle_state_and_does_not_cancel_twice(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }
        scheduled = {
            **active,
            "scheduled_change": {
                "action": "cancel",
                "effective_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            },
        }
        schedule_deletion = account_store.schedule_user_deletion
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active),
            self.paddle_api_response(scheduled),
            self.paddle_api_response(scheduled),
        ]) as provider_request, patch.object(
            account_store, "schedule_user_deletion", wraps=schedule_deletion
        ) as schedule_call:
            first = client.delete("/api/account", json={"email": self.user["email"]})
            second = client.delete("/api/account", json={"email": self.user["email"]})

        self.assertEqual((first.status_code, second.status_code), (202, 202))
        self.assertEqual([call.args[0] for call in provider_request.call_args_list], ["GET", "POST", "GET"])
        self.assertEqual(schedule_call.call_count, 1)

    def test_provider_confirmation_survives_database_failure_and_retry(self):
        subscription_id = self.provision_active_subscription()
        client = self.authenticated_client()
        active = {
            "id": subscription_id,
            "customer_id": "ctm_paddle_test",
            "status": "active",
            "scheduled_change": None,
        }
        scheduled = {
            **active,
            "scheduled_change": {
                "action": "cancel",
                "effective_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            },
        }
        with patch("paddle_billing.requests.request", side_effect=[
            self.paddle_api_response(active),
            self.paddle_api_response(scheduled),
            self.paddle_api_response(scheduled),
        ]) as provider_request:
            with patch.object(
                account_store,
                "schedule_user_deletion",
                side_effect=RuntimeError("simulated database failure"),
            ):
                failed = client.delete("/api/account", json={"email": self.user["email"]})
            self.assertEqual(failed.status_code, 503)
            self.assertIsNone(account_store.get_user(self.user["id"])["deleted_at"])
            retried = client.delete("/api/account", json={"email": self.user["email"]})

        self.assertEqual(retried.status_code, 202)
        self.assertEqual([call.args[0] for call in provider_request.call_args_list], ["GET", "POST", "GET"])

    def test_razorpay_deletion_cancellation_uses_cycle_end_and_requires_provider_confirmation(self):
        provider = billing.RazorpaySubscriptionProvider()
        subscription_id = "sub_razorpay_test"
        current = {"id": subscription_id, "status": "active", "cancel_at_cycle_end": False,
                   "notes": {"ennoic_user_id": self.user["id"]}}
        scheduled = {"id": subscription_id, "status": "active", "cancel_at_cycle_end": True,
                     "current_end": int(time.time()) + 86400,
                     "notes": {"ennoic_user_id": self.user["id"]}}
        with patch.object(provider, "_request", side_effect=[current, scheduled]) as provider_request:
            result = provider.ensure_subscription_canceled(
                subscription_id=subscription_id, expected_user_id=self.user["id"]
            )
        self.assertTrue(result["confirmed"])
        self.assertEqual(provider_request.call_args_list[1].args[:2], (
            "POST", f"/subscriptions/{subscription_id}/cancel"
        ))
        self.assertEqual(provider_request.call_args_list[1].kwargs["body"], {"cancel_at_cycle_end": 1})

    def test_razorpay_past_due_subscription_reference_survives_and_is_canceled_on_deletion(self):
        class TestRazorpayProvider:
            name = "razorpay"

            def verify_event(self, payload, signature):
                return billing.VerifiedSubscriptionEvent(
                    event_id="evt_razorpay_failed",
                    user_id=self_user_id,
                    event_type="payment_failed",
                    subscription_id="sub_razorpay_past_due",
                    period_start=None,
                    period_end=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
                )

        self_user_id = self.user["id"]
        service = billing.SubscriptionService(TestRazorpayProvider())
        self.assertTrue(service.process_provider_event(b"verified", "signature"))
        stored = account_store.get_account_subscription_for_deletion(self.user["id"])
        self.assertEqual(stored["status"], "past_due")
        self.assertEqual(stored["provider_subscription_id"], "sub_razorpay_past_due")

        client = self.authenticated_client()
        with patch.object(
            billing.subscription_service,
            "ensure_subscription_canceled",
            return_value={
                "confirmed": True,
                "status": "active",
                "effective_at": (datetime.now(timezone.utc) + timedelta(days=45)).isoformat(),
            },
        ) as cancel:
            response = client.delete("/api/account", json={"email": self.user["email"]})
        self.assertEqual(response.status_code, 202)
        cancel.assert_called_once_with(
            subscription_id="sub_razorpay_past_due", expected_user_id=self.user["id"]
        )
        stored = account_store.get_account_subscription_for_deletion(self.user["id"])
        self.assertEqual(stored["status"], "canceled")
        self.assertGreater(
            datetime.fromisoformat(stored["period_end"]), datetime.now(timezone.utc)
        )

    def test_restoring_razorpay_account_shows_scheduled_cancellation(self):
        effective_at = (datetime.now(timezone.utc) + timedelta(days=45)).isoformat()
        with account_store.connection() as db:
            db.execute(
                "UPDATE account_subscriptions SET plan = 'pro', status = 'active', provider = 'razorpay', "
                "provider_subscription_id = ?, period_end = ?, updated_at = ? WHERE user_id = ?",
                ("sub_razorpay_restore", effective_at, datetime.now(timezone.utc).isoformat(), self.user["id"]),
            )
        client = self.authenticated_client()
        with patch.object(
            billing.subscription_service,
            "ensure_subscription_canceled",
            return_value={"confirmed": True, "status": "active", "effective_at": effective_at},
        ):
            deletion = client.delete("/api/account", json={"email": self.user["email"]})
        self.assertEqual(deletion.status_code, 202)
        stored = account_store.get_account_subscription_for_deletion(self.user["id"])
        self.assertEqual(stored["status"], "canceled")

        class DelayedRazorpayProvider:
            name = "razorpay"

            def verify_event(self, payload, signature):
                return billing.VerifiedSubscriptionEvent(
                    event_id="evt_delayed_pre_cancel_charge",
                    user_id=self_user_id,
                    event_type="renewed",
                    subscription_id="sub_razorpay_restore",
                    period_start=None,
                    period_end=(datetime.now(timezone.utc) + timedelta(days=60)).isoformat(),
                )

        self_user_id = self.user["id"]
        delayed_events = billing.SubscriptionService(DelayedRazorpayProvider())
        self.assertTrue(delayed_events.process_provider_event(b"verified", "signature"))
        stored = account_store.get_account_subscription_for_deletion(self.user["id"])
        self.assertEqual(stored["status"], "canceled")
        self.assertEqual(stored["period_end"], effective_at)

        restored = client.post(
            "/auth/login",
            data={"email": self.user["email"], "password": "correct-horse-battery-7"},
        )
        self.assertEqual(restored.status_code, 302)
        entitlement_response = client.get("/api/entitlements").get_json()
        self.assertEqual((entitlement_response["plan"], entitlement_response["status"]), ("pro", "canceled"))

    def test_locally_expired_razorpay_subscription_keeps_reference_for_deletion_check(self):
        self_user_id = self.user["id"]
        expired_period = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        class TestRazorpayProvider:
            name = "razorpay"

            def verify_event(self, payload, signature):
                return billing.VerifiedSubscriptionEvent(
                    event_id="evt_razorpay_expired_locally",
                    user_id=self_user_id,
                    event_type="activated",
                    subscription_id="sub_razorpay_expired_locally",
                    period_start=None,
                    period_end=expired_period,
                )

        service = billing.SubscriptionService(TestRazorpayProvider())
        self.assertTrue(service.process_provider_event(b"verified", "signature"))
        view = entitlements.account_subscription_status(self.user["id"])
        self.assertEqual((view["plan"], view["status"]), ("free", "expired"))
        stored = account_store.get_account_subscription_for_deletion(self.user["id"])
        self.assertEqual(stored["provider_subscription_id"], "sub_razorpay_expired_locally")

        client = self.authenticated_client()
        settings = client.get("/settings").get_data(as_text=True)
        self.assertIn("previous Razorpay subscription record", settings)
        with patch.object(
            billing.subscription_service,
            "ensure_subscription_canceled",
            return_value={"confirmed": True, "status": "completed"},
        ) as cancel:
            response = client.delete("/api/account", json={"email": self.user["email"]})
        self.assertEqual(response.status_code, 202)
        cancel.assert_called_once_with(
            subscription_id="sub_razorpay_expired_locally", expected_user_id=self.user["id"]
        )

    def test_expired_razorpay_record_without_id_fails_closed(self):
        with account_store.connection() as db:
            db.execute(
                "UPDATE account_subscriptions SET plan = 'free', status = 'expired', provider = 'razorpay', "
                "provider_subscription_id = NULL, period_end = NULL WHERE user_id = ?",
                (self.user["id"],),
            )
        client = self.authenticated_client()
        with patch.object(billing.subscription_service, "ensure_subscription_canceled") as cancel:
            response = client.delete("/api/account", json={"email": self.user["email"]})
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(account_store.get_user(self.user["id"])["deleted_at"])
        self.assertIn("remains available", response.get_json()["error"])
        cancel.assert_not_called()

    def test_account_purge_preserves_provider_transaction_ledger(self):
        transaction_id = "txn_deletion_audit"
        with account_store.connection() as db:
            db.execute(
                "INSERT INTO paddle_transactions (paddle_transaction_id, user_id, paddle_customer_id, "
                "paddle_subscription_id, environment, status, price_id, last_event_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (transaction_id, self.user["id"], "ctm_paddle_test", "sub_paddle_test", "sandbox",
                 "completed", "pri_sandbox_monthly", datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )
        client = self.authenticated_client()
        scheduled = client.delete("/api/account", json={"email": self.user["email"]})
        self.assertEqual(scheduled.status_code, 202)
        with account_store.connection() as db:
            db.execute(
                "UPDATE users SET purge_after = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), self.user["id"]),
            )
        self.assertEqual(
            account_store.purge_due_deletions(verified_user_ids=[self.user["id"]]), 1
        )
        with account_store.connection() as db:
            retained = db.execute(
                "SELECT user_id, paddle_customer_id, paddle_subscription_id, status "
                "FROM paddle_transactions WHERE paddle_transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        self.assertIsNone(retained["user_id"])
        self.assertEqual(retained["paddle_customer_id"], "ctm_paddle_test")
        self.assertEqual(retained["paddle_subscription_id"], "sub_paddle_test")
        self.assertEqual(retained["status"], "completed")

    def test_delayed_signed_webhook_still_maps_expired_checkout_intent(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        with account_store.connection() as db:
            db.execute(
                "UPDATE paddle_checkout_intents SET expires_at = ?",
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
            )
        event = self.subscription_event(intent=token)
        body, signature = self.signed(event)
        self.assertTrue(paddle_billing.process_webhook(body, signature))
        stored = account_store.get_paddle_subscription(self.user["id"], "sandbox")
        self.assertEqual(stored["paddle_subscription_id"], "sub_paddle_test")
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")

    def test_transaction_paid_does_not_grant_pro_and_duplicate_is_ignored(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        now = datetime.now(timezone.utc).isoformat()
        event = {"event_id": "evt_txn_paid", "event_type": "transaction.paid", "occurred_at": now,
                 "data": {"id": "txn_test", "customer_id": "ctm_test", "subscription_id": "sub_test",
                          "status": "completed", "custom_data": {"ennoic_checkout_intent": token},
                          "items": [{"price": {"id": "pri_sandbox_monthly"}}]}}
        body, signature = self.signed(event)
        self.assertTrue(paddle_billing.process_webhook(body, signature))
        self.assertFalse(paddle_billing.process_webhook(body, signature))
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "free")

    def test_out_of_order_event_cannot_restore_canceled_subscription(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        now = datetime.now(timezone.utc)
        future_end = (now + timedelta(days=30)).isoformat()
        event = self.subscription_event(intent=token, occurred_at=now.isoformat(), ends_at=future_end)
        body, signature = self.signed(event)
        self.assertTrue(paddle_billing.process_webhook(body, signature))
        canceled_at = (now + timedelta(minutes=1)).isoformat()
        cancel = self.subscription_event(event_id="evt_canceled", event_type="subscription.updated",
                                         occurred_at=canceled_at, status="active", ends_at=future_end,
                                         subscription_id="sub_paddle_test")
        cancel["data"]["scheduled_change"] = {"action": "cancel", "effective_at": future_end}
        body, signature = self.signed(cancel)
        self.assertTrue(paddle_billing.process_webhook(body, signature))
        older = self.subscription_event(event_id="evt_older", event_type="subscription.updated",
                                        occurred_at=now.isoformat(), status="active", intent=token,
                                        subscription_id="sub_paddle_test")
        body, signature = self.signed(older)
        self.assertFalse(paddle_billing.process_webhook(body, signature))
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")
        scheduled = account_store.get_paddle_subscription(self.user["id"], "sandbox")
        self.assertEqual(scheduled["status"], "canceled")
        expired = account_store.get_paddle_subscription(self.user["id"], "sandbox", now=(now + timedelta(days=31)).isoformat())
        self.assertEqual(expired["status"], "expired")
        with patch("account_store._now", return_value=(now + timedelta(days=31)).isoformat()):
            self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "free")

    def test_live_checkout_is_disabled_by_default_even_with_live_ids(self):
        with patch.dict(os.environ, self.live_environment()):
            config = paddle_billing.checkout_configuration()
        self.assertFalse(config["ready"])
        self.assertIn("disabled", config["message"])
        with patch.dict(os.environ, self.live_environment(checkout_enabled="true")):
            enabled = paddle_billing.checkout_configuration()
        self.assertTrue(enabled["ready"])

    def test_production_ignores_legacy_unscoped_sandbox_credentials(self):
        values = {
            "PADDLE_ENVIRONMENT": "production",
            "PADDLE_CLIENT_TOKEN": "live_wrongly_shared_token",
            "PADDLE_API_KEY": "sandbox_api_key",
            "PADDLE_WEBHOOK_SECRET": "whsec_test_secret",
            "PADDLE_PRICE_ID_LIVE": "pri_live_monthly",
            "PADDLE_LIVE_CHECKOUT_ENABLED": "true",
        }
        with patch.dict(os.environ, values):
            config = paddle_billing.checkout_configuration()
            self.assertEqual(paddle_billing.client_token(), "")
            with self.assertRaisesRegex(paddle_billing.PaddleUnavailable, "PADDLE_API_KEY_LIVE"):
                paddle_billing.PaddleAPI()
            with self.assertRaisesRegex(paddle_billing.PaddleUnavailable, "PADDLE_WEBHOOK_SECRET_LIVE"):
                paddle_billing.process_webhook(b"{}", "ts=1;h1=invalid")
        self.assertFalse(config["ready"])
        self.assertIn("PADDLE_CLIENT_TOKEN_LIVE", config["message"])

    def test_production_rejects_a_signing_secret_reused_from_sandbox(self):
        values = self.live_environment()
        values["PADDLE_WEBHOOK_SECRET_LIVE"] = "whsec_test_secret"
        with patch.dict(os.environ, values):
            self.assertEqual(paddle_billing.webhook_secret("production"), "")
            self.assertFalse(paddle_billing.checkout_configuration()["ready"])

    def test_sandbox_subscription_is_retained_but_cannot_grant_pro_after_live_switch(self):
        sandbox_intent = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        sandbox_event = self.subscription_event(intent=sandbox_intent)
        sandbox_body, sandbox_signature = self.signed(sandbox_event)
        self.assertTrue(paddle_billing.process_webhook(sandbox_body, sandbox_signature))
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")

        sandbox_record = account_store.get_paddle_subscription(self.user["id"], "sandbox")
        self.assertEqual(sandbox_record["environment"], "sandbox")
        with patch.dict(os.environ, self.live_environment()):
            self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "free")
            retained = account_store.get_paddle_subscription(self.user["id"], "sandbox")
            self.assertEqual(retained["paddle_subscription_id"], sandbox_record["paddle_subscription_id"])
            self.assertEqual(retained["status"], "active")

            # Paddle event IDs are scoped by environment, so a sandbox delivery cannot
            # suppress the corresponding live event even if the IDs happen to collide.
            live_intent = account_store.create_paddle_checkout_intent(
                self.user["id"], "production", "pri_live_monthly"
            )
            live_event = self.subscription_event(intent=live_intent, subscription_id="sub_paddle_live")
            live_event["data"]["items"][0]["price"]["id"] = "pri_live_monthly"
            live_body, live_signature = self.signed(live_event, secret="whsec_live_secret")
            self.assertTrue(paddle_billing.process_webhook(live_body, live_signature))
            self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")
            self.assertEqual(
                account_store.get_paddle_subscription(self.user["id"], "production")["environment"],
                "production",
            )
            self.assertEqual(
                account_store.get_paddle_subscription(self.user["id"], "sandbox")["paddle_subscription_id"],
                sandbox_record["paddle_subscription_id"],
            )

    def test_production_webhook_requires_live_secret_and_live_checkout_intent(self):
        live_intent = account_store.create_paddle_checkout_intent(
            self.user["id"], "production", "pri_live_monthly"
        )
        event = self.subscription_event(intent=live_intent)
        event["data"]["items"][0]["price"]["id"] = "pri_live_monthly"
        with patch.dict(os.environ, self.live_environment()):
            body, sandbox_signature = self.signed(event, secret="whsec_test_secret")
            with self.assertRaisesRegex(ValueError, "Invalid Paddle webhook signature"):
                paddle_billing.process_webhook(body, sandbox_signature)

            body, live_signature = self.signed(event, secret="whsec_live_secret")
            self.assertTrue(paddle_billing.process_webhook(body, live_signature))
            self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")

    def test_live_portal_uses_production_api_key_and_endpoint(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "production", "pri_live_monthly"
        )
        event = self.subscription_event(intent=token)
        event["data"]["id"] = "sub_paddle_live"
        event["data"]["items"][0]["price"]["id"] = "pri_live_monthly"
        body, signature = self.signed(event, secret="whsec_live_secret")
        client = app_module.app.test_client()
        client.post("/auth/login", data={"email": self.user["email"], "password": "correct-horse-battery-7"})

        with patch.dict(os.environ, self.live_environment()):
            self.assertTrue(paddle_billing.process_webhook(body, signature))
            response_mock = Mock()
            response_mock.json.return_value = {
                "data": {"urls": {"subscriptions": [{"view_subscription": "https://portal.example.test/live"}]}}
            }
            with patch("paddle_billing.requests.post", return_value=response_mock) as post:
                response = client.post("/api/paddle/portal")

        self.assertEqual(response.status_code, 200)
        args, kwargs = post.call_args
        self.assertTrue(args[0].startswith("https://api.paddle.com/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer live_api_key")

    def test_checkout_intent_is_authenticated_and_portal_ignores_supplied_customer_id(self):
        client = app_module.app.test_client()
        anonymous = client.post("/api/paddle/checkout-intent", json={})
        self.assertNotEqual(anonymous.status_code, 200)
        logged_in = client.post("/auth/login", data={"email": self.user["email"], "password": "correct-horse-battery-7"})
        self.assertEqual(logged_in.status_code, 302)
        intent = client.post("/api/paddle/checkout-intent", json={})
        self.assertEqual(intent.status_code, 200)
        token = intent.get_json()["checkout_intent"]
        event = self.subscription_event(intent=token)
        body, signature = self.signed(event)
        self.assertTrue(paddle_billing.process_webhook(body, signature))
        with patch.object(paddle_billing.PaddleAPI, "create_portal_session", return_value="https://portal.example.test") as create:
            response = client.post("/api/paddle/portal", json={"customer_id": "ctm_attacker"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["url"], "https://portal.example.test")
        create.assert_called_once_with("ctm_paddle_test", "sub_paddle_test")

    def test_admin_dashboard_uses_production_paddle_entitlements_for_plan_counts(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "production", "pri_live_monthly"
        )
        event = self.subscription_event(intent=token, subscription_id="sub_paddle_live")
        event["data"]["items"][0]["price"]["id"] = "pri_live_monthly"
        event["data"]["customer_id"] = "ctm_paddle_live"
        body, signature = self.signed(event, secret="whsec_live_secret")

        admin = account_store.create_user("anukalpy29@gmail.com", "correct-horse-battery-7")
        account_store.verify_email(admin["id"])
        client = app_module.app.test_client()
        client.post("/auth/login", data={"email": admin["email"], "password": "correct-horse-battery-7"})

        with patch.dict(os.environ, self.live_environment()), patch.dict(
            os.environ, {"ENNOIC_ADMIN_EMAILS": "attacker@example.com"}
        ):
            self.assertTrue(paddle_billing.process_webhook(body, signature))
            self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")
            response = client.get("/admin/users")

        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("<span>Pro plan</span><strong>1</strong>", page)
        self.assertIn("paddle@example.com", page)
        paddle_account_row = page.split("paddle@example.com", 1)[1].split("</tr>", 1)[0]
        self.assertIn("badge pro", paddle_account_row)
        self.assertNotIn("contact@example.com", page)

    def test_admin_users_denies_non_admin_even_if_environment_lists_them(self):
        client = app_module.app.test_client()
        client.post("/auth/login", data={"email": self.user["email"], "password": "correct-horse-battery-7"})
        with patch.dict(os.environ, {"ENNOIC_ADMIN_EMAILS": self.user["email"]}):
            response = client.get("/admin/users")
        self.assertEqual(response.status_code, 404)

    def test_reconciliation_syncs_only_current_users_paddle_custom_data(self):
        token = account_store.create_paddle_checkout_intent(
            self.user["id"], "sandbox", "pri_sandbox_monthly"
        )
        with account_store.connection() as db:
            db.execute(
                "UPDATE paddle_checkout_intents SET expires_at = ?",
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
            )
        now = datetime.now(timezone.utc)
        subscription = {
            "id": "sub_paddle_reconcile",
            "customer_id": "ctm_paddle_reconcile",
            "status": "active",
            "custom_data": {"ennoic_checkout_intent": token},
            "items": [{"price": {"id": "pri_sandbox_monthly"}, "quantity": 1}],
            "current_billing_period": {
                "starts_at": now.isoformat(),
                "ends_at": (now + timedelta(days=30)).isoformat(),
            },
            "updated_at": now.isoformat(),
        }

        def response(payload):
            result = Mock()
            result.json.return_value = payload
            result.raise_for_status.return_value = None
            return result

        def paddle_get(url, **kwargs):
            if url.endswith("/customers"):
                return response({
                    "data": [{"id": "ctm_paddle_reconcile", "email": self.user["email"]}],
                    "meta": {"pagination": {"has_more": False}},
                })
            return response({
                "data": [subscription],
                "meta": {"pagination": {"has_more": False}},
            })

        client = app_module.app.test_client()
        client.post("/auth/login", data={"email": self.user["email"], "password": "correct-horse-battery-7"})
        with patch("paddle_billing.requests.get", side_effect=paddle_get) as paddle_request:
            response = client.post("/checkout/success/reconcile")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/checkout/success", response.headers["Location"])
        self.assertEqual(paddle_request.call_count, 2)
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "pro")
        active_page = client.get("/checkout/success").get_data(as_text=True)
        self.assertIn("Your Ennoic Pro subscription is active!", active_page)

    def test_reconciliation_does_not_map_by_email_without_stored_checkout_token(self):
        customer = {"id": "ctm_paddle_reconcile", "email": self.user["email"]}
        snapshot = {
            "id": "sub_unlinked",
            "customer_id": customer["id"],
            "status": "active",
            "custom_data": None,
            "items": [{"price": {"id": "pri_sandbox_monthly"}, "quantity": 1}],
            "current_billing_period": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        responses = [
            {"data": [customer], "meta": {"pagination": {"has_more": False}}},
            {"data": [snapshot], "meta": {"pagination": {"has_more": False}}},
        ]
        client = app_module.app.test_client()
        client.post("/auth/login", data={"email": self.user["email"], "password": "correct-horse-battery-7"})
        with patch("paddle_billing.requests.get", side_effect=[
            Mock(json=Mock(return_value=responses[0]), raise_for_status=Mock()),
            Mock(json=Mock(return_value=responses[1]), raise_for_status=Mock()),
        ]):
            client.post("/checkout/success/reconcile")
        self.assertEqual(entitlements.account_entitlements(self.user["id"])["plan"], "free")

    def test_reconciliation_requires_authentication_and_accepts_no_client_subscription_id(self):
        client = app_module.app.test_client()
        response = client.post("/checkout/success/reconcile", json={"subscription_id": "sub_attacker"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))


if __name__ == "__main__":
    unittest.main()
