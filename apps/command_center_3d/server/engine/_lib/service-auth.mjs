/**
 * JEV V4.3 - trusted service-to-service authentication.
 *
 * ZERO trust in Origin headers, browser cookies, or client-supplied identity.
 * Two independent trust anchors:
 *
 *   1. HMAC-SHA256 over (version.timestamp.nonce.actor.sha256(rawBody)) using
 *      a DEDICATED service key (NR_SERVICE_SIGNING_SECRET). Timestamp skew
 *      <= 300s, nonce replay protection persisted in D1.
 *   2. GitHub Actions OIDC ID token (RS256, JWKS verified, claim pinned to
 *      repository / ref / commit sha / workflow ref / audience).
 *
 * This key is deliberately NOT PAPER_LEDGER_SECRET (browser session signing)
 * and NOT JCC_CONTROL_TOKEN (a plain bearer token). Reusing either would let a
 * browser session or a UI control token forge "trusted runner" evidence.
 *
 * All failures are FAIL CLOSED: no secret, no signature, bad timestamp, used
 * nonce, unknown kid, wrong claim -> reject. Never fall back to an anonymous
 * or "local" trust mode implicitly.
 */
import { json } from "./paper-ledger.mjs";

export const MAX_SKEW_SECONDS = 300;
export const MAX_SERVICE_BODY_BYTES = 512_000;
const OIDC_ISSUER = "https://token.actions.githubusercontent.com";
const OIDC_JWKS = OIDC_ISSUER + "/.well-known/jwks";
const DEFAULT_REPOSITORY = "yihui315/night-researcher-dash";
const DEFAULT_AUDIENCE = "jev-strategy-verification";

let jwksCache = { at: 0, keys: [] };

function b64url(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function unb64url(value) {
  const padded = String(value).replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]
  );
}

/** Canonical string shared by signer and verifier. Exported for independent tests. */
export function canonicalServicePayload({ timestamp, nonce, actor, bodySha256 }) {
  return `v1.${timestamp}.${nonce}.${actor}.${bodySha256}`;
}

/** Build signed headers for an outbound trusted call. */
export async function signServiceBody(secret, actor, rawBody, opts = {}) {
  const timestamp = String(opts.timestamp ?? Math.floor(Date.now() / 1000));
  const nonce = opts.nonce ?? crypto.randomUUID();
  const bodySha256 = await sha256Hex(rawBody);
  const payload = canonicalServicePayload({ timestamp, nonce, actor, bodySha256 });
  const signature = await crypto.subtle.sign("HMAC", await hmacKey(secret), new TextEncoder().encode(payload));
  return {
    "X-JEV-Service-Actor": actor,
    "X-JEV-Service-Timestamp": timestamp,
    "X-JEV-Service-Nonce": nonce,
    "X-JEV-Service-Signature": "sha256=" + b64url(new Uint8Array(signature))
  };
}

function serviceKeyError() {
  return json({
    ok: false, code: "SERVICE_AUTH_NOT_CONFIGURED",
    error: "服务间签名密钥 NR_SERVICE_SIGNING_SECRET 未配置（至少 32 字符）；拒绝任何可信写入"
  }, 503);
}

/**
 * Consume a nonce exactly once. Returns true only when this call created the
 * row. Replays (or a concurrent duplicate) are rejected.
 */
export async function consumeNonce(db, nonce, actor, nowMs = Date.now()) {
  const at = new Date(nowMs).toISOString();
  const expiresAt = new Date(nowMs + MAX_SKEW_SECONDS * 1000).toISOString();
  // Full-suite regression: comparing created_at with `at` is NOT safe - two
  // requests inside the same millisecond produce the same ISO string and a
  // replay would be misclassified as the first use. Existence + the PRIMARY
  // KEY constraint are the real authority here.
  const existing = await db.prepare(
    "SELECT nonce FROM verification_nonces WHERE nonce = ?"
  ).bind(nonce).first();
  if (existing) return false;
  try {
    await db.prepare(
      "INSERT INTO verification_nonces (nonce, actor, created_at, expires_at) VALUES (?, ?, ?, ?)"
    ).bind(nonce, actor, at, expiresAt).run();
    return true;
  } catch (_) {
    // Concurrent duplicate: UNIQUE(nonce) decides, not a timestamp.
    return false;
  }
}

/**
 * Verify the HMAC trust anchor. `rawBody` MUST be the exact bytes that were
 * signed; never re-serialize parsed JSON before verifying.
 */
export async function verifyServiceSignature(request, env, rawBody, options = {}) {
  const secret = env && env.NR_SERVICE_SIGNING_SECRET;
  if (typeof secret !== "string" || secret.length < 32) {
    return { ok: false, response: serviceKeyError() };
  }
  const bodyBytes = new TextEncoder().encode(rawBody).byteLength;
  if (bodyBytes > MAX_SERVICE_BODY_BYTES) {
    return { ok: false, response: json({ ok: false, code: "BODY_TOO_LARGE", error: "可信请求体过大" }, 413) };
  }
  const actor = String(request.headers.get("X-JEV-Service-Actor") || "");
  const timestampRaw = String(request.headers.get("X-JEV-Service-Timestamp") || "");
  const nonce = String(request.headers.get("X-JEV-Service-Nonce") || "");
  const signature = String(request.headers.get("X-JEV-Service-Signature") || "");
  if (!/^[A-Za-z0-9._:-]{1,80}$/.test(actor) ||
      !/^\d{10}$/.test(timestampRaw) ||
      !/^[A-Za-z0-9._:-]{8,120}$/.test(nonce) ||
      !/^sha256=[A-Za-z0-9_-]{43,44}$/.test(signature)) {
    return { ok: false, response: json({ ok: false, code: "SERVICE_AUTH_MALFORMED", error: "服务签名头不完整或格式错误" }, 401) };
  }
  const timestamp = Number(timestampRaw);
  const nowSec = Math.floor((options.nowMs ?? Date.now()) / 1000);
  if (Math.abs(nowSec - timestamp) > MAX_SKEW_SECONDS) {
    return { ok: false, response: json({ ok: false, code: "SERVICE_AUTH_EXPIRED", error: "服务签名时间戳超出允许窗口" }, 401) };
  }
  const bodySha256 = await sha256Hex(rawBody);
  const payload = canonicalServicePayload({ timestamp: timestampRaw, nonce, actor, bodySha256 });
  let valid = false;
  try {
    valid = await crypto.subtle.verify(
      "HMAC", await hmacKey(secret), unb64url(signature.slice("sha256=".length)),
      new TextEncoder().encode(payload)
    );
  } catch (_) { valid = false; }
  if (!valid) {
    return { ok: false, response: json({ ok: false, code: "SERVICE_AUTH_BAD_SIGNATURE", error: "服务签名校验失败" }, 401) };
  }
  if (options.db) {
    const consumed = await consumeNonce(options.db, nonce, actor, options.nowMs ?? Date.now());
    if (!consumed) {
      return { ok: false, response: json({ ok: false, code: "SERVICE_AUTH_REPLAY", error: "签名 nonce 已被使用（重放请求）" }, 409) };
    }
  }
  return { ok: true, actor, authMethod: "hmac-service", timestamp: timestampRaw, nonce };
}

async function loadJwks(fetcher, nowMs) {
  if (jwksCache.keys.length && nowMs - jwksCache.at < 3_600_000) return jwksCache.keys;
  const response = await fetcher(OIDC_JWKS, { headers: { "cache-control": "no-cache", accept: "application/json" } });
  if (!response || !response.ok) throw new Error("OIDC_JWKS_UNAVAILABLE");
  const data = await response.json();
  const keys = Array.isArray(data.keys) ? data.keys : [];
  if (!keys.length) throw new Error("OIDC_JWKS_EMPTY");
  jwksCache = { at: nowMs, keys };
  return keys;
}

/**
 * Verify a GitHub Actions OIDC ID token (RS256). `expected` pins the claims
 * that a real research runner must present. Everything unverified -> reject.
 */
export async function verifyGithubOidc(token, env, expected = {}, options = {}) {
  if (typeof token !== "string" || token.length > 8192) {
    return { ok: false, error: "OIDC_TOKEN_MISSING" };
  }
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, error: "OIDC_TOKEN_MALFORMED" };
  let header, claims;
  try {
    header = JSON.parse(new TextDecoder().decode(unb64url(parts[0])));
    claims = JSON.parse(new TextDecoder().decode(unb64url(parts[1])));
  } catch (_) { return { ok: false, error: "OIDC_TOKEN_UNREADABLE" }; }
  if (!header || header.alg !== "RS256" || !header.kid) return { ok: false, error: "OIDC_ALG_UNSUPPORTED" };

  const nowSec = Math.floor((options.nowMs ?? Date.now()) / 1000);
  if (!Number.isFinite(claims.exp) || claims.exp <= nowSec) return { ok: false, error: "OIDC_TOKEN_EXPIRED" };
  if (!Number.isFinite(claims.iat) || claims.iat > nowSec + 60) return { ok: false, error: "OIDC_TOKEN_NOT_YET_VALID" };
  if (claims.iss !== OIDC_ISSUER) return { ok: false, error: "OIDC_ISSUER_MISMATCH" };

  const audience = String(env && env.NR_OIDC_AUDIENCE || DEFAULT_AUDIENCE);
  const audOk = Array.isArray(claims.aud) ? claims.aud.includes(audience) : claims.aud === audience;
  if (!audOk) return { ok: false, error: "OIDC_AUDIENCE_MISMATCH" };

  const repository = String(expected.repository || env && env.NR_OIDC_REPOSITORY || DEFAULT_REPOSITORY);
  if (claims.repository !== repository) return { ok: false, error: "OIDC_REPOSITORY_MISMATCH" };
  const allowedRefs = String(env && env.NR_OIDC_ALLOWED_REFS || "refs/heads/main")
    .split(",").map((s) => s.trim()).filter(Boolean);
  if (!allowedRefs.includes(String(claims.ref))) return { ok: false, error: "OIDC_REF_NOT_ALLOWED" };
  if (expected.commitSha && claims.sha !== expected.commitSha) return { ok: false, error: "OIDC_COMMIT_MISMATCH" };
  if (expected.workflowRef && claims.job_workflow_ref !== expected.workflowRef) {
    return { ok: false, error: "OIDC_WORKFLOW_MISMATCH" };
  }
  if (claims.runner_environment && claims.runner_environment !== "github-hosted") {
    return { ok: false, error: "OIDC_RUNNER_ENVIRONMENT_UNTRUSTED" };
  }

  let keys;
  try { keys = await loadJwks(options.fetcher || fetch, options.nowMs ?? Date.now()); }
  catch (_) { return { ok: false, error: "OIDC_JWKS_UNAVAILABLE" }; }
  const jwk = keys.find((k) => k.kid === header.kid);
  if (!jwk) return { ok: false, error: "OIDC_KID_UNKNOWN" };
  let verified = false;
  try {
    const key = await crypto.subtle.importKey(
      "jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]
    );
    verified = await crypto.subtle.verify(
      "RSASSA-PKCS1-v1_5", key, unb64url(parts[2]),
      new TextEncoder().encode(parts[0] + "." + parts[1])
    );
  } catch (_) { verified = false; }
  if (!verified) return { ok: false, error: "OIDC_SIGNATURE_INVALID" };

  return {
    ok: true, authMethod: "github-actions-oidc", claims,
    actor: String(claims.actor || ""),
    repository: claims.repository,
    workflowRef: String(claims.job_workflow_ref || ""),
    commitSha: String(claims.sha || ""),
    workflowRunId: String(claims.run_id || claims.workflow_run_id || "")
  };
}

export const __test = {
  canonicalServicePayload, sha256Hex, signServiceBody,
  verifyServiceSignature, verifyGithubOidc, consumeNonce,
  MAX_SKEW_SECONDS, DEFAULT_AUDIENCE, DEFAULT_REPOSITORY
};
