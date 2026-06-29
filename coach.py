"""Pydantic AI training coach.

A conversational agent that answers training & recovery questions using the
athlete's OWN logged data. Same machinery as ai.py's tag extractor — a
lazily-built Pydantic AI Agent that calls the Anthropic API — with two
differences:

  1. The output is a plain string (a chat reply), not a typed model.
  2. We feed the model a short summary of the user's recent sessions and
     recovery (see build_coach_context) so its advice is personalised.
  3. It has a TOOL — log_session — so when the user says "log my kickboxing
     session, 60 min, switch kick drills" the model calls it and we actually
     save a Session (tool / function calling).

Flow per message:
    user text ─► coach_reply(message, user_id, now, history, context)
                     │  prepends the data summary on the first turn
                     ▼
              Agent.run_sync(...)  ─►  Anthropic API
                     │                   │  may call log_session(...) ─► save_user_session
                     ▼                   ▼
                 reply text          (session saved)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from pydantic_ai import Agent, RunContext

from charts import current_streak, total_minutes, weekly_recovery_score
from gcal import create_event, list_events
from models import (
    CardioData,
    GrapplingData,
    MmaData,
    RecoveryActivity,
    RecoveryLog,
    Session,
    StrikingData,
    WeightsData,
)
from services.recovery import save_user_recovery
from services.sessions import save_user_session

log = logging.getLogger("strain.coach")

DISCIPLINES = ("bjj", "wrestling", "mma", "boxing", "kickboxing", "cardio", "weights")
RECOVERY_ACTIVITIES = ("sauna", "massage", "ice_bath", "stretching")

# Timezone used to anchor calendar events. The app has no per-user locale yet,
# so we default to the deployment region. TODO: derive this from the user.
COACH_TZ = "Europe/Brussels"

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

Logging sessions:
- When the user asks you to log / record / add / save a session, call the
  `log_session` tool. Map their words to one discipline of: bjj, wrestling,
  mma, boxing, kickboxing, cardio, weights. Pass the total training minutes
  and put what they worked on in `notes`.
- The current date and time is given at the top of each message. For "today"
  or "tonight" you can omit `when_iso` (it defaults to now). For another day,
  work out the ISO datetime from the current date and pass it as `when_iso`.
- After logging, confirm in one short sentence what you saved.
- Only log when they clearly ask you to — don't log just because they mention
  a workout.
- You CANNOT delete or edit sessions. If asked, tell them to use the History
  tab to delete a session themselves.

Logging recovery:
- When the user tells you how they slept or recovered (e.g. "slept 7.5 hours
  and did 15 min sauna", "log 8 hours sleep"), call the `log_recovery` tool.
- Pass `sleep_hours` as hours in bed (a number, may have a decimal). Put any
  active-recovery blocks in `activities` — each is {activity_type, minutes}
  where activity_type is one of: sauna, massage, ice_bath, stretching.
- You must pass at least sleep_hours OR one activity. If they only mention one,
  pass just that.
- Recovery is tracked per DAY. For "last night" / "today" omit `when_iso`; for
  another day work out the date and pass it as `when_iso`.
- After logging, confirm in one short sentence what you saved.
- Only log when they clearly ask you to. You CANNOT delete or edit recovery
  logs — point them at the Recovery tab for that.

Calendar / planning future training:
- The calendar is the PLANNING layer and is SEPARATE from logged sessions and
  stats. Scheduling training does NOT change their training numbers — only
  logging does.
- To schedule future training ("put BJJ on Tuesday 6pm", "plan 3 sessions this
  week"), call `schedule_training` with the discipline and an ISO start time.
  Work out the date from the current date & time at the top of the message.
- To answer "what's planned" / "what's on my calendar", call
  `list_planned_sessions`.
- To LOG a session that's on the calendar ("log my session from today that's on
  my calendar"), FIRST call `list_planned_sessions` for that day to find it,
  THEN call `log_session` with the discipline and minutes from that event.
  Remember: scheduling is a plan; logging is what changes stats.
- If a calendar tool reports it couldn't reach the calendar, relay that to the
  user (they may need to sign in again) — don't keep retrying.
"""


@dataclass
class CoachDeps:
    """Per-conversation data the coach's tools need.

    `user_id` ties any logged session/recovery to the right owner; `now` lets
    the tools default the time; `logged` collects the IDs of sessions and
    `logged_recovery` the IDs of recovery logs created this turn, so the UI
    knows which parts of the dashboard to refresh after a chat-driven log."""

    user_id: str
    now: datetime
    # Google OAuth access token for Calendar (Phase 1). None if the user didn't
    # grant the scope; the calendar tools degrade gracefully when it's missing.
    access_token: str | None = None
    time_zone: str = COACH_TZ
    logged: list = field(default_factory=list)
    logged_recovery: list = field(default_factory=list)


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
            deps_type=CoachDeps,
            system_prompt=_SYSTEM_PROMPT,
            tools=[
                _log_session_tool,
                _log_recovery_tool,
                _schedule_training_tool,
                _list_planned_tool,
            ],
        )
    return _agent


def _build_session_data(discipline: str, minutes: int):
    """Build the right discipline-specific data object, putting the stated
    total minutes into the field that `total_minutes` counts 1:1, so the
    session's total comes out equal to `minutes`."""
    if discipline in ("bjj", "wrestling"):
        return GrapplingData(discipline=discipline, drilling_minutes=minutes)
    if discipline == "mma":
        return MmaData(discipline="mma", drilling_minutes=minutes)
    if discipline in ("boxing", "kickboxing"):
        return StrikingData(discipline=discipline, bag_minutes=minutes)
    if discipline == "cardio":
        return CardioData(discipline="cardio", activity_type="session", duration_minutes=minutes)
    if discipline == "weights":
        return WeightsData(discipline="weights", duration_minutes=minutes)
    raise ValueError(f"Unknown discipline: {discipline}")


def _log_session_tool(
    ctx: RunContext[CoachDeps],
    discipline: str,
    minutes: int,
    notes: str = "",
    when_iso: str | None = None,
) -> str:
    """Log a training session for the athlete and save it.

    Args:
        discipline: one of bjj, wrestling, mma, boxing, kickboxing, cardio, weights.
        minutes: total training time for the session, in minutes.
        notes: short description of what they worked on (optional).
        when_iso: ISO datetime like "2026-06-16T19:00". Omit for now/today/tonight.

    Returns a short confirmation the coach can relay to the user.
    """
    disc = discipline.lower().strip()
    if disc not in DISCIPLINES:
        log.warning(
            "coach.log_session rejected: unknown discipline=%r uid=%s",
            discipline, ctx.deps.user_id,
        )
        return f"Couldn't log — '{discipline}' isn't a known discipline."
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return "Couldn't log — minutes must be a number."

    try:
        when = datetime.fromisoformat(when_iso) if when_iso else ctx.deps.now
    except ValueError:
        when = ctx.deps.now

    session = Session(
        user_id=ctx.deps.user_id,
        started_at=when,
        notes=notes or None,
        data=_build_session_data(disc, minutes),
    )
    saved = save_user_session(ctx.deps.user_id, session)
    ctx.deps.logged.append(saved.id)
    log.info(
        "coach.log_session ok uid=%s id=%s discipline=%s minutes=%d",
        ctx.deps.user_id, saved.id, disc, minutes,
    )
    return f"Logged a {minutes}-minute {disc} session for {when:%b %d}."


def _log_recovery_tool(
    ctx: RunContext[CoachDeps],
    sleep_hours: float | None = None,
    activities: list[RecoveryActivity] | None = None,
    notes: str = "",
    when_iso: str | None = None,
) -> str:
    """Log a day's recovery for the athlete: sleep and/or active-recovery blocks.

    Args:
        sleep_hours: hours in bed for the night (optional, may be a decimal).
        activities: active-recovery blocks, each {activity_type, minutes} where
            activity_type is one of: sauna, massage, ice_bath, stretching.
        notes: short free-text note (optional).
        when_iso: ISO date/datetime for the DAY this recovery is for, e.g.
            "2026-06-16". Omit for last night / today.

    Returns a short confirmation the coach can relay to the user.
    """
    acts = activities or []
    if sleep_hours is None and not acts:
        log.warning(
            "coach.log_recovery rejected: nothing to log uid=%s", ctx.deps.user_id,
        )
        return "Couldn't log recovery — give sleep hours or at least one activity."

    if sleep_hours is not None:
        try:
            sleep_hours = float(sleep_hours)
        except (TypeError, ValueError):
            return "Couldn't log recovery — sleep hours must be a number."

    # Recovery is tracked per day; stamp at noon like the Recovery tab does.
    try:
        when = datetime.fromisoformat(when_iso) if when_iso else ctx.deps.now
    except ValueError:
        when = ctx.deps.now
    when = when.replace(hour=12, minute=0, second=0, microsecond=0)

    rec = RecoveryLog(
        user_id=ctx.deps.user_id,
        logged_at=when,
        sleep_hours=sleep_hours,
        activities=acts,
        notes=notes or None,
    )
    saved = save_user_recovery(ctx.deps.user_id, rec)
    ctx.deps.logged_recovery.append(saved.id)
    log.info(
        "coach.log_recovery ok uid=%s id=%s sleep=%s activities=%d",
        ctx.deps.user_id, saved.id, sleep_hours, len(acts),
    )

    bits = []
    if sleep_hours is not None:
        bits.append(f"{sleep_hours:g}h sleep")
    if acts:
        bits.append(", ".join(f"{a.minutes}min {a.activity_type}" for a in acts))
    return f"Logged recovery for {when:%b %d}: {'; '.join(bits)}."


def _schedule_training_tool(
    ctx: RunContext[CoachDeps],
    discipline: str,
    when_iso: str,
    duration_minutes: int = 60,
    notes: str = "",
) -> str:
    """Add a PLANNED training session to the athlete's Google Calendar.

    This is the planning layer — it does NOT log a session or change stats.
    Use it when the user wants to schedule future training.

    Args:
        discipline: bjj, wrestling, mma, boxing, kickboxing, cardio, weights.
        when_iso: ISO start datetime like "2026-06-24T18:00".
        duration_minutes: how long to block out (default 60).
        notes: optional event description.

    Returns a short confirmation the coach can relay.
    """
    token = ctx.deps.access_token
    if not token:
        log.warning("coach.schedule_training: no calendar token uid=%s", ctx.deps.user_id)
        return ("Couldn't reach Google Calendar — ask the user to sign out and "
                "back in to reconnect it.")
    tz = ZoneInfo(ctx.deps.time_zone)
    try:
        start = datetime.fromisoformat(when_iso)
    except ValueError:
        log.warning(
            "coach.schedule_training: bad datetime=%r uid=%s", when_iso, ctx.deps.user_id,
        )
        return "Couldn't schedule — I need a valid date and time."
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    end = start + timedelta(minutes=duration_minutes)

    try:
        create_event(
            token,
            summary=f"{discipline.lower()} training",
            start=start,
            end=end,
            time_zone=ctx.deps.time_zone,
            description=notes or None,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            log.warning("coach.schedule_training: token expired (401) uid=%s", ctx.deps.user_id)
            return "The Google session expired — ask the user to sign in again."
        log.warning(
            "coach.schedule_training: calendar error %s uid=%s",
            exc.response.status_code, ctx.deps.user_id,
        )
        return f"Couldn't add it to the calendar (error {exc.response.status_code})."
    except Exception:
        log.exception("coach.schedule_training: unexpected calendar failure uid=%s", ctx.deps.user_id)
        return "Something went wrong reaching Google Calendar."
    log.info(
        "coach.schedule_training ok uid=%s discipline=%s start=%s",
        ctx.deps.user_id, discipline, start.isoformat(),
    )
    return f"Added {discipline} to Google Calendar on {start:%a %b %d at %H:%M}."


def _list_planned_tool(
    ctx: RunContext[CoachDeps],
    start_iso: str | None = None,
    end_iso: str | None = None,
) -> str:
    """List the athlete's PLANNED training from their Google Calendar.

    Use this to answer "what's on my calendar", and to find an event the user
    wants to log (call this first, then call log_session with what you find).

    Args:
        start_iso: window start ISO datetime. Omit for now.
        end_iso: window end ISO datetime. Omit for 7 days from now.

    Returns a short, human-readable list the coach can relay.
    """
    token = ctx.deps.access_token
    if not token:
        log.warning("coach.list_planned: no calendar token uid=%s", ctx.deps.user_id)
        return ("Couldn't reach Google Calendar — ask the user to sign in again "
                "to reconnect it.")
    tz = ZoneInfo(ctx.deps.time_zone)
    try:
        tmin = datetime.fromisoformat(start_iso) if start_iso else ctx.deps.now
        tmax = datetime.fromisoformat(end_iso) if end_iso else ctx.deps.now + timedelta(days=7)
    except ValueError:
        log.warning(
            "coach.list_planned: bad dates start=%r end=%r uid=%s",
            start_iso, end_iso, ctx.deps.user_id,
        )
        return "Couldn't read the calendar — I need valid dates."
    if tmin.tzinfo is None:
        tmin = tmin.replace(tzinfo=tz)
    if tmax.tzinfo is None:
        tmax = tmax.replace(tzinfo=tz)

    try:
        events = list_events(token, tmin, tmax)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            log.warning("coach.list_planned: token expired (401) uid=%s", ctx.deps.user_id)
            return "The Google session expired — ask the user to sign in again."
        return f"Couldn't read the calendar (error {exc.response.status_code})."
    except Exception:
        return "Something went wrong reaching Google Calendar."

    if not events:
        log.info("coach.list_planned ok uid=%s count=0", ctx.deps.user_id)
        return "No planned sessions on the calendar in that window."
    lines = []
    for ev in events:
        when = ev.start.date_time
        stamp = f"{when:%a %b %d %H:%M}" if when else "(all day)"
        lines.append(f"- {stamp} · {ev.summary or '(no title)'}")
    log.info("coach.list_planned ok uid=%s count=%d", ctx.deps.user_id, len(events))
    return "Planned sessions:\n" + "\n".join(lines)


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


def coach_reply(message: str, user_id: str, now: datetime, history=None,
                context: str = "", access_token: str | None = None):
    """Generate the coach's reply to one user `message`.

    Returns a `(reply_text, messages, logged_ids, logged_recovery_ids)` tuple:
    - `reply_text` is the string to show in the chat.
    - `messages` is the full Pydantic AI conversation so far; the caller keeps
      it and passes it back as `history` next turn, so the coach remembers
      earlier questions.
    - `logged_ids` is the IDs of any sessions the coach logged this turn (via
      the log_session tool), so the UI can refresh training stats. Empty if none.
    - `logged_recovery_ids` is the IDs of any recovery logs the coach logged
      this turn (via the log_recovery tool), so the UI can refresh the recovery
      views and Home score. Empty if none.

    - `user_id` / `now`: passed to the tool via deps so a logged session is
      owned correctly and timed sensibly.
    - `context`: the summary from build_coach_context. We prepend it only on
      the FIRST turn; after that the model already has it in the conversation.
    - `access_token`: the Google OAuth token for Calendar (Phase 1). Passed to
      the calendar tools via deps; None means calendar actions degrade to a
      "please sign in again" message.

    Empty input short-circuits — no API call (and no charge) for a blank send.
    """
    if not message.strip():
        log.debug("coach.reply skip: empty message uid=%s", user_id)
        return "", history or [], [], []

    stamp = f"(Current date & time: {now:%A %Y-%m-%d %H:%M})"
    if context and not history:
        prompt = f"{stamp}\nHere is my recent training data:\n\n{context}\n\nMy question: {message}"
    else:
        prompt = f"{stamp}\n{message}"

    deps = CoachDeps(user_id=user_id, now=now, access_token=access_token)
    log.info(
        "coach.reply start uid=%s first_turn=%s chars=%d",
        user_id, not history, len(message),
    )
    t0 = time.perf_counter()
    result = _get_agent().run_sync(prompt, message_history=history, deps=deps)
    log.info(
        "coach.reply ok uid=%s ms=%.0f reply_chars=%d logged=%d recovery=%d",
        user_id, (time.perf_counter() - t0) * 1000,
        len(result.output or ""), len(deps.logged), len(deps.logged_recovery),
    )
    return result.output, result.all_messages(), deps.logged, deps.logged_recovery
