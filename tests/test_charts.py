"""Tests for charts.py — chart data aggregation."""

from datetime import date, datetime, timedelta

from charts import (
    current_streak,
    discipline_totals,
    total_minutes,
    weekly_discipline_minutes,
)
from models import (
    CardioData,
    Exercise,
    GrapplingData,
    MmaData,
    Session,
    StrikingData,
    WeightsData,
)


# Helper: build a Session quickly
def _session(when: datetime, data) -> Session:
    return Session(id=None, user_id="u", started_at=when, data=data)


# ---------------- total_minutes ----------------

class TestTotalMinutes:
    def test_grappling(self):
        d = GrapplingData(discipline="bjj", drilling_minutes=30, sparring_rounds=4, round_length_minutes=6)
        assert total_minutes(d) == 30 + 4 * 6  # 54

    def test_striking(self):
        d = StrikingData(discipline="boxing", bag_minutes=10, pad_minutes=15, sparring_rounds=3, round_length_minutes=3)
        assert total_minutes(d) == 10 + 15 + 3 * 3  # 34

    def test_mma(self):
        d = MmaData(discipline="mma", drilling_minutes=20, sparring_rounds=2,
                    round_length_minutes=5, wall_wrestling_minutes=10, strikes_to_takedown_minutes=10)
        assert total_minutes(d) == 20 + 10 + 10 + 2 * 5  # 50

    def test_cardio(self):
        d = CardioData(discipline="cardio", activity_type="run", duration_minutes=45)
        assert total_minutes(d) == 45

    def test_weights(self):
        d = WeightsData(discipline="weights", duration_minutes=60, exercises=[Exercise(name="x", sets=3, reps=8)])
        assert total_minutes(d) == 60


# ---------------- current_streak ----------------

class TestCurrentStreak:
    def test_empty(self):
        assert current_streak([]) == 0

    def test_today_only(self):
        s = _session(datetime.now(), GrapplingData(discipline="bjj"))
        assert current_streak([s]) == 1

    def test_three_days_in_a_row(self):
        today = datetime.now()
        sessions = [
            _session(today, GrapplingData(discipline="bjj")),
            _session(today - timedelta(days=1), CardioData(discipline="cardio", activity_type="r", duration_minutes=30)),
            _session(today - timedelta(days=2), WeightsData(discipline="weights", duration_minutes=30)),
        ]
        assert current_streak(sessions) == 3

    def test_gap_breaks_streak(self):
        today = datetime.now()
        sessions = [
            _session(today, GrapplingData(discipline="bjj")),
            # skip yesterday
            _session(today - timedelta(days=2), GrapplingData(discipline="bjj")),
        ]
        assert current_streak(sessions) == 1


# ---------------- weekly_discipline_minutes ----------------

class TestWeeklyDisciplineMinutes:
    def test_empty(self):
        result = weekly_discipline_minutes([], n_weeks=4, today=date(2026, 5, 26))
        assert len(result["weeks"]) == 4
        assert result["series"] == {}

    def test_buckets_by_week(self):
        today = date(2026, 5, 27)  # Wednesday
        # this Monday = May 25
        # Sessions in current week
        s1 = _session(datetime(2026, 5, 25, 10), GrapplingData(discipline="bjj", drilling_minutes=30))
        # one week ago — week of May 18
        s2 = _session(datetime(2026, 5, 19, 9), GrapplingData(discipline="bjj", drilling_minutes=45))
        # current week again — cardio
        s3 = _session(datetime(2026, 5, 26, 18), CardioData(discipline="cardio", activity_type="r", duration_minutes=60))

        result = weekly_discipline_minutes([s1, s2, s3], n_weeks=3, today=today)
        # Weeks oldest-first: May 11 (empty), May 18 (s2), May 25 (s1+s3)
        assert len(result["weeks"]) == 3
        assert result["series"]["bjj"] == [0, 45, 30]
        assert result["series"]["cardio"] == [0, 0, 60]

    def test_only_nonzero_disciplines_appear(self):
        s = _session(datetime(2026, 5, 26, 10), CardioData(discipline="cardio", activity_type="r", duration_minutes=30))
        result = weekly_discipline_minutes([s], n_weeks=2, today=date(2026, 5, 26))
        assert "cardio" in result["series"]
        assert "bjj" not in result["series"]


# ---------------- discipline_totals ----------------

class TestDisciplineTotals:
    def test_empty(self):
        assert discipline_totals([]) == {}

    def test_sums_across_disciplines(self):
        sessions = [
            _session(datetime.now(), GrapplingData(discipline="bjj", drilling_minutes=30)),
            _session(datetime.now(), GrapplingData(discipline="bjj", drilling_minutes=20, sparring_rounds=4)),
            _session(datetime.now(), CardioData(discipline="cardio", activity_type="r", duration_minutes=45)),
        ]
        result = discipline_totals(sessions)
        # bjj: (30 + 0*6) + (20 + 4*6) = 30 + 44 = 74
        # cardio: 45
        assert result == {"bjj": 74, "cardio": 45}

    def test_skips_zero_total_sessions(self):
        sessions = [
            _session(datetime.now(), GrapplingData(discipline="bjj")),  # all zero
            _session(datetime.now(), CardioData(discipline="cardio", activity_type="r", duration_minutes=30)),
        ]
        result = discipline_totals(sessions)
        assert result == {"cardio": 30}
