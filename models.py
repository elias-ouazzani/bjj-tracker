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


# The discriminated union: Pydantic picks the right class from
# the `discipline` field's value.
SessionData = Annotated[
    GrapplingData | StrikingData | CardioData | WeightsData,
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
