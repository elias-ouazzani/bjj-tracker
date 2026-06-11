# bjj-tracker

Personal BJJ training session log. Built with NiceGUI + Firestore + Pydantic AI.

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
├── main.py        # NiceGUI app + all UI pages
├── models.py      # Pydantic models (Session, WeeklyScore)
├── db.py          # Firestore read/write functions
├── ai.py          # Pydantic AI training tip generation
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

## Design system (Apple-Fitness-inspired dark)
- Background: #000000 (pure black canvas)
- Surface: #1C1C1E / hover #2C2C2E (floating rounded cards, 18px radius)
- Accent: #E8A957 (highlights only — streak, active nav, primary actions)
- Text: #FFFFFF / muted #8E8E93
- Per-discipline colors carry the personality (DISCIPLINE_COLORS in main.py)
- Font: Inter for UI text; JetBrains Mono only on big stat numbers (.stat-num)
- Navigation: top tabs ≥768px, fixed bottom tab bar on phones (.top-tabs/.bottom-nav)
- Log flow: 2-step quick-add stepper (discipline tile grid → per-discipline fields)
