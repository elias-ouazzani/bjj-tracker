"""PostHog product analytics — server-side event capture.

Analytics must NEVER break the app. Two safety rails enforce that:
  1. If POSTHOG_KEY is unset (or posthog isn't installed), every function
     here is a silent no-op — the app behaves identically.
  2. Every network call is wrapped in try/except and only ever logs a
     warning. A PostHog outage cannot take down a user's training log.

All events are keyed to the user's Firebase `uid` as the PostHog
`distinct_id` — the same identifier used everywhere else in the app. That
means if we add browser-side tracking later, both sides stitch into one
person automatically.

Configure via env (see .env.example):
  POSTHOG_KEY   — project API key (starts with phc_). Absent => tracking off.
  POSTHOG_HOST  — https://us.i.posthog.com (default) or https://eu.i.posthog.com
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("strain.analytics")

try:
    from posthog import Posthog
except ImportError:  # pragma: no cover - library not installed => analytics off
    Posthog = None
    log.info("posthog not installed — analytics disabled")

# Created lazily on first capture(), then cached. `False` = "checked, disabled".
_client: "Posthog | None | bool" = None


def _get_client() -> "Posthog | None":
    """Return a configured PostHog client, or None if analytics is off.

    Resolves config from the environment the first time it's called and
    caches the result (including the disabled state) so we don't re-check
    on every event.
    """
    global _client
    if _client is not None:
        return _client or None  # False -> None

    if Posthog is None:
        _client = False
        return None
    key = os.environ.get("POSTHOG_KEY", "")
    if not key.startswith("phc_"):
        log.info("POSTHOG_KEY not set — analytics disabled")
        _client = False
        return None
    host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
    _client = Posthog(project_api_key=key, host=host)
    log.info("analytics enabled host=%s", host)
    return _client


def capture(event: str, distinct_id: str, properties: dict | None = None) -> None:
    """Record one event for a user. Silent no-op if analytics is off.

    `properties` may include the special key ``$set`` to attach durable
    person properties (e.g. email, name) — that's how PostHog "identifies"
    a user from the backend, no separate call needed.
    """
    client = _get_client()
    if client is None or not distinct_id:
        return
    try:
        client.capture(event, distinct_id=distinct_id, properties=properties or {})
    except Exception:
        # exc_info so we can debug, but WARNING not ERROR — this is non-fatal.
        log.warning("analytics capture failed event=%s", event, exc_info=True)
