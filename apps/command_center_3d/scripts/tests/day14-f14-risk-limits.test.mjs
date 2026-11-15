#!/usr/bin/env node
/**
 * F-14 回归测试：统一「生效单笔名义上限」口径（200 vs 1000）
 *
 * 缺陷（JEV_V2_FINAL_GAP_REGISTER.md § F-14）：
 *   day14-runner.mjs 里 CFG.maxNotionalPerTrade = LEGACY_FREEZE_EXPECTED.maxNotionalPerTrade = 200
 *   是死配置——风险判定从未读取它；真实生效口径是正式风控 RISK_LIMITS.maxNotionalUsd = 1000。
 *   任何「上限 = 200」的推断都是错的。
 *
 * 修复后：
 *   - CFG 不再携带会误导的 maxNotionalPerTrade（死字段已删除）；
 *   - 新增 EFFECTIVE_MAX_NOTIONAL_USD，唯一权威来源 = RISK_LIMITS.maxNotionalUsd (1000)；
 *   - 风险判定路径用 limits: RISK_LIMITS（1000），绝不回退到 legacy 200。
 *
 * 证明：
 *   1) 正向：EFFECTIVE_MAX_NOTIONAL_USD === 1000 === RISK_LIMITS.maxNotionalUsd；CFG 不再暴露误导字段
 *   2) 负向/变异（F-14 失败模式）：LEGACY_FREEZE_EXPECTED.maxNotionalPerTrade 仍是 200，但与
 *      EFFECTIVE_MAX_NOTIONAL_USD(1000) 必然相异——证明 legacy 200 不是生效口径
 *   3) 生效口径实测：正式风控门 riskCheck 在名义 500→放行、2500→LIMIT_EXCEEDED（上限确为 1000，不是 200）
 *   4) 静态源码扫描：runner 中风险调用使用 limits: RISK_LIMITS，且不含回退到 legacy 200 的名义检查
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  EFFECTIVE_MAX_NOTIONAL_USD, CFG, RISK_LIMITS, LEGACY_FREEZE_EXPECTED,
} from "../day14-runner.mjs";
import { riskCheck, DEFAULT_RISK_LIMITS, RISK_CODES } from "../../server/ai/risk-gate.mjs";

const RUNNER_SRC = readFileSync(join(import.meta.dirname, "..", "day14-runner.mjs"), "utf8");

function makeDecision(quantity) {
  return {
    schemaVersion: "jev.ai.decision/v1", strategyId: "strat1",
    symbol: "BTCUSDT", action: "BUY", side: "BUY",
    orderType: "MARKET", quantity, limitPrice: null,
    confidence: 0.5, reason: "F-14-test", riskFlags: [],
    marketContextId: "mc-0001", validForSeconds: 30,
  };
}
function makeContext(nowMs) {
  return {
    marketContextId: "mc-0001", symbol: "BTCUSDT",
    markPriceUsd: 50000, priceTs: new Date(nowMs).toISOString(), markQuality: "verified",
  };
}

test("F-14 positive: effective limit is 1000 and CFG no longer exposes the misleading 200", () => {
  assert.equal(EFFECTIVE_MAX_NOTIONAL_USD, 1000, "effective limit must be 1000");
  assert.equal(EFFECTIVE_MAX_NOTIONAL_USD, RISK_LIMITS.maxNotionalUsd, "effective limit wired from RISK_LIMITS");
  assert.equal(RISK_LIMITS.maxNotionalUsd, 1000, "RISK_LIMITS.maxNotionalUsd is 1000");
  assert.equal(CFG.maxNotionalPerTrade, undefined, "dead/misleading CFG.maxNotionalPerTrade removed");
});

test("F-14 negative/mutation: legacy 200 snapshot diverges from effective 1000 (must not be inferred as the cap)", () => {
  assert.equal(LEGACY_FREEZE_EXPECTED.maxNotionalPerTrade, 200, "legacy freeze snapshot still 200 (tamper-evident, read-only)");
  assert.notEqual(EFFECTIVE_MAX_NOTIONAL_USD, LEGACY_FREEZE_EXPECTED.maxNotionalPerTrade,
    "effective limit (1000) != legacy snapshot (200): 200 is NOT the effective cap");
});

test("F-14 enforcement: formal risk gate caps at 1000, not 200 (real measured notional)", () => {
  const nowMs = Date.now();
  const under = riskCheck({ decision: makeDecision(0.01), context: makeContext(nowMs), nowMs, limits: DEFAULT_RISK_LIMITS });
  // 0.01 * 50000 = 500 USD <= 1000 => approved
  assert.equal(under.approved, true, "notional 500 (<=1000) must be approved");
  assert.equal(under.code, RISK_CODES.APPROVED);

  const over = riskCheck({ decision: makeDecision(0.05), context: makeContext(nowMs), nowMs, limits: DEFAULT_RISK_LIMITS });
  // 0.05 * 50000 = 2500 USD > 1000 => LIMIT_EXCEEDED
  assert.equal(over.approved, false, "notional 2500 (>1000) must be rejected");
  assert.equal(over.code, RISK_CODES.LIMIT_EXCEEDED, "rejection code must be LIMIT_EXCEEDED");
  assert.equal(over.max_notional_usd, 1000, "rejection reports the real cap 1000, not 200");
});

test("F-14 static: runner risk path uses RISK_LIMITS (1000) via locked gate and never falls back to legacy notional", () => {
  // 风险判定调用必须经由 effectiveRiskGate 且显式传入 limits: RISK_LIMITS（生效口径）
  assert.ok(/effectiveRiskGate\(\s*\{[^}]*limits:\s*RISK_LIMITS\s*\}/s.test(RUNNER_SRC),
    "runner must call effectiveRiskGate with limits: RISK_LIMITS");
  // 锁定的注入面必须存在（F-15）：lockRiskGate 保证注入门无法绕过正式门
  assert.ok(/function\s+lockRiskGate/.test(RUNNER_SRC), "lockRiskGate must exist to lock the riskCheckImpl injection surface");
  // 不得存在「名义 > CFG.maxNotionalPerTrade(200)」这类回退到 legacy 200 的简化名义检查
  assert.equal(/notional\s*>\s*CFG\.maxNotionalPerTrade/.test(RUNNER_SRC), false,
    "no fallback notional check referencing the dead CFG.maxNotionalPerTrade(200)");
});
