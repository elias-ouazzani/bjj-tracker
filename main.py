"""NiceGUI app for bjj-tracker.

Single page with two sections:
1. Log a new session (date + slot + drilling/sparring totals + N log entries)
2. History (recent sessions with their tags)

Cloud Run reads PORT from env; default 8080 locally.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

from dotenv import load_dotenv
from nicegui import ui

from ai import extract_tags
from db import list_sessions, save_session
from models import LogEntry, Session

load_dotenv()

# Design tokens (mirror CLAUDE.md)
BG = "#0F0F0D"
SURFACE = "#1C1B18"
ACCENT = "#E8A957"
TEXT = "#FFFFFF"
MUTED = "#888880"


def _new_entry_row(container: ui.column, entries: list[dict]) -> None:
    """Append one log-entry input row. Tracked in `entries` for read-on-save."""
    entry_state: dict = {"notes": "", "category": "drill"}
    entries.append(entry_state)

    with container:
        with ui.row().classes("w-full items-center gap-2"):
            ui.input(placeholder="What did you work on? (1-2 sentences)") \
                .props("dark outlined dense").classes("flex-grow") \
                .bind_value(entry_state, "notes")
            ui.select(["drill", "spar"], value="drill") \
                .props("dark outlined dense").classes("w-32") \
                .bind_value(entry_state, "category")


@ui.page("/")
def index() -> None:
    ui.colors(primary=ACCENT)
    ui.query("body").style(f"background-color: {BG}; color: {TEXT}; font-family: 'JetBrains Mono', monospace;")

    with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-6"):
        ui.label("bjj-tracker").classes("text-3xl font-bold").style(f"color: {ACCENT}")

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
                        tags = extract_tags(notes)
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
                    ui.notify(f"Saved {session.id}", color="positive")
                    history_container.refresh()

                ui.button("Save session", on_click=on_save) \
                    .style(f"background-color: {ACCENT}; color: {BG}")

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
                    with ui.column().classes("w-full p-2 border-l-2").style(f"border-color: {ACCENT}"):
                        ui.label(f"{s.date.isoformat()}  {s.slot}").classes("font-bold")
                        ui.label(
                            f"drill {s.drilling_minutes}min · "
                            f"{s.sparring_rounds}×{s.round_length_minutes}min rolls"
                        ).style(f"color: {MUTED}")
                        for e in s.log_entries:
                            tag_str = ", ".join(f"{t.technique} ({t.position})" for t in e.tags) or "—"
                            ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm")
                            ui.label(f"  tags: {tag_str}").classes("text-xs").style(f"color: {MUTED}")

            history_container()


if __name__ in {"__main__", "__mp_main__"}:
    port = int(os.environ.get("PORT", 8080))
    ui.run(host="0.0.0.0", port=port, title="bjj-tracker", dark=True, reload=False)
