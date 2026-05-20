"""Firestore read/write for bjj-tracker.

All data goes into one collection: "sessions". Each Session document's
ID is "{date}_{slot}" (e.g. "2026-05-20_AM") — that gives us uniqueness
per (date, slot) for free, and makes lookups trivial.
"""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from models import Session

SESSIONS_COLLECTION = "sessions"


@lru_cache(maxsize=1)
def _client():
    """Initialize Firestore once and cache the client.

    Auth path:
      - On Cloud Run: uses the attached service account automatically.
      - Locally: reads GOOGLE_APPLICATION_CREDENTIALS env var pointing
        to a service account JSON file.
    """
    if not firebase_admin._apps:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path:
            firebase_admin.initialize_app(credentials.Certificate(cred_path))
        else:
            firebase_admin.initialize_app()
    return firestore.client()


def save_session(session: Session) -> None:
    """Upsert a Session into Firestore. Overwrites if the ID already exists."""
    doc = _client().collection(SESSIONS_COLLECTION).document(session.id)
    doc.set(session.model_dump(mode="json"))


def get_session(session_id: str) -> Session | None:
    """Fetch one session by ID. Returns None if not found."""
    snap = _client().collection(SESSIONS_COLLECTION).document(session_id).get()
    if not snap.exists:
        return None
    return Session(**snap.to_dict())


def delete_session(session_id: str) -> None:
    """Delete a session by ID. Idempotent — no-op if the doc doesn't exist."""
    _client().collection(SESSIONS_COLLECTION).document(session_id).delete()


def list_sessions(start: date, end: date) -> list[Session]:
    """All sessions with `start <= date <= end`, oldest first."""
    query = (
        _client()
        .collection(SESSIONS_COLLECTION)
        .where(filter=FieldFilter("date", ">=", start.isoformat()))
        .where(filter=FieldFilter("date", "<=", end.isoformat()))
        .order_by("date")
    )
    return [Session(**doc.to_dict()) for doc in query.stream()]
