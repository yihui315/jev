// JEV Phase B — integration composition.
//
// Wires the five Phase B controls into one enforceable flow WITHOUT modifying
// HUMAN_PUBLISH_GATE (so N1-N32 stay green):
//
//   1. C2  server-trusted per-user identity (Mock IdP + MFA amr)
//   2. F-14/F-15 independent risk gate (deny on breach / unknown)
//   3. C3  role-bound grant: APPROVER issues, EXECUTOR runs, approver!=executor
//   4. D1  atomic, cross-instance nonce consumption + revocation (ledger)
//   5. F-19 external WORM audit anchor + startup recovery verification
//
// Also exports `runCoreGateWithD1State`, which proves the EXISTING core gate
// can be backed by the durable D1 ledger (cross-instance replay) using the
// D1BackedGateState adapter — no change to the core module.
//
// MOCK / isolated. Production needs the external authorizations called out in
// the design doc.

import { AppendOnlyAuditLog, GATE_CODES } from "../human-publish-gate.mjs";
import {
  buildTrustedIdentity, IdentityRevocationStore, SharedIdentityRevocationStore, IDENTITY_CODES,
} from "./identity.mjs";
import {
  issueRoleBoundGrant, verifyRoleBoundGrant, ROLE_CODES, ROLES,
} from "./roles.mjs";
import { AuthorizationLedger, D1BackedGateState, LedgerReplayError } from "./ledger.mjs";
import { WormAuditAnchor, verifyAnchorOnStartup, ANCHOR_CODES } from "./audit-anchor.mjs";
import { evaluateRiskGate, combineRiskAndHuman } from "./risk-gate-b.mjs";

export const PHASEB_CODES = Object.freeze({
  RISK_DENIED: "PHASEB_RISK_DENIED",
  IDENTITY_DENIED: "PHASEB_IDENTITY_DENIED",
  NO_GRANT: "PHASEB_GRANT_REQUIRED",
});

/**
 * APPROVER issues a role-bound grant for a future EXECUTOR.
 * Returns { ok, grant } or a fail-closed error.
 */
export function phaseBIssueGrant({
  approverToken, idpSecret, executorId, action, strategyId,
  strategyVersion, contentHash, nowMs, secret, nonce,
}) {
  const id = buildTrustedIdentity({ idToken: approverToken, idpSecret, nowMs });
  if (!id.ok) return { ok: false, code: id.code, detail: id.detail };
  const issued = issueRoleBoundGrant({
    approverClaims: id.claims, action, strategyId, strategyVersion, contentHash,
    executorId, ttlMs: 300000, nowMs, secret, nonce,
  });
  if (!issued.ok) return issued;
  return { ok: true, grant: issued.grant, approverId: id.claims.sub };
}

/**
 * Full enforceable Phase B authorization flow. Risk is evaluated FIRST and
 * independently; a human/role grant can never override a risk denial.
 *
 * @param {object} opts
 * @param {string} opts.executorToken  EXECUTOR's IdP token (untrusted input)
 * @param {string} opts.idpSecret      Mock IdP HMAC secret
 * @param {object} opts.grant          role-bound grant issued by an approver
 * @param {object} opts.ledger         AuthorizationLedger (D1 sim)
 * @param {object} opts.audit          AppendOnlyAuditLog
 * @param {object} opts.worm           WormAuditAnchor
 * @param {object} opts.riskState      server risk state (or null => unknown)
 */
export function phaseBEnforce({
  executorToken, idpSecret, grant, action, strategyId, strategyVersion,
  contentHash, nowMs, secret, ledger, audit, worm, riskState,
}) {
  // (1) C2 — server-trusted executor identity (MFA required).
  const id = buildTrustedIdentity({ idToken: executorToken, idpSecret, nowMs });
  if (!id.ok) {
    if (audit) audit.append({ atMs: nowMs, type: "PHASEB_DENIED", code: id.code, action, detail: id.detail });
    return { ok: false, code: id.code, detail: id.detail };
  }

  // (2) F-14/F-15 — independent risk gate. Deny closed regardless of human.
  const risk = evaluateRiskGate({ riskState });
  if (!risk.approved) {
    if (audit) audit.append({ atMs: nowMs, type: "PHASEB_RISK_DENIED", code: risk.code, action, detail: risk.reason });
    return { ok: false, code: PHASEB_CODES.RISK_DENIED, risk, detail: risk.reason };
  }

  // (3)+(4) C3 role-bound grant verify + D1 atomic nonce/revocation.
  if (!grant) {
    if (audit) audit.append({ atMs: nowMs, type: "PHASEB_DENIED", code: PHASEB_CODES.NO_GRANT, action });
    return { ok: false, code: PHASEB_CODES.NO_GRANT };
  }
  const verdict = verifyRoleBoundGrant({
    grant, executorClaims: id.claims, action, strategyId, strategyVersion,
    contentHash, nowMs, secret, ledger,
  });
  if (!verdict.ok) {
    if (audit) audit.append({ atMs: nowMs, type: "PHASEB_DENIED", code: verdict.code, action, detail: verdict.detail });
    return { ok: false, code: verdict.code, detail: verdict.detail, risk };
  }

  // (5) F-19 — anchor the recomputed audit chain tip to the WORM store. This is
  // done AFTER the final ALLOWED audit append so the anchored tip reflects the
  // complete, just-closed chain.
  if (audit) audit.append({
    atMs: nowMs, type: "PHASEB_ALLOWED", code: "PHASEB_AUTHORIZED", action,
    strategyId, version: strategyVersion, contentHash,
    actorHash: id.actor.identityHash, authMethod: id.actor.authMethod,
    detail: verdict.scope.grantId,
  });

  if (worm && audit) {
    const entries = audit.entries();
    const tip = entries.length ? entries[entries.length - 1].hash : null;
    if (tip) worm.anchor(tip, nowMs);
  }

  return { ok: true, code: "PHASEB_AUTHORIZED", scope: verdict.scope, risk, identity: id.claims.sub };
}

/**
 * Startup recovery check using F-19: recompute in-memory chain tip, compare to
 * last external anchor. Fail-closed on mismatch.
 */
export function phaseBStartupRecovery({ worm, audit, auditStateKnown = true }) {
  const entries = audit ? audit.entries() : [];
  const tip = entries.length ? entries[entries.length - 1].hash : null;
  return verifyAnchorOnStartup({ worm, recomputedTipHash: tip, auditStateKnown });
}

/**
 * Prove the EXISTING core HUMAN_PUBLISH_GATE can be backed by the durable D1
 * ledger for cross-instance replay — using the D1BackedGateState adapter and
 * WITHOUT editing the core module.
 *
 * NOTE: D1BackedGateState is DEMONSTRATION-ONLY. When a REAL core enforcement
 * (enforceHumanPublishGate / verifyPublishGrant) is actually run through it, the
 * core gate's post-check `add()` can throw LedgerReplayError on a cross-instance
 * REPLAY (the core does has() then add() as two separate calls). Wrap that call
 * in safeCoreEnforceWithD1 so the throw becomes a clean REPLAY denial. The
 * production path is phaseBEnforce, which checks the consume result inline.
 */
export function runCoreGateWithD1State({ coreEnforceArgs, ledger, coreEnforce, audit }) {
  const state = new D1BackedGateState(ledger);
  if (typeof coreEnforce === "function") {
    return safeCoreEnforceWithD1({ coreEnforce, coreEnforceArgs, ledger, audit });
  }
  // audit must be a real AppendOnlyAuditLog (caller passes it).
  return { state };
}

/**
 * Run a core enforcement through the D1-backed adapter and convert a thrown
 * LedgerReplayError (cross-instance REPLAY detected by the adapter's fail-closed
 * `add()`) into a clean { ok:false, code:GATE_CODES.REPLAY } result. Without this
 * wrapper the core gate would otherwise surface a raw exception on REPLAY.
 */
export async function safeCoreEnforceWithD1({ coreEnforce, coreEnforceArgs = {}, ledger, audit }) {
  if (typeof coreEnforce !== "function") {
    return { error: "NO_CORE_ENFORCE" };
  }
  const state = new D1BackedGateState(ledger);
  const args = { ...coreEnforceArgs, state, audit: audit || new AppendOnlyAuditLog() };
  try {
    const result = await coreEnforce(args);
    // Surface a core denial (e.g. a REPLAY caught by the gate's pre-check
    // `has()`) as a wrapper denial instead of masking it as ok:true. Without
    // this, a cross-instance REPLAY returned by the core gate would be reported
    // as a successful enforcement, defeating the fail-closed guarantee. The
    // thrown-LedgerReplayError path below remains the cross-instance TOCTOU
    // catch; this handles the same-instance returned-denial path.
    if (result && result.ok === false) {
      return { ok: false, code: result.code, detail: result.detail, result };
    }
    return { ok: true, result };
  } catch (err) {
    if (err instanceof LedgerReplayError) {
      return { ok: false, code: GATE_CODES.REPLAY, detail: err.nonce };
    }
    return { ok: false, code: "ENFORCE_ERROR", detail: String(err && err.message ? err.message : err) };
  }
}

export {
  buildTrustedIdentity, IdentityRevocationStore, SharedIdentityRevocationStore, IDENTITY_CODES,
  issueRoleBoundGrant, verifyRoleBoundGrant, ROLE_CODES, ROLES,
  AuthorizationLedger, D1BackedGateState, LedgerReplayError,
  WormAuditAnchor, verifyAnchorOnStartup, ANCHOR_CODES,
  evaluateRiskGate, combineRiskAndHuman,
  AppendOnlyAuditLog,
};
