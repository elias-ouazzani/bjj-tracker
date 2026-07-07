// Generate an RSA keypair for the Cloudflare Worker <-> GCP WIF trust.
//
// Produces two files:
//   priv.pem   — PKCS8 private key. Goes into the Worker as a SECRET
//                (`wrangler secret put PRIVATE_KEY_PEM`). Never commit it.
//   jwks.json  — public key as a JWKS. Uploaded to the GCP OIDC provider
//                (inline, via --jwk-json-path) so GCP can verify the Worker's
//                self-signed tokens. Safe to share; it's a public key.
//
// The Worker self-signs an OIDC JWT with priv.pem; GCP verifies it against
// jwks.json. No GCP-issued service-account key is ever created, so the org's
// iam.disableServiceAccountKeyCreation constraint never applies.
//
// Run in Cloud Shell (Node 18+ is preinstalled):  node gen-keys.mjs

import { webcrypto as crypto } from "node:crypto";
import { writeFileSync } from "node:fs";

const { publicKey, privateKey } = await crypto.subtle.generateKey(
  {
    name: "RSASSA-PKCS1-v1_5",
    modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]),
    hash: "SHA-256",
  },
  true,
  ["sign", "verify"],
);

// A stable key id so the Worker header (kid) matches the JWKS entry.
const kid = "strain-key-1";

// --- private key -> PKCS8 PEM (for the Worker secret) ---
const pkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", privateKey));
const b64 = Buffer.from(pkcs8).toString("base64").match(/.{1,64}/g).join("\n");
writeFileSync("priv.pem", `-----BEGIN PRIVATE KEY-----\n${b64}\n-----END PRIVATE KEY-----\n`);

// --- public key -> JWKS (for the GCP OIDC provider) ---
const jwk = await crypto.subtle.exportKey("jwk", publicKey);
jwk.kid = kid;
jwk.alg = "RS256";
jwk.use = "sig";
delete jwk.key_ops;
delete jwk.ext;
writeFileSync("jwks.json", JSON.stringify({ keys: [jwk] }, null, 2));

console.log("KID:", kid);
console.log("Wrote priv.pem  -> Worker secret  (wrangler secret put PRIVATE_KEY_PEM < priv.pem)");
console.log("Wrote jwks.json -> GCP provider    (--jwk-json-path=jwks.json)");
