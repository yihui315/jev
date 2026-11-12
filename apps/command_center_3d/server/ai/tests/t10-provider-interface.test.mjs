// T10 - JEV AI Provider Gateway contract tests.
// Plain node:assert/strict, stdlib-only, runnable by bare `node` (no vitest,
// no deps, no network). Prints a final RESULT line and exits 1 on any failure.

import assert from "node:assert/strict";
import {
  PROVIDER_RUNTIMES,
  STATUS_FIELDS,
  PROVIDER_GATEWAY_TARGETS,
  ERROR_TAXONOMY,
  ERROR_CODES,
  RESULT_POLICY,
  assertResponseAttribution,
  normalizeResult,
  callProvider,
  validateRegistry,
  allProviderEntries,
  findProviderEntry,
} from "../provider-interface.mjs";

let PASS = 0;
let FAIL = 0;

function check(name, fn) {
  try {
    fn();
    PASS += 1;
    process.stdout.write("[PASS] " + name + "\n");
  } catch (err) {
    FAIL += 1;
    const msg = err && err.message ? err.message : String(err);
    process.stdout.write("[FAIL] " + name + " :: " + msg + "\n");
  }
}

// 1 - per-runtime counts 4 / 6 / 2
check("A1 per-runtime counts are python=4 node=6 frozen_engine=2", () => {
  assert.equal(PROVIDER_RUNTIMES.python.length, 4);
  assert.equal(PROVIDER_RUNTIMES.node.length, 6);
  assert.equal(PROVIDER_RUNTIMES.frozen_engine.length, 2);
});

// 2 - every entry has all four status fields (booleans)
check("A2 every entry has registered/configured/verified/callable boolean fields", () => {
  const entries = allProviderEntries();
  assert.equal(entries.length, 12);
  for (const e of entries) {
    for (const f of STATUS_FIELDS) {
      assert.equal(typeof e[f], "boolean", e.id + " missing " + f);
    }
    for (const f of ["id", "runtime", "displayName", "sourcePath", "configEnvVar"]) {
      assert.equal(f in e, true, e.id + " missing " + f);
    }
  }
});

// 3 - no provider is callable (NEGATIVE)
check("A3 no provider is callable (all callable===false)", () => {
  const entries = allProviderEntries();
  assert.equal(entries.length, 12);
  assert.equal(entries.every((e) => e.callable === false), true);
  assert.equal(entries.some((e) => e.callable === true), false);
});

// 4 - no provider is configured (0 real keys) and all registered
check("A4 all registered=true and all configured=false", () => {
  const entries = allProviderEntries();
  assert.equal(entries.every((e) => e.registered === true), true);
  assert.equal(entries.every((e) => e.configured === false), true);
});

// 5 - validateRegistry asserts counts + four status fields
check("A5 validateRegistry returns ok with 4/6/2 counts and total 12", () => {
  const v = validateRegistry();
  assert.equal(v.ok, true);
  assert.equal(v.counts.python, 4);
  assert.equal(v.counts.node, 6);
  assert.equal(v.counts.frozen_engine, 2);
  assert.equal(v.total, 12);
  assert.equal(Object.isFrozen(v), true);
});

// 6 - registry is frozen
check("A6 PROVIDER_RUNTIMES and its runtime arrays are frozen", () => {
  assert.equal(Object.isFrozen(PROVIDER_RUNTIMES), true);
  assert.equal(Object.isFrozen(PROVIDER_RUNTIMES.python), true);
  assert.equal(Object.isFrozen(PROVIDER_RUNTIMES.node), true);
  assert.equal(Object.isFrozen(PROVIDER_RUNTIMES.frozen_engine), true);
});

// 7 - NEGATIVE: mismatched provider response rejected, never re-attributed
check("A7 mismatched providerId is rejected with PROVIDER_ATTRIBUTION_MISMATCH", () => {
  const r = assertResponseAttribution(
    { providerId: "node:openai", model: "gpt-4o-mini" },
    { providerId: "node:minimax", model: "gpt-4o-mini" }
  );
  assert.equal(r.ok, false);
  assert.equal(r.code, "PROVIDER_ATTRIBUTION_MISMATCH");
  assert.equal(r.errorClass, "PROVIDER_ATTRIBUTION_MISMATCH");
  // never silently re-attribute: top-level providerId stays the requested one
  assert.equal(r.providerId, "node:openai");
  assert.equal(r.responseProviderId, "node:minimax");
});

// 8 - NEGATIVE: missing provider id in response rejected
check("A8 missing providerId in response is rejected with PROVIDER_ATTRIBUTION_MISMATCH", () => {
  const r = assertResponseAttribution(
    { providerId: "python:deepseek" },
    { model: "deepseek-v4-pro" }
  );
  assert.equal(r.ok, false);
  assert.equal(r.code, "PROVIDER_ATTRIBUTION_MISMATCH");
  assert.equal(r.providerId, "python:deepseek");
});

// 9 - NEGATIVE: blank provider id in response rejected
check("A9 blank providerId in response is rejected with PROVIDER_ATTRIBUTION_MISMATCH", () => {
  const r = assertResponseAttribution(
    { providerId: "frozen_engine:qwen" },
    { providerId: "   ", model: "qwen3-coder-next" }
  );
  assert.equal(r.ok, false);
  assert.equal(r.code, "PROVIDER_ATTRIBUTION_MISMATCH");
});

// 10 - POSITIVE CONTROL: exact attribution accepted
check("A10 exact matching attribution is accepted (ok===true)", () => {
  const r = assertResponseAttribution(
    { providerId: "python:minimax", model: "MiniMax-M3" },
    { providerId: "python:minimax", model: "MiniMax-M3", latencyMs: 12, usage: { total_tokens: 3 }, finishReason: "stop" }
  );
  assert.equal(r.ok, true);
  assert.equal(r.code, null);
  assert.equal(r.errorClass, null);
  assert.equal(r.providerId, "python:minimax");
  assert.equal(r.model, "MiniMax-M3");
});

// 11 - NEGATIVE: model missing when a model was requested is rejected
check("A11 requested model missing from response is rejected (PROVIDER_BAD_RESPONSE)", () => {
  const r = assertResponseAttribution(
    { providerId: "node:openai", model: "gpt-4o-mini" },
    { providerId: "node:openai" }
  );
  assert.equal(r.ok, false);
  assert.equal(r.code, "PROVIDER_BAD_RESPONSE");
});

// 12 - PROVIDER_CALL_DISABLED path
check("A12 callProvider on a registered provider returns PROVIDER_CALL_DISABLED", () => {
  const e = findProviderEntry("python:minimax");
  assert.notEqual(e, null);
  assert.equal(e.callable, false);
  const r = callProvider({ providerId: "python:minimax" });
  assert.equal(r.ok, false);
  assert.equal(r.code, "PROVIDER_CALL_DISABLED");
  assert.equal(r.errorClass, "PROVIDER_CALL_DISABLED");
  assert.equal(r.providerId, "python:minimax");
});

// 13 - error taxonomy has exactly the six required codes
check("A13 error taxonomy contains all six required codes", () => {
  const required = [
    "PROVIDER_TIMEOUT",
    "PROVIDER_RETRY_EXHAUSTED",
    "PROVIDER_AUTH_MISSING",
    "PROVIDER_ATTRIBUTION_MISMATCH",
    "PROVIDER_BAD_RESPONSE",
    "PROVIDER_CALL_DISABLED",
  ];
  for (const c of required) {
    assert.equal(c in ERROR_TAXONOMY, true, "missing taxonomy code " + c);
    assert.equal(ERROR_CODES.includes(c), true, "missing ERROR_CODES entry " + c);
  }
});

// 14 - normalizeResult uniform envelope + never mutates attribution
check("A14 normalizeResult is a uniform envelope and never mutates attribution", () => {
  const input = {
    providerId: "node:openai",
    model: "MiniMax-M3",
    ok: true,
    latencyMs: 7,
    usage: { total_tokens: 5 },
    cost: null,
    rawResponseRef: "ref-abc",
    finishReason: "stop",
  };
  const snapshot = JSON.parse(JSON.stringify(input));
  const r = normalizeResult(input);
  // attribution copied verbatim, never re-derived from model
  assert.equal(r.providerId, "node:openai");
  assert.equal(r.model, "MiniMax-M3");
  assert.equal(r.latencyMs, 7);
  assert.equal(r.code, null);
  for (const f of ["providerId", "model", "latencyMs", "usage", "cost", "rawResponseRef", "finishReason", "errorClass"]) {
    assert.equal(f in r, true, "envelope missing " + f);
  }
  assert.equal(Object.isFrozen(r), true);
  // input object was not mutated
  assert.deepEqual(input, snapshot);
});

// 15 - documented timeout/retry/cost/raw-response-retention fields
check("A15 RESULT_POLICY documents timeout, retry, cost and raw-response-retention", () => {
  assert.equal(typeof RESULT_POLICY.defaultTimeoutMs, "number");
  assert.equal(typeof RESULT_POLICY.maxRetries, "number");
  assert.equal(typeof RESULT_POLICY.costPolicy, "string");
  assert.equal(typeof RESULT_POLICY.rawResponseRetention, "string");
  const r = normalizeResult({ providerId: "x", ok: true });
  assert.equal(typeof r.timeoutMs, "number");
  assert.equal(typeof r.retry.max, "number");
  assert.equal(typeof r.costTracked, "boolean");
  assert.equal(typeof r.rawResponseRetention, "string");
});

// 16 - gateway targets: M3 UNVERIFIED, all connected=false
check("A16 M3 target is UNVERIFIED and every gateway target has connected=false", () => {
  const targets = Object.values(PROVIDER_GATEWAY_TARGETS);
  assert.equal(targets.length, 4);
  assert.equal(targets.every((t) => t.connected === false), true);
  assert.equal(PROVIDER_GATEWAY_TARGETS.m3.status, "UNVERIFIED");
  assert.equal(PROVIDER_GATEWAY_TARGETS.m3.connected, false);
});

// final RESULT line (must be the last stdout line)
process.stdout.write("RESULT PASS=" + PASS + " FAIL=" + FAIL + "\n");
if (FAIL > 0) {
  process.exit(1);
}
