"""Public PWA resources; no account data or authentication dependencies."""

import os
import re

from flask import Blueprint, current_app, jsonify, send_from_directory


pwa = Blueprint("pwa", __name__)

# Public identity of the existing, explicitly authorized Android test APK.
# Replace/remove this test association when finalizing release signing.
TEST_ANDROID_PACKAGE_ID = "com.ennoic.app"
TEST_ANDROID_SHA256_FINGERPRINT = (
    "60:F5:54:7E:71:0B:4A:3C:D0:6C:0F:8D:CB:8F:EB:80:"
    "17:6C:65:D7:7E:D8:21:C1:9A:4B:0E:75:72:2A:78:98"
)


def _public_file(filename, mimetype):
    response = send_from_directory(current_app.static_folder, filename, mimetype=mimetype, max_age=0)
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


@pwa.get("/manifest.webmanifest")
def manifest():
    return _public_file("pwa/manifest.webmanifest", "application/manifest+json")


@pwa.get("/service-worker.js")
def service_worker():
    response = _public_file("pwa/service-worker.js", "application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@pwa.get("/offline")
def offline():
    return _public_file("pwa/offline.html", "text/html")


@pwa.get("/.well-known/assetlinks.json")
def assetlinks():
    # Certificate fingerprints are public. Private keys/passwords never belong here.
    package = os.environ.get("ENNOIC_ANDROID_PACKAGE_ID", TEST_ANDROID_PACKAGE_ID).strip()
    # An explicit empty override revokes trust. A different package must supply
    # its own certificate rather than inheriting the Ennoic test certificate.
    default_fingerprint = TEST_ANDROID_SHA256_FINGERPRINT if package == TEST_ANDROID_PACKAGE_ID else ""
    fingerprints = [value.strip().upper() for value in os.environ.get(
        "ENNOIC_ANDROID_SHA256_FINGERPRINTS", default_fingerprint
    ).split(",") if value.strip()]
    valid_package = re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package)
    valid_fingerprints = all(re.fullmatch(r"(?:[0-9A-F]{2}:){31}[0-9A-F]{2}", value) for value in fingerprints)
    statements = []
    if fingerprints and valid_package and valid_fingerprints:
        statements.append({
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": package,
                "sha256_cert_fingerprints": list(dict.fromkeys(fingerprints)),
            },
        })
    elif fingerprints:
        current_app.logger.error("android_assetlinks_configuration_invalid")
    response = jsonify(statements)
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response
