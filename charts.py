"""Aggregation helpers for the dashboard charts.

Pure functions over a list of Sessions — no UI, no Firestore. Tested
in isolation so chart rendering doesn't need a running browser.

Two aggregations:
- weekly_discipline_minutes: stacked-bar data over the last N weeks
- discipline_totals: pie-chart data over the last 30 days

Plus the underlying total_minutes(data) and current_streak(sessions)
helpers, hoisted out of main.py.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from models import (
    CardioData,
    GrapplingData,
    MmaData,
    RecoveryLog,
    Session,
    StrikingData,
    WeightsData,
)


def total_minutes(data) -> int:
    """Total training minutes for a session's discipline-specific data."""
    if isinstance(data, GrapplingData):
        return data.drilling_minutes + data.sparring_rounds * data.round_length_minutes
    if isinstance(data, StrikingData):
        return data.bag_minutes + data.pad_minutes + data.sparring_rounds * data.round_length_minutes
    if isinstance(data, MmaData):
        return (
            data.drilling_minutes
            + data.wall_wrestling_minutes
            + data.strikes_to_takedown_minutes
            + data.sparring_rounds * data.round_length_minutes
        )
    if isinstance(data, (CardioData, WeightsData)):
        return data.duration_minutes
    return 0


def current_streak(sessions: list[Session]) -> int:
    """Consecutive days (back from today) with at least one session."""
    if not sessions:
        return 0
    by_day = {s.started_at.date() for s in sessions}
    streak = 0
    day = date.today()
    while day in by_day:
        streak += 1
        day = day - timedelta(days=1)
    return streak


# Streak milestones (consecutive-day badges). Kept small + memorable.
STREAK_MILESTONES = [3, 7, 14, 30, 60, 100, 365]


def streak_milestones(streak: int) -> dict:
    """Milestone breakdown for a current streak.

    Returns:
        {
            "streak": 9,
            "earned": [3, 7],          # milestones already reached
            "next": 14,                # next milestone, or None if all earned
            "days_to_next": 5,         # days until `next`, or None
        }
    """
    earned = [m for m in STREAK_MILESTONES if streak >= m]
    upcoming = [m for m in STREAK_MILESTONES if streak < m]
    nxt = upcoming[0] if upcoming else None
    return {
        "streak": streak,
        "earned": earned,
        "next": nxt,
        "days_to_next": (nxt - streak) if nxt is not None else None,
    }


def weekly_discipline_minutes(
    sessions: list[Session], n_weeks: int = 8, *, today: date | None = None,
) -> dict:
    """Bucket sessions into the last `n_weeks` weeks by discipline.

    Returns:
        {
            "weeks": ["May 5", "May 12", ...]   # n_weeks labels, oldest first
            "series": {
                "bjj":    [60, 90, 120, ...],   # minutes per week
                "cardio": [30,  0,  45, ...],
                ...
            }
        }

    Only disciplines with at least one non-zero week appear in `series`.
    """
    if today is None:
        today = date.today()
    # week_start = the most-recent Monday on/before today
    week_start = today - timedelta(days=today.weekday())
    # Generate n_weeks of Monday dates, oldest first
    week_starts = [week_start - timedelta(weeks=n_weeks - 1 - i) for i in range(n_weeks)]

    series: dict[str, list[int]] = {}
    for i, ws in enumerate(week_starts):
        we = ws + timedelta(days=7)  # exclusive end
        for s in sessions:
            sd = s.started_at.date()
            if ws <= sd < we:
                d = s.data.discipline
                if d not in series:
                    series[d] = [0] * n_weeks
                series[d][i] += total_minutes(s.data)

    labels = [ws.strftime("%b %d") for ws in week_starts]
    return {"weeks": labels, "series": series}


def discipline_totals(sessions: list[Session]) -> dict[str, int]:
    """Sum of training minutes per discipline across all given sessions.

    Returns only disciplines with non-zero totals.
    """
    totals: dict[str, int] = {}
    for s in sessions:
        m = total_minutes(s.data)
        if m == 0:
            continue
        totals[s.data.discipline] = totals.get(s.data.discipline, 0) + m
    return totals


# ---------------------------------------------------------------------
# Recovery score
#
# A 0–100 score balancing recovery input (sleep) against training stress
# (minutes trained that day). More sleep relative to training = higher
# recovery. The two tuning constants below define the curve:
#
#   score = clamp( (sleep / TARGET) * 100  -  (training_min / 60) * PENALTY , 0, 100)
#
# Worked examples (the spec's anchors):
#   1h training + 8h sleep -> (8/8)*100 - (60/60)*15  = 100 - 15 =  85  (high)
#   3h training + 5h sleep -> (5/8)*100 - (180/60)*15 = 62.5 - 45 = 18  (low)
#
# Active-recovery activities (sauna/massage/etc.) are intentionally NOT
# part of the score — they're logged for the record only.
# ---------------------------------------------------------------------

SLEEP_TARGET_HOURS = 8        # hours of sleep that maxes out the sleep term
STRAIN_PENALTY_PER_HOUR = 15  # score points subtracted per hour trained


def daily_recovery_score(sleep_hours: float, training_minutes: int) -> int:
    """The core formula. Returns an int in [0, 100]."""
    sleep_factor = (sleep_hours / SLEEP_TARGET_HOURS) * 100
    strain_factor = (training_minutes / 60) * STRAIN_PENALTY_PER_HOUR
    return round(max(0.0, min(100.0, sleep_factor - strain_factor)))


def recovery_score_on(
    day: date, recovery_logs: list[RecoveryLog], sessions: list[Session]
) -> int | None:
    """Recovery score for one calendar day, or None if no sleep was logged
    that day (we can't score recovery without a sleep input).

    Sleep is summed across that day's logs (normally one); training minutes
    are summed across that day's sessions.
    """
    day_logs = [r for r in recovery_logs if r.logged_at.date() == day and r.sleep_hours]
    if not day_logs:
        return None
    sleep = sum(r.sleep_hours or 0 for r in day_logs)
    training = sum(total_minutes(s.data) for s in sessions if s.started_at.date() == day)
    return daily_recovery_score(sleep, training)


def weekly_recovery_score(
    recovery_logs: list[RecoveryLog],
    sessions: list[Session],
    *,
    today: date | None = None,
) -> int | None:
    """Average daily recovery score over the last 7 days (today inclusive),
    counting only days that have a sleep log. None if no day qualifies.
    """
    if today is None:
        today = date.today()
    scores = []
    for i in range(7):
        sc = recovery_score_on(today - timedelta(days=i), recovery_logs, sessions)
        if sc is not None:
            scores.append(sc)
    if not scores:
        return None
    return round(sum(scores) / len(scores))
