"""NiceGUI app for bjj-tracker.

Single page with three sections:
1. Log a new session (date + slot + drilling/sparring totals + N log entries)
2. Stats panel (weekly score + 30-day rollups)
3. History (recent sessions; each has edit/delete actions)

Cloud Run reads PORT from env; default 8080 locally.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from nicegui import ui

from ai import extract_tags
from db import delete_session, list_sessions, save_session
from models import LogEntry, Session

load_dotenv()

# Design tokens (mirror CLAUDE.md)
BG = "#0F0F0D"
SURFACE = "#1C1B18"
ACCENT = "#E8A957"
TEXT = "#FFFFFF"
MUTED = "#888880"


def _new_entry_row(
    container: ui.column,
    entries: list[dict],
    notes: str = "",
    category: str = "drill",
) -> None:
    """Append one log-entry input row. Tracked in `entries` for read-on-save.

    Accepts initial values so the row can be pre-filled when editing an
    existing session.
    """
    entry_state: dict = {"notes": notes, "category": category}
    entries.append(entry_state)

    with container:
        with ui.row().classes("w-full items-center gap-2"):
            ui.input(placeholder="What did you work on? (1-2 sentences)", value=notes) \
                .props("dark outlined dense").classes("flex-grow") \
                .bind_value(entry_state, "notes")
            ui.select(["drill", "spar"], value=category) \
                .props("dark outlined dense").classes("w-32") \
                .bind_value(entry_state, "category")


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
        ui.label("Session Tracker — Grappling").classes("text-3xl font-bold").style(f"color: {ACCENT}")

        # ---- Log section ----
        with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
            ui.label("Log a session").classes("text-xl mb-2")

            session_state: dict = {
                "date": date.today().isoformat(),
                "slot": "AM",
                "drilling_minutes": 0,
                "sparring_rounds": 0,
                "round_length_minutes": 6,
            }
            entries: list[dict] = []
            editing_id: dict = {"value": None}  # holds original ID while editing

            with ui.row().classes("w-full gap-4"):
                ui.input("Date", value=session_state["date"]) \
                    .props("dark outlined dense type=date") \
                    .bind_value(session_state, "date")
                ui.select(["AM", "PM"], value="AM", label="Slot") \
                    .props("dark outlined dense").classes("w-24") \
                    .bind_value(session_state, "slot")

            with ui.row().classes("w-full gap-4"):
                ui.number("Drilling (min)", value=0, min=0) \
                    .props("dark outlined dense") \
                    .bind_value(session_state, "drilling_minutes")
                ui.number("Sparring rounds", value=0, min=0) \
                    .props("dark outlined dense") \
                    .bind_value(session_state, "sparring_rounds")
                ui.number("Round length (min)", value=6, min=1) \
                    .props("dark outlined dense") \
                    .bind_value(session_state, "round_length_minutes")

            ui.label("Log entries").classes("text-md mt-4").style(f"color: {MUTED}")
            entries_col = ui.column().classes("w-full gap-2")
            _new_entry_row(entries_col, entries)

            def reset_form() -> None:
                """Reset form to defaults and exit edit-mode."""
                session_state["date"] = date.today().isoformat()
                session_state["slot"] = "AM"
                session_state["drilling_minutes"] = 0
                session_state["sparring_rounds"] = 0
                session_state["round_length_minutes"] = 6
                entries.clear()
                entries_col.clear()
                _new_entry_row(entries_col, entries)
                editing_id["value"] = None
                edit_banner.refresh()

            def start_edit(session: Session) -> None:
                """Load a session's values into the form for editing."""
                editing_id["value"] = session.id
                session_state["date"] = session.date.isoformat()
                session_state["slot"] = session.slot
                session_state["drilling_minutes"] = session.drilling_minutes
                session_state["sparring_rounds"] = session.sparring_rounds
                session_state["round_length_minutes"] = session.round_length_minutes
                entries.clear()
                entries_col.clear()
                if session.log_entries:
                    for log in session.log_entries:
                        _new_entry_row(entries_col, entries, notes=log.notes_raw, category=log.category)
                else:
                    _new_entry_row(entries_col, entries)
                edit_banner.refresh()
                ui.run_javascript("window.scrollTo({top: 0, behavior: 'smooth'})")

            def on_delete(session_id: str) -> None:
                """Show a confirm dialog, delete on confirm, refresh data."""
                with ui.dialog() as dialog, ui.card().style(f"background-color: {SURFACE}; color: {TEXT}"):
                    ui.label(f"Delete session {session_id}?").classes("text-lg")
                    ui.label("This cannot be undone.").style(f"color: {MUTED}").classes("text-sm")

                    def confirm() -> None:
                        delete_session(session_id)
                        dialog.close()
                        ui.notify(f"Deleted {session_id}", color="warning")
                        # If we were editing the deleted session, drop edit state
                        if editing_id["value"] == session_id:
                            reset_form()
                        stats_panel.refresh()
                        history_container.refresh()

                    with ui.row().classes("justify-end gap-2 w-full"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        ui.button("Delete", on_click=confirm).props("color=negative")
                dialog.open()

            @ui.refreshable
            def edit_banner() -> None:
                if editing_id["value"]:
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.icon("edit").style(f"color: {ACCENT}")
                        ui.label(f"Editing {editing_id['value']} — Save to update, Cancel to discard") \
                            .style(f"color: {ACCENT}").classes("text-sm")
                        ui.button("Cancel", on_click=reset_form).props("flat dense").style(f"color: {MUTED}")

            edit_banner()

            with ui.row().classes("gap-2 mt-2"):
                ui.button("+ Add entry", on_click=lambda: _new_entry_row(entries_col, entries)) \
                    .props("flat").style(f"color: {ACCENT}")

                async def on_save() -> None:
                    ui.notify("Extracting tags…")
                    log_entries: list[LogEntry] = []
                    for e in entries:
                        notes = (e["notes"] or "").strip()
                        if not notes:
                            continue
                        tags = await asyncio.to_thread(extract_tags, notes)
                        log_entries.append(
                            LogEntry(notes_raw=notes, category=e["category"], tags=tags)
                        )

                    session_date = date.fromisoformat(session_state["date"])
                    slot = session_state["slot"]
                    session = Session(
                        id=f"{session_date.isoformat()}_{slot}",
                        date=session_date,
                        slot=slot,
                        drilling_minutes=int(session_state["drilling_minutes"]),
                        sparring_rounds=int(session_state["sparring_rounds"]),
                        round_length_minutes=int(session_state["round_length_minutes"]),
                        log_entries=log_entries,
                    )
                    save_session(session)
                    # If editing and the new ID differs (date/slot changed), drop the old doc
                    if editing_id["value"] and editing_id["value"] != session.id:
                        delete_session(editing_id["value"])
                    ui.notify(f"Saved {session.id}", color="positive")

                    reset_form()
                    stats_panel.refresh()
                    history_container.refresh()

                ui.button("Save session", on_click=on_save) \
                    .style(f"background-color: {ACCENT}; color: {BG}")

        # ---- Stats panel ----
        @ui.refreshable
        def stats_panel() -> None:
            end = date.today()
            week_start = end - timedelta(days=end.weekday())  # Monday of this week
            month_start = end - timedelta(days=30)
            try:
                month_sessions = list_sessions(month_start, end)
            except Exception:
                return
            week_sessions = [s for s in month_sessions if s.date >= week_start]
            week_mat_min = sum(
                s.drilling_minutes + s.sparring_rounds * s.round_length_minutes
                for s in week_sessions
            )
            month_session_count = len(month_sessions)
            month_rounds = sum(s.sparring_rounds for s in month_sessions)
            month_drill_hours = sum(s.drilling_minutes for s in month_sessions) / 60

            with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
                with ui.row().classes("w-full gap-4 justify-around items-center"):
                    with ui.column().classes("items-center gap-0"):
                        ui.label("THIS WEEK").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(str(week_mat_min)) \
                            .classes("text-5xl font-bold score-pulse") \
                            .style(f"color: {ACCENT}")
                        ui.label("mat minutes").style(f"color: {MUTED}").classes("text-xs")
                    with ui.column().classes("items-center gap-0"):
                        ui.label("SESSIONS").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(str(month_session_count)).classes("text-3xl font-bold")
                        ui.label("last 30 days").style(f"color: {MUTED}").classes("text-xs")
                    with ui.column().classes("items-center gap-0"):
                        ui.label("ROUNDS").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(str(month_rounds)).classes("text-3xl font-bold")
                        ui.label("last 30 days").style(f"color: {MUTED}").classes("text-xs")
                    with ui.column().classes("items-center gap-0"):
                        ui.label("DRILLING").style(f"color: {MUTED}").classes("text-xs tracking-widest")
                        ui.label(f"{month_drill_hours:.1f}h").classes("text-3xl font-bold")
                        ui.label("last 30 days").style(f"color: {MUTED}").classes("text-xs")

        stats_panel()

        # ---- History section ----
        with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
            ui.label("Recent sessions (last 30 days)").classes("text-xl mb-2")

            @ui.refreshable
            def history_container() -> None:
                end = date.today()
                start = end - timedelta(days=30)
                try:
                    sessions = list_sessions(start, end)
                except Exception as exc:
                    ui.label(f"Could not load history: {exc}").style(f"color: {MUTED}")
                    return

                if not sessions:
                    ui.label("No sessions yet.").style(f"color: {MUTED}")
                    return

                for s in sessions:
                    with ui.column().classes("w-full p-2 border-l-2 gap-1").style(f"border-color: {ACCENT}"):
                        with ui.row().classes("w-full items-center"):
                            ui.label(f"{s.date.isoformat()}  {s.slot}").classes("font-bold flex-grow")
                            ui.button(icon="edit", on_click=lambda s=s: start_edit(s)) \
                                .props("flat dense round size=sm").style(f"color: {ACCENT}")
                            ui.button(icon="delete", on_click=lambda sid=s.id: on_delete(sid)) \
                                .props("flat dense round size=sm").style(f"color: {MUTED}")
                        ui.label(
                            f"drill {s.drilling_minutes}min · "
                            f"{s.sparring_rounds}×{s.round_length_minutes}min rolls"
                        ).style(f"color: {MUTED}")
                        for e in s.log_entries:
                            ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-2")
                            with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                if not e.tags:
                                    ui.label("—").classes("text-xs").style(f"color: {MUTED}")
                                for t in e.tags:
                                    ui.label(f"{t.technique} · {t.position}") \
                                        .classes("text-xs font-semibold px-2 py-0.5 rounded-full") \
                                        .style(f"background-color: {ACCENT}; color: {BG};")

            history_container()


if __name__ in {"__main__", "__mp_main__"}:
    port = int(os.environ.get("PORT", 8080))
    ui.run(host="0.0.0.0", port=port, title="Session Tracker — Grappling", dark=True, reload=False)
