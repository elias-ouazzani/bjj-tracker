"""Admin authorization.

`is_admin(email)` gates the /admin page. Admin access is a SECURITY boundary,
not a feature toggle — so it's an explicit email allow-list (env var), never a
PostHog feature flag (which could be rolled out / flipped by accident).

Env:
- ADMIN_EMAILS — comma-separated allow-list, case-insensitive. Each entry is
  either a full email ("you@x.com") or a whole domain ("@atheal.com", note the
  leading @). Unset ⇒ nobody is admin (safe default).
    e.g. ADMIN_EMAILS="@atheal.com"          # anyone @atheal.com
         ADMIN_EMAILS="@atheal.com,me@x.com" # the domain + one extra person
"""

from __future__ import annotations

import os


def _admin_entries() -> set[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(email: str | None) -> bool:
    """True if `email` is allowed by ADMIN_EMAILS.

    Matches on either an exact email or a "@domain" entry (case-insensitive).
    """
    if not email or "@" not in email:
        return False
    email = email.strip().lower()
    domain = "@" + email.split("@", 1)[1]
    entries = _admin_entries()
    return email in entries or domain in entries
