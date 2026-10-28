"""Small, privacy-safe timing traces for chat requests."""

from contextvars import ContextVar
from datetime import datetime, timezone
import secrets
import time


_trace = ContextVar("ennoic_latency_trace", default=None)


def begin_trace():
    context = {"id": secrets.token_hex(4), "started": time.perf_counter()}
    _trace.set(context)
    mark_trace("request.start")


def mark_trace(stage, *, duration_ms=None, **details):
    context = _trace.get()
    if context is None:
        return

    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    elapsed_ms = round((time.perf_counter() - context["started"]) * 1000)
    fields = [
        f"timestamp={timestamp}",
        f"trace={context['id']}",
        f"elapsed_ms={elapsed_ms}",
        f"stage={stage}",
    ]
    if duration_ms is not None:
        fields.append(f"duration_ms={duration_ms}")
    for key, value in details.items():
        if value is not None:
            fields.append(f"{key}={value}")
    print("LATENCY " + " ".join(fields), flush=True)


def end_trace(status_code):
    mark_trace("request.complete", status=status_code)
    _trace.set(None)
