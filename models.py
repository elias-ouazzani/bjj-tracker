"""Pydantic models for bjj-tracker.

These describe the shape of the data we store in Firestore and pass
between layers (UI, AI, database). Pydantic validates every instance
on creation, so a "bad" Session never makes it past this layer.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel


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
    """One thing you worked on within a session — a drill block or a sparring focus.

    A single Session can have multiple LogEntries (e.g. one for drilling
    spider guard, another for what you worked in rolling).
    """

    notes_raw: str
    category: Literal["drill", "spar"]
    tags: list[Tag] = []


class Session(BaseModel):
    """One BJJ training session (AM or PM).

    Doc ID convention in Firestore: "{date}_{slot}", e.g. "2026-05-20_AM".
    """

    id: str
    date: date
    slot: Literal["AM", "PM"]
    drilling_minutes: int = 0
    sparring_rounds: int = 0
    round_length_minutes: int = 6
    log_entries: list[LogEntry] = []
