"""Central plan definitions, configurable limits, and account entitlement views."""

import os
from datetime import datetime, timezone

import account_store


DEFAULT_PLAN_VALUES = {
    "free": {
        "ai_requests_monthly": 40,
        "memory_capacity": 40,
        "conversation_history_limit": 10,
        "attachment_count": 3,
        "attachment_file_bytes": 5 * 1024 * 1024,
        "attachment_storage_bytes": 50 * 1024 * 1024,
        "file_analysis_requests_monthly": 0,
    },
    "pro": {
        "ai_requests_monthly": 1000,
        "memory_capacity": 1000,
        "conversation_history_limit": 0,
        "attachment_count": 20,
        "attachment_file_bytes": 50 * 1024 * 1024,
        "attachment_storage_bytes": 1024 * 1024 * 1024,
        "file_analysis_requests_monthly": 50,
    },
}

SUPPORTED_ATTACHMENT_MIME_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/csv",
)

ENV_LIMITS = {
    ("free", "ai_requests_monthly"): "ENNOIC_FREE_AI_REQUESTS_MONTHLY",
    ("pro", "ai_requests_monthly"): "ENNOIC_PRO_AI_REQUESTS_MONTHLY",
    ("free", "memory_capacity"): "ENNOIC_FREE_MEMORY_CAPACITY",
    ("pro", "memory_capacity"): "ENNOIC_PRO_MEMORY_CAPACITY",
    ("free", "conversation_history_limit"): "ENNOIC_FREE_CONVERSATION_HISTORY",
    ("pro", "conversation_history_limit"): "ENNOIC_PRO_CONVERSATION_HISTORY",
    ("free", "attachment_count"): "ENNOIC_FREE_ATTACHMENT_COUNT",
    ("pro", "attachment_count"): "ENNOIC_PRO_ATTACHMENT_COUNT",
    ("free", "attachment_file_bytes"): "ENNOIC_FREE_ATTACHMENT_FILE_BYTES",
    ("pro", "attachment_file_bytes"): "ENNOIC_PRO_ATTACHMENT_FILE_BYTES",
    ("free", "attachment_storage_bytes"): "ENNOIC_FREE_ATTACHMENT_STORAGE_BYTES",
    ("pro", "attachment_storage_bytes"): "ENNOIC_PRO_ATTACHMENT_STORAGE_BYTES",
    ("free", "file_analysis_requests_monthly"): "ENNOIC_FREE_FILE_ANALYSIS_MONTHLY",
    ("pro", "file_analysis_requests_monthly"): "ENNOIC_PRO_FILE_ANALYSIS_MONTHLY",
}

PRICE_DEFAULTS = {
    "INR": {"amount_minor": 17900, "currency": "INR", "display": "₹179"},
    "USD": {"amount_minor": 499, "currency": "USD", "display": "$4.99"},
}

PRICING_COPY = {
    "en": {
        "title": "Simple pricing for intelligent automation",
        "subtitle": "Start free. Move to Pro when you need more room.",
        "free": "Free",
        "pro": "Pro",
        "free_price": "Free",
        "no_payment": "No payment needed.",
        "billed_monthly": "Billed monthly. Checkout is not available yet.",
        "start_free": "Start free",
        "current_plan": "Current plan",
        "usage_ai": "AI requests this month: {used} of {limit}",
        "usage_memory": "Saved memories: {used} of {limit}",
        "usage_history": "Conversations available: {used} of {limit}",
        "usage_history_all": "All saved conversations are available",
        "upgrade": "Upgrade to Pro",
        "free_features": ["Unlimited task management", "{ai} AI requests each month", "Up to {memory} saved memories", "Up to {history} conversations in your history"],
        "pro_features": ["Unlimited task management", "{ai} AI requests each month", "Up to {memory} saved memories", "Full searchable conversation history"],
        "history_all": "Full",
        "per_month": "/month",
        "upgrade_unavailable": "Checkout is not available yet. Nothing was charged.",
    },
    "es": {
        "title": "Precios sencillos para una automatización inteligente",
        "subtitle": "Empieza gratis. Pásate a Pro cuando necesites más.",
        "free": "Gratis",
        "pro": "Pro",
        "free_price": "Gratis",
        "no_payment": "No necesitas pagar.",
        "billed_monthly": "Facturación mensual. El pago aún no está disponible.",
        "start_free": "Empezar gratis",
        "current_plan": "Plan actual",
        "usage_ai": "Solicitudes de IA este mes: {used} de {limit}",
        "usage_memory": "Recuerdos guardados: {used} de {limit}",
        "usage_history": "Conversaciones disponibles: {used} de {limit}",
        "usage_history_all": "Todas las conversaciones guardadas están disponibles",
        "upgrade": "Pasar a Pro",
        "free_features": ["Gestión de tareas ilimitada", "{ai} solicitudes de IA al mes", "Hasta {memory} recuerdos guardados", "Hasta {history} conversaciones en tu historial"],
        "pro_features": ["Gestión de tareas ilimitada", "{ai} solicitudes de IA al mes", "Hasta {memory} recuerdos guardados", "Historial completo de conversaciones con búsqueda"],
        "history_all": "Completo",
        "per_month": "/mes",
        "upgrade_unavailable": "El pago aún no está disponible. No se realizó ningún cobro.",
    },
    "fr": {
        "title": "Des tarifs simples pour une automatisation intelligente",
        "subtitle": "Commencez gratuitement. Passez à Pro selon vos besoins.",
        "free": "Gratuit",
        "pro": "Pro",
        "free_price": "Gratuit",
        "no_payment": "Aucun paiement requis.",
        "billed_monthly": "Facturation mensuelle. Le paiement n’est pas encore disponible.",
        "start_free": "Commencer gratuitement",
        "current_plan": "Offre actuelle",
        "usage_ai": "Requêtes IA ce mois-ci : {used} sur {limit}",
        "usage_memory": "Souvenirs enregistrés : {used} sur {limit}",
        "usage_history": "Conversations disponibles : {used} sur {limit}",
        "usage_history_all": "Toutes les conversations enregistrées sont disponibles",
        "upgrade": "Passer à Pro",
        "free_features": ["Gestion des tâches illimitée", "{ai} requêtes IA par mois", "Jusqu’à {memory} souvenirs enregistrés", "Jusqu’à {history} conversations dans votre historique"],
        "pro_features": ["Gestion des tâches illimitée", "{ai} requêtes IA par mois", "Jusqu’à {memory} souvenirs enregistrés", "Historique complet des conversations avec recherche"],
        "history_all": "Complet",
        "per_month": "/mois",
        "upgrade_unavailable": "Le paiement n’est pas encore disponible. Aucun paiement n’a été effectué.",
    },
}


def _configured_int(name, default, minimum=0):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


def plan_definitions():
    plans = {name: dict(values) for name, values in DEFAULT_PLAN_VALUES.items()}
    for (plan, entitlement), environment_name in ENV_LIMITS.items():
        plans[plan][entitlement] = _configured_int(
            environment_name,
            plans[plan][entitlement],
            minimum=0,
        )
    return plans


def pro_prices():
    prices = {currency: dict(value) for currency, value in PRICE_DEFAULTS.items()}
    prices["INR"]["amount_minor"] = _configured_int(
        "ENNOIC_PRO_PRICE_INR_MINOR", prices["INR"]["amount_minor"]
    )
    prices["USD"]["amount_minor"] = _configured_int(
        "ENNOIC_PRO_PRICE_USD_MINOR", prices["USD"]["amount_minor"]
    )
    prices["INR"]["display"] = f"₹{prices['INR']['amount_minor'] / 100:g}"
    prices["USD"]["display"] = f"${prices['USD']['amount_minor'] / 100:.2f}"
    return prices


def current_period(now=None):
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


def language_for(value):
    return value if value in PRICING_COPY else "en"


def public_pricing(language="en", selected_currency="USD"):
    language = language_for(language)
    selected_currency = selected_currency if selected_currency in PRICE_DEFAULTS else "USD"
    copy = PRICING_COPY[language]
    plans = plan_definitions()
    pro_history = plans["pro"]["conversation_history_limit"]
    free_history = plans["free"]["conversation_history_limit"]
    prices = pro_prices()
    return {
        "language": language,
        "selected_currency": selected_currency,
        "selected_price": dict(prices[selected_currency]),
        "copy": {key: value for key, value in copy.items() if not key.endswith("features")},
        "features": {
            "free": [
                entry.format(
                    ai=plans["free"]["ai_requests_monthly"],
                    memory=plans["free"]["memory_capacity"],
                    history=free_history,
                )
                for entry in copy["free_features"]
            ],
            "pro": [
                entry.format(
                    ai=plans["pro"]["ai_requests_monthly"],
                    memory=plans["pro"]["memory_capacity"],
                    history=copy["history_all"] if not pro_history else pro_history,
                )
                for entry in copy["pro_features"]
            ],
        },
        "prices": prices,
        "checkout_available": False,
    }


def account_entitlements(user_id):
    plans = plan_definitions()
    subscription = account_store.get_account_subscription(user_id) or {
        "plan": "free", "status": "free", "period_end": None, "provider": None,
    }
    plan = subscription["plan"] if subscription["plan"] == "pro" and subscription["status"] in {"active", "canceled"} else "free"
    limits = plans[plan]
    period_start, period_end = current_period()
    ai_used = account_store.get_usage(user_id, "ai_requests", period_start)
    memory_used = account_store.count_memories(user_id)
    conversation_count = account_store.count_conversations(user_id)
    configured_history_limit = limits["conversation_history_limit"]
    history_limit = None if plan == "pro" and configured_history_limit == 0 else configured_history_limit
    return {
        "plan": plan,
        "status": subscription["status"],
        "period_end": subscription.get("period_end"),
        "provider": subscription.get("provider"),
        "usage_period": {"start": period_start, "resets_at": period_end},
        "usage": {
            "ai_requests": {"used": ai_used, "limit": limits["ai_requests_monthly"], "resets_at": period_end},
            "memories": {"used": memory_used, "limit": limits["memory_capacity"]},
            "conversations": {
                "used": min(conversation_count, history_limit) if history_limit is not None else conversation_count,
                "total": conversation_count,
                "limit": history_limit,
            },
            "attachments": {
                "used": 0,
                "limit": limits["attachment_count"],
                "max_file_bytes": limits["attachment_file_bytes"],
                "storage_bytes": limits["attachment_storage_bytes"],
                "allowed_types": list(SUPPORTED_ATTACHMENT_MIME_TYPES),
                "available": False,
            },
            "file_analysis": {
                "used": 0,
                "limit": limits["file_analysis_requests_monthly"],
                "available": False,
            },
        },
        "features": {
            "tasks": True,
            "memory": True,
            "file_attachments": False,
            "file_analysis": False,
            "advanced_planning": False,
            "searchable_history": True,
        },
        "upgrade_url": "/pricing",
    }
