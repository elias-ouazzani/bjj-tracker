"""Tests for coach.py — the Agent is mocked, no LLM calls happen.

build_coach_context is a pure function so it's tested directly; coach_reply
is tested with a mocked agent (same pattern as test_ai.py).
"""

from datetime import datetime
from unittest.mock import MagicMock

import coach
from models import CardioData, GrapplingData, RecoveryLog, Session


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
    assert coach.coach_reply("") == ("", [])
    assert coach.coach_reply("   \n ") == ("", [])


def test_coach_reply_prepends_context_on_first_turn(monkeypatch):
    fake_result = MagicMock()
    fake_result.output = "Train hard, sleep more."
    fake_result.all_messages.return_value = ["m1", "m2"]
    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_result
    monkeypatch.setattr(coach, "_agent", fake_agent)

    out, messages = coach.coach_reply("How am I doing?", history=None, context="6 sessions")
    assert out == "Train hard, sleep more."
    assert messages == ["m1", "m2"]            # returned for the next turn
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

    out, messages = coach.coach_reply("and tomorrow?", history=["prior"], context="6 sessions")
    assert out == "ok"
    sent_prompt = fake_agent.run_sync.call_args[0][0]
    assert sent_prompt == "and tomorrow?"       # no context re-sent
    # history is forwarded so the coach remembers the conversation
    assert fake_agent.run_sync.call_args.kwargs["message_history"] == ["prior"]


def test_get_agent_lazy_construction_and_cache(monkeypatch):
    monkeypatch.setattr(coach, "_agent", None)
    first = coach._get_agent()
    second = coach._get_agent()
    assert first is second
