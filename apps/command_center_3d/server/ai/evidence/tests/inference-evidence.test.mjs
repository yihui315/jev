// JEV V1.6 / P2 — inference evidence 层独立测试。
//
// 运行方式：node <本文件路径>；exit code 0 才算通过。
// 约定：只用 node:assert/strict 手写断言 + Node 标准库；纯内存 fixture，禁网。
// 验收边界：本轮为本地 manual gate；CI 覆盖 UNVERIFIED（无本地 Actions runner），
// 不宣称任何远程运行编号或自动化门禁通过。
//
// 禁网策略（全部在测试内原生实现，不复用任何外部独占库、不引用其计数符号）：
//   1) 在任何被测模块被加载前，把宿主进程的全局网络出口替换为「记录并 throw」守卫（安装 1 处）；
//   2) 被测模块与聚合导出都用动态 import，在守卫之后加载，以验证「模块加载不发网络」；
//   3) 静态扫描本层全部源码，确认不存在主动网络调用 / http 模块引用 / 真实地址；
//   4) 全程结束断言守卫被调用次数为 0（未 spawn 子进程，无子进程 driver）。

import assert from "node:assert/strict";
import { readFileSync, writeFileSync, unlinkSync, existsSync } from "node:fs";

// —— 网络出口守卫（先于一切被测模块加载）——
let networkCallCount = 0;
globalThis.fetch = function networkEgressGuard() {
  networkCallCount += 1;
  throw new Error("P2 证据层禁止网络调用");
};

const EV = await import("../inference-evidence.mjs");
const MM = await import("../multi-model-provider.mjs");
const IDX = await import("../index.mjs");

let passed = 0;
let failed = 0;
const failures = [];

async function test(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log("PASS  " + name);
  } catch (err) {
    failed += 1;
    failures.push(name);
    console.error("FAIL  " + name + " :: " + (err && err.message ? err.message : String(err)));
  }
}

const TMP = new URL("./_p2_chain_tmp.jsonl", import.meta.url);
function cleanTmp() {
  if (existsSync(TMP)) unlinkSync(TMP);
}
const MARKET_HASH = EV.FIXTURE_MARKET_SNAPSHOT_HASH;
const NOW = 1700000000000;

function legalProviderResult(overrides = {}) {
  return {
    ok: true,
    parseFailed: false,
    rawResponseBytes: JSON.stringify({ action: "HOLD", reason: "fixture legal decision", seed: "alpha" }),
    parsedDecision: { action: "HOLD", reason: "fixture legal decision", confidence: null },
    tokenUsage: { prompt: 4, completion: 6, total: 10, provenance: "FIXTURE_REPORTED" },
    ...overrides,
  };
}

function legalParams(overrides = {}) {
  return {
    providerResult: legalProviderResult(),
    modelId: "fixture-model-a",
    promptVersion: EV.FIXTURE_PROMPT_VERSION,
    marketSnapshotHash: MARKET_HASH,
    marketContextId: "mctx-fixture-001",
    riskVerdict: "APPROVED",
    nowMs: NOW,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------

await test("① 合法记录 8 类字段齐全且 decisionClass=APPROVED", () => {
  const r = EV.buildInferenceEvidence(legalParams());
  const groups = [
    ["身份", ["schemaVersion", "modelId", "promptVersion", "providerId"]],
    ["市场", ["marketSnapshotHash", "marketContextId", "hashScopes"]],
    ["原始响应", ["rawResponseHash", "rawResponseBase64"]],
    ["决策", ["parsedDecision", "decisionClass"]],
    ["token 用量", ["tokenUsage"]],
    ["推理成本", ["actualInferenceCost"]],
    ["风控", ["riskVerdict", "failClosedReason"]],
    ["溯源", ["capturedAtMs", "isReal", "dataMode", "note"]],
  ];
  for (const [label, keys] of groups) {
    for (const k of keys) {
      assert.ok(Object.prototype.hasOwnProperty.call(r, k), label + " 类缺字段 " + k);
    }
  }
  assert.equal(Object.keys(r).length, 19, "顶层字段应恰为 19 个");
  assert.equal(typeof r.rawResponseBase64, "string", "rawResponseBase64 必须是 base64 字符串，不是数字");
  assert.equal(Buffer.from(r.rawResponseBase64, "base64").length > 0, true);
  assert.equal(r.schemaVersion, "jev.inference.evidence/v1");
  assert.equal(r.decisionClass, "APPROVED");
  assert.equal(r.failClosedReason, null);
  assert.equal(r.isReal, false);
  assert.equal(r.dataMode, "fixture");
  assert.equal(r.providerId, "mock");
  assert.equal(r.marketContextId, "mctx-fixture-001");
  assert.equal(r.capturedAtMs, NOW);
  assert.equal(r.note, EV.HONEST_EMPTY_NOTE);
  assert.equal(EV.verifyRawResponseHash(r), true, "留存的原始字节应可独立重算核验");
});

await test("② 原始响应改动一个字节 → rawResponseHash 改变（且非对解析后对象）", () => {
  const a = EV.buildInferenceEvidence(
    legalParams({ providerResult: legalProviderResult({ rawResponseBytes: "RAW-BYTE-A" }) }),
  );
  const b = EV.buildInferenceEvidence(
    legalParams({ providerResult: legalProviderResult({ rawResponseBytes: "RAW-BYTE-B" }) }),
  );
  assert.notEqual(a.rawResponseHash, b.rawResponseHash);
  assert.match(a.rawResponseHash, /^[0-9a-f]{64}$/);
  assert.match(b.rawResponseHash, /^[0-9a-f]{64}$/);
  assert.deepEqual(a.parsedDecision, b.parsedDecision, "解析后对象相同，仅原始字节不同");
  assert.equal(a.rawResponseHash, EV.sha256Hex("RAW-BYTE-A"), "hash 必须是对原始字节求得");
  assert.equal(EV.verifyRawResponseHash(a), true);
  assert.equal(EV.verifyRawResponseHash(b), true);
});

await test("③ providerImpl 抛错（模拟超时）→ NO_ACTION / PROVIDER_FAILED，且 != HOLD", async () => {
  const impl = () => {
    throw new Error("simulated timeout");
  };
  const res = await MM.runMultiModel({
    models: ["fixture-model-a"],
    providerImpl: impl,
    timeoutMs: 1000,
    nowMs: NOW,
  });
  assert.equal(res.decisionClass, "NO_ACTION");
  assert.notEqual(res.decisionClass, "HOLD");
  assert.equal(res.failClosedReason, "PROVIDER_FAILED");
  assert.equal(res.execution, "NONE");
  assert.equal(res.orderable, false);
  assert.equal(res.evidence.length, 1);
  assert.equal(res.evidence[0].decisionClass, "NO_ACTION");
  assert.notEqual(res.evidence[0].decisionClass, "HOLD");
  assert.equal(res.evidence[0].failClosedReason, "PROVIDER_FAILED");
  assert.equal(EV.verifyRawResponseHash(res.evidence[0]), true, "失败事件字节仍须留存可核验");
});

await test("④ 解析失败 → 同样 NO_ACTION / PROVIDER_FAILED，且 != HOLD", async () => {
  const impl = () => ({
    ok: true,
    parseFailed: true,
    rawResponseBytes: "NOT_JSON_RESPONSE",
    parsedDecision: null,
    tokenUsage: { prompt: null, completion: null, total: null, provenance: "NOT_MEASURED" },
  });
  const res = await MM.runMultiModel({
    models: ["fixture-model-a"],
    providerImpl: impl,
    timeoutMs: 1000,
    nowMs: NOW,
  });
  assert.equal(res.decisionClass, "NO_ACTION");
  assert.notEqual(res.decisionClass, "HOLD");
  assert.equal(res.failClosedReason, "PROVIDER_FAILED");
  assert.equal(res.evidence[0].parsedDecision, null);
  assert.equal(EV.verifyRawResponseHash(res.evidence[0]), true);
});

await test("⑤ 多模型部分失败 → 整体 NO_ACTION，但成功模型 evidence 仍在", async () => {
  const impl = (modelId) => {
    if (modelId === "fixture-model-bad") throw new Error("boom");
    return legalProviderResult();
  };
  const res = await MM.runMultiModel({
    models: [
      { modelId: "fixture-model-good", riskVerdict: "APPROVED" },
      { modelId: "fixture-model-bad", riskVerdict: "APPROVED" },
    ],
    providerImpl: impl,
    timeoutMs: 1000,
    nowMs: NOW,
  });
  assert.equal(res.decisionClass, "NO_ACTION");
  assert.equal(res.failClosedReason, "PROVIDER_FAILED");
  assert.equal(res.orderable, false);
  assert.equal(res.evidence.length, 2);
  const good = res.evidence.find((e) => e.modelId === "fixture-model-good");
  const bad = res.evidence.find((e) => e.modelId === "fixture-model-bad");
  assert.ok(good, "成功模型 evidence 不得因部分失败丢弃");
  assert.equal(good.decisionClass, "APPROVED");
  assert.notEqual(good.parsedDecision, null);
  assert.ok(bad);
  assert.equal(bad.decisionClass, "NO_ACTION");
  assert.equal(bad.failClosedReason, "PROVIDER_FAILED");
});

await test("⑥ 未实测成本 amount=null / evidence_level=UNVERIFIED，且未被 estCostUsd 回填", () => {
  const pr = legalProviderResult({
    estCostUsd: 0.0123,
    state: { costUsd: 0.0456 },
    // 故意不提供 actualInferenceCost
  });
  const r = EV.buildInferenceEvidence(legalParams({ providerResult: pr }));
  assert.equal(r.actualInferenceCost.amount, null);
  assert.equal(r.actualInferenceCost.evidence_level, "UNVERIFIED");
  assert.equal(r.actualInferenceCost.provenance, "UNVERIFIED");
  assert.equal(r.actualInferenceCost.note, EV.HONEST_EMPTY_NOTE);
  assert.notEqual(r.actualInferenceCost.amount, 0.0123, "禁止用估算值回填");
  assert.notEqual(r.actualInferenceCost.amount, 0.0456, "禁止用预算/状态值回填");
  assert.equal(Object.prototype.hasOwnProperty.call(r.actualInferenceCost, "estCostUsd"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(r.actualInferenceCost, "budget"), false);
});

await test("⑦ 两模型各 usage=10 → 各自 10，断言不存在聚合值 20", async () => {
  const impl = () => legalProviderResult({
    tokenUsage: { prompt: 4, completion: 6, total: 10, provenance: "FIXTURE_REPORTED" },
  });
  const res = await MM.runMultiModel({
    models: [
      { modelId: "fixture-model-a", riskVerdict: "APPROVED" },
      { modelId: "fixture-model-b", riskVerdict: "APPROVED" },
    ],
    providerImpl: impl,
    timeoutMs: 1000,
    nowMs: NOW,
  });
  assert.equal(res.evidence.length, 2);
  for (const e of res.evidence) {
    assert.equal(e.tokenUsage.total, 10);
    assert.equal(e.tokenUsage.prompt, 4);
    assert.equal(e.tokenUsage.completion, 6);
    assert.equal(e.tokenUsage.note, null);
  }
  for (const k of ["tokenUsage", "usage", "actualInferenceCost", "totalTokens", "aggregatedCost"]) {
    assert.equal(Object.prototype.hasOwnProperty.call(res, k), false, "结果不得含聚合字段 " + k);
  }
  for (const v of Object.values(res)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      assert.equal(Object.prototype.hasOwnProperty.call(v, "total"), false, "结果顶层不得有含 total 的聚合对象");
    }
  }
  const usageAndCostOnly = JSON.stringify(
    res.evidence.map((e) => ({ usage: e.tokenUsage, cost: e.actualInferenceCost })),
  );
  assert.equal(/"total":20/.test(usageAndCostOnly), false, "绝不存在聚合值 20");
});

await test("⑧ 成本口径绝不合并：merge 对象被拒绝，未实测值不被回填", () => {
  const mergeCost = {
    amount: 0.03,
    currency: "USD",
    provenance: "MERGED",
    evidence_level: "MIXED",
    merged: true,
    sources: ["budget", "estimated", "measured"],
    models: ["fixture-model-a", "fixture-model-b"],
  };
  assert.throws(
    () => EV.buildInferenceEvidence(
      legalParams({ providerResult: legalProviderResult({ actualInferenceCost: mergeCost }) }),
    ),
    /COST_MERGE_REJECTED/,
  );
  const estimatedOnly = { amount: 0.0099, currency: "USD", provenance: "ESTIMATED", evidence_level: "ESTIMATED" };
  const r = EV.buildInferenceEvidence(
    legalParams({ providerResult: legalProviderResult({ actualInferenceCost: estimatedOnly }) }),
  );
  assert.equal(r.actualInferenceCost.amount, null, "单一估算口径也不得回填金额");
  assert.equal(r.actualInferenceCost.evidence_level, "UNVERIFIED");
});

await test("⑨ 证据链追加 3 条校验通过；篡改第 2 条 / 第 3 条 _prev 均被检出", () => {
  cleanTmp();
  try {
    for (let i = 0; i < 3; i += 1) {
      const rec = EV.buildInferenceEvidence(
        legalParams({ modelId: "fixture-model-" + i, nowMs: NOW + i }),
      );
      EV.appendEvidence(TMP, rec);
    }
    const ok = EV.loadAndVerifyChain(TMP);
    assert.equal(ok.valid, true, JSON.stringify(ok.errors));
    assert.equal(ok.count, 3);
    assert.equal(ok.records[0]._prev, "GENESIS");
    assert.equal(ok.records[1]._prev, ok.records[0]._hash);
    assert.equal(ok.records[2]._prev, ok.records[1]._hash);
    assert.equal(ok.tip, ok.records[2]._hash);
    assert.equal(EV.computeChainHash(ok.records[0]), ok.records[0]._hash, "重算首条 hash 防篡改");

    const originalText = readFileSync(TMP, "utf8");
    const originalLines = originalText.trim().split("\n");

    // 篡改第 2 条内容（改业务字段，但不重算 _hash）
    const line2 = JSON.parse(originalLines[1]);
    line2.rawResponseHash = "0".repeat(64);
    writeFileSync(TMP, [originalLines[0], JSON.stringify(line2), originalLines[2]].join("\n") + "\n", "utf8");
    const afterContent = EV.loadAndVerifyChain(TMP);
    assert.equal(afterContent.valid, false, "篡改第 2 条内容必须被检出");
    assert.ok(afterContent.errors.some((e) => e.code === "HASH_MISMATCH" || e.code === "PREV_MISMATCH"));

    // 把第 3 条 _prev 改成 GENESIS
    const line3 = JSON.parse(originalLines[2]);
    line3._prev = "GENESIS";
    writeFileSync(TMP, [originalLines[0], originalLines[1], JSON.stringify(line3)].join("\n") + "\n", "utf8");
    const afterPrev = EV.loadAndVerifyChain(TMP);
    assert.equal(afterPrev.valid, false, "第 3 条 _prev 改成 GENESIS 必须被检出");
    assert.ok(afterPrev.errors.some((e) => e.code === "PREV_MISMATCH"));
  } finally {
    cleanTmp();
  }
});

await test("⑩ 禁网：源码静态自检 + 运行时守卫调用次数为 0", () => {
  const SOURCE_URLS = [
    new URL("./inference-evidence.test.mjs", import.meta.url),
    new URL("../inference-evidence.mjs", import.meta.url),
    new URL("../multi-model-provider.mjs", import.meta.url),
    new URL("../index.mjs", import.meta.url),
  ];
  // 以下三个模式都用拼接构造，确保本测试源文件自身不含被禁止的字面串。
  const FORBIDDEN_CALL = new RegExp("fet" + "ch" + "\\s*" + "\\(");
  const FORBIDDEN_HTTP_MOD = "node:" + "http";
  const FORBIDDEN_HTTPS_MOD = "node:" + "https";
  const FORBIDDEN_URL = new RegExp("http" + "s?" + ":" + "/" + "/");
  for (const u of SOURCE_URLS) {
    const src = readFileSync(u, "utf8");
    assert.equal(FORBIDDEN_CALL.test(src), false, "源码含主动网络调用: " + String(u));
    assert.equal(src.includes(FORBIDDEN_HTTP_MOD), false, "源码引用 http 模块: " + String(u));
    assert.equal(src.includes(FORBIDDEN_HTTPS_MOD), false, "源码引用 https 模块: " + String(u));
    assert.equal(FORBIDDEN_URL.test(src), false, "源码含真实地址: " + String(u));
  }
  assert.equal(networkCallCount, 0, "运行期间不得发起任何网络调用");
  assert.equal(typeof IDX.buildInferenceEvidence, "function");
  assert.equal(typeof IDX.runMultiModel, "function");
  assert.equal(IDX.INFERENCE_EVIDENCE_SCHEMA_VERSION, EV.INFERENCE_EVIDENCE_SCHEMA_VERSION);
});

await test("⑪ fail-closed：必填缺失 / raw 为空 / hash 非法 / riskVerdict 缺失一律抛错", () => {
  assert.throws(
    () => EV.buildInferenceEvidence(legalParams({ providerResult: undefined })),
    /providerResult/,
  );
  assert.throws(() => EV.buildInferenceEvidence(legalParams({ modelId: "" })), /modelId/);
  assert.throws(
    () => EV.buildInferenceEvidence(
      legalParams({ providerResult: legalProviderResult({ rawResponseBytes: "" }) }),
    ),
    /原始响应为空/,
  );
  assert.throws(() => EV.buildInferenceEvidence(legalParams({ marketSnapshotHash: "abc" })), /64 位 hex/);
  assert.throws(() => EV.buildInferenceEvidence(legalParams({ riskVerdict: "" })), /riskVerdict/);
  assert.throws(() => EV.buildInferenceEvidence(legalParams({ riskVerdict: undefined })), /riskVerdict/);
});

await test("⑫ 证据链损坏时 appendEvidence 拒绝追加（fail-closed）", () => {
  cleanTmp();
  try {
    const rec = EV.buildInferenceEvidence(legalParams());
    EV.appendEvidence(TMP, rec);
    const line = JSON.parse(readFileSync(TMP, "utf8").trim());
    line.modelId = "TAMPERED";
    writeFileSync(TMP, JSON.stringify(line) + "\n", "utf8");
    assert.equal(EV.loadAndVerifyChain(TMP).valid, false);
    assert.throws(() => EV.appendEvidence(TMP, rec), /EVIDENCE_CHAIN_INVALID/);
  } finally {
    cleanTmp();
  }
});

await test("⑬ 默认 fixture provider 纯内存、诚实标记、无实测成本", async () => {
  const res = await MM.runMultiModel({ models: ["fixture-default"], timeoutMs: 1000, nowMs: NOW });
  assert.equal(res.evidence.length, 1);
  const e = res.evidence[0];
  assert.equal(e.isReal, false);
  assert.equal(e.dataMode, "fixture");
  assert.equal(e.providerId, "mock");
  assert.equal(e.riskVerdict, null);
  assert.equal(e.marketContextId, null);
  assert.equal(e.tokenUsage.total, null);
  assert.equal(e.tokenUsage.provenance, "NOT_MEASURED");
  assert.equal(e.tokenUsage.note, EV.HONEST_EMPTY_NOTE);
  assert.equal(e.actualInferenceCost.amount, null);
  assert.equal(e.actualInferenceCost.evidence_level, "UNVERIFIED");
  assert.equal(e.actualInferenceCost.note, EV.HONEST_EMPTY_NOTE);
  assert.equal(e.hashScopes.marketSnapshotHash, "raw_upstream_bytes");
  assert.equal(e.note, EV.HONEST_EMPTY_NOTE);
  assert.equal(res.orderable, false);
  assert.equal(res.execution, "NONE");
  assert.equal(networkCallCount, 0);
});

await test("⑭ 诚实空值反例：未实测 null + note，禁止 0 / 空对象 / 预算值回填", () => {
  const emptyPr = {
    ok: false,
    parseFailed: true,
    rawResponseBytes: "PROVIDER_FAILURE_NO_RAW",
    parsedDecision: null,
    tokenUsage: {},
    actualInferenceCost: { amount: 0, currency: "USD", provenance: "BUDGET", evidence_level: "BUDGET" },
    estCostUsd: 0.0123,
    state: { costUsd: 0.0456 },
  };
  const r = EV.buildInferenceEvidence(legalParams({
    providerResult: emptyPr,
    riskVerdict: null,
    marketContextId: undefined,
  }));
  assert.equal(r.parsedDecision, null);
  assert.equal(r.riskVerdict, null);
  assert.equal(r.marketContextId, null);
  assert.equal(r.tokenUsage.prompt, null);
  assert.equal(r.tokenUsage.completion, null);
  assert.equal(r.tokenUsage.total, null);
  assert.equal(r.tokenUsage.provenance, "NOT_MEASURED");
  assert.equal(r.tokenUsage.note, EV.HONEST_EMPTY_NOTE);
  assert.equal(r.actualInferenceCost.amount, null);
  assert.equal(r.actualInferenceCost.evidence_level, "UNVERIFIED");
  assert.equal(r.actualInferenceCost.note, EV.HONEST_EMPTY_NOTE);
  assert.equal(r.note, EV.HONEST_EMPTY_NOTE);
  assert.notEqual(r.tokenUsage.total, 0);
  assert.notEqual(r.actualInferenceCost.amount, 0);
  assert.notEqual(r.actualInferenceCost.amount, 0.0123);
  assert.notEqual(r.actualInferenceCost.amount, 0.0456);
  assert.equal(Object.keys(r.tokenUsage).length > 0, true, "不得是空对象");
  assert.equal(EV.HONEST_EMPTY_NOTE, "UNKNOWN, never 0. No real inference has been executed.");
  // 全 0 的 tokenUsage 也必须降级为 null + note
  const z = EV.buildInferenceEvidence(legalParams({
    providerResult: legalProviderResult({ tokenUsage: { prompt: 0, completion: 0, total: 0 } }),
  }));
  assert.equal(z.tokenUsage.total, null, "全 0 填充必须视为未实测");
  assert.equal(z.tokenUsage.note, EV.HONEST_EMPTY_NOTE);
});

await test("⑮ mock 诚实性 + verdict 口径：provider_id=mock / 无凭据 BLOCKED / 0 样本 NO_DATA / 单次 test 200+内容才 CONNECTED", () => {
  const pr = legalProviderResult({
    providerId: "MiniMax-M3",
    provider_id: "MiniMax-M3",
    dataMode: "real",
    data_mode: "live",
    isReal: true,
  });
  const r = EV.buildInferenceEvidence(legalParams({ providerResult: pr, modelId: "MiniMax-M3" }));
  assert.equal(r.providerId, "mock", "fixture provider_id 强制字面量 mock");
  assert.equal(r.isReal, false, "isReal 由服务器写，绝不采信注入的 real");
  assert.equal(r.dataMode, "fixture", "data_mode 由服务器写，绝不采信注入的 live");
  assert.equal(r.modelId, "MiniMax-M3", "modelId 原样留存，但不得据此冒充 real 推理");

  // 列表态 verdict 只允许 BLOCKED / UNVERIFIED，绝不 CONNECTED
  assert.equal(EV.providerListVerdict({ credentialPresent: false }), "BLOCKED");
  assert.equal(EV.providerListVerdict({ credentialPresent: true }), "UNVERIFIED");
  assert.ok(EV.LIST_VERDICT_STATES.includes(EV.providerListVerdict({ credentialPresent: false })));
  assert.ok(EV.LIST_VERDICT_STATES.includes(EV.providerListVerdict({ credentialPresent: true })));
  assert.equal(EV.LIST_VERDICT_STATES.includes("CONNECTED"), false);

  // 无凭据 → BLOCKED 不是 CONNECTED
  assert.equal(EV.providerTestStatus({ credentialPresent: false, httpStatus: 200, hasContent: true }), "BLOCKED");
  // 单次 test 仅真实 200 + 有内容 → CONNECTED（不全局禁）
  assert.equal(EV.providerTestStatus({ credentialPresent: true, httpStatus: 200, hasContent: true }), "CONNECTED");
  assert.equal(EV.providerTestStatus({ credentialPresent: true, httpStatus: 200, hasContent: false }), "UNVERIFIED");
  assert.equal(EV.providerTestStatus({ credentialPresent: true, httpStatus: 502, hasContent: true }), "UNVERIFIED");
  // 0 样本绩效 → NO_DATA 不是 0
  assert.equal(EV.performanceVerdict({ sampleCount: 0 }), "NO_DATA");
  assert.notEqual(EV.performanceVerdict({ sampleCount: 0 }), 0);
  assert.equal(EV.NO_DATA, "NO_DATA");
});

await test("⑯ hash_scope：marketSnapshotHash=raw_upstream_bytes（消费 P1）与 rawResponseHash 独立，绝不平移", () => {
  const r = EV.buildInferenceEvidence(legalParams({
    marketContextId: "mctx-fixture-016",
    marketSnapshotHash: MARKET_HASH,
  }));
  assert.equal(r.hashScopes.marketSnapshotHash, "raw_upstream_bytes");
  assert.equal(r.hashScopes.rawResponseHash, "raw_response_bytes");
  assert.notEqual(r.hashScopes.marketSnapshotHash, r.hashScopes.rawResponseHash);
  assert.equal(r.marketContextId, "mctx-fixture-016", "marketSnapshotHash 成对 marketContextId（消费 P1 锚点）");
  assert.notEqual(r.marketSnapshotHash, r.rawResponseHash, "两个 hash 指不同字节范围，绝不混用");
  assert.equal(EV.HASH_SCOPE_MARKET_SNAPSHOT, "raw_upstream_bytes");
  assert.equal(EV.HASH_SCOPE_RAW_RESPONSE, "raw_response_bytes");
  // 反例：把脱敏响应 / 规范化输入 / 响应字节口径平移成快照哈希 → 一律拒绝
  for (const bad of EV.FORBIDDEN_SNAPSHOT_SCOPES) {
    assert.throws(
      () => EV.buildInferenceEvidence(legalParams({
        providerResult: legalProviderResult({ hash_scope: bad }),
      })),
      /SNAPSHOT_SCOPE_FORBIDDEN/,
    );
  }
  // 合法显式声明 P1 scope 可通过
  const ok = EV.buildInferenceEvidence(legalParams({
    providerResult: legalProviderResult({ hash_scope: "raw_upstream_bytes" }),
  }));
  assert.equal(ok.hashScopes.marketSnapshotHash, "raw_upstream_bytes");
});

await test("⑰ index.mjs：MODEL_ID_AUTHORITY 只声明不接线，不自造第五套清单", () => {
  assert.equal(IDX.MODEL_ID_AUTHORITY, "server/ai/runtime.mjs:providerRegistryFromEnv");
  assert.equal(IDX.BINDING, "DECLARED_NOT_WIRED");
  assert.equal(IDX.BINDING_TARGETS.hash_scope, "raw_upstream_bytes");
  assert.equal(IDX.BINDING_TARGETS.ai_market_contexts_table, "DECLARED_NOT_WIRED");
  assert.equal(IDX.BINDING_TARGETS.day14_runner, "DECLARED_NOT_WIRED");
  assert.match(IDX.SIX_ROLE_MAPPING, /OWNER-DECIDED/);
  assert.match(IDX.SIX_ROLE_MAPPING, /UNVERIFIED/);
  // index.mjs 不 import 任何模块（纯 re-export + 常量声明），不假装已接线
  const idxSrc = readFileSync(new URL("../index.mjs", import.meta.url), "utf8");
  assert.equal(/^\s*import\s/m.test(idxSrc), false, "index.mjs 不得 import runtime.mjs 或任何模块");
  assert.equal(idxSrc.includes("from \"./inference-evidence.mjs\""), true);
  assert.equal(idxSrc.includes("from \"./multi-model-provider.mjs\""), true);
});

// —— 全局兜底：整轮运行期间网络守卫调用次数必须为 0 ——
assert.equal(networkCallCount, 0, "整轮运行期间不得发起任何网络调用");

console.log("\n---- P2 inference-evidence 测试摘要 ----");
console.log("PASS=" + passed + " FAIL=" + failed);
if (failed > 0) {
  console.error("FAIL 用例: " + failures.join(" | "));
  process.exitCode = 1;
}
