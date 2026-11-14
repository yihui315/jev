// C9 (F-16 根因) 绑定测试：verified / mark_quality 语义必须绑定真实出网证据。
//
// 核心命题：
//   1) 即便 mark_quality === "verified"，只要 networkContext 属于 synthetic（local_test_shim /
//      fixture / synthetic），风控不得认定其为「真实出网核实」——realEgressVerified 必须为 false。
//   2) 真实出网上下文（networkContext 为真实 egress 或历史/网关未声明）才 realEgressVerified===true。
//   3) 即便 P3 解锁 allowBuySell，synthetic 上下文也永远走不到 ENGINE_EXEC（真实成交）。
//
// 不破坏既有契约：R1③ 要求的 markQuality:"verified" + orderable + SHADOW_GATED（fail-closed）保持不变。

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { riskCheck, isRealEgressNetworkContext } from "../../server/ai/risk-gate.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const RUNNER_SRC = readFileSync(join(HERE, "../day14-runner.mjs"), "utf8");
const RISK_GATE_SRC = readFileSync(join(HERE, "../../server/ai/risk-gate.mjs"), "utf8");

function legalBuy(nowMs) {
  return {
    schemaVersion: "jev.ai.decision/v1",
    strategyId: "strat1",
    symbol: "BTCUSDT",
    action: "BUY",
    side: "BUY",
    orderType: "MARKET",
    quantity: 0.01, // notional ≈ 0.01*50000 = 500 USD，落在 [5,1000] 内
    limitPrice: null,
    confidence: 0.5,
    reason: "C9-test",
    riskFlags: [],
    marketContextId: "mc_test_123456",
    validForSeconds: 60,
  };
}

function realEgressContext(nowMs) {
  return {
    symbol: "BTCUSDT",
    markQuality: "verified",
    markPriceUsd: 50000,
    price_ts: new Date(nowMs).toISOString(),
    networkContext: "sandbox_proxy_egress_binance_us",
  };
}

function syntheticContext(nowMs) {
  return {
    symbol: "BTCUSDT",
    markQuality: "verified", // 故意标称 verified，模拟 C9 缺陷：fixture 行情被硬编码 verified
    markPriceUsd: 50000,
    price_ts: new Date(nowMs).toISOString(),
    networkContext: "local_test_shim", // 诚实标注非真实 egress
  };
}

function notVerifiedContext(nowMs) {
  return {
    symbol: "BTCUSDT",
    markQuality: "synthetic",
    markPriceUsd: 50000,
    price_ts: new Date(nowMs).toISOString(),
    networkContext: "local_test_shim",
  };
}

const LIMITS = { minNotionalUsd: 5, maxNotionalUsd: 1000, maxQuantity: 10, maxContextAgeMs: 60000 };

test("C9 正向：真实出网上下文 mark_quality=verified => approved 且 realEgressVerified=true", () => {
  const nowMs = Date.now();
  const r = riskCheck({ decision: legalBuy(nowMs), context: realEgressContext(nowMs), nowMs, limits: LIMITS });
  assert.equal(r.approved, true, "real-egress verified context must be approved");
  assert.equal(r.realEgressVerified, true, "real-egress context must be realEgressVerified");
});

test("C9 负向/变异：synthetic 上下文标称 verified => approved 但 realEgressVerified=false（verified 串不能单独放行真实出网）", () => {
  const nowMs = Date.now();
  const r = riskCheck({ decision: legalBuy(nowMs), context: syntheticContext(nowMs), nowMs, limits: LIMITS });
  // mark_quality 仍是 verified，所以门仍「批准」（保留 paper/sim 路由，不破坏 R1③）
  assert.equal(r.approved, true, "synthetic-but-verified still approved for paper/sim routing");
  // 但真实出网核实必须为 false —— 这是 C9 的绑定
  assert.equal(r.realEgressVerified, false, "synthetic context must NOT be realEgressVerified even if mark_quality=verified");
});

test("C9：mark_quality 非 verified => 门拒绝且 realEgressVerified=false", () => {
  const nowMs = Date.now();
  const r = riskCheck({ decision: legalBuy(nowMs), context: notVerifiedContext(nowMs), nowMs, limits: LIMITS });
  assert.equal(r.approved, false, "non-verified context rejected");
  assert.equal(r.realEgressVerified, false, "rejected context is not real-egress-verified");
});

test("C9 路由守卫：P3 解锁后 synthetic 上下文永远到不了 ENGINE_EXEC（真实成交），real 上下文可以", () => {
  const nowMs = Date.now();
  const allowBuySell = true; // 模拟 P3 解锁
  const realGate = riskCheck({ decision: legalBuy(nowMs), context: realEgressContext(nowMs), nowMs, limits: LIMITS });
  const synGate = riskCheck({ decision: legalBuy(nowMs), context: syntheticContext(nowMs), nowMs, limits: LIMITS });

  const realOrderable = realGate.approved && realGate.code === "RISK_APPROVED" && legalBuy(nowMs).action !== "HOLD";
  const realRealOrderable = realOrderable && allowBuySell && realGate.realEgressVerified;
  const synOrderable = synGate.approved && synGate.code === "RISK_APPROVED" && legalBuy(nowMs).action !== "HOLD";
  const synRealOrderable = synOrderable && allowBuySell && synGate.realEgressVerified;

  assert.equal(realRealOrderable, true, "real-egress context WOULD reach ENGINE_EXEC under P3");
  assert.equal(synRealOrderable, false, "synthetic context CANNOT reach ENGINE_EXEC even under P3 unlock (C9)");
});

test("C9 单元：isRealEgressNetworkContext 判定", () => {
  assert.equal(isRealEgressNetworkContext(null), true, "未声明（网关/历史路径）维持现状");
  assert.equal(isRealEgressNetworkContext(undefined), true, "undefined 同 null");
  assert.equal(isRealEgressNetworkContext("local_test_shim"), false, "local_test_shim 是 synthetic");
  assert.equal(isRealEgressNetworkContext("fixture"), false, "fixture 是 synthetic");
  assert.equal(isRealEgressNetworkContext("synthetic"), false, "synthetic 是 synthetic");
  assert.equal(isRealEgressNetworkContext("sandbox_proxy_egress_binance_us"), true, "真实 egress 上下文放行");
});

test("C9 静态：runner 已接线真实出网绑定（import + 路由守卫 + 阻断分支）", () => {
  assert.ok(/isRealEgressNetworkContext/.test(RUNNER_SRC), "runner must import isRealEgressNetworkContext");
  assert.ok(/REJECTED_SYNTHETIC_NO_REAL_EGRESS/.test(RUNNER_SRC), "runner must have the C9 synthetic-reject branch");
  assert.ok(/orderable && RED_LINES\.allowBuySell && realEgressVerified/.test(RUNNER_SRC),
    "runner routing must gate ENGINE_EXEC on realEgressVerified");
  assert.ok(/orderable && RED_LINES\.allowBuySell && !realEgressVerified/.test(RUNNER_SRC),
    "runner must block synthetic under P3 unlock");
});

test("C9 静态：risk-gate 已声明 SYNTHETIC_NETWORK_CONTEXTS、导出 isRealEgressNetworkContext 并在 normalizeContext 透传 networkContext", () => {
  assert.ok(/SYNTHETIC_NETWORK_CONTEXTS/.test(RISK_GATE_SRC), "gate must declare synthetic blocklist");
  assert.ok(/export function isRealEgressNetworkContext/.test(RISK_GATE_SRC), "gate must export the helper");
  assert.ok(/realEgressVerified:\s*isRealEgressNetworkContext/.test(RISK_GATE_SRC), "approve return must carry realEgressVerified");
  assert.ok(/networkContext:\s*input\.networkContext/.test(RISK_GATE_SRC), "normalizeContext must thread networkContext");
});
