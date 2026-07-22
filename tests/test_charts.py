"""Tests for charts.py — chart data aggregation."""

from datetime import date, datetime, timedelta

from charts import (
    current_streak,
    daily_recovery_score,
    discipline_totals,
    recovery_score_on,
    streak_milestones,
    total_minutes,
    weekly_discipline_minutes,
    weekly_recovery_score,
)
from models import (
    CardioData,
    Exercise,
    GrapplingData,
    MmaData,
    RecoveryLog,
    Session,
    StrikingData,
    WeightsData,
)


# Helper: build a Session quickly
def _session(when: datetime, data) -> Session:
    return Session(id=None, user_id="u", started_at=when, data=data)


def _recovery(when: datetime, sleep_hours=None) -> RecoveryLog:
    return RecoveryLog(id=None, user_id="u", logged_at=when, sleep_hours=sleep_hours)


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


# ---------------- streak_milestones ----------------

class TestStreakMilestones:
    def test_zero_streak_none_earned(self):
        ms = streak_milestones(0)
        assert ms["streak"] == 0
        assert ms["earned"] == []
        assert ms["next"] == 3
        assert ms["days_to_next"] == 3

    def test_partway_earned_and_next(self):
        ms = streak_milestones(9)
        assert ms["earned"] == [3, 7]
        assert ms["next"] == 14
        assert ms["days_to_next"] == 5

    def test_exactly_on_a_milestone(self):
        ms = streak_milestones(7)
        assert 7 in ms["earned"]
        assert ms["next"] == 14

    def test_all_milestones_earned(self):
        ms = streak_milestones(400)
        assert ms["next"] is None
        assert ms["days_to_next"] is None
        assert 365 in ms["earned"]


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


# ---------------- daily_recovery_score ----------------

class TestDailyRecoveryScore:
    def test_spec_anchor_high(self):
        # 1h training + 8h sleep -> 100 - 15 = 85 (high)
        assert daily_recovery_score(sleep_hours=8, training_minutes=60) == 85

    def test_spec_anchor_low(self):
        # 3h training + 5h sleep -> 62.5 - 45 = 17.5 -> 18 (low)
        assert daily_recovery_score(sleep_hours=5, training_minutes=180) == 18

    def test_rest_day_full_sleep_maxes_out(self):
        assert daily_recovery_score(sleep_hours=8, training_minutes=0) == 100

    def test_clamped_at_zero(self):
        # huge training, little sleep can't go negative
        assert daily_recovery_score(sleep_hours=2, training_minutes=600) == 0

    def test_clamped_at_hundred(self):
        # oversleeping can't exceed 100
        assert daily_recovery_score(sleep_hours=12, training_minutes=0) == 100


# ---------------- recovery_score_on ----------------

class TestRecoveryScoreOn:
    def test_none_without_sleep_log(self):
        day = date(2026, 6, 15)
        # a recovery log exists but with no sleep -> can't score
        logs = [_recovery(datetime(2026, 6, 15, 12), sleep_hours=None)]
        assert recovery_score_on(day, logs, []) is None

    def test_uses_that_days_training(self):
        day = date(2026, 6, 15)
        logs = [_recovery(datetime(2026, 6, 15, 12), sleep_hours=8)]
        sessions = [
            _session(datetime(2026, 6, 15, 9), GrapplingData(discipline="bjj", drilling_minutes=60)),
            _session(datetime(2026, 6, 14, 9), GrapplingData(discipline="bjj", drilling_minutes=120)),  # other day
        ]
        # only the 60-min same-day session counts: 100 - 15 = 85
        assert recovery_score_on(day, logs, sessions) == 85

    def test_sums_sleep_across_logs_same_day(self):
        day = date(2026, 6, 15)
        logs = [
            _recovery(datetime(2026, 6, 15, 6), sleep_hours=5),
            _recovery(datetime(2026, 6, 15, 14), sleep_hours=3),  # nap
        ]
        # 8h total, no training -> 100
        assert recovery_score_on(day, logs, []) == 100


# ---------------- weekly_recovery_score ----------------

class TestWeeklyRecoveryScore:
    def test_none_when_no_sleep_logged(self):
        assert weekly_recovery_score([], [], today=date(2026, 6, 15)) is None

    def test_averages_daily_scores(self):
        today = date(2026, 6, 15)
        logs = [
            _recovery(datetime(2026, 6, 15, 12), sleep_hours=8),  # -> 100 (no training)
            _recovery(datetime(2026, 6, 14, 12), sleep_hours=4),  # -> 50 (no training)
        ]
        # avg(100, 50) = 75
        assert weekly_recovery_score(logs, [], today=today) == 75

    def test_ignores_days_outside_window(self):
        today = date(2026, 6, 15)
        logs = [
            _recovery(datetime(2026, 6, 15, 12), sleep_hours=8),   # in window -> 100
            _recovery(datetime(2026, 6, 1, 12), sleep_hours=4),    # >7 days ago, ignored
        ]
        assert weekly_recovery_score(logs, [], today=today) == 100
