#!/usr/bin/env node
/**
 * F-15 回归测试：riskBreach 不再恒 false + riskCheckImpl 注入面锁定
 *
 * 缺陷（JEV_V2_FINAL_GAP_REGISTER.md § F-15）：
 *   (1) s.riskBreach 永远为 false —— 真实风控拦截从未写回 state，统计失真；
 *   (2) riskCheckImpl 为可注入参数，调用方可替换整个风控门（绕过正式门）。
 *
 * 修复：
 *   (1) 在 genuine 风控拒绝（RISK_REJECTED）时置 s.riskBreach = true；
 *   (2) lockRiskGate：注入的门只能「收紧」（与正式门取交集），绝不能绕过正式门。
 *
 * 证明：
 *   1) 正向：一次真实拒绝后 summary.riskBreach === true（不再恒 false）
 *   2) 对照：默认运行无拒绝时 summary.riskBreach === false
 *   3) 负向/变异：注入「永远放行」的门 + 超限单 => 仍被拒绝（riskBreach 仍为 true，注入门无法绕过）
 *   4) 静态：runner 不再直接调用 riskCheckImpl({...})，全部经由 effectiveRiskGate（锁定已接线）
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runDay14 } from "../day14-runner.mjs";

const RUNNER_SRC = readFileSync(join(import.meta.dirname, "..", "day14-runner.mjs"), "utf8");
const NOW = 1_700_000_000_000;

function tmpOut() { return mkdtempSync(join(tmpdir(), "f15-")); }

// 注入一个返回「超限 BUY 意图」的 provider：quantity=1 × 约 70000 价格 => 名义远超 1000 => 正式门 LIMIT_EXCEEDED
function oversizedBuyProvider() {
  return async () => ({
    ok: true, kind: "fixture", model: "f", status: "FIXTURE", costUsd: 0, latencyMs: 0,
    intent: { action: "BUY", symbol: "BTCUSDT", quantity: 1, confidence: 0.9, reason: "oversized" },
  });
}
// 注入一个「永远放行」的门——用于证明锁定后它无法绕过正式门
const permissiveGate = () => ({ approved: true, code: "RISK_APPROVED", reason: "PERMISSIVE_INJECTED" });

test("F-15 part1 positive: a genuine risk rejection sets summary.riskBreach = true", async () => {
  const out = tmpOut();
  try {
    const summary = await runDay14({ outDir: out, newRun: true, cycles: 1, nowMs: NOW, providerImpl: oversizedBuyProvider() });
    assert.equal(summary.riskBreach, true, "rejected order must set riskBreach true (no longer always false)");
  } finally { rmSync(out, { recursive: true, force: true }); }
});

test("F-15 part1 control: default run with no real rejection keeps riskBreach = false", async () => {
  const out = tmpOut();
  try {
    const summary = await runDay14({ outDir: out, newRun: true, cycles: 1, nowMs: NOW });
    assert.equal(summary.riskBreach, false, "no rejection => riskBreach stays false");
  } finally { rmSync(out, { recursive: true, force: true }); }
});

test("F-15 part2 negative/mutation: a permissive injected gate CANNOT bypass the formal gate", async () => {
  const out = tmpOut();
  try {
    const summary = await runDay14({
      outDir: out, newRun: true, cycles: 1, nowMs: NOW,
      providerImpl: oversizedBuyProvider(), riskCheckImpl: permissiveGate,
    });
    assert.equal(summary.riskBreach, true,
      "injected permissive gate must NOT bypass formal rejection (riskBreach still true)");
  } finally { rmSync(out, { recursive: true, force: true }); }
});

test("F-15 part2 static: riskCheckImpl injection surface is locked (all calls go through effectiveRiskGate)", () => {
  // 不得再有「直接调用 riskCheckImpl({...})」——必须经过锁定的 effectiveRiskGate
  assert.equal(/riskCheckImpl\(\s*\{/.test(RUNNER_SRC), false,
    "no direct riskCheckImpl({...}) call; all risk checks route through locked effectiveRiskGate");
  assert.ok(/function\s+lockRiskGate/.test(RUNNER_SRC), "lockRiskGate must exist");
  assert.ok(/const\s+effectiveRiskGate\s*=\s*lockRiskGate\(riskCheckImpl,\s*formalRiskGate\)/.test(RUNNER_SRC),
    "effectiveRiskGate must be wired from lockRiskGate(riskCheckImpl, formalRiskGate)");
});
