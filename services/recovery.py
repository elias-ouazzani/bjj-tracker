"""Recovery service — business operations on recovery logs.

Same split as services.sessions: db.py is dumb storage, this module
enforces ownership so one user cannot read, overwrite, or delete
another user's recovery log even by guessing its ID. The UI layer
(main.py) and any future HTTP API go through here, never db directly.
"""

from __future__ import annotations

import logging
from datetime import datetime

import db
from models import RecoveryLog

log = logging.getLogger("strain.services.recovery")


class RecoveryNotFound(Exception):
    """Raised when a recovery-log ID doesn't exist in the store."""


class RecoveryAccessDenied(Exception):
    """Raised when a user attempts to touch a recovery log that isn't theirs."""


def save_user_recovery(user_id: str, recovery: RecoveryLog) -> RecoveryLog:
    """Save (create or update) a recovery log belonging to `user_id`.

    Rejects the write if the log's user_id doesn't match the caller, and
    (for updates) if the existing doc belongs to someone else.
    """
    if recovery.user_id != user_id:
        log.warning(
            "save_user_recovery: caller=%s recovery.user_id=%s — rejecting",
            user_id, recovery.user_id,
        )
        raise RecoveryAccessDenied("Recovery user_id does not match caller")

    if recovery.id is not None:
        existing = db.get_recovery(recovery.id)
        if existing is not None and existing.user_id != user_id:
            log.warning(
                "save_user_recovery: caller=%s tried to overwrite doc owned by %s",
                user_id, existing.user_id,
            )
            raise RecoveryAccessDenied("Cannot overwrite another user's recovery log")

    saved = db.save_recovery(recovery)
    log.info(
        "recovery.save uid=%s id=%s sleep=%s activities=%d",
        user_id, saved.id, saved.sleep_hours, len(saved.activities),
    )
    return saved


def list_user_recovery(user_id: str, start: datetime, end: datetime) -> list[RecoveryLog]:
    """All recovery logs for `user_id` in [start, end], sorted ascending."""
    return db.list_recovery(user_id, start, end)


def delete_user_recovery(user_id: str, recovery_id: str) -> None:
    """Delete a recovery log, but only if it belongs to `user_id`.

    Raises RecoveryNotFound if the doc doesn't exist, RecoveryAccessDenied
    if it exists but belongs to someone else.
    """
    existing = db.get_recovery(recovery_id)
    if existing is None:
        raise RecoveryNotFound(recovery_id)
    if existing.user_id != user_id:
        log.warning(
            "delete_user_recovery: caller=%s tried to delete doc owned by %s",
            user_id, existing.user_id,
        )
        raise RecoveryAccessDenied("Cannot delete another user's recovery log")
    db.delete_recovery(recovery_id)
    log.info("recovery.delete uid=%s id=%s", user_id, recovery_id)
