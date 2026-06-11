"""NiceGUI app for the training-tracker.

Multi-discipline: BJJ, wrestling, MMA, boxing, kickboxing, cardio, weights.
Dashboard / Log / History tabs. Auth: Google sign-in via Firebase (popup),
verified server-side; the user's Firebase uid is the per-session user_id.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from fastapi import Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from nicegui import app, ui

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,  # stdout so Cloud Run doesn't tag every line ERROR
)
log = logging.getLogger("strain.main")

from ai import extract_tags
from auth import verify_id_token
from charts import (
    current_streak,
    discipline_totals,
    total_minutes,
    weekly_discipline_minutes,
)
from services.sessions import (
    SessionAccessDenied,
    SessionNotFound,
    delete_user_session,
    list_user_sessions,
    save_user_session,
)
from models import (
    CardioData,
    Exercise,
    GrapplingData,
    LogEntry,
    MmaData,
    Session,
    StrikingData,
    WeightsData,
)

load_dotenv()

AUTH_COOKIE_NAME = "strain_auth"
AUTH_COOKIE_MAX_AGE = 14 * 24 * 60 * 60


@app.middleware("http")
async def _add_coop_header(request, call_next):
    """Allow Firebase Auth popup to postMessage back. Without this,
    modern browsers silently close the sign-in popup before its result
    reaches our JS."""
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return response


# Firebase web SDK config — public, not a secret. Safe to embed.
# authDomain must match the OAuth client's registered redirect URI, which
# Firebase auto-pins to <project>.firebaseapp.com.
# Sign-in is popup-only (like the auth-practice app): works on desktop where
# popups aren't blocked. The COOP header above lets the popup postMessage back.
FIREBASE_CONFIG_JS = """{
  apiKey: "AIzaSyDNrJL5vN4TFSzlBmh8gSjxW0jWy3Ir9js",
  authDomain: "atheal-internship-elias.firebaseapp.com",
  projectId: "atheal-internship-elias",
  storageBucket: "atheal-internship-elias.firebasestorage.app",
  messagingSenderId: "264025165631",
  appId: "1:264025165631:web:706577eb5755308c61cd8a"
}"""


def _auth_serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("STORAGE_SECRET", "dev-only-not-for-production")
    return URLSafeTimedSerializer(secret, salt="strain-auth")


def _make_auth_cookie(decoded: dict) -> str:
    return _auth_serializer().dumps({
        "uid": decoded["uid"],
        "email": decoded.get("email", ""),
        "name": decoded.get("name", decoded.get("email", "user")),
    })


def _read_auth_cookie(request: Request) -> dict | None:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    try:
        return _auth_serializer().loads(token, max_age=AUTH_COOKIE_MAX_AGE)
    except BadSignature:
        return None


def _cookie_is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _inject_firebase_sdk() -> None:
    """Embed the Firebase Auth JS SDK and define the popup sign-in flow.

    Same shape as the auth-practice app: popup -> get ID token -> POST it to
    /auth/callback -> on success, go to /. No redirect fallback, no
    onAuthStateChanged/getRedirectResult — one path, one way.

    Progress is logged to the browser console and relayed to the server so it
    lands in Cloud Run logs — no on-screen banner. Elias debugs on Windows with
    no easy devtools, so the server log is the timeline. See
    [[feedback-debug-without-devtools]].
    """
    ui.add_head_html(f"""
    <script type="module">
      import {{ initializeApp }} from "https://www.gstatic.com/firebasejs/10.7.0/firebase-app.js";
      import {{
        getAuth,
        GoogleAuthProvider,
        signInWithPopup,
        signOut
      }} from "https://www.gstatic.com/firebasejs/10.7.0/firebase-auth.js";

      const __authT0 = Date.now();
      const __authLoadId = Math.random().toString(36).slice(2, 8);
      function showStatus(msg) {{
        const line = '+' + ((Date.now() - __authT0) / 1000).toFixed(1) + 's  ' + msg;
        console.log('[auth]', line);
        // Relay every breadcrumb to the server so it lands in Cloud Run logs.
        // Elias debugs on Windows + iPhone with no devtools, so the server log
        // is the only place we can read this timeline. Fire-and-forget — never
        // let logging break the auth flow. See [[feedback-debug-without-devtools]].
        try {{
          fetch('/auth/clientlog', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            credentials: 'same-origin',
            keepalive: true,
            body: JSON.stringify({{
              loadId: __authLoadId,
              host: window.location.host,
              path: window.location.pathname,
              tMs: Date.now() - __authT0,
              msg: msg
            }})
          }}).catch(function () {{}});
        }} catch (e) {{}}
      }}
      window.__authStatus = showStatus;

      let fbApp, auth;
      try {{
        showStatus('init: host=' + window.location.host);
        fbApp = initializeApp({FIREBASE_CONFIG_JS});
        auth = getAuth(fbApp);
        showStatus('init: ok authDomain=' + (auth.config && auth.config.authDomain));
      }} catch (e) {{
        showStatus('init FAILED: ' + e.message);
        throw e;
      }}

      // Popup-only sign-in (like the auth-practice app): one path, one way.
      window.firebaseSignIn = async () => {{
        const provider = new GoogleAuthProvider();
        try {{
          // (a) browser opens Google's account picker; Google hands back a token
          showStatus('1/4 opening Google popup…');
          const result = await signInWithPopup(auth, provider);
          showStatus('2/4 signed in as ' + result.user.email + ' — getting token…');
          const idToken = await result.user.getIdToken();

          // (b) send the token to OUR server to verify + start the session
          showStatus('3/4 got token (len=' + idToken.length + ') — verifying on server…');
          const resp = await fetch('/auth/callback', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            credentials: 'same-origin',
            body: JSON.stringify({{ idToken }})
          }});
          const data = await resp.json().catch(() => ({{}}));

          // (c) on success the server has set the auth cookie — load the app
          if (resp.ok && data.ok) {{
            showStatus('4/4 verified — loading app…');
            window.location.replace('/');
          }} else {{
            showStatus('❌ server rejected (' + resp.status + '): ' + (data.error || 'unknown'));
          }}
        }} catch (e) {{
          showStatus('❌ sign-in failed: ' + (e.code || '') + ' ' + e.message);
        }}
      }};

      window.firebaseSignOut = async () => {{
        try {{ await signOut(auth); }} catch (e) {{ showStatus('Sign-out failed: ' + e.message); }}
      }};

      window.handleSignInClick = () => {{
        if (window.firebaseSignIn) window.firebaseSignIn();
      }};
    </script>
    """)


@app.post("/auth/clientlog")
async def auth_clientlog(request: Request):
    """Sink for client-side auth breadcrumbs.

    The login JS POSTs every showStatus() line here so the full client
    timeline lands in the server (Cloud Run) logs — the only place we can
    read it when debugging on a device with no devtools. Pure observability:
    it stores nothing and always returns ok so it can never break sign-in.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    log.info(
        "auth.clientlog load=%s host=%s path=%s t=%sms msg=%r",
        body.get("loadId"), body.get("host"), body.get("path"),
        body.get("tMs"), body.get("msg"),
    )
    return JSONResponse({"ok": True})


@app.post("/auth/callback")
async def auth_callback(request: Request):
    """Verify the Firebase ID token sent by the browser, then set the signed
    auth cookie that keeps the user logged in."""
    # Log request origin so we can spot a page-origin vs Firebase-authDomain
    # mismatch — the prime suspect when popup auth misbehaves on Cloud Run.
    log.info(
        "auth/callback: received host=%s origin=%s xfwd_proto=%s",
        request.url.hostname,
        request.headers.get("origin"),
        request.headers.get("x-forwarded-proto"),
    )
    body = await request.json()
    id_token = body.get("idToken")
    if not id_token:
        log.warning("auth/callback: request with no idToken")
        return JSONResponse({"ok": False, "error": "missing idToken"}, status_code=400)
    try:
        decoded = verify_id_token(id_token)
    except Exception as exc:
        # Bad/expired token is the USER's problem, not ours -> WARNING.
        log.warning("auth/callback: token rejected: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)
    log.info("auth/callback: verified uid=%s email=%s",
             decoded["uid"], decoded.get("email"))
    response = JSONResponse({"ok": True, "uid": decoded["uid"]})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _make_auth_cookie(decoded),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=_cookie_is_secure(request),
        samesite="lax",
    )
    log.info("auth/callback: session cookie set for uid=%s", decoded["uid"])
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request):
    """Delete the signed auth cookie, logging the user out."""
    log.info("auth/logout: clearing session cookie")
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response


# ---------------- Design tokens ----------------

BG = "#0F0F0D"
SURFACE = "#1C1B18"
SURFACE_HI = "#26241F"  # slightly lighter for hover/elevated cards
ACCENT = "#E8A957"
TEXT = "#FFFFFF"
MUTED = "#888880"

# Per-discipline visual identity
DISCIPLINE_COLORS: dict[str, str] = {
    "bjj":        "#E8A957",  # warm orange (gi)
    "wrestling":  "#A77BCA",  # purple
    "mma":        "#E84B3C",  # red
    "boxing":     "#F26F4C",  # orange-red
    "kickboxing": "#E91E63",  # pink
    "cardio":     "#5BC68B",  # green
    "weights":    "#5BA0F2",  # blue
}

DISCIPLINE_ICONS: dict[str, str] = {
    "bjj":        "sports_martial_arts",
    "wrestling":  "sports_kabaddi",
    "mma":        "sports_mma",
    "boxing":     "sports_mma",
    "kickboxing": "sports_mma",
    "cardio":     "directions_run",
    "weights":    "fitness_center",
}

DISCIPLINE_LABELS: dict[str, str] = {
    "bjj": "BJJ",
    "wrestling": "Wrestling",
    "mma": "MMA",
    "boxing": "Boxing",
    "kickboxing": "Kickboxing",
    "cardio": "Cardio",
    "weights": "Weights",
}

DISCIPLINES = list(DISCIPLINE_COLORS.keys())
INTENSITIES = ["low", "moderate", "high"]


# ---------------- Form row helpers ----------------

def _new_entry_row(container, entries, notes="", category="drill"):
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
    state = {"name": name, "sets": sets, "reps": reps, "weight_kg": weight_kg}
    exercises.append(state)
    with container:
        with ui.row().classes("w-full items-center gap-2"):
            ui.input(placeholder="Exercise", value=name) \
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


# ---------------- Pages ----------------

@ui.page("/login")
def login_page() -> None:
    """Sign-in page. Popup Google OAuth via Firebase, verify token
    server-side, store uid in session, redirect to /."""
    _inject_firebase_sdk()
    ui.colors(primary=ACCENT)
    ui.query("body").style(
        f"background-color: {BG}; color: {TEXT}; "
        f"font-family: 'JetBrains Mono', monospace;"
    )

    with ui.column().classes("items-center justify-center w-full min-h-screen gap-4 p-6"):
        ui.icon("sports_martial_arts").style(f"color: {ACCENT}; font-size: 4rem;")
        ui.label("Strain").classes("text-4xl font-bold tracking-wide").style(f"color: {TEXT}")
        ui.label("Training and fitness tracker").style(f"color: {MUTED}")
        # Pure client-side click handler via NiceGUI's js_handler. We CAN'T
        # use ui.button(on_click=...) here because Firebase Hosting breaks
        # the NiceGUI WebSocket; server-side click handlers silently no-op
        # on iPhone when the app is reached via firebaseapp.com.
        ui.button("Sign in with Google", icon="login") \
            .props('size=lg') \
            .style(f"background-color: {ACCENT}; color: {BG}; margin-top: 1rem;") \
            .on('click', js_handler='() => window.handleSignInClick && window.handleSignInClick()')
        ui.label("Each user's sessions stay private to them.") \
            .classes("text-xs mt-4").style(f"color: {MUTED}")


@ui.page("/")
def index(request: Request) -> None:
    # Auth gate — redirect unauthenticated visitors to /login.
    auth_session = _read_auth_cookie(request)
    if not auth_session or not auth_session.get("uid"):
        log.info("auth gate: unauthenticated request to / -> /login")
        ui.navigate.to("/login")
        return
    log.info("auth gate: authenticated uid=%s", auth_session["uid"])

    current_user_id: str = auth_session["uid"]
    current_user_name: str = auth_session.get("name", "user")

    _inject_firebase_sdk()
    ui.colors(primary=ACCENT)
    ui.query("body").style(
        f"background-color: {BG}; color: {TEXT}; "
        f"font-family: 'JetBrains Mono', monospace;"
    )
    ui.add_css(f"""
        @keyframes scoreflash {{
            0%   {{ transform: scale(1); }}
            30%  {{ transform: scale(1.15); filter: brightness(1.4); }}
            100% {{ transform: scale(1); }}
        }}
        .score-pulse {{ animation: scoreflash 0.7s ease-out; transform-origin: center; }}

        .session-card {{
            transition: transform 0.15s ease, background-color 0.15s ease, box-shadow 0.15s ease;
        }}
        .session-card:hover {{
            transform: translateY(-2px);
            background-color: {SURFACE_HI} !important;
            box-shadow: 0 4px 16px rgba(0,0,0,0.4);
        }}
        .stat-tile {{
            transition: transform 0.15s ease;
        }}
        .stat-tile:hover {{
            transform: translateY(-2px);
        }}
        .app-header {{
            background-color: {SURFACE};
            border-bottom: 1px solid #2a2925;
        }}
        .q-tab {{
            color: {MUTED} !important;
        }}
        .q-tab--active {{
            color: {ACCENT} !important;
        }}
        .empty-state {{
            text-align: center;
            padding: 3rem 1rem;
            color: {MUTED};
        }}
    """)

    async def sign_out() -> None:
        try:
            await ui.run_javascript("""
              await window.firebaseSignOut();
              await fetch('/auth/logout', {method: 'POST', credentials: 'same-origin'});
            """, timeout=10)
        except Exception:
            pass
        ui.navigate.to("/login")

    # ---- Header ----
    with ui.header().classes("app-header items-center px-6 py-3").props("elevated=false"):
        with ui.row().classes("items-center gap-3 w-full max-w-5xl mx-auto"):
            ui.icon("sports_martial_arts").style(f"color: {ACCENT}; font-size: 1.8rem;")
            with ui.column().classes("gap-0"):
                ui.label("Strain").classes("text-xl font-bold tracking-wide").style(f"color: {TEXT}")
                ui.label("Training and fitness tracker").classes("text-xs").style(f"color: {MUTED}")
            ui.space()
            ui.label(f"Hi, {current_user_name}").classes("text-sm").style(f"color: {MUTED}")
            ui.button(icon="logout", on_click=sign_out) \
                .props("flat dense round").style(f"color: {MUTED}").tooltip("Sign out")

    # ---- Form state (shared across tabs) ----
    today = date.today()
    session_state: dict = {
        "discipline": "bjj",
        "date": today.isoformat(),
        "time": "09:00",
        "notes": "",
        "drilling_minutes": 0,
        "sparring_rounds": 0,
        "round_length_minutes": 6,
        "bag_minutes": 0,
        "pad_minutes": 0,
        "wall_wrestling_minutes": 0,
        "strikes_to_takedown_minutes": 0,
        "activity_type": "run",
        "duration_minutes": 0,
        "distance_km": None,
        "intensity": "moderate",
        "heart_rate_avg": None,
        "weights_duration_minutes": 0,
    }
    entries: list[dict] = []
    exercises: list[dict] = []
    editing_id: dict = {"value": None}

    # ---- Tabs ----
    with ui.tabs().classes("w-full max-w-5xl mx-auto") as tabs:
        tab_dash = ui.tab("Dashboard", icon="dashboard")
        tab_log = ui.tab("Log session", icon="add_circle")
        tab_history = ui.tab("History", icon="history")

    with ui.tab_panels(tabs, value=tab_dash).classes("w-full max-w-5xl mx-auto").style(f"background-color: {BG}"):

        # ============= DASHBOARD =============
        with ui.tab_panel(tab_dash).classes("p-0"):
            with ui.column().classes("w-full gap-6 p-6"):
                ui.label("Dashboard").classes("text-2xl font-bold").style(f"color: {TEXT}")

                @ui.refreshable
                def stats_panel() -> None:
                    end = datetime.now()
                    week_start = datetime.combine(end.date() - timedelta(days=end.weekday()), datetime.min.time())
                    month_start = end - timedelta(days=30)
                    try:
                        month_sessions = list_user_sessions(current_user_id, month_start, end)
                    except Exception:
                        log.exception("stats_panel.list_sessions uid=%s", current_user_id)
                        ui.label("Could not load stats.").style(f"color: {MUTED}")
                        return
                    week_sessions = [s for s in month_sessions if s.started_at >= week_start]
                    week_mat_min = sum(total_minutes(s.data) for s in week_sessions)
                    streak = current_streak(month_sessions)
                    total_min = sum(total_minutes(s.data) for s in month_sessions)
                    discipline_count = len({s.data.discipline for s in month_sessions})

                    def tile(icon, label, value, sub, big=False, pulse=False, color=ACCENT):
                        with ui.card().classes("stat-tile flex-1").style(
                            f"background-color: {SURFACE}; min-width: 180px;"
                        ):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon(icon).style(f"color: {color}; font-size: 1.2rem;")
                                ui.label(label).style(f"color: {MUTED}").classes("text-xs tracking-widest uppercase")
                            classes = "font-bold mt-1 " + ("text-5xl " if big else "text-3xl ")
                            if pulse:
                                classes += "score-pulse"
                            ui.label(str(value)).classes(classes).style(f"color: {color}")
                            ui.label(sub).style(f"color: {MUTED}").classes("text-xs")

                    with ui.row().classes("w-full gap-4 flex-wrap"):
                        tile("local_fire_department", "Streak", streak, "day(s) in a row",
                             big=True, pulse=True, color=ACCENT)
                        tile("schedule", "This week", week_mat_min, "training minutes",
                             color=ACCENT)
                        tile("event", "Sessions", len(month_sessions), "last 30 days",
                             color=TEXT)
                        tile("timer", "Total minutes", total_min, "last 30 days",
                             color=TEXT)
                        tile("category", "Disciplines", discipline_count, "last 30 days",
                             color=TEXT)

                stats_panel()

                # ---- Charts (weekly stacked bar + discipline donut) ----
                @ui.refreshable
                def charts_row() -> None:
                    end = datetime.now()
                    try:
                        month_sessions = list_user_sessions(current_user_id, end - timedelta(days=60), end)
                    except Exception:
                        log.exception("charts_row.list_sessions uid=%s", current_user_id)
                        return

                    weekly = weekly_discipline_minutes(month_sessions, n_weeks=8)
                    totals = discipline_totals(
                        [s for s in month_sessions if s.started_at >= end - timedelta(days=30)]
                    )

                    with ui.row().classes("w-full gap-4 flex-wrap"):
                        # --- Weekly stacked bar chart ---
                        with ui.card().classes("flex-1").style(
                            f"background-color: {SURFACE}; min-width: 360px;"
                        ):
                            ui.label("Last 8 weeks · minutes by discipline") \
                                .classes("text-sm font-bold mb-2").style(f"color: {TEXT}")
                            if not weekly["series"]:
                                with ui.column().classes("empty-state w-full items-center gap-2"):
                                    ui.icon("bar_chart").style(f"color: {MUTED}; font-size: 2.5rem;")
                                    ui.label("Not enough data yet.").classes("text-sm")
                            else:
                                bar_series = [
                                    {
                                        "name": DISCIPLINE_LABELS.get(d, d),
                                        "type": "bar",
                                        "stack": "total",
                                        "data": values,
                                        "itemStyle": {"color": DISCIPLINE_COLORS.get(d, ACCENT)},
                                    }
                                    for d, values in weekly["series"].items()
                                ]
                                ui.echart({
                                    "tooltip": {
                                        "trigger": "axis",
                                        "axisPointer": {"type": "shadow"},
                                        "backgroundColor": SURFACE,
                                        "borderColor": MUTED,
                                        "textStyle": {"color": TEXT},
                                    },
                                    "legend": {
                                        "textStyle": {"color": MUTED},
                                        "top": 0,
                                    },
                                    "grid": {"left": 40, "right": 16, "top": 36, "bottom": 24},
                                    "xAxis": {
                                        "type": "category",
                                        "data": weekly["weeks"],
                                        "axisLabel": {"color": MUTED, "fontSize": 10},
                                        "axisLine": {"lineStyle": {"color": MUTED}},
                                    },
                                    "yAxis": {
                                        "type": "value",
                                        "axisLabel": {"color": MUTED, "fontSize": 10},
                                        "splitLine": {"lineStyle": {"color": "#2a2925"}},
                                    },
                                    "series": bar_series,
                                }).classes("w-full").style("height: 280px;")

                        # --- Discipline donut ---
                        with ui.card().classes("flex-1").style(
                            f"background-color: {SURFACE}; min-width: 280px;"
                        ):
                            ui.label("Last 30 days · discipline split") \
                                .classes("text-sm font-bold mb-2").style(f"color: {TEXT}")
                            if not totals:
                                with ui.column().classes("empty-state w-full items-center gap-2"):
                                    ui.icon("donut_large").style(f"color: {MUTED}; font-size: 2.5rem;")
                                    ui.label("Not enough data yet.").classes("text-sm")
                            else:
                                pie_data = [
                                    {
                                        "value": minutes,
                                        "name": DISCIPLINE_LABELS.get(d, d),
                                        "itemStyle": {"color": DISCIPLINE_COLORS.get(d, ACCENT)},
                                    }
                                    for d, minutes in sorted(totals.items(), key=lambda x: -x[1])
                                ]
                                ui.echart({
                                    "tooltip": {
                                        "trigger": "item",
                                        "formatter": "{b}: {c} min ({d}%)",
                                        "backgroundColor": SURFACE,
                                        "borderColor": MUTED,
                                        "textStyle": {"color": TEXT},
                                    },
                                    "legend": {
                                        "textStyle": {"color": MUTED},
                                        "orient": "vertical",
                                        "left": "left",
                                        "top": "middle",
                                    },
                                    "series": [{
                                        "name": "Minutes",
                                        "type": "pie",
                                        "radius": ["45%", "70%"],
                                        "center": ["65%", "50%"],
                                        "avoidLabelOverlap": True,
                                        "itemStyle": {"borderColor": SURFACE, "borderWidth": 2},
                                        "label": {"show": False},
                                        "labelLine": {"show": False},
                                        "data": pie_data,
                                    }],
                                }).classes("w-full").style("height: 280px;")

                charts_row()

                # Recent sessions snapshot (last 5)
                with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
                    ui.label("Recent activity").classes("text-lg font-bold mb-2")

                    @ui.refreshable
                    def recent_snapshot() -> None:
                        end = datetime.now()
                        try:
                            sessions = list_user_sessions(current_user_id, end - timedelta(days=30), end)
                        except Exception as exc:
                            ui.label(f"Could not load: {exc}").style(f"color: {MUTED}")
                            return
                        if not sessions:
                            with ui.column().classes("empty-state w-full items-center gap-2"):
                                ui.icon("history").style(f"color: {MUTED}; font-size: 2.5rem;")
                                ui.label("No sessions yet — log your first one to see it here.").classes("text-sm")
                            return
                        for s in list(reversed(sessions))[:5]:
                            color = DISCIPLINE_COLORS.get(s.data.discipline, ACCENT)
                            with ui.row().classes("w-full items-center gap-3 p-2"):
                                ui.icon(DISCIPLINE_ICONS.get(s.data.discipline, "circle")) \
                                    .style(f"color: {color}; font-size: 1.5rem;")
                                with ui.column().classes("gap-0 flex-grow"):
                                    ui.label(
                                        f"{DISCIPLINE_LABELS.get(s.data.discipline, s.data.discipline)} · "
                                        f"{s.started_at.strftime('%b %d %H:%M')}"
                                    ).classes("text-sm font-semibold").style(f"color: {TEXT}")
                                    ui.label(f"{total_minutes(s.data)} min").style(f"color: {MUTED}").classes("text-xs")

                    recent_snapshot()

        # ============= LOG SESSION =============
        with ui.tab_panel(tab_log).classes("p-0"):
            with ui.column().classes("w-full gap-4 p-6"):
                ui.label("Log a session").classes("text-2xl font-bold").style(f"color: {TEXT}")

                with ui.card().classes("w-full").style(f"background-color: {SURFACE}"):
                    # Date + time + discipline
                    with ui.row().classes("w-full gap-4"):
                        ui.input("Date", value=session_state["date"]) \
                            .props("dark outlined dense type=date") \
                            .bind_value(session_state, "date")
                        ui.input("Time", value=session_state["time"]) \
                            .props("dark outlined dense type=time") \
                            .bind_value(session_state, "time")
                        ui.select(
                            {d: DISCIPLINE_LABELS[d] for d in DISCIPLINES},
                            value="bjj", label="Discipline",
                        ).props("dark outlined dense").classes("min-w-40") \
                            .bind_value(session_state, "discipline") \
                            .on("update:model-value", lambda: discipline_form.refresh())

                    ui.input("Session notes (optional)") \
                        .props("dark outlined dense").classes("w-full") \
                        .bind_value(session_state, "notes")

                    @ui.refreshable
                    def discipline_form():
                        d = session_state["discipline"]
                        color = DISCIPLINE_COLORS.get(d, ACCENT)
                        # Visual cue: discipline label with its icon + color
                        with ui.row().classes("items-center gap-2 mt-2"):
                            ui.icon(DISCIPLINE_ICONS.get(d, "circle")) \
                                .style(f"color: {color}; font-size: 1.3rem;")
                            ui.label(DISCIPLINE_LABELS.get(d, d)).classes("text-md font-bold") \
                                .style(f"color: {color}")

                        if d in ("bjj", "wrestling"):
                            with ui.row().classes("w-full gap-4"):
                                ui.number("Drilling (min)", value=session_state["drilling_minutes"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "drilling_minutes")
                                ui.number("Sparring rounds", value=session_state["sparring_rounds"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "sparring_rounds")
                                ui.number("Round length (min)", value=session_state["round_length_minutes"], min=1) \
                                    .props("dark outlined dense").bind_value(session_state, "round_length_minutes")
                            ui.label("Log entries").classes("text-sm mt-2").style(f"color: {MUTED}")
                            nonlocal_col = ui.column().classes("w-full gap-2")
                            entries.clear()
                            _new_entry_row(nonlocal_col, entries)
                            discipline_form._entries_col = nonlocal_col

                        elif d == "mma":
                            with ui.row().classes("w-full gap-4"):
                                ui.number("Drilling (min)", value=session_state["drilling_minutes"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "drilling_minutes")
                                ui.number("Wall wrestling (min)", value=session_state["wall_wrestling_minutes"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "wall_wrestling_minutes")
                                ui.number("Strike→TD (min)", value=session_state["strikes_to_takedown_minutes"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "strikes_to_takedown_minutes")
                            with ui.row().classes("w-full gap-4"):
                                ui.number("Sparring rounds", value=session_state["sparring_rounds"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "sparring_rounds")
                                ui.number("Round length (min)", value=session_state.get("round_length_minutes", 5), min=1) \
                                    .props("dark outlined dense").bind_value(session_state, "round_length_minutes")
                            ui.label("Log entries").classes("text-sm mt-2").style(f"color: {MUTED}")
                            nonlocal_col = ui.column().classes("w-full gap-2")
                            entries.clear()
                            _new_entry_row(nonlocal_col, entries)
                            discipline_form._entries_col = nonlocal_col

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
                            ui.label("Log entries").classes("text-sm mt-2").style(f"color: {MUTED}")
                            nonlocal_col = ui.column().classes("w-full gap-2")
                            entries.clear()
                            _new_entry_row(nonlocal_col, entries)
                            discipline_form._entries_col = nonlocal_col

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
                            ui.label("Exercises").classes("text-sm mt-2").style(f"color: {MUTED}")
                            ex_col = ui.column().classes("w-full gap-2")
                            exercises.clear()
                            _new_exercise_row(ex_col, exercises)
                            discipline_form._ex_col = ex_col

                    discipline_form()

                    @ui.refreshable
                    def edit_banner():
                        if editing_id["value"]:
                            with ui.row().classes("items-center gap-2 mt-2 p-2 rounded") \
                                    .style(f"background-color: {SURFACE_HI};"):
                                ui.icon("edit").style(f"color: {ACCENT}")
                                ui.label("Editing — Save to update, Cancel to discard").classes("text-sm") \
                                    .style(f"color: {ACCENT}")
                                ui.button("Cancel", on_click=lambda: reset_form()) \
                                    .props("flat dense").style(f"color: {MUTED}")

                    edit_banner()

                    with ui.row().classes("gap-2 mt-2"):
                        def add_row():
                            d = session_state["discipline"]
                            if d in ("bjj", "wrestling", "mma", "boxing", "kickboxing") and hasattr(discipline_form, "_entries_col"):
                                _new_entry_row(discipline_form._entries_col, entries)
                            elif d == "weights" and hasattr(discipline_form, "_ex_col"):
                                _new_exercise_row(discipline_form._ex_col, exercises)
                        ui.button("Add row", on_click=add_row, icon="add").props("flat") \
                            .style(f"color: {ACCENT}")

                        async def on_save() -> None:
                            ui.notify("Saving…")
                            d = session_state["discipline"]
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
                            elif d == "mma":
                                log_entries = []
                                for e in entries:
                                    notes = (e["notes"] or "").strip()
                                    if not notes:
                                        continue
                                    tags = await asyncio.to_thread(extract_tags, notes)
                                    log_entries.append(LogEntry(notes_raw=notes, category=e["category"], tags=tags))
                                data = MmaData(
                                    discipline="mma",
                                    drilling_minutes=int(session_state["drilling_minutes"]),
                                    sparring_rounds=int(session_state["sparring_rounds"]),
                                    round_length_minutes=int(session_state["round_length_minutes"]),
                                    wall_wrestling_minutes=int(session_state["wall_wrestling_minutes"]),
                                    strikes_to_takedown_minutes=int(session_state["strikes_to_takedown_minutes"]),
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
                                user_id=current_user_id,
                                started_at=started,
                                notes=session_state["notes"] or None,
                                data=data,
                            )
                            try:
                                saved = save_user_session(current_user_id, session)
                            except SessionAccessDenied:
                                ui.notify("Cannot save — session belongs to another user", color="negative")
                                return
                            log.info(
                                "session.save uid=%s id=%s discipline=%s",
                                current_user_id, saved.id, d,
                            )
                            ui.notify("Saved", color="positive")
                            editing_id["value"] = None
                            reset_form()
                            stats_panel.refresh()
                            charts_row.refresh()
                            recent_snapshot.refresh()
                            history_container.refresh()

                        ui.button("Save session", on_click=on_save, icon="check") \
                            .style(f"background-color: {ACCENT}; color: {BG};")

                    def reset_form():
                        session_state["date"] = date.today().isoformat()
                        session_state["time"] = "09:00"
                        session_state["notes"] = ""
                        session_state["drilling_minutes"] = 0
                        session_state["sparring_rounds"] = 0
                        session_state["round_length_minutes"] = 6
                        session_state["bag_minutes"] = 0
                        session_state["pad_minutes"] = 0
                        session_state["wall_wrestling_minutes"] = 0
                        session_state["strikes_to_takedown_minutes"] = 0
                        session_state["duration_minutes"] = 0
                        session_state["distance_km"] = None
                        session_state["intensity"] = "moderate"
                        session_state["heart_rate_avg"] = None
                        session_state["weights_duration_minutes"] = 0
                        editing_id["value"] = None
                        discipline_form.refresh()
                        edit_banner.refresh()

        # ============= HISTORY =============
        with ui.tab_panel(tab_history).classes("p-0"):
            with ui.column().classes("w-full gap-4 p-6"):
                ui.label("History").classes("text-2xl font-bold").style(f"color: {TEXT}")

                def on_delete(session_id: str):
                    with ui.dialog() as dialog, ui.card().style(f"background-color: {SURFACE}; color: {TEXT}"):
                        ui.label("Delete this session?").classes("text-lg")
                        ui.label("This cannot be undone.").style(f"color: {MUTED}").classes("text-sm")
                        def confirm():
                            try:
                                delete_user_session(current_user_id, session_id)
                            except (SessionNotFound, SessionAccessDenied):
                                dialog.close()
                                ui.notify("Could not delete session", color="negative")
                                return
                            log.info("session.delete uid=%s id=%s", current_user_id, session_id)
                            dialog.close()
                            ui.notify("Deleted", color="warning")
                            stats_panel.refresh()
                            charts_row.refresh()
                            recent_snapshot.refresh()
                            history_container.refresh()
                        with ui.row().classes("justify-end gap-2 w-full"):
                            ui.button("Cancel", on_click=dialog.close).props("flat")
                            ui.button("Delete", on_click=confirm).props("color=negative")
                    dialog.open()

                @ui.refreshable
                def history_container() -> None:
                    end = datetime.now()
                    start = end - timedelta(days=30)
                    try:
                        sessions = list_user_sessions(current_user_id, start, end)
                    except Exception as exc:
                        ui.label(f"Could not load history: {exc}").style(f"color: {MUTED}")
                        return
                    if not sessions:
                        with ui.column().classes("empty-state w-full items-center gap-2"):
                            ui.icon("inbox").style(f"color: {MUTED}; font-size: 3rem;")
                            ui.label("No sessions in the last 30 days.").classes("text-sm")
                            ui.label("Head to the Log Session tab to add one.").classes("text-xs")
                        return

                    for s in reversed(sessions):
                        color = DISCIPLINE_COLORS.get(s.data.discipline, ACCENT)
                        icon = DISCIPLINE_ICONS.get(s.data.discipline, "circle")
                        label = DISCIPLINE_LABELS.get(s.data.discipline, s.data.discipline)

                        with ui.card().classes("session-card w-full").style(
                            f"background-color: {SURFACE}; border-left: 4px solid {color};"
                        ):
                            with ui.row().classes("w-full items-center gap-3"):
                                ui.icon(icon).style(f"color: {color}; font-size: 1.7rem;")
                                with ui.column().classes("gap-0 flex-grow"):
                                    ui.label(
                                        f"{label} · {s.started_at.strftime('%a %b %d · %H:%M')}"
                                    ).classes("font-bold").style(f"color: {TEXT}")
                                    ui.label(f"{total_minutes(s.data)} total minutes") \
                                        .classes("text-xs").style(f"color: {MUTED}")
                                ui.button(icon="delete", on_click=lambda sid=s.id: on_delete(sid)) \
                                    .props("flat dense round size=sm").style(f"color: {MUTED}")

                            # Discipline-specific summary
                            if isinstance(s.data, GrapplingData):
                                ui.label(
                                    f"drill {s.data.drilling_minutes}min · "
                                    f"{s.data.sparring_rounds}×{s.data.round_length_minutes}min rolls"
                                ).classes("text-sm").style(f"color: {MUTED}")
                                for e in s.data.log_entries:
                                    ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1")
                                    with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                        for t in e.tags:
                                            ui.label(f"{t.technique} · {t.position}") \
                                                .classes("text-xs font-semibold px-2 py-0.5 rounded-full") \
                                                .style(f"background-color: {color}; color: {BG};")

                            elif isinstance(s.data, MmaData):
                                parts = []
                                if s.data.drilling_minutes:
                                    parts.append(f"drill {s.data.drilling_minutes}min")
                                if s.data.wall_wrestling_minutes:
                                    parts.append(f"wall {s.data.wall_wrestling_minutes}min")
                                if s.data.strikes_to_takedown_minutes:
                                    parts.append(f"S→TD {s.data.strikes_to_takedown_minutes}min")
                                if s.data.sparring_rounds:
                                    parts.append(f"{s.data.sparring_rounds}×{s.data.round_length_minutes}min sparring")
                                ui.label(" · ".join(parts)).classes("text-sm").style(f"color: {MUTED}")
                                for e in s.data.log_entries:
                                    ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1")
                                    with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                        for t in e.tags:
                                            ui.label(f"{t.technique} · {t.position}") \
                                                .classes("text-xs font-semibold px-2 py-0.5 rounded-full") \
                                                .style(f"background-color: {color}; color: {BG};")

                            elif isinstance(s.data, StrikingData):
                                ui.label(
                                    f"bag {s.data.bag_minutes}min · pads {s.data.pad_minutes}min · "
                                    f"{s.data.sparring_rounds}×{s.data.round_length_minutes}min sparring"
                                ).classes("text-sm").style(f"color: {MUTED}")
                                for e in s.data.log_entries:
                                    ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1")
                                    with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                        for t in e.tags:
                                            ui.label(f"{t.technique} · {t.position}") \
                                                .classes("text-xs font-semibold px-2 py-0.5 rounded-full") \
                                                .style(f"background-color: {color}; color: {BG};")

                            elif isinstance(s.data, CardioData):
                                parts = [f"{s.data.activity_type}", f"{s.data.duration_minutes}min", s.data.intensity]
                                if s.data.distance_km:
                                    parts.append(f"{s.data.distance_km}km")
                                if s.data.heart_rate_avg:
                                    parts.append(f"{s.data.heart_rate_avg}bpm")
                                ui.label(" · ".join(parts)).classes("text-sm").style(f"color: {MUTED}")

                            elif isinstance(s.data, WeightsData):
                                ui.label(f"{s.data.duration_minutes}min · {len(s.data.exercises)} exercises") \
                                    .classes("text-sm").style(f"color: {MUTED}")
                                for ex in s.data.exercises:
                                    wt = f" @ {ex.weight_kg}kg" if ex.weight_kg else ""
                                    ui.label(f"  {ex.name} — {ex.sets}×{ex.reps}{wt}").classes("text-sm")

                            if s.notes:
                                ui.label(f"note: {s.notes}").classes("text-xs italic mt-1") \
                                    .style(f"color: {MUTED}")

                history_container()


if __name__ in {"__main__", "__mp_main__"}:
    port = int(os.environ.get("PORT", 8080))
    # storage_secret encrypts the session cookie that backs app.storage.user.
    # In production set STORAGE_SECRET via Cloud Run env var (Secret Manager).
    storage_secret = os.environ.get("STORAGE_SECRET", "dev-only-not-for-production")
    log.info("strain.startup port=%s log_level=%s", port, logging.getLogger().getEffectiveLevel())
    ui.run(
        host="0.0.0.0", port=port,
        title="Strain — Training and fitness tracker",
        dark=True, reload=False,
        storage_secret=storage_secret,
    )
