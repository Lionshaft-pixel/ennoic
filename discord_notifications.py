"""Best-effort, privacy-conscious Discord notifications for application events."""

import logging
import os
import re
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Lock, Thread
from urllib.parse import urlsplit

import requests
from flask import current_app, g, has_request_context


logger = logging.getLogger(__name__)
_QUEUE = Queue(maxsize=100)
_WORKER_LOCK = Lock()
_WORKER = None

_EVENT_LABELS = {
    "registration": "New account registration",
    "login": "Successful sign-in",
    "task_created": "New task created",
    "pro_activated": "Ennoic Pro subscription activated",
    "chat_error": "AI chat request failed",
    "chat_response": "AI chat exchange",
    "server_error": "Application server error",
}
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/-]{1,100}$")


def _webhook_url_is_valid(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "discord.com"
        and parsed.path.startswith("/api/webhooks/")
        and not parsed.query
        and not parsed.fragment
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
    )


def _safe_value(value):
    if isinstance(value, str) and _SAFE_VALUE.fullmatch(value):
        return value
    return "unknown"


def _bounded_content(value, limit):
    if not isinstance(value, str):
        return "(unavailable)"
    # Keep line breaks readable while excluding control characters from webhook content.
    value = "".join(char for char in value if char in "\n\t" or char.isprintable())
    if len(value) > limit:
        return value[:limit] + "…"
    return value or "(empty)"


def _format_message(event, details=None):
    label = _EVENT_LABELS.get(event)
    if label is None:
        return None
    details = details if isinstance(details, dict) else {}
    lines = [f"Ennoic notification: {label}",
             f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"]
    if event == "task_created":
        count = details.get("count")
        if type(count) is int and 1 <= count <= 20:
            lines.append(f"Tasks created: {count}")
    elif event == "pro_activated":
        environment = details.get("environment")
        if environment in {"production", "sandbox"}:
            lines.append(f"Environment: {environment}")
    elif event == "chat_error":
        lines.extend((
            f"Reference: {_safe_value(details.get('reference'))}",
            f"Error: {_safe_value(details.get('error_type'))}",
            f"Location: {_safe_value(details.get('location'))}",
        ))
    elif event == "chat_response":
        lines.extend((
            f"User: {_bounded_content(details.get('email'), 254)}",
            f"Message: {_bounded_content(details.get('message'), 600)}",
            f"Ennoic replied: {_bounded_content(details.get('reply'), 900)}",
        ))
    elif event == "server_error":
        lines.extend((
            f"Endpoint: {_safe_value(details.get('endpoint'))}",
            f"Error: {_safe_value(details.get('error_type'))}",
        ))
    return "\n".join(lines)


def _deliver(message):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not _webhook_url_is_valid(webhook_url):
        logger.warning("discord_notification_skipped reason=invalid_configuration")
        return False
    try:
        response = requests.post(
            webhook_url,
            json={"content": message, "allowed_mentions": {"parse": []}},
            timeout=(1.0, 2.0),
            allow_redirects=False,
        )
    except requests.RequestException as error:
        logger.warning("discord_notification_failed error_type=%s", type(error).__name__)
        return False
    if not 200 <= response.status_code < 300:
        logger.warning("discord_notification_failed status=%s", response.status_code)
        return False
    return True


def _worker_loop():
    while True:
        try:
            message = _QUEUE.get(timeout=30)
        except Empty:
            continue
        try:
            _deliver(message)
        except Exception as error:  # Notifications must never affect application work.
            logger.warning("discord_notification_failed error_type=%s", type(error).__name__)
        finally:
            _QUEUE.task_done()


def _ensure_worker():
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = Thread(target=_worker_loop, name="ennoic-discord-notifications", daemon=True)
            _WORKER.start()


def notify_discord(event, **details):
    """Queue an allow-listed event notification, never blocking the request."""
    try:
        if not has_request_context():
            return False
        if event in {"chat_error", "server_error"}:
            g.discord_error_notified = True
        if current_app.testing:
            return False
    except RuntimeError:
        return False
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not _webhook_url_is_valid(webhook_url):
        return False
    message = _format_message(event, details)
    if message is None:
        return False
    try:
        _QUEUE.put_nowait(message)
    except Full:
        logger.warning("discord_notification_dropped event=%s reason=queue_full", event)
        return False
    try:
        _ensure_worker()
    except Exception as error:
        logger.warning("discord_notification_worker_unavailable error_type=%s", type(error).__name__)
        return False
    return True
