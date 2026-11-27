import json
import os
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch

import app
import account_store


class PwaTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {
            "ENNOIC_TASK_DB": str(Path(directory.name) / "pwa.sqlite3"),
            "ENNOIC_ANDROID_SHA256_FINGERPRINTS": "",
            "ENNOIC_ANDROID_PACKAGE_ID": "com.ennoic.app",
        })
        environment.start()
        self.addCleanup(environment.stop)
        app.app.config.update(TESTING=True, RATELIMIT_ENABLED=False, WTF_CSRF_ENABLED=True)
        app.limiter.enabled = False
        app._initialized_databases.clear()
        self.client = app.app.test_client()

    def test_manifest_identity_scope_and_real_icons(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/manifest+json")
        manifest = json.loads(response.data)
        self.assertEqual((manifest["name"], manifest["short_name"]), ("Ennoic", "Ennoic"))
        self.assertEqual((manifest["id"], manifest["start_url"], manifest["scope"]), ("/", "/", "/"))
        self.assertEqual(manifest["display"], "standalone")
        self.assertFalse(manifest["prefer_related_applications"])
        self.assertEqual({icon["purpose"] for icon in manifest["icons"]}, {"any", "maskable"})
        for icon in manifest["icons"] + [{"src": "/static/pwa/icons/apple-touch-icon.png", "sizes": "180x180"}]:
            asset = self.client.get(icon["src"])
            self.assertEqual(asset.status_code, 200)
            self.assertEqual(asset.data[:8], b"\x89PNG\r\n\x1a\n")
            size = struct.unpack(">II", asset.data[16:24])
            self.assertEqual("x".join(map(str, size)), icon["sizes"])

    def test_public_resources_work_without_database_or_session(self):
        paths = ["/manifest.webmanifest", "/service-worker.js", "/offline", "/.well-known/assetlinks.json",
                 "/static/pwa/offline.css", "/static/pwa/offline.js", "/static/email/ennoic-logo.png"]
        with patch("app.account_store.initialize", side_effect=AssertionError("Database must not be used")):
            for path in paths:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertNotIn("Set-Cookie", response.headers, path)
        worker = self.client.get("/service-worker.js")
        self.assertEqual(worker.mimetype, "application/javascript")
        self.assertEqual(worker.headers["Service-Worker-Allowed"], "/")
        self.assertIn("no-cache", worker.headers["Cache-Control"])
        self.assertIn("worker-src 'self'", worker.headers["Content-Security-Policy"])

    def test_metadata_on_public_and_signed_in_pages_and_session_survives(self):
        for path in ["/", "/login", "/pricing", "/terms", "/privacy", "/refund-policy"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(b'rel="manifest" href="/manifest.webmanifest"', response.data)
            self.assertIn(b'apple-touch-icon', response.data)
            self.assertIn(b'/static/pwa/pwa.js', response.data)
        token = re.search(rb'<meta name="csrf-token" content="([^"]+)"', self.client.get("/login").data)[1].decode()
        user = account_store.create_user("pwa-regression@example.com", "pwa-test-password-987")
        account_store.verify_email(user["id"])
        login = self.client.post("/auth/login", data={
            "csrf_token": token, "email": user["email"], "password": "pwa-test-password-987",
        })
        self.assertEqual(login.status_code, 302)
        for path in ["/", "/settings", "/manifest.webmanifest", "/service-worker.js", "/offline"]:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").get_json()["user"]["email"], user["email"])
        home = self.client.get("/")
        self.assertIn("no-store", home.headers["Cache-Control"])
        self.assertIn(b'rel="manifest"', home.data)

    def test_assetlinks_defaults_to_the_verified_test_apk_identity(self):
        with patch.dict(os.environ):
            os.environ.pop("ENNOIC_ANDROID_SHA256_FINGERPRINTS", None)
            os.environ.pop("ENNOIC_ANDROID_PACKAGE_ID", None)
            with patch("app.account_store.initialize", side_effect=AssertionError("Database must not be used")):
                response = self.client.get("/.well-known/assetlinks.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertIn("must-revalidate", response.headers["Cache-Control"])
        self.assertEqual(response.get_json(), [{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.ennoic.app",
                "sha256_cert_fingerprints": [
                    "60:F5:54:7E:71:0B:4A:3C:D0:6C:0F:8D:CB:8F:EB:80:"
                    "17:6C:65:D7:7E:D8:21:C1:9A:4B:0E:75:72:2A:78:98",
                ],
            },
        }])

    def test_assetlinks_does_not_reuse_test_certificate_for_another_package(self):
        with patch.dict(os.environ, {"ENNOIC_ANDROID_PACKAGE_ID": "com.example.other"}):
            os.environ.pop("ENNOIC_ANDROID_SHA256_FINGERPRINTS", None)
            self.assertEqual(self.client.get("/.well-known/assetlinks.json").get_json(), [])

    def test_assetlinks_explicit_override_replaces_or_revokes_default_trust(self):
        # setUp's explicit empty setting must revoke the checked-in association.
        self.assertEqual(self.client.get("/.well-known/assetlinks.json").get_json(), [])
        fingerprint = ":".join(["AB"] * 32)
        second_fingerprint = ":".join(["CD"] * 32)
        with patch.dict(os.environ, {
            "ENNOIC_ANDROID_SHA256_FINGERPRINTS": f"{fingerprint.lower()}, {fingerprint}, {second_fingerprint}",
            "ENNOIC_ANDROID_PACKAGE_ID": "com.example.release",
        }):
            response = self.client.get("/.well-known/assetlinks.json")
        self.assertEqual(response.mimetype, "application/json")
        target = response.get_json()[0]["target"]
        self.assertEqual(target["package_name"], "com.example.release")
        self.assertEqual(target["sha256_cert_fingerprints"], [fingerprint, second_fingerprint])
        for values in [{"ENNOIC_ANDROID_SHA256_FINGERPRINTS": "placeholder"}, {
            "ENNOIC_ANDROID_SHA256_FINGERPRINTS": fingerprint,
            "ENNOIC_ANDROID_PACKAGE_ID": "invalid/package",
        }]:
            with patch.dict(os.environ, values), self.assertLogs(app.app.logger, level="ERROR"):
                self.assertEqual(self.client.get("/.well-known/assetlinks.json").get_json(), [])


if __name__ == "__main__":
    unittest.main()
