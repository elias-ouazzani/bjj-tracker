"""Tests for db.py — Firestore client is mocked, no network calls."""

from datetime import date
from unittest.mock import MagicMock

import pytest

import db
from models import LogEntry, Session, Tag


@pytest.fixture
def fake_client(monkeypatch):
    """Replace db._client with a MagicMock client. Returns the client mock."""
    client = MagicMock()
    monkeypatch.setattr(db, "_client", lambda: client)
    return client


def _sample_session() -> Session:
    return Session(
        id="2026-05-20_AM",
        date=date(2026, 5, 20),
        slot="AM",
        drilling_minutes=30,
        sparring_rounds=4,
        log_entries=[LogEntry(notes_raw="x", category="drill", tags=[Tag(technique="T", position="P")])],
    )


def test_save_session_writes_to_correct_doc(fake_client):
    session = _sample_session()
    db.save_session(session)

    fake_client.collection.assert_called_once_with("sessions")
    fake_client.collection.return_value.document.assert_called_once_with("2026-05-20_AM")
    set_call = fake_client.collection.return_value.document.return_value.set
    set_call.assert_called_once()
    written = set_call.call_args[0][0]
    assert written["id"] == "2026-05-20_AM"
    assert written["date"] == "2026-05-20"  # mode="json" serializes date to ISO


def test_get_session_returns_session_when_exists(fake_client):
    session = _sample_session()
    snap = MagicMock()
    snap.exists = True
    snap.to_dict.return_value = session.model_dump(mode="json")
    fake_client.collection.return_value.document.return_value.get.return_value = snap

    result = db.get_session("2026-05-20_AM")
    assert isinstance(result, Session)
    assert result.id == "2026-05-20_AM"


def test_delete_session_targets_correct_doc(fake_client):
    db.delete_session("2026-05-20_AM")
    fake_client.collection.assert_called_once_with("sessions")
    fake_client.collection.return_value.document.assert_called_once_with("2026-05-20_AM")
    fake_client.collection.return_value.document.return_value.delete.assert_called_once()


def test_get_session_returns_none_when_missing(fake_client):
    snap = MagicMock()
    snap.exists = False
    fake_client.collection.return_value.document.return_value.get.return_value = snap

    assert db.get_session("nonexistent") is None


def test_list_sessions_queries_date_range(fake_client):
    session = _sample_session()
    doc = MagicMock()
    doc.to_dict.return_value = session.model_dump(mode="json")
    query = fake_client.collection.return_value.where.return_value.where.return_value.order_by.return_value
    query.stream.return_value = [doc]

    result = db.list_sessions(date(2026, 5, 1), date(2026, 5, 31))
    assert len(result) == 1
    assert result[0].id == "2026-05-20_AM"


def test_list_sessions_empty(fake_client):
    query = fake_client.collection.return_value.where.return_value.where.return_value.order_by.return_value
    query.stream.return_value = []

    assert db.list_sessions(date(2026, 5, 1), date(2026, 5, 31)) == []


# --- _client() initialization paths ---

def test_client_with_credentials_env(monkeypatch):
    monkeypatch.setattr(db.firebase_admin, "_apps", {})
    fake_init = MagicMock()
    fake_cert = MagicMock(return_value="cert-obj")
    fake_firestore = MagicMock(return_value="client-instance")
    monkeypatch.setattr(db.firebase_admin, "initialize_app", fake_init)
    monkeypatch.setattr(db.credentials, "Certificate", fake_cert)
    monkeypatch.setattr(db.firestore, "client", fake_firestore)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/sa.json")
    db._client.cache_clear()

    result = db._client()

    fake_cert.assert_called_once_with("/path/to/sa.json")
    fake_init.assert_called_once_with("cert-obj")
    assert result == "client-instance"


def test_client_default_credentials(monkeypatch):
    monkeypatch.setattr(db.firebase_admin, "_apps", {})
    fake_init = MagicMock()
    monkeypatch.setattr(db.firebase_admin, "initialize_app", fake_init)
    monkeypatch.setattr(db.firestore, "client", MagicMock(return_value="c"))
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    db._client.cache_clear()

    db._client()

    fake_init.assert_called_once_with()


def test_client_skips_init_if_already_initialized(monkeypatch):
    monkeypatch.setattr(db.firebase_admin, "_apps", {"[DEFAULT]": object()})
    fake_init = MagicMock()
    monkeypatch.setattr(db.firebase_admin, "initialize_app", fake_init)
    monkeypatch.setattr(db.firestore, "client", MagicMock(return_value="c"))
    db._client.cache_clear()

    db._client()

    fake_init.assert_not_called()
