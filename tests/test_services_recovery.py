"""Service-layer tests: ownership enforcement on recovery ops.

Mirrors test_services_sessions — the same security property applies:
a user must not be able to read, overwrite, or delete another user's
recovery log by guessing its ID.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from models import RecoveryActivity, RecoveryLog
from services import recovery as svc


def _recovery(user_id="u1", id_=None) -> RecoveryLog:
    return RecoveryLog(
        id=id_,
        user_id=user_id,
        logged_at=datetime(2026, 6, 15, 12, 0),
        sleep_hours=8,
        activities=[RecoveryActivity(activity_type="sauna", minutes=15)],
    )


# ------------- save_user_recovery -------------

def test_save_user_recovery_happy_path(monkeypatch):
    fake_save = MagicMock(side_effect=lambda r: r.model_copy(update={"id": r.id or "new-id"}))
    monkeypatch.setattr(svc.db, "save_recovery", fake_save)

    saved = svc.save_user_recovery("u1", _recovery(user_id="u1"))
    assert saved.id == "new-id"


def test_save_user_recovery_rejects_uid_mismatch(monkeypatch):
    fake_save = MagicMock()
    monkeypatch.setattr(svc.db, "save_recovery", fake_save)

    with pytest.raises(svc.RecoveryAccessDenied):
        svc.save_user_recovery("u1", _recovery(user_id="u2"))
    fake_save.assert_not_called()


def test_save_user_recovery_rejects_overwrite_of_other_user(monkeypatch):
    existing = _recovery(user_id="u2", id_="abc")
    monkeypatch.setattr(svc.db, "get_recovery", lambda rid: existing)
    fake_save = MagicMock()
    monkeypatch.setattr(svc.db, "save_recovery", fake_save)

    # caller u1 owns the payload but the stored doc belongs to u2
    payload = _recovery(user_id="u1", id_="abc")
    monkeypatch.setattr(svc.db, "get_recovery", lambda rid: existing)
    with pytest.raises(svc.RecoveryAccessDenied):
        svc.save_user_recovery("u1", payload)
    fake_save.assert_not_called()


def test_save_user_recovery_allows_update_of_own(monkeypatch):
    own = _recovery(user_id="u1", id_="abc")
    monkeypatch.setattr(svc.db, "get_recovery", lambda rid: own)
    monkeypatch.setattr(svc.db, "save_recovery", lambda r: r)

    result = svc.save_user_recovery("u1", own)
    assert result.id == "abc"


def test_save_user_recovery_create_skips_existence_check(monkeypatch):
    get_called = MagicMock()
    monkeypatch.setattr(svc.db, "get_recovery", get_called)
    monkeypatch.setattr(svc.db, "save_recovery", lambda r: r.model_copy(update={"id": "new"}))

    svc.save_user_recovery("u1", _recovery(user_id="u1", id_=None))
    get_called.assert_not_called()


# ------------- delete_user_recovery -------------

def test_delete_user_recovery_happy_path(monkeypatch):
    monkeypatch.setattr(svc.db, "get_recovery", lambda rid: _recovery(user_id="u1", id_=rid))
    delete = MagicMock()
    monkeypatch.setattr(svc.db, "delete_recovery", delete)

    svc.delete_user_recovery("u1", "abc")
    delete.assert_called_once_with("abc")


def test_delete_user_recovery_not_found(monkeypatch):
    monkeypatch.setattr(svc.db, "get_recovery", lambda rid: None)
    delete = MagicMock()
    monkeypatch.setattr(svc.db, "delete_recovery", delete)

    with pytest.raises(svc.RecoveryNotFound):
        svc.delete_user_recovery("u1", "missing")
    delete.assert_not_called()


def test_delete_user_recovery_blocks_other_users_doc(monkeypatch):
    monkeypatch.setattr(svc.db, "get_recovery", lambda rid: _recovery(user_id="u2", id_=rid))
    delete = MagicMock()
    monkeypatch.setattr(svc.db, "delete_recovery", delete)

    with pytest.raises(svc.RecoveryAccessDenied):
        svc.delete_user_recovery("u1", "u2s-doc")
    delete.assert_not_called()


# ------------- list_user_recovery -------------

def test_list_user_recovery_delegates_to_db(monkeypatch):
    fake_list = MagicMock(return_value=[_recovery(user_id="u1", id_="x")])
    monkeypatch.setattr(svc.db, "list_recovery", fake_list)

    start = datetime(2026, 6, 1)
    end = datetime(2026, 6, 30)
    result = svc.list_user_recovery("u1", start, end)

    assert len(result) == 1
    fake_list.assert_called_once_with("u1", start, end)
