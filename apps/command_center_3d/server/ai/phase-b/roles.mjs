// JEV Phase B — C3: four-role separation (Owner/Approver/Executor/Auditor)
// with approver != executor enforcement and per-grant binding to
// user + action + strategyVersion + expiry.
//
// This module implements a SELF-CONTAINED, type-aware role-bound grant (its own
// canonicalization + HMAC) so it does NOT modify HUMAN_PUBLISH_GATE and the
// existing N1-N32 reverse tests stay green. The binding fields (approverId,
// executorId) are signed into the preimage so a grant cannot be replayed across
// identities or roles.
//
// MOCK / isolated design. Production C3 needs the four roles actually configured
// in the IdP / platform permission model and an isolated test environment.

import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import {
  issueIdToken, verifyIdToken, assertMfaSatisfied, IDENTITY_CODES,
} from "./identity.mjs";

export const ROLES = Object.freeze({
  OWNER: "OWNER",
  APPROVER: "APPROVER",
  EXECUTOR: "EXECUTOR",
  AUDITOR: "AUDITOR",
});

export const ROLE_BOUND_SCHEMA = "jev.phaseb.rolebound.grant/v1";

export const ROLE_CODES = Object.freeze({
  MISSING_ROLE: "ROLE_REQUIRED",
  SEPARATION_VIOLATION: "ACTOR_SEPARATION_VIOLATION",
  EXECUTOR_MISMATCH: "EXECUTOR_IDENTITY_MISMATCH",
  MFA_NOT_SATISFIED: IDENTITY_CODES.MFA_NOT_SATISFIED,
  BAD_SIGNATURE: "ROLEBOUND_SIGNATURE_INVALID",
  EXPIRED: "ROLEBOUND_EXPIRED",
  NOT_YET_VALID: "ROLEBOUND_NOT_YET_VALID",
  REPLAY: "ROLEBOUND_REPLAYED",
  REVOKED: "ROLEBOUND_REVOKED",
  SCOPE_MISMATCH: "ROLEBOUND_SCOPE_MISMATCH",
  CONTENT_MISMATCH: "ROLEBOUND_CONTENT_HASH_MISMATCH",
  SECRET_MISSING: "ROLEBOUND_SECRET_REQUIRED",
  INVALID_SCOPE: "ROLEBOUND_SCOPE_INVALID",
  FIELD_TYPE_INVALID: "ROLEBOUND_FIELD_TYPE_INVALID",
  STATE_REQUIRED: "ROLEBOUND_STATE_REQUIRED",
});

const SIGNED_STRING_FIELDS = Object.freeze([
  "grantId", "schema", "action", "strategyId", "strategyVersion",
  "contentHash", "nonce", "approverId", "executorId",
]);

/** Inject over JS values so the preimage is injective (mirrors core R3 fix). */
function encodeField(f) {
  const t = typeof f;
  let tag;
  let payload;
  if (t === "string") { tag = "s"; payload = f; }
  else if (t === "number") {
    if (Number.isNaN(f)) { tag = "x"; payload = "nan"; }
    else if (!Number.isFinite(f)) { tag = "i"; payload = f < 0 ? "neg-inf" : "pos-inf"; }
    else if (Object.is(f, -0)) { tag = "n"; payload = "neg-zero"; }
    else { tag = "n"; payload = String(f); }
  } else if (t === "boolean") { tag = "b"; payload = f ? "1" : "0"; }
  else if (t === "bigint") { tag = "B"; payload = f.toString(); }
  else if (f === null) { tag = "z"; payload = "null"; }
  else if (f === undefined) { tag = "u"; payload = "undefined"; }
  else { tag = "Y"; payload = Object.prototype.toString.call(f); }
  const token = tag + ":" + payload;
  return token.length + ":" + token;
}

function rbFields(g) {
  return [
    g.schema, g.grantId, g.action, g.strategyId, g.strategyVersion,
    g.contentHash, g.nonce, g.approverId, g.executorId,
    g.issuedAtMs, g.expiresAtMs,
  ];
}

function canonical(g) { return rbFields(g).map(encodeField).join("|"); }

function sign(g, secret) {
  return createHmac("sha256", String(secret)).update(canonical(g), "utf8").digest("hex");
}

function validScope({ strategyId, strategyVersion, contentHash }) {
  return typeof strategyId === "string" && strategyId.length > 0 &&
    typeof strategyVersion === "string" && strategyVersion.length > 0 &&
    typeof contentHash === "string" && /^[a-f0-9]{64}$/.test(contentHash);
}

export function validateRoleBoundTypes(g) {
  if (!g || typeof g !== "object") return false;
  for (const k of SIGNED_STRING_FIELDS) {
    if (typeof g[k] !== "string") return false;
  }
  if (typeof g.issuedAtMs !== "number" || !Number.isFinite(g.issuedAtMs)) return false;
  if (typeof g.expiresAtMs !== "number" || !Number.isFinite(g.expiresAtMs)) return false;
  return true;
}

function safeHexEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  return timingSafeEqual(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

export function hasRole(claims, role) {
  return !!claims && Array.isArray(claims.roles) && claims.roles.includes(role);
}

export function requireRole(claims, role) {
  if (!hasRole(claims, role)) return { ok: false, code: ROLE_CODES.MISSING_ROLE, detail: role };
  return { ok: true };
}

/** approverId and executorId must be distinct real identities. */
export function enforceApproverExecutorSeparation({ approverId, executorId }) {
  if (approverId && executorId && approverId === executorId) {
    return { ok: false, code: ROLE_CODES.SEPARATION_VIOLATION, detail: { approverId, executorId } };
  }
  return { ok: true };
}

/**
 * Issue a role-bound grant. Only an APPROVER (with MFA) may issue, and the
 * approver must not be the same identity as the executor.
 */
export function issueRoleBoundGrant({
  approverClaims, action, strategyId, strategyVersion, contentHash,
  executorId, ttlMs = 300000, nowMs, secret, nonce,
}) {
  const fail = (code, detail) => ({ ok: false, code, detail: detail || null });
  if (typeof secret !== "string" || secret.length < 16) return fail(ROLE_CODES.SECRET_MISSING);
  if (!assertMfaSatisfied(approverClaims)) return fail(ROLE_CODES.MFA_NOT_SATISFIED);
  const r = requireRole(approverClaims, ROLES.APPROVER);
  if (!r.ok) return fail(r.code, r.detail);
  if (!validScope({ strategyId, strategyVersion, contentHash })) return fail(ROLE_CODES.INVALID_SCOPE);
  if (!Number.isFinite(nowMs)) return fail(ROLE_CODES.INVALID_SCOPE, "nowMs required");
  if (!Number.isFinite(ttlMs) || ttlMs <= 0 || ttlMs > 86400000) return fail(ROLE_CODES.INVALID_SCOPE, "ttlMs out of range");
  if (typeof executorId !== "string" || executorId.length === 0) return fail(ROLE_CODES.INVALID_SCOPE, "executorId required");
  const approverId = approverClaims.sub;
  const sep = enforceApproverExecutorSeparation({ approverId, executorId });
  if (!sep.ok) return sep;

  const grant = {
    schema: ROLE_BOUND_SCHEMA,
    grantId: "rbg-" + randomUUID(),
    action,
    strategyId: String(strategyId),
    strategyVersion: String(strategyVersion),
    contentHash: String(contentHash),
    nonce: String(nonce || randomUUID()),
    approverId,
    executorId,
    issuedAtMs: nowMs,
    expiresAtMs: nowMs + ttlMs,
  };
  if (!validateRoleBoundTypes(grant)) return fail(ROLE_CODES.FIELD_TYPE_INVALID);
  grant.signature = sign(grant, secret);
  return { ok: true, code: "ROLEBOUND_ISSUED", grant: Object.freeze(grant) };
}

/**
 * Verify + consume a role-bound grant. The executor identity (executorClaims.sub)
 * must match the bound executorId, must hold the EXECUTOR role, and the
 * approver/executor separation must still hold. Nonce consumption and
 * revocation are delegated to a D1-backed ledger (cross-instance safe).
 */
export function verifyRoleBoundGrant({
  grant, executorClaims, action, strategyId, strategyVersion, contentHash,
  nowMs, secret, ledger,
}) {
  const deny = (code, detail) => ({ ok: false, code, detail: detail || null });
  if (typeof secret !== "string" || secret.length < 16) return deny(ROLE_CODES.SECRET_MISSING);
  if (!grant || typeof grant !== "object" || grant.schema !== ROLE_BOUND_SCHEMA) return deny(ROLE_CODES.BAD_SIGNATURE);
  if (!ledger) return deny(ROLE_CODES.STATE_REQUIRED);
  if (!assertMfaSatisfied(executorClaims)) return deny(ROLE_CODES.MFA_NOT_SATISFIED);
  const ex = requireRole(executorClaims, ROLES.EXECUTOR);
  if (!ex.ok) return deny(ex.code, ex.detail);
  if (!validateRoleBoundTypes(grant)) return deny(ROLE_CODES.FIELD_TYPE_INVALID);
  if (!safeHexEqual(grant.signature, sign(grant, secret))) return deny(ROLE_CODES.BAD_SIGNATURE);
  if (!Number.isFinite(nowMs)) return deny(ROLE_CODES.INVALID_SCOPE, "nowMs required");
  if (nowMs < grant.issuedAtMs) return deny(ROLE_CODES.NOT_YET_VALID);
  if (nowMs >= grant.expiresAtMs) return deny(ROLE_CODES.EXPIRED);
  if (executorClaims.sub !== grant.executorId) return deny(ROLE_CODES.EXECUTOR_MISMATCH, { expected: grant.executorId, actual: executorClaims.sub });
  const sep = enforceApproverExecutorSeparation({ approverId: grant.approverId, executorId: grant.executorId });
  if (!sep.ok) return deny(sep.code, sep.detail);
  if (action !== grant.action || String(strategyId) !== grant.strategyId || String(strategyVersion) !== grant.strategyVersion) {
    return deny(ROLE_CODES.SCOPE_MISMATCH);
  }
  if (contentHash !== grant.contentHash) return deny(ROLE_CODES.CONTENT_MISMATCH);

  // Cross-instance safe: atomic nonce consumption via D1 ledger.
  const consumed = ledger.consumeNonce({
    nonce: grant.nonce, grantId: grant.grantId,
    actorHash: "rb:" + grant.executorId, strategyId: grant.strategyId, nowMs,
  });
  if (!consumed.ok) return deny(ROLE_CODES.REPLAY, grant.nonce);

  // Cross-instance safe: revocation via D1 ledger.
  if (ledger.isRevoked(grant.grantId)) return deny(ROLE_CODES.REVOKED, grant.grantId);

  return {
    ok: true, code: "ROLEBOUND_ALLOWED",
    scope: Object.freeze({
      action: grant.action, strategyId: grant.strategyId, strategyVersion: grant.strategyVersion,
      contentHash: grant.contentHash, approverId: grant.approverId, executorId: grant.executorId,
      grantId: grant.grantId, nonce: grant.nonce,
    }),
  };
}

/** Helper for tests / integration: mint a fresh mock IdP token for a subject. */
export function mintActorToken({ sub, roles, idpSecret, nowMs, ttlMs = 900000, amr }) {
  return issueIdToken({ sub, roles, amr, issuerSecret: idpSecret, nowMs, ttlMs });
}

/** Re-verify a raw token (used by integration to recover claims). */
export function recoverClaims(token, idpSecret, nowMs) {
  return verifyIdToken(token, { issuerSecret: idpSecret, nowMs });
}
