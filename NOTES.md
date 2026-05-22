# NOTES — study reference

Personal cheat sheet for every tool, library, and concept used in this project. Read this when you need to refresh your understanding or prep to explain something to Hassan.

---

## Tools and libraries

### Pydantic
- Python **library** (not an API, not an LLM) — installed via `pip install pydantic`
- Lets me describe **data shapes as classes** with type hints
- Validates **every instance on creation** — raises `ValidationError` with a clear message if data is wrong
- Type coercion: tries to convert compatible types (`"45"` → `45`, `"2026-05-22"` → `date(2026,5,22)`). Only fails when conversion is impossible (`"forty"` → int)
- Used in `models.py` for 3 models: `Tag`, `LogEntry`, `Session`
- Mental model: "Pydantic = Python's built-in converters + a wrapper"

### Pydantic AI
- A separate library that wraps LLM APIs (Anthropic, OpenAI, etc.)
- **NOT an LLM itself** — Claude is the LLM
- Used via `Agent` class with `output_type=list[Tag]` — tells Claude "return JSON that matches the Tag schema"
- Pydantic AI handles: prompting, JSON parsing, validation, retries on malformed responses
- Returns already-validated Pydantic objects, not raw strings
- Used in `ai.py` to extract structured tags from free-text notes

### NiceGUI
- Python web framework — write UI in Python, no HTML/JS file
- Built on Quasar (a Vue.js UI library) under the hood
- Key concepts:
  - `@ui.page("/")` — defines a route
  - `ui.input()`, `ui.button()`, `ui.label()` — components
  - `bind_value(target, key)` — two-way data binding to a dict
  - `@ui.refreshable` — lets a function re-render its content on demand (e.g., `stats_panel.refresh()`)
- Runs an async event loop — that's why `extract_tags` is called via `asyncio.to_thread` (avoids loop conflict)
- File: `main.py`

### Firestore
- Google Cloud's **serverless NoSQL document database**
- NoSQL = no rows/tables; data is JSON-like **documents** grouped in **collections**
- Documents nest naturally — your `Session` has nested `log_entries` with nested `tags`, all in one doc
- Serverless = no servers to manage, scales to zero, pay per read/write
- Free tier: 1 GiB storage, 50K reads / 20K writes per day
- This app: one collection `sessions`, doc ID = `{date}_{slot}` (will change to auto-generated in schema refactor)
- Database lives in `eur3` (Belgium + Netherlands multi-region) — location is PERMANENT

### firebase-admin (Python SDK)
- Server-side SDK for talking to Firestore (and other Firebase services)
- Auth happens via:
  - **Locally:** `gcloud auth application-default login` creates ADC credentials
  - **In Cloud Run:** attached service account is auto-detected via the metadata server
- `_client()` in `db.py` is wrapped with `@lru_cache(maxsize=1)` so the SDK initializes once per process

### Docker
- Containers package an app + its dependencies into one isolated image
- Cloud Run can only run containers — so we need a Dockerfile
- Dockerfile recipe: `python:3.11-slim` base → install requirements.txt → copy code → `CMD python main.py`
- `.dockerignore` keeps `.venv`, `.git`, `tests/`, secrets out of the image (smaller + safer)
- `EXPOSE 8080` + reading `PORT` env var is the Cloud Run convention

### Cloud Run
- Google's **serverless container runtime**
- You give it a container image; it runs your container against HTTP requests
- **Scales to zero** when idle — no requests = no instances = no cost
- "Cold start" = the latency hit when scaling from zero (1-2 seconds for this app)
- Service runs as a **service account** (in our case `bjj-tracker-runtime`)
- Service lives in `europe-west1` (Belgium) — co-located with Firestore eur3

### Artifact Registry
- Google's **Docker registry** — where built container images are stored
- URL format: `europe-west1-docker.pkg.dev/{project_id}/{repo}/{image}:{tag}`
- Our images are tagged with `${{ github.sha }}` — every commit produces a unique image, so any past version can be redeployed

### Secret Manager
- Encrypted store for sensitive config (API keys, DB passwords)
- Values are **encrypted at rest**, versioned, audit-logged
- Cloud Run injects secrets at startup via `--set-secrets KEY=secret-name:version`
- We use it for `ANTHROPIC_API_KEY` — never in code, never in plain Cloud Run env vars
- Only service accounts granted `roles/secretmanager.secretAccessor` can read the value

### GitHub Actions
- GitHub's CI/CD — runs YAML-defined workflows on push, PR, schedule, etc.
- Our workflow `.github/workflows/deploy.yml` has 2 jobs:
  1. **test** (every push/PR) — pytest + 95% coverage gate
  2. **deploy** (only push to main, only after test passes) — auth → build → push → deploy
- `needs: test` enforces the dependency so broken code can't deploy

### Workload Identity Federation (WIF)
- Lets GitHub Actions authenticate to GCP **without storing a JSON service-account key**
- Old way: download a JSON key, paste it as a GitHub secret. Keys are long-lived → if leaked, persistent access.
- WIF way: GitHub generates a short-lived **OIDC token**, GCP verifies its signature **and** that the claims match (e.g., `repository_owner == 'elias-ouazzani'`), then issues a 1-hour access token.
- Setup: a **Pool** + **Provider** in GCP, plus an IAM binding letting the GitHub repo impersonate a service account.

### pytest + pytest-cov
- pytest = Python's most popular test framework
- pytest-cov = adds coverage tracking
- This project: 28 tests, **100% coverage** on `models.py`, `db.py`, `ai.py`. `main.py` is excluded (UI glue needs browser automation)
- `pytest.ini` enforces `--cov-fail-under=95` — CI fails if coverage drops
- Firestore and Pydantic AI agent are **mocked** so tests don't hit the network

---

## Flows in this app

### Save flow (clicking "Save session")
1. NiceGUI reads form state (date, slot, drilling/sparring totals, log-entry notes + category) into a dict
2. For each non-empty log entry, `extract_tags(notes)` is called via `asyncio.to_thread` → sends text to Claude via Pydantic AI → gets back validated `list[Tag]`
3. A `Session` Pydantic object is built from the dict + extracted tags — Pydantic validates all fields
4. `db.save_session(session)` writes to Firestore at `sessions/{date}_{slot}` using `set()` (not `add()` — we control the doc ID)
5. Form resets to defaults; `stats_panel.refresh()` and `history_container.refresh()` re-query and re-render

### Load/history flow (page refresh, or after save)
1. `list_sessions(start, end)` queries `sessions` collection where `date >= start AND date <= end`
2. Firestore returns documents as dicts → each is passed to `Session(**dict)` → Pydantic re-validates on read (catches corrupt data)
3. NiceGUI renders each session: date/slot header + edit/delete buttons + stat line + log entries with tag chips

### Edit flow
1. Click pencil icon on a history session → `start_edit(session)` is called
2. Form state is populated from the existing session's values; `editing_id` is set to the original doc ID
3. Page smooth-scrolls to top; orange edit banner appears
4. On Save: a new Session is built and saved. If the user changed date/slot mid-edit (new ID differs from `editing_id`), the old doc is deleted to avoid duplicates.

### Delete flow
1. Click trash icon → `on_delete(session_id)` opens a confirm dialog
2. On confirm → `delete_session(session_id)` removes the Firestore doc → stats + history refresh

### Deploy flow (every push to `main`)
1. GitHub Actions triggers on the push
2. `test` job runs pytest with coverage gate
3. `deploy` job (only if test passed):
   a. Auths to GCP via WIF (short-lived token, no JSON key)
   b. Builds Docker image from `Dockerfile`
   c. Pushes image to Artifact Registry
   d. Tells Cloud Run to deploy that image, pulling `ANTHROPIC_API_KEY` from Secret Manager at startup

---

## Gotchas we hit and how we fixed them

### `pip install` "resolution-too-deep"
- Cause: `pydantic==2.7.1` (pinned) wasn't compatible with modern `pydantic-ai` versions
- Fix: loosened pins in `requirements.txt` — `pydantic>=2.10,<3`, `pydantic-ai` (no pin)

### `ModuleNotFoundError: No module named 'pydantic'`
- Cause: ran `pip install` in the wrong venv, OR the venv wasn't activated
- Fix: always `cd C:\Users\elias\bjj-tracker` + `.venv\Scripts\activate` before `pip` or `python` commands

### Pydantic AI Agent crashed on import — `Set the ANTHROPIC_API_KEY environment variable`
- Cause: `_agent = Agent(...)` was at module top level. Import ran before `load_dotenv()`, so the env var wasn't set yet.
- Fix: lazy-init via `_get_agent()` — Agent is constructed on first call, after dotenv has loaded.

### Cloud Run save crashed with `RuntimeError: This event loop is already running`
- Cause: `extract_tags()` calls `agent.run_sync()` → tries to spin up its own asyncio loop. But NiceGUI is already running one, and you can't have two.
- Fix: call extract_tags via `await asyncio.to_thread(extract_tags, notes)` — runs the sync function in a worker thread with its own loop context.

### Firestore warnings — "Detected filter using positional arguments"
- Cause: `.where("date", ">=", value)` is deprecated in the new Firestore SDK
- Fix: import `FieldFilter` from `google.cloud.firestore_v1.base_query` and use `.where(filter=FieldFilter("date", ">=", value))`

### GitHub Actions deploy crashed — `invalid tag "europe-west1-docker.pkg.dev//bjj-tracker/..."`
- Cause: `secrets.GCP_PROJECT_ID` was empty (had a leading space from paste, or wrong name)
- Fix: re-typed the value into the GitHub secret by hand (not pasted)

### Cloud Run URL returns "Forbidden" even when signed in
- Cause: Atheal org policy blocks the `allUsers` invoker binding on Cloud Run services
- Workaround for demo: `gcloud run services proxy` tunnels through a local auth proxy → access via `localhost:8080`
- Long-term fix (Phase A): identity-aware proxy (IAP) or admin override for project

---

## Coming next — concepts to learn

### Discriminated unions (Phase B — schema redesign)
- Pydantic feature for polymorphic data
- One field acts as the "tag" that tells Pydantic which class to validate against
- Syntax: `Annotated[ClassA | ClassB, Field(discriminator="some_field")]`
- Each class has `some_field: Literal["..."]` declaring its tag value
- Used to model `Session.data` as `GrapplingData | StrikingData | CardioData | WeightsData`

### Firebase Authentication (Phase C — later)
- Adds user sign-in via Google account
- JS SDK on the front-end + ID-token verification on the back-end via firebase-admin
- Each user gets a stable UID — stored on every Session as `user_id`

### Firestore security rules (Phase C)
- Server-side rules that enforce per-user data isolation
- "User can only read/write sessions where the session's user_id matches their UID"
- Bypassed by firebase-admin (server-side) but matters if we ever expose Firestore to the client

### PR-based workflow (going forward)
- Every change: branch → commit → push branch → open PR → merge via PR
- No more direct pushes to main
- This branch: `feature/schema-redesign`
