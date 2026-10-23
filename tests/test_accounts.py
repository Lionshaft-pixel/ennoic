import hashlib
import html
import json
import os
import re
import smtplib
import tempfile
import unittest
from unittest.mock import patch

import account_store
import app
import task_store


class AccountTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.dict(
            os.environ,
            {"ENNOIC_TASK_DB": os.path.join(self.temp_dir.name, "accounts.sqlite3")},
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        app._initialized_databases.clear()
        app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True, RATELIMIT_ENABLED=False)
        app.limiter.reset()
        app.limiter.enabled = False
        account_store.initialize()

    def create_verified_user(self, email):
        user = account_store.create_user(email, "correct-horse-battery-7")
        self.assertIsNotNone(user)
        account_store.verify_email(user["id"])
        return account_store.get_user(user["id"])

    @staticmethod
    def log_in_client(client, user):
        token = AccountTestCase.csrf_token(client)
        response = client.post(
            "/auth/login",
            data={
                "csrf_token": token,
                "email": user["email"],
                "password": "correct-horse-battery-7",
            },
        )
        if response.status_code != 302:
            raise AssertionError(f"Test account sign in failed: {response.status_code}")

    @staticmethod
    def csrf_token(client, path="/login"):
        response = client.get(path, follow_redirects=True)
        match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', response.data)
        if match:
            return match.group(1).decode()
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if not match:
            raise AssertionError("A CSRF token was not rendered.")
        return match.group(1).decode()

    @staticmethod
    def auth_context(response):
        match = re.search(rb'<meta name="ennoic-auth-context" content="([^"]+)"', response.data)
        if not match:
            raise AssertionError("Authentication context was not rendered.")
        return json.loads(html.unescape(match.group(1).decode()))


class AccountAuthenticationTests(AccountTestCase):
    def test_password_hashing_uses_memory_safe_parameters_and_accepts_legacy_scrypt(self):
        user = account_store.create_user("hashing@example.com", "correct-horse-battery-7")
        self.assertTrue(user["password_hash"].startswith("pbkdf2:sha256:600000$"))
        self.assertTrue(account_store.verify_password(user, "correct-horse-battery-7"))

        legacy_password = "old-scrypt-password-123"
        legacy_salt = "known-legacy-salt"
        legacy_digest = hashlib.scrypt(
            legacy_password.encode("utf-8"),
            salt=legacy_salt.encode("utf-8"),
            n=32768,
            r=8,
            p=1,
            maxmem=64 * 1024 * 1024,
        ).hex()
        legacy_user = {
            "password_hash": f"scrypt:32768:8:1${legacy_salt}${legacy_digest}",
        }
        self.assertTrue(account_store.verify_password(legacy_user, legacy_password))
        self.assertFalse(account_store.verify_password(legacy_user, "not-the-password"))

    def test_registration_fails_clearly_when_email_delivery_is_unconfigured(self):
        client = app.app.test_client()
        csrf_token = self.csrf_token(client)
        smtp_variables = {
            "ENNOIC_SMTP_HOST": "",
            "ENNOIC_SMTP_FROM": "",
            "SMTP_HOST": "",
            "SMTP_FROM_EMAIL": "",
            "SMTP_FROM": "",
        }
        with patch.dict(os.environ, smtp_variables):
            response = client.post(
                "/auth/register",
                data={
                    "csrf_token": csrf_token,
                    "email": "not-created@example.com",
                    "password": "correct-horse-battery-7",
                },
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"email delivery is not configured", response.data)
        self.assertIsNone(account_store.get_user_by_email("not-created@example.com"))

    def test_existing_smtp_environment_names_are_supported(self):
        with patch.dict(
            os.environ,
            {
                "ENNOIC_SMTP_HOST": "",
                "ENNOIC_SMTP_FROM": "",
                "SMTP_HOST": "smtp.example.invalid",
                "SMTP_FROM_EMAIL": "no-reply@example.invalid",
            },
        ):
            self.assertTrue(app._email_delivery_configured())

    def test_smtp_authentication_failure_logs_stage_without_secrets(self):
        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def ehlo(self):
                return None

            def starttls(self, **kwargs):
                return None

            def login(self, username, password):
                raise smtplib.SMTPAuthenticationError(535, b"authentication rejected")

        with patch.dict(
            os.environ,
            {
                "ENNOIC_SMTP_HOST": "smtp.example.invalid",
                "ENNOIC_SMTP_FROM": "no-reply@example.invalid",
                "ENNOIC_SMTP_PORT": "587",
                "ENNOIC_SMTP_SSL": "",
                "ENNOIC_SMTP_USERNAME": "private-user",
                "ENNOIC_SMTP_PASSWORD": "private-password",
            },
        ), patch("app.smtplib.SMTP", FakeSMTP), self.assertLogs(app.app.logger, level="ERROR") as logs:
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                app._send_account_email(
                    "private-recipient@example.invalid",
                    "Verification",
                    "private-token",
                )
        diagnostic = "\n".join(logs.output)
        self.assertIn("stage=authentication", diagnostic)
        self.assertIn("smtp_code=535", diagnostic)
        for secret in ("private-user", "private-password", "private-recipient", "private-token"):
            self.assertNotIn(secret, diagnostic)

    def test_registration_verification_login_session_and_logout(self):
        client = app.app.test_client()
        response = client.post(
            "/auth/register",
            data={"email": "new@example.com", "password": "correct-horse-battery-7"},
        )
        self.assertEqual(response.status_code, 400)
        csrf_token = self.csrf_token(client)
        with patch.dict(os.environ, {"ENNOIC_SMTP_HOST": "mail.invalid", "ENNOIC_SMTP_FROM": "no-reply@example.com"}):
            with patch("app._send_token_email", return_value=True):
                response = client.post(
                    "/auth/register",
                    data={
                        "csrf_token": csrf_token,
                        "email": "new@example.com",
                        "password": "correct-horse-battery-7",
                    },
                )
        self.assertEqual(response.status_code, 202)
        self.assertNotIn(b"new@example.com", response.data)
        user = account_store.get_user_by_email("new@example.com")
        self.assertFalse(user["email_verified"])
        self.assertNotEqual(user["password_hash"], "correct-horse-battery-7")

        token = app._create_email_token(user, "verify")
        self.assertEqual(client.get(f"/auth/verify/{token}").status_code, 200)
        self.assertTrue(account_store.get_user(user["id"])["email_verified"])
        login_token = self.csrf_token(client)
        response = client.post(
            "/auth/login",
            data={
                "csrf_token": login_token,
                "email": "new@example.com",
                "password": "correct-horse-battery-7",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.get("/api/auth/me").get_json()["authenticated"])
        self.assertEqual(client.post("/auth/logout").status_code, 400)
        logout_token = self.csrf_token(client)
        response = client.post("/auth/logout", data={"csrf_token": logout_token})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(client.get("/api/auth/me").get_json()["authenticated"])

    def test_invalid_login_and_expired_or_reused_verification_link(self):
        client = app.app.test_client()
        self.assertEqual(client.get("/login").status_code, 200)
        user = account_store.create_user("verify@example.com", "correct-horse-battery-7")
        token = app._create_email_token(user, "verify")
        self.assertEqual(client.get(f"/auth/verify/{token}").status_code, 200)
        self.assertEqual(client.get(f"/auth/verify/{token}").status_code, 400)
        csrf_token = self.csrf_token(client)
        response = client.post(
            "/auth/login",
            data={"csrf_token": csrf_token, "email": "missing@example.com", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.auth_context(response)["error"], "Email or password is incorrect.")

    def test_public_landing_links_to_account_forms(self):
        client = app.app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        embedded_page = re.search(
            rb'<script type="__bundler/template">\s*(.*?)\s*</script>',
            response.data,
            re.S,
        )
        self.assertIsNotNone(embedded_page)
        landing_page = json.loads(embedded_page.group(1))
        nonce = re.search(rb'<script nonce="([^"]+)">', response.data)
        self.assertIsNotNone(nonce)
        self.assertIn(
            f"'nonce-{nonce.group(1).decode()}'".encode(),
            response.headers["Content-Security-Policy"].encode(),
        )
        self.assertIn(b"'strict-dynamic' 'unsafe-eval' blob:", response.headers["Content-Security-Policy"].encode())
        self.assertIn("Get started free", landing_page)
        self.assertIn('href="/login?form=register"', landing_page)
        self.assertIn('href="#benefits"', landing_page)
        self.assertIn(
            '<a href="/login" aria-label="Open the Ennoic app preview" id="preview"',
            landing_page,
        )

        login_page = client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        self.assertIn(b"auth-integration.js", login_page.data)
        nonce = re.search(rb'<script nonce="([^"]+)">', login_page.data)
        self.assertIsNotNone(nonce)
        self.assertIn(
            f"'nonce-{nonce.group(1).decode()}'".encode(),
            login_page.headers["Content-Security-Policy"].encode(),
        )
        self.assertIn(b"'strict-dynamic' 'unsafe-eval' blob:", login_page.headers["Content-Security-Policy"].encode())
        self.assertEqual(self.auth_context(login_page)["activeForm"], "login")
        register_page = client.get("/login?form=register")
        self.assertEqual(register_page.status_code, 200)
        self.assertEqual(self.auth_context(register_page)["activeForm"], "register")
        self.assertIn(b"auth-integration.js", register_page.data)
        self.assertNotIn(b"Welcome back", register_page.data)

        csrf_error = client.post("/auth/login", data={"csrf_token": "invalid"})
        self.assertEqual(csrf_error.status_code, 400)
        self.assertEqual(self.auth_context(csrf_error)["error"], "Your session expired. Please try again.")
        self.assertIn(b"auth-integration.js", csrf_error.data)
        self.assertNotIn(b"Welcome back", csrf_error.data)

    def test_password_reset_changes_hash_and_invalidates_existing_sessions(self):
        user = self.create_verified_user("reset@example.com")
        client = app.app.test_client()
        self.log_in_client(client, user)
        token = app._create_email_token(user, "reset")
        url = f"/auth/password-reset/{token}"
        csrf_token = self.csrf_token(client, url)
        response = client.post(
            url,
            data={"csrf_token": csrf_token, "password": "a-new-long-password-99"},
        )
        self.assertEqual(response.status_code, 200)
        updated = account_store.get_user(user["id"])
        self.assertEqual(updated["auth_version"], user["auth_version"] + 1)
        self.assertNotEqual(updated["password_hash"], user["password_hash"])
        self.assertFalse(client.get("/api/auth/me").get_json()["authenticated"])
        self.assertEqual(
            client.post(
                url,
                data={"csrf_token": csrf_token, "password": "another-long-password-99"},
            ).status_code,
            400,
        )
        login_client = app.app.test_client()
        login_token = self.csrf_token(login_client)
        self.assertEqual(
            login_client.post(
                "/auth/login",
                data={
                    "csrf_token": login_token,
                    "email": user["email"],
                    "password": "a-new-long-password-99",
                },
            ).status_code,
            302,
        )

    def test_password_change_requires_current_password_and_preserves_current_session(self):
        user = self.create_verified_user("change@example.com")
        client = app.app.test_client()
        self.log_in_client(client, user)
        response = client.post(
            "/api/account/password",
            json={
                "current_password": "correct-horse-battery-7",
                "new_password": "new-password-change-123",
            },
        )
        self.assertEqual(response.status_code, 400)
        csrf_token = self.csrf_token(client)
        response = client.post(
            "/api/account/password",
            json={
                "current_password": "correct-horse-battery-7",
                "new_password": "new-password-change-123",
            },
            headers={"X-CSRFToken": csrf_token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(client.get("/api/auth/me").get_json()["authenticated"])
        self.assertTrue(account_store.verify_password(account_store.get_user(user["id"]), "new-password-change-123"))
        logout_response = client.post(
            "/auth/logout",
            data={"csrf_token": response.get_json()["csrf_token"]},
        )
        self.assertEqual(logout_response.status_code, 302)

    def test_login_rate_limit_applies(self):
        app.limiter.enabled = True
        client = app.app.test_client()
        token = self.csrf_token(client)
        with patch("app.check_password_hash", return_value=False):
            statuses = [
                client.post(
                    "/auth/login",
                    data={"csrf_token": token, "email": "rate@example.com", "password": "not-the-password"},
                    environ_overrides={"REMOTE_ADDR": "192.0.2.25"},
                ).status_code
                for _ in range(11)
            ]
        self.assertEqual(statuses[-1], 429)


class AccountDataIsolationTests(AccountTestCase):
    def setUp(self):
        super().setUp()
        app.app.config.update(WTF_CSRF_ENABLED=False)
        self.alice = self.create_verified_user("alice@example.com")
        self.bob = self.create_verified_user("bob@example.com")
        self.alice_client = app.app.test_client()
        self.bob_client = app.app.test_client()
        self.log_in_client(self.alice_client, self.alice)
        self.log_in_client(self.bob_client, self.bob)

    def test_tasks_conversations_and_memories_are_isolated(self):
        created_task = self.alice_client.post("/api/tasks", json={"title": "Alice private task"})
        self.assertEqual(created_task.status_code, 201)
        task_id = created_task.get_json()["task"]["id"]
        self.assertEqual(self.bob_client.get("/api/tasks").get_json()["tasks"], [])
        self.assertEqual(
            self.bob_client.patch(f"/api/tasks/{task_id}", json={"title": "Stolen task"}).status_code,
            404,
        )
        self.assertEqual(self.bob_client.delete(f"/api/tasks/{task_id}").status_code, 404)
        self.assertEqual(task_store.get_task(task_id, user_id=self.alice["id"])["title"], "Alice private task")

        created_conversation = self.alice_client.post(
            "/api/conversations",
            json={"title": "Alice private conversation"},
        )
        self.assertEqual(created_conversation.status_code, 201)
        conversation_id = created_conversation.get_json()["conversation"]["id"]
        self.assertEqual(self.bob_client.get("/api/conversations").get_json()["conversations"], [])
        self.assertEqual(
            self.bob_client.put(
                f"/api/conversations/{conversation_id}",
                json={"title": "Stolen conversation"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.bob_client.delete(f"/api/conversations/{conversation_id}").status_code,
            404,
        )
        self.assertEqual(
            self.bob_client.post(
                "/api/chat",
                json={"conversation_id": conversation_id, "message": "hello"},
            ).status_code,
            404,
        )
        changed_conversation = self.alice_client.put(
            f"/api/conversations/{conversation_id}",
            json={"title": "Alice renamed conversation", "pinned": True},
        )
        self.assertEqual(changed_conversation.status_code, 200)
        self.assertTrue(changed_conversation.get_json()["conversation"]["pinned"])

        with patch("app.process_message_with_metadata", return_value={"reply": "Saved reply", "truncated": False}):
            reply = self.alice_client.post(
                "/api/chat",
                json={"conversation_id": conversation_id, "message": "A private message"},
            )
        self.assertEqual(reply.status_code, 200)
        persisted = self.alice_client.get("/api/conversations").get_json()["conversations"][0]
        self.assertEqual(
            [message["text"] for message in persisted["messages"]],
            ["A private message", "Saved reply"],
        )

        account_store.remember(self.alice["id"], "Alex", "keys", "Monday")
        account_store.remember(self.bob["id"], "Alex", "passport", "Tuesday")
        self.assertEqual(
            self.alice_client.get("/api/account/memories").get_json()["memories"][0]["object"],
            "keys",
        )
        self.assertEqual(
            self.bob_client.get("/api/account/memories").get_json()["memories"][0]["object"],
            "passport",
        )

    def test_task_proposals_and_legacy_rows_cannot_be_claimed_by_another_user(self):
        legacy = task_store.create_task({"title": "Unassigned legacy task"})
        private = task_store.create_task(
            {"title": "Alice proposal candidate"},
            user_id=self.alice["id"],
        )
        staged = task_store.stage_task_batch(
            [{"title": "Alice proposal candidate"}],
            "alice-conversation",
            user_id=self.alice["id"],
        )
        proposal_id = staged["proposals"][0]["id"]
        response = self.bob_client.post(
            "/api/tasks/confirm-duplicate",
            json={
                "proposal_id": proposal_id,
                "candidate_id": private["id"],
                "choice": "update_existing",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(task_store.get_task(legacy["id"]))
        self.assertEqual(self.alice_client.get("/api/tasks").get_json()["tasks"], [private])
        self.assertEqual(self.bob_client.get("/api/tasks").get_json()["tasks"], [])

    def test_legacy_import_is_explicit_and_assigns_rows_to_only_selected_account(self):
        task_store.create_task({"title": "Legacy task"})
        account_store.import_legacy_data(
            self.alice["id"],
            [{"person": "Alex", "object": "keys", "time": "Friday"}],
        )
        self.assertEqual(
            [task["title"] for task in self.alice_client.get("/api/tasks").get_json()["tasks"]],
            ["Legacy task"],
        )
        self.assertEqual(self.bob_client.get("/api/tasks").get_json()["tasks"], [])
        self.assertEqual(
            self.alice_client.get("/api/account/memories").get_json()["memories"][0]["object"],
            "keys",
        )
        self.assertEqual(self.bob_client.get("/api/account/memories").get_json()["memories"], [])

    def test_unauthenticated_api_requests_are_rejected(self):
        client = app.app.test_client()
        self.assertEqual(client.get("/api/tasks").status_code, 401)
        self.assertEqual(client.get("/api/conversations").status_code, 401)
        self.assertEqual(client.get("/api/account/memories").status_code, 401)


if __name__ == "__main__":
    unittest.main()
