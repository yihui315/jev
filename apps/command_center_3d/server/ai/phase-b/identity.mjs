// JEV Phase B — C2: per-user IdP identity + MFA (amr) verification + server
// trusted identity injection.
//
// MOCK IdP ONLY. This module simulates an OIDC/SAML IdP with an HMAC-signed
// identity token. It is NOT a production IdP integration. Per the Phase B
// design doc (D:/数据分析/jev_v3_p0_baseline/phase_b_security_design.md §2),
// production C2 requires a real IdP provider + credentials + an isolated test
// environment — all of which are external-authorization items.
//
// HARD INVARIANTS preserved from HUMAN_PUBLISH_GATE:
//   * The trusted actor is ONLY ever produced by makeServerActor() — a client
//     JSON body can never carry the module-private Symbol, so a client-supplied
//     actor is always rejected by assertServerActor().
//   * Identity is derived SOLELY from a server-verified IdP token; the request
//     body never supplies the actor or the claims.
//   * MFA is mandatory: amr MUST contain "mfa", otherwise the identity is
//     rejected (maps to the existing INVALID_ACTOR fail-closed path).
//   * Identity expiry and revocation are enforced server-side.
//
// NEVER mark mock results as "production security PASS".

import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { makeServerActor, assertServerActor, GATE_CODES } from "../human-publish-gate.mjs";

export const REQUIRED_AMR = Object.freeze(["mfa"]);

export const IDENTITY_CODES = Object.freeze({
  TOKEN_REQUIRED: "IDENTITY_TOKEN_REQUIRED",
  TOKEN_MALFORMED: "IDENTITY_TOKEN_MALFORMED",
  TOKEN_INVALID_SIGNATURE: "IDENTITY_TOKEN_INVALID_SIGNATURE",
  TOKEN_EXPIRED: "IDENTITY_TOKEN_EXPIRED",
  TOKEN_MISSING_SUBJECT: "IDENTITY_TOKEN_MISSING_SUBJECT",
  MFA_NOT_SATISFIED: "IDENTITY_MFA_NOT_SATISFIED",
  IDENTITY_REVOKED: "IDENTITY_REVOKED",
  INVALID_ACTOR: GATE_CODES.INVALID_ACTOR,
});

function b64urlEncode(obj) {
  return Buffer.from(JSON.stringify(obj), "utf8").toString("base64url");
}

function b64urlDecode(s) {
  return JSON.parse(Buffer.from(s, "base64url").toString("utf8"));
}

function safeB64Equal(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  return timingSafeEqual(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

/**
 * MOCK IdP token issuance (HMAC-signed compact JWT shape).
 * Claims: { sub, amr, iss, iat, exp, jti, roles }.
 */
export function issueIdToken({
  sub, amr = REQUIRED_AMR.slice(), roles = [], issuerSecret, nowMs, ttlMs = 900000,
}) {
  if (typeof issuerSecret !== "string" || issuerSecret.length < 16) {
    throw new TypeError("IDP_SECRET_REQUIRED");
  }
  if (typeof sub !== "string" || sub.length === 0) throw new TypeError("SUB_REQUIRED");
  if (!Number.isFinite(nowMs)) throw new TypeError("NOW_MS_REQUIRED");
  const header = { alg: "HS256", typ: "JWT" };
  const claims = {
    sub,
    amr: Array.isArray(amr) ? amr.slice() : [],
    roles: Array.isArray(roles) ? roles.slice() : [],
    iss: "mock-idp.local",
    iat: nowMs,
    exp: nowMs + (Number.isFinite(ttlMs) && ttlMs > 0 ? ttlMs : 900000),
    jti: "jit-" + randomUUID(),
  };
  const h = b64urlEncode(header);
  const p = b64urlEncode(claims);
  const sig = createHmac("sha256", issuerSecret).update(h + "." + p, "utf8").digest("base64url");
  return h + "." + p + "." + sig;
}

/**
 * Verify a MOCK IdP token. Returns { ok, claims, code }. Fail-closed on every
 * malformation: bad signature, expiry, missing subject, or missing MFA.
 */
export function verifyIdToken(token, { issuerSecret, nowMs }) {
  if (typeof token !== "string" || token.length === 0) {
    return { ok: false, code: IDENTITY_CODES.TOKEN_REQUIRED };
  }
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, code: IDENTITY_CODES.TOKEN_MALFORMED };
  const [h, p, s] = parts;
  let claims;
  try {
    claims = b64urlDecode(p);
  } catch {
    return { ok: false, code: IDENTITY_CODES.TOKEN_MALFORMED };
  }
  if (typeof issuerSecret !== "string") {
    return { ok: false, code: IDENTITY_CODES.TOKEN_INVALID_SIGNATURE };
  }
  const expected = createHmac("sha256", issuerSecret).update(h + "." + p, "utf8").digest("base64url");
  if (!safeB64Equal(s, expected)) {
    return { ok: false, code: IDENTITY_CODES.TOKEN_INVALID_SIGNATURE };
  }
  if (!claims || typeof claims.sub !== "string" || claims.sub.length === 0) {
    return { ok: false, code: IDENTITY_CODES.TOKEN_MISSING_SUBJECT };
  }
  if (!Number.isFinite(nowMs)) {
    return { ok: false, code: IDENTITY_CODES.TOKEN_MALFORMED, detail: "nowMs required" };
  }
  if (Number.isFinite(claims.exp) && nowMs >= claims.exp) {
    return { ok: false, code: IDENTITY_CODES.TOKEN_EXPIRED, detail: { exp: claims.exp, nowMs } };
  }
  if (!Array.isArray(claims.amr) || !REQUIRED_AMR.every((m) => claims.amr.includes(m))) {
    return { ok: false, code: IDENTITY_CODES.MFA_NOT_SATISFIED, detail: { amr: claims.amr || [] } };
  }
  return { ok: true, claims };
}

/** True when the server-verified claims record a satisfied MFA challenge. */
export function assertMfaSatisfied(claims) {
  return !!claims && Array.isArray(claims.amr) && REQUIRED_AMR.every((m) => claims.amr.includes(m));
}

/**
 * Server-trusted identity injection. The actor is built ONLY from a verified
 * IdP token; the request body is never consulted. A client-supplied actor /
 * token is rejected fail-closed.
 */
export function buildTrustedIdentity({ idToken, idpSecret, nowMs, revocationStore = null }) {
  const v = verifyIdToken(idToken, { issuerSecret: idpSecret, nowMs });
  if (!v.ok) return { ok: false, code: v.code, detail: v.detail || null };
  const claims = v.claims;
  if (revocationStore && revocationStore.isRevoked(claims)) {
    return { ok: false, code: IDENTITY_CODES.IDENTITY_REVOKED, detail: claims.sub };
  }
  const actor = makeServerActor({
    authenticatedUserId: claims.sub,
    authMethod: "IDP_OIDC_MFA",
    nowMs,
  });
  return { ok: true, actor, claims };
}

/** In-memory IdP identity revocation list (sub or jti based). MOCK only. */
export class IdentityRevocationStore {
  #revoked = new Set();

  revokeSub(sub) { this.#revoked.add("sub:" + String(sub)); }
  revokeJti(jti) { this.#revoked.add("jti:" + String(jti)); }
  isRevoked(claims) {
    if (!claims || typeof claims !== "object") return false;
    if (this.#revoked.has("sub:" + claims.sub)) return true;
    if (claims.jti && this.#revoked.has("jti:" + claims.jti)) return true;
    return false;
  }
  get size() { return this.#revoked.size; }
}

/**
 * Cross-instance IdP identity revocation list. Takes a SHARED Map (mirroring how
 * AuthorizationLedger takes a D1SharedStore) so a revocation on worker A is
 * immediately visible on worker B. This is the production-correct variant; the
 * in-memory IdentityRevocationStore above is single-instance/mock only.
 */
export class SharedIdentityRevocationStore {
  /**
   * @param {Map<string,true>} sharedMap a Map shared by every instance
   *   (e.g. backed by D1 / a durable KV in production).
   */
  constructor(sharedMap) {
    if (!sharedMap || typeof sharedMap.set !== "function") {
      throw new TypeError("SharedIdentityRevocationStore requires a shared Map");
    }
    this.store = sharedMap;
  }

  revokeSub(sub) { this.store.set("sub:" + String(sub), true); }
  revokeJti(jti) { this.store.set("jti:" + String(jti), true); }
  isRevoked(claims) {
    if (!claims || typeof claims !== "object") return false;
    if (this.store.has("sub:" + claims.sub)) return true;
    if (claims.jti && this.store.has("jti:" + claims.jti)) return true;
    return false;
  }
  get size() { return this.store.size; }
}

/**
 * True only when the actor was produced by the server auth layer. A
 * client-supplied plain object (no module-private Symbol) returns false.
 */
export function isTrustedActor(actor) {
  return assertServerActor(actor) === true;
}
