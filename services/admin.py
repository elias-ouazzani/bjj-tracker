"""Admin authorization.

`is_admin(email)` gates the /admin page. Admin access is a SECURITY boundary,
not a feature toggle — so it's an explicit email allow-list (env var), never a
PostHog feature flag (which could be rolled out / flipped by accident).

Env:
- ADMIN_EMAILS — comma-separated allow-list, e.g. "you@x.com,teammate@x.com".
  Unset ⇒ nobody is admin (safe default).
"""

from __future__ import annotations

import os


def _admin_emails() -> set[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(email: str | None) -> bool:
    """True if `email` is on the ADMIN_EMAILS allow-list (case-insensitive)."""
    if not email:
        return False
    return email.strip().lower() in _admin_emails()
