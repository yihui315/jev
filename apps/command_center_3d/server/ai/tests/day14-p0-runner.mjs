/**
 * P0-F Independent QA — RUNNER acceptance via fault injection (T1/T2/T3/T4/T5/T6/T8/T9/T10).
 *
 * Every test runs the REAL runner (copied from disk) inside an isolated temp
 * sandbox as a child process. Real files are never modified. Each fault is
 * injected on the COPY only. No network egress (guard blocks globalThis.fetch).
 *
 * Run: node day14-p0-runner.mjs
 *      (exit code 0 = all pass, 1 = any fail)
 *
 * Includes "falsifiability proofs": a baseline on ebc3fcc and mutation checks
 * demonstrating the assertions actually FAIL when the protection is removed.
 * Zero self-proving-green: no check uses file existence / qa.pass as evidence.
 */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import {
  harness, buildSandbox, runChild, readJsonl, readLines, writeJsonl, verifyChainFile,
  findArtifact, readState, runnerSource, runnerSourceAt, sha256Hex, logEvidence,
  nonetAttempts, childRuns, LIB_DIR, REPO,
} from "./day14-p0-lib.mjs";

const { __test: engine } = await import(pathToFileURL(join(LIB_DIR, "paper-server.mjs")).href);

function marker(r, name) {
  const m = r.stdout.match(new RegExp("^" + name + "=(.*)$", "m"));
  return m ? m[1] : null;
}
function runResult(r) {
  const s = marker(r, "QA_RUN_RESULT");
  return s ? JSON.parse(s) : null;
}
function firstLineOf(r) { return (r.stdout.split("\n").find((l) => l.includes("=")) || "").slice(0, 240); }
function combined(r) { return r.stdout + "\n" + r.stderr; }
function recs(sb, name) { return readJsonl(findArtifact(sb.OUT, name)); }
function activeRunDirs(OUT) {
  const d = join(OUT, "runs");
  if (!existsSync(d)) return [];
  return readdirSync(d, { withFileTypes: true }).filter((x) => x.isDirectory()).map((x) => x.name);
}
/** Collect every journal record written anywhere in a sandbox (root + runs/*). */
function allJournalRecords(sb) {
  const out = [];
  const dirs = [sb.OUT, ...activeRunDirs(sb.OUT).map((d) => join(sb.OUT, "runs", d))];
  for (const d of dirs) {
    for (const n of ["snapshots.jsonl", "decisions.jsonl", "risk.jsonl", "provider_health.jsonl", "equity.jsonl"]) {
      const p = join(d, n);
      if (existsSync(p)) for (const r of readJsonl(p)) out.push({ __file: n, ...r });
    }
  }
  return out;
}
/** Existence-style checklist (what ebc3fcc independentQA() used to declare PASS on). */
function existenceChecklist(sb) {
  const names = ["snapshots.jsonl", "decisions.jsonl", "risk.jsonl", "provider_health.jsonl", "equity.jsonl", "state.json", "day14-anchor.json", "day14-summary.json"];
  const dir = activeRunDirs(sb.OUT)[0] ? join(sb.OUT, "runs", activeRunDirs(sb.OUT)[0]) : sb.OUT;
  const items = {};
  for (const n of names) {
    const p = existsSync(join(dir, n)) ? join(dir, n) : join(sb.OUT, n);
    items[n] = existsSync(p) && readFileSync(p, "utf8").trim().length > 0;
  }
  return { allExistNonEmpty: Object.values(items).every(Boolean), items, dir };
}

const H = harness("P0-F RUNNER (sandbox fault injection + R1-R6 + F-12)");

// ===========================================================================
// APPARATUS / FALSIFIABILITY PROOFS (not product acceptance — prove the tests bite)
// ===========================================================================
H.test("B1 apparatus: ebc3fcc baseline WITH broken gate DOES warn+continue + approve via simplified fallback (proves T1/T2 injection is real)", () => {
  const sb = buildSandbox({ runnerSource: runnerSourceAt("ebc3fcc"), breakFormalGate: true });
  const r = runChild(sb, { dry: true, cycles: 2 });
  const risk = recs(sb, "risk.jsonl");
  const fallbackApproved = risk.filter((x) => /fallback|simplified/i.test(String(x.gate)) && x.approved === true);
  logEvidence("ebc3fcc code", r.code);
  logEvidence("warn-and-continue present", /fallback|unavailable|warn/i.test(combined(r)));
  logEvidence("fallback approved records", fallbackApproved.map((x) => ({ gate: x.gate, code: x.code })));
  assert.equal(r.code, 0, "ebc3fcc must continue (that is its defect)");
  assert.ok(fallbackApproved.length >= 1, "ebc3fcc must emit approved:true through the simplified fallback — otherwise the injection would prove nothing");
  sb.cleanup();
});

// ===========================================================================
// T1 — formal gate import failure => fail-closed (no warn+continue, no approvals)
// ===========================================================================
H.test("R1①/T1 formal-gate IMPORT FAILURE => throw + zero logs written + zero RISK_APPROVED (control succeeds)", () => {
  // Control: intact gate, normal run succeeds.
  const ctl = buildSandbox();
  const rc = runChild(ctl, { dry: true, cycles: 2 });
  assert.equal(rc.code, 0, "control run with intact gate must succeed");
  assert.ok(runResult(rc), "control must produce a QA_RUN_RESULT");
  logEvidence("control (intact gate) code", rc.code);
  ctl.cleanup();

  // Injected failure: break the formal gate import (module-load-time termination, L140-149).
  const sb = buildSandbox({ breakFormalGate: true });
  const r = runChild(sb, { dry: true, cycles: 2 });
  logEvidence("broken gate code", r.code);
  logEvidence("broken gate first output line", firstLineOf(r));
  assert.notEqual(r.code, 0, "must NOT exit 0 (warn-and-continue would be a failure)");
  const imp = marker(r, "QA_IMPORT_FAILED") || "";
  assert.equal(marker(r, "QA_RUN_RESULT"), null, "runDay14 must NOT have executed (no summary)");
  assert.ok(/fail-closed/i.test(imp), "termination reason must be fail-closed: " + imp);
  assert.ok(!/warn.*continue|using prep-simplified fallback/i.test(combined(r)), "must not warn-and-continue");

  // R1① exact: ZERO logs written (resolveRun never ran -> no runs/<id>/ dir), ZERO RISK_APPROVED.
  const allRecs = allJournalRecords(sb);
  const riskApproved = allRecs.filter((x) => x.code === "RISK_APPROVED");
  const anyApproved = allRecs.filter((x) => x.approved === true || x.riskApproved === true);
  const tradeableApproved = allRecs.filter((x) => x.riskApproved === true && x.action !== "HOLD");
  const fallbackApproved = allRecs.filter((x) => /fallback|simplified/i.test(String(x.gate)) && x.approved === true);
  const runDirs = activeRunDirs(sb.OUT);
  logEvidence("run dirs created (should be 0)", runDirs.length);
  logEvidence("journal records written in total", allRecs.length);
  logEvidence("RISK_APPROVED count", riskApproved.length);
  logEvidence("any approved:true count", anyApproved.length);
  logEvidence("tradeable approved conclusions", tradeableApproved.length);
  logEvidence("fallback-approved risk records", fallbackApproved.length);
  assert.equal(runDirs.length, 0, "zero run dirs => zero logs written by runner");
  assert.equal(allRecs.length, 0, "zero journal records written");
  assert.equal(riskApproved.length, 0, "R1①: zero RISK_APPROVED on import failure");
  assert.equal(anyApproved.length, 0, "R1①: not even a HOLD approval may be produced");
  assert.equal(tradeableApproved.length, 0, "no approved:true tradeable conclusion");
  assert.equal(fallbackApproved.length, 0, "no simplified-fallback approval");
  sb.cleanup();
});

// ===========================================================================
// T2 — simplified fallback gate must not release anything
// ===========================================================================
H.test("T2a static: NO executable simplified 2-check fallback code (comments stripped; checks real code patterns)", () => {
  const src = runnerSource();
  // Strip block comments and full-line // comments so a prose mention (e.g.
  // "彻底删除 fallbackRiskGate 的放行能力") is NOT a false positive.
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const patterns = {
    functionDecl: /function\s+fallbackRiskGate/,
    callSite: /fallbackRiskGate\s*\(/,
    nAHoldLiteral: /["']N\/A_HOLD["']/,
    fallbackGateName: /["']prep-simplified-fallback["']/,
    twoCheckBreach: /approved:\s*breach\s*===\s*null/,
    simplifiedNotionalCheck: /notional\s*>\s*CFG\.maxNotionalPerTrade/,
  };
  const found = Object.entries(patterns).filter(([, re]) => re.test(code)).map(([k]) => k);
  logEvidence("executable simplified-gate patterns found", found);
  logEvidence("comment-only mention of fallbackRiskGate (informational)", /fallbackRiskGate/.test(src));
  assert.equal(found.length, 0, "no executable simplified-gate code: " + found.join(","));
  // The authoritative formal gate must be wired.
  assert.ok(/riskCheck/.test(src) && /formal-risk-gate/.test(src), "formal riskCheck must be the wired gate");
});

H.test("T2b runtime: with formal gate DOWN, no fallback-approved record and no approved tradeable conclusion", () => {
  const sb = buildSandbox({ breakFormalGate: true });
  const r = runChild(sb, { dry: true, cycles: 2 });
  const risk = existsSync(findArtifact(sb.OUT, "risk.jsonl")) ? recs(sb, "risk.jsonl") : [];
  const dec = existsSync(findArtifact(sb.OUT, "decisions.jsonl")) ? recs(sb, "decisions.jsonl") : [];
  const fallbackApproved = risk.filter((x) => /fallback|simplified/i.test(String(x.gate)) && x.approved === true);
  const tradeableApproved = dec.filter((d) => d.riskApproved === true && d.action !== "HOLD");
  logEvidence("run code", r.code);
  logEvidence("risk records", risk.length);
  logEvidence("fallback-approved", fallbackApproved.length);
  logEvidence("approved tradeable conclusions", tradeableApproved.length);
  assert.equal(fallbackApproved.length, 0, "no simplified-fallback approval path");
  assert.equal(tradeableApproved.length, 0, "no approved tradeable conclusion");
  sb.cleanup();
});

// ===========================================================================
// T3 — full hash-chain validation at startup (tamper / chain-break => throw; valid => pass)
// ===========================================================================
function tamperRun(kind) {
  const sb = buildSandbox();
  const r1 = runChild(sb, { dry: true, cycles: 2 });
  assert.equal(r1.code, 0, "run1 must succeed");
  const p = findArtifact(sb.OUT, "decisions.jsonl");
  const lines = readLines(p).map((l) => JSON.parse(l));
  if (kind === "content") lines[1].reason = "P0-QA-TAMPERED-CONTENT";
  else if (kind === "chainbreak") lines[1]._prev = "GENESIS";
  writeJsonl(p, lines);
  const independent = verifyChainFile(p);
  const r2 = runChild(sb, { dry: true, cycles: 0 });
  const out = combined(r2);
  const result = { sb, r2, independent, out };
  return result;
}

H.test("T3 control: valid pre-existing chain passes startup validation on resume", () => {
  const sb = buildSandbox();
  const r1 = runChild(sb, { dry: true, cycles: 2 });
  const r2 = runChild(sb, { dry: true, cycles: 0 });
  const v = verifyChainFile(findArtifact(sb.OUT, "decisions.jsonl"));
  logEvidence("run1 code / run2 code", [r1.code, r2.code]);
  logEvidence("reference chain check", { ok: v.ok, count: v.count });
  assert.equal(r1.code, 0);
  assert.equal(r2.code, 0, "valid chain must NOT be rejected (no false positive)");
  assert.equal(v.ok, true);
  sb.cleanup();
});

H.test("T3a content tamper: startup THROWS + independent validator also detects", () => {
  const { sb, r2, independent, out } = tamperRun("content");
  logEvidence("run2 code", r2.code);
  logEvidence("run2 output", firstLineOf(r2));
  logEvidence("independent validator", { ok: independent.ok, error: independent.error });
  assert.equal(independent.ok, false, "my reference validator must detect the tamper");
  assert.notEqual(r2.code, 0, "runner must refuse to start on a tampered chain");
  assert.ok(/HASH TAMPER|CHAIN BROKEN|chain/i.test(out), "throw must be a chain-integrity error: " + firstLineOf(r2));
  sb.cleanup();
});

H.test("T3b chain break (_prev reset to GENESIS): startup THROWS", () => {
  const { sb, r2, independent, out } = tamperRun("chainbreak");
  logEvidence("run2 code", r2.code);
  logEvidence("run2 output", firstLineOf(r2));
  logEvidence("independent validator", { ok: independent.ok, error: independent.error });
  assert.equal(independent.ok, false);
  assert.notEqual(r2.code, 0, "runner must refuse a broken chain");
  assert.ok(/CHAIN BROKEN|chain|HASH/i.test(out), "throw must reference the chain: " + firstLineOf(r2));
  sb.cleanup();
});

H.test("M3 falsifiability proof: disabling chain validation in a MUTANT makes tamper go undetected (so T3 assertions genuinely bite)", () => {
  let src = runnerSource();
  src = src.replace("if (_prev !== prev)", "if (false && _prev !== prev)")
           .replace("if (recomputed !== _hash)", "if (false && recomputed !== _hash)");
  const sb = buildSandbox({ runnerSource: src });
  runChild(sb, { dry: true, cycles: 2 });
  const p = findArtifact(sb.OUT, "decisions.jsonl");
  const lines = readLines(p).map((l) => JSON.parse(l));
  lines[1].reason = "TAMPERED";
  writeJsonl(p, lines);
  const r2 = runChild(sb, { dry: true, cycles: 0 });
  logEvidence("mutant tamper run2 code (0 = protection gone)", r2.code);
  assert.equal(r2.code, 0, "with validation removed the mutant must NOT throw — proving T3 would FAIL on a regressing impl");
  sb.cleanup();
});

// ===========================================================================
// T4 — restart continuation: _prev of resumed record == previous _hash, NOT GENESIS
// ===========================================================================
H.test("T4 restart chaining: two runs share one continuous chain; resumed _prev == prior _hash (print hashes)", () => {
  const sb = buildSandbox();
  const r1 = runChild(sb, { dry: true, cycles: 3 });
  const r2 = runChild(sb, { dry: true, cycles: 2 });
  const p = findArtifact(sb.OUT, "decisions.jsonl");
  const lines = readLines(p).map((l) => JSON.parse(l));
  const v = verifyChainFile(p);
  logEvidence("run1 / run2 code", [r1.code, r2.code]);
  logEvidence("total decision records", lines.length);
  logEvidence("reference full-chain check", { ok: v.ok, error: v.error });
  logEvidence("boundary", {
    "line2._hash": (lines[2]._hash || "").slice(0, 24),
    "line3._prev": (lines[3]._prev || "").slice(0, 24),
    "line3._hash": (lines[3]._hash || "").slice(0, 24),
    "line4._prev": (lines[4]._prev || "").slice(0, 24),
  });
  logEvidence("state after resume", { cycle: readState(sb.OUT).cycle, calls: readState(sb.OUT).calls });
  assert.equal(r1.code, 0);
  assert.equal(r2.code, 0);
  assert.equal(lines.length, 5, "3 + 2 cycles must produce 5 records");
  assert.equal(v.ok, true, "the whole chain must verify end-to-end");
  assert.equal(lines[3]._prev, lines[2]._hash, "first record of run2 must continue from run1 tip");
  assert.notEqual(lines[3]._prev, "GENESIS", "resumed record must NOT restart at GENESIS");
  assert.equal(lines[4]._prev, lines[3]._hash, "within-run2 link must also hold");
  assert.equal(readState(sb.OUT).cycle, 5);
  sb.cleanup();
});

// ===========================================================================
// T5 — breakpoint resume + cumulative accounting + budget uses cumulative value
// ===========================================================================
H.test("T5 resume + cumulative: state cycle=N not reset; costUsd/calls/providerFails accumulate; budget check uses cumulative", () => {
  const sb = buildSandbox();
  const env = { QA_SCENARIO: "costly_provider" }; // +3 USD per cycle, budget = 5
  const r1 = runChild(sb, { dry: true, cycles: 10 }, env);
  const s1 = readState(sb.OUT);
  const r2 = runChild(sb, { dry: true, cycles: 5 }, env);
  const res2 = runResult(r2);
  const s2 = readState(sb.OUT);
  logEvidence("run1 state", { cycle: s1.cycle, costUsd: s1.costUsd, calls: s1.calls, providerFails: s1.providerFails, paused: s1.paused, pauseReason: s1.pauseReason });
  logEvidence("run2 cyclesThisRun", res2 && res2.cyclesThisRun);
  logEvidence("run2 state", { cycle: s2.cycle, costUsd: s2.costUsd, calls: s2.calls, providerFails: s2.providerFails, paused: s2.paused });

  assert.equal(r1.code, 0);
  assert.equal(s1.cycle, 2, "must pause after 2 cycles (cumulative 6 > 5), not 10");
  assert.equal(s1.costUsd, 6, "cumulative cost must be 6 (each single cycle is only 3 < 5)");
  assert.equal(s1.paused, true);
  assert.equal(s1.pauseReason, "BUDGET_EXCEEDED", "budget must be judged on CUMULATIVE cost, not a fresh 0");
  assert.equal(res2.cyclesThisRun, 0, "run2 must resume from the paused state (0 new cycles), not restart from cycle 0");
  assert.equal(s2.cycle, 2, "state.cycle must not reset to 0");
  assert.equal(s2.costUsd, 6, "cumulative costUsd must not be zeroed on restart");
  assert.equal(s2.calls, 2, "cumulative calls must not be zeroed on restart");
  sb.cleanup();
});

// ===========================================================================
// T6 — NO_ACTION != HOLD
// ===========================================================================
H.test("R1②/T6a unverified market => NO_ACTION + execution=NONE + provider NO_CALL + zero orderable + zero RISK_APPROVED (RISK_HOLD_NO_ORDER allowed)", () => {
  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 2 }, { QA_SCENARIO: "unverified" });
  const dec = recs(sb, "decisions.jsonl");
  const prov = recs(sb, "provider_health.jsonl");
  const risk = recs(sb, "risk.jsonl");
  logEvidence("decisions", dec.map((d) => ({ decisionClass: d.decisionClass, aiKind: d.aiKind, action: d.action, orderable: d.orderable, execution: d.execution, code: d.riskCode, reason: d.reason })));
  logEvidence("provider statuses", prov.map((p) => p.status));
  logEvidence("risk codes", risk.map((x) => ({ code: x.code, approved: x.approved, orderable: x.orderable, failClosedReason: x.failClosedReason })));
  assert.equal(r.code, 0);
  assert.ok(dec.length >= 1);
  const all = allJournalRecords(sb);
  const riskApproved = all.filter((x) => x.code === "RISK_APPROVED" || x.riskCode === "RISK_APPROVED");
  const orderableTrue = all.filter((x) => x.orderable === true);
  logEvidence("RISK_APPROVED count (must be 0)", riskApproved.length);
  logEvidence("orderable=true count (must be 0)", orderableTrue.length);
  assert.equal(riskApproved.length, 0, "R1②: zero RISK_APPROVED on unverified market");
  assert.equal(orderableTrue.length, 0, "R1②: zero orderable=true");
  for (const d of dec) {
    assert.equal(d.decisionClass, "NO_ACTION", "unverified market must be NO_ACTION, not HOLD");
    assert.equal(d.aiKind, "none", "no model call on unverified market");
    assert.equal(d.orderable, false, "must not be an orderable intent");
    assert.equal(d.execution, "NONE", "R1②: execution must be NONE (no order, no fill)");
    assert.equal(d.riskCode, "RISK_HOLD_NO_ORDER", "R1②: RISK_HOLD_NO_ORDER approved:true is LEGAL here (HOLD no-order), not a tradable approval");
    assert.ok(/UNVERIFIED|FAILCLOSED/i.test(String(d.reason)), "reason must record fail-closed");
  }
  for (const p of prov) assert.ok(/NO_CALL|UNVERIFIED/i.test(String(p.status)), "provider must NOT be invoked");
  sb.cleanup();
});

H.test("T6b provider timeout / parse failure: fail-closed properties hold (no orderable intent)", () => {
  for (const scenario of ["provider_error", "parse_failure"]) {
    const sb = buildSandbox();
    const r = runChild(sb, { dry: true, cycles: 2 }, { QA_SCENARIO: scenario });
    const dec = recs(sb, "decisions.jsonl");
    const prov = recs(sb, "provider_health.jsonl");
    logEvidence(scenario + " decisions", dec.map((d) => ({ decisionClass: d.decisionClass, aiKind: d.aiKind, orderable: d.orderable, reason: d.reason })));
    logEvidence(scenario + " provider", prov.map((p) => p.status));
    assert.equal(r.code, 0);
    assert.ok(dec.length >= 1);
    for (const d of dec) {
      assert.equal(d.orderable, false, "a provider failure must never become an orderable intent");
      assert.notEqual(d.riskCode, "RISK_APPROVED", "must not be approved as a BUY/SELL");
    }
    sb.cleanup();
  }
});

// Strict clause of the brief: "解析失败/超时不得改写成 HOLD".
// F1 was reported fixed by the implementation owner (final sha 002653fe...).
// This is an INDEPENDENT re-run on the current on-disk file: it must now be
// NO_ACTION for both timeout and parse-failure. The failing assertion is KEPT
// (asserting NO_ACTION, not deleted); if the fix regressed it FAILS again.
H.test("F1 re-run/T6b-strict parse failure / timeout => NO_ACTION (not decisionClass=HOLD), fail-closed fields intact", () => {
  for (const scenario of ["provider_error", "parse_failure"]) {
    const sb = buildSandbox();
    const r = runChild(sb, { dry: true, cycles: 2 }, { QA_SCENARIO: scenario });
    const dec = recs(sb, "decisions.jsonl");
    const risk = recs(sb, "risk.jsonl");
    const prov = recs(sb, "provider_health.jsonl");
    const classes = dec.map((d) => d.decisionClass);
    logEvidence(scenario + " decisionClass", classes);
    logEvidence(scenario + " fields", dec.map((d) => ({ aiKind: d.aiKind, reason: d.reason, orderable: d.orderable, execution: d.execution, code: d.riskCode })));
    logEvidence(scenario + " failClosedReason", risk.map((x) => x.failClosedReason));
    logEvidence(scenario + " provider status", prov.map((p) => p.status));
    assert.equal(r.code, 0, "run must succeed (fail-closed records, not a crash)");
    assert.ok(dec.length >= 1);
    for (const d of dec) {
      assert.equal(d.decisionClass, "NO_ACTION",
        "F1: provider failure must NOT be rewritten as decisionClass:HOLD (brief clause 6)");
      assert.equal(d.orderable, false, "no orderable intent");
      assert.equal(d.execution, "NONE", "execution must be NONE");
      assert.ok(/PROVIDER_FAILED|FAILCLOSED/i.test(String(d.reason)), "reason must state provider-failed fail-closed");
      assert.notEqual(d.riskCode, "RISK_APPROVED", "must never be RISK_APPROVED");
    }
    // Two NO_ACTION variants must be distinguishable via failClosedReason.
    if (risk.length) assert.ok(risk.every((x) => x.failClosedReason === "PROVIDER_FAILED"),
      "provider-failure NO_ACTION must be tagged failClosedReason=PROVIDER_FAILED");
    sb.cleanup();
  }
});

// ===========================================================================
// T8 — independent reconciliation: engine-sourced values, basis, diff, noFills; NOT hardcoded
// ===========================================================================
H.test("T8 runtime: equity journal carries engine-sourced values + comparison basis + delta + noFills (not hardcoded 10000/MATCH)", () => {
  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 1 });
  const eq = recs(sb, "equity.jsonl")[0];
  logEvidence("equity record", {
    equity: eq.equity, reconcile: eq.reconcile, noFills: eq.noFills,
    hasBasis: !!eq.basis, delta: eq.basis && eq.basis.delta,
    engineEquity: eq.basis && eq.basis.engine && eq.basis.engine.equity,
    harnessEquity: eq.basis && eq.basis.harness && eq.basis.harness.equity,
    engineVersion: eq.basis && eq.basis.engine && eq.basis.engine.version,
  });
  assert.equal(r.code, 0);
  assert.ok(eq.basis && eq.basis.engine && eq.basis.harness, "must contain engine + harness comparison basis");
  assert.equal(typeof eq.basis.delta, "number", "must contain a numeric diff field");
  assert.equal(typeof eq.basis.engine.equity, "number", "engine actual value must be sourced");
  assert.equal(typeof eq.basis.engine.version, "number", "engine account state version must be present");
  assert.equal(eq.noFills, true, "no fills must be stated honestly");
  assert.ok(/no fills|无成交/i.test(String(eq.basis.note)), "note must state no-fills basis");

  // Static: the old hardcodings must be gone.
  const src = runnerSource();
  assert.ok(!src.includes('reconcile: "MATCH (frozen-engine account vs harness ledger)"'), "old hardcoded reconcile string must be absent");
  assert.ok(!/const equity = CONFIG_FREEZE\.virtualCapitalUsdt/.test(src), "old hardcoded equity must be absent");
  sb.cleanup();
});

H.test("T8 injection (counterexample): a non-default engine account yields equity != 10000 and reconcile MISMATCH (proves computed, not hardcoded)", () => {
  const acct = engine.createAccount();
  acct.cash = "9500.00000000";
  acct.positions = { BTCUSDT: { qty: "0.10000000", avgPrice: "70000.00000000", reservedQty: "0" } };
  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 1, initialAccount: acct });
  const eq = recs(sb, "equity.jsonl")[0];
  logEvidence("injected-account equity", {
    equity: eq.equity, reconcile: eq.reconcile,
    delta: eq.basis && eq.basis.delta,
    engineEquity: eq.basis && eq.basis.engine && eq.basis.engine.equity,
    harnessEquity: eq.basis && eq.basis.harness && eq.basis.harness.equity,
  });
  assert.equal(r.code, 0, "run with injected account must succeed");
  assert.notEqual(eq.equity, 10000, "equity must be derived from the account, not hardcoded 10000");
  assert.equal(eq.reconcile, "MISMATCH", "a real difference must be reported, not a hardcoded MATCH");
  assert.notEqual(eq.basis.delta, 0, "diff must be non-zero");
  sb.cleanup();
});

// ===========================================================================
// T9 — config freeze: modified copy => MISMATCH; new runId must not overwrite old freeze
// ===========================================================================
H.test("T9a config freeze: modifying the isolated legacy freeze copy triggers MISMATCH and refuses to start", () => {
  const sb = buildSandbox();
  const fp = join(sb.OUT, "config.freeze.json");
  const cfg = JSON.parse(readFileSync(fp, "utf8"));
  cfg.callBudgetUsd = 999; // tamper one field
  writeFileSync(fp, JSON.stringify(cfg, null, 2));
  const r = runChild(sb, { dry: true, cycles: 1 });
  logEvidence("code", r.code);
  logEvidence("output", firstLineOf(r));
  assert.notEqual(r.code, 0, "tampered freeze must refuse to start");
  assert.ok(/MISMATCH/.test(combined(r)), "must be a MISMATCH error: " + firstLineOf(r));
  sb.cleanup();
});

H.test("T9b new runId must NOT overwrite the legacy freeze (hash stable; old run dir preserved)", () => {
  const sb = buildSandbox();
  const fp = join(sb.OUT, "config.freeze.json");
  const before = sha256Hex(readFileSync(fp, "utf8"));
  const r1 = runChild(sb, { dry: true, cycles: 1 });
  const r2 = runChild(sb, { dry: true, cycles: 1, newRun: true }); // force a new runId
  const after = sha256Hex(readFileSync(fp, "utf8"));
  const dirs = activeRunDirs(sb.OUT);
  logEvidence("run1 / run2 code", [r1.code, r2.code]);
  logEvidence("freeze hash before / after", { before: before.slice(0, 24), after: after.slice(0, 24), equal: before === after });
  logEvidence("run dirs", dirs);
  assert.equal(r1.code, 0);
  assert.equal(r2.code, 0);
  assert.equal(before, after, "legacy freeze bytes must be unchanged (read-only)");
  assert.ok(dirs.length >= 2, "a new runId must create a new isolated dir, not overwrite the old one");
  sb.cleanup();
});

// ===========================================================================
// T10 — red lines + no network evidence
// ===========================================================================
H.test("T10 red lines + no-network: enableRealTrading/allowBuySell false, provider shows no real call, no egress attempt", () => {
  const src = runnerSource();
  const freeze = JSON.parse(readFileSync(join(REPO, "reports", "day14-artifacts", "config.freeze.json"), "utf8"));
  const badLiterals = src.match(/enableRealTrading\s*:\s*true|allowBuySell\s*:\s*true/g) || [];
  logEvidence("true-valued red-line literals in source", badLiterals);
  logEvidence("RED_LINES false present", /enableRealTrading:\s*false/.test(src) && /allowBuySell:\s*false/.test(src));
  logEvidence("freeze red lines", { enableRealTrading: freeze.enableRealTrading, allowBuySell: freeze.allowBuySell });
  assert.equal(badLiterals.length, 0, "no true-valued red-line literals");
  assert.equal(freeze.enableRealTrading, false);
  assert.equal(freeze.allowBuySell, false);

  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 3 });
  const prov = recs(sb, "provider_health.jsonl");
  const realCalls = prov.filter((p) => /CALL_OK|LIVE|REAL_CALL/i.test(String(p.status)) || (Number(p.costUsd) > 0) || (Number(p.latencyMs) > 0));
  const nonetInThisRun = /P0-QA-NONET-ATTEMPT/.test(combined(r));
  const runJsonPath = findArtifact(sb.OUT, "run.json");
  const runJson = existsSync(runJsonPath) ? JSON.parse(readFileSync(runJsonPath, "utf8")) : null;
  logEvidence("provider statuses", prov.map((p) => ({ status: p.status, costUsd: p.costUsd, latencyMs: p.latencyMs })));
  logEvidence("real-call-shaped records", realCalls.length);
  logEvidence("network guard triggered", nonetInThisRun);
  logEvidence("run.json redLines", runJson && runJson.redLines);
  assert.equal(r.code, 0, "a dry run with red lines intact must succeed");
  assert.equal(realCalls.length, 0, "no real/paid provider call evidence");
  assert.equal(nonetInThisRun, false, "no network egress was attempted (guard never fired)");
  if (runJson && runJson.redLines) {
    assert.equal(runJson.redLines.enableRealTrading, false);
    assert.equal(runJson.redLines.allowBuySell, false);
    assert.equal(runJson.redLines.noEgress, true);
  }
  sb.cleanup();
});

// ===========================================================================
// R1③ — legal BUY: full verified context + fixed limits => RISK_APPROVED;
//        orderable computed; red line still prevents any execution.
// ===========================================================================
H.test("R1③ legal BUY with verified context => RISK_APPROVED, context non-null on non-HOLD path, limits fixed, execution SHADOW_GATED", () => {
  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 2 }, { QA_SCENARIO: "buy_provider" });
  const dec = recs(sb, "decisions.jsonl");
  const risk = recs(sb, "risk.jsonl");
  const eq = recs(sb, "equity.jsonl");
  logEvidence("run code", r.code);
  logEvidence("decisions", dec.map((d) => ({ action: d.action, decisionClass: d.decisionClass, orderable: d.orderable, execution: d.execution, code: d.riskCode })));
  logEvidence("risk", risk.map((x) => ({ gate: x.gate, code: x.code, approved: x.approved, orderable: x.orderable, context: x.context, riskLimits: x.riskLimits })));
  assert.equal(r.code, 0, "legal BUY run must succeed");
  assert.ok(dec.length >= 1);
  // A RISK_APPROVED must appear (BUY passed the full non-HOLD validation).
  const approved = risk.filter((x) => x.code === "RISK_APPROVED" && x.approved === true);
  logEvidence("RISK_APPROVED records", approved.length);
  assert.ok(approved.length >= 1, "R1③: a legal BUY with verified context must reach RISK_APPROVED");
  // On the NON-HOLD path context must NOT be null (brief: context:null only legal for HOLD/fail-closed).
  const nonHoldRisk = risk.filter((x) => x.action !== "HOLD");
  logEvidence("non-HOLD risk records with context:null", nonHoldRisk.filter((x) => x.context === null).length);
  for (const x of nonHoldRisk) {
    assert.ok(x.context && typeof x.context === "object", "non-HOLD path must carry a full built context, not null");
    assert.equal(x.context.markQuality, "verified", "context mark_quality must be verified");
  }
  // limits are fixed by the runner (not overridable / not relaxed by the provider).
  for (const x of risk) {
    assert.deepEqual(x.riskLimits, { minNotionalUsd: 5, maxNotionalUsd: 1000, maxQuantity: 10, maxContextAgeMs: 60000 },
      "riskLimits must equal the frozen defaults (not relaxed by external input)");
  }
  // orderable=true but RED_LINES.allowBuySell=false -> no execution, no fill.
  for (const d of dec) {
    assert.equal(d.orderable, true, "legal BUY is orderable");
    assert.equal(d.execution, "SHADOW_GATED", "but must be gated by allowBuySell=false (no real order)");
  }
  for (const e of eq) assert.equal(e.noFills, true, "SHADOW_GATED => no fills");
  sb.cleanup();
});

// ===========================================================================
// R1③ counter-example — non-whitelisted BUY must be rejected, even with 0.99 confidence
// ===========================================================================
H.test("R1③ counterexample: BUY on non-whitelisted symbol => approved:false (RISK_SYMBOL_NOT_ALLOWED), zero RISK_APPROVED, context non-null", () => {
  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 2 }, { QA_SCENARIO: "bad_symbol_provider" });
  const dec = recs(sb, "decisions.jsonl");
  const risk = recs(sb, "risk.jsonl");
  logEvidence("run code", r.code);
  logEvidence("decisions", dec.map((d) => ({ action: d.action, symbol: d.symbol, orderable: d.orderable, execution: d.execution, code: d.riskCode })));
  logEvidence("risk codes", risk.map((x) => ({ code: x.code, approved: x.approved, orderable: x.orderable })));
  assert.equal(r.code, 0);
  const all = allJournalRecords(sb);
  assert.equal(all.filter((x) => x.code === "RISK_APPROVED" || x.riskCode === "RISK_APPROVED").length, 0,
    "no RISK_APPROVED for an illegal symbol");
  for (const d of dec) {
    assert.equal(d.orderable, false, "illegal BUY must not be orderable");
    assert.equal(d.riskCode, "RISK_SYMBOL_NOT_ALLOWED", "must be rejected with the declared RISK_CODES value");
  }
  for (const x of risk.filter((z) => z.action !== "HOLD")) assert.ok(x.context !== null, "non-HOLD context must still be built (not null)");
  sb.cleanup();
});

// ===========================================================================
// R1 cross-check via the implementation-provided injection point (riskCheckImpl:null)
// ===========================================================================
H.test("R1 cross-check: riskCheckImpl:null => fail-closed throw, zero logs, zero RISK_APPROVED (no frozen file touched)", () => {
  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 2 }, { QA_SCENARIO: "riskcheck_null" });
  const recsAll = allJournalRecords(sb);
  logEvidence("code", r.code);
  logEvidence("output", firstLineOf(r));
  logEvidence("run dirs / journal records", [activeRunDirs(sb.OUT).length, recsAll.length]);
  assert.notEqual(r.code, 0, "must fail closed");
  assert.ok(/FATAL fail-closed|formal riskCheck unavailable/.test(combined(r)), "must be the fail-closed guard: " + firstLineOf(r));
  assert.equal(recsAll.filter((x) => x.code === "RISK_APPROVED").length, 0, "zero RISK_APPROVED");
  sb.cleanup();
});

// ===========================================================================
// R6 — independentQA no longer declares PASS on mere file existence.
// Construct: ALL files exist & non-empty (the old existence-only condition),
// but a journal's content is corrupted -> implementation MUST still reject.
// ===========================================================================
H.test("R6 not existence-based: ALL artifacts exist & non-empty (old independentQA would PASS) but tampered chain must still be refused", () => {
  const src = runnerSource();
  const hasOldFn = /function\s+independentQA\s*\(/.test(src);
  const internalOnly = /INTERNAL_SANITY_ONLY/.test(src);
  logEvidence("old independentQA() present", hasOldFn);
  logEvidence("internalSanity self-labelled non-independent", internalOnly);
  assert.equal(hasOldFn, false, "the old existence-only independentQA() must be gone");
  assert.equal(internalOnly, true, "internal sanity must be explicitly labelled non-independent");

  const sb = buildSandbox();
  const r1 = runChild(sb, { dry: true, cycles: 2 });
  assert.equal(r1.code, 0);
  const chk = existenceChecklist(sb);
  logEvidence("existence checklist (all files exist & non-empty)", chk);
  assert.equal(chk.allExistNonEmpty, true, "precondition: every artifact exists and is non-empty (old QA would pass)");
  // Corrupt one record's CONTENT (files still exist & non-empty).
  const p = findArtifact(sb.OUT, "decisions.jsonl");
  const lines = readLines(p).map((l) => JSON.parse(l));
  lines[1].reason = "P0-QA-CORRUPTED-WHILE-FILES-INTACT";
  writeJsonl(p, lines);
  const stillThere = existenceChecklist(sb);
  logEvidence("existence checklist AFTER corruption", stillThere.allExistNonEmpty);
  assert.equal(stillThere.allExistNonEmpty, true, "files STILL exist & non-empty after corruption");
  const r2 = runChild(sb, { dry: true, cycles: 0 });
  logEvidence("resume code (must be non-zero)", r2.code);
  logEvidence("resume output", firstLineOf(r2));
  assert.notEqual(r2.code, 0,
    "R6: despite all files existing & non-empty, a corrupted chain MUST be refused (not existence-based PASS)");
  sb.cleanup();
});

// ===========================================================================
// Dual cost models: two separate ledgers, venue_verified NOT back-filled
// ===========================================================================
H.test("Cost models: engine_conservative (0.001/0.015) and venue_verified (null/NOT_MEASURED) kept separate, not merged", () => {
  const sb = buildSandbox();
  const r = runChild(sb, { dry: true, cycles: 1 });
  assert.equal(r.code, 0);
  const runJsonPath = findArtifact(sb.OUT, "run.json");
  const runJson = JSON.parse(readFileSync(runJsonPath, "utf8"));
  const cm = runJson.costModels;
  logEvidence("engine_conservative", cm && cm.engine_conservative);
  logEvidence("venue_verified", cm && cm.venue_verified);
  assert.ok(cm && cm.engine_conservative && cm.venue_verified, "both cost models present");
  assert.equal(cm.engine_conservative.feeRate, 0.001);
  assert.equal(cm.engine_conservative.slippagePct, 0.015);
  assert.equal(cm.venue_verified.feeRate, null, "venue feeRate must remain null (UNVERIFIED), not back-filled");
  assert.equal(cm.venue_verified.slippagePct, null, "venue slippagePct must remain null, not back-filled");
  assert.ok(/NOT_MEASURED|UNVERIFIED/.test(cm.venue_verified.status + " " + cm.venue_verified.provenance), "venue must be labelled not-measured");
  assert.notDeepEqual(cm.engine_conservative.feeRate, cm.venue_verified.feeRate, "the two ledgers must NOT be merged");
  sb.cleanup();
});

// ===========================================================================
// F-12 — QA-side external-call count (this is an INDEPENDENT count, separate
//         from the implementation owner's written statement).
// ===========================================================================
H.test("F-12 QA-side external calls = 0 (guard source, static scan, dynamic child-stderr count)", () => {
  const files = [
    join(REPO, "apps", "command_center_3d", "server", "ai", "tests", "day14-p0-lib.mjs"),
    join(REPO, "apps", "command_center_3d", "server", "ai", "tests", "day14-p0-contracts.mjs"),
    join(REPO, "apps", "command_center_3d", "server", "ai", "tests", "day14-p0-runner.mjs"),
  ];
  const src = files.map((p) => readFileSync(p, "utf8")).join("\n");
  const netImports = (src.match(/from\s+["'](?:node:https?|node:net|node:tls|undici|axios|ws)["']/g) || []).length;
  const netCalls = (src.match(/(?:https?\.request|https?\.get|net\.connect|net\.createConnection|new\s+WebSocket)\s*\(/g) || []).length;
  const guardInstallations = (src.match(/globalThis\.fetch\s*=/g) || []).length;
  const guardIsActive = /P0-QA-NONET/.test(String(globalThis.fetch));
  logEvidence("network module imports in QA sources", netImports);
  logEvidence("raw network call primitives in QA sources", netCalls);
  logEvidence("globalThis.fetch guard installations (lib + child driver)", guardInstallations);
  logEvidence("parent globalThis.fetch is the NONET guard", guardIsActive);
  logEvidence("child processes spawned (sandboxed local node)", childRuns());
  logEvidence("P0-QA-NONET-ATTEMPT hits across ALL child stderr (blocked egress)", nonetAttempts());
  assert.equal(netImports, 0, "no http/https/net/undici/axios/ws imports in QA code");
  assert.equal(netCalls, 0, "no raw network call primitives in QA code");
  assert.ok(guardInstallations >= 2, "fetch guard installed in parent lib AND every child driver");
  assert.equal(guardIsActive, true, "parent fetch is hard-replaced by the nonet guard");
  assert.ok(childRuns() > 0, "children actually ran (so the 0 count is meaningful, not vacuous)");
  assert.equal(nonetAttempts(), 0, "F-12: zero real external HTTP / paid-model / order calls; guard never fired");
});

await H.run();
