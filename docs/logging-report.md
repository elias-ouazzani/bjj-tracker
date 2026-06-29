# Strain — fixes, bugs, and logging report

_For review. Prepared 2026-06-29. Branch: `feature/ai-coach`._

## 1. Summary

This report covers three things:

1. The **end-to-end fixes and bugs** addressed across the app's lifetime (auth, deploy, data, AI).
2. The **logging approach** as implemented — how logs are produced, named, levelled, and read in Cloud Run.
3. A **logging-improvement pass** done now: the three external-call modules (`ai.py`, `coach.py`, `gcal.py`) were emitting **no logs at all**, and several handlers swallowed or leaked errors. That is now fixed, with timing and error capture, verified by the test suite.

Result of the improvement pass: **148 tests pass, total coverage 97.21%** (gate is 95%).

---

## 2. End-to-end fixes and addressed bugs

### 2a. Functional bugs fixed over the project (from git history)

| Area | Bug | Fix | Commit |
|------|-----|-----|--------|
| Auth | Sign-in popup closed before its result returned (esp. iPhone) | `Cross-Origin-Opener-Policy: same-origin-allow-popups` middleware; popup-only flow | `7feb20c` |
| Auth | Login loop — user bounced back to `/login` after signing in | Session/cookie persistence fixes | `8e6949c`, `e1be43f` |
| Auth | Safari/iOS couldn't complete sign-in | Tried `signInWithRedirect`, then standardised on popup-only | `66609c3` → `7feb20c` |
| Auth | Debug banner leaked onto the login screen | Removed on-screen banner; breadcrumbs go to server logs instead | `c957db0` |
| Deploy | CI build failed: `invalid tag "…//bjj-tracker/…"` (empty `GCP_PROJECT_ID`) | Re-entered the GitHub secret by hand | NOTES |
| Deploy | Service rename to `strain` broke the deploy target | Reverted to `bjj-tracker` | `414882e` |
| UI | Discipline-tile hover outline clipped by the card | Inset the outline | `3ec09d2` |

### 2b. Environment / integration bugs (from `NOTES.md`)

| Bug | Root cause | Fix |
|-----|------------|-----|
| `pip install` "resolution-too-deep" | `pydantic==2.7.1` pin incompatible with `pydantic-ai` | Loosened pins: `pydantic>=2.10,<3`, unpinned `pydantic-ai` |
| `Set the ANTHROPIC_API_KEY…` crash on import | `Agent(...)` built at module top, before `load_dotenv()` | Lazy `_get_agent()` — Agent built on first call |
| `RuntimeError: This event loop is already running` on save | `agent.run_sync()` spins its own loop inside NiceGUI's loop | Call via `await asyncio.to_thread(...)` |
| Firestore "positional arguments" warning | Deprecated `.where("date", ">=", v)` | `FieldFilter(...)` keyword form |
| Cloud Run "Forbidden" when signed in | Org policy blocks `allUsers` invoker | `gcloud run services proxy` for demo; IAP/admin override as the real fix |

### 2c. Bugs fixed in this logging pass

| Bug | Impact | Fix |
|-----|--------|-----|
| `on_save` did not guard the Anthropic or Firestore calls | A Claude/network/Firestore failure crashed the save with a generic error and **no log** | Broad handler logs `log.exception(uid, discipline)` and shows a friendly message; AI tagging is now best-effort (a tagging failure saves the session **without** tags rather than losing the entry) |
| `on_save_recovery` only handled `RecoveryAccessDenied` | Other failures were unlogged and surfaced as raw errors | Added a generic handler with `log.exception` |
| `recent_snapshot`, `recovery_recent`, `history_container` caught errors but **never logged** them | Failures were invisible in Cloud Run; raw exception text was rendered **to the user** (info leak) | Each now `log.exception(...)` and shows a generic "please refresh" message |
| `ai.py`, `coach.py`, `gcal.py` had **zero logging** | The exact modules calling Anthropic and Google Calendar were silent — no visibility into failures or latency | Added module loggers + structured INFO/WARNING/timing (see §3) |

---

## 3. Implemented approach to logging

### 3a. Foundation (pre-existing)

- **One config at startup** (`main.py`):
  ```python
  logging.basicConfig(
      level=os.environ.get("LOG_LEVEL", "INFO"),
      format="%(asctime)s %(levelname)s %(name)s %(message)s",
      stream=sys.stdout,  # stdout so Cloud Run doesn't tag every line ERROR
  )
  ```
- **Why stdout:** Cloud Run tags anything on *stderr* as `ERROR`. Writing to stdout preserves the real level of each line.
- **Named, hierarchical loggers** under a single `strain.*` root, so every line says which subsystem produced it and the level can be tuned per-area later.
- **Secret hygiene:** tokens and keys are **never** logged. `/auth/callback` records only whether a token was `present`/`absent`, never its value.
- **Client breadcrumb relay:** the browser sign-in flow POSTs each step to `/auth/clientlog`, which logs it server-side — so the iPhone/no-devtools sign-in timeline still lands in Cloud Run logs.
- **CI/CD:** `deploy.yml` sets `LOG_LEVEL` as a Cloud Run env var (currently `DEBUG`).

Originating commits: `e65df41`, `e22703e`, `eb937bd` (structured auth/db/session logging + breadcrumb relay).

### 3b. Logger map (after this pass)

| Logger | Module | Emits |
|--------|--------|-------|
| `strain.main` | `main.py` | Auth gate, session/recovery save & delete, data-load failures, coach-reply failures, startup config |
| `strain.auth` | `auth.py` | firebase-admin init, token verification outcomes |
| `strain.db` | `db.py` | Doc create/update/delete; **new:** list query timing + fetched/in-range counts |
| `strain.services.sessions` / `strain.services.recovery` | `services/*` | Ownership decisions (incl. WARNING on rejected cross-user access) and save/delete |
| `strain.ai` *(new)* | `ai.py` | Tag-extraction start/skip + success with tag count and **latency (ms)** |
| `strain.coach` *(new)* | `coach.py` | Reply start/success (latency, reply size, logged counts); WARNING on every handled tool failure; INFO on successful tool actions |
| `strain.gcal` *(new)* | `gcal.py` | Calendar create/list start + success with event id/count and **latency (ms)** |

`charts.py` is pure arithmetic (no I/O) and intentionally emits nothing.

### 3c. Level conventions (now applied consistently)

- **DEBUG** — verbose internals for diagnosis: Firestore reads (with timing/counts), AI/calendar call start. Off by default.
- **INFO** — meaningful user/system actions: auth events, session/recovery save & delete, coach actions, and **every external-call success with its latency**.
- **WARNING** — handled, often user-caused failures: rejected cross-user access, unknown discipline, missing input, calendar 401/expired token, calendar API error.
- **ERROR** (`log.exception`, includes stack trace) — unexpected failures with context: session/recovery save failures, dashboard data-load failures, coach-reply failures, unexpected calendar failures.

### 3d. What troubleshooting looks like now

- A slow dashboard shows up as `strain.db … ms=…` and `strain.ai … ms=…` / `strain.gcal … ms=…` lines — you can see *which* dependency is slow.
- A failed save now produces `strain.main session.save failed uid=… discipline=…` **with a stack trace**, instead of a silent crash.
- A failed Claude tagging call no longer loses the session — it logs and saves without tags.
- A calendar problem is attributable: token absent vs expired (401) vs API error vs unexpected, each with its own line and the user's `uid`.

---

## 4. Verification

```
148 passed
Required test coverage of 95% reached. Total coverage: 97.21%
  ai.py 100% · auth.py 100% · db.py 100% · gcal.py 100% · models.py 100%
  services/* 100% · coach.py 93% · charts.py 97%
```

- All new logging on tested branches is covered.
- Added one test (`test_unexpected_error_is_handled_and_logged`) for the new unexpected-calendar-failure path.
- Remaining uncovered lines in `coach.py`/`charts.py` are **pre-existing** input-edge branches, not introduced here.
- `main.py`, `ai.py`, `coach.py`, `gcal.py`, `db.py` all byte-compile.

---

## 5. Next steps (prioritised)

**Near-term**
1. **Structured JSON logging for Cloud Run** — emit each line as JSON with a `severity` field and structured `jsonPayload` (uid, discipline, ms) so Cloud Logging parses severity correctly and lets you filter on fields instead of regex over text.
2. **Lower production `LOG_LEVEL` to `INFO`** — `deploy.yml` currently sets `DEBUG`, which logs a line per Firestore read in production. Keep DEBUG as an opt-in for active diagnosis.
3. **Request / interaction correlation id** — attach a short id per page load and per coach turn (via a logging filter or `contextvars`) so one user action can be traced across `main → services → db → ai/gcal`.

**Medium-term**
4. **Alerting on ERROR** — log-based metrics + alert policy (or Cloud Error Reporting / Sentry) so functional errors page you rather than waiting to be noticed.
5. **Access-log middleware** — extend the existing COOP middleware to log method/path/status/latency for every request, giving a request-level baseline.
6. **Close the remaining `coach.py` edge branches** with small tests (the input-validation paths), tightening the net you now log from.

**Longer-term**
7. **Startup self-check** — log Firestore reachability and `ANTHROPIC_API_KEY` presence at boot; add a health endpoint.
8. **Retention & cost review** — sampling and a retention policy once log volume grows.
9. **PII pass** — confirm message text is never logged (today only length is); document the policy.
