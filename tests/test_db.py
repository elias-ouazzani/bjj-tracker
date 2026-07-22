"""Tests for db.py — Firestore client is mocked, no network calls."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

import db
from models import CardioData, GrapplingData, RecoveryActivity, RecoveryLog, Session


@pytest.fixture
def fake_client(monkeypatch):
    """Replace db._client with a MagicMock client."""
    client = MagicMock()
    monkeypatch.setattr(db, "_client", lambda: client)
    return client


def _bjj_session(user_id="u1", id_=None) -> Session:
    return Session(
        id=id_,
        user_id=user_id,
        started_at=datetime(2026, 5, 22, 9, 0),
        data=GrapplingData(discipline="bjj", drilling_minutes=30, sparring_rounds=4),
    )


# ------------- save_session -------------

def test_save_session_new_assigns_id(fake_client):
    """When session.id is None, Firestore generates a new ID."""
    doc_ref = MagicMock()
    doc_ref.id = "auto-generated-id"
    fake_client.collection.return_value.document.return_value = doc_ref

    saved = db.save_session(_bjj_session(id_=None))

    assert saved.id == "auto-generated-id"
    fake_client.collection.assert_called_with("sessions")
    fake_client.collection.return_value.document.assert_called_with()  # no-arg = auto-gen
    doc_ref.set.assert_called_once()
    written = doc_ref.set.call_args[0][0]
    assert written["id"] == "auto-generated-id"
    assert written["user_id"] == "u1"


def test_save_session_existing_overwrites(fake_client):
    """When session.id is set, that doc is overwritten."""
    saved = db.save_session(_bjj_session(id_="existing-id"))

    assert saved.id == "existing-id"
    fake_client.collection.return_value.document.assert_called_once_with("existing-id")
    fake_client.collection.return_value.document.return_value.set.assert_called_once()


# ------------- get_session -------------

def test_get_session_exists(fake_client):
    session = _bjj_session(id_="abc")
    snap = MagicMock()
    snap.exists = True
    snap.to_dict.return_value = session.model_dump(mode="json")
    fake_client.collection.return_value.document.return_value.get.return_value = snap

    result = db.get_session("abc")
    assert isinstance(result, Session)
    assert result.id == "abc"


def test_get_session_missing(fake_client):
    snap = MagicMock()
    snap.exists = False
    fake_client.collection.return_value.document.return_value.get.return_value = snap

    assert db.get_session("nope") is None


# ------------- delete_session -------------

def test_delete_session(fake_client):
    db.delete_session("abc")
    fake_client.collection.return_value.document.assert_called_once_with("abc")
    fake_client.collection.return_value.document.return_value.delete.assert_called_once()


# ------------- list_sessions -------------

def test_list_sessions_filters_by_user(fake_client):
    in_range = _bjj_session(id_="a", user_id="u1")
    in_range.started_at = datetime(2026, 5, 22, 10, 0)
    out_of_range = _bjj_session(id_="b", user_id="u1")
    out_of_range.started_at = datetime(2026, 4, 1, 10, 0)

    doc_a = MagicMock(); doc_a.to_dict.return_value = in_range.model_dump(mode="json")
    doc_b = MagicMock(); doc_b.to_dict.return_value = out_of_range.model_dump(mode="json")

    query = fake_client.collection.return_value.where.return_value
    query.stream.return_value = [doc_a, doc_b]

    results = db.list_sessions(
        user_id="u1",
        start=datetime(2026, 5, 1, 0, 0),
        end=datetime(2026, 5, 31, 23, 59),
    )

    assert len(results) == 1  # out_of_range filtered out by Python
    assert results[0].id == "a"


def test_list_sessions_empty(fake_client):
    query = fake_client.collection.return_value.where.return_value
    query.stream.return_value = []
    assert db.list_sessions("u1", datetime(2026, 1, 1), datetime(2026, 12, 31)) == []


def test_list_sessions_mixed_disciplines(fake_client):
    """Discriminated union round-trips: BJJ + cardio sessions in the same query."""
    bjj = _bjj_session(id_="bjj1", user_id="u1")
    bjj.started_at = datetime(2026, 5, 22, 9, 0)
    cardio = Session(
        id="cardio1",
        user_id="u1",
        started_at=datetime(2026, 5, 22, 17, 0),
        data=CardioData(discipline="cardio", activity_type="run", duration_minutes=45),
    )

    docs = [MagicMock(), MagicMock()]
    docs[0].to_dict.return_value = bjj.model_dump(mode="json")
    docs[1].to_dict.return_value = cardio.model_dump(mode="json")
    query = fake_client.collection.return_value.where.return_value
    query.stream.return_value = docs

    results = db.list_sessions("u1", datetime(2026, 5, 1), datetime(2026, 5, 31))
    assert len(results) == 2
    assert isinstance(results[0].data, GrapplingData)
    assert isinstance(results[1].data, CardioData)


# ------------- list_all_sessions (admin) -------------

def test_list_all_sessions_returns_every_user(fake_client):
    s1 = _bjj_session(id_="a", user_id="u1")
    s2 = _bjj_session(id_="b", user_id="u2")
    doc1 = MagicMock(); doc1.to_dict.return_value = s1.model_dump(mode="json")
    doc2 = MagicMock(); doc2.to_dict.return_value = s2.model_dump(mode="json")
    fake_client.collection.return_value.stream.return_value = [doc1, doc2]

    results = db.list_all_sessions()
    assert {r.user_id for r in results} == {"u1", "u2"}
    fake_client.collection.assert_called_with("sessions")


def test_list_all_sessions_skips_unparseable_doc(fake_client):
    """A single malformed/legacy doc must not sink the whole admin read."""
    good = _bjj_session(id_="ok", user_id="u1")
    doc_good = MagicMock(); doc_good.to_dict.return_value = good.model_dump(mode="json")
    doc_bad = MagicMock(); doc_bad.id = "legacy"; doc_bad.to_dict.return_value = {"nope": 1}
    fake_client.collection.return_value.stream.return_value = [doc_good, doc_bad]

    results = db.list_all_sessions()
    assert [r.id for r in results] == ["ok"]  # bad doc skipped, good one kept


# ------------- _client() initialization -------------

def test_client_with_credentials_env(monkeypatch):
    monkeypatch.setattr(db.firebase_admin, "_apps", {})
    fake_init = MagicMock()
    fake_cert = MagicMock(return_value="cert")
    monkeypatch.setattr(db.firebase_admin, "initialize_app", fake_init)
    monkeypatch.setattr(db.credentials, "Certificate", fake_cert)
    monkeypatch.setattr(db.firestore, "client", MagicMock(return_value="c"))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/key.json")
    db._client.cache_clear()

    db._client()

    fake_cert.assert_called_once_with("/path/key.json")
    fake_init.assert_called_once_with("cert")


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


# ------------- recovery storage -------------

def _recovery(user_id="u1", id_=None) -> RecoveryLog:
    return RecoveryLog(
        id=id_,
        user_id=user_id,
        logged_at=datetime(2026, 6, 15, 12, 0),
        sleep_hours=8,
        activities=[RecoveryActivity(activity_type="sauna", minutes=15)],
    )


def test_save_recovery_new_assigns_id(fake_client):
    doc_ref = MagicMock()
    doc_ref.id = "rec-auto-id"
    fake_client.collection.return_value.document.return_value = doc_ref

    saved = db.save_recovery(_recovery(id_=None))

    assert saved.id == "rec-auto-id"
    fake_client.collection.assert_called_with("recovery_logs")
    doc_ref.set.assert_called_once()
    written = doc_ref.set.call_args[0][0]
    assert written["id"] == "rec-auto-id"
    assert written["sleep_hours"] == 8


def test_save_recovery_existing_overwrites(fake_client):
    saved = db.save_recovery(_recovery(id_="existing"))
    assert saved.id == "existing"
    fake_client.collection.return_value.document.assert_called_once_with("existing")


def test_get_recovery_exists(fake_client):
    rec = _recovery(id_="abc")
    snap = MagicMock()
    snap.exists = True
    snap.to_dict.return_value = rec.model_dump(mode="json")
    fake_client.collection.return_value.document.return_value.get.return_value = snap

    result = db.get_recovery("abc")
    assert isinstance(result, RecoveryLog)
    assert result.id == "abc"


def test_get_recovery_missing(fake_client):
    snap = MagicMock()
    snap.exists = False
    fake_client.collection.return_value.document.return_value.get.return_value = snap
    assert db.get_recovery("nope") is None


def test_delete_recovery(fake_client):
    db.delete_recovery("abc")
    fake_client.collection.return_value.document.assert_called_once_with("abc")
    fake_client.collection.return_value.document.return_value.delete.assert_called_once()


def test_list_recovery_filters_by_date(fake_client):
    in_range = _recovery(id_="a")
    in_range.logged_at = datetime(2026, 6, 15, 12)
    out_of_range = _recovery(id_="b")
    out_of_range.logged_at = datetime(2026, 4, 1, 12)

    doc_a = MagicMock(); doc_a.to_dict.return_value = in_range.model_dump(mode="json")
    doc_b = MagicMock(); doc_b.to_dict.return_value = out_of_range.model_dump(mode="json")
    query = fake_client.collection.return_value.where.return_value
    query.stream.return_value = [doc_a, doc_b]

    results = db.list_recovery(
        user_id="u1",
        start=datetime(2026, 6, 1),
        end=datetime(2026, 6, 30),
    )
    assert len(results) == 1
    assert results[0].id == "a"


def test_list_all_recovery_returns_every_user(fake_client):
    r1 = _recovery(id_="a", user_id="u1")
    r2 = _recovery(id_="b", user_id="u2")
    doc1 = MagicMock(); doc1.to_dict.return_value = r1.model_dump(mode="json")
    doc2 = MagicMock(); doc2.to_dict.return_value = r2.model_dump(mode="json")
    fake_client.collection.return_value.stream.return_value = [doc1, doc2]

    results = db.list_all_recovery()
    assert {r.user_id for r in results} == {"u1", "u2"}
    fake_client.collection.assert_called_with("recovery_logs")


def test_list_all_recovery_skips_unparseable_doc(fake_client):
    good = _recovery(id_="ok", user_id="u1")
    doc_good = MagicMock(); doc_good.to_dict.return_value = good.model_dump(mode="json")
    doc_bad = MagicMock(); doc_bad.id = "legacy"; doc_bad.to_dict.return_value = {"nope": 1}
    fake_client.collection.return_value.stream.return_value = [doc_good, doc_bad]

    results = db.list_all_recovery()
    assert [r.id for r in results] == ["ok"]
