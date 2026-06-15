"""Pydantic models for the training-tracker app.

Schema layout uses a **discriminated union** on `Session.data`. The
`discipline` field on each data class is the discriminator: Pydantic
reads its value to decide which class to validate against.

Class shapes:
- GrapplingData (BJJ, wrestling): drilling + sparring + log entries
- StrikingData  (boxing, kickboxing): bag + pad + sparring + log entries
- CardioData    (running, cycling, swimming, etc.): duration + intensity
- WeightsData   (resistance training): list of exercises
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------
# Shared bits used by Grappling + Striking
# ---------------------------------------------------------------------

class Tag(BaseModel):
    """A single technique tag, extracted by the AI from free-text notes.

    Example: notes_raw="rubber guard series from bottom" → Tag(
        technique="Rubber Guard",
        position="Bottom Guard",
    )
    """

    technique: str
    position: str


class LogEntry(BaseModel):
    """One thing worked on within a session — a drill block or sparring focus.

    Used by GrapplingData and StrikingData. Cardio/Weights don't have
    log entries (their data is structured differently).
    """

    notes_raw: str
    category: Literal["drill", "spar"]
    tags: list[Tag] = []


class Exercise(BaseModel):
    """One exercise inside a WeightsData session."""

    name: str                     # "Bench Press", "Deadlift", etc.
    sets: int
    reps: int
    weight_kg: float | None = None  # None = bodyweight


# ---------------------------------------------------------------------
# Per-discipline data classes (discriminated by `discipline`)
# ---------------------------------------------------------------------

class GrapplingData(BaseModel):
    """BJJ or wrestling. Both have the same physical data shape, so they
    share one class — the discipline literal accepts either value."""

    discipline: Literal["bjj", "wrestling"]
    drilling_minutes: int = 0
    sparring_rounds: int = 0
    round_length_minutes: int = 6
    log_entries: list[LogEntry] = []


class StrikingData(BaseModel):
    """Boxing or kickboxing. Pad + bag work in addition to sparring."""

    discipline: Literal["boxing", "kickboxing"]
    bag_minutes: int = 0
    pad_minutes: int = 0
    sparring_rounds: int = 0
    round_length_minutes: int = 3  # boxing convention is 3-min rounds
    log_entries: list[LogEntry] = []


class CardioData(BaseModel):
    """Running, cycling, swimming, rowing, etc."""

    discipline: Literal["cardio"]
    activity_type: str                              # "run" | "bike" | "swim" | "row" | ...
    duration_minutes: int
    distance_km: float | None = None                # optional (rowing has no distance, etc.)
    intensity: Literal["low", "moderate", "high"] = "moderate"
    heart_rate_avg: int | None = None


class WeightsData(BaseModel):
    """Resistance training session — a list of exercises."""

    discipline: Literal["weights"]
    exercises: list[Exercise] = []
    duration_minutes: int = 0


class MmaData(BaseModel):
    """MMA session — blends grappling + striking with cage-specific work.

    Tracks both pure-grappling/pure-striking minutes AND MMA-specific
    drills like wall wrestling and strike-to-takedown chains. Sparring
    here means MMA sparring (strikes + clinch + takedowns + ground).
    """

    discipline: Literal["mma"]
    drilling_minutes: int = 0
    sparring_rounds: int = 0
    round_length_minutes: int = 5  # MMA convention is 5-min rounds
    wall_wrestling_minutes: int = 0       # cage-specific clinch/takedown work
    strikes_to_takedown_minutes: int = 0  # combo drills (strike entries → TD)
    log_entries: list[LogEntry] = []


# The discriminated union: Pydantic picks the right class from
# the `discipline` field's value.
SessionData = Annotated[
    GrapplingData | StrikingData | CardioData | WeightsData | MmaData,
    Field(discriminator="discipline"),
]


# ---------------------------------------------------------------------
# Top-level Session
# ---------------------------------------------------------------------

class Session(BaseModel):
    """One training session for one user.

    `id` is None for newly-created sessions; `save_session` assigns a
    Firestore-generated ID on first save and returns the Session with
    `id` set. `user_id` will come from Firebase Auth's UID once auth
    is wired (Phase C).
    """

    id: str | None = None         # None until saved; assigned by save_session
    user_id: str                  # owner (Firebase Auth UID)
    started_at: datetime          # session start time
    notes: str | None = None      # optional session-level note
    data: SessionData             # discriminated union — varies by discipline


# ---------------------------------------------------------------------
# Recovery — a separate concept from training.
#
# Recovery is deliberately NOT a Session discipline. A session measures
# training *stress* (minutes trained); a RecoveryLog measures what you
# did to *recover* (sleep + active recovery). Mixing them would pollute
# training stats — the weekly-load gauge, streak, discipline split, and
# charts all sum `total_minutes(session.data)`, and a sauna is not a
# workout. They live in their own collection (`recovery_logs`) and feed
# the recovery score, which reads sleep from here and training minutes
# from sessions.
# ---------------------------------------------------------------------

# The four active-recovery types the UI offers. Sleep is handled by its
# own field on RecoveryLog, not as an activity.
RecoveryActivityType = Literal["sauna", "massage", "ice_bath", "stretching"]


class RecoveryActivity(BaseModel):
    """One active-recovery block inside a RecoveryLog (e.g. 15 min sauna)."""

    activity_type: RecoveryActivityType
    minutes: int = 0


class RecoveryLog(BaseModel):
    """One day's recovery for one user: sleep + any active-recovery blocks.

    Like Session, `id` is None until saved. `logged_at` is the day the
    recovery is for (the UI stamps it at noon, since recovery is tracked
    per-day, not per-minute). `sleep_hours` is hours in bed and may be
    None if the user only logged an activity.
    """

    id: str | None = None
    user_id: str
    logged_at: datetime
    sleep_hours: float | None = None        # hours in bed
    activities: list[RecoveryActivity] = []
    notes: str | None = None
