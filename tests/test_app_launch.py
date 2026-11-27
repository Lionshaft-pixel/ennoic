"""Installed-app entry and auth use existing accounts, CSRF, and OAuth state."""

import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import account_store
import app
from test_accounts import AccountTestCase


class AppLaunchTests(AccountTestCase):
    def test_initial_dashboard_and_settings_skip_usage_counters_but_keep_billing_status(self):
        user = self.create_verified_user("navigation-status@example.com")
        client = app.app.test_client()
        self.log_in_client(client, user)
        status = {"plan": "pro", "status": "canceled", "provider": "paddle", "period_end": "2027-01-15"}
        with patch("app.entitlements.account_subscription_status", return_value=status), patch(
            "app.entitlements.account_entitlements", side_effect=AssertionError("Usage does not block initial HTML")
        ):
            for path in ["/", "/app"]:
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'id="composer-input"', response.data)
                self.assertNotIn(b'id="account-pro-badge" aria-label="Ennoic Pro subscriber" title="Ennoic Pro" hidden', response.data)
            response = client.get("/settings")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'data-account-has-billing="true"', response.data)
            self.assertIn(b"Ends this billing period", response.data)
            self.assertIn(b"2027-01-15", response.data)

    def test_app_entry_is_distinct_from_browser_marketing_and_never_cached(self):
        client = app.app.test_client()
        browser = client.get("/")
        self.assertIn(b"/static/landing-page.html", browser.data)
        self.assertNotIn(b"data-app-welcome", browser.data)
        installed = client.get("/app")
        self.assertEqual(installed.status_code, 200)
        self.assertIn(b"data-app-welcome", installed.data)
        self.assertIn(b"Continue with Google", installed.data)
        self.assertIn(b'action="/auth/continue"', installed.data)
        self.assertIn(b'/static/email/ennoic-logo.png', installed.data)
        self.assertIn("no-store", installed.headers["Cache-Control"])
        self.assertNotIn(b"/static/landing-manifest.json", installed.data)
        # A normal tab's / always retains the existing marketing page, even if
        # another tab has visited the installed-app start URL in this session.
        self.assertIn(b"/static/landing-page.html", client.get("/").data)

    def test_existing_email_signin_returns_to_app_and_logout_returns_to_welcome(self):
        user = self.create_verified_user("installed-login@example.com")
        client = app.app.test_client()
        token = self.csrf_token(client, "/app")
        response = client.post("/auth/continue?next=https://attacker.invalid", data={
            "csrf_token": token, "email": user["email"], "password": "correct-horse-battery-7",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/app")
        dashboard = client.get("/app")
        self.assertIn(b'id="composer-input"', dashboard.data)
        self.assertNotIn(b"data-app-welcome", dashboard.data)
        token = self.csrf_token(client, "/app")
        logged_out = client.post("/auth/logout", data={"csrf_token": token})
        self.assertEqual(logged_out.location, "/app")
        self.assertFalse(client.get("/api/auth/me").get_json()["authenticated"])
        self.assertIn(b"data-app-welcome", client.get(logged_out.location).data)

    def test_welcome_email_flow_keeps_csrf_and_email_verification(self):
        client = app.app.test_client()
        token = self.csrf_token(client, "/app?form=register")
        missing_csrf = client.post("/auth/continue", data={
            "email": "installed-new@example.com", "password": "correct-horse-battery-7",
        })
        self.assertEqual(missing_csrf.status_code, 400)
        self.assertIsNone(account_store.get_user_by_email("installed-new@example.com"))
        with patch("app._email_delivery_configured", return_value=True), patch(
            "app._send_token_email", return_value=True
        ) as send_email:
            signup = client.post("/auth/continue", data={
                "csrf_token": token, "email": "installed-new@example.com",
                "password": "correct-horse-battery-7",
            })
        self.assertEqual(signup.status_code, 202)
        self.assertIn(b"data-app-welcome", signup.data)
        user = account_store.get_user_by_email("installed-new@example.com")
        self.assertFalse(user["email_verified"])
        send_email.assert_called_once_with(user, "verify")
        self.assertFalse(client.get("/api/auth/me").get_json()["authenticated"])

    def test_email_help_posts_existing_secure_endpoints_and_preserves_app_context(self):
        client = app.app.test_client()
        for path, endpoint in [("/app/email-help", "/auth/password-reset"),
                               ("/app/email-help?action=verify", "/auth/resend-verification")]:
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'action="{endpoint}"'.encode(), response.data)
            self.assertIn(b'name="csrf_token"', response.data)
        with patch("app._send_token_email") as email:
            token = self.csrf_token(client, "/app/email-help")
            response = client.post("/auth/password-reset", data={
                "csrf_token": token, "email": "unknown@example.com",
            })
        self.assertEqual(response.status_code, 202)
        self.assertIn(b"data-app-welcome", response.data)
        email.assert_not_called()

    def test_app_google_callback_keeps_canonical_uri_pkce_state_and_return_path(self):
        user = self.create_verified_user("installed-google@example.com")
        client = app.app.test_client()
        client.get("/app")
        environment = {
            "ENNOIC_GOOGLE_CLIENT_ID": "test-client", "ENNOIC_GOOGLE_CLIENT_SECRET": "test-secret",
            "ENNOIC_PUBLIC_ORIGIN": "https://ennoic.onrender.com",
        }
        with patch.dict(os.environ, environment):
            authorize = client.get("/auth/google?next=https://attacker.invalid")
        params = parse_qs(urlparse(authorize.location).query)
        self.assertEqual(params["redirect_uri"], ["https://ennoic.onrender.com/auth/google/callback"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        with client.session_transaction() as session:
            oauth = dict(session["google_oauth"])

        class TokenResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id_token": "mock-identity-token"}

        claims = {"nonce": oauth["nonce"], "email": user["email"], "email_verified": True}
        with patch.dict(os.environ, environment), patch(
            "app.requests.post", return_value=TokenResponse()
        ) as exchange, patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims):
            callback = client.get(f'/auth/google/callback?state={oauth["state"]}&code=mock-code')
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(callback.location, "/app")
        self.assertEqual(exchange.call_args.kwargs["data"]["code_verifier"], oauth["verifier"])
        self.assertEqual(exchange.call_args.kwargs["data"]["redirect_uri"], params["redirect_uri"][0])
        self.assertTrue(client.get("/api/auth/me").get_json()["authenticated"])

    def test_google_invalid_state_never_calls_provider_or_authenticates(self):
        client = app.app.test_client()
        client.get("/app")
        with patch.dict(os.environ, {
            "ENNOIC_GOOGLE_CLIENT_ID": "test-client", "ENNOIC_GOOGLE_CLIENT_SECRET": "test-secret",
        }), patch("app.requests.post") as exchange:
            client.get("/auth/google")
            response = client.get("/auth/google/callback?state=forged&code=forged")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"data-app-welcome", response.data)
        exchange.assert_not_called()
        self.assertFalse(client.get("/api/auth/me").get_json()["authenticated"])

    def test_browser_auth_still_returns_home_and_rejects_arbitrary_redirects(self):
        user = self.create_verified_user("browser-login@example.com")
        client = app.app.test_client()
        token = self.csrf_token(client)
        response = client.post("/auth/login?next=//attacker.invalid", data={
            "csrf_token": token, "email": user["email"], "password": "correct-horse-battery-7",
        })
        self.assertEqual(response.location, "/")
