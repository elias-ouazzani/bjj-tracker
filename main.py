"""NiceGUI app for the training-tracker.

Multi-discipline: BJJ, wrestling, boxing, kickboxing, cardio, weights.
Auth is not wired yet — all sessions are written with a stub user_id.
Phase C will swap this for the authenticated Firebase user's UID.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from nicegui import ui

from ai import extract_tags
from db import delete_session, list_sessions, save_session
from models import (
    CardioData,
    Exercise,
    GrapplingData,
    LogEntry,
    Session,
    StrikingData,
    WeightsData,
)

load_dotenv()

# Design tokens
BG = "#0F0F0D"
SURFACE = "#1C1B18"
ACCENT = "#E8A957"
TEXT = "#FFFFFF"
MUTED = "#888880"

# Stub until Firebase Auth lands in Phase C
STUB_USER_ID = "stub-user-1"

DISCIPLINES = ["bjj", "wrestling", "boxing", "kickboxing", "cardio", "weights"]
INTENSITIES = ["low", "moderate", "high"]


def _total_minutes(data) -> int:
    """Mat-time / training-time minutes for stats aggregation."""
    if isinstance(data, GrapplingData):
        return data.drilling_minutes + data.sparring_rounds * data.round_length_minutes
    if isinstance(data, StrikingData):
        return data.bag_minutes + data.pad_minutes + data.sparring_rounds * data.round_length_minutes
    if isinstance(data, (CardioData, WeightsData)):
        return data.duration_minutes
    return 0


def _new_entry_row(container, entries, notes="", category="drill"):
    """Append one log-entry input row (used by grappling + striking)."""
    state = {"notes": notes, "category": category}
    entries.append(state)
    with container:
        with ui.row().classes("w-full items-center gap-2"):
            ui.input(placeholder="What did you work on?", value=notes) \
                .props("dark outlined dense").classes("flex-grow") \
                .bind_value(state, "notes")
            ui.select(["drill", "spar"], value=category) \
                .props("dark outlined dense").classes("w-32") \
                .bind_value(state, "category")


def _new_exercise_row(container, exercises, name="", sets=3, reps=10, weight_kg=None):
    """Append one exercise input row (used by weights discipline)."""
    state = {"name": name, "sets": sets, "reps": reps, "weight_kg": weight_kg}
    exercises.append(state)
    with container:
        with ui.row().classes("w-full items-center gap-2"):
            ui.input(placeholder="Exercise name", value=name) \
                .props("dark outlined dense").classes("flex-grow") \
                .bind_value(state, "name")
            ui.number("Sets", value=sets, min=1) \
                .props("dark outlined dense").classes("w-20") \
                .bind_value(state, "sets")
            ui.number("Reps", value=reps, min=1) \
                .props("dark outlined dense").classes("w-20") \
                .bind_value(state, "reps")
            ui.number("kg", value=weight_kg, min=0) \
                .props("dark outlined dense").classes("w-24") \
                .bind_value(state, "weight_kg")


@ui.page("/")
def index() -> None:
    ui.colors(primary=ACCENT)
    ui.query("body").style(f"background-color: {BG}; color: {TEXT}; font-family: 'JetBrains Mono', monospace;")
    ui.add_css("""
        @keyframes scoreflash {
            0%   { transform: scale(1); }
            30%  { transform: scale(1.18); filter: brightness(1.4); }
            100% { transform: scale(1); }
        }
        .score-pulse { animation: scoreflash 0.7s ease-out; transform-origin: center; }
    """)

    with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-6"):
        ui.label("Session Tracker — Training").classes("text-3xl font-bold").style(f"color: {ACCENT}")

        # ---- Log section ----
        with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
            ui.label("Log a session").classes("text-xl mb-2")

            today = date.today()
            session_state: dict = {
                "discipline": "bjj",
                "date": today.isoformat(),
                "time": "09:00",
                "notes": "",
                # grappling
                "drilling_minutes": 0,
                "sparring_rounds": 0,
                "round_length_minutes": 6,
                # striking
                "bag_minutes": 0,
                "pad_minutes": 0,
                # cardio
                "activity_type": "run",
                "duration_minutes": 0,
                "distance_km": None,
                "intensity": "moderate",
                "heart_rate_avg": None,
                # weights
                "weights_duration_minutes": 0,
            }
            entries: list[dict] = []     # for grappling / striking
            exercises: list[dict] = []   # for weights
            editing_id: dict = {"value": None}

            # Date + time + discipline picker
            with ui.row().classes("w-full gap-4"):
                ui.input("Date", value=session_state["date"]) \
                    .props("dark outlined dense type=date") \
                    .bind_value(session_state, "date")
                ui.input("Time", value=session_state["time"]) \
                    .props("dark outlined dense type=time") \
                    .bind_value(session_state, "time")
                ui.select(DISCIPLINES, value="bjj", label="Discipline") \
                    .props("dark outlined dense").classes("min-w-40") \
                    .bind_value(session_state, "discipline") \
                    .on("update:model-value", lambda: discipline_form.refresh())

            ui.input("Session notes (optional)").props("dark outlined dense").classes("w-full") \
                .bind_value(session_state, "notes")

            # ---- Per-discipline form (refreshes when discipline changes) ----
            @ui.refreshable
            def discipline_form():
                d = session_state["discipline"]

                if d in ("bjj", "wrestling"):
                    with ui.row().classes("w-full gap-4"):
                        ui.number("Drilling (min)", value=session_state["drilling_minutes"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "drilling_minutes")
                        ui.number("Sparring rounds", value=session_state["sparring_rounds"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "sparring_rounds")
                        ui.number("Round length (min)", value=session_state["round_length_minutes"], min=1) \
                            .props("dark outlined dense").bind_value(session_state, "round_length_minutes")
                    ui.label("Log entries").classes("text-md mt-2").style(f"color: {MUTED}")
                    nonlocal_entries_col = ui.column().classes("w-full gap-2")
                    entries.clear()
                    _new_entry_row(nonlocal_entries_col, entries)
                    discipline_form._entries_col = nonlocal_entries_col

                elif d in ("boxing", "kickboxing"):
                    with ui.row().classes("w-full gap-4"):
                        ui.number("Bag (min)", value=session_state["bag_minutes"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "bag_minutes")
                        ui.number("Pads (min)", value=session_state["pad_minutes"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "pad_minutes")
                        ui.number("Sparring rounds", value=session_state["sparring_rounds"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "sparring_rounds")
                        ui.number("Round length (min)", value=session_state.get("round_length_minutes", 3), min=1) \
                            .props("dark outlined dense").bind_value(session_state, "round_length_minutes")
                    ui.label("Log entries").classes("text-md mt-2").style(f"color: {MUTED}")
                    nonlocal_entries_col = ui.column().classes("w-full gap-2")
                    entries.clear()
                    _new_entry_row(nonlocal_entries_col, entries)
                    discipline_form._entries_col = nonlocal_entries_col

                elif d == "cardio":
                    with ui.row().classes("w-full gap-4"):
                        ui.input("Activity type", value=session_state["activity_type"]) \
                            .props("dark outlined dense").classes("flex-grow") \
                            .bind_value(session_state, "activity_type")
                        ui.number("Duration (min)", value=session_state["duration_minutes"], min=1) \
                            .props("dark outlined dense").bind_value(session_state, "duration_minutes")
                    with ui.row().classes("w-full gap-4"):
                        ui.number("Distance (km)", value=session_state["distance_km"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "distance_km")
                        ui.select(INTENSITIES, value=session_state["intensity"], label="Intensity") \
                            .props("dark outlined dense").classes("w-32") \
                            .bind_value(session_state, "intensity")
                        ui.number("Avg HR (bpm)", value=session_state["heart_rate_avg"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "heart_rate_avg")

                elif d == "weights":
                    with ui.row().classes("w-full gap-4"):
                        ui.number("Total duration (min)", value=session_state["weights_duration_minutes"], min=0) \
                            .props("dark outlined dense").bind_value(session_state, "weights_duration_minutes")
                    ui.label("Exercises").classes("text-md mt-2").style(f"color: {MUTED}")
                    ex_col = ui.column().classes("w-full gap-2")
                    exercises.clear()
                    _new_exercise_row(ex_col, exercises)
                    discipline_form._ex_col = ex_col

            discipline_form()

            # Add entry / Add exercise buttons (only show for relevant disciplines)
            with ui.row().classes("gap-2 mt-2"):
                def add_row():
                    d = session_state["discipline"]
                    if d in ("bjj", "wrestling", "boxing", "kickboxing") and hasattr(discipline_form, "_entries_col"):
                        _new_entry_row(discipline_form._entries_col, entries)
                    elif d == "weights" and hasattr(discipline_form, "_ex_col"):
                        _new_exercise_row(discipline_form._ex_col, exercises)
                ui.button("+ Add row", on_click=add_row).props("flat").style(f"color: {ACCENT}")

            # ---- Edit banner ----
            @ui.refreshable
            def edit_banner():
                if editing_id["value"]:
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.icon("edit").style(f"color: {ACCENT}")
                        ui.label(f"Editing — Save to update, Cancel to discard") \
                            .style(f"color: {ACCENT}").classes("text-sm")
                        ui.button("Cancel", on_click=lambda: reset_form()) \
                            .props("flat dense").style(f"color: {MUTED}")

            edit_banner()

            # ---- Save handler ----
            async def on_save() -> None:
                ui.notify("Saving…")
                d = session_state["discipline"]

                # Build the per-discipline data object
                if d in ("bjj", "wrestling"):
                    log_entries = []
                    for e in entries:
                        notes = (e["notes"] or "").strip()
                        if not notes:
                            continue
                        tags = await asyncio.to_thread(extract_tags, notes)
                        log_entries.append(LogEntry(notes_raw=notes, category=e["category"], tags=tags))
                    data = GrapplingData(
                        discipline=d,
                        drilling_minutes=int(session_state["drilling_minutes"]),
                        sparring_rounds=int(session_state["sparring_rounds"]),
                        round_length_minutes=int(session_state["round_length_minutes"]),
                        log_entries=log_entries,
                    )
                elif d in ("boxing", "kickboxing"):
                    log_entries = []
                    for e in entries:
                        notes = (e["notes"] or "").strip()
                        if not notes:
                            continue
                        tags = await asyncio.to_thread(extract_tags, notes)
                        log_entries.append(LogEntry(notes_raw=notes, category=e["category"], tags=tags))
                    data = StrikingData(
                        discipline=d,
                        bag_minutes=int(session_state["bag_minutes"]),
                        pad_minutes=int(session_state["pad_minutes"]),
                        sparring_rounds=int(session_state["sparring_rounds"]),
                        round_length_minutes=int(session_state["round_length_minutes"]),
                        log_entries=log_entries,
                    )
                elif d == "cardio":
                    data = CardioData(
                        discipline="cardio",
                        activity_type=session_state["activity_type"],
                        duration_minutes=int(session_state["duration_minutes"]),
                        distance_km=float(session_state["distance_km"]) if session_state["distance_km"] else None,
                        intensity=session_state["intensity"],
                        heart_rate_avg=int(session_state["heart_rate_avg"]) if session_state["heart_rate_avg"] else None,
                    )
                elif d == "weights":
                    ex_objs = [
                        Exercise(
                            name=e["name"],
                            sets=int(e["sets"]),
                            reps=int(e["reps"]),
                            weight_kg=float(e["weight_kg"]) if e["weight_kg"] else None,
                        )
                        for e in exercises if e["name"].strip()
                    ]
                    data = WeightsData(
                        discipline="weights",
                        exercises=ex_objs,
                        duration_minutes=int(session_state["weights_duration_minutes"]),
                    )

                started = datetime.fromisoformat(f"{session_state['date']}T{session_state['time']}:00")
                session = Session(
                    id=editing_id["value"],
                    user_id=STUB_USER_ID,
                    started_at=started,
                    notes=session_state["notes"] or None,
                    data=data,
                )
                saved = save_session(session)
                ui.notify(f"Saved", color="positive")
                editing_id["value"] = None
                reset_form()
                stats_panel.refresh()
                history_container.refresh()

            ui.button("Save session", on_click=on_save) \
                .style(f"background-color: {ACCENT}; color: {BG}")

            def reset_form():
                session_state["date"] = date.today().isoformat()
                session_state["time"] = "09:00"
                session_state["notes"] = ""
                session_state["drilling_minutes"] = 0
                session_state["sparring_rounds"] = 0
                session_state["round_length_minutes"] = 6
                session_state["bag_minutes"] = 0
                session_state["pad_minutes"] = 0
                session_state["duration_minutes"] = 0
                session_state["distance_km"] = None
                session_state["intensity"] = "moderate"
                session_state["heart_rate_avg"] = None
                session_state["weights_duration_minutes"] = 0
                editing_id["value"] = None
                discipline_form.refresh()
                edit_banner.refresh()

            def on_delete(session_id: str):
                with ui.dialog() as dialog, ui.card().style(f"background-color: {SURFACE}; color: {TEXT}"):
                    ui.label("Delete this session?").classes("text-lg")
                    ui.label("This cannot be undone.").style(f"color: {MUTED}").classes("text-sm")
                    def confirm():
                        delete_session(session_id)
                        dialog.close()
                        ui.notify("Deleted", color="warning")
                        stats_panel.refresh()
                        history_container.refresh()
                    with ui.row().classes("justify-end gap-2 w-full"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        ui.button("Delete", on_click=confirm).props("color=negative")
                dialog.open()

        # ---- Stats panel ----
        @ui.refreshable
        def stats_panel() -> None:
            end = datetime.now()
            week_start = datetime.combine(end.date() - timedelta(days=end.weekday()), datetime.min.time())
            month_start = end - timedelta(days=30)
            try:
                month_sessions = list_sessions(STUB_USER_ID, month_start, end)
            except Exception:
                return
            week_sessions = [s for s in month_sessions if s.started_at >= week_start]
            week_mat_min = sum(_total_minutes(s.data) for s in week_sessions)
            session_count = len(month_sessions)
            disciplines = {s.data.discipline for s in month_sessions}
            total_min = sum(_total_minutes(s.data) for s in month_sessions)

            with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
                with ui.row().classes("w-full gap-4 justify-around items-center"):
                    with ui.column().classes("items-center gap-0"):
                        ui.label("THIS WEEK").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(str(week_mat_min)).classes("text-5xl font-bold score-pulse").style(f"color: {ACCENT}")
                        ui.label("minutes").style(f"color: {MUTED}").classes("text-xs")
                    with ui.column().classes("items-center gap-0"):
                        ui.label("SESSIONS").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(str(session_count)).classes("text-3xl font-bold")
                        ui.label("last 30 days").style(f"color: {MUTED}").classes("text-xs")
                    with ui.column().classes("items-center gap-0"):
                        ui.label("MINUTES").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(str(total_min)).classes("text-3xl font-bold")
                        ui.label("last 30 days").style(f"color: {MUTED}").classes("text-xs")
                    with ui.column().classes("items-center gap-0"):
                        ui.label("DISCIPLINES").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(str(len(disciplines))).classes("text-3xl font-bold")
                        ui.label("last 30 days").style(f"color: {MUTED}").classes("text-xs")

        stats_panel()

        # ---- History ----
        with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
            ui.label("Recent sessions (last 30 days)").classes("text-xl mb-2")

            @ui.refreshable
            def history_container() -> None:
                end = datetime.now()
                start = end - timedelta(days=30)
                try:
                    sessions = list_sessions(STUB_USER_ID, start, end)
                except Exception as exc:
                    ui.label(f"Could not load history: {exc}").style(f"color: {MUTED}")
                    return
                if not sessions:
                    ui.label("No sessions yet.").style(f"color: {MUTED}")
                    return

                for s in reversed(sessions):
                    with ui.column().classes("w-full p-2 border-l-2 gap-1").style(f"border-color: {ACCENT}"):
                        with ui.row().classes("w-full items-center"):
                            ui.label(f"{s.started_at.strftime('%Y-%m-%d %H:%M')} · {s.data.discipline}") \
                                .classes("font-bold flex-grow")
                            ui.button(icon="delete", on_click=lambda sid=s.id: on_delete(sid)) \
                                .props("flat dense round size=sm").style(f"color: {MUTED}")

                        # Per-discipline summary line
                        if isinstance(s.data, GrapplingData):
                            ui.label(
                                f"drill {s.data.drilling_minutes}min · "
                                f"{s.data.sparring_rounds}×{s.data.round_length_minutes}min rolls"
                            ).style(f"color: {MUTED}")
                            for e in s.data.log_entries:
                                ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1")
                                with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                    for t in e.tags:
                                        ui.label(f"{t.technique} · {t.position}") \
                                            .classes("text-xs font-semibold px-2 py-0.5 rounded-full") \
                                            .style(f"background-color: {ACCENT}; color: {BG};")

                        elif isinstance(s.data, StrikingData):
                            ui.label(
                                f"bag {s.data.bag_minutes}min · pads {s.data.pad_minutes}min · "
                                f"{s.data.sparring_rounds}×{s.data.round_length_minutes}min sparring"
                            ).style(f"color: {MUTED}")
                            for e in s.data.log_entries:
                                ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1")
                                with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                    for t in e.tags:
                                        ui.label(f"{t.technique} · {t.position}") \
                                            .classes("text-xs font-semibold px-2 py-0.5 rounded-full") \
                                            .style(f"background-color: {ACCENT}; color: {BG};")

                        elif isinstance(s.data, CardioData):
                            parts = [f"{s.data.activity_type}", f"{s.data.duration_minutes}min", s.data.intensity]
                            if s.data.distance_km:
                                parts.append(f"{s.data.distance_km}km")
                            if s.data.heart_rate_avg:
                                parts.append(f"{s.data.heart_rate_avg}bpm")
                            ui.label(" · ".join(parts)).style(f"color: {MUTED}")

                        elif isinstance(s.data, WeightsData):
                            ui.label(f"{s.data.duration_minutes}min · {len(s.data.exercises)} exercises") \
                                .style(f"color: {MUTED}")
                            for ex in s.data.exercises:
                                wt = f" @ {ex.weight_kg}kg" if ex.weight_kg else ""
                                ui.label(f"  {ex.name} — {ex.sets}×{ex.reps}{wt}").classes("text-sm")

                        if s.notes:
                            ui.label(f"note: {s.notes}").classes("text-xs italic mt-1").style(f"color: {MUTED}")

            history_container()


if __name__ in {"__main__", "__mp_main__"}:
    port = int(os.environ.get("PORT", 8080))
    ui.run(host="0.0.0.0", port=port, title="Training Tracker", dark=True, reload=False)
