# Cloudflare Worker → private Cloud Run (no service-account key)

Makes the app publicly reachable at `app.<your-domain>` **without** needing
`allUsers` on Cloud Run and **without** a service-account key — the two things
the Atheal org policy blocks (`iam.managed.allowedPolicyMembers` and
`iam.disableServiceAccountKeyCreation`).

## How it works (the short version)

Cloud Run stays **private**. A Cloudflare Worker sits in front at
`app.<your-domain>`. On each request the Worker self-signs an OIDC token with
its own private key, trades it (via Workload Identity Federation) for a
Cloud Run-scoped Google ID token, and forwards the request to the real
`*.run.app` URL with that token attached. Google sees an authorized identity;
the public visitor only ever talks to Cloudflare.

This is the **same mechanism** GitHub Actions already uses to deploy (WIF, no
key) — just pointed at Cloudflare. That's why it should pass where a key/public
grant fails.

## Files

- `gen-keys.mjs`  — generates the keypair (`priv.pem` + `jwks.json`)
- `setup-gcp.sh`  — gcloud commands (pool, provider, IAM). **Has the fail-fast gate.**
- `worker.js`     — the Worker (auth + proxy + WebSocket passthrough)
- `wrangler.toml` — Worker config (edit the placeholders)

`priv.pem` and `jwks.json` are generated at runtime and **must not be committed**
(see `.gitignore`).

---

## Run order

### 1. Generate keys (Cloud Shell)
```
cd cloudflare-proxy
node gen-keys.mjs      # writes priv.pem + jwks.json, prints the KID
```

### 2. Run the GCP setup — this is the fail-fast gate
```
bash setup-gcp.sh
```
- If **Step 4** errors with `constraints/iam.managed.allowedPolicyMembers`,
  **STOP** — the org blocks this too, and you should migrate to a personal GCP
  project instead. Nothing before Step 4 costs anything.
- If it finishes, note the printed **Cloud Run URL** — you need it next.

### 3. Fill in `wrangler.toml`
- `CLOUD_RUN_URL` → the URL printed at the end of step 2
- `KID` → the KID printed in step 1 (default `strain-key-1`)
- `routes` → replace `YOURDOMAIN` with your real domain (2 spots)

### 4. Deploy the Worker (Cloudflare)
```
npm install -g wrangler        # if not already installed
wrangler login                 # opens browser, authorizes your CF account
wrangler secret put PRIVATE_KEY_PEM < priv.pem
wrangler deploy
```

### 5. DNS
- In Cloudflare, add an `app` record for your domain. A common trick: point it
  at a dummy (`AAAA` `100::` or `CNAME` to your domain) — the value doesn't
  matter because the **Worker route** intercepts it. It **must be
  PROXIED (orange cloud)** so the Worker runs.

### 6. Firebase — authorize the new domain (easy to forget)
Firebase Console → your project → **Authentication → Settings → Authorized
domains** → add `app.<your-domain>`. Without this the Google sign-in popup
fails with `auth/unauthorized-domain`.

### 7. Test
Open `https://app.<your-domain>` — you should get the login page, sign in, and
land in the app with live updates working (WebSockets proxied).

---

## Troubleshooting
- **502 "Proxy auth error: STS 400 …"** — the JWT the Worker signed was
  rejected. Check that `ISSUER` / `AUDIENCE` / `SUBJECT` / `KID` match exactly
  between `setup-gcp.sh`, `wrangler.toml`, and `gen-keys.mjs`, and that
  `jwks.json` was the one uploaded to the provider.
- **403 from Cloud Run** — the SA lacks `run.invoker` (step 5) or the ID token
  audience ≠ the Cloud Run URL. Confirm `CLOUD_RUN_URL` has no trailing path.
- **Blank page / no interactivity** — WebSockets not passing through; confirm
  the app record is proxied (orange) and the route pattern ends in `/*`.
