"""Tests for Pydantic models — validation rules and JSON round-trip."""

from datetime import date

import pytest
from pydantic import ValidationError

from models import LogEntry, Session, Tag


class TestTag:
    def test_valid(self):
        t = Tag(technique="Rubber Guard", position="Bottom Guard")
        assert t.technique == "Rubber Guard"
        assert t.position == "Bottom Guard"

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError) as exc:
            Tag(technique="Rubber Guard")
        assert "position" in str(exc.value)

    def test_wrong_type_raises(self):
        with pytest.raises(ValidationError):
            Tag(technique=123, position=["not", "a", "string"])


class TestLogEntry:
    def test_valid_drill(self):
        e = LogEntry(notes_raw="rubber guard from bottom", category="drill")
        assert e.category == "drill"
        assert e.tags == []  # default

    def test_valid_spar(self):
        e = LogEntry(notes_raw="rolled with John", category="spar")
        assert e.category == "spar"

    def test_invalid_category_rejected(self):
        with pytest.raises(ValidationError) as exc:
            LogEntry(notes_raw="x", category="meditation")
        assert "category" in str(exc.value).lower()

    def test_nested_tags_validated(self):
        e = LogEntry(
            notes_raw="x",
            category="drill",
            tags=[Tag(technique="Triangle", position="Bottom Guard")],
        )
        assert len(e.tags) == 1
        assert e.tags[0].technique == "Triangle"

    def test_nested_tag_validation_fails(self):
        with pytest.raises(ValidationError):
            LogEntry(notes_raw="x", category="drill", tags=[{"technique": "Triangle"}])  # missing position


class TestSession:
    def _valid_kwargs(self):
        return dict(
            id="2026-05-20_AM",
            date=date(2026, 5, 20),
            slot="AM",
        )

    def test_minimal_with_defaults(self):
        s = Session(**self._valid_kwargs())
        assert s.drilling_minutes == 0
        assert s.sparring_rounds == 0
        assert s.round_length_minutes == 6
        assert s.log_entries == []

    def test_full(self):
        s = Session(
            **self._valid_kwargs(),
            drilling_minutes=30,
            sparring_rounds=4,
            round_length_minutes=6,
            log_entries=[
                LogEntry(notes_raw="x", category="drill", tags=[Tag(technique="X-Guard", position="Bottom Guard")]),
            ],
        )
        assert s.log_entries[0].tags[0].technique == "X-Guard"

    def test_invalid_slot_rejected(self):
        with pytest.raises(ValidationError):
            Session(**{**self._valid_kwargs(), "slot": "midnight"})

    def test_date_string_coerced(self):
        s = Session(id="x", date="2026-05-20", slot="AM")
        assert s.date == date(2026, 5, 20)

    def test_int_string_coerced(self):
        s = Session(**self._valid_kwargs(), drilling_minutes="45")
        assert s.drilling_minutes == 45

    def test_json_round_trip(self):
        s = Session(
            **self._valid_kwargs(),
            log_entries=[LogEntry(notes_raw="x", category="drill", tags=[Tag(technique="T", position="P")])],
        )
        as_json = s.model_dump_json()
        revived = Session.model_validate_json(as_json)
        assert revived == s
