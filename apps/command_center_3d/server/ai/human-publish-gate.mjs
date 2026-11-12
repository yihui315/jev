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

function canonicalGrant(g) {
  return [
    GATE_VERSION, g.grantId, g.actorHash, g.actorUserId, g.authMethod,
    g.action, g.strategyId, g.strategyVersion, g.contentHash, g.nonce,
    String(g.issuedAtMs), String(g.expiresAtMs),
  ].join("|");
}

function signGrant(g, secret) {
  return createHmac("sha256", String(secret)).update(canonicalGrant(g), "utf8").digest("hex");
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
  if (state && state.revokedGrantIds && state.revokedGrantIds.has(grant.grantId)) return deny(GATE_CODES.REVOKED, grant.grantId);
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
