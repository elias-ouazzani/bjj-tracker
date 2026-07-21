"""Feature flags via PostHog — server-side evaluation.

`flag_on(key, user_id, email)` returns a bool: is this feature enabled for
this user? Wrap any new UI or route in it so features can ship dark and be
turned on per-user / by percentage from the PostHog dashboard — no redeploy.

Design choices:
- **Safe default OFF.** If PostHog isn't configured, is unreachable, or
  errors, `flag_on` returns False. A missing flag hides the feature rather
  than crashing the app or leaking a half-built feature.
- **Env override for local dev.** `FLAG_<KEY>=1` forces a flag on without
  touching PostHog (e.g. `FLAG_TRAINING_STREAK=1`). The override wins over
  everything, so you can develop offline.
- **Lazy, cached client.** Built on first use (after dotenv has loaded, like
  ai.py's agent), cached for the process. Local evaluation (fast, no per-call
  network hop) kicks in when POSTHOG_PERSONAL_API_KEY is set.

Env vars:
- POSTHOG_API_KEY           — project API key (required to talk to PostHog)
- POSTHOG_PERSONAL_API_KEY  — enables local flag evaluation (recommended)
- POSTHOG_HOST              — defaults to the EU cloud (GDPR; matches eur3)
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

log = logging.getLogger("strain.flags")

DEFAULT_HOST = "https://eu.i.posthog.com"


def _env_override(key: str) -> bool | None:
    """Read FLAG_<KEY> from the environment. None if unset."""
    val = os.environ.get(f"FLAG_{key.upper()}")
    if val is None:
        return None
    return val.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _client():
    """Build the PostHog client once, or return None if unconfigured."""
    api_key = os.environ.get("POSTHOG_API_KEY")
    if not api_key:
        log.info("flags: POSTHOG_API_KEY unset — flags default off "
                 "(FLAG_<KEY> env overrides still apply)")
        return None
    from posthog import Posthog  # pragma: no cover
    return Posthog(  # pragma: no cover
        api_key,
        host=os.environ.get("POSTHOG_HOST", DEFAULT_HOST),
        personal_api_key=os.environ.get("POSTHOG_PERSONAL_API_KEY"),
    )


def flag_on(key: str, user_id: str, email: str | None = None) -> bool:
    """True if feature `key` is enabled for this user. Safe default False.

    Evaluation order: FLAG_<KEY> env override → PostHog → False.
    `email` is passed as a person property so PostHog can target by email
    (e.g. "release to elias@… only").
    """
    override = _env_override(key)
    if override is not None:
        return override

    client = _client()
    if client is None:
        return False

    try:
        person_properties = {"email": email} if email else None
        return bool(client.feature_enabled(
            key, user_id, person_properties=person_properties,
        ))
    except Exception:
        log.exception("flags: feature_enabled failed key=%s", key)
        return False
