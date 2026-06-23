"""Tests for gcal.py — Calendar response parsing + the HTTP wrappers.

The Pydantic parsing is pure and is where most real-world bugs would hide
(camelCase aliases, datetime coercion, missing fields), so it gets the most
attention. The HTTP calls are tested with a fake httpx response so no network
traffic happens.
"""

from datetime import datetime

import pytest

import gcal
from gcal import CalendarEvent, create_event, list_events


# A realistic (trimmed) event payload, exactly as Google returns it — note the
# camelCase keys and the many fields we DON'T model.
_GOOGLE_EVENT = {
    "kind": "calendar#event",
    "etag": "\"abc\"",
    "id": "evt123",
    "status": "confirmed",
    "htmlLink": "https://www.google.com/calendar/event?eid=xyz",
    "created": "2026-06-20T10:00:00.000Z",
    "summary": "bjj training",
    "description": "rolling night",
    "start": {"dateTime": "2026-06-24T18:00:00+02:00", "timeZone": "Europe/Brussels"},
    "end": {"dateTime": "2026-06-24T19:00:00+02:00", "timeZone": "Europe/Brussels"},
}


class FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


# ---------------- CalendarEvent parsing (the Pydantic lesson) ----------------

class TestCalendarEventParsing:
    def test_alias_maps_camelcase(self):
        ev = CalendarEvent.model_validate(_GOOGLE_EVENT)
        # Google's "htmlLink" lands on our pythonic html_link.
        assert ev.html_link == "https://www.google.com/calendar/event?eid=xyz"

    def test_datetime_is_coerced(self):
        ev = CalendarEvent.model_validate(_GOOGLE_EVENT)
        # The ISO string became a real datetime we can do math on.
        assert isinstance(ev.start.date_time, datetime)
        assert ev.start.date_time.hour == 18

    def test_extra_fields_ignored(self):
        # kind/etag/created aren't on our model — parsing must not choke.
        ev = CalendarEvent.model_validate(_GOOGLE_EVENT)
        assert ev.summary == "bjj training"

    def test_optional_description_may_be_missing(self):
        payload = {k: v for k, v in _GOOGLE_EVENT.items() if k != "description"}
        ev = CalendarEvent.model_validate(payload)
        assert ev.description is None

    def test_missing_required_id_raises(self):
        from pydantic import ValidationError

        payload = {k: v for k, v in _GOOGLE_EVENT.items() if k != "id"}
        with pytest.raises(ValidationError):
            CalendarEvent.model_validate(payload)


# ---------------- create_event ----------------

class TestCreateEvent:
    def test_builds_request_and_parses_reply(self, monkeypatch):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse(_GOOGLE_EVENT)

        monkeypatch.setattr(gcal.httpx, "post", fake_post)

        ev = create_event(
            "tok123",
            summary="bjj training",
            start=datetime(2026, 6, 24, 18, 0),
            end=datetime(2026, 6, 24, 19, 0),
            time_zone="Europe/Brussels",
            description="rolling night",
        )

        # Bearer header carries the token.
        assert captured["headers"]["Authorization"] == "Bearer tok123"
        # Body has the nested start/end in Google's shape.
        assert captured["json"]["start"]["timeZone"] == "Europe/Brussels"
        assert captured["json"]["summary"] == "bjj training"
        # Reply parsed into our model.
        assert isinstance(ev, CalendarEvent)
        assert ev.id == "evt123"


# ---------------- list_events ----------------

class TestListEvents:
    def test_parses_items(self, monkeypatch):
        def fake_get(url, headers=None, params=None, timeout=None):
            return FakeResponse({"items": [_GOOGLE_EVENT, _GOOGLE_EVENT]})

        monkeypatch.setattr(gcal.httpx, "get", fake_get)

        events = list_events(
            "tok123",
            time_min=datetime(2026, 6, 24, 0, 0),
            time_max=datetime(2026, 7, 1, 0, 0),
        )
        assert len(events) == 2
        assert all(isinstance(e, CalendarEvent) for e in events)

    def test_empty_calendar_returns_empty_list(self, monkeypatch):
        def fake_get(url, headers=None, params=None, timeout=None):
            return FakeResponse({})  # no "items" key at all

        monkeypatch.setattr(gcal.httpx, "get", fake_get)

        events = list_events(
            "tok123",
            time_min=datetime(2026, 6, 24, 0, 0),
            time_max=datetime(2026, 7, 1, 0, 0),
        )
        assert events == []
