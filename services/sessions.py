"""Session service — business operations on training sessions.

What lives here vs. db.py:
- db.py is dumb storage: given an ID, read/write/delete the doc.
- services.sessions enforces *who* can do *what*: a user cannot
  read, modify, or delete another user's session, even if they
  guess the ID. db.py has no concept of ownership.

The UI layer (main.py) and any future HTTP API should go through
this module, never directly through db.
"""

from __future__ import annotations

import logging
from datetime import datetime

import db
from models import Session

log = logging.getLogger("strain.services.sessions")


class SessionNotFound(Exception):
    """Raised when a session ID doesn't exist in the store."""


class SessionAccessDenied(Exception):
    """Raised when a user attempts to touch a session that isn't theirs."""


def save_user_session(user_id: str, session: Session) -> Session:
    """Save (create or update) a session belonging to `user_id`.

    Rejects the write if the session's user_id doesn't match the caller —
    prevents a client from writing under someone else's UID.
    For updates, also rejects if the existing doc belongs to someone else.
    """
    if session.user_id != user_id:
        log.warning(
            "save_user_session: caller=%s session.user_id=%s — rejecting",
            user_id, session.user_id,
        )
        raise SessionAccessDenied("Session user_id does not match caller")

    if session.id is not None:
        existing = db.get_session(session.id)
        if existing is not None and existing.user_id != user_id:
            log.warning(
                "save_user_session: caller=%s tried to overwrite doc owned by %s",
                user_id, existing.user_id,
            )
            raise SessionAccessDenied("Cannot overwrite another user's session")

    saved = db.save_session(session)
    log.info(
        "session.save uid=%s id=%s discipline=%s",
        user_id, saved.id, saved.data.discipline,
    )
    return saved


def list_user_sessions(user_id: str, start: datetime, end: datetime) -> list[Session]:
    """All sessions for `user_id` with `start <= started_at <= end`,
    sorted ascending."""
    return db.list_sessions(user_id, start, end)


def delete_user_session(user_id: str, session_id: str) -> None:
    """Delete a session, but only if it belongs to `user_id`.

    Raises SessionNotFound if the doc doesn't exist, SessionAccessDenied
    if it exists but belongs to someone else. Without these checks, the
    delete endpoint would let any signed-in user wipe any other user's
    history by ID guessing.
    """
    existing = db.get_session(session_id)
    if existing is None:
        raise SessionNotFound(session_id)
    if existing.user_id != user_id:
        log.warning(
            "delete_user_session: caller=%s tried to delete doc owned by %s",
            user_id, existing.user_id,
        )
        raise SessionAccessDenied("Cannot delete another user's session")
    db.delete_session(session_id)
    log.info("session.delete uid=%s id=%s", user_id, session_id)
