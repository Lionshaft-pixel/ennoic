import os
import unittest
from unittest.mock import patch

from flask import Flask

import app as app_module
import discord_notifications


class DiscordNotificationTests(unittest.TestCase):
    def test_messages_contain_only_allow_listed_metadata(self):
        message = discord_notifications._format_message(
            "chat_error",
            {
                "reference": "ab12cd34ef56",
                "error_type": "ValueError",
                "location": "app.py:chat:1670",
                "prompt": "private prompt",
                "email": "private@example.invalid",
            },
        )
        self.assertIn("AI chat request failed", message)
        self.assertIn("Reference: ab12cd34ef56", message)
        self.assertIn("Error: ValueError", message)
        self.assertNotIn("private prompt", message)
        self.assertNotIn("private@example.invalid", message)

    def test_task_alert_omits_task_text_and_unknown_events_are_rejected(self):
        message = discord_notifications._format_message(
            "task_created", {"count": 2, "title": "private task details"}
        )
        self.assertIn("Tasks created: 2", message)
        self.assertNotIn("private task details", message)
        self.assertIsNone(discord_notifications._format_message("unknown_event"))

    def test_only_discord_https_webhook_urls_are_accepted(self):
        self.assertTrue(discord_notifications._webhook_url_is_valid(
            "https://discord.com/api/webhooks/123/token"
        ))
        for value in (
            "http://discord.com/api/webhooks/123/token",
            "https://example.com/api/webhooks/123/token",
            "https://discord.com.evil.example/api/webhooks/123/token",
            "https://discord.com:bad/api/webhooks/123/token",
            "https://discord.com/api/webhooks/123/token?secret=private",
        ):
            self.assertFalse(discord_notifications._webhook_url_is_valid(value))

    def test_delivery_posts_without_logging_or_exposing_webhook_url(self):
        webhook = "https://discord.com/api/webhooks/123/private-token"
        response = type("Response", (), {"status_code": 204})()
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": webhook}), \
                patch("discord_notifications.requests.post", return_value=response) as post:
            self.assertTrue(discord_notifications._deliver("Ennoic notification: Successful sign-in"))
        post.assert_called_once_with(
            webhook,
            json={
                "content": "Ennoic notification: Successful sign-in",
                "allowed_mentions": {"parse": []},
            },
            timeout=(1.0, 2.0),
            allow_redirects=False,
        )

    def test_notification_queue_is_disabled_in_flask_testing_mode(self):
        test_app = Flask(__name__)
        test_app.config["TESTING"] = True
        with test_app.test_request_context("/"), patch.dict(
            os.environ,
            {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"},
        ):
            self.assertFalse(discord_notifications.notify_discord("registration"))

    def test_server_errors_are_alerted_using_only_endpoint_and_error_type(self):
        with app_module.app.test_request_context("/api/private-user-path"):
            with patch("app.notify_discord") as notify:
                app_module.add_security_headers(app_module.app.response_class(status=503))
        notify.assert_called_once_with(
            "server_error", endpoint="unknown", error_type="HTTP503"
        )
