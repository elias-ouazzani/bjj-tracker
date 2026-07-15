"""NiceGUI app for the training-tracker.

Multi-discipline: BJJ, wrestling, MMA, boxing, kickboxing, cardio, weights.
Dashboard / Log / History tabs. Auth: Google sign-in via Firebase (popup),
verified server-side; the user's Firebase uid is the per-session user_id.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import time
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

import analytics
from ai import extract_tags
from auth import verify_id_token
from coach import build_coach_context, coach_reply
from charts import (
    current_streak,
    discipline_totals,
    recovery_score_on,
    total_minutes,
    weekly_discipline_minutes,
    weekly_recovery_score,
)
from services.sessions import (
    SessionAccessDenied,
    SessionNotFound,
    delete_user_session,
    list_user_sessions,
    save_user_session,
)
from services.recovery import (
    RecoveryAccessDenied,
    RecoveryNotFound,
    delete_user_recovery,
    list_user_recovery,
    save_user_recovery,
)
from models import (
    CardioData,
    Exercise,
    GrapplingData,
    LogEntry,
    MmaData,
    RecoveryActivity,
    RecoveryLog,
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


def _make_auth_cookie(decoded: dict, google_access_token: str | None = None) -> str:
    return _auth_serializer().dumps({
        "uid": decoded["uid"],
        "email": decoded.get("email", ""),
        "name": decoded.get("name", decoded.get("email", "user")),
        # Google OAuth access token for the calendar.events scope, captured at
        # sign-in. Short-lived (~1h) and NOT refreshable here, so calendar calls
        # start failing once it expires and the user must sign in again. The
        # cookie is signed + httponly + HTTPS-only, but signed is NOT encrypted —
        # in production an OAuth token belongs in encrypted server-side session
        # storage, not a cookie. Acceptable here for a single-user learning app.
        "gcal_token": google_access_token,
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
        // SCOPE: also ask Google for permission to manage the user's calendar
        // events. This is what makes the consent popup say "Strain wants to
        // manage your calendar". Without it we'd only get an identity token.
        provider.addScope('https://www.googleapis.com/auth/calendar.events');
        try {{
          // (a) browser opens Google's account picker; Google hands back a token
          showStatus('1/4 opening Google popup…');
          const result = await signInWithPopup(auth, provider);
          showStatus('2/4 signed in as ' + result.user.email + ' — getting token…');
          const idToken = await result.user.getIdToken();

          // ---------------------------------------------------------------
          // TODO (Elias): pull the Google OAuth ACCESS TOKEN out of `result`.
          //   - `idToken` above proves WHO the user is (used for login).
          //   - the ACCESS TOKEN is the "key card" our backend uses to call
          //     Calendar on the user's behalf. It lives on the OAuth credential
          //     attached to the sign-in result, NOT on result.user.
          //   Hints (two lines):
          //     const credential = GoogleAuthProvider.credentialFromResult(result);
          //     const accessToken = credential ? credential.accessToken : null;
          //   credentialFromResult() can return null — handle that, don't crash.
          // ---------------------------------------------------------------
          const credential = GoogleAuthProvider.credentialFromResult(result);
          const accessToken = credential ? credential.accessToken : null;

          // (b) send BOTH tokens to OUR server to verify + start the session
          showStatus('3/4 got tokens (id len=' + idToken.length +
                     ', access=' + (accessToken ? 'yes' : 'no') + ') — verifying…');
          const resp = await fetch('/auth/callback', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            credentials: 'same-origin',
            body: JSON.stringify({{ idToken, accessToken }})
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
    # The Google OAuth access token for the calendar scope. May be absent if the
    # user declined the calendar permission — login must still succeed without it.
    google_access_token = body.get("accessToken")
    if not id_token:
        log.warning("auth/callback: request with no idToken")
        return JSONResponse({"ok": False, "error": "missing idToken"}, status_code=400)
    try:
        decoded = verify_id_token(id_token)
    except Exception as exc:
        # Bad/expired token is the USER's problem, not ours -> WARNING.
        log.warning("auth/callback: token rejected: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)
    # NEVER log the token itself — just whether we got one (a secret in logs is a leak).
    log.info("auth/callback: verified uid=%s email=%s gcal_token=%s",
             decoded["uid"], decoded.get("email"),
             "present" if google_access_token else "absent")
    # Analytics: record the login and attach who this uid is (email/name) as
    # durable person properties via $set — this is our "identify".
    analytics.capture("logged_in", decoded["uid"], {
        "login_method": "google",
        "connected_calendar": bool(google_access_token),
        "$set": {"email": decoded.get("email"), "name": decoded.get("name")},
    })
    response = JSONResponse({"ok": True, "uid": decoded["uid"]})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        _make_auth_cookie(decoded, google_access_token),
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


# ============================================================
# Design system — "Strain" (Whoop-inspired, via Claude Design handoff).
# Dark, serious, performance-device feel. The big number IS the design;
# color communicates meaning only, never decoration. Cards are shade-only
# (no border, no shadow); separation is by background shade. Signature
# element is the ScoreRing gauge. Typeface: Hanken Grotesk (single grotesk,
# heavy weights carry the stat numbers, tabular figures on).
# Tokens mirror the handoff's tokens/*.css.
# ============================================================

# --- Base surfaces (shade-only elevation, no borders) ---
BG = "#0D0D0D"          # near-black, slightly warm — app background
SURFACE = "#1A1A1A"     # dark charcoal — cards, minimal contrast from bg
ELEVATED = "#242424"    # active / selected / hovered surfaces
TRACK = "#2A2A2A"       # gauge background track, inactive bars
HAIRLINE = "#2E2E2E"    # rare, near-invisible divider

# --- Text ---
TEXT = "#F5F5F5"        # off-white, never pure white
TEXT2 = "#A8A8A8"       # secondary readable text
MUTED = "#6B6B6B"       # labels, captions, de-emphasized
FAINT = "#4A4A4A"       # disabled, ghosted

# --- Semantic / meaning colors ---
RECOVERY = "#00C853"
RECOVERY_START = "#00C853"   # recovery-ring gradient (green, mirrors STRAIN_*)
RECOVERY_END = "#69F0AE"
STRAIN_START = "#FF4500"
STRAIN_END = "#FF6B00"
STRAIN = "#FF5A1F"      # strain solid midpoint — the primary accent
SLEEP = "#1E88E5"
WARNING = "#FFD600"
DANGER = "#FF3B30"

ACCENT = STRAIN         # primary accent is the strain orange

# Single grotesk for everything; heavy weights carry the stat numbers.
FONT_FAMILY = "'Hanken Grotesk', -apple-system, 'Segoe UI', Roboto, sans-serif"

# Weekly training-load goal (minutes) — drives the dashboard ScoreRing gauge.
WEEKLY_GOAL_MIN = 300

# Per-discipline visual identity. The handoff groups everything into four
# semantic accents (striking / grappling / conditioning / strength); the app
# has seven disciplines it must keep DISTINGUISHABLE in the charts, so each
# gets its own hue pulled toward the handoff palette's temperature. Blues =
# grappling, warm = striking, green = conditioning, yellow = strength.
DISCIPLINE_COLORS: dict[str, str] = {
    "bjj":        "#1E88E5",  # grappling blue
    "wrestling":  "#42A5F5",  # grappling, lighter blue
    "mma":        "#FF5A1F",  # striking orange (the strain accent)
    "boxing":     "#FF8A3D",  # striking, amber-orange
    "kickboxing": "#E64980",  # striking, controlled magenta (chart legibility)
    "cardio":     "#00C853",  # conditioning green
    "weights":    "#FFD600",  # strength yellow
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

# Active-recovery activities offered on the Recovery tab. Sleep is its own
# field, not an activity. These are logged for the record only — they do
# NOT feed the recovery score (which is sleep vs training load).
RECOVERY_ACTIVITIES = ["sauna", "massage", "ice_bath", "stretching"]
RECOVERY_ACTIVITY_ICONS: dict[str, str] = {
    "sauna":      "hot_tub",
    "massage":    "spa",
    "ice_bath":   "ac_unit",
    "stretching": "self_improvement",
}
RECOVERY_ACTIVITY_LABELS: dict[str, str] = {
    "sauna":      "Sauna",
    "massage":    "Massage",
    "ice_bath":   "Ice bath",
    "stretching": "Stretching",
}

# The gauge-arc brand mark from the handoff (assets/logo-mark.svg), inlined so
# we never depend on a static-file route. Used in the header and on login.
LOGO_MARK_SVG = """
<svg width="{size}" height="{size}" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="strainMark{uid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#FF4500"></stop>
      <stop offset="100%" stop-color="#FF6B00"></stop>
    </linearGradient>
  </defs>
  <g transform="translate(32,32)">
    <circle r="24" fill="none" stroke="#2A2A2A" stroke-width="8" stroke-dasharray="113 150.8" stroke-linecap="round" transform="rotate(135)"></circle>
    <circle r="24" fill="none" stroke="url(#strainMark{uid})" stroke-width="8" stroke-dasharray="88 150.8" stroke-linecap="round" transform="rotate(135)"></circle>
  </g>
</svg>
"""


def _logo_mark(size: int = 30, uid: str = "h") -> None:
    ui.html(LOGO_MARK_SVG.format(size=size, uid=uid))


def _score_ring_html(
    value, max_value, *, size: int = 180, label: str | None = None,
    unit: str | None = None, sublabel: str | None = None,
    stops=(STRAIN_START, STRAIN_END), gid: str = "ring",
    value_color: str = TEXT,
) -> str:
    """Render the signature Strain gauge as an inline SVG string.

    A 270° arc (90° gap centered at the bottom) over a dark track, gradient
    fill encoding the value, with the big number sitting in the center. Ported
    faithfully from the handoff's ScoreRing.jsx. Returned as HTML so it can be
    dropped in via ui.html().
    """
    stroke = round(size * 0.085)
    r = (size - stroke) / 2
    cx = cy = size / 2
    circ = 2 * math.pi * r
    gap = 90
    arc_len = circ * (360 - gap) / 360
    pct = max(0.0, min(1.0, (value / max_value) if max_value else 0.0))
    filled = arc_len * pct
    rotation = 90 + gap / 2  # gap centered at the bottom

    n = len(stops)
    stops_svg = "".join(
        f'<stop offset="{(i / (n - 1) * 100) if n > 1 else 0:.0f}%" stop-color="{c}"/>'
        for i, c in enumerate(stops)
    )
    unit_html = (
        f'<span style="font-size:{size * 0.13:.0f}px;font-weight:700;margin-left:1px">{unit}</span>'
        if unit else ""
    )
    label_html = (
        f'<div style="font-size:{max(10, size * 0.055):.0f}px;font-weight:600;'
        f'letter-spacing:0.12em;text-transform:uppercase;color:{MUTED}">{label}</div>'
        if label else ""
    )
    sub_html = (
        f'<div style="font-size:{max(11, size * 0.06):.0f}px;color:{TEXT2};margin-top:2px">{sublabel}</div>'
        if sublabel else ""
    )
    return f"""
    <div style="position:relative;width:{size}px;height:{size}px;display:inline-block">
      <svg width="{size}" height="{size}" style="display:block;transform:rotate({rotation:.0f}deg)">
        <defs>
          <linearGradient id="{gid}" x1="0%" y1="0%" x2="100%" y2="100%">{stops_svg}</linearGradient>
        </defs>
        <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{TRACK}"
          stroke-width="{stroke}" stroke-linecap="round"
          stroke-dasharray="{arc_len:.2f} {circ:.2f}"/>
        <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="url(#{gid})"
          stroke-width="{stroke}" stroke-linecap="round"
          stroke-dasharray="{filled:.2f} {circ:.2f}"/>
      </svg>
      <div style="position:absolute;inset:0;display:flex;flex-direction:column;
        align-items:center;justify-content:center;text-align:center;gap:2px;padding:{stroke}px">
        <div style="font-family:{FONT_FAMILY};font-weight:800;font-size:{size * 0.3:.0f}px;
          line-height:1;letter-spacing:-0.02em;color:{value_color};font-variant-numeric:tabular-nums">
          {value}{unit_html}
        </div>
        {label_html}{sub_html}
      </div>
    </div>
    """


def _apply_theme() -> None:
    """Shared theming: Hanken Grotesk, design tokens, base utility classes,
    and the Quasar overrides that make NiceGUI's cards/inputs/buttons match
    the Strain handoff (shade-only surfaces, no shadows, tight radii)."""
    ui.add_head_html(
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&display=swap" rel="stylesheet">'
    )
    ui.colors(primary=ACCENT, dark=BG)
    ui.query("body").style(
        f"background-color: {BG}; color: {TEXT}; font-family: {FONT_FAMILY};"
    )
    ui.add_head_html(f"""
    <style>
      :root {{
        --bg: {BG}; --surface: {SURFACE}; --elevated: {ELEVATED};
        --track: {TRACK}; --hairline: {HAIRLINE};
        --text-primary: {TEXT}; --text-secondary: {TEXT2};
        --text-muted: {MUTED}; --text-faint: {FAINT};
        --strain: {STRAIN}; --recovery: {RECOVERY}; --accent: {ACCENT};
      }}
      html, body {{
        background: {BG}; color: {TEXT};
        font-family: {FONT_FAMILY};
        font-feature-settings: 'tnum' 1;   /* tabular figures for stats */
        -webkit-font-smoothing: antialiased;
      }}
      ::selection {{ background: rgba(255,90,31,0.30); color: {TEXT}; }}

      /* --- Type primitives (mirror the handoff base.css) --- */
      .s-label {{
        font-size: 11px; font-weight: 600; letter-spacing: 0.12em;
        text-transform: uppercase; color: {MUTED}; line-height: 1.3;
      }}
      .s-stat {{
        font-weight: 800; letter-spacing: -0.02em; line-height: 1.0;
        color: {TEXT}; font-variant-numeric: tabular-nums;
      }}
      .s-section {{ font-size: 15px; font-weight: 600; color: {TEXT}; letter-spacing: -0.01em; }}

      /* --- Cards: shade-only, no border, no shadow, 12px radius --- */
      .q-card {{
        background: {SURFACE} !important; border-radius: 12px !important;
        box-shadow: none !important; border: none !important; color: {TEXT};
      }}
      .nicegui-card {{ box-shadow: none !important; gap: 0; }}

      /* --- Buttons: no shouty caps, tight radius, confident press --- */
      .q-btn {{ text-transform: none; border-radius: 10px; }}
      .q-btn:not(.q-btn--round) {{ font-weight: 600; letter-spacing: 0.01em; }}
      .q-btn .q-btn__content {{ text-transform: none; }}

      /* --- Inputs: embedded on the near-black bg, hairline edge, 10px --- */
      .q-field--outlined .q-field__control {{
        border-radius: 10px; background: {BG};
      }}
      .q-field--outlined .q-field__control:before {{ border-color: {HAIRLINE}; }}
      .q-field--outlined .q-field__control:hover:before {{ border-color: {MUTED}; }}
      .q-field__native, .q-field__input {{ color: {TEXT}; }}

      /* --- Streak pulse (the one bit of motion that celebrates) --- */
      @keyframes scoreflash {{
        0%   {{ transform: scale(1); }}
        30%  {{ transform: scale(1.12); filter: brightness(1.3); }}
        100% {{ transform: scale(1); }}
      }}
      .score-pulse {{ animation: scoreflash 0.7s ease-out; transform-origin: center; }}

      /* --- Header: frosted near-black --- */
      .app-header {{
        background: rgba(13,13,13,0.82); backdrop-filter: blur(16px);
        border-bottom: 1px solid {HAIRLINE};
      }}

      /* --- Tabs (top, desktop): muted -> bright on active, no color --- */
      .q-tab {{ color: {MUTED} !important; text-transform: none; }}
      .q-tab--active {{ color: {TEXT} !important; }}
      .q-tab__indicator {{ background: {ACCENT} !important; }}

      /* --- Table-style rows: barely-perceptible hover lift --- */
      .s-row {{ border-radius: 10px; transition: background 0.18s ease; }}
      .s-row:hover {{ background: {ELEVATED} !important; }}

      .empty-state {{ text-align: center; padding: 3rem 1rem; color: {MUTED}; }}

      /* --- Icon tiles (elevated rounded square, discipline-colored glyph) --- */
      .icon-tile {{ border-radius: 10px; background: {ELEVATED};
        display: flex; align-items: center; justify-content: center; }}
      .avatar-pill {{ border-radius: 999px; display: flex; align-items: center; justify-content: center; }}

      /* --- KPI strip: 2-up phones, 4-up desktop --- */
      .kpi-grid {{ display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0,1fr)); }}
      @media (min-width: 768px) {{ .kpi-grid {{ grid-template-columns: repeat(4, minmax(0,1fr)); }} }}

      /* --- Hero row: stack on phones, 1.4fr/1fr on desktop --- */
      .hero-grid {{ display: grid; gap: 16px; grid-template-columns: 1fr; }}
      @media (min-width: 900px) {{ .hero-grid {{ grid-template-columns: 1.4fr 1fr; }} }}

      /* --- Discipline picker grid --- */
      .disc-grid {{ display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0,1fr)); }}
      @media (min-width: 640px) {{ .disc-grid {{ grid-template-columns: repeat(3, minmax(0,1fr)); }} }}
      @media (min-width: 1024px) {{ .disc-grid {{ grid-template-columns: repeat(4, minmax(0,1fr)); }} }}
      .disc-tile {{ transition: background 0.14s ease, outline-color 0.14s ease;
        outline: 1.5px solid transparent; outline-offset: -2px; cursor: pointer; }}
      .disc-tile:hover {{ background: {ELEVATED} !important; outline-color: var(--disc, transparent); }}
      .disc-tile:active {{ transform: scale(0.98); }}

      /* --- Bottom tab bar: thumb nav on phones, hidden on desktop --- */
      .bottom-nav {{
        position: fixed; bottom: 0; left: 0; right: 0; z-index: 1000;
        display: flex; justify-content: space-around; align-items: stretch;
        background: {SURFACE}; border-top: 1px solid {HAIRLINE};
        padding: 8px 6px calc(10px + env(safe-area-inset-bottom));
      }}
      @media (max-width: 767px) {{
        .top-tabs {{ display: none !important; }}
        .page-content {{ padding-bottom: 92px; }}
      }}
      @media (min-width: 768px) {{ .bottom-nav {{ display: none !important; }} }}
    </style>
    """)


# ---------------- Small UI helpers ----------------

def _disc_icon_tile(disc: str, *, tile: int = 38, glyph: float = 1.2) -> None:
    """The elevated rounded square with a discipline-colored Lucide-ish glyph."""
    color = DISCIPLINE_COLORS.get(disc, ACCENT)
    with ui.element("div").classes("icon-tile").style(f"width:{tile}px;height:{tile}px;"):
        ui.icon(DISCIPLINE_ICONS.get(disc, "circle")).style(
            f"color: {color}; font-size: {glyph}rem;"
        )


def _kpi_card(label: str, value, *, unit: str = "", color: str = TEXT, pulse: bool = False) -> None:
    """A KPI cell: tiny uppercase label above one big heavy number."""
    with ui.card().classes("p-4 gap-1").style(f"background-color: {SURFACE}; min-width: 0;"):
        ui.label(label).classes("s-label")
        with ui.row().classes("items-baseline gap-1 mt-1").style("flex-wrap: nowrap;"):
            num = ui.label(str(value)).classes("s-stat text-4xl").style(f"color: {color};")
            if pulse:
                num.classes("score-pulse")
            if unit:
                ui.label(unit).style(f"color: {TEXT2}; font-size: 0.95rem; font-weight: 700;")


def _metric_row(icon: str, label: str, value, *, unit: str = "", accent: str = TEXT, divider: bool = True) -> None:
    """Secondary stat row beneath the dominant number — never competes with it."""
    border = f"border-top: 1px solid {HAIRLINE};" if divider else ""
    with ui.row().classes("items-center gap-3 w-full").style(f"padding: 10px 0; {border}"):
        with ui.element("div").classes("icon-tile").style("width:28px;height:28px;"):
            ui.icon(icon).style(f"color: {accent}; font-size: 0.95rem;")
        ui.label(label).classes("s-label flex-grow")
        with ui.row().classes("items-baseline gap-1"):
            ui.label(str(value)).classes("s-stat").style(
                f"color: {accent}; font-size: 1.1rem; font-weight: 700;"
            )
            if unit:
                ui.label(unit).style(f"color: {MUTED}; font-size: 0.75rem; font-weight: 600;")


def _recovery_band(score):
    """Map a recovery score (0–100, or None) to its traffic-light identity:
    (gradient_stops, solid_color, sublabel). Red = bad, yellow = mid, green
    = good — so the Home ring reads at a glance."""
    if score is None:
        return (TRACK, TRACK), MUTED, "Log last night's sleep"
    if score >= 67:
        return (RECOVERY_START, RECOVERY_END), RECOVERY, "Well recovered"
    if score >= 34:
        return ("#FFB300", WARNING), WARNING, "Moderately recovered"
    return (DANGER, "#FF6B5B"), DANGER, "Low — prioritise rest"


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


def _new_recovery_activity_row(container, activities, activity_type="sauna", minutes=15):
    state = {"activity_type": activity_type, "minutes": minutes}
    activities.append(state)
    with container:
        with ui.row().classes("w-full items-center gap-2") as row:
            icon = ui.icon(RECOVERY_ACTIVITY_ICONS.get(activity_type, "spa")) \
                .style(f"color: {RECOVERY}; font-size: 1.1rem;")
            # Keep the leading icon in sync if the user switches the type.
            ui.select(RECOVERY_ACTIVITY_LABELS, value=activity_type) \
                .props("dark outlined dense").classes("flex-grow") \
                .bind_value(state, "activity_type") \
                .on_value_change(lambda e: icon.set_name(
                    RECOVERY_ACTIVITY_ICONS.get(e.value, "spa")))
            ui.number("min", value=minutes, min=0) \
                .props("dark outlined dense").classes("w-24") \
                .bind_value(state, "minutes")

            def remove() -> None:
                if state in activities:
                    activities.remove(state)
                row.delete()

            ui.button(icon="close", on_click=remove) \
                .props("flat dense round size=sm").style(f"color: {MUTED}") \
                .tooltip("Remove")


# ---------------- Pages ----------------

@ui.page("/login")
def login_page() -> None:
    """Sign-in page. Popup Google OAuth via Firebase, verify token
    server-side, store uid in session, redirect to /."""
    _inject_firebase_sdk()
    _apply_theme()

    with ui.column().classes("items-center justify-center w-full min-h-screen gap-5 p-6"):
        _logo_mark(72, uid="login")
        with ui.column().classes("items-center gap-1"):
            ui.label("STRAIN").classes("s-stat text-5xl") \
                .style(f"color: {TEXT}; letter-spacing: 0.04em;")
            ui.label("Training & fitness performance tracker").classes("s-label") \
                .style("letter-spacing: 0.16em;")
        # Pure client-side click handler via NiceGUI's js_handler. We CAN'T
        # use ui.button(on_click=...) here because Firebase Hosting breaks
        # the NiceGUI WebSocket; server-side click handlers silently no-op
        # on iPhone when the app is reached via firebaseapp.com.
        ui.button("Sign in with Google", icon="login") \
            .props('size=lg no-caps unelevated') \
            .style(f"background-color: {ACCENT}; color: #FFFFFF; margin-top: 0.75rem; "
                   f"border-radius: 10px; padding: 0.7rem 1.8rem; font-weight: 600;") \
            .on('click', js_handler='() => window.handleSignInClick && window.handleSignInClick()')
        ui.label("Each athlete's sessions stay private to them.") \
            .classes("text-xs mt-3").style(f"color: {MUTED}")


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
    # Google OAuth access token for Calendar, captured at sign-in (Phase 1).
    # May be None (user declined the scope, or signed in before the feature
    # existed) or expired (~1h life) — the coach's calendar tools handle both.
    current_gcal_token: str | None = auth_session.get("gcal_token")

    _inject_firebase_sdk()
    _apply_theme()

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
    with ui.header().classes("app-header items-center px-4 py-2").props("elevated=false"):
        with ui.row().classes("items-center gap-2 w-full max-w-5xl mx-auto"):
            _logo_mark(26, uid="hdr")
            ui.label("STRAIN").classes("text-lg s-stat") \
                .style(f"color: {TEXT}; letter-spacing: 0.04em;")
            ui.space()
            with ui.element("div").classes("avatar-pill").style(
                f"width:34px;height:34px;background-color:{ELEVATED};"
            ):
                ui.label((current_user_name or "U")[0].upper()) \
                    .style(f"color: {TEXT}; font-weight: 700;")
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

    # Recovery form state (Recovery tab) — independent of session_state.
    recovery_state: dict = {
        "date": today.isoformat(),
        "sleep_hours": None,
        "notes": "",
    }
    recovery_activities: list[dict] = []

    # ---- Navigation: top tabs on desktop, bottom bar on phones ----
    with ui.tabs().classes("top-tabs w-full max-w-5xl mx-auto").props("no-caps") as tabs:
        tab_dash = ui.tab("Home", icon="home")
        tab_log = ui.tab("Log", icon="add_circle")
        tab_recovery = ui.tab("Recovery", icon="spa")
        tab_history = ui.tab("History", icon="history")
        tab_coach = ui.tab("Coach", icon="forum")

    # Bottom bar mirrors the tabs; CSS swaps which one is visible per screen
    # size. Re-rendered on every tab change so the active item highlights.
    current_tab = {"value": "Home"}

    @ui.refreshable
    def bottom_nav() -> None:
        with ui.element("div").classes("bottom-nav"):
            for name, icon, text in (
                ("Home", "home", "Home"),
                ("Log", "add_circle", "Log"),
                ("Recovery", "spa", "Recovery"),
                ("History", "history", "History"),
                ("Coach", "forum", "Coach"),
            ):
                active = current_tab["value"] == name
                color = TEXT if active else FAINT
                with ui.column().classes("items-center gap-1 cursor-pointer flex-grow py-1") \
                        .on("click", lambda name=name: tabs.set_value(name)):
                    ui.icon(icon).style(f"color: {color}; font-size: 1.5rem;")
                    ui.label(text).classes("text-[10px] font-semibold") \
                        .style(f"color: {color}; letter-spacing: 0.06em; text-transform: uppercase;")

    bottom_nav()

    def _tab_changed(e) -> None:
        current_tab["value"] = str(e.value)
        bottom_nav.refresh()

    tabs.on_value_change(_tab_changed)

    with ui.tab_panels(tabs, value=tab_dash).classes("w-full max-w-5xl mx-auto").style(f"background-color: {BG}"):

        # ============= HOME =============
        with ui.tab_panel(tab_dash).classes("p-0"):
            with ui.column().classes("page-content w-full gap-5 p-6"):
                first_name = (current_user_name or "there").split()[0]
                hour = datetime.now().hour
                greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
                with ui.column().classes("gap-1"):
                    ui.label(datetime.now().strftime("%A · %b %d").upper()).classes("s-label")
                    ui.label(f"{greeting}, {first_name}").classes("s-stat text-3xl").style(f"color: {TEXT}")

                # ---- KPI strip (4-up): quick-scan numbers ----
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
                    sessions_30d = len(month_sessions)
                    avg_per = round(total_min / sessions_30d) if sessions_30d else 0

                    # Recovery: sleep comes from recovery_logs, training load
                    # from the sessions above. None until a sleep log exists.
                    try:
                        recovery_logs = list_user_recovery(current_user_id, month_start, end)
                    except Exception:
                        log.exception("stats_panel.list_recovery uid=%s", current_user_id)
                        recovery_logs = []
                    day_recovery = recovery_score_on(end.date(), recovery_logs, month_sessions)
                    week_recovery = weekly_recovery_score(recovery_logs, month_sessions)
                    today_sleep = sum(
                        r.sleep_hours or 0 for r in recovery_logs
                        if r.logged_at.date() == end.date() and r.sleep_hours
                    )

                    with ui.element("div").classes("kpi-grid w-full"):
                        _kpi_card("This week", week_mat_min, unit="min", color=STRAIN)
                        _kpi_card("Streak", streak, unit="days", color=STRAIN, pulse=True)
                        _kpi_card("Sessions", sessions_30d, color=TEXT)
                        _kpi_card("Total · 30d", total_min, unit="min", color=TEXT)

                    # ---- Hero row: weekly-load ScoreRing + discipline donut ----
                    with ui.element("div").classes("hero-grid w-full"):
                        # Weekly training-load gauge (the signature element).
                        with ui.card().classes("p-5").style(f"background-color: {SURFACE}"):
                            ui.label("Weekly load").classes("s-section").style("margin-bottom: 14px;")
                            with ui.row().classes("items-center gap-6 w-full no-wrap"):
                                pct = round(min(1.0, week_mat_min / WEEKLY_GOAL_MIN) * 100)
                                sub = "Goal reached" if week_mat_min >= WEEKLY_GOAL_MIN else f"{pct}% of weekly goal"
                                ui.html(_score_ring_html(
                                    week_mat_min, WEEKLY_GOAL_MIN, size=172,
                                    label="min this week", sublabel=sub,
                                    stops=(STRAIN_START, STRAIN_END), gid="weekring",
                                    value_color=TEXT,
                                ))
                                with ui.column().classes("flex-grow gap-0"):
                                    _metric_row("local_fire_department", "Streak", streak,
                                                unit="days", accent=STRAIN, divider=False)
                                    _metric_row("event", "This week", len(week_sessions), unit="sessions")
                                    _metric_row("category", "Disciplines · 30d", discipline_count)
                                    _metric_row("timer", "Avg / session", avg_per, unit="min")

                        # Discipline split donut.
                        with ui.card().classes("p-5").style(f"background-color: {SURFACE}"):
                            ui.label("Discipline split · 30d").classes("s-section").style("margin-bottom: 8px;")
                            totals = discipline_totals(
                                [s for s in month_sessions if s.started_at >= end - timedelta(days=30)]
                            )
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
                                        "borderColor": HAIRLINE,
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
                                        "radius": ["52%", "72%"],
                                        "center": ["65%", "50%"],
                                        "avoidLabelOverlap": True,
                                        "itemStyle": {"borderColor": SURFACE, "borderWidth": 3},
                                        "label": {"show": False},
                                        "labelLine": {"show": False},
                                        "data": pie_data,
                                    }],
                                }).classes("w-full").style("height: 240px;")

                    # ---- Recovery: traffic-light gauge balancing sleep vs training load ----
                    with ui.card().classes("w-full p-5").style(f"background-color: {SURFACE}"):
                        ui.label("Recovery").classes("s-section").style("margin-bottom: 14px;")
                        with ui.row().classes("items-center gap-6 w-full no-wrap"):
                            ring_stops, ring_color, sub = _recovery_band(day_recovery)
                            week_color = _recovery_band(week_recovery)[1]
                            ui.html(_score_ring_html(
                                day_recovery if day_recovery is not None else 0, 100, size=172,
                                label="recovery today", sublabel=sub,
                                stops=ring_stops, gid="recoveryring",
                                value_color=ring_color,
                            ))
                            with ui.column().classes("flex-grow gap-0"):
                                _metric_row(
                                    "favorite", "This week",
                                    week_recovery if week_recovery is not None else "—",
                                    unit="/100" if week_recovery is not None else "",
                                    accent=week_color, divider=False,
                                )
                                _metric_row("bedtime", "Sleep tonight",
                                            round(today_sleep, 1) if today_sleep else "—",
                                            unit="h" if today_sleep else "", accent=SLEEP)
                                _metric_row("fitness_center", "Load today",
                                            sum(total_minutes(s.data) for s in month_sessions
                                                if s.started_at.date() == end.date()),
                                            unit="min")

                stats_panel()

                # ---- Weekly trend (stacked bar by discipline) ----
                @ui.refreshable
                def charts_row() -> None:
                    end = datetime.now()
                    try:
                        month_sessions = list_user_sessions(current_user_id, end - timedelta(days=60), end)
                    except Exception:
                        log.exception("charts_row.list_sessions uid=%s", current_user_id)
                        return

                    weekly = weekly_discipline_minutes(month_sessions, n_weeks=8)

                    with ui.card().classes("w-full p-5").style(f"background-color: {SURFACE}"):
                        ui.label("Last 8 weeks · minutes by discipline").classes("s-section") \
                            .style("margin-bottom: 8px;")
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
                                    "borderColor": HAIRLINE,
                                    "textStyle": {"color": TEXT},
                                },
                                "legend": {"textStyle": {"color": MUTED}, "top": 0},
                                "grid": {"left": 40, "right": 16, "top": 36, "bottom": 24},
                                "xAxis": {
                                    "type": "category",
                                    "data": weekly["weeks"],
                                    "axisLabel": {"color": MUTED, "fontSize": 10},
                                    "axisLine": {"lineStyle": {"color": HAIRLINE}},
                                },
                                "yAxis": {
                                    "type": "value",
                                    "axisLabel": {"color": MUTED, "fontSize": 10},
                                    "splitLine": {"lineStyle": {"color": TRACK}},
                                },
                                "series": bar_series,
                            }).classes("w-full").style("height: 280px;")

                charts_row()

                # ---- Recent sessions (table-style rows) ----
                with ui.card().classes("w-full p-5").style(f"background-color: {SURFACE}"):
                    ui.label("Recent sessions").classes("s-section").style("margin-bottom: 6px;")

                    @ui.refreshable
                    def recent_snapshot() -> None:
                        end = datetime.now()
                        try:
                            sessions = list_user_sessions(current_user_id, end - timedelta(days=30), end)
                        except Exception:
                            log.exception("recent_snapshot.list_sessions uid=%s", current_user_id)
                            ui.label("Couldn't load recent sessions — please refresh.").style(f"color: {MUTED}")
                            return
                        if not sessions:
                            with ui.column().classes("empty-state w-full items-center gap-2"):
                                ui.icon("history").style(f"color: {MUTED}; font-size: 2.5rem;")
                                ui.label("No sessions yet — log your first one to see it here.").classes("text-sm")
                            return
                        recent = list(reversed(sessions))[:5]
                        for i, s in enumerate(recent):
                            color = DISCIPLINE_COLORS.get(s.data.discipline, ACCENT)
                            border = f"border-top: 1px solid {HAIRLINE};" if i else ""
                            with ui.row().classes("s-row w-full items-center gap-3 no-wrap") \
                                    .style(f"padding: 12px 8px; {border}"):
                                _disc_icon_tile(s.data.discipline)
                                with ui.column().classes("gap-0 flex-grow"):
                                    ui.label(DISCIPLINE_LABELS.get(s.data.discipline, s.data.discipline)) \
                                        .style(f"color: {TEXT}; font-weight: 600; font-size: 0.9rem;")
                                    ui.label(s.started_at.strftime("%a %b %d · %H:%M").upper()) \
                                        .classes("s-label").style("letter-spacing: 0.05em;")
                                with ui.row().classes("items-baseline gap-1"):
                                    ui.label(str(total_minutes(s.data))).classes("s-stat") \
                                        .style(f"color: {color}; font-size: 1.05rem; font-weight: 800;")
                                    ui.label("min").style(f"color: {MUTED}; font-size: 0.7rem; font-weight: 600;")

                    recent_snapshot()

        # ============= LOG WORKOUT =============
        with ui.tab_panel(tab_log).classes("p-0"):
            with ui.column().classes("page-content w-full gap-4 p-6"):
                # Quick-add stepper: step 1 is a grid of discipline tiles; step
                # 2 shows only that discipline's fields. `picked` drives which
                # step renders.
                picked: dict = {"value": None}

                @ui.refreshable
                def log_flow():
                    d = picked["value"]

                    # ---- Step 1: pick a workout ----
                    if d is None:
                        with ui.column().classes("gap-1"):
                            ui.label("Log a session").classes("s-stat text-3xl").style(f"color: {TEXT}")
                            ui.label("What did you train?").classes("s-label")
                        with ui.element("div").classes("disc-grid w-full"):
                            for disc in DISCIPLINES:
                                disc_color = DISCIPLINE_COLORS[disc]
                                with ui.card().classes("disc-tile items-center p-5 gap-3").style(
                                    f"background-color: {SURFACE}; --disc: {disc_color};"
                                ) as disc_tile:
                                    with ui.element("div").classes("icon-tile").style(
                                        "width:54px;height:54px;"
                                    ):
                                        ui.icon(DISCIPLINE_ICONS[disc]).style(
                                            f"color: {disc_color}; font-size: 1.7rem;"
                                        )
                                    ui.label(DISCIPLINE_LABELS[disc]).style(
                                        f"color: {TEXT}; font-weight: 600; font-size: 0.85rem;"
                                    )
                                disc_tile.on("click", lambda disc=disc: pick(disc))
                        return

                    # ---- Step 2: only this discipline's fields ----
                    color = DISCIPLINE_COLORS.get(d, ACCENT)
                    with ui.row().classes("items-center gap-3 w-full"):
                        ui.button(icon="arrow_back", on_click=go_back) \
                            .props("flat round dense").style(f"color: {MUTED}")
                        _disc_icon_tile(d, tile=42, glyph=1.3)
                        ui.label(DISCIPLINE_LABELS.get(d, d)).classes("s-stat text-2xl") \
                            .style(f"color: {TEXT}")

                    if editing_id["value"]:
                        with ui.row().classes("items-center gap-2 p-3 rounded") \
                                .style(f"background-color: {ELEVATED};"):
                            ui.icon("edit").style(f"color: {ACCENT}")
                            ui.label("Editing — Save to update, Cancel to discard").classes("text-sm") \
                                .style(f"color: {ACCENT}")
                            ui.button("Cancel", on_click=lambda: reset_form()) \
                                .props("flat dense").style(f"color: {MUTED}")

                    with ui.card().classes("w-full gap-4 p-5").style(f"background-color: {SURFACE}"):
                        with ui.row().classes("w-full gap-4"):
                            ui.input("Date", value=session_state["date"]) \
                                .props("dark outlined dense type=date").classes("flex-1") \
                                .bind_value(session_state, "date")
                            ui.input("Time", value=session_state["time"]) \
                                .props("dark outlined dense type=time").classes("flex-1") \
                                .bind_value(session_state, "time")

                        if d in ("bjj", "wrestling"):
                            with ui.row().classes("w-full gap-4"):
                                ui.number("Drilling (min)", value=session_state["drilling_minutes"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "drilling_minutes")
                                ui.number("Sparring rounds", value=session_state["sparring_rounds"], min=0) \
                                    .props("dark outlined dense").bind_value(session_state, "sparring_rounds")
                                ui.number("Round length (min)", value=session_state["round_length_minutes"], min=1) \
                                    .props("dark outlined dense").bind_value(session_state, "round_length_minutes")
                            ui.label("Log entries").classes("s-label mt-2")
                            nonlocal_col = ui.column().classes("w-full gap-2")
                            entries.clear()
                            _new_entry_row(nonlocal_col, entries)
                            log_flow._entries_col = nonlocal_col

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
                            ui.label("Log entries").classes("s-label mt-2")
                            nonlocal_col = ui.column().classes("w-full gap-2")
                            entries.clear()
                            _new_entry_row(nonlocal_col, entries)
                            log_flow._entries_col = nonlocal_col

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
                            ui.label("Log entries").classes("s-label mt-2")
                            nonlocal_col = ui.column().classes("w-full gap-2")
                            entries.clear()
                            _new_entry_row(nonlocal_col, entries)
                            log_flow._entries_col = nonlocal_col

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
                            ui.label("Exercises").classes("s-label mt-2")
                            ex_col = ui.column().classes("w-full gap-2")
                            exercises.clear()
                            _new_exercise_row(ex_col, exercises)
                            log_flow._ex_col = ex_col

                        ui.input("Notes (optional)") \
                            .props("dark outlined dense").classes("w-full") \
                            .bind_value(session_state, "notes")

                        if d in ("bjj", "wrestling", "mma", "boxing", "kickboxing", "weights"):
                            ui.button("Add row", on_click=lambda: add_row(), icon="add") \
                                .props("flat dense no-caps").style(f"color: {ACCENT}")

                        ui.button("Save session", on_click=on_save, icon="check") \
                            .props("size=lg no-caps unelevated").classes("w-full mt-2") \
                            .style(f"background-color: {color}; color: #FFFFFF; font-weight: 700;")

                log_flow()

                def pick(d: str) -> None:
                    session_state["discipline"] = d
                    picked["value"] = d
                    log_flow.refresh()

                def go_back() -> None:
                    picked["value"] = None
                    log_flow.refresh()

                def add_row() -> None:
                    d = picked["value"]
                    if d in ("bjj", "wrestling", "mma", "boxing", "kickboxing") and hasattr(log_flow, "_entries_col"):
                        _new_entry_row(log_flow._entries_col, entries)
                    elif d == "weights" and hasattr(log_flow, "_ex_col"):
                        _new_exercise_row(log_flow._ex_col, exercises)

                async def _safe_extract_tags(notes: str):
                    """AI tagging is best-effort. If Claude or the network fails,
                    log it and save the session WITHOUT tags rather than losing
                    the whole entry — a hiccup in tagging shouldn't cost the log."""
                    try:
                        return await asyncio.to_thread(extract_tags, notes)
                    except Exception:
                        log.exception(
                            "extract_tags failed uid=%s — saving session without tags",
                            current_user_id,
                        )
                        ui.notify("Couldn't auto-tag techniques — saved without them.",
                                  color="warning")
                        return []

                async def on_save() -> None:
                    d = picked["value"]
                    if not d:
                        return
                    ui.notify("Saving…")
                    if d in ("bjj", "wrestling"):
                        log_entries = []
                        for e in entries:
                            notes = (e["notes"] or "").strip()
                            if not notes:
                                continue
                            tags = await _safe_extract_tags(notes)
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
                            tags = await _safe_extract_tags(notes)
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
                            tags = await _safe_extract_tags(notes)
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

                    t0 = time.perf_counter()
                    try:
                        started = datetime.fromisoformat(f"{session_state['date']}T{session_state['time']}:00")
                        session = Session(
                            id=editing_id["value"],
                            user_id=current_user_id,
                            started_at=started,
                            notes=session_state["notes"] or None,
                            data=data,
                        )
                        saved = save_user_session(current_user_id, session)
                    except SessionAccessDenied:
                        ui.notify("Cannot save — session belongs to another user", color="negative")
                        return
                    except Exception:
                        log.exception(
                            "session.save failed uid=%s discipline=%s", current_user_id, d,
                        )
                        ui.notify("Couldn't save your session — please try again.",
                                  color="negative")
                        return
                    log.info(
                        "session.save uid=%s id=%s discipline=%s ms=%.0f",
                        current_user_id, saved.id, d, (time.perf_counter() - t0) * 1000,
                    )
                    # Analytics: the core engagement event. is_edit distinguishes a
                    # brand-new log from editing an existing one (only new logs count
                    # toward "did the user train").
                    analytics.capture("session_logged", current_user_id, {
                        "discipline": d,
                        "session_id": saved.id,
                        "is_edit": editing_id["value"] is not None,
                    })
                    ui.notify("Saved", color="positive")
                    editing_id["value"] = None
                    reset_form()
                    stats_panel.refresh()
                    charts_row.refresh()
                    recent_snapshot.refresh()
                    history_container.refresh()
                    # Land back on Home so the updated stats greet the user.
                    tabs.set_value("Home")

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
                    picked["value"] = None
                    log_flow.refresh()

        # ============= RECOVERY =============
        with ui.tab_panel(tab_recovery).classes("p-0"):
            with ui.column().classes("page-content w-full gap-4 p-6"):

                def add_recovery_activity(act: str) -> None:
                    if hasattr(recovery_form, "_act_col"):
                        _new_recovery_activity_row(recovery_form._act_col, recovery_activities, act)

                @ui.refreshable
                def recovery_form() -> None:
                    with ui.column().classes("gap-1"):
                        ui.label("Log recovery").classes("s-stat text-3xl").style(f"color: {TEXT}")
                        ui.label("How did you recover?").classes("s-label")

                    with ui.card().classes("w-full gap-4 p-5").style(f"background-color: {SURFACE}"):
                        ui.input("Date", value=recovery_state["date"]) \
                            .props("dark outlined dense type=date").classes("flex-1") \
                            .bind_value(recovery_state, "date")

                        # Sleep — the one input that drives the recovery score.
                        ui.label("Sleep").classes("s-label mt-1")
                        ui.number("Hours in bed", value=recovery_state["sleep_hours"],
                                  min=0, max=24, step=0.5) \
                            .props("dark outlined dense").classes("w-full") \
                            .bind_value(recovery_state, "sleep_hours")

                        # Active recovery — tap a tile to add a row with minutes.
                        ui.label("Recovery activities").classes("s-label mt-2")
                        with ui.element("div").classes("disc-grid w-full"):
                            for act in RECOVERY_ACTIVITIES:
                                with ui.card().classes("disc-tile items-center p-4 gap-2").style(
                                    f"background-color: {ELEVATED}; --disc: {RECOVERY};"
                                ) as act_tile:
                                    with ui.element("div").classes("icon-tile").style("width:46px;height:46px;"):
                                        ui.icon(RECOVERY_ACTIVITY_ICONS[act]).style(
                                            f"color: {RECOVERY}; font-size: 1.5rem;"
                                        )
                                    ui.label(RECOVERY_ACTIVITY_LABELS[act]).style(
                                        f"color: {TEXT}; font-weight: 600; font-size: 0.8rem;"
                                    )
                                act_tile.on("click", lambda act=act: add_recovery_activity(act))

                        act_col = ui.column().classes("w-full gap-2")
                        recovery_activities.clear()
                        recovery_form._act_col = act_col

                        ui.input("Notes (optional)") \
                            .props("dark outlined dense").classes("w-full") \
                            .bind_value(recovery_state, "notes")

                        ui.button("Save recovery", on_click=on_save_recovery, icon="check") \
                            .props("size=lg no-caps unelevated").classes("w-full mt-2") \
                            .style(f"background-color: {RECOVERY}; color: #0D0D0D; font-weight: 700;")

                async def on_save_recovery() -> None:
                    sleep_raw = recovery_state["sleep_hours"]
                    sleep = float(sleep_raw) if sleep_raw not in (None, "") else None
                    acts = [
                        RecoveryActivity(activity_type=a["activity_type"], minutes=int(a["minutes"] or 0))
                        for a in recovery_activities
                    ]
                    if sleep is None and not acts:
                        ui.notify("Add sleep hours or a recovery activity first", color="warning")
                        return
                    # Recovery is tracked per-day; stamp at noon on the chosen date.
                    logged = datetime.fromisoformat(f"{recovery_state['date']}T12:00:00")
                    rec = RecoveryLog(
                        user_id=current_user_id,
                        logged_at=logged,
                        sleep_hours=sleep,
                        activities=acts,
                        notes=recovery_state["notes"] or None,
                    )
                    try:
                        save_user_recovery(current_user_id, rec)
                    except RecoveryAccessDenied:
                        ui.notify("Cannot save — recovery belongs to another user", color="negative")
                        return
                    except Exception:
                        log.exception("recovery.save failed uid=%s", current_user_id)
                        ui.notify("Couldn't save your recovery log — please try again.",
                                  color="negative")
                        return
                    # Analytics: the secondary habit — do people track recovery too?
                    analytics.capture("recovery_logged", current_user_id, {
                        "sleep_hours": sleep,
                        "activity_count": len(acts),
                    })
                    ui.notify("Saved", color="positive")
                    reset_recovery_form()
                    stats_panel.refresh()
                    recovery_recent.refresh()
                    # Land back on Home so the updated recovery score greets the user.
                    tabs.set_value("Home")

                def reset_recovery_form() -> None:
                    recovery_state["date"] = date.today().isoformat()
                    recovery_state["sleep_hours"] = None
                    recovery_state["notes"] = ""
                    recovery_form.refresh()

                def on_delete_recovery(recovery_id: str):
                    with ui.dialog() as dialog, ui.card().style(f"background-color: {SURFACE}; color: {TEXT}"):
                        ui.label("Delete this recovery log?").classes("text-lg")
                        ui.label("This cannot be undone.").style(f"color: {MUTED}").classes("text-sm")
                        def confirm():
                            try:
                                delete_user_recovery(current_user_id, recovery_id)
                            except (RecoveryNotFound, RecoveryAccessDenied):
                                dialog.close()
                                ui.notify("Could not delete recovery log", color="negative")
                                return
                            dialog.close()
                            ui.notify("Deleted", color="warning")
                            stats_panel.refresh()
                            recovery_recent.refresh()
                        with ui.row().classes("justify-end gap-2 w-full"):
                            ui.button("Cancel", on_click=dialog.close).props("flat")
                            ui.button("Delete", on_click=confirm).props("color=negative unelevated")
                    dialog.open()

                @ui.refreshable
                def recovery_recent() -> None:
                    end = datetime.now()
                    try:
                        logs = list_user_recovery(current_user_id, end - timedelta(days=30), end)
                    except Exception:
                        log.exception("recovery_recent.list_recovery uid=%s", current_user_id)
                        ui.label("Couldn't load recovery — please refresh.").style(f"color: {MUTED}")
                        return
                    if not logs:
                        with ui.column().classes("empty-state w-full items-center gap-2"):
                            ui.icon("spa").style(f"color: {MUTED}; font-size: 2.5rem;")
                            ui.label("No recovery logged yet.").classes("text-sm")
                        return
                    for s in reversed(logs):
                        with ui.card().classes("w-full p-4 gap-2").style(f"background-color: {SURFACE}"):
                            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                                with ui.element("div").classes("icon-tile").style("width:42px;height:42px;"):
                                    ui.icon("bedtime").style(f"color: {RECOVERY}; font-size: 1.3rem;")
                                with ui.column().classes("gap-0 flex-grow"):
                                    ui.label(s.logged_at.strftime("%a %b %d").upper()) \
                                        .classes("s-label").style("letter-spacing: 0.05em;")
                                    bits = []
                                    if s.sleep_hours:
                                        bits.append(f"{round(s.sleep_hours, 1)}h sleep")
                                    for a in s.activities:
                                        lbl = RECOVERY_ACTIVITY_LABELS.get(a.activity_type, a.activity_type)
                                        bits.append(f"{lbl} {a.minutes}min" if a.minutes else lbl)
                                    ui.label(" · ".join(bits) or "—").classes("text-sm").style(f"color: {TEXT2}")
                                if s.sleep_hours is not None:
                                    with ui.row().classes("items-baseline gap-1"):
                                        ui.label(str(round(s.sleep_hours, 1))).classes("s-stat") \
                                            .style(f"color: {SLEEP}; font-size: 1.2rem; font-weight: 800;")
                                        ui.label("h").style(f"color: {MUTED}; font-size: 0.7rem; font-weight: 600;")
                                ui.button(icon="delete_outline", on_click=lambda rid=s.id: on_delete_recovery(rid)) \
                                    .props("flat dense round size=sm").style(f"color: {MUTED}")
                            if s.notes:
                                ui.label(f"note: {s.notes}").classes("text-xs italic") \
                                    .style(f"color: {MUTED}")

                # Render in visual order: form, then the recent-recovery list.
                recovery_form()
                ui.label("Recent recovery").classes("s-label mt-4")
                recovery_recent()

        # ============= HISTORY =============
        with ui.tab_panel(tab_history).classes("p-0"):
            with ui.column().classes("page-content w-full gap-4 p-6"):
                ui.label("History").classes("s-stat text-2xl").style(f"color: {TEXT}")
                ui.label("Last 30 days").classes("s-label")

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
                            ui.button("Delete", on_click=confirm).props("color=negative unelevated")
                    dialog.open()

                @ui.refreshable
                def history_container() -> None:
                    end = datetime.now()
                    start = end - timedelta(days=30)
                    try:
                        sessions = list_user_sessions(current_user_id, start, end)
                    except Exception:
                        log.exception("history_container.list_sessions uid=%s", current_user_id)
                        ui.label("Couldn't load history — please refresh.").style(f"color: {MUTED}")
                        return
                    if not sessions:
                        with ui.column().classes("empty-state w-full items-center gap-2"):
                            ui.icon("inbox").style(f"color: {MUTED}; font-size: 3rem;")
                            ui.label("No sessions in the last 30 days.").classes("text-sm")
                            ui.label("Head to the Log tab to add one.").classes("text-xs")
                        return

                    for s in reversed(sessions):
                        color = DISCIPLINE_COLORS.get(s.data.discipline, ACCENT)
                        label = DISCIPLINE_LABELS.get(s.data.discipline, s.data.discipline)

                        with ui.card().classes("session-card w-full p-4 gap-3").style(
                            f"background-color: {SURFACE};"
                        ):
                            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                                _disc_icon_tile(s.data.discipline, tile=42, glyph=1.3)
                                with ui.column().classes("gap-0 flex-grow"):
                                    ui.label(label).style(f"color: {TEXT}; font-weight: 600;")
                                    ui.label(s.started_at.strftime("%a %b %d · %H:%M").upper()) \
                                        .classes("s-label").style("letter-spacing: 0.05em;")
                                with ui.row().classes("items-baseline gap-1"):
                                    ui.label(str(total_minutes(s.data))).classes("s-stat") \
                                        .style(f"color: {color}; font-size: 1.3rem; font-weight: 800;")
                                    ui.label("min").style(f"color: {MUTED}; font-size: 0.7rem; font-weight: 600;")
                                ui.button(icon="delete_outline", on_click=lambda sid=s.id: on_delete(sid)) \
                                    .props("flat dense round size=sm").style(f"color: {MUTED}")

                            # Discipline-specific summary
                            if isinstance(s.data, GrapplingData):
                                ui.label(
                                    f"drill {s.data.drilling_minutes}min · "
                                    f"{s.data.sparring_rounds}×{s.data.round_length_minutes}min rolls"
                                ).classes("text-sm").style(f"color: {TEXT2}")
                                for e in s.data.log_entries:
                                    ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1") \
                                        .style(f"color: {TEXT}")
                                    with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                        for t in e.tags:
                                            _tag_pill(f"{t.technique} · {t.position}", color)

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
                                ui.label(" · ".join(parts)).classes("text-sm").style(f"color: {TEXT2}")
                                for e in s.data.log_entries:
                                    ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1") \
                                        .style(f"color: {TEXT}")
                                    with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                        for t in e.tags:
                                            _tag_pill(f"{t.technique} · {t.position}", color)

                            elif isinstance(s.data, StrikingData):
                                ui.label(
                                    f"bag {s.data.bag_minutes}min · pads {s.data.pad_minutes}min · "
                                    f"{s.data.sparring_rounds}×{s.data.round_length_minutes}min sparring"
                                ).classes("text-sm").style(f"color: {TEXT2}")
                                for e in s.data.log_entries:
                                    ui.label(f"[{e.category}] {e.notes_raw}").classes("text-sm mt-1") \
                                        .style(f"color: {TEXT}")
                                    with ui.row().classes("gap-1 ml-4 flex-wrap"):
                                        for t in e.tags:
                                            _tag_pill(f"{t.technique} · {t.position}", color)

                            elif isinstance(s.data, CardioData):
                                parts = [f"{s.data.activity_type}", f"{s.data.duration_minutes}min", s.data.intensity]
                                if s.data.distance_km:
                                    parts.append(f"{s.data.distance_km}km")
                                if s.data.heart_rate_avg:
                                    parts.append(f"{s.data.heart_rate_avg}bpm")
                                ui.label(" · ".join(parts)).classes("text-sm").style(f"color: {TEXT2}")

                            elif isinstance(s.data, WeightsData):
                                ui.label(f"{s.data.duration_minutes}min · {len(s.data.exercises)} exercises") \
                                    .classes("text-sm").style(f"color: {TEXT2}")
                                for ex in s.data.exercises:
                                    wt = f" @ {ex.weight_kg}kg" if ex.weight_kg else ""
                                    ui.label(f"  {ex.name} — {ex.sets}×{ex.reps}{wt}").classes("text-sm") \
                                        .style(f"color: {TEXT}")

                            if s.notes:
                                ui.label(f"note: {s.notes}").classes("text-xs italic mt-1") \
                                    .style(f"color: {MUTED}")

                history_container()

        # ============= COACH =============
        with ui.tab_panel(tab_coach).classes("p-0"):
            with ui.column().classes("page-content w-full gap-3 p-6"):
                with ui.column().classes("gap-1"):
                    ui.label("Coach").classes("s-stat text-3xl").style(f"color: {TEXT}")
                    ui.label("Ask about your training & recovery").classes("s-label")

                # Conversation memory across turns: Pydantic AI message objects
                # returned by coach_reply, fed back in as history next turn.
                chat_state: dict = {"messages": None, "busy": False}

                chat_scroll = ui.scroll_area().classes("w-full").style(
                    f"height: 58vh; background-color: {BG}; border-radius: 12px;"
                )
                with chat_scroll:
                    messages_col = ui.column().classes("w-full gap-3 p-1")

                def add_bubble(text: str, *, me: bool) -> None:
                    with messages_col:
                        with ui.row().classes("w-full " + ("justify-end" if me else "justify-start")):
                            with ui.element("div").style(
                                f"background:{ACCENT if me else SURFACE}; "
                                f"border-radius:14px; padding:10px 14px; max-width:82%;"
                            ):
                                if me:
                                    ui.label(text).style(
                                        "color:#FFFFFF; white-space:pre-wrap; line-height:1.45;"
                                    )
                                else:
                                    ui.markdown(text).style(f"color:{TEXT};")
                    chat_scroll.scroll_to(percent=1.0)

                add_bubble(
                    "Hi! I'm your coach. Ask me about your training load, recovery, "
                    "what to focus on, or how to plan your week.",
                    me=False,
                )

                async def on_send() -> None:
                    msg = (chat_input.value or "").strip()
                    if not msg or chat_state["busy"]:
                        return
                    chat_state["busy"] = True
                    chat_input.value = ""
                    add_bubble(msg, me=True)
                    # Analytics: AI-coach adoption. We send length + whether it's the
                    # first turn, never the message content (it can be personal).
                    analytics.capture("coach_message_sent", current_user_id, {
                        "message_length": len(msg),
                        "first_turn": chat_state["messages"] is None,
                    })

                    with messages_col:
                        with ui.row().classes("w-full justify-start") as thinking:
                            with ui.element("div").style(
                                f"background:{SURFACE}; border-radius:14px; padding:10px 14px;"
                            ):
                                ui.label("Coach is thinking…").style(f"color:{MUTED};")
                    chat_scroll.scroll_to(percent=1.0)

                    try:
                        # Build the data summary once, on the first turn only.
                        if chat_state["messages"] is None:
                            end = datetime.now()
                            sessions = list_user_sessions(current_user_id, end - timedelta(days=30), end)
                            try:
                                rlogs = list_user_recovery(current_user_id, end - timedelta(days=30), end)
                            except Exception:
                                rlogs = []
                            context = build_coach_context(sessions, rlogs)
                        else:
                            context = ""
                        reply, new_messages, logged, logged_recovery = await asyncio.to_thread(
                            coach_reply, msg, current_user_id, datetime.now(),
                            chat_state["messages"], context,
                            current_gcal_token,
                        )
                        chat_state["messages"] = new_messages
                        thinking.delete()
                        add_bubble(reply or "(no reply)", me=False)
                        # If the coach logged a session via its tool, refresh the
                        # dashboard so the new session shows up immediately.
                        if logged:
                            ui.notify(f"Logged {len(logged)} session(s) from chat", color="positive")
                            stats_panel.refresh()
                            charts_row.refresh()
                            recent_snapshot.refresh()
                            history_container.refresh()
                        # If the coach logged recovery, refresh the recovery views
                        # and the Home recovery score (which lives in stats_panel).
                        if logged_recovery:
                            ui.notify(f"Logged {len(logged_recovery)} recovery entr(ies) from chat", color="positive")
                            stats_panel.refresh()
                            recovery_recent.refresh()
                    except Exception:
                        log.exception("coach reply failed uid=%s", current_user_id)
                        thinking.delete()
                        add_bubble(
                            "Sorry — I couldn't reach the coach just now. Please try again.",
                            me=False,
                        )
                    finally:
                        chat_state["busy"] = False

                with ui.row().classes("w-full items-center gap-2"):
                    chat_input = ui.input(placeholder="Ask your coach…") \
                        .props("outlined dense dark").classes("flex-grow") \
                        .on("keydown.enter", on_send)
                    ui.button(icon="send", on_click=on_send) \
                        .props("round dense unelevated") \
                        .style(f"background-color: {ACCENT}; color: #FFFFFF;")


def _tag_pill(text: str, color: str) -> None:
    """A soft (tinted) technique badge — color = meaning, never decoration."""
    ui.label(text).classes("text-xs font-bold px-2 py-0.5 rounded") \
        .style(
            f"color: {color}; background-color: color-mix(in srgb, {color} 16%, transparent); "
            f"letter-spacing: 0.04em;"
        )


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
