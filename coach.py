"""Pydantic AI training coach.

A conversational agent that answers training & recovery questions using the
athlete's OWN logged data. Same machinery as ai.py's tag extractor — a
lazily-built Pydantic AI Agent that calls the Anthropic API — with two
differences:

  1. The output is a plain string (a chat reply), not a typed model.
  2. We feed the model a short summary of the user's recent sessions and
     recovery (see build_coach_context) so its advice is personalised.

Flow per message:
    user text ─► coach_reply(message, history, context)
                     │  prepends the data summary on the first turn
                     ▼
              Agent.run_sync(...)  ─►  Anthropic API  ─►  reply text
"""

from __future__ import annotations

from pydantic_ai import Agent

from charts import current_streak, total_minutes, weekly_recovery_score
from models import RecoveryLog, Session

_SYSTEM_PROMPT = """You are an experienced strength, conditioning, and martial
arts coach built into a training-tracker app called Strain.

You will be given a summary of the athlete's recent training sessions and
recovery data. Use it to give specific, practical, encouraging advice.

Guidelines:
- Be concise and direct — a few sentences, not an essay.
- Ground your advice in their actual numbers when relevant
  (e.g. "you've trained 320 min this week, but your recovery is only 35/100").
- Balance training load against recovery: high load + low recovery = ease off.
- You are not a doctor. For pain or possible injury, suggest seeing a
  professional rather than diagnosing.
- If they have little or no data yet, encourage them to log a few sessions.
"""

_agent: Agent | None = None


def _get_agent() -> Agent:
    """Lazily construct the coach Agent.

    Same pattern as ai.py: defer ANTHROPIC_API_KEY validation until the first
    call, so importing this module works before dotenv loads the key (and in
    unit tests that mock the agent). We use a stronger model than tag
    extraction because advice quality matters more than it does for parsing.
    """
    global _agent
    if _agent is None:
        _agent = Agent(
            "anthropic:claude-sonnet-4-6",
            system_prompt=_SYSTEM_PROMPT,
        )
    return _agent


def build_coach_context(sessions: list[Session], recovery_logs: list[RecoveryLog]) -> str:
    """Summarise the athlete's recent data into a compact plain-text block
    that gets sent to the coach so its advice is about THIS athlete.

    This is a PURE function: list of models in → string out. No API call, no
    Firestore. That makes it trivial to unit-test (we feed it fake Sessions
    and assert on the string), and it's where you turn your Pydantic models
    into something the LLM can read.

    --------------------------------------------------------------------
    TODO (Elias): build and return the summary string. A good summary is
    SHORT — it's sent to the model on every conversation, so summarise, don't
    dump every record. Suggested contents (use the helpers imported above):

      - If BOTH lists are empty: return "No training data logged yet."
      - Total training minutes  → sum(total_minutes(s.data) for s in sessions)
      - Number of sessions      → len(sessions)
      - Current streak (days)   → current_streak(sessions)
      - Weekly recovery score   → weekly_recovery_score(recovery_logs, sessions)
                                   (may be None if no sleep logged — handle it)
      - The most recent ~5 sessions, one per line:
            f"{s.started_at:%b %d} · {s.data.discipline} · {total_minutes(s.data)} min"
        (hint: sessions are sorted oldest→newest, so the last 5 are sessions[-5:])

    Return one multi-line string. Example shape:

        Recent training (last 30 days):
        - 6 sessions, 410 total minutes, 3-day streak
        - Weekly recovery score: 72/100
        Recent sessions:
        - Jun 12 · bjj · 60 min
        - Jun 13 · weights · 45 min
        ...
    --------------------------------------------------------------------
    """
    if not sessions and not recovery_logs:
        return "No training data logged yet."

    total_min = sum(total_minutes(s.data) for s in sessions)
    streak = current_streak(sessions)
    week_recovery = weekly_recovery_score(recovery_logs, sessions)
    recovery_line = (
        f"{week_recovery}/100" if week_recovery is not None else "no sleep logged yet"
    )

    lines = [
        "Recent training (last 30 days):",
        f"- {len(sessions)} sessions, {total_min} total minutes, {streak}-day streak",
        f"- Weekly recovery score: {recovery_line}",
    ]

    if sessions:
        # sessions arrive sorted oldest→newest; show the most recent few.
        lines.append("Recent sessions:")
        for s in sessions[-5:]:
            lines.append(
                f"- {s.started_at:%b %d} · {s.data.discipline} · {total_minutes(s.data)} min"
            )

    return "\n".join(lines)


def coach_reply(message: str, history=None, context: str = ""):
    """Generate the coach's reply to one user `message`.

    Returns a `(reply_text, messages)` tuple:
    - `reply_text` is the string to show in the chat.
    - `messages` is the full Pydantic AI conversation so far; the caller keeps
      it and passes it back as `history` next turn, so the coach remembers
      earlier questions.

    - `context`: the summary from build_coach_context. We prepend it only on
      the FIRST turn (when there's no history yet); after that the model
      already has it in the conversation, so re-sending would waste tokens.

    Empty input short-circuits — no API call (and no charge) for a blank send.
    """
    if not message.strip():
        return "", history or []

    if context and not history:
        prompt = f"Here is my recent training data:\n\n{context}\n\nMy question: {message}"
    else:
        prompt = message

    result = _get_agent().run_sync(prompt, message_history=history)
    return result.output, result.all_messages()
