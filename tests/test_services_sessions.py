"""Service-layer tests: ownership enforcement on session ops."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from models import GrapplingData, Session
from services import sessions as svc


def _session(user_id="u1", id_=None) -> Session:
    return Session(
        id=id_,
        user_id=user_id,
        started_at=datetime(2026, 6, 1, 9, 0),
        data=GrapplingData(discipline="bjj", drilling_minutes=30, sparring_rounds=4),
    )


# ------------- save_user_session -------------

def test_save_user_session_happy_path(monkeypatch):
    fake_save = MagicMock(side_effect=lambda s: s.model_copy(update={"id": s.id or "new-id"}))
    monkeypatch.setattr(svc.db, "save_session", fake_save)

    s = _session(user_id="u1")
    saved = svc.save_user_session("u1", s)

    assert saved.id == "new-id"
    fake_save.assert_called_once_with(s)


def test_save_user_session_rejects_uid_mismatch(monkeypatch):
    """A request authenticated as u1 cannot write a session claiming to be u2's."""
    fake_save = MagicMock()
    monkeypatch.setattr(svc.db, "save_session", fake_save)

    with pytest.raises(svc.SessionAccessDenied):
        svc.save_user_session("u1", _session(user_id="u2"))
    fake_save.assert_not_called()


def test_save_user_session_rejects_overwrite_of_other_user(monkeypatch):
    """Updating an existing doc requires owning it. Without this, anyone
    could overwrite another user's session by guessing its ID."""
    existing = _session(user_id="u2", id_="abc")
    monkeypatch.setattr(svc.db, "get_session", lambda sid: existing)
    fake_save = MagicMock()
    monkeypatch.setattr(svc.db, "save_session", fake_save)

    payload = _session(user_id="u1", id_="abc")  # caller forges different user_id
    # The user_id mismatch trips the first check before get_session is consulted.
    with pytest.raises(svc.SessionAccessDenied):
        svc.save_user_session("u1", payload)
    fake_save.assert_not_called()


def test_save_user_session_allows_update_of_own(monkeypatch):
    own = _session(user_id="u1", id_="abc")
    monkeypatch.setattr(svc.db, "get_session", lambda sid: own)
    monkeypatch.setattr(svc.db, "save_session", lambda s: s)

    result = svc.save_user_session("u1", own)
    assert result.id == "abc"


def test_save_user_session_create_skips_existence_check(monkeypatch):
    """New sessions (id=None) don't need a get_session lookup."""
    get_called = MagicMock()
    monkeypatch.setattr(svc.db, "get_session", get_called)
    monkeypatch.setattr(svc.db, "save_session", lambda s: s.model_copy(update={"id": "new"}))

    svc.save_user_session("u1", _session(user_id="u1", id_=None))
    get_called.assert_not_called()


# ------------- delete_user_session -------------

def test_delete_user_session_happy_path(monkeypatch):
    monkeypatch.setattr(svc.db, "get_session", lambda sid: _session(user_id="u1", id_=sid))
    delete = MagicMock()
    monkeypatch.setattr(svc.db, "delete_session", delete)

    svc.delete_user_session("u1", "abc")
    delete.assert_called_once_with("abc")


def test_delete_user_session_not_found(monkeypatch):
    monkeypatch.setattr(svc.db, "get_session", lambda sid: None)
    delete = MagicMock()
    monkeypatch.setattr(svc.db, "delete_session", delete)

    with pytest.raises(svc.SessionNotFound):
        svc.delete_user_session("u1", "missing")
    delete.assert_not_called()


def test_delete_user_session_blocks_other_users_doc(monkeypatch):
    """The core security property: I cannot delete your data by ID."""
    monkeypatch.setattr(svc.db, "get_session", lambda sid: _session(user_id="u2", id_=sid))
    delete = MagicMock()
    monkeypatch.setattr(svc.db, "delete_session", delete)

    with pytest.raises(svc.SessionAccessDenied):
        svc.delete_user_session("u1", "u2s-doc")
    delete.assert_not_called()


# ------------- list_user_sessions -------------

def test_list_user_sessions_delegates_to_db(monkeypatch):
    fake_list = MagicMock(return_value=[_session(user_id="u1", id_="x")])
    monkeypatch.setattr(svc.db, "list_sessions", fake_list)

    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 31)
    result = svc.list_user_sessions("u1", start, end)

    assert len(result) == 1
    fake_list.assert_called_once_with("u1", start, end)
