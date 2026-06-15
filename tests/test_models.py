"""Tests for Pydantic models — discriminated union + per-discipline shapes."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from models import (
    CardioData,
    Exercise,
    GrapplingData,
    LogEntry,
    MmaData,
    RecoveryActivity,
    RecoveryLog,
    Session,
    StrikingData,
    Tag,
    WeightsData,
)


# ---------------- Tag ----------------

class TestTag:
    def test_valid(self):
        t = Tag(technique="Rubber Guard", position="Bottom Guard")
        assert t.technique == "Rubber Guard"

    def test_missing_field(self):
        with pytest.raises(ValidationError):
            Tag(technique="Rubber Guard")


# ---------------- LogEntry ----------------

class TestLogEntry:
    def test_valid(self):
        e = LogEntry(notes_raw="x", category="drill")
        assert e.tags == []

    def test_invalid_category(self):
        with pytest.raises(ValidationError):
            LogEntry(notes_raw="x", category="meditation")


# ---------------- Exercise ----------------

class TestExercise:
    def test_minimal(self):
        e = Exercise(name="Pull-up", sets=3, reps=10)
        assert e.weight_kg is None

    def test_with_weight(self):
        e = Exercise(name="Bench", sets=3, reps=8, weight_kg=80)
        assert e.weight_kg == 80


# ---------------- Per-discipline data classes ----------------

class TestGrapplingData:
    def test_bjj(self):
        d = GrapplingData(discipline="bjj", drilling_minutes=30, sparring_rounds=4)
        assert d.discipline == "bjj"
        assert d.round_length_minutes == 6  # default

    def test_wrestling_also_valid(self):
        d = GrapplingData(discipline="wrestling", sparring_rounds=10)
        assert d.discipline == "wrestling"

    def test_rejects_non_grappling_discipline(self):
        with pytest.raises(ValidationError):
            GrapplingData(discipline="boxing")


class TestStrikingData:
    def test_boxing(self):
        d = StrikingData(discipline="boxing", bag_minutes=15, pad_minutes=10)
        assert d.round_length_minutes == 3  # boxing default

    def test_kickboxing(self):
        d = StrikingData(discipline="kickboxing")
        assert d.discipline == "kickboxing"


class TestCardioData:
    def test_valid(self):
        d = CardioData(discipline="cardio", activity_type="run", duration_minutes=45)
        assert d.intensity == "moderate"  # default
        assert d.distance_km is None  # default

    def test_with_optionals(self):
        d = CardioData(
            discipline="cardio",
            activity_type="bike",
            duration_minutes=60,
            distance_km=25.0,
            intensity="high",
            heart_rate_avg=155,
        )
        assert d.distance_km == 25.0

    def test_invalid_intensity(self):
        with pytest.raises(ValidationError):
            CardioData(discipline="cardio", activity_type="run", duration_minutes=30, intensity="extreme")


class TestMmaData:
    def test_minimal(self):
        d = MmaData(discipline="mma")
        assert d.round_length_minutes == 5  # MMA default
        assert d.wall_wrestling_minutes == 0
        assert d.strikes_to_takedown_minutes == 0

    def test_full(self):
        d = MmaData(
            discipline="mma",
            drilling_minutes=20,
            sparring_rounds=3,
            wall_wrestling_minutes=10,
            strikes_to_takedown_minutes=15,
            log_entries=[LogEntry(notes_raw="cage pummeling", category="drill")],
        )
        assert d.wall_wrestling_minutes == 10
        assert d.strikes_to_takedown_minutes == 15

    def test_rejects_non_mma_discipline(self):
        with pytest.raises(ValidationError):
            MmaData(discipline="bjj")


class TestWeightsData:
    def test_empty(self):
        d = WeightsData(discipline="weights")
        assert d.exercises == []

    def test_with_exercises(self):
        d = WeightsData(
            discipline="weights",
            duration_minutes=60,
            exercises=[
                Exercise(name="Squat", sets=5, reps=5, weight_kg=100),
                Exercise(name="Pull-up", sets=3, reps=10),
            ],
        )
        assert len(d.exercises) == 2


# ---------------- Session (discriminated union) ----------------

class TestSessionDiscriminator:
    def _kwargs(self):
        return dict(user_id="u1", started_at=datetime(2026, 5, 22, 9, 0))

    def test_bjj_session(self):
        s = Session(**self._kwargs(), data=GrapplingData(discipline="bjj", drilling_minutes=30))
        assert isinstance(s.data, GrapplingData)
        assert s.data.drilling_minutes == 30
        assert s.id is None  # not yet saved

    def test_cardio_session(self):
        s = Session(
            **self._kwargs(),
            data=CardioData(discipline="cardio", activity_type="run", duration_minutes=45),
        )
        assert isinstance(s.data, CardioData)
        assert s.data.activity_type == "run"

    def test_mma_session(self):
        s = Session(
            **self._kwargs(),
            data=MmaData(discipline="mma", drilling_minutes=20, wall_wrestling_minutes=10),
        )
        assert isinstance(s.data, MmaData)
        assert s.data.wall_wrestling_minutes == 10

    def test_weights_session(self):
        s = Session(
            **self._kwargs(),
            data=WeightsData(discipline="weights", exercises=[Exercise(name="Bench", sets=3, reps=8)]),
        )
        assert isinstance(s.data, WeightsData)

    def test_discriminator_from_dict(self):
        """Pydantic picks the right class from the discipline field."""
        s = Session(
            **self._kwargs(),
            data={"discipline": "cardio", "activity_type": "swim", "duration_minutes": 30},
        )
        assert isinstance(s.data, CardioData)
        assert s.data.activity_type == "swim"

    def test_invalid_discipline_rejected(self):
        with pytest.raises(ValidationError):
            Session(**self._kwargs(), data={"discipline": "yoga", "duration_minutes": 60})

    def test_mismatched_fields_rejected(self):
        """cardio data shape with drilling_minutes makes no sense."""
        with pytest.raises(ValidationError):
            Session(
                **self._kwargs(),
                data={"discipline": "cardio", "drilling_minutes": 30},  # missing duration_minutes
            )

    def test_id_optional(self):
        s = Session(**self._kwargs(), data=GrapplingData(discipline="bjj"))
        assert s.id is None

    def test_id_can_be_set(self):
        s = Session(id="abc123", **self._kwargs(), data=GrapplingData(discipline="bjj"))
        assert s.id == "abc123"

    def test_user_id_required(self):
        with pytest.raises(ValidationError):
            Session(started_at=datetime.now(), data=GrapplingData(discipline="bjj"))

    def test_json_round_trip(self):
        s = Session(
            id="x",
            **self._kwargs(),
            data=GrapplingData(
                discipline="bjj",
                drilling_minutes=30,
                log_entries=[LogEntry(notes_raw="x", category="drill", tags=[Tag(technique="T", position="P")])],
            ),
        )
        as_json = s.model_dump_json()
        revived = Session.model_validate_json(as_json)
        assert revived == s


# ---------------- Recovery ----------------

class TestRecoveryActivity:
    def test_valid(self):
        a = RecoveryActivity(activity_type="sauna", minutes=20)
        assert a.activity_type == "sauna"
        assert a.minutes == 20

    def test_minutes_default_zero(self):
        a = RecoveryActivity(activity_type="massage")
        assert a.minutes == 0

    def test_invalid_activity_type(self):
        with pytest.raises(ValidationError):
            RecoveryActivity(activity_type="yoga")


class TestRecoveryLog:
    def test_minimal_sleep_only(self):
        r = RecoveryLog(user_id="u1", logged_at=datetime(2026, 6, 15, 12), sleep_hours=8)
        assert r.sleep_hours == 8
        assert r.activities == []
        assert r.id is None  # not yet saved

    def test_activity_only_no_sleep(self):
        r = RecoveryLog(
            user_id="u1",
            logged_at=datetime(2026, 6, 15, 12),
            activities=[RecoveryActivity(activity_type="ice_bath", minutes=10)],
        )
        assert r.sleep_hours is None
        assert r.activities[0].activity_type == "ice_bath"

    def test_user_id_required(self):
        with pytest.raises(ValidationError):
            RecoveryLog(logged_at=datetime(2026, 6, 15, 12), sleep_hours=8)

    def test_json_round_trip(self):
        r = RecoveryLog(
            id="r1",
            user_id="u1",
            logged_at=datetime(2026, 6, 15, 12),
            sleep_hours=7.5,
            activities=[
                RecoveryActivity(activity_type="sauna", minutes=15),
                RecoveryActivity(activity_type="stretching", minutes=10),
            ],
            notes="felt good",
        )
        revived = RecoveryLog.model_validate_json(r.model_dump_json())
        assert revived == r
