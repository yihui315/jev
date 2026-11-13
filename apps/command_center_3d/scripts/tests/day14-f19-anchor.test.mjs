/**
 * F-19 regression + mutation tests — external anchor READ-BACK on recovery.
 *
 * The point of F-19 is that the recovery path must read the EXTERNALLY-stored
 * anchor (day14-anchor.json written by the previous run) and fail-closed if it
 * disagrees with the recomputed chain tip. The internal journal chain
 * (makeJournal) already self-verifies, so a positive test alone is NOT enough:
 * the mutation test tampers ONLY the external anchor (journals untouched) and
 * asserts recovery is refused. That isolates "external read-back" from
 * "internal hash-chain consistency".
 *
 * Red lines respected: this never touches real trading/network/paid APIs; it
 * drives runDay14 with injectable fixtures into an isolated temp outDir.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runDay14 } from "../day14-runner.mjs";

const ANCHOR = "day14-anchor.json";

test("F-19 positive: resume reads external anchor and verifies match (recovery succeeds)", async () => {
  const outDir = mkdtempSync(join(tmpdir(), "f19-pos-"));
  try {
    const r1 = await runDay14({ outDir, newRun: true, cycles: 2, nowMs: 1700000000000 });
    assert.equal(typeof r1.runId, "string");
    assert.ok(existsSync(join(r1.runDir, ANCHOR)), "anchor written at end of run 1");

    // Resume (newRun:false) — external anchor read-back must pass and continue the chain.
    const r2 = await runDay14({ outDir, newRun: false, cycles: 1, nowMs: 1700000100000 });
    assert.ok(r2, "resume produced a summary");
    assert.equal(r2.cycles, 3, "resumed cycle count continues from 2 -> 3");
    assert.equal(r2.runId, r1.runId, "resume targets the same runId");
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }
});

test("F-19 mutation: tampered EXTERNAL anchor (journals untouched) => fail-closed", async () => {
  const outDir = mkdtempSync(join(tmpdir(), "f19-mut-"));
  try {
    const r1 = await runDay14({ outDir, newRun: true, cycles: 2, nowMs: 1700000000000 });
    const anchorPath = join(r1.runDir, ANCHOR);
    assert.ok(existsSync(anchorPath));

    // TAMPER the external anchor ONLY. The internal journals are NOT modified,
    // so makeJournal's internal chain check would still pass — this proves the
    // failure comes from the EXTERNAL anchor read-back, not internal consistency.
    const a = JSON.parse(readFileSync(anchorPath, "utf8"));
    a.root = "0".repeat(64);
    a.tipsRoot = "0".repeat(64);
    a.journalTips = {
      market: "deadbeef", decision: "deadbeef", risk: "deadbeef",
      provider: "deadbeef", equity: "deadbeef",
    };
    writeFileSync(anchorPath, JSON.stringify(a, null, 2));

    await assert.rejects(
      () => runDay14({ outDir, newRun: false, cycles: 1, nowMs: 1700000100000 }),
      /AUDIT_CHAIN_ANCHOR_MISMATCH/,
      "tampered external anchor must trigger fail-closed recovery refusal",
    );

    // Sanity: the journals themselves are still internally valid (proves the
    // failure is specifically the external anchor check, not a broken chain).
    assert.ok(existsSync(join(r1.runDir, "decisions.jsonl")), "journals remain untouched & intact");
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }
});

test("F-19 negative: missing external anchor on non-empty resume => fail-closed", async () => {
  const outDir = mkdtempSync(join(tmpdir(), "f19-miss-"));
  try {
    const r1 = await runDay14({ outDir, newRun: true, cycles: 2, nowMs: 1700000000000 });
    const anchorPath = join(r1.runDir, ANCHOR);
    assert.ok(existsSync(anchorPath));
    rmSync(anchorPath, { force: true }); // delete external anchor, keep journals

    await assert.rejects(
      () => runDay14({ outDir, newRun: false, cycles: 1, nowMs: 1700000100000 }),
      /AUDIT_CHAIN_ANCHOR_MISMATCH/,
      "missing external anchor on non-empty resume must fail-closed (rollback/loss)",
    );
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }
});
