// JEV Phase A / P0-A — server-side HTTP surface for HUMAN_PUBLISH_GATE.
//
// This handler is reachable ONLY through the existing lazy import in
// functions/api/jev/[[path]].js, i.e. AFTER the ENABLE_REAL_TRADING red line,
// the preview-credential check, the ledger-secret check and Basic auth. That
// file is deliberately NOT edited: its line numbers are cited by the security
// audit, and the AI routes were designed to stay out of it.
//
// P0 capability boundary: even a fully valid human grant does NOT enable real
// trading, production deploy or an external publish target. Those three
// actions are gated AND additionally denied by the P0 red lines below. The
// only action that executes in P0 is STRATEGY_PROMOTION, whose side effect is
// an append-only, audited AUTHORIZED_INTENT record — no order, no deployment,
// no network call. This is "execute within the approved scope" at P0 fidelity.
//
// Nothing here reads an `approved` flag from the client. The actor is derived
// from the server's authenticated principal in env, never from the body.

import { createHash } from "node:crypto";
import {
  GATE_NAME, GATE_CODES, AppendOnlyAuditLog,
  makeServerActor, enforceHumanPublishGate, issuePublishGrant,
} from "./human-publish-gate.mjs";

export function createPublishRuntime() {
  return {
    consumedNonces: new Set(),
    revokedGrantIds: new Set(),
    audit: new AppendOnlyAuditLog(),
  };
}

const DEFAULT_RUNTIME = createPublishRuntime();
export function getDefaultPublishRuntime() { return DEFAULT_RUNTIME; }

// P0 red lines. A human grant is necessary but NOT sufficient for these.
const P0_ACTION_BLOCKS = Object.freeze({
  REAL_TRADING_AUTHORIZATION: "REAL_TRADING_DISABLED_BY_RED_LINE",
  PRODUCTION_DEPLOY: "PRODUCTION_DEPLOY_DISABLED_IN_P0",
  EXTERNAL_PUBLISH: "EXTERNAL_PUBLISH_NO_TARGET_IN_P0",
});

function sha256hex(text) {
  return createHash("sha256").update(String(text), "utf8").digest("hex");
}

/** Content hash is computed by the SERVER from actual content, never trusted from the body. */
export function hashPublishContent(content) {
  if (content === undefined || content === null) return null;
  const s = typeof content === "string" ? content : JSON.stringify(content);
  return sha256hex(s);
}

function safeJson(text) {
  if (!text) return {};
  try { const v = JSON.parse(text); return v && typeof v === "object" ? v : {}; } catch { return {}; }
}

function localEnvelope({ ok = true, data, error, code, meta = {}, status = 200 }) {
  return new Response(JSON.stringify({ ok, data, error, code, meta, status }), {
    status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

/**
 * @returns {Promise<Response|null>} null when the path is not a publish route.
 * Any /api/jev/ai/publish/* path always returns a Response (never a bare null),
 * because onRequest uses `if (ai) return ai;`.
 */
export async function handlePublishApi(request, env, url, ctx = {}) {
  const pathname = url.pathname;
  if (!pathname.startsWith("/api/jev/ai/publish/")) return null;

  const envelope = typeof ctx.envelope === "function" ? ctx.envelope : localEnvelope;
  const runtime = ctx.publishRuntime || DEFAULT_RUNTIME;
  const nowMs = Number.isFinite(ctx.nowMs) ? ctx.nowMs : Date.now();
  const secret = env && env.JEV_PUBLISH_GATE_SECRET;

  let actor;
  try {
    // The authenticated principal comes from env (already verified by Basic
    // auth in onRequest). It is NEVER read from the request body.
    actor = makeServerActor({
      authenticatedUserId: env && env.JEV_PREVIEW_USER,
      authMethod: "SERVER_BASIC_AUTH",
      nowMs,
    });
  } catch {
    return envelope({
      ok: false, error: "SERVER_IDENTITY_UNAVAILABLE", code: GATE_CODES.INVALID_ACTOR,
      data: { gate: GATE_NAME }, status: 503,
    });
  }

  let bodyText = "";
  if (request.method === "POST" || request.method === "PUT") bodyText = await request.text();
  const body = safeJson(bodyText);

  // ---- read-only audit projection (no secrets, no mutation API) ------------
  if (pathname === "/api/jev/ai/publish/audit" && request.method === "GET") {
    return envelope({
      ok: true,
      data: {
        gate: GATE_NAME, count: runtime.audit.length,
        chainValid: runtime.audit.verifyChain(),
        entries: runtime.audit.entries(),
      },
      meta: { channel: "HUMAN_PUBLISH_GATE", label: "AUDIT" },
      status: 200,
    });
  }

  // ---- human grant issuance (authenticated human action) ------------------
  if (pathname === "/api/jev/ai/publish/grant" && request.method === "POST") {
    const contentHash = hashPublishContent(body.content);
    const issued = issuePublishGrant({
      actor, action: body.action, strategyId: body.strategyId,
      version: body.version, contentHash,
      ttlMs: Number.isFinite(body.ttlMs) ? body.ttlMs : 300000,
      nowMs, secret, nonce: body.nonce, audit: runtime.audit,
    });
    if (!issued.ok) return envelope({
      ok: false, error: issued.code, code: issued.code,
      data: { gate: GATE_NAME, reason: issued.detail || null },
      meta: { channel: "HUMAN_PUBLISH_GATE", label: "REJECTED" }, status: 403,
    });
    return envelope({
      ok: true, data: { gate: GATE_NAME, grant: issued.grant },
      meta: { channel: "HUMAN_PUBLISH_GATE", label: "GRANTED" }, status: 200,
    });
  }

  // ---- revocation ---------------------------------------------------------
  if (pathname === "/api/jev/ai/publish/revoke" && request.method === "POST") {
    const grantId = typeof body.grantId === "string" ? body.grantId : null;
    if (!grantId) return envelope({
      ok: false, error: "GRANT_ID_REQUIRED", code: "BAD_REQUEST", status: 400,
    });
    runtime.revokedGrantIds.add(grantId);
    runtime.audit.append({
      atMs: nowMs, type: "GRANT_REVOKED", code: GATE_CODES.REVOKED,
      actorHash: actor.identityHash, authMethod: actor.authMethod, detail: grantId,
    });
    return envelope({
      ok: true, data: { gate: GATE_NAME, revoked: grantId },
      meta: { channel: "HUMAN_PUBLISH_GATE", label: "REVOKED" }, status: 200,
    });
  }

  // ---- gated execution ----------------------------------------------------
  if (pathname === "/api/jev/ai/publish/execute" && request.method === "POST") {
    const contentHash = hashPublishContent(body.content);
    const executeWithinScope = (scope) => {
      const block = P0_ACTION_BLOCKS[scope.action];
      if (block) {
        runtime.audit.append({
          atMs: nowMs, type: "P0_RED_LINE_BLOCK", code: block, action: scope.action,
          strategyId: scope.strategyId, version: scope.version,
          contentHash: scope.contentHash, actorHash: scope.actorHash,
        });
        return Object.freeze({ executed: false, code: block, scope });
      }
      const entry = runtime.audit.append({
        atMs: nowMs, type: "AUTHORIZED_INTENT", code: GATE_CODES.OK,
        action: scope.action, strategyId: scope.strategyId, version: scope.version,
        contentHash: scope.contentHash, actorHash: scope.actorHash,
        authMethod: scope.authMethod, detail: scope.grantId,
      });
      return Object.freeze({
        executed: true, sideEffect: "AUTHORIZED_INTENT_RECORDED",
        auditSeq: entry.seq, scope,
      });
    };

    const verdict = await enforceHumanPublishGate({
      body, actor, action: body.action, strategyId: body.strategyId,
      version: body.version, contentHash,
      nowMs, secret, grant: body.grant, state: runtime,
      audit: runtime.audit, executeWithinScope,
    });

    if (!verdict.ok) return envelope({
      ok: false, error: verdict.code, code: verdict.code,
      data: { gate: GATE_NAME, reason: verdict.detail || null, gated: true },
      meta: { channel: "HUMAN_PUBLISH_GATE", label: "REJECTED" }, status: 403,
    });

    if (!verdict.execution || verdict.execution.executed !== true) {
      const code = (verdict.execution && verdict.execution.code) || "ACTION_NOT_ENABLED_IN_P0";
      return envelope({
        ok: false, error: code, code,
        data: { gate: GATE_NAME, scope: verdict.scope, humanAuthorized: true },
        meta: { channel: "HUMAN_PUBLISH_GATE", label: "P0_RED_LINE" }, status: 403,
      });
    }

    return envelope({
      ok: true,
      data: {
        gate: GATE_NAME, executed: true,
        sideEffect: verdict.execution.sideEffect,
        auditSeq: verdict.execution.auditSeq,
        scope: verdict.scope,
        realMoney: false, productionDeploy: false,
      },
      meta: { channel: "HUMAN_PUBLISH_GATE", label: "EXECUTED_WITHIN_SCOPE" },
      status: 200,
    });
  }

  return envelope({
    ok: false, error: "UNKNOWN_PUBLISH_ROUTE", code: "NOT_FOUND",
    data: { gate: GATE_NAME, pathname }, status: 404,
  });
}
