"""Tests for the coach's calendar tools (schedule_training / list_planned).

The tools only touch `ctx.deps`, so a SimpleNamespace stands in for the real
pydantic_ai RunContext. gcal.create_event / list_events are monkeypatched so no
network traffic happens — we're testing the tool logic, not Google.
"""

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

import coach
from coach import CoachDeps, _list_planned_tool, _schedule_training_tool
from gcal import CalendarEvent

_NOW = datetime(2026, 6, 23, 12, 0)


def _ctx(token="tok123"):
    return SimpleNamespace(deps=CoachDeps(user_id="u1", now=_NOW, access_token=token))


def _http_error(status: int):
    req = httpx.Request("GET", "https://example.com")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


_EVENT = CalendarEvent.model_validate({
    "id": "e1",
    "summary": "bjj training",
    "start": {"dateTime": "2026-06-24T18:00:00+02:00"},
    "end": {"dateTime": "2026-06-24T19:00:00+02:00"},
})


# ---------------- schedule_training ----------------

class TestScheduleTraining:
    def test_success(self, monkeypatch):
        captured = {}

        def fake_create(token, **kwargs):
            captured.update(kwargs)
            captured["token"] = token
            return _EVENT

        monkeypatch.setattr(coach, "create_event", fake_create)
        out = _schedule_training_tool(_ctx(), "bjj", "2026-06-24T18:00", 60, "rolling")
        assert "Added bjj" in out
        assert captured["token"] == "tok123"
        assert captured["time_zone"] == coach.COACH_TZ

    def test_no_token_degrades_gracefully(self):
        out = _schedule_training_tool(_ctx(token=None), "bjj", "2026-06-24T18:00")
        assert "sign" in out.lower()

    def test_bad_datetime(self):
        out = _schedule_training_tool(_ctx(), "bjj", "not-a-date")
        assert "valid date" in out.lower()

    def test_expired_token(self, monkeypatch):
        def fake_create(token, **kwargs):
            raise _http_error(401)

        monkeypatch.setattr(coach, "create_event", fake_create)
        out = _schedule_training_tool(_ctx(), "bjj", "2026-06-24T18:00")
        assert "expired" in out.lower()

    def test_other_http_error(self, monkeypatch):
        def fake_create(token, **kwargs):
            raise _http_error(500)

        monkeypatch.setattr(coach, "create_event", fake_create)
        out = _schedule_training_tool(_ctx(), "bjj", "2026-06-24T18:00")
        assert "500" in out


# ---------------- list_planned ----------------

class TestListPlanned:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(coach, "list_events", lambda *a, **k: [_EVENT])
        out = _list_planned_tool(_ctx())
        assert "bjj training" in out
        assert "Planned sessions" in out

    def test_empty(self, monkeypatch):
        monkeypatch.setattr(coach, "list_events", lambda *a, **k: [])
        out = _list_planned_tool(_ctx())
        assert "no planned" in out.lower()

    def test_no_token(self):
        out = _list_planned_tool(_ctx(token=None))
        assert "sign" in out.lower()

    def test_bad_dates(self):
        out = _list_planned_tool(_ctx(), start_iso="nope")
        assert "valid dates" in out.lower()

    def test_expired_token(self, monkeypatch):
        def fake_list(*a, **k):
            raise _http_error(401)

        monkeypatch.setattr(coach, "list_events", fake_list)
        out = _list_planned_tool(_ctx())
        assert "expired" in out.lower()
