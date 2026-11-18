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
        view = entitlements.account_entitlements(self.user["id"])
        self.assertEqual((view["plan"], view["provider"]), ("pro", "paddle"))
        self.assertEqual(entitlements.chat_limits(self.user["id"])["plan"], "pro")

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


if __name__ == "__main__":
    unittest.main()
