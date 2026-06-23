"""Google Calendar integration for Strain.

This module is the *planning* layer: it creates and reads events on the user's
Google Calendar. It deliberately knows NOTHING about Sessions, stats, or
Firestore — planning (calendar) and record-keeping (logged Sessions) are kept
separate on purpose. The coach is what bridges the two (see coach.py).

NOTE the filename: `gcal.py`, NOT `calendar.py`. Python ships a built-in module
called `calendar`; naming our file `calendar.py` would shadow it and cause
baffling import errors. When in doubt, don't name a file after a stdlib module.

--------------------------------------------------------------------------
How auth works here (recap from Phase 1):
  - Every function takes an `access_token` — the Google OAuth token captured at
    sign-in. We present it to Google in an "Authorization: Bearer <token>"
    header. That header is how the "key card" is actually shown at the door.
  - The token is short-lived (~1h). If it has expired, Google answers 401 and
    httpx's raise_for_status() turns that into an error the caller handles by
    asking the user to sign in again.
--------------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field

# Base URL for the Calendar REST API. "primary" means the signed-in user's
# default calendar — we don't need to know its ID.
_GCAL_BASE = "https://www.googleapis.com/calendar/v3"
_EVENTS_URL = f"{_GCAL_BASE}/calendars/primary/events"


# =====================================================================
# Pydantic models — the shape of Google's responses.
#
# We model ONLY the fields we use. Google sends ~30 per event; Pydantic
# ignores the ones we don't declare (that's the default behaviour). So this
# is small on purpose.
# =====================================================================

class EventDateTime(BaseModel):
    """The start/end of an event. Google nests the timestamp one level deep:
        "start": { "dateTime": "2026-06-24T18:00:00+02:00", "timeZone": "..." }

    Two Pydantic ideas on display here:
    - ALIAS: Google's key is `dateTime` (camelCase). We want a Pythonic
      `date_time`, so `Field(alias="dateTime")` maps one to the other.
    - COERCION: we declare `datetime`, and Pydantic parses the ISO string into
      a real datetime object for us — no manual strptime anywhere.
    """

    # populate_by_name lets us build this object in tests using either the
    # alias ("dateTime") OR the python name ("date_time"). Without it, only the
    # alias works, which makes unit tests awkward.
    model_config = ConfigDict(populate_by_name=True)

    date_time: datetime | None = Field(default=None, alias="dateTime")
    time_zone: str | None = Field(default=None, alias="timeZone")


class CalendarEvent(BaseModel):
    """One calendar event, parsed from Google's JSON.

    `id` and the start/end are the only things we truly rely on. Everything
    else is optional — defensive, because we don't control Google's payload.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    summary: str | None = None                          # the event title
    description: str | None = None
    html_link: str | None = Field(default=None, alias="htmlLink")  # link to open it
    start: EventDateTime
    end: EventDateTime


# =====================================================================
# create_event — WORKED EXAMPLE. Read this carefully; list_events below
# is your TODO and follows the same shape.
# =====================================================================

def create_event(
    access_token: str,
    summary: str,
    start: datetime,
    end: datetime,
    time_zone: str,
    description: str | None = None,
) -> CalendarEvent:
    """Create one event on the user's primary calendar and return it parsed.

    Args:
        access_token: the Google OAuth access token captured at sign-in.
        summary:      the event title, e.g. "BJJ training".
        start, end:   when the event starts/ends (datetime objects).
        time_zone:    IANA tz name, e.g. "Europe/Madrid".
        description:  optional longer note.

    Raises httpx.HTTPStatusError on a non-2xx response (e.g. 401 expired token).
    """
    # (1) The Bearer header — this is how the access token is presented.
    headers = {"Authorization": f"Bearer {access_token}"}

    # (2) The request body, in the shape Google's API expects. Note camelCase
    #     and the nested start/end — we're SENDING here, so we hand-build the
    #     dict (Pydantic is for PARSING what comes back).
    body: dict = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": time_zone},
        "end": {"dateTime": end.isoformat(), "timeZone": time_zone},
    }
    if description:
        body["description"] = description

    # (3) Make the call. raise_for_status() converts a 4xx/5xx into an
    #     exception so problems surface immediately instead of silently.
    resp = httpx.post(_EVENTS_URL, headers=headers, json=body, timeout=10.0)
    resp.raise_for_status()

    # (4) Parse Google's JSON reply into our typed model. If Google's payload
    #     is missing something required (e.g. no `id`), this raises a clear
    #     pydantic ValidationError right here, at the boundary.
    return CalendarEvent.model_validate(resp.json())


# =====================================================================
# list_events — YOUR TODO.
#
# Goal: fetch events between two datetimes and return them as a
# list[CalendarEvent]. It mirrors create_event, with these differences:
#   - it's a GET, not a POST (we're reading, not writing)
#   - the time window goes in QUERY PARAMS, not a JSON body
#   - Google returns { "items": [ {event}, {event}, ... ] }, so you parse a
#     LIST of events, not a single one.
# =====================================================================

def list_events(
    access_token: str,
    time_min: datetime,
    time_max: datetime,
) -> list[CalendarEvent]:
    """Return the user's events between time_min and time_max.

    Raises httpx.HTTPStatusError on a non-2xx response.
    """
    # (1) Same Bearer header as create_event.
    headers = {"Authorization": f"Bearer {access_token}"}

    # (2) The time window + listing options go in QUERY PARAMS (a GET has no
    #     body). timeMin/timeMax must be RFC3339 with a timezone — the caller
    #     passes timezone-aware datetimes, so .isoformat() includes the offset.
    params = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "singleEvents": "true",   # expand recurring events into individual ones
        "orderBy": "startTime",   # chronological order
    }

    # (3) GET (reading, not writing), then surface any error loudly.
    resp = httpx.get(_EVENTS_URL, headers=headers, params=params, timeout=10.0)
    resp.raise_for_status()

    # (4) Google wraps the events in an "items" list. Parse each into our model.
    items = resp.json().get("items", [])
    return [CalendarEvent.model_validate(item) for item in items]
