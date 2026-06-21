"""Tests for coach.py — the Agent is mocked, no LLM calls happen.

build_coach_context is a pure function so it's tested directly; coach_reply
is tested with a mocked agent (same pattern as test_ai.py).
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import coach
from models import (
    CardioData,
    GrapplingData,
    RecoveryActivity,
    RecoveryLog,
    Session,
    StrikingData,
    WeightsData,
)

NOW = datetime(2026, 6, 16, 19, 0)


def _session(when: datetime, data) -> Session:
    return Session(id=None, user_id="u", started_at=when, data=data)


# ---------------- build_coach_context ----------------

class TestBuildCoachContext:
    def test_empty_both(self):
        assert coach.build_coach_context([], []) == "No training data logged yet."

    def test_summarises_sessions(self):
        sessions = [
            _session(datetime(2026, 6, 12, 9), GrapplingData(discipline="bjj", drilling_minutes=60)),
            _session(datetime(2026, 6, 13, 9), CardioData(discipline="cardio", activity_type="run", duration_minutes=30)),
        ]
        ctx = coach.build_coach_context(sessions, [])
        assert "2 sessions" in ctx
        assert "90 total minutes" in ctx  # 60 + 30
        assert "bjj" in ctx and "cardio" in ctx

    def test_recovery_score_none_when_no_sleep(self):
        sessions = [_session(datetime(2026, 6, 12, 9), GrapplingData(discipline="bjj", drilling_minutes=30))]
        ctx = coach.build_coach_context(sessions, [])
        assert "no sleep logged yet" in ctx

    def test_recovery_score_present(self):
        # full sleep + no training on its day -> 100
        today = datetime.now()
        sessions = []
        logs = [RecoveryLog(user_id="u", logged_at=today, sleep_hours=8)]
        ctx = coach.build_coach_context(sessions, logs)
        assert "100/100" in ctx

    def test_caps_recent_sessions_at_five(self):
        sessions = [
            _session(datetime(2026, 6, d, 9), GrapplingData(discipline="bjj", drilling_minutes=10))
            for d in range(1, 9)  # 8 sessions
        ]
        ctx = coach.build_coach_context(sessions, [])
        # "Recent sessions:" header + at most 5 bullet lines under it
        recent_block = ctx.split("Recent sessions:")[1]
        assert recent_block.count("· bjj ·") == 5


# ---------------- coach_reply ----------------

def test_coach_reply_empty_returns_empty():
    assert coach.coach_reply("", "u", NOW) == ("", [], [], [])
    assert coach.coach_reply("   \n ", "u", NOW) == ("", [], [], [])


def test_coach_reply_prepends_context_on_first_turn(monkeypatch):
    fake_result = MagicMock()
    fake_result.output = "Train hard, sleep more."
    fake_result.all_messages.return_value = ["m1", "m2"]
    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_result
    monkeypatch.setattr(coach, "_agent", fake_agent)

    out, messages, logged, logged_recovery = coach.coach_reply(
        "How am I doing?", "u", NOW, history=None, context="6 sessions"
    )
    assert out == "Train hard, sleep more."
    assert messages == ["m1", "m2"]            # returned for the next turn
    assert logged == []                         # nothing logged this turn
    assert logged_recovery == []                # no recovery logged this turn
    sent_prompt = fake_agent.run_sync.call_args[0][0]
    assert "6 sessions" in sent_prompt          # context was prepended
    assert "How am I doing?" in sent_prompt


def test_coach_reply_skips_context_when_history_exists(monkeypatch):
    fake_result = MagicMock()
    fake_result.output = "ok"
    fake_result.all_messages.return_value = ["prior", "new"]
    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_result
    monkeypatch.setattr(coach, "_agent", fake_agent)

    out, messages, logged, logged_recovery = coach.coach_reply(
        "and tomorrow?", "u", NOW, history=["prior"], context="6 sessions"
    )
    assert out == "ok"
    sent_prompt = fake_agent.run_sync.call_args[0][0]
    assert "6 sessions" not in sent_prompt      # no context re-sent
    assert "and tomorrow?" in sent_prompt
    # history is forwarded so the coach remembers the conversation
    assert fake_agent.run_sync.call_args.kwargs["message_history"] == ["prior"]


# ---------------- _build_session_data ----------------

class TestBuildSessionData:
    def test_kickboxing_minutes_count(self):
        from charts import total_minutes
        data = coach._build_session_data("kickboxing", 60)
        assert isinstance(data, StrikingData)
        assert total_minutes(data) == 60          # the stated minutes survive round-trip

    def test_cardio(self):
        data = coach._build_session_data("cardio", 45)
        assert isinstance(data, CardioData)
        assert data.duration_minutes == 45

    def test_weights(self):
        data = coach._build_session_data("weights", 50)
        assert isinstance(data, WeightsData)
        assert data.duration_minutes == 50

    def test_unknown_raises(self):
        import pytest
        with pytest.raises(ValueError):
            coach._build_session_data("yoga", 30)


# ---------------- _log_session_tool ----------------

def test_log_session_tool_saves_and_records(monkeypatch):
    captured = {}
    def fake_save(uid, session):
        captured["uid"] = uid
        captured["session"] = session
        return session.model_copy(update={"id": "sess-123"})
    monkeypatch.setattr(coach, "save_user_session", fake_save)

    ctx = SimpleNamespace(deps=coach.CoachDeps(user_id="u1", now=NOW))
    out = coach._log_session_tool(ctx, "kickboxing", 60, notes="switch kick drills")

    assert "kickboxing" in out and "60" in out
    assert captured["uid"] == "u1"
    assert captured["session"].user_id == "u1"
    assert captured["session"].notes == "switch kick drills"
    assert captured["session"].started_at == NOW           # defaulted to deps.now
    assert ctx.deps.logged == ["sess-123"]                 # recorded for UI refresh


def test_log_session_tool_rejects_unknown_discipline(monkeypatch):
    save = MagicMock()
    monkeypatch.setattr(coach, "save_user_session", save)
    ctx = SimpleNamespace(deps=coach.CoachDeps(user_id="u1", now=NOW))

    out = coach._log_session_tool(ctx, "yoga", 30)
    assert "isn't a known discipline" in out
    save.assert_not_called()


def test_log_session_tool_uses_when_iso(monkeypatch):
    captured = {}
    monkeypatch.setattr(coach, "save_user_session",
                        lambda uid, s: (captured.update(session=s), s.model_copy(update={"id": "x"}))[1])
    ctx = SimpleNamespace(deps=coach.CoachDeps(user_id="u1", now=NOW))

    coach._log_session_tool(ctx, "cardio", 30, when_iso="2026-06-10T07:30")
    assert captured["session"].started_at == datetime(2026, 6, 10, 7, 30)


# ---------------- _log_recovery_tool ----------------

def test_log_recovery_tool_saves_sleep_and_activities(monkeypatch):
    captured = {}
    def fake_save(uid, rec):
        captured["uid"] = uid
        captured["rec"] = rec
        return rec.model_copy(update={"id": "rec-1"})
    monkeypatch.setattr(coach, "save_user_recovery", fake_save)

    ctx = SimpleNamespace(deps=coach.CoachDeps(user_id="u1", now=NOW))
    out = coach._log_recovery_tool(
        ctx, sleep_hours=7.5,
        activities=[RecoveryActivity(activity_type="sauna", minutes=15)],
    )

    rec = captured["rec"]
    assert captured["uid"] == "u1"
    assert rec.user_id == "u1"
    assert rec.sleep_hours == 7.5
    assert rec.activities[0].activity_type == "sauna"
    assert rec.logged_at == NOW.replace(hour=12, minute=0)   # stamped at noon
    assert ctx.deps.logged_recovery == ["rec-1"]             # recorded for UI refresh
    assert "7.5h sleep" in out and "sauna" in out


def test_log_recovery_tool_requires_something(monkeypatch):
    save = MagicMock()
    monkeypatch.setattr(coach, "save_user_recovery", save)
    ctx = SimpleNamespace(deps=coach.CoachDeps(user_id="u1", now=NOW))

    out = coach._log_recovery_tool(ctx)                       # no sleep, no activities
    assert "sleep hours or at least one activity" in out
    save.assert_not_called()


def test_log_recovery_tool_uses_when_iso_at_noon(monkeypatch):
    captured = {}
    monkeypatch.setattr(coach, "save_user_recovery",
                        lambda uid, r: (captured.update(rec=r), r.model_copy(update={"id": "x"}))[1])
    ctx = SimpleNamespace(deps=coach.CoachDeps(user_id="u1", now=NOW))

    coach._log_recovery_tool(ctx, sleep_hours=8, when_iso="2026-06-10")
    assert captured["rec"].logged_at == datetime(2026, 6, 10, 12, 0)


def test_get_agent_lazy_construction_and_cache(monkeypatch):
    monkeypatch.setattr(coach, "_agent", None)
    first = coach._get_agent()
    second = coach._get_agent()
    assert first is second
