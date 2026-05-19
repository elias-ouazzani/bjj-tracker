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

## Design system
- Background: #0F0F0D
- Surface: #1C1B18
- Accent: #E8A957
- Text: #FFFFFF / #888880
- Font: JetBrains Mono
