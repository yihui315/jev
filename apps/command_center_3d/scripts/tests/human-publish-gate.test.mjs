// P0-A — HUMAN_PUBLISH_GATE reverse tests + Paper unattended regression.
// Plain `node` + node:assert, no dependencies, no network, no host clock.
// Every case uses a fixed T0 so results are reproducible on any machine.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import {
  GATE_NAME, GATE_CODES, GATE_VERSION, PROTECTED_ACTIONS, UNATTENDED_ACTIONS,
  makeServerActor, assertServerActor, findClientSuppliedApproval,
  AppendOnlyAuditLog, issuePublishGrant, verifyPublishGrant,
  enforceHumanPublishGate, isGateRequired, isKnownAction,
} from "../../server/ai/human-publish-gate.mjs";
import {
  handlePublishApi, createPublishRuntime, hashPublishContent,
} from "../../server/ai/publish-routes.mjs";

const T0 = 1800000000000; // fixed clock; never read Date.now() in assertions
const SECRET = "g".repeat(32);
const USER = "preview-human";
const CONTENT_A = "strategy-content-A";
const H = (s) => createHash("sha256").update(s, "utf8").digest("hex");
const HASH_A = H(CONTENT_A);
const ACTION = "STRATEGY_PROMOTION";

const actor = (userId = USER) => makeServerActor({ authenticatedUserId: userId, nowMs: T0 });
const fresh = () => ({ consumedNonces: new Set(), revokedGrantIds: new Set(), audit: new AppendOnlyAuditLog() });

function grant(st, opts = {}) {
  return issuePublishGrant({
    actor: opts.actor || actor(),
    action: opts.action || ACTION,
    strategyId: "STRAT-7", version: "v3",
    contentHash: opts.contentHash || HASH_A,
    ttlMs: opts.ttlMs ?? 300000,
    nowMs: T0, secret: opts.secret ?? SECRET,
    nonce: opts.nonce || "nonce-A",
    audit: st.audit,
  });
}

function enforce(st, opts = {}) {
  return enforceHumanPublishGate({
    body: opts.body || {},
    actor: opts.actor || actor(),
    action: opts.action || ACTION,
    strategyId: "STRAT-7", version: "v3",
    contentHash: opts.contentHash || HASH_A,
    nowMs: opts.nowMs ?? T0,
    secret: opts.secret === null ? undefined : (opts.secret ?? SECRET),
    grant: opts.grant,
    state: st, audit: st.audit,
    executeWithinScope: opts.executeWithinScope,
  });
}

async function callPublish(path, body, runtime, envOverrides = {}) {
  const env = {
    JEV_PREVIEW_USER: USER,
    JEV_PUBLISH_GATE_SECRET: SECRET,
    ENABLE_REAL_TRADING: "false",
    ...envOverrides,
  };
  const request = new Request("https://preview.example.com" + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const url = new URL(request.url);
  const res = await handlePublishApi(request, env, url, { publishRuntime: runtime, nowMs: T0 });
  return { status: res.status, body: await res.clone().json() };
}

let PASS = 0;
let FAIL = 0;
async function test(name, fn) {
  try { await fn(); PASS++; console.log("PASS " + name); }
  catch (e) { FAIL++; console.log("FAIL " + name + " :: " + (e && e.message ? e.message : String(e))); }
}

// ---- 6 mandated reverse tests --------------------------------------------

await test("N1 no human grant -> HUMAN_GRANT_REQUIRED", async () => {
  const st = fresh();
  const v = await enforce(st, { grant: undefined });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.NO_GRANT);
});

await test("N2 forged confirmationId -> CLIENT_SUPPLIED_APPROVAL_REJECTED", async () => {
  const st = fresh();
  const g = grant(st);
  assert.equal(g.ok, true);
  const v = await enforce(st, { grant: g.grant, body: { confirmationId: "confirm-forged" } });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.CLIENT_APPROVAL);
  assert.equal(v.detail, "confirmationId");
});

await test("N3 expired grant -> GRANT_EXPIRED", async () => {
  const st = fresh();
  const g = grant(st, { ttlMs: 1000 });
  const v = await enforce(st, { grant: g.grant, nowMs: T0 + 1001 });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.EXPIRED);
});

await test("N4 replayed grant -> GRANT_REPLAYED", async () => {
  const st = fresh();
  const g = grant(st);
  const first = await enforce(st, { grant: g.grant });
  assert.equal(first.ok, true);
  const second = await enforce(st, { grant: g.grant });
  assert.equal(second.ok, false);
  assert.equal(second.code, GATE_CODES.REPLAY);
});

await test("N5 content edited, old grant reused -> CONTENT_HASH_MISMATCH", async () => {
  const st = fresh();
  const g = grant(st);
  const v = await enforce(st, { grant: g.grant, contentHash: H("strategy-content-B") });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.CONTENT_MISMATCH);
});

await test("N6 valid human grant -> executes ONCE within approved scope", async () => {
  const st = fresh();
  const g = grant(st);
  let calls = 0;
  let seen = null;
  const v = await enforce(st, {
    grant: g.grant,
    executeWithinScope: (scope) => { calls++; seen = scope; return { executed: true }; },
  });
  assert.equal(v.ok, true);
  assert.equal(v.code, GATE_CODES.OK);
  assert.equal(calls, 1, "executor must be called exactly once");
  assert.equal(seen.action, ACTION);
  assert.equal(seen.strategyId, "STRAT-7");
  assert.equal(seen.version, "v3");
  assert.equal(seen.contentHash, HASH_A);
  assert.equal(Object.isFrozen(seen), true, "approved scope must be immutable");
});

// ---- identity / integrity / fail-closed ----------------------------------

await test("N7 identity mismatch -> IDENTITY_MISMATCH", async () => {
  const st = fresh();
  const g = grant(st);
  const v = await enforce(st, { grant: g.grant, actor: actor("a-different-human") });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.ACTOR_MISMATCH);
});

await test("N8 revoked grant -> GRANT_REVOKED", async () => {
  const st = fresh();
  const g = grant(st);
  st.revokedGrantIds.add(g.grant.grantId);
  const v = await enforce(st, { grant: g.grant });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.REVOKED);
});

await test("N9 tampered grant signature -> GRANT_SIGNATURE_INVALID", async () => {
  const st = fresh();
  const g = grant(st);
  const tampered = { ...g.grant, action: "PRODUCTION_DEPLOY" };
  const v = await enforce(st, { grant: tampered });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.BAD_SIGNATURE);
});

await test("N10 unknown action fails closed and cannot be granted", async () => {
  const st = fresh();
  const g = grant(st, { action: "MYSTERY_ACTION" });
  assert.equal(g.ok, false);
  assert.equal(g.code, GATE_CODES.UNKNOWN_ACTION);
  const v = await enforce(st, { action: "MYSTERY_ACTION" });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.UNKNOWN_ACTION);
});

await test("N11 all four protected actions are gated; Paper/Shadow are not", async () => {
  for (const a of PROTECTED_ACTIONS) assert.equal(isGateRequired(a), true, a);
  for (const a of UNATTENDED_ACTIONS) assert.equal(isGateRequired(a), false, a);
  assert.equal(isKnownAction("MYSTERY_ACTION"), false);
  assert.equal(isGateRequired("MYSTERY_ACTION"), true, "unknown must fail closed");
});

await test("N12 Paper/Shadow unattended survives with NO grant and NO secret", async () => {
  const st = fresh();
  for (const a of UNATTENDED_ACTIONS) {
    const v = await enforce(st, { action: a, grant: undefined, secret: null });
    assert.equal(v.ok, true, a);
    assert.equal(v.gated, false, a);
    assert.equal(v.code, GATE_CODES.UNGATED, a);
  }
});

await test("N13 front-end approved=true cannot replace the server check", async () => {
  const st = fresh();
  const g = grant(st);
  const v = await enforce(st, { grant: g.grant, body: { approved: true, approvedBy: "ui-button" } });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.CLIENT_APPROVAL);
  assert.equal(findClientSuppliedApproval({ humanApproved: 1 }), "humanApproved");
});

await test("N14 audit log has no overwrite/delete API and verifies its hash chain", async () => {
  const log = new AppendOnlyAuditLog();
  log.append({ atMs: T0, type: "GATE_DENIED", code: GATE_CODES.NO_GRANT });
  log.append({ atMs: T0, type: "GATE_ALLOWED", code: GATE_CODES.OK });
  assert.equal(log.length, 2);
  assert.equal(log.verifyChain(), true);
  assert.equal(typeof log.delete, "undefined");
  assert.equal(typeof log.clear, "undefined");
  assert.equal(typeof log.update, "undefined");
  assert.equal(Object.isFrozen(log), true);
  assert.equal(Object.isFrozen(log.entries()[0]), true);
});

await test("N15 both ALLOW and DENY are written to the audit trail", async () => {
  const st = fresh();
  await enforce(st, { grant: undefined });
  const g = grant(st);
  await enforce(st, { grant: g.grant });
  const types = st.audit.entries().map((e) => e.record.type);
  assert.ok(types.includes("GATE_DENIED"), "deny must be audited");
  assert.ok(types.includes("GATE_ALLOWED"), "allow must be audited");
  assert.equal(st.audit.verifyChain(), true);
});

await test("N16 missing gate secret is fail-closed", async () => {
  const st = fresh();
  const v = await enforce(st, { secret: null });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.SECRET_MISSING);
  const g = issuePublishGrant({
    actor: actor(), action: ACTION, strategyId: "S", version: "v1",
    contentHash: HASH_A, nowMs: T0, secret: undefined, audit: st.audit,
  });
  assert.equal(g.ok, false);
  assert.equal(g.code, GATE_CODES.SECRET_MISSING);
});

await test("N17 a client-supplied JSON actor can never be a server actor", async () => {
  const forged = {
    userId: USER, authMethod: "SERVER_BASIC_AUTH", authenticatedAtMs: T0,
    identityHash: H(USER + "|SERVER_BASIC_AUTH"), source: "SERVER_AUTH_LAYER",
  };
  assert.equal(assertServerActor(forged), false);
  assert.equal(assertServerActor(actor()), true);
  const st = fresh();
  const v = verifyPublishGrant({
    grant: grant(st).grant, actor: forged, action: ACTION,
    strategyId: "STRAT-7", version: "v3", contentHash: HASH_A,
    nowMs: T0, secret: SECRET, state: st, audit: st.audit,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, GATE_CODES.INVALID_ACTOR);
});

// ---- real HTTP surface (server-side enforcement, not a button) -----------

await test("N18 HTTP publish/execute with no grant -> 403 HUMAN_GRANT_REQUIRED", async () => {
  const rt = createPublishRuntime();
  const r = await callPublish("/api/jev/ai/publish/execute", {
    action: ACTION, strategyId: "STRAT-7", version: "v3", content: CONTENT_A,
  }, rt);
  assert.equal(r.status, 403);
  assert.equal(r.body.ok, false);
  assert.equal(r.body.code, GATE_CODES.NO_GRANT);
  assert.equal(r.body.data.gate, GATE_NAME);
});

await test("N19 HTTP forged confirmationId -> 403 CLIENT_SUPPLIED_APPROVAL_REJECTED", async () => {
  const rt = createPublishRuntime();
  const issued = await callPublish("/api/jev/ai/publish/grant", {
    action: ACTION, strategyId: "STRAT-7", version: "v3", content: CONTENT_A, nonce: "http-nonce-1",
  }, rt);
  assert.equal(issued.status, 200);
  const r = await callPublish("/api/jev/ai/publish/execute", {
    action: ACTION, strategyId: "STRAT-7", version: "v3", content: CONTENT_A,
    grant: issued.body.data.grant, confirmationId: "confirm-forged",
  }, rt);
  assert.equal(r.status, 403);
  assert.equal(r.body.code, GATE_CODES.CLIENT_APPROVAL);
});

await test("N20 HTTP valid human grant -> 200 executed within scope, no real money", async () => {
  const rt = createPublishRuntime();
  const issued = await callPublish("/api/jev/ai/publish/grant", {
    action: ACTION, strategyId: "STRAT-7", version: "v3", content: CONTENT_A, nonce: "http-nonce-2",
  }, rt);
  assert.equal(issued.status, 200);
  assert.equal(issued.body.data.grant.schema, GATE_VERSION);
  const r = await callPublish("/api/jev/ai/publish/execute", {
    action: ACTION, strategyId: "STRAT-7", version: "v3", content: CONTENT_A,
    grant: issued.body.data.grant,
  }, rt);
  assert.equal(r.status, 200);
  assert.equal(r.body.ok, true);
  assert.equal(r.body.data.executed, true);
  assert.equal(r.body.data.sideEffect, "AUTHORIZED_INTENT_RECORDED");
  assert.equal(r.body.data.realMoney, false);
  assert.equal(r.body.data.productionDeploy, false);
  assert.equal(r.body.data.scope.contentHash, hashPublishContent(CONTENT_A));
});

await test("N21 HTTP real-trading authorization stays blocked by P0 red line", async () => {
  const rt = createPublishRuntime();
  const issued = await callPublish("/api/jev/ai/publish/grant", {
    action: "REAL_TRADING_AUTHORIZATION", strategyId: "STRAT-7", version: "v3",
    content: CONTENT_A, nonce: "http-nonce-3",
  }, rt);
  assert.equal(issued.status, 200, "a valid human grant CAN be issued for this action");
  const r = await callPublish("/api/jev/ai/publish/execute", {
    action: "REAL_TRADING_AUTHORIZATION", strategyId: "STRAT-7", version: "v3",
    content: CONTENT_A, grant: issued.body.data.grant,
  }, rt);
  assert.equal(r.status, 403);
  assert.equal(r.body.code, "REAL_TRADING_DISABLED_BY_RED_LINE");
});

await test("N22 static: paper/execute route contains no HUMAN_PUBLISH_GATE call", async () => {
  const src = readFileSync(new URL("../../server/ai/gateway-routes.mjs", import.meta.url), "utf8");
  const i = src.indexOf('"/api/jev/ai/paper/execute"');
  assert.ok(i > 0, "paper/execute route must still exist");
  const j = src.indexOf("const runMatch", i);
  assert.ok(j > i);
  const paperBlock = src.slice(i, j);
  assert.equal(paperBlock.includes("enforceHumanPublishGate"), false);
  assert.equal(paperBlock.includes("HUMAN_PUBLISH_GATE"), false);
  assert.equal(src.includes('/api/jev/ai/publish/'), true, "publish delegation must be wired");
});

// ---- Aegis independent review findings (2026-11-12) ----------------------

await test("N23 Aegis C1 FAIL fix: pipe in version cannot re-scope a grant", async () => {
  const st = fresh();
  const g = issuePublishGrant({
    actor: actor(), action: ACTION, strategyId: "A", version: "B|C",
    contentHash: HASH_A, ttlMs: 300000, nowMs: T0, secret: SECRET,
    nonce: "nonce-pipe", audit: st.audit,
  });
  assert.equal(g.ok, true);
  // The old `fields.join("|")` preimage made these two tuples identical:
  //   ["A", "B|C"]  ->  "A|B|C"
  //   ["A|B", "C"]  ->  "A|B|C"   (same HMAC, no secret needed)
  const shifted = { ...g.grant, strategyId: "A|B", strategyVersion: "C" };
  const v = await enforceHumanPublishGate({
    body: {}, actor: actor(), action: ACTION,
    strategyId: "A|B", version: "C", contentHash: HASH_A,
    nowMs: T0, secret: SECRET, grant: shifted,
    state: st, audit: st.audit,
  });
  assert.equal(v.ok, false, "shifted-boundary grant must NOT verify");
  assert.equal(v.code, GATE_CODES.BAD_SIGNATURE);
  // Positive control: the original grant still verifies for its OWN scope.
  const good = await enforceHumanPublishGate({
    body: {}, actor: actor(), action: ACTION,
    strategyId: "A", version: "B|C", contentHash: HASH_A,
    nowMs: T0, secret: SECRET, grant: g.grant,
    state: st, audit: st.audit,
  });
  assert.equal(good.ok, true, "length-prefixed preimage must not break legitimate pipes");
});

await test("N24 Aegis C1 over HTTP: shifted-boundary grant is refused by the route", async () => {
  const rt = createPublishRuntime();
  const issued = await callPublish("/api/jev/ai/publish/grant", {
    action: ACTION, strategyId: "A", version: "B|C", content: CONTENT_A, nonce: "http-pipe-1",
  }, rt);
  assert.equal(issued.status, 200);
  const shifted = { ...issued.body.data.grant, strategyId: "A|B", strategyVersion: "C" };
  const r = await callPublish("/api/jev/ai/publish/execute", {
    action: ACTION, strategyId: "A|B", version: "C", content: CONTENT_A, grant: shifted,
  }, rt);
  assert.equal(r.status, 403);
  assert.equal(r.body.code, GATE_CODES.BAD_SIGNATURE);
  assert.equal(r.body.data.gate, GATE_NAME);
});

await test("N25 Aegis C6: missing state or audit fails closed, never degrades", async () => {
  const st = fresh();
  const g = grant(st);
  const common = {
    body: {}, actor: actor(), action: ACTION,
    strategyId: "STRAT-7", version: "v3", contentHash: HASH_A,
    nowMs: T0, secret: SECRET, grant: g.grant,
  };
  const noState = await enforceHumanPublishGate({ ...common, state: undefined, audit: st.audit });
  assert.equal(noState.ok, false);
  assert.equal(noState.code, GATE_CODES.STATE_REQUIRED);
  const noAudit = await enforceHumanPublishGate({ ...common, state: st, audit: undefined });
  assert.equal(noAudit.ok, false);
  assert.equal(noAudit.code, GATE_CODES.AUDIT_REQUIRED);
  // A missing state must also be refused by the lower-level verifier.
  const direct = verifyPublishGrant({
    grant: g.grant, actor: actor(), action: ACTION,
    strategyId: "STRAT-7", version: "v3", contentHash: HASH_A,
    nowMs: T0, secret: SECRET, state: undefined, audit: st.audit,
  });
  assert.equal(direct.ok, false);
  assert.equal(direct.code, GATE_CODES.STATE_REQUIRED);
});

console.log(`RESULT PASS=${PASS} FAIL=${FAIL}`);
if (FAIL > 0) process.exit(1);
