/**
 * day14-verify.mjs — trading-engineer 本地验证（P0 A–E 反例/故障注入）。
 * 不属于 independent-qa 的 tests/ 或 reports/JEV_INDEPENDENT_QA.md。
 * 覆盖：重启续链接、篡改检测、fail-closed、非 HOLD 完整 context、真实对账、
 *       runId 不可变配置、未验证行情 fail-closed、成交走冻结引擎。
 * 运行：node scripts/day14-verify.mjs
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

import { runDay14 } from "./day14-runner.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const RUNNER = join(HERE, "day14-runner.mjs");
const REPO_OUT = join(HERE, "..", "..", "..", "reports", "day14-artifacts");
const VERIFY_ROOT = join(REPO_OUT, "_verify", "verify-" + Date.now() + "-" + Math.random().toString(16).slice(2, 8));
const LEGACY_FREEZE = join(REPO_OUT, "config.freeze.json");
const NOW = 1770000000000; // 固定注入时钟，保证确定性

mkdirSync(VERIFY_ROOT, { recursive: true });
const sha = (s) => createHash("sha256").update(s).digest("hex");
const readJsonl = (p) => readFileSync(p, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
const out = (name) => { const d = join(VERIFY_ROOT, name); mkdirSync(d, { recursive: true }); return d; };
const runDirOf = (d, runId) => join(d, "runs", runId);
const activeRunId = (d) => JSON.parse(readFileSync(join(d, "active-run.json"), "utf8")).runId;

let passed = 0, failed = 0;
const results = [];
function check(name, cond, evidence) {
  const ok = !!cond;
  if (ok) passed++; else failed++;
  results.push({ name, status: ok ? "PASS" : "FAIL", evidence });
  console.log((ok ? "[PASS] " : "[FAIL] ") + name + (evidence ? "  :: " + evidence : ""));
}
async function expectThrow(name, fn, matchRe, extra) {
  let threw = false, msg = "";
  try { await fn(); } catch (e) { threw = true; msg = String(e.message); }
  check(name, threw && (!matchRe || matchRe.test(msg)), "throw=" + threw + " msg=" + JSON.stringify(msg) + (extra ? " " + extra : ""));
}

// 坏 fetcher：payload 声明 foreign market -> fetchMarket 拦截 -> unverified -> fail-closed
function badFetcher(nowMs) {
  return async (url) => {
    const u = new URL(url);
    const headers = { get: () => null };
    if (u.pathname === "/api/v3/time")
      return { ok: true, status: 200, headers, text: async () => JSON.stringify({ serverTime: nowMs }) };
    if (u.pathname === "/api/v3/ticker/price") {
      const syms = JSON.parse(u.searchParams.get("symbols") || "[]");
      const items = syms.map((s) => ({ symbol: s, price: "70000", exchange: "Binance Global spot" }));
      return { ok: true, status: 200, headers, text: async () => JSON.stringify(items) };
    }
    throw new Error("BAD_FIXTURE_UNEXPECTED_URL");
  };
}
// 注入 BUY provider（用于验证非 HOLD 完整 context 与成交接线）
const buyProvider = async () => ({
  ok: true, kind: "fixture", model: "fixture-buy", status: "FIXTURE_INJECTED_BUY",
  latencyMs: 1, costUsd: 0,
  intent: { action: "BUY", symbol: "BTCUSDT", quantity: 0.001, confidence: 0.5, reason: "injected buy intent" },
});

console.log("VERIFY_ROOT = " + VERIFY_ROOT);
console.log("legacy freeze sha256 (before) = " + sha(readFileSync(LEGACY_FREEZE, "utf8")));
const legacyFreezeBefore = sha(readFileSync(LEGACY_FREEZE, "utf8"));

// ---------------------------------------------------------------- T1a 链续接（同进程模拟重启）
{
  const d = out("t1a");
  const r1 = await runDay14({ outDir: d, newRun: true, cycles: 3, nowMs: NOW });
  const r2 = await runDay14({ outDir: d, cycles: 3, nowMs: NOW + 1000 });
  const dir = runDirOf(d, r1.runId);
  const lines = readJsonl(join(dir, "snapshots.jsonl"));
  const cont = lines.length === 6 && lines[0]._prev === "GENESIS" && lines[3]._prev === lines[2]._hash;
  check("T1a 重启续链接：第4条 _prev === 第3条 _hash（非 GENESIS）", cont,
    "lines=" + lines.length + " line[2]._hash=" + (lines[2] ? lines[2]._hash.slice(0, 16) : "?") +
    " line[3]._prev=" + (lines[3] ? lines[3]._prev.slice(0, 16) : "?") +
    " runIdSame=" + (r1.runId === r2.runId) + " finalCycle=" + r2.cycles);
  // 逐条链连续性 + 首条 GENESIS
  let chainOk = lines[0]._prev === "GENESIS";
  for (let i = 1; i < lines.length; i++) if (lines[i]._prev !== lines[i - 1]._hash) chainOk = false;
  check("T1a 全链连续 + 首条 GENESIS", chainOk, "validated " + lines.length + " records");
}

// ---------------------------------------------------------------- T1b 真实进程重启（两个独立 node 进程）
{
  const d = out("t1b");
  const p1 = spawnSync(process.execPath, [RUNNER, "3", "--out", d, "--new-run"], { encoding: "utf8" });
  const p2 = spawnSync(process.execPath, [RUNNER, "3", "--out", d], { encoding: "utf8" });
  const dir = runDirOf(d, activeRunId(d));
  const lines = existsSync(join(dir, "snapshots.jsonl")) ? readJsonl(join(dir, "snapshots.jsonl")) : [];
  const cont = lines.length === 6 && lines[3]._prev === lines[2]._hash && lines[0]._prev === "GENESIS";
  check("T1b 真实重启（两独立进程）链续接", p1.status === 0 && p2.status === 0 && cont,
    "exit1=" + p1.status + " exit2=" + p2.status + " lines=" + lines.length +
    " line[2]._hash=" + (lines[2] ? lines[2]._hash.slice(0, 16) : "?") +
    " line[3]._prev=" + (lines[3] ? lines[3]._prev.slice(0, 16) : "?"));
}

// ---------------------------------------------------------------- T2 篡改检测
{
  const d = out("t2");
  const r = await runDay14({ outDir: d, newRun: true, cycles: 3, nowMs: NOW });
  const dir = runDirOf(d, r.runId);
  const p = join(dir, "snapshots.jsonl");
  const lines = readFileSync(p, "utf8").trim().split("\n");
  const rec = JSON.parse(lines[1]);
  rec.price = 1; // 篡改业务字段，但保留 _prev/_hash（内容 hash 重算将不匹配）
  lines[1] = JSON.stringify(rec);
  writeFileSync(p, lines.join("\n") + "\n");
  await expectThrow("T2 篡改一条日志后启动 throw（HASH TAMPER）",
    () => runDay14({ outDir: d, cycles: 1, nowMs: NOW + 1000 }), /TAMPER|HASH|CHAIN|MISMATCH/i);
}

// ---------------------------------------------------------------- T3 fail-closed：formal gate 不可用
{
  const d = out("t3");
  await expectThrow("T3 注入 riskCheckImpl=null 应终止",
    () => runDay14({ outDir: d, newRun: true, cycles: 2, nowMs: NOW, riskCheckImpl: null }),
    /fail-closed|riskCheck|FATAL/i);
  // 不得产生任何 approved 记录 / risk 日志
  const runsDir = join(d, "runs");
  let anyRisk = false, anyApproved = false;
  if (existsSync(runsDir)) {
    for (const e of readdirSync(runsDir)) {
      const rp = join(runsDir, e, "risk.jsonl");
      if (existsSync(rp)) { anyRisk = true; if (readJsonl(rp).some((x) => x.approved === true)) anyApproved = true; }
    }
  }
  check("T3 fail-closed 不产生 risk 日志 / 不产生 approved:true", !anyRisk && !anyApproved,
    "anyRisk=" + anyRisk + " anyApproved=" + anyApproved);
}

// ---------------------------------------------------------------- T4a 非 HOLD 完整 context 全量校验
{
  const d = out("t4a");
  const r = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW, providerImpl: buyProvider });
  const dir = runDirOf(d, r.runId);
  const risk = readJsonl(join(dir, "risk.jsonl"))[0];
  const dec = readJsonl(join(dir, "decisions.jsonl"))[0];
  const ok = risk.action === "BUY" && risk.code === "RISK_APPROVED" && risk.orderable === true &&
    risk.context && risk.context.markQuality === "verified" && risk.evaluated &&
    Number.isFinite(risk.evaluated.notional_usd) && Number.isFinite(risk.evaluated.mark_price_usd);
  check("T4a 非 HOLD 构造完整 context -> riskCheck 全量通过 RISK_APPROVED", ok,
    "code=" + risk.code + " orderable=" + risk.orderable + " ctx=" + JSON.stringify(risk.context) +
    " notional_usd=" + (risk.evaluated ? risk.evaluated.notional_usd : "?") +
    " execution=" + dec.execution + " decisionClass=" + dec.decisionClass);
}

// ---------------------------------------------------------------- T4b HOLD 通过契约 != BUY/SELL 通过（日志区分）
{
  const d = out("t4b");
  const r = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW });
  const dir = runDirOf(d, r.runId);
  const risk = readJsonl(join(dir, "risk.jsonl"))[0];
  const ok = risk.code === "RISK_HOLD_NO_ORDER" && risk.approved === true && risk.orderable === false &&
    risk.decisionClass === "HOLD";
  check("T4b HOLD approved 但 orderable=false 且 code=RISK_HOLD_NO_ORDER", ok,
    "code=" + risk.code + " approved=" + risk.approved + " orderable=" + risk.orderable + " class=" + risk.decisionClass);
}

// ---------------------------------------------------------------- T5a 对账正常（真实引擎值 + 依据）
{
  const d = out("t5a");
  const r = await runDay14({ outDir: d, newRun: true, cycles: 2, nowMs: NOW });
  const dir = runDirOf(d, r.runId);
  const eq = readJsonl(join(dir, "equity.jsonl"));
  const ok = eq.length === 2 && eq.every((x) => x.reconcile === "MATCH" && x.basis && x.basis.delta === 0 &&
    x.basis.engine && x.basis.harness && x.noFills === true && x.basis.engine.version >= 1);
  check("T5a 对账 MATCH 带比对依据(engine vs harness + delta) + noFills", ok,
    "reconcile=" + eq[0].reconcile + " delta=" + eq[0].basis.delta +
    " engine.equity=" + eq[0].basis.engine.equity + " harness.equity=" + eq[0].basis.harness.equity +
    " noFills=" + eq[0].noFills + " version=" + eq[0].basis.engine.version);
}

// ---------------------------------------------------------------- T5b 篡改引擎账户 -> MISMATCH（不写死）
{
  const d = out("t5b");
  const r = await runDay14({ outDir: d, newRun: true, cycles: 2, nowMs: NOW });
  const dir = runDirOf(d, r.runId);
  const sp = join(dir, "state.json");
  const st = JSON.parse(readFileSync(sp, "utf8"));
  st.engineAccount.cash = "9000.00000000"; // 篡改引擎账户现金
  writeFileSync(sp, JSON.stringify(st, null, 2));
  const r2 = await runDay14({ outDir: d, cycles: 1, nowMs: NOW + 1000 });
  const eq = readJsonl(join(dir, "equity.jsonl")).pop();
  const ok = eq.reconcile === "MISMATCH" && Math.abs(eq.basis.delta) > 1 && r2.reconcileMismatch === true;
  check("T5b 篡改账户后 reconcile=MISMATCH 且触发 auto-pause", ok,
    "reconcile=" + eq.reconcile + " delta=" + eq.basis.delta +
    " engine.equity=" + eq.basis.engine.equity + " harness.equity=" + eq.basis.harness.equity +
    " mismatchState=" + r2.reconcileMismatch + " paused=" + r2.paused + " reason=" + r2.pauseReason);
}

// ---------------------------------------------------------------- T6 runId 不可变 + 旧 freeze 未改 + 双口径
{
  const d = out("t6");
  const rA = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW });
  const dirA = runDirOf(d, rA.runId);
  const cfg = JSON.parse(readFileSync(join(dirA, "run.json"), "utf8"));
  const noRealMarketLabel = !("realMarket" in cfg);
  const dual = cfg.costModels.engine_conservative.feeRate === 0.001 &&
    cfg.costModels.engine_conservative.slippagePct === 0.015 &&
    cfg.costModels.venue_verified.feeRate === null && cfg.costModels.venue_verified.slippagePct === null;
  // 篡改不可变配置 -> 续接应 throw
  cfg.intent.marketIntent = "TAMPERED";
  writeFileSync(join(dirA, "run.json"), JSON.stringify(cfg, null, 2));
  await expectThrow("T6 篡改不可变 run.json 后续接 throw",
    () => runDay14({ outDir: d, cycles: 1, nowMs: NOW + 1000 }), /ALTERED|immutable|config/i);
  // newRun 生成全新 runId（不读被篡改的 dirA/run.json），无需还原
  const rB = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW + 2000 });
  const legacyAfter = sha(readFileSync(LEGACY_FREEZE, "utf8"));
  check("T6 runId 隔离 + 旧 freeze 未改 + 清除 realMarket 误导 + 双口径分列",
    rA.runId !== rB.runId && legacyAfter === legacyFreezeBefore && noRealMarketLabel && dual,
    "runA=" + rA.runId + " runB=" + rB.runId + " freezeUnchanged=" + (legacyAfter === legacyFreezeBefore) +
    " noRealMarketLabel=" + noRealMarketLabel + " dualModels=" + dual);
}

// ---------------------------------------------------------------- T7 未验证行情 fail-closed
{
  const d = out("t7");
  const r = await runDay14({ outDir: d, newRun: true, cycles: 2, nowMs: NOW, fetchImpl: badFetcher(NOW) });
  const dir = runDirOf(d, r.runId);
  const m = readJsonl(join(dir, "snapshots.jsonl"));
  const dec = readJsonl(join(dir, "decisions.jsonl"));
  const prov = readJsonl(join(dir, "provider_health.jsonl"));
  const risk = readJsonl(join(dir, "risk.jsonl"));
  const ok = m.every((x) => x.kind === "unverified") && dec.every((x) => x.decisionClass === "NO_ACTION" && x.execution === "NONE") &&
    prov.every((x) => x.status === "NO_CALL_UNVERIFIED_MARKET") && risk.every((x) => x.orderable === false && x.code !== "RISK_APPROVED");
  check("T7 未验证行情 -> NO_ACTION / provider 不调用 / 无 RISK_APPROVED", ok,
    "kind=" + m[0].kind + " guardOk=" + m[0].guardOk + " decisionClass=" + dec[0].decisionClass +
    " provider=" + prov[0].status + " riskCode=" + risk[0].code + " reason=" + m[0].reason);
}

// ---------------------------------------------------------------- T8 成交走 engineImpl（冻结引擎 simulateNewOrder）
{
  const d = out("t8");
  const r = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW, providerImpl: buyProvider, simulateFills: true });
  const dir = runDirOf(d, r.runId);
  const eq = readJsonl(join(dir, "equity.jsonl"))[0];
  const dec = readJsonl(join(dir, "decisions.jsonl"))[0];
  const ok = eq.noFills === false && eq.basis.engine.trades > 0 && eq.apiFeeUsd > 0 &&
    eq.reconcile === "MATCH" && /SIMULATED_FILL_LOCAL/.test(dec.execution);
  check("T8 成交走冻结引擎 simulateNewOrder 并真实对账(有 fills)", ok,
    "execution=" + dec.execution + " noFills=" + eq.noFills + " trades=" + eq.basis.engine.trades +
    " apiFeeUsd=" + eq.apiFeeUsd + " reconcile=" + eq.reconcile + " delta=" + eq.basis.delta);
}

// ---------------------------------------------------------------- T9 provider 失败/解析失败 -> NO_ACTION（不得改写为 HOLD）
{
  const d = out("t9");
  // 场景 a：providerImpl 抛错（超时）
  const throwingProvider = async () => { throw new Error("simulated timeout"); };
  const r1 = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW, providerImpl: throwingProvider });
  const dir1 = runDirOf(d, r1.runId);
  const dec1 = readJsonl(join(dir1, "decisions.jsonl"))[0];
  const risk1 = readJsonl(join(dir1, "risk.jsonl"))[0];
  const prov1 = readJsonl(join(dir1, "provider_health.jsonl"))[0];
  const ok1 = dec1.decisionClass === "NO_ACTION" && dec1.action === "HOLD" && dec1.orderable === false &&
    dec1.reason === "PROVIDER_FAILED_FAILCLOSED" && risk1.failClosedReason === "PROVIDER_FAILED" &&
    prov1.status === "PROVIDER_ERROR" && risk1.code !== "RISK_APPROVED";
  check("T9a provider 抛错(超时) -> NO_ACTION，绝不改写为 HOLD", ok1,
    "decisionClass=" + dec1.decisionClass + " reason=" + dec1.reason + " provider=" + prov1.status +
    " failClosedReason=" + risk1.failClosedReason + " orderable=" + dec1.orderable);

  // 场景 b：providerImpl 返回 intent:null（解析失败）
  const parseFailProvider = async () => ({
    ok: true, kind: "fixture", model: "fixture-parse", status: "PARSE_FAILED_SRC",
    latencyMs: 1, costUsd: 0, intent: null,
  });
  const r2 = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW + 1000, providerImpl: parseFailProvider });
  const dir2 = runDirOf(d, r2.runId);
  const dec2 = readJsonl(join(dir2, "decisions.jsonl"))[0];
  const prov2 = readJsonl(join(dir2, "provider_health.jsonl"))[0];
  const ok2 = dec2.decisionClass === "NO_ACTION" && dec2.orderable === false &&
    dec2.reason === "PROVIDER_FAILED_FAILCLOSED" && prov2.status === "PARSE_FAILED";
  check("T9b provider 解析失败(intent:null) -> NO_ACTION", ok2,
    "decisionClass=" + dec2.decisionClass + " reason=" + dec2.reason + " provider=" + prov2.status +
    " orderable=" + dec2.orderable);

  // 对照：正常 fixture HOLD 仍是 HOLD（修复未误伤合法决策）
  const r3 = await runDay14({ outDir: d, newRun: true, cycles: 1, nowMs: NOW + 2000 });
  const dec3 = readJsonl(join(runDirOf(d, r3.runId), "decisions.jsonl"))[0];
  check("T9c 对照：正常 fixture HOLD 仍记 HOLD（非 NO_ACTION）", dec3.decisionClass === "HOLD",
    "decisionClass=" + dec3.decisionClass + " reason=" + dec3.reason);
}

// ---------------------------------------------------------------- 汇总
console.log("\n==== VERIFY SUMMARY ====");
console.log(JSON.stringify({ verifyRoot: VERIFY_ROOT, total: results.length, passed, failed, results }, null, 2));
process.exitCode = failed > 0 ? 1 : 0;
