"""Paddle Billing configuration, webhook verification, and customer portal."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

import requests

from discord_notifications import notify_discord

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
            allow_expired_intent=True,
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
    processed = account_store.apply_paddle_subscription_event(
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
        allow_expired_intent=True,
    )
    if processed and status == "active" and event_type in {"subscription.created", "subscription.activated"}:
        notify_discord("pro_activated", environment=env)
    return processed


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

    def _subscription_request(self, method, subscription_id, *, body=None):
        if (not isinstance(subscription_id, str) or not subscription_id.startswith("sub_")
                or not all(char.isascii() and (char.isalnum() or char == "_") for char in subscription_id)):
            raise PaddleUnavailable("The stored Paddle subscription reference is invalid.")
        try:
            response = requests.request(
                method,
                f"{self.base_url}/subscriptions/{quote(subscription_id, safe='')}",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PaddleUnavailable("Could not verify or cancel the Paddle subscription.") from exc
        except ValueError as exc:
            raise PaddleUnavailable("Paddle returned an invalid subscription response.") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or data.get("id") != subscription_id:
            raise PaddleUnavailable("Paddle returned an incomplete subscription response.")
        return data

    @staticmethod
    def _confirmed_cancellation(subscription, subscription_id, expected_customer_id):
        if subscription.get("id") != subscription_id:
            return None
        if (expected_customer_id and subscription.get("customer_id")
                != expected_customer_id):
            return None
        status = subscription.get("status")
        if status == "canceled":
            return {"confirmed": True, "status": "canceled", "effective_at": subscription.get("canceled_at")}
        scheduled = subscription.get("scheduled_change")
        if (status in {"active", "past_due"} and isinstance(scheduled, dict)
                and scheduled.get("action") == "cancel"
                and isinstance(scheduled.get("effective_at"), str)):
            return {
                "confirmed": True,
                "status": status,
                "effective_at": scheduled["effective_at"],
            }
        return None

    def ensure_subscription_canceled(self, subscription_id, *, expected_customer_id=None):
        """Stop future renewals and return only after Paddle confirms the state."""
        current = self._subscription_request("GET", subscription_id)
        if (expected_customer_id
                and current.get("customer_id") != expected_customer_id):
            raise PaddleUnavailable("The Paddle subscription could not be verified for this account.")
        confirmed = self._confirmed_cancellation(current, subscription_id, expected_customer_id)
        if confirmed:
            return confirmed

        status = current.get("status")
        if status not in {"active", "past_due", "paused", "pending", "trialing"}:
            raise PaddleUnavailable("Paddle did not confirm a cancellable subscription state.")
        effective_from = (
            "immediately" if status in {"past_due", "paused", "pending", "trialing"}
            else "next_billing_period"
        )
        try:
            updated = self._subscription_request(
                "POST", subscription_id, body={"effective_from": effective_from}
            )
        except PaddleUnavailable as cancel_error:
            # The provider may have accepted the request before a timeout. Re-read
            # its authoritative state before deciding the deletion must stop.
            try:
                current = self._subscription_request("GET", subscription_id)
            except PaddleUnavailable:
                raise cancel_error
            confirmed = self._confirmed_cancellation(current, subscription_id, expected_customer_id)
            if confirmed:
                return confirmed
            raise cancel_error

        confirmed = self._confirmed_cancellation(updated, subscription_id, expected_customer_id)
        if confirmed:
            return confirmed
        # A successful HTTP response is not sufficient unless its returned state
        # shows the cancellation. A fresh read also handles provider-side races.
        current = self._subscription_request("GET", subscription_id)
        confirmed = self._confirmed_cancellation(current, subscription_id, expected_customer_id)
        if confirmed:
            return confirmed
        raise PaddleUnavailable("Paddle did not confirm that the subscription will stop renewing.")

    def reconcile_account_subscription(self, *, user_id, email, expected_price_id):
        """Sync only Paddle subscriptions carrying this account's stored checkout token.

        Email is used only to locate candidate Paddle customers. A subscription is
        accepted only when Paddle returns the opaque checkout token stored for this
        authenticated Ennoic user, in this Paddle environment, and for this price.
        """
        if not isinstance(email, str) or not email.strip() or not expected_price_id:
            return False

        def get(path, params):
            try:
                response = requests.get(
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    params=params,
                    timeout=15,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise PaddleUnavailable("Could not verify subscription status with Paddle.") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise PaddleUnavailable("Paddle returned an invalid subscription response.")
            meta = payload.get("meta")
            pagination = meta.get("pagination") if isinstance(meta, dict) else None
            if isinstance(pagination, dict) and pagination.get("has_more"):
                raise PaddleUnavailable("Paddle returned an incomplete subscription result.")
            return payload["data"]

        customers = get("/customers", {"email": email.strip(), "per_page": 200})
        matched_customers = [
            row for row in customers
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and row["id"].startswith("ctm_")
            and isinstance(row.get("email"), str)
            and row["email"].strip().casefold() == email.strip().casefold()
        ]
        snapshots = []
        for customer in matched_customers:
            subscriptions = get("/subscriptions", {
                "customer_id": customer["id"],
                "status": "active,canceled,past_due,paused,pending,trialing",
                "per_page": 200,
            })
            for data in subscriptions:
                if not isinstance(data, dict):
                    continue
                subscription_id = data.get("id")
                if (not isinstance(subscription_id, str) or not subscription_id.startswith("sub_")
                        or data.get("customer_id") != customer["id"]):
                    continue
                token = _custom_checkout_token(data)
                intent = account_store.get_paddle_checkout_intent(
                    user_id, self.env, expected_price_id, token
                )
                if not intent or _item_price_id(data) != expected_price_id:
                    continue
                status = data.get("status")
                if status not in {"active", "past_due", "paused", "canceled", "pending", "trialing"}:
                    continue
                updated_at = data.get("updated_at") or data.get("created_at")
                try:
                    provider_updated_at = (
                        _parse_timestamp(data["updated_at"], "subscription update timestamp")
                        if data.get("updated_at") else None
                    )
                    occurred_at = _parse_timestamp(
                        updated_at, "subscription update timestamp" if data.get("updated_at") else "subscription creation timestamp"
                    )
                    current_period = data.get("current_billing_period")
                    if current_period is not None and not isinstance(current_period, dict):
                        continue
                    period_start = (
                        _parse_timestamp(current_period["starts_at"], "billing period start")
                        if current_period and current_period.get("starts_at") else None
                    )
                    period_end = (
                        _parse_timestamp(current_period["ends_at"], "billing period end")
                        if current_period and current_period.get("ends_at") else None
                    )
                    scheduled_change = data.get("scheduled_change")
                    cancel_at = None
                    if isinstance(scheduled_change, dict) and scheduled_change.get("action") == "cancel":
                        cancel_at = _parse_timestamp(
                            scheduled_change.get("effective_at"), "scheduled cancellation date"
                        )
                    elif status == "canceled" and data.get("canceled_at"):
                        cancel_at = _parse_timestamp(data["canceled_at"], "cancellation date")
                except ValueError:
                    continue
                snapshots.append((occurred_at, data, token, subscription_id, status,
                                  provider_updated_at, period_start, period_end, cancel_at))

        # Apply oldest first so the newest Paddle snapshot remains the account's latest row.
        snapshots.sort(key=lambda entry: entry[0])
        changed = False
        event_source = "paddle" if self.env == "sandbox" else "paddle:production"
        for occurred_at, data, token, subscription_id, status, provider_updated_at, period_start, period_end, cancel_at in snapshots:
            digest = hashlib.sha256(
                f"{self.env}:{subscription_id}:{provider_updated_at or occurred_at}".encode("utf-8")
            ).hexdigest()
            changed = account_store.apply_paddle_subscription_event(
                event_id=f"evt_sync_{digest}",
                occurred_at=occurred_at,
                provider_updated_at=provider_updated_at,
                environment=self.env,
                subscription_id=subscription_id,
                customer_id=data["customer_id"],
                status=status,
                price_id=expected_price_id,
                period_start=period_start,
                period_end=period_end,
                cancel_at=cancel_at,
                checkout_token=token,
                event_source=event_source,
                allow_expired_intent=True,
            ) or changed
        return changed
