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
