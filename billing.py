"""Razorpay subscription operations and normalized, verified webhook events."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from typing import Protocol

import requests

import account_store
import entitlements


class PaymentProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedSubscriptionEvent:
    """Normalized event returned only after an adapter validates provider evidence."""

    event_id: str
    user_id: str
    event_type: str
    subscription_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None


class PaymentProvider(Protocol):
    name: str

    def create_checkout_session(self, *, user_id: str, plan: str, currency: str,
                                return_url: str, contact: dict) -> dict:
        ...

    def verify_event(self, payload: bytes, signature: str | None) -> VerifiedSubscriptionEvent | None:
        ...

    def verify_checkout_payment(self, *, subscription_id: str, payment_id: str, signature: str) -> bool:
        ...


class RazorpaySubscriptionProvider:
    name = "razorpay"
    api_base = "https://api.razorpay.com/v1"

    def __init__(self):
        self.key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
        self.key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        self.webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()

    def _credentials(self):
        if not self.key_id or not self.key_secret:
            raise PaymentProviderUnavailable("Razorpay API credentials are not configured.")
        return self.key_id, self.key_secret

    def _request(self, method, path, *, body=None):
        try:
            response = requests.request(
                method,
                f"{self.api_base}{path}",
                auth=self._credentials(),
                json=body,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise PaymentProviderUnavailable("Could not connect to Razorpay. Please try again.") from exc
        try:
            result = response.json()
        except ValueError:
            result = {}
        if not response.ok:
            # Do not pass provider response bodies or credentials back to the browser.
            raise PaymentProviderUnavailable(
                f"Razorpay could not complete the request (HTTP {response.status_code})."
            )
        return result

    def create_checkout_session(self, *, user_id, plan, currency, return_url, contact):
        if plan != "pro":
            raise ValueError("Only the Pro plan can be checked out.")
        plan_id = os.environ.get(f"RAZORPAY_PRO_PLAN_ID_{currency}", "").strip()
        if not plan_id:
            raise PaymentProviderUnavailable(
                f"The Razorpay {currency} subscription plan ID is not configured."
            )
        plan_details = self._request("GET", f"/plans/{plan_id}")
        item = plan_details.get("item") or {}
        expected_amount = entitlements.pro_prices()[currency]["amount_minor"]
        if item.get("currency") != currency or item.get("amount") != expected_amount:
            raise PaymentProviderUnavailable(
                "The Razorpay plan amount does not match the displayed price. Check the plan ID and app pricing settings."
            )
        subscription = self._request(
            "POST",
            "/subscriptions",
            body={
                "plan_id": plan_id,
                "total_count": 1200,
                "quantity": 1,
                "customer_notify": True,
                "notes": {"ennoic_user_id": user_id},
            },
        )
        subscription_id = subscription.get("id")
        if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
            raise PaymentProviderUnavailable("Razorpay returned an invalid subscription response.")
        return {
            "provider": self.name,
            "key_id": self.key_id,
            "subscription_id": subscription_id,
            "prefill": {
                "name": contact["full_name"],
                "email": contact["email"],
                "contact": contact["phone"],
            },
        }

    def verify_checkout_payment(self, *, subscription_id, payment_id, signature):
        _key_id, key_secret = self._credentials()
        message = f"{payment_id}|{subscription_id}".encode("utf-8")
        expected = hmac.new(key_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_event(self, payload, signature):
        if not self.webhook_secret:
            raise PaymentProviderUnavailable("Razorpay webhook secret is not configured.")
        if not isinstance(signature, str) or not signature:
            raise ValueError("Missing Razorpay webhook signature.")
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid Razorpay webhook signature.")
        try:
            event = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid Razorpay webhook payload.") from exc

        event_id = event.get("id")
        razorpay_event_type = event.get("event")
        normalized_types = {
            "subscription.activated": "activated",
            "subscription.resumed": "activated",
            "subscription.charged": "renewed",
            "subscription.cancelled": "canceled",
            "subscription.completed": "expired",
            "subscription.halted": "payment_failed",
            "subscription.paused": "payment_failed",
        }
        normalized_type = normalized_types.get(razorpay_event_type)
        if not normalized_type:
            return None
        try:
            subscription = event["payload"]["subscription"]["entity"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Razorpay subscription event is incomplete.") from exc
        notes = subscription.get("notes") or {}
        user_id = notes.get("ennoic_user_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Razorpay event ID is missing.")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("Razorpay event is missing the Ennoic account reference.")
        return VerifiedSubscriptionEvent(
            event_id=event_id,
            user_id=user_id,
            event_type=normalized_type,
            subscription_id=subscription.get("id"),
            period_start=_iso_timestamp(subscription.get("current_start")),
            period_end=_iso_timestamp(subscription.get("current_end")),
        )

    def cancel_subscription(self, *, subscription_id):
        self._request("POST", f"/subscriptions/{subscription_id}/cancel", body={"cancel_at_cycle_end": 1})


def _iso_timestamp(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class SubscriptionService:
    def __init__(self, provider: PaymentProvider | None = None):
        self.provider = provider

    def _provider(self):
        if self.provider is not None:
            return self.provider
        if os.environ.get("RAZORPAY_KEY_ID") or os.environ.get("RAZORPAY_KEY_SECRET"):
            return RazorpaySubscriptionProvider()
        return None

    def is_configured(self, currency):
        if currency not in {"INR", "USD"}:
            return False
        if self.provider is not None:
            return True
        return all((
            os.environ.get("RAZORPAY_KEY_ID", "").strip(),
            os.environ.get("RAZORPAY_KEY_SECRET", "").strip(),
            os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip(),
            os.environ.get(f"RAZORPAY_PRO_PLAN_ID_{currency}", "").strip(),
        ))

    def create_checkout_session(self, *, user_id, plan, currency="USD", return_url, contact):
        if plan != "pro":
            raise ValueError("Only the Pro plan can be checked out.")
        if currency not in {"INR", "USD"}:
            raise ValueError("Unsupported checkout currency.")
        provider = self._provider()
        if provider is None:
            raise PaymentProviderUnavailable("Checkout is not configured.")
        return provider.create_checkout_session(
            user_id=user_id, plan=plan, currency=currency, return_url=return_url, contact=contact
        )

    def verify_checkout_payment(self, *, subscription_id, payment_id, signature):
        provider = self._provider()
        if provider is None:
            raise PaymentProviderUnavailable("Checkout is not configured.")
        return provider.verify_checkout_payment(
            subscription_id=subscription_id, payment_id=payment_id, signature=signature
        )

    def process_provider_event(self, payload, signature):
        provider = self._provider()
        if provider is None:
            raise PaymentProviderUnavailable("No payment provider is configured.")
        event = provider.verify_event(payload, signature)
        if event is None:
            return False
        if not isinstance(event, VerifiedSubscriptionEvent):
            raise ValueError("The payment provider returned an unverified subscription event.")
        return account_store.apply_verified_subscription_event(
            provider=provider.name,
            event_id=event.event_id,
            user_id=event.user_id,
            event_type=event.event_type,
            subscription_id=event.subscription_id,
            period_start=event.period_start,
            period_end=event.period_end,
        )

    def cancel_subscription(self, *, subscription_id):
        provider = self._provider()
        if provider is None:
            raise PaymentProviderUnavailable("Subscription cancellation is not configured.")
        provider.cancel_subscription(subscription_id=subscription_id)


subscription_service = SubscriptionService()
