/**
 * P3/P4 可验证引擎骨架 - 契约测试（目录根，CI 引用此路径）。
 *
 * 用 node:assert/strict 手写断言；`node <本文件>` exit 0 才算通过，打印 PASS/FAIL 计数。
 * 覆盖主理人要求的 ①–⑫，外加只读扫描仓库 decisions.jsonl 证明 D 臂 NO_DATA 成立。
 * 纪律：纯断言、零网络、不写任何持久化绩效报告、不改任何既有测试。
 *
 * 范围说明：本目录只可 import 冻结引擎 __test 与 decimal.mjs（见 fee-wrap / _kit）。
 * 绝不 import day14-runner.mjs / day14-verify.mjs / verification.mjs / handlers /
 * fetchMarketContext —— T5a/T8 与 REQUIRED_FOLDS 仅「复用语义、注明出处」。
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { __test as engine } from "../../vendor/nr_trusted/functions/_lib/paper-server.mjs";
import * as M from "./index.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../../..");

let PASS = 0, FAIL = 0;
const results = [];
function check(name, fn) {
  try {
    fn();
    PASS++;
    results.push({ name, status: "PASS" });
    console.log("[PASS] " + name);
  } catch (e) {
    FAIL++;
    results.push({ name, status: "FAIL", error: String(e && e.message || e) });
    console.log("[FAIL] " + name + " :: " + String(e && e.message || e));
  }
}

function collectModuleSources() {
  const rootFiles = readdirSync(HERE).filter((f) => f.endsWith(".mjs") && f !== "p3-contracts.mjs");
  const armFiles = readdirSync(join(HERE, "arms")).filter((f) => f.endsWith(".mjs"));
  const all = [
    ...rootFiles.map((f) => ({ p: join(HERE, f), text: readFileSync(join(HERE, f), "utf8") })),
    ...armFiles.map((f) => ({ p: join(HERE, "arms", f), text: readFileSync(join(HERE, "arms", f), "utf8") })),
  ];
  return all;
}

// ① computeFee 与 __test.simulateNewOrder 契约相等
check("① fee-wrap.computeFee 与冻结引擎 simulateNewOrder fills[0].fee 契约相等（容差 1e-8）", () => {
  const r = M.assertEngineFeeContract();
  assert.equal(r.status, "FEE_CONTRACT_MATCHES_FROZEN_ENGINE");
  assert.equal(r.provenance, "SYNTHETIC_FIXTURE", "费率契约断言的 provenance 必须显式为合成 fixture，不得留空");
  assert.equal(r.fillFee, r.computedFee);
  assert.ok(r.delta <= 1e-8);
  assert.ok(r.fills >= 1, "必须真的产生过 fill，不能是弱测试");
  assert.notEqual(r.feeRate, null);
  // feeRate 来自冻结引擎，且 computeFee 对同一名义金额可重复
  assert.equal(r.computedFee, M.computeFee(r.qty * r.price));
  assert.equal(typeof M.ENGINE_FEE_RATE, "string");
});

// ② assertNoMerge 对三种合并/回填企图均抛错
check("② assertNoMerge 对三种合并/回填企图均抛错，合法双口径并列不抛", () => {
  const bad = [
    { name: "合并-多 id 数组", report: { cost_model_id: ["engine_conservative", "venue_verified"] } },
    { name: "合并-两数值块", report: { engine_conservative: { feeRate: 0.001, slippagePct: 0.015 }, venue_verified: { feeRate: null, slippagePct: null } } },
    { name: "venue_verified 非 null 无 VERIFIED 证据", report: { cost_model_id: "venue_verified", feeRate: 0.0007, slippagePct: 0.008, provenance: "UNVERIFIED" } },
    { name: "回填 conservative 数值（即便声称 VERIFIED）", report: { cost_model_id: "venue_verified", feeRate: 0.001, slippagePct: 0.015, provenance: "VERIFIED" } },
    { name: "配置容器 venue_verified 被回填", report: { costModels: { engine_conservative: M.ENGINE_CONSERVATIVE, venue_verified: { feeRate: 0.001, slippagePct: 0.015 } } } },
  ];
  for (const c of bad) {
    assert.throws(() => M.assertNoMerge(c.report), /COST_MODEL_MERGE_REJECTED/, c.name);
  }
  // 合法：单口径结果记录 + 合法的 venue_verified 全 null + 双口径并列配置
  assert.equal(M.assertNoMerge({ cost_model_id: "engine_conservative", feeRate: 0.001, slippagePct: 0.015 }), true);
  assert.equal(M.assertNoMerge({ cost_model_id: "venue_verified", feeRate: null, slippagePct: null, provenance: "UNVERIFIED", status: "NOT_MEASURED" }), true);
  assert.equal(M.assertNoMerge({ records: [{ cost_model_id: "engine_conservative" }, { cost_model_id: "engine_conservative" }] }), true);
  assert.equal(M.assertNoMerge({ costModels: { engine_conservative: M.ENGINE_CONSERVATIVE, venue_verified: M.VENUE_VERIFIED } }), true);
  // 双口径定义本身
  assert.equal(M.ENGINE_CONSERVATIVE.slippagePct_is_planning_estimate, true);
  assert.equal(M.VENUE_VERIFIED.feeRate, null);
  assert.equal(M.VENUE_VERIFIED.status, "NOT_MEASURED");
});

// ③ timeline 确定性
check("③ timeline 同 seed 两次 timeline_sha256 完全一致，不同 seed 不同", () => {
  const a = M.generateTimeline({ seed: 4242, bars: 120 });
  const b = M.generateTimeline({ seed: 4242, bars: 120 });
  const c = M.generateTimeline({ seed: 4243, bars: 120 });
  assert.equal(a.timeline_sha256, b.timeline_sha256);
  assert.notEqual(a.timeline_sha256, c.timeline_sha256);
  assert.equal(a.timeline_sha256.length, 64);
  for (const bar of a.series) {
    assert.equal(bar.data_mode, "FIXTURE");
    assert.equal(bar.evidence_level, "SYNTHETIC_FIXTURE");
    assert.equal(bar.isReal, false);
    assert.equal(bar.venue, "fixture");
    assert.equal(bar.no_synthetic_data, false);
    assert.equal(bar.seed, 4242);
    assert.equal(bar.timeline_sha256, a.timeline_sha256);
    assert.ok(bar.sample_foundation_note && /sample foundation/i.test(bar.sample_foundation_note));
  }
  assert.equal(a.isReal, false);
  assert.equal(a.venue, "fixture");
  assert.equal(a.no_synthetic_data, false);
  assert.ok(/BACKFILL/.test(a.sample_foundation_note));
  assert.equal(a.sample_foundation.bars_backfill, 0);
});

// ④ 四臂 timeline hash 与 cost_model_id 一致
check("④ 四臂共享同一 timeline_sha256 与同一 cost_model_id，每条记录单一口径 id", () => {
  const t = M.generateTimeline({ seed: 777, bars: 240 });
  const r = M.runFourArms(t);
  assert.equal(r.arms.length, 4);
  for (const a of r.arms) {
    assert.equal(a.timeline_sha256, t.timeline_sha256);
    assert.equal(a.cost_model_id, r.cost_model_id);
    assert.equal(typeof a.cost_model_id, "string");
    assert.ok(!Array.isArray(a.cost_model_id));
    for (const rec of [...a.equity, ...a.fills]) {
      assert.equal(rec.cost_model_id, r.cost_model_id);
      assert.equal(typeof rec.cost_model_id, "string");
      assert.ok(!("cost_model_ids" in rec));
      assert.equal(rec.timeline_sha256, t.timeline_sha256);
      assert.equal(rec.isReal, false);
    }
  }
});

// ⑤ D 臂 NO_DATA 且未产生任何合成 decision
check("⑤ D 臂 NO_DATA 且未产生任何合成 decision（仓库无真实 AI 决策日志）", () => {
  const t = M.generateTimeline({ seed: 1, bars: 60 });
  const d = M.runAiDecisions(t, { cost_model_id: M.DEFAULT_COST_MODEL_ID });
  assert.equal(d.status, "NO_DATA");
  assert.equal(d.reason, "NO_REAL_AI_DECISION_LOG");
  assert.equal(d.fills.length, 0);
  assert.equal(d.equity.length, 0);
  assert.equal(d.decisions_replayed, 0);
  assert.equal(d.synthetic_decisions_generated, 0);
  // 只读扫描仓库现存 decisions.jsonl，确认没有任何真实 AI 决策日志
  const artDir = join(REPO_ROOT, "reports", "day14-artifacts");
  let realLogs = 0, scanned = 0;
  if (existsSync(artDir)) {
    const walk = (dir) => {
      for (const e of readdirSync(dir)) {
        const p = join(dir, e);
        let st; try { st = statSync(p); } catch { continue; }
        if (st.isDirectory()) walk(p);
        else if (e === "decisions.jsonl") {
          scanned++;
          if (/"isReal"\s*:\s*true/.test(readFileSync(p, "utf8"))) realLogs++;
        }
      }
    };
    walk(artDir);
  }
  assert.equal(realLogs, 0, "仓库现存 decisions.jsonl 不得含真实 AI 决策标记");
  // 即便强行传入 fixture/mock 决策，也不得被合成回放凑数
  const forced = M.runAiDecisions(t, {
    cost_model_id: M.DEFAULT_COST_MODEL_ID,
    decisions: [{ isReal: false, action: "BUY", data_mode: "mock", quantity: 0.01 }],
  });
  assert.equal(forced.status, "NO_DATA");
  assert.equal(forced.synthetic_decisions_generated, 0);
  assert.equal(forced.decisions_replayed, 0);
  console.log("       (扫描 decisions.jsonl 文件数=" + scanned + "，含真实标记=" + realLogs + ")");
});

// ⑥ 整体 INSUFFICIENT_DATA、无排名输出
check("⑥ 任一臂 NO_DATA -> 整体 INSUFFICIENT_DATA，且无排名/胜出/alpha 输出", () => {
  const t = M.generateTimeline({ seed: 2, bars: 60 });
  const r = M.runFourArms(t);
  assert.equal(r.status, "INSUFFICIENT_DATA");
  assert.ok(!("ranking" in r), "不得含 ranking 键");
  assert.ok(!("winner" in r), "不得含 winner 键");
  assert.ok(!("alpha" in r), "不得含 alpha 键");
  const txt = JSON.stringify(r);
  assert.ok(!/"winner"\s*:/.test(txt));
  assert.ok(!/"alpha"\s*:/.test(txt));
  assert.equal(r.isReal, false);
  assert.equal(r.arms.find((a) => a.arm_id === M.D_ID).status, "NO_DATA");
});

// ⑦ walk-forward 3 折、每折 trainEnd<testStart、不计入真实 OOS 门
check("⑦ walk-forward 3 折、每折 trainEnd<testStart、oos_pass=0/verified=0/不计入真实 OOS 门", () => {
  const t = M.generateTimeline({ seed: 3, bars: 240 });
  const w = M.buildWalkForward(t);
  assert.equal(w.REQUIRED_FOLDS, 3);
  assert.equal(M.REQUIRED_FOLDS, 3);
  assert.ok(/verification\.mjs:19/.test(M.FOLDS_SOURCE));
  assert.equal(w.fold_count, 3);
  assert.equal(w.folds.length, 3);
  assert.equal(w.label, "SYNTHETIC_OOS_DRILL");
  assert.equal(w.oos_pass, 0);
  assert.equal(w.verified, 0);
  assert.equal(w.counts_toward_real_oos_gate, false);
  assert.equal(w.isReal, false);
  for (const f of w.folds) {
    assert.ok(f.trainEnd < f.testStart, `fold ${f.fold} 必须 trainEnd<testStart`);
    assert.equal(f.trainStart, 0, "锚定窗口起点恒为 0");
    assert.equal(f.anchored, true);
    assert.equal(f.expanding_window, true);
    assert.equal(f.isReal, false);
    assert.equal(f.timeline_sha256, t.timeline_sha256);
  }
  for (let i = 1; i < w.folds.length; i++) {
    assert.ok(w.folds[i].trainEnd > w.folds[i - 1].trainEnd, "扩展窗口逐折增长");
    assert.ok(w.folds[i].testStart > w.folds[i - 1].testStart, "测试窗口前移，不回看");
  }
});

// ⑧ 真实 Sharpe ≠ 代理
check("⑧ 真实 Sharpe（收益率序列样本标准差）≠ 代理 Sharpe（sqrt(24) 总体标准差）", () => {
  const returns = [0.01, -0.004, 0.012, 0.003, -0.002, 0.008, 0.015, -0.006, 0.004, 0.009];
  const real = M.sharpeReal(returns);
  const proxy = M.sharpeProxy(returns);
  assert.notEqual(real, null);
  assert.notEqual(proxy, null);
  assert.notEqual(real, proxy, "真实 Sharpe 不得等于代理 Sharpe");
  assert.ok(Number.isFinite(real));
  // 样本标准差(n-1) 与 总体标准差(n) 本身也不同
  assert.notEqual(M.sampleStdDev(returns), M.populationStdDev(returns));
  assert.ok(M.sampleStdDev(returns) > M.populationStdDev(returns));
  const m = M.computeMetrics({ returns, trades: [{ pnl: 2 }, { pnl: -1 }, { pnl: 3 }] });
  assert.equal(m.measurement.sharpe, "MEASURED");
  assert.equal(m.sharpe, real);
});

// ⑨ 缺失指标为 null + NOT_MEASURED 而非 0
check("⑨ 缺失指标一律 null + NOT_MEASURED，绝不返回 0", () => {
  const m = M.computeMetrics({});
  for (const k of M.METRIC_FIELDS) {
    assert.equal(m[k], null, `${k} 缺失应为 null`);
    assert.equal(m.measurement[k], "NOT_MEASURED");
    assert.notEqual(m[k], 0);
  }
  assert.equal(M.winRate([]), null);
  assert.equal(M.profitFactor([]), null);
  assert.equal(M.sharpeReal([]), null);
  assert.equal(M.netReturn([]), null);
  assert.equal(M.maxDrawdown([]), null);
  // 无亏损交易时 profit factor 不可表达为有限值 -> null，不返回 0/Infinity
  assert.equal(M.profitFactor([{ pnl: 1 }, { pnl: 2 }]), null);
});

// ⑩ 全模块无 isReal:true / LIVE / 硬编码 equity:10000 / 写死 reconcile:MATCH
check("⑩ 所有新建模块源码无禁用字面量（真实标记/LIVE/硬编码净值/写死对账）", () => {
  const src = collectModuleSources();
  const forbidden = [
    [/isReal\s*:\s*true/, "真实标记字面量"],
    [/\bLIVE\b/, "LIVE 标签"],
    [/equity\s*[:=]\s*10000\b/, "硬编码净值"],
    [/reconcile\s*:\s*["']MATCH["']/, "写死对账 MATCH"],
  ];
  for (const { p, text } of src) {
    for (const [re, label] of forbidden) {
      assert.ok(!re.test(text), `${p} 含禁用字面量 ${label}`);
    }
  }
  assert.ok(src.length >= 8, "应覆盖所有新建模块（实际 " + src.length + " 个）");
  // 快照对账字段由 reconcileEquity 计算得出，源码里 MATCH 只是结果标签之一
  const t = M.generateTimeline({ seed: 9, bars: 30 });
  const eq = M.runHoldCash(t, { cost_model_id: M.DEFAULT_COST_MODEL_ID }).equity[0];
  assert.equal(eq.reconcile, "MATCH");
  assert.equal(eq.basis.delta, 0);
});

// ⑪ 零网络原语
check("⑪ 全模块零网络原语（fetch( / node:http(s) / 真实 URL / CommonJS require）", () => {
  const src = collectModuleSources();
  for (const { p, text } of src) {
    assert.ok(!/fetch\s*\(/.test(text), `${p} 含 fetch(`);
    assert.ok(!/["']node:https?["']/.test(text), `${p} 含 node:http(s)`);
    assert.ok(!/https?:\/\//.test(text), `${p} 含真实 URL`);
    assert.ok(!/require\s*\(/.test(text), `${p} 含 CommonJS require`);
    // 精确检查：不得从被冻结模块 import（注释里注明出处/说明不算 import）
    assert.ok(!/from\s*["'][^"']*(day14-runner\.mjs|day14-verify\.mjs|verification\.mjs|trading_api\.mjs)["']/.test(text),
      `${p} 含被冻结模块 import`);
    assert.ok(!/import\s*\{[^}]*fetchMarketContext/.test(text), `${p} 含被禁 handler import`);
  }
});

// ⑫ 复用 day14-verify.mjs T5a/T8 语义：MATCH 计算得出 + engine equity 计算得出
check("⑫ 复用 T5a/T8 语义：对账 MATCH 由 delta 计算得出；engine 净值由冻结引擎 summarize 计算（非硬编码）", () => {
  // 纯函数：相等 -> MATCH，不等 -> MISMATCH（计算得出，非写死）
  const ok = M.reconcileEquity(16600, 16600);
  assert.equal(ok.delta, 0);
  assert.equal(ok.reconcile, "MATCH");
  const bad = M.reconcileEquity(16600, 16000);
  assert.equal(bad.delta, 600);
  assert.equal(bad.reconcile, "MISMATCH");
  // T8-engine 语义：非默认账户净值由冻结引擎 summarize 计算（9500 + 0.1*71000 = 16600）
  const acct = engine.createAccount();
  acct.cash = "9500.00000000";
  acct.positions = { BTCUSDT: { qty: "0.10000000", avgPrice: "70000.00000000", reservedQty: "0" } };
  const derived = engine.summarize(acct, { BTCUSDT: "71000.00000000" });
  assert.equal(derived.equity, 16600);
  assert.notEqual(derived.equity, 10000);
  assert.equal(derived.marketValue, 7100);
  // T5a 形态：engine vs harness + delta=0 + noFills=true -> 计算得出 MATCH
  const r1 = M.reconcileEquity(derived.equity, 16600);
  assert.equal(r1.delta, 0);
  assert.equal(r1.reconcile, "MATCH");
  // 篡改 harness 后必须变 MISMATCH（证明不是写死 MATCH）
  const r2 = M.reconcileEquity(derived.equity, 16000);
  assert.equal(r2.reconcile, "MISMATCH");
  // T8 形态：有成交（fee>0, noFills=false）仍以 delta 定 MATCH
  const fee = M.computeFee(0.1 * 71000);
  assert.ok(Number(fee) > 0);
  const t = M.generateTimeline({ seed: 12, bars: 30 });
  const bEq = M.runBuyAndHold(t, { cost_model_id: M.DEFAULT_COST_MODEL_ID }).equity[0];
  assert.equal(bEq.noFills, false);
  assert.ok(bEq.basis.engine.trades > 0);
  assert.equal(bEq.basis.delta, 0);
  assert.equal(bEq.reconcile, "MATCH", "有成交时仍由 delta=0 计算得出 MATCH");
});

console.log("\n==== P3/P4 CONTRACTS SUMMARY ====");
console.log(JSON.stringify({ total: results.length, passed: PASS, failed: FAIL, results }, null, 2));
console.log("PASS=" + PASS + " FAIL=" + FAIL + " TOTAL=" + results.length);
process.exitCode = FAIL > 0 ? 1 : 0;
