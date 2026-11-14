// JEV Phase A / P0-A — HUMAN_PUBLISH_GATE (server-side, fail-closed).
//
// Ruling from the owner (2026-11-12): a REAL, identity-bound human approval is
// mandatory for strategy promotion, external publish, production deploy and
// real-trading authorization. Shadow / Paper internal simulation stays
// unattended. An auto-derived `confirmationId` is NOT a human approval.
//
// HARD INVARIANTS (each has a named reverse test in
// scripts/tests/human-publish-gate.test.mjs):
//   1. The actor is manufactured ONLY by the server auth layer. Its authenticity
//      is carried by a module-private Symbol that no JSON body can ever carry.
//      A client-supplied `approved` / `approvedBy` / `confirmationId` is not
//      ignored — it is an explicit DENY (a front-end button cannot replace the
//      server-side check).
//   2. The content hash is computed by the SERVER from the actual content, so
//      reusing an old grant after editing the strategy cannot pass.
//   3. Every grant is HMAC-signed, scoped to actor + action + strategy +
//      version + content hash, time-boxed, single-use (nonce), and revocable.
//   4. Every ALLOW and every DENY is written to an append-only, hash-chained
//      audit log. There is no update, delete or clear method.
//   5. Unknown actions fail closed. PAPER_EXECUTION / SHADOW_EXECUTION are
//      explicitly ungated, which is what keeps Paper Trading unattended.
//
// P0 LIMITATIONS (must not be overclaimed): the audit log and nonce/revocation
// sets are in-process. Cross-instance replay protection needs a durable store
// (D1) and is NOT implemented here; it is a P1 item. The staging identity is
// the Basic-auth principal from JEV_PREVIEW_USER, i.e. a real authenticated
// identity but a SHARED one — per-user IdP / MFA is NOT implemented.
// This module performs NO real trading, NO deployment and NO network call.

import { createHash, createHmac, timingSafeEqual, randomUUID } from "node:crypto";

export const GATE_NAME = "HUMAN_PUBLISH_GATE";
export const GATE_VERSION = "jev.human.publish.grant/v1";

export const PROTECTED_ACTIONS = Object.freeze([
  "STRATEGY_PROMOTION",
  "EXTERNAL_PUBLISH",
  "PRODUCTION_DEPLOY",
  "REAL_TRADING_AUTHORIZATION",
]);

export const UNATTENDED_ACTIONS = Object.freeze([
  "SHADOW_EXECUTION",
  "PAPER_EXECUTION",
]);

export const GATE_CODES = Object.freeze({
  OK: "HUMAN_GATE_OK",
  NO_GRANT: "HUMAN_GRANT_REQUIRED",
  CLIENT_APPROVAL: "CLIENT_SUPPLIED_APPROVAL_REJECTED",
  INVALID_ACTOR: "IDENTITY_NOT_SERVER_AUTHENTICATED",
  ACTOR_MISMATCH: "IDENTITY_MISMATCH",
  BAD_SIGNATURE: "GRANT_SIGNATURE_INVALID",
  EXPIRED: "GRANT_EXPIRED",
  NOT_YET_VALID: "GRANT_NOT_YET_VALID",
  REPLAY: "GRANT_REPLAYED",
  REVOKED: "GRANT_REVOKED",
  SCOPE_MISMATCH: "GRANT_SCOPE_MISMATCH",
  CONTENT_MISMATCH: "CONTENT_HASH_MISMATCH",
  SECRET_MISSING: "GATE_SECRET_REQUIRED",
  UNKNOWN_ACTION: "ACTION_UNKNOWN_FAIL_CLOSED",
  INVALID_SCOPE: "GRANT_SCOPE_INVALID",
  MALFORMED_SCOPE: "GRANT_SCOPE_MALFORMED_UTF16",
  MALFORMED_FIELD_TYPE: "GRANT_FIELD_TYPE_INVALID",
  STATE_REQUIRED: "GATE_STATE_REQUIRED",
  AUDIT_REQUIRED: "GATE_AUDIT_REQUIRED",
  UNGATED: "UNGATED_UNATTENDED",
});

const SERVER_ACTOR_TAG = Symbol("jev.server.authenticated.actor");

const CLIENT_APPROVAL_FIELDS = Object.freeze([
  "approved", "humanApproved", "approvedBy", "humanApproval",
  "confirmationId", "grantOverride", "skipGate", "bypassHumanGate",
]);

function sha256hex(text) {
  return createHash("sha256").update(String(text), "utf8").digest("hex");
}

/** Unknown / unrecognised actions are gated (i.e. denied) — fail closed. */
export function isGateRequired(action) {
  if (UNATTENDED_ACTIONS.includes(action)) return false;
  if (PROTECTED_ACTIONS.includes(action)) return true;
  return true;
}

export function isKnownAction(action) {
  return PROTECTED_ACTIONS.includes(action) || UNATTENDED_ACTIONS.includes(action);
}

/**
 * The ONLY way to produce a valid actor. Must be called by the server after
 * its own authentication succeeded; never pass a value from a request body.
 */
export function makeServerActor({ authenticatedUserId, authMethod = "SERVER_BASIC_AUTH", nowMs }) {
  if (typeof authenticatedUserId !== "string" || authenticatedUserId.length === 0) {
    throw new TypeError("SERVER_ACTOR_USER_REQUIRED");
  }
  if (!Number.isFinite(nowMs)) throw new TypeError("SERVER_ACTOR_TIME_REQUIRED");
  const actor = {
    userId: authenticatedUserId,
    authMethod: String(authMethod),
    authenticatedAtMs: nowMs,
    identityHash: sha256hex(authenticatedUserId + "|" + String(authMethod)),
    source: "SERVER_AUTH_LAYER",
  };
  Object.defineProperty(actor, SERVER_ACTOR_TAG, {
    value: true, enumerable: false, writable: false, configurable: false,
  });
  return Object.freeze(actor);
}

export function assertServerActor(actor) {
  return !!actor && typeof actor === "object" &&
    actor[SERVER_ACTOR_TAG] === true &&
    actor.source === "SERVER_AUTH_LAYER" &&
    typeof actor.userId === "string" && actor.userId.length > 0 &&
    typeof actor.identityHash === "string" && actor.identityHash.length === 64 &&
    Number.isFinite(actor.authenticatedAtMs);
}

/** Return the first client-supplied approval field present, or null. */
export function findClientSuppliedApproval(body) {
  if (!body || typeof body !== "object") return null;
  for (const k of CLIENT_APPROVAL_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(body, k)) return k;
  }
  return null;
}

/**
 * Append-only, hash-chained audit log. Deliberately exposes NO mutation API:
 * no delete, no clear, no update, no index setter. verifyChain() detects any
 * forced in-place tampering with a stored entry.
 */
export class AppendOnlyAuditLog {
  #entries = [];

  constructor() { Object.freeze(this); }

  append(record) {
    if (!record || typeof record !== "object") throw new TypeError("AUDIT_RECORD_INVALID");
    const seq = this.#entries.length;
    const prevHash = seq === 0 ? "GENESIS" : this.#entries[seq - 1].hash;
    const payload = JSON.stringify({
      seq,
      atMs: Number.isFinite(record.atMs) ? record.atMs : null,
      type: String(record.type || "UNSPECIFIED"),
      code: String(record.code || ""),
      action: record.action ?? null,
      strategyId: record.strategyId ?? null,
      version: record.version ?? null,
      contentHash: record.contentHash ?? null,
      actorHash: record.actorHash ?? null,
      authMethod: record.authMethod ?? null,
      detail: record.detail ?? null,
    });
    const entry = Object.freeze({
      seq, prevHash, payload,
      hash: sha256hex(prevHash + "|" + payload),
    });
    this.#entries.push(entry);
    return entry;
  }

  get length() { return this.#entries.length; }

  entries() {
    return Object.freeze(this.#entries.map((e) => Object.freeze({
      seq: e.seq, prevHash: e.prevHash, hash: e.hash, record: JSON.parse(e.payload),
    })));
  }

  verifyChain() {
    for (let i = 0; i < this.#entries.length; i++) {
      const e = this.#entries[i];
      const prev = i === 0 ? "GENESIS" : this.#entries[i - 1].hash;
      if (e.seq !== i || e.prevHash !== prev || e.hash !== sha256hex(prev + "|" + e.payload)) {
        return false;
      }
    }
    return true;
  }
}

/**
 * Type-aware, length-prefixed HMAC preimage — well-formed UTF-16 only, and
 * INJECTIVE over JavaScript values (not merely over strings).
 *
 * Round 1 (Aegis, 2026-11-12): old `fields.join("|")` let (A,"B|C") and
 * ("A|B",C) collide. Length prefixes fixed the BOUNDARY ambiguity.
 *
 * Round 2 (Aegis recheck): boundary uniqueness was necessary but not
 * sufficient — `createHmac.update(str,"utf8")` folds every malformed lone
 * surrogate AND U+FFFD into the same bytes, so a U+FFFD grant could be
 * re-labelled to a lone surrogate with an identical preimage. Fix: REJECT
 * non-well-formed UTF-16 in every signed field before the HMAC is computed or
 * compared. Do NOT normalise with toWellFormed() — that keeps the two strings
 * equivalent and leaves the attack working.
 *
 * Round 3 (Aegis recheck R2): even with well-formed UTF-16, `String(f)` is NOT
 * injective over JS values. String("1") === String(1) === "1", and the same
 * holds for null/"null", true/"true", [1,2]/"1,2", {}/"[object Object]". A
 * grant minted for nonce "1" could therefore be replayed with nonce 1 (a
 * number): the preimage was byte-identical, the signature matched, and the
 * replay Set keys by RAW value so "1" and 1 are different keys — a secret-free
 * single-use bypass. Two complementary fixes:
 *   (a) `validateGrantTypes()` enforces the exact JS type of every signed
 *       field at BOTH issuance and verification (fail closed). A client-
 *       supplied non-string nonce is rejected outright, before the HMAC.
 *   (b) `encodeField()` tags each field with its JS type, so the HMAC
 *       preimage is injective over values: "1" -> `s:1`, 1 -> `n:1`. Even if
 *       validation were bypassed, the signature would not be.
 * P0 has no persisted grants, so tightening validation invalidates nothing.
 */
function grantFields(g) {
  return [
    GATE_VERSION, g.grantId, g.actorHash, g.actorUserId, g.authMethod,
    g.action, g.strategyId, g.strategyVersion, g.contentHash, g.nonce,
    g.issuedAtMs, g.expiresAtMs,
  ];
}

/** Stdlib-only well-formed UTF-16 check (no String.isWellFormed dependency). */
function isWellFormedUtf16(s) {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xD800 && c <= 0xDBFF) {
      const next = s.charCodeAt(i + 1);
      if (!(next >= 0xDC00 && next <= 0xDFFF)) return false;
      i++;
    } else if (c >= 0xDC00 && c <= 0xDFFF) {
      return false;
    }
  }
  return true;
}

/** True when ANY signed STRING field contains a malformed UTF-16 code unit. */
export function hasMalformedUtf16(g) {
  return grantFields(g || {}).some(
    (f) => typeof f === "string" && !isWellFormedUtf16(f),
  );
}

/**
 * Encode a single field with an explicit type tag so the HMAC preimage is
 * injective over JavaScript values. Two values that String()-coerce to the
 * same text but have different JS types produce DIFFERENT tokens.
 */
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

function canonicalGrant(g) {
  return grantFields(g).map(encodeField).join("|");
}

/**
 * The grant contract: every signed field has an exact JS type. strategyId,
 * strategyVersion, contentHash, nonce and the identity fields MUST be strings;
 * issuedAtMs / expiresAtMs MUST be finite numbers. Any deviation is rejected
 * fail-closed (Aegis recheck R2 — secret-free single-use bypass via
 * String()-equivalent but type-distinct field values).
 */
const SIGNED_STRING_FIELDS = Object.freeze([
  "grantId", "actorHash", "actorUserId", "authMethod",
  "action", "strategyId", "strategyVersion", "contentHash", "nonce",
]);

export function validateGrantTypes(g) {
  if (!g || typeof g !== "object") return false;
  for (const k of SIGNED_STRING_FIELDS) {
    if (typeof g[k] !== "string") return false;
  }
  if (typeof g.issuedAtMs !== "number" || !Number.isFinite(g.issuedAtMs)) return false;
  if (typeof g.expiresAtMs !== "number" || !Number.isFinite(g.expiresAtMs)) return false;
  return true;
}

function signGrant(g, secret) {
  return createHmac("sha256", String(secret)).update(canonicalGrant(g), "utf8").digest("hex");
}

/** Aegis HARD CONDITION 6: omitting state/audit must fail closed, never silently degrade. */
export function isGateStateValid(state) {
  return !!state &&
    !!state.consumedNonces && typeof state.consumedNonces.has === "function" &&
    typeof state.consumedNonces.add === "function" &&
    !!state.revokedGrantIds && typeof state.revokedGrantIds.has === "function";
}

export function isAuditValid(audit) {
  return !!audit && typeof audit.append === "function" && typeof audit.verifyChain === "function";
}

function validScope({ strategyId, version, contentHash }) {
  return typeof strategyId === "string" && strategyId.length > 0 &&
    (typeof version === "string" || Number.isFinite(version)) && String(version).length > 0 &&
    typeof contentHash === "string" && /^[a-f0-9]{64}$/.test(contentHash);
}

export function issuePublishGrant({
  actor, action, strategyId, version, contentHash,
  ttlMs = 300000, nowMs, secret, nonce, audit,
}) {
  const fail = (code, detail) => {
    if (audit) audit.append({ atMs: nowMs, type: "GRANT_ISSUE_DENIED", code, action, detail });
    return { ok: false, code, detail: detail || null };
  };
  if (typeof secret !== "string" || secret.length < 16) return fail(GATE_CODES.SECRET_MISSING);
  if (!assertServerActor(actor)) return fail(GATE_CODES.INVALID_ACTOR);
  if (!isKnownAction(action) || !isGateRequired(action)) return fail(GATE_CODES.UNKNOWN_ACTION);
  if (!validScope({ strategyId, version, contentHash })) return fail(GATE_CODES.INVALID_SCOPE);
  if (!Number.isFinite(nowMs)) return fail(GATE_CODES.INVALID_SCOPE, "nowMs required");
  if (!Number.isFinite(ttlMs) || ttlMs <= 0 || ttlMs > 86400000) return fail(GATE_CODES.INVALID_SCOPE, "ttlMs out of range");

  const grant = {
    grantId: "hpg-" + randomUUID(),
    schema: GATE_VERSION,
    action,
    strategyId: String(strategyId),
    strategyVersion: String(version),
    contentHash: String(contentHash),
    nonce: String(nonce || randomUUID()),
    issuedAtMs: nowMs,
    expiresAtMs: nowMs + ttlMs,
    actorHash: actor.identityHash,
    actorUserId: actor.userId,
    authMethod: actor.authMethod,
  };
  // Never sign a preimage whose UTF-8 encoding is ambiguous.
  if (hasMalformedUtf16(grant)) {
    return fail(GATE_CODES.MALFORMED_SCOPE, "malformed UTF-16 in signed grant fields");
  }
  // Enforce the exact JS type of every signed field (Aegis recheck R2 — the
  // issuer always coerces to the contract types, so this is belt-and-suspenders
  // here, but it makes the invariant self-documenting and future-proof).
  if (!validateGrantTypes(grant)) {
    return fail(GATE_CODES.MALFORMED_FIELD_TYPE, "signed field types violated at issuance");
  }
  grant.signature = signGrant(grant, secret);
  if (audit) audit.append({
    atMs: nowMs, type: "GRANT_ISSUED", code: GATE_CODES.OK, action,
    strategyId: grant.strategyId, version: grant.strategyVersion,
    contentHash: grant.contentHash, actorHash: grant.actorHash,
    authMethod: grant.authMethod, detail: grant.grantId,
  });
  return { ok: true, code: GATE_CODES.OK, grant: Object.freeze(grant) };
}

function safeHexEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  return timingSafeEqual(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

export function verifyPublishGrant({
  grant, actor, action, strategyId, version, contentHash,
  nowMs, secret, state, audit,
}) {
  const deny = (code, detail) => {
    if (audit) audit.append({
      atMs: nowMs, type: "GATE_DENIED", code, action,
      strategyId: strategyId ?? null, version: version ?? null,
      contentHash: contentHash ?? null,
      actorHash: assertServerActor(actor) ? actor.identityHash : null,
      authMethod: assertServerActor(actor) ? actor.authMethod : null,
      detail: detail || null,
    });
    return { ok: false, code, detail: detail || null };
  };

  if (typeof secret !== "string" || secret.length < 16) return deny(GATE_CODES.SECRET_MISSING);
  if (!assertServerActor(actor)) return deny(GATE_CODES.INVALID_ACTOR);
  if (!grant || typeof grant !== "object" || grant.schema !== GATE_VERSION) return deny(GATE_CODES.NO_GRANT);
  // Fail closed when the replay/revocation store is missing. The old code used
  // `if (state && state.consumedNonces)` guards, which silently degraded into
  // "replay protection off" when a caller forgot to pass state (Aegis R4).
  if (!isGateStateValid(state)) return deny(GATE_CODES.STATE_REQUIRED);
  if (state.revokedGrantIds.has(grant.grantId)) return deny(GATE_CODES.REVOKED, grant.grantId);
  // Enforce the exact JS type of every signed field BEFORE the HMAC is touched
  // (Aegis recheck R2). A client-supplied non-string nonce (e.g. number 1 where
  // the issued grant had string "1") is rejected here, closing the secret-free
  // single-use bypass at the boundary regardless of the signature.
  if (!validateGrantTypes(grant)) {
    return deny(GATE_CODES.MALFORMED_FIELD_TYPE, "signed field types violated");
  }
  // Reject malformed UTF-16 BEFORE computing/comparing the HMAC. Otherwise
  // Node folds the lone surrogate into U+FFFD and the tampered grant hashes
  // to the same bytes as a legitimately minted one (Aegis recheck round 2).
  if (hasMalformedUtf16(grant)) {
    return deny(GATE_CODES.MALFORMED_SCOPE, "malformed UTF-16 in signed grant fields");
  }
  if (!safeHexEqual(grant.signature, signGrant(grant, secret))) return deny(GATE_CODES.BAD_SIGNATURE);
  if (!Number.isFinite(nowMs)) return deny(GATE_CODES.INVALID_SCOPE, "nowMs required");
  if (nowMs < grant.issuedAtMs) return deny(GATE_CODES.NOT_YET_VALID);
  if (nowMs >= grant.expiresAtMs) return deny(GATE_CODES.EXPIRED);
  if (state && state.consumedNonces && state.consumedNonces.has(grant.nonce)) return deny(GATE_CODES.REPLAY, grant.nonce);
  if (actor.identityHash !== grant.actorHash || actor.userId !== grant.actorUserId ||
      actor.authMethod !== grant.authMethod) return deny(GATE_CODES.ACTOR_MISMATCH);
  if (action !== grant.action || String(strategyId) !== grant.strategyId ||
      String(version) !== grant.strategyVersion) return deny(GATE_CODES.SCOPE_MISMATCH);
  if (contentHash !== grant.contentHash) return deny(GATE_CODES.CONTENT_MISMATCH);

  // Consume only after EVERY check passed. A rejected grant stays reusable by
  // its legitimate holder (rejection is not a replay).
  if (state && state.consumedNonces) state.consumedNonces.add(grant.nonce);
  const scope = Object.freeze({
    action: grant.action,
    strategyId: grant.strategyId,
    version: grant.strategyVersion,
    contentHash: grant.contentHash,
    actorHash: grant.actorHash,
    authMethod: grant.authMethod,
    grantId: grant.grantId,
    nonce: grant.nonce,
  });
  if (audit) audit.append({
    atMs: nowMs, type: "GATE_ALLOWED", code: GATE_CODES.OK, action,
    strategyId: scope.strategyId, version: scope.version,
    contentHash: scope.contentHash, actorHash: scope.actorHash,
    authMethod: scope.authMethod, detail: scope.grantId,
  });
  return { ok: true, code: GATE_CODES.OK, scope };
}

/**
 * The single server-side choke point. `body` is the UNTRUSTED request body;
 * `actor` MUST come from makeServerActor(), never from that body.
 */
export async function enforceHumanPublishGate({
  body, actor, action, strategyId, version, contentHash,
  nowMs, secret, grant, state, audit, executeWithinScope,
}) {
  if (!isKnownAction(action)) {
    if (audit) audit.append({ atMs: nowMs, type: "GATE_DENIED", code: GATE_CODES.UNKNOWN_ACTION, action });
    return { ok: false, gated: true, code: GATE_CODES.UNKNOWN_ACTION };
  }
  if (!isGateRequired(action)) {
    if (audit) audit.append({
      atMs: nowMs, type: "UNATTENDED_ALLOWED", code: GATE_CODES.UNGATED, action,
      detail: "Shadow/Paper internal simulation stays unattended",
    });
    return { ok: true, gated: false, code: GATE_CODES.UNGATED, action };
  }

  // Aegis HARD CONDITION 6: the choke point must never approve when its
  // replay/revocation store or append-only audit log is missing. Without this,
  // omitting an optional argument silently turned the gate into "verify the
  // signature and forget everything else". A denial here cannot be recorded —
  // there is no log to record it in — which is precisely why it fails closed.
  if (!isGateStateValid(state)) {
    return { ok: false, gated: true, code: GATE_CODES.STATE_REQUIRED };
  }
  if (!isAuditValid(audit)) {
    return { ok: false, gated: true, code: GATE_CODES.AUDIT_REQUIRED };
  }

  const clientField = findClientSuppliedApproval(body);
  if (clientField) {
    if (audit) audit.append({
      atMs: nowMs, type: "GATE_DENIED", code: GATE_CODES.CLIENT_APPROVAL,
      action, detail: clientField,
    });
    return { ok: false, gated: true, code: GATE_CODES.CLIENT_APPROVAL, detail: clientField };
  }

  const verdict = verifyPublishGrant({
    grant, actor, action, strategyId, version, contentHash, nowMs, secret, state, audit,
  });
  if (!verdict.ok) return { ok: false, gated: true, code: verdict.code, detail: verdict.detail };

  let execution = null;
  if (typeof executeWithinScope === "function") execution = await executeWithinScope(verdict.scope);
  return {
    ok: true, gated: true, code: GATE_CODES.OK,
    scope: verdict.scope, execution,
  };
}
