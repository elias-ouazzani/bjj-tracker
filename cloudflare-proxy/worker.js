// =============================================================================
// Cloudflare Worker: public edge -> PRIVATE Cloud Run, via Workload Identity
// Federation (no service-account key).
//
// Per request the Worker:
//   1. self-signs a short-lived OIDC JWT with its private key (RS256),
//   2. exchanges it at Google STS for a federated access token,
//   3. impersonates the runtime service account to mint a Cloud Run-scoped
//      ID token (iamcredentials.generateIdToken),
//   4. forwards the request to the real *.run.app URL with that ID token as
//      Authorization: Bearer — this is the "hostname handoff": the public host
//      (app.yourdomain) is rewritten to Cloud Run's internal URL on forward.
//
// The ID token is cached per isolate (~50 min) so most requests skip 1-3.
// WebSocket upgrades are proxied too (NiceGUI needs them for live updates).
// =============================================================================

let cachedToken = null; // { token, exp } — module global, per-isolate cache

export default {
  async fetch(request, env) {
    let idToken;
    try {
      idToken = await getIdToken(env);
    } catch (err) {
      return new Response("Proxy auth error: " + err.message, { status: 502 });
    }

    const url = new URL(request.url);
    const target = env.CLOUD_RUN_URL.replace(/\/$/, "") + url.pathname + url.search;

    const headers = new Headers(request.headers);
    headers.set("Authorization", "Bearer " + idToken);
    headers.delete("Host"); // let fetch set Host for the run.app origin

    const isWs = (request.headers.get("Upgrade") || "").toLowerCase() === "websocket";
    const noBody = isWs || request.method === "GET" || request.method === "HEAD";

    const resp = await fetch(target, {
      method: request.method,
      headers,
      body: noBody ? undefined : request.body,
      redirect: "manual",
    });

    if (isWs && resp.webSocket) {
      return new Response(null, { status: 101, webSocket: resp.webSocket });
    }
    return resp;
  },
};

async function getIdToken(env) {
  const now = Math.floor(Date.now() / 1000);
  if (cachedToken && cachedToken.exp - 120 > now) return cachedToken.token;

  const assertion = await signJwt(env, now);
  const federated = await stsExchange(env, assertion);
  const idToken = await generateIdToken(env, federated);

  cachedToken = { token: idToken, exp: now + 3000 }; // ~50 min
  return idToken;
}

async function signJwt(env, now) {
  const header = { alg: "RS256", typ: "JWT", kid: env.KID };
  const payload = {
    iss: env.ISSUER,
    sub: env.SUBJECT,
    aud: env.AUDIENCE,
    iat: now,
    exp: now + 300,
  };
  const input = `${b64urlJson(header)}.${b64urlJson(payload)}`;
  const key = await importPkcs8(env.PRIVATE_KEY_PEM);
  const sig = await crypto.subtle.sign(
    { name: "RSASSA-PKCS1-v1_5" },
    key,
    new TextEncoder().encode(input),
  );
  return `${input}.${b64url(new Uint8Array(sig))}`;
}

async function stsExchange(env, assertion) {
  const audience =
    `//iam.googleapis.com/projects/${env.PROJECT_NUMBER}` +
    `/locations/global/workloadIdentityPools/${env.POOL_ID}` +
    `/providers/${env.PROVIDER_ID}`;
  const body = new URLSearchParams({
    grant_type: "urn:ietf:params:oauth:grant-type:token-exchange",
    audience,
    scope: "https://www.googleapis.com/auth/cloud-platform",
    requested_token_type: "urn:ietf:params:oauth:token-type:access_token",
    subject_token: assertion,
    subject_token_type: "urn:ietf:params:oauth:token-type:jwt",
  });
  const r = await fetch("https://sts.googleapis.com/v1/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!r.ok) throw new Error("STS " + r.status + ": " + (await r.text()));
  return (await r.json()).access_token;
}

async function generateIdToken(env, federatedAccessToken) {
  const url =
    `https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/` +
    `${env.SA_EMAIL}:generateIdToken`;
  const r = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + federatedAccessToken,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ audience: env.CLOUD_RUN_URL, includeEmail: true }),
  });
  if (!r.ok) throw new Error("generateIdToken " + r.status + ": " + (await r.text()));
  return (await r.json()).token;
}

// --- helpers ---
async function importPkcs8(pem) {
  const b = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const der = Uint8Array.from(atob(b), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey(
    "pkcs8",
    der.buffer,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

function b64urlJson(obj) {
  return b64url(new TextEncoder().encode(JSON.stringify(obj)));
}

function b64url(bytes) {
  let s = "";
  for (const byte of bytes) s += String.fromCharCode(byte);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
