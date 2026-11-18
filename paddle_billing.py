"""Paddle Billing configuration, webhook verification, and customer portal."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time

import requests

import account_store


class PaddleUnavailable(RuntimeError):
    """Paddle is not configured or its API is unavailable."""


def _env(name):
    return os.environ.get(name, "").strip()


def environment():
    value = _env("PADDLE_ENVIRONMENT").lower()
    return value if value in {"sandbox", "production"} else None


def _environment_value(name, env=None):
    """Resolve an environment-scoped Paddle credential; legacy names are sandbox-only."""
    env = env or environment()
    if env not in {"sandbox", "production"}:
        return ""
    suffix = "SANDBOX" if env == "sandbox" else "LIVE"
    scoped = _env(f"{name}_{suffix}")
    if scoped:
        return scoped
    return _env(name) if env == "sandbox" else ""


def price_id(env=None):
    env = env or environment()
    if env not in {"sandbox", "production"}:
        return ""
    key = "PADDLE_PRICE_ID_SANDBOX" if env == "sandbox" else "PADDLE_PRICE_ID_LIVE"
    return _env(key)


def client_token(env=None):
    env = env or environment()
    token = _environment_value("PADDLE_CLIENT_TOKEN", env)
    expected_prefix = "test_" if env == "sandbox" else "live_"
    return token if env and token.startswith(expected_prefix) else ""


def configured_api_key(env=None):
    return _environment_value("PADDLE_API_KEY", env)


def webhook_secret(env=None):
    env = env or environment()
    secret = _environment_value("PADDLE_WEBHOOK_SECRET", env)
    if env == "production" and secret:
        sandbox_secret = _env("PADDLE_WEBHOOK_SECRET_SANDBOX") or _env("PADDLE_WEBHOOK_SECRET")
        if sandbox_secret and hmac.compare_digest(secret, sandbox_secret):
            return ""
    return secret


def live_checkout_enabled():
    return _env("PADDLE_LIVE_CHECKOUT_ENABLED").lower() in {"1", "true", "yes"}


def checkout_configuration():
    env = environment()
    missing = []
    if env is None:
        missing.append("PADDLE_ENVIRONMENT must be sandbox or production")
    if env and not client_token(env):
        expected = "test_" if env == "sandbox" else "live_"
        suffix = "SANDBOX" if env == "sandbox" else "LIVE"
        missing.append(f"PADDLE_CLIENT_TOKEN_{suffix} must be a {expected} client-side token for {env}")
    if env and not price_id(env):
        missing.append("PADDLE_PRICE_ID_SANDBOX" if env == "sandbox" else "PADDLE_PRICE_ID_LIVE")
    suffix = "SANDBOX" if env == "sandbox" else "LIVE"
    if not configured_api_key(env):
        missing.append(f"PADDLE_API_KEY_{suffix}")
    if not webhook_secret(env):
        missing.append(f"PADDLE_WEBHOOK_SECRET_{suffix}")
    if env == "production" and not live_checkout_enabled():
        missing.append("live checkout is disabled until PADDLE_LIVE_CHECKOUT_ENABLED=true")
    if env and price_id(env) and not price_id(env).startswith("pri_"):
        missing.append("configured Paddle price ID must start with pri_")
    return {
        "environment": env,
        "client_token": client_token(env) if env else "",
        "price_id": price_id(env),
        "ready": not missing,
        "message": "; ".join(missing),
    }


def _parse_timestamp(value, label):
    if not isinstance(value, str):
        raise ValueError(f"Paddle {label} is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Paddle {label} is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Paddle {label} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat()


def verify_webhook_signature(raw_body, signature, *, secret=None, now=None, tolerance_seconds=300):
    """Verify Paddle-Signature against the unmodified request body."""
    secret = secret if secret is not None else webhook_secret()
    if not secret:
        env = environment()
        suffix = "SANDBOX" if env == "sandbox" else "LIVE"
        raise PaddleUnavailable(f"PADDLE_WEBHOOK_SECRET_{suffix} is not configured.")
    if not isinstance(raw_body, bytes):
        raise ValueError("Paddle webhook body must be raw bytes.")
    if not isinstance(signature, str):
        raise ValueError("Missing Paddle-Signature header.")
    parts = {}
    for component in signature.split(";"):
        if "=" in component:
            key, value = component.split("=", 1)
            parts.setdefault(key.strip(), []).append(value.strip())
    timestamps = parts.get("ts", [])
    signatures = parts.get("h1", [])
    if not timestamps or not signatures or not timestamps[0].isdigit():
        raise ValueError("Invalid Paddle-Signature header.")
    timestamp = int(timestamps[0])
    now = time.time() if now is None else now
    if abs(now - timestamp) > tolerance_seconds:
        raise ValueError("Paddle webhook signature timestamp is outside the allowed window.")
    signed = str(timestamp).encode("ascii") + b":" + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, supplied) for supplied in signatures):
        raise ValueError("Invalid Paddle webhook signature.")
    return True


def _custom_checkout_token(data):
    custom_data = data.get("custom_data")
    if not isinstance(custom_data, dict):
        return None
    token = custom_data.get("ennoic_checkout_intent")
    return token if isinstance(token, str) and len(token) <= 200 else None


def _item_price_id(data):
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return None
    identifiers = set()
    for item in items:
        if not isinstance(item, dict):
            return None
        price = item.get("price")
        identifier = price.get("id") if isinstance(price, dict) else item.get("price_id")
        if not isinstance(identifier, str):
            return None
        identifiers.add(identifier)
    return next(iter(identifiers)) if len(identifiers) == 1 else None


def _event_base(event):
    event_id = event.get("event_id")
    event_type = event.get("event_type")
    if not isinstance(event_id, str) or not event_id.startswith("evt_"):
        raise ValueError("Paddle event ID is invalid.")
    if not isinstance(event_type, str):
        raise ValueError("Paddle event type is missing.")
    occurred_at = _parse_timestamp(event.get("occurred_at"), "event timestamp")
    data = event.get("data")
    if not isinstance(data, dict):
        raise ValueError("Paddle event data is invalid.")
    return event_id, event_type, occurred_at, data


def process_webhook(raw_body, signature):
    env = environment()
    if env is None:
        raise PaddleUnavailable("PADDLE_ENVIRONMENT must be sandbox or production.")
    verify_webhook_signature(raw_body, signature, secret=webhook_secret(env))
    try:
        event = json.loads(raw_body)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid Paddle webhook JSON.") from exc
    if not isinstance(event, dict):
        raise ValueError("Invalid Paddle webhook event.")
    event_id, event_type, occurred_at, data = _event_base(event)
    expected_price = price_id(env)
    event_source = "paddle" if env == "sandbox" else "paddle:production"

    if event_type.startswith("transaction."):
        relevant = {
            "transaction.created", "transaction.updated", "transaction.paid",
            "transaction.completed", "transaction.past_due", "transaction.payment_failed",
            "transaction.canceled",
        }
        if event_type not in relevant:
            return False
        transaction_id = data.get("id")
        if not isinstance(transaction_id, str) or not transaction_id.startswith("txn_"):
            raise ValueError("Paddle transaction ID is invalid.")
        item_price = _item_price_id(data)
        if item_price != expected_price:
            return False
        # Transaction events are retained for correlation but never grant Pro by themselves.
        return account_store.record_paddle_transaction_event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            transaction_id=transaction_id,
            status=data.get("status", event_type.rsplit(".", 1)[-1]),
            environment=env,
            customer_id=data.get("customer_id"),
            subscription_id=data.get("subscription_id"),
            price_id=item_price,
            checkout_token=_custom_checkout_token(data),
            event_source=event_source,
        )

    supported = {
        "subscription.created", "subscription.updated", "subscription.canceled",
        "subscription.activated", "subscription.past_due", "subscription.paused",
        "subscription.resumed",
    }
    if event_type not in supported:
        return False
    subscription_id = data.get("id")
    customer_id = data.get("customer_id")
    if (not isinstance(subscription_id, str) or not subscription_id.startswith("sub_")
            or not isinstance(customer_id, str) or not customer_id.startswith("ctm_")):
        raise ValueError("Paddle subscription or customer ID is invalid.")
    item_price = _item_price_id(data)
    if item_price != expected_price:
        return False

    status = data.get("status")
    if event_type == "subscription.canceled":
        status = "canceled"
    elif event_type in {"subscription.activated", "subscription.resumed"}:
        status = "active"
    elif event_type == "subscription.past_due":
        status = "past_due"
    elif event_type == "subscription.paused":
        status = "paused"
    if status not in {"active", "past_due", "paused", "canceled"}:
        status = "pending"

    current_period = data.get("current_billing_period")
    if current_period is not None and not isinstance(current_period, dict):
        raise ValueError("Paddle billing period is invalid.")
    period_start = _parse_timestamp(current_period["starts_at"], "billing period start") if current_period and current_period.get("starts_at") else None
    period_end = _parse_timestamp(current_period["ends_at"], "billing period end") if current_period and current_period.get("ends_at") else None
    scheduled_change = data.get("scheduled_change")
    cancel_at = None
    if isinstance(scheduled_change, dict) and scheduled_change.get("action") == "cancel":
        cancel_at = _parse_timestamp(scheduled_change.get("effective_at"), "scheduled cancellation date")
    elif status == "canceled" and data.get("canceled_at"):
        cancel_at = _parse_timestamp(data.get("canceled_at"), "cancellation date")
    provider_updated_at = _parse_timestamp(data.get("updated_at"), "subscription update timestamp") if data.get("updated_at") else None
    return account_store.apply_paddle_subscription_event(
        event_id=event_id,
        occurred_at=occurred_at,
        provider_updated_at=provider_updated_at,
        environment=env,
        subscription_id=subscription_id,
        customer_id=customer_id,
        status=status,
        price_id=item_price,
        period_start=period_start,
        period_end=period_end,
        cancel_at=cancel_at,
        checkout_token=_custom_checkout_token(data),
        event_source=event_source,
    )


class PaddleAPI:
    def __init__(self, *, api_key=None, env=None):
        self.env = env or environment()
        if self.env not in {"sandbox", "production"}:
            raise PaddleUnavailable("PADDLE_ENVIRONMENT must be sandbox or production.")
        self.api_key = api_key if api_key is not None else configured_api_key(self.env)
        if not self.api_key:
            suffix = "SANDBOX" if self.env == "sandbox" else "LIVE"
            raise PaddleUnavailable(f"PADDLE_API_KEY_{suffix} is not configured.")
        self.base_url = (
            "https://sandbox-api.paddle.com" if self.env == "sandbox" else "https://api.paddle.com"
        )

    def create_portal_session(self, customer_id, subscription_id=None):
        body = {"subscription_ids": [subscription_id]} if subscription_id else {}
        try:
            response = requests.post(
                f"{self.base_url}/customers/{customer_id}/portal-sessions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PaddleUnavailable("Could not connect to Paddle customer portal.") from exc
        except ValueError as exc:
            raise PaddleUnavailable("Paddle returned an invalid customer portal response.") from exc
        data = payload.get("data", {})
        urls = data.get("urls", {})
        subscriptions = urls.get("subscriptions", [])
        if subscriptions:
            url = subscriptions[0].get("view_subscription") or subscriptions[0].get("cancel_subscription")
            if url:
                return url
        general = urls.get("general", {}).get("overview")
        if isinstance(general, str):
            return general
        raise PaddleUnavailable("Paddle did not return a customer portal link.")
