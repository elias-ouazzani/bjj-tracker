# NOTES — study reference

Personal cheat sheet for every tool, library, and concept used in this project. Read this when you need to refresh your understanding or prep to explain something to Hassan.

---

## ✅ LAUNCH STATUS — DONE (launched 2026-07-10)

> **Superseded.** The launch is live: **strain.fit** (marketing) → **app.strain.fit**
> (app). The block below is the pre-launch plan, kept for history. Step 4 of
> `setup-gcp.sh` PASSED (org did NOT block it), so the "migrate to personal GCP"
> fallback was never needed. Full launch detail + redeploy commands are in the
> "✅ LAUNCHED (2026-07-10)" section further down.

**Goal (pre-launch):** make the app publicly reachable on a custom domain with a
marketing page in front. Working branch: `claude/strain-project-next-steps-pqn8hg`.

**Done:**
- Domain bought (on Cloudflare as DNS host).
- Marketing landing page built → `marketing/` (Cloudflare Pages, deploy notes
  in `marketing/README.md`). Placeholder app URL in 3 spots to swap later.
- Confirmed the launch blocker is an **org policy**, TWICE:
  - `allUsers` invoker on Cloud Run → blocked by
    `constraints/iam.managed.allowedPolicyMembers`.
  - service-account key creation → blocked by
    `constraints/iam.disableServiceAccountKeyCreation`.
- Chosen path to work around it **without an admin**: Cloudflare Worker →
  PRIVATE Cloud Run via Workload Identity Federation (no key). Full package
  built → `cloudflare-proxy/` (gen-keys.mjs, setup-gcp.sh, worker.js,
  wrangler.toml, README.md).

**NEXT ACTION (in Cloud Shell, project `atheal-internship-elias`):**
```
cd ~ && git clone https://github.com/elias-ouazzani/bjj-tracker.git
cd bjj-tracker && git checkout claude/strain-project-next-steps-pqn8hg
cd cloudflare-proxy
node gen-keys.mjs      # writes priv.pem + jwks.json, prints KID
bash setup-gcp.sh      # <-- Step 4 is the FAIL-FAST GATE
```
- **If `setup-gcp.sh` Step 4 errors** with `iam.managed.allowedPolicyMembers`
  → the org blocks this too. **Abandon the Worker path and migrate to a
  personal GCP project** (recreate Cloud Run + Firestore + Secret Manager +
  WIF there; personal project has no org policy so `allUsers` just works).
- **If Step 4 succeeds** → copy the printed Cloud Run URL, then continue with
  `cloudflare-proxy/README.md` steps 3–7 (fill wrangler.toml, `wrangler
  deploy`, add proxied `app` DNS record, add `app.<domain>` to Firebase
  Authorized domains, test).

**Still open / decisions pending:**
- Admin request (asking for a policy exception) was drafted but not yet
  answered — the Worker path makes it optional, not required.
- Marketing page's 3 app links still point at `https://app.example.com/` —
  swap to `https://app.<real-domain>` once the app domain is live.
- Marketing page is on the feature branch, not `main` — merge to `main` (or
  point Cloudflare Pages at the branch) before deploying.

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

## Next steps — public launch (custom domain + marketing page)

### ✅ LAUNCHED (2026-07-10)
The public launch is live:
- **strain.fit** → marketing landing page (Cloudflare Pages project `strain-marketing`).
- **app.strain.fit** → the app (Cloudflare Worker `strain-proxy` → private Cloud Run via WIF).
- **www.strain.fit** → marketing page (attached; cert may lag a few min after apex).
- Cloudflare account: personal (elias.oc.2007@gmail.com). Domain: GoDaddy → Cloudflare NS.
- Marketing page was redesigned (instrument-panel layout) and deployed via
  `wrangler pages deploy` from `marketing/`.
- Redeploy app: push to main (GitHub Actions). Redeploy marketing:
  `cd marketing && npx wrangler pages deploy . --project-name=strain-marketing --branch=main`.

### CURRENT STATUS — historical (as of 2026-07-09)
Domain is **strain.fit** (registered at GoDaddy). Cloudflare account is
**personal** (elias.oc.2007@gmail.com), NOT Atheal — the app works fine as a
cross-account setup (personal Cloudflare → Atheal GCP); only long-term backend
ownership depends on GCP, not Cloudflare.

Done:
- [x] **Blocker RESOLVED** — not via IAP. Went with a Cloudflare Worker that
  authenticates to the PRIVATE Cloud Run via WIF (no SA key, no `allUsers`).
  See `cloudflare-proxy/`. Ran `setup-gcp.sh` — the Step-4 fail-fast gate
  PASSED (org policy did NOT block the token-creator binding).
- [x] `wrangler.toml` filled in: `CLOUD_RUN_URL` =
  `https://bjj-tracker-6otadxxc2a-ew.a.run.app`, route = `app.strain.fit/*`.
- [x] `marketing/index.html` — 3 CTA links updated to `https://app.strain.fit/`.
- [x] strain.fit added to Cloudflare (Free); GoDaddy nameservers changed to
  Cloudflare's.

Waiting / next (resume here in a couple hours):
- [ ] Cloudflare zone to go **Active** (nameserver propagation — was still
  showing GoDaddy `domaincontrol.com` when we stopped). Check:
  `nslookup -type=NS strain.fit 8.8.8.8` → should show `*.ns.cloudflare.com`.
- [ ] `npx wrangler login` + `npx wrangler secret put PRIVATE_KEY_PEM < priv.pem`
  (can do before Active) then `npx wrangler deploy` (needs Active).
- [ ] Add DNS record: `AAAA`, name `app`, `100::`, **PROXIED / orange**.
- [ ] Firebase → Auth → Settings → Authorized domains → add `app.strain.fit`.
- [ ] Marketing page → Cloudflare Pages, output dir `marketing`, apex + `www`.
- [ ] NOTE: uncommitted changes on branch (`wrangler.toml`, `marketing/index.html`,
  this file) — commit when ready.

### 0. (HISTORICAL) Original blocker: Cloud Run "Forbidden" org policy
- Resolved by the Worker approach above. Left here for context.
- `deploy.yml` already passes `--allow-unauthenticated`, but Atheal's GCP org
  policy strips the `allUsers` invoker binding anyway — so even signed-in
  users hit "Forbidden" before Firebase Auth ever runs (see Gotchas above).
- A custom domain does **not** fix this — it's an IAM-layer block, upstream
  of the app. Options considered:
  - **Identity-Aware Proxy (IAP)** in front of Cloud Run — gates the request
    with a Google-account check before it reaches the container.
  - Ask an org admin for an exception on the org policy.
  - **Cloudflare Worker + WIF (CHOSEN)** — see `cloudflare-proxy/README.md`.

### 1. Domain + Cloudflare DNS
- Point the domain's nameservers at Cloudflare (Cloudflare becomes the DNS host).
- Two things to route:
  - `app.<domain>` → the NiceGUI/Cloud Run service (the real product, gated
    by Firebase login at `/`)
  - apex `<domain>` (and/or `www.<domain>`) → the marketing/landing page
- Verify domain ownership in Google Search Console first — required before
  Cloud Run will map a custom domain to a service. Google gives a `TXT`
  record to add in Cloudflare.
- Create the mapping:
  `gcloud run domain-mappings create --service bjj-tracker --domain app.<domain> --region europe-west1`
  This prints the exact record(s) to add — a `CNAME` to `ghs.googlehosted.com`
  for a subdomain (an apex domain needs `A`/`AAAA` records to Google's anycast
  IPs instead, since `CNAME` isn't valid at a zone apex).
- **Cloudflare proxy must be "DNS only" (grey cloud, not orange) for
  `app.<domain>`.** Cloud Run provisions and serves its own Google-managed TLS
  cert for that hostname; if Cloudflare proxies the traffic, requests hit
  Cloudflare's edge/cert instead of Google's, so both cert issuance and normal
  serving break.
- Cert provisioning after the DNS record is correct: usually 15–60 min, up to
  24h worst case.

### 2. Marketing page → app login
- Simplest: a static one-page site on **Cloudflare Pages** (free, DNS-native,
  no separate host to manage) at the apex/`www`, with one CTA button
  ("Get started" / "Log in") linking to `https://app.<domain>/`.
- That link lands on the existing auth gate in `main.py` (`index()` at `/`),
  which already redirects unauthenticated visitors to `/login` — no app
  changes needed, the marketing site is purely additive.
- Alternative (no second codebase to host): add a logged-out marketing view
  directly in `main.py` at `/` instead of the immediate redirect, and move
  today's authenticated home to e.g. `/app`. More work, one less moving part
  to host/deploy.

### 3. DNS — concepts + the records THIS project needs

**What DNS is:** the internet's phone book. People type a name
(`strain.fit`); machines need an address to connect. DNS is the lookup that
turns name → address. Each entry is a **record** (one line in the phone book).

**Nameservers:** the servers that hold your domain's phone book. Putting the
domain "on Cloudflare" = changing the nameservers *at the registrar* to
Cloudflare's two, so Cloudflare becomes the authority for every record.

**Record types you touch here:**
| Type | Maps a name to… | Example |
|---|---|---|
| `A` | an IPv4 address | `strain.fit` → `192.0.2.1` |
| `AAAA` | an IPv6 address | `app.strain.fit` → `100::` (dummy, see below) |
| `CNAME` | another *name* (an alias) | `www.strain.fit` → `strain.fit` |
| `TXT` | free text (used for verification) | `google-site-verification=…` |

**Apex vs subdomain:** the apex/root is the bare domain (`strain.fit`, host
`@`). A subdomain is a prefix (`app.strain.fit`, host `app`). Gotcha: a real
`CNAME` isn't valid at the apex — apex needs `A`/`AAAA` (Cloudflare fakes apex
CNAMEs with "CNAME flattening", so on Cloudflare it works anyway).

**Cloudflare proxy status — orange vs grey (the thing that trips everyone up):**
- **Proxied (orange cloud):** traffic flows *through* Cloudflare's edge —
  needed to run a **Worker**, gives caching + hides the origin. Cloudflare
  serves the TLS cert.
- **DNS only (grey cloud):** Cloudflare just answers the lookup and steps out
  of the way — the origin serves its own TLS cert.
- Rule of thumb: **need a Worker or Cloudflare TLS → orange. Origin manages its
  own cert (e.g. raw Cloud Run domain mapping) → grey.**

**TTL / propagation:** TTL = how long resolvers cache a record. Changes aren't
instant — a few minutes typically, up to 24–48h for nameserver changes. Don't
panic if a new record isn't live immediately.

**The records this project actually needs (CURRENT plan — Worker proxy):**
| Record | Host | Value | Proxy | Purpose |
|---|---|---|---|---|
| ~~CNAME~~ | `app` | ~~`ghs.googlehosted.com`~~ | ~~DNS only (grey)~~ | SUPERSEDED — direct Cloud Run mapping, abandoned (org blocks public Cloud Run) |
| AAAA | `app` | `100::` (dummy) | **PROXIED (orange)** | Worker path (what we use): the route intercepts `app.strain.fit`; value is a placeholder, must be orange so the Worker runs |
| A/AAAA (apex) or CNAME (`www`) | `@` / `www` | auto-set by Cloudflare Pages | proxied (orange) | serves the marketing page |

> On the Worker path there is **no Search Console TXT step** — that only
> belonged to the abandoned direct domain-mapping approach (which would come
> back if we ever migrate to a personal GCP project).

### 4. Other important concepts (from the launch work)

**Org policy constraints (why we can't just go public):** an *organization*
(Atheal) can enforce rules on every project inside it. Two bit us:
- `iam.managed.allowedPolicyMembers` — restricts *which identities* can be
  granted roles. Blocks adding `allUsers` (anonymous public) → the "Forbidden".
- `iam.disableServiceAccountKeyCreation` — blocks downloading SA JSON keys.
- Key insight: these are enforced at **Google's edge, before the app runs**, so
  no DNS/proxy trick bypasses them. You either get an exception, or move to a
  project with no org (personal account).

**The Worker proxy trick (public edge, private backend):** the app can still
go public *without* violating either policy:
- Cloud Run stays **private** (no `allUsers`).
- A **Cloudflare Worker** at `app.<domain>` authenticates *itself* to Cloud Run
  on every request and forwards the traffic. Public visitors only ever talk to
  Cloudflare; Cloudflare talks to Google as an authorized identity.

**Workload Identity Federation (WIF) — the "no key" part:** normally you'd
prove identity to Google with a downloaded key (blocked). WIF instead lets an
*external* identity be trusted: the Worker **self-signs a token with its own
keypair** (we generate it — `gen-keys.mjs`), GCP verifies it against the public
half (`jwks.json`), and hands back a short-lived Google token. No GCP-issued
key ever exists → `disableServiceAccountKeyCreation` never applies. This is the
**same mechanism GitHub Actions already uses to deploy** (`deploy.yml`'s
`workload_identity_provider`), which is why it should slip past the org policy.

**OIDC token exchange (the 3 hops in `worker.js`):**
1. Worker signs a JWT → 2. trades it at Google **STS** for a federated access
token → 3. uses that to **impersonate** the runtime SA and mint a Cloud
Run-scoped **ID token** → attaches it as `Authorization: Bearer` and forwards.
The ID token is cached ~50 min so most requests skip the dance.

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
