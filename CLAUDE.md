# bjj-tracker

Personal BJJ training session log. Built with NiceGUI + Firestore + Pydantic AI.

> **Launched (2026-07-10):** live at **strain.fit** (marketing, Cloudflare Pages)
> → **app.strain.fit** (app, Cloudflare Worker → private Cloud Run via WIF). See
> `NOTES.md` → "✅ LAUNCHED (2026-07-10)" for architecture + redeploy commands.

## Stack
- Python 3.11+
- NiceGUI 2.x (web UI)
- Firestore (session storage)
- Pydantic (data models)
- Pydantic AI (training tip generation)
- Docker + Google Cloud Run (deployment)
- GitHub Actions (CI/CD)

## Project structure
```
bjj-tracker/
├── main.py        # NiceGUI app + all UI pages (Home / Log / Recovery / History / Coach)
├── models.py      # Pydantic models (Session discriminated union + RecoveryLog)
├── db.py          # Firestore read/write (sessions + recovery_logs collections)
├── charts.py      # Pure aggregation: training stats + recovery score formula
├── services/      # Ownership-enforcing layer (sessions.py, recovery.py)
├── ai.py          # Pydantic AI tag extraction (claude-haiku)
├── coach.py       # Pydantic AI chat coach (claude-sonnet) — reads user data
├── tests/
│   ├── test_models.py
│   └── test_db.py
├── Dockerfile
├── requirements.txt
└── .env           # local only, never committed
```

## Running locally
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Running tests
```bash
pytest --cov=. --cov-report=term-missing
```

## Environment variables
- `ANTHROPIC_API_KEY` — API key for Pydantic AI
- `GOOGLE_APPLICATION_CREDENTIALS` — path to Firebase service account JSON (local only)
- `FIRESTORE_PROJECT_ID` — Firebase project ID

## Design system ("Strain" — Whoop-inspired, via Claude Design handoff)
Performance-device feel: dark, serious, data-dense. The big number IS the
design; **color communicates meaning only, never decoration**. Tokens mirror
the handoff's `tokens/*.css` and live as Python constants at the top of main.py.
- Surfaces (shade-only, NO border, NO shadow, 12px radius): bg #0D0D0D (warm
  near-black) · surface #1A1A1A · elevated #242424 (hover/active) · track
  #2A2A2A (gauge/bar backgrounds) · hairline #2E2E2E (rare dividers)
- Text: #F5F5F5 (never pure white) · secondary #A8A8A8 · muted #6B6B6B · faint #4A4A4A
- Accent: STRAIN orange #FF5A1F (gradient #FF4500→#FF6B00) — primary actions, streak
- Font: **Hanken Grotesk** (single grotesk; weight 800 + tabular figures carry
  stat numbers). No JetBrains Mono, no Inter. Tiny UPPERCASE tracked labels (.s-label).
- Signature element: **ScoreRing** gauge (`_score_ring_html`) — 270° gradient arc
  over a dark track, big number centered. Used on Home for weekly training load
  vs WEEKLY_GOAL_MIN (300).
- Per-discipline colors (DISCIPLINE_COLORS) kept distinct for chart legibility
  but pulled toward the handoff palette: grappling=blue, striking=warm, cardio=green,
  weights=yellow. Rendered as elevated icon tiles (`_disc_icon_tile`), not bubbles.
- Cards/rows: shade-only; lists are table-style rows with a barely-perceptible
  hover lift (.s-row → --elevated). Technique tags are soft tinted badges (`_tag_pill`).
- Icons: still Material Symbols (handoff specs Lucide; not swapped — would need bundling).
- Navigation: top tabs ≥768px, fixed bottom tab bar on phones (.top-tabs/.bottom-nav);
  active = bright text, inactive = faint (no accent color on nav).
- Log flow: 2-step quick-add stepper (discipline tile grid → per-discipline fields).
- Handoff source: `C:\Users\elias\Downloads\Strain Design System_extracted\`
  (HANDOFF.md + SKILL.md + tokens/ + ui_kits/strain-web/).
