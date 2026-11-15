/**
 * P1 正式数据层 — 反例驱动验收测试（node:assert/strict 手写断言）。
 *
 * 运行：node apps/equity_collector_v45/tests/market-data.test.mjs
 * 退出码非 0 即失败；如实打印 PASS / FAIL 计数。
 *
 * 零网络原语：
 *   - 本文件与 market-data/ 全部源码不得出现全局取数原语、HTTP(S)/net/tls
 *     内置模块、第三方网络库或任何真实 URL（用拼接串做静态自审，避免自指）。
 *   - 动态 import 模块前把 globalThis.fetch 替换为计数守卫，结束断言从未被调用。
 *   - 全部 store 用内存 IO（readImpl/writeImpl/existsImpl/mkdirImpl 注入，
 *     纯 Map），不写磁盘、不触网、不读真实时钟（nowMs 固定注入）。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve, dirname, join } from "node:path";

// ---- 零网络守卫：必须在动态 import 之前装好 --------------------------------
let networkFetchCalls = 0;
const originalFetch = globalThis.fetch;
globalThis.fetch = (...args) => {
  networkFetchCalls += 1;
  // 返回无害占位值：若某模块误用全局 fetch，计数会暴露且测试判 FAIL。
  return Promise.resolve({ ok: false, status: 0, text: async () => "" });
};

let md;
try {
  md = await import("../src/market-data/index.mjs");
} catch (e) {
  console.error("[FATAL] market-data modules failed to import:", (e && e.stack) || e);
  process.exit(1);
}

// ---- 固定注入 ------------------------------------------------------------
const FIXED_NOW = 1700000000000;
const TEST_FILE = resolve(process.argv[1]);
const SRC_DIR = join(dirname(TEST_FILE), "..", "src", "market-data");
const MODULE_FILES = [
  "append-only-log.mjs",
  "provenance-guard.mjs",
  "snapshot-store.mjs",
  "bar-store.mjs",
  "orderbook-journal.mjs",
  "quality-monitor.mjs",
  "replay-dataset.mjs",
  "index.mjs",
];

/** 内存 IO：多个实例共享同一个 Map，模拟重启续接（两次实例化）。 */
function makeIO() {
  const files = new Map();
  return {
    files,
    readImpl: (p) => (files.has(p) ? files.get(p) : null),
    writeImpl: (p, text) => { files.set(p, (files.get(p) || "") + text); },
    existsImpl: (p) => files.has(p),
    mkdirImpl: (_d) => {},
  };
}

function opts(io, runId, extra = {}) {
  return {
    baseDir: "mem",
    runId,
    readImpl: io.readImpl,
    writeImpl: io.writeImpl,
    existsImpl: io.existsImpl,
    mkdirImpl: io.mkdirImpl,
    nowMs: FIXED_NOW,
    ...extra,
  };
}

/** 用权威 buildMarketEnvelope 补全信封（已存在字段保留，便于注入非法值反例）。 */
function withEnvelope(base, overrides, recordId) {
  const merged = { ...base, ...overrides };
  return md.buildMarketEnvelope(merged, {
    isReal: !!(merged.provenance && merged.provenance.isReal === true),
    recordId,
  });
}

function snapRecord(overrides = {}) {
  return withEnvelope({
    type: "snapshot",
    seq: 0,
    symbol: "BTCUSDT",
    marksUsd: { BTCUSDT: 70000 },
    priceTs: FIXED_NOW,
    priceTsSource: "fixture",
    provenance: md.buildFixtureProvenance({ symbol: "BTCUSDT", capturedAtMs: FIXED_NOW }),
  }, overrides, "snap-test");
}

function barRecord(overrides = {}) {
  const openTime = 1700000000000;
  return withEnvelope({
    type: "bar",
    interval: "1m",
    symbol: "BTCUSDT",
    openTime,
    closeTime: openTime + 60000,
    open: 70000,
    high: 70010,
    low: 69990,
    close: 70002,
    volume: 100,
    provenance: md.buildFixtureProvenance({ symbol: "BTCUSDT", capturedAtMs: FIXED_NOW }),
  }, overrides, "bar-test");
}

function obRecord(overrides = {}) {
  const bid = 69990;
  const ask = 70010;
  const spread = ask - bid;
  const mid = (bid + ask) / 2;
  return withEnvelope({
    type: "orderbook",
    symbol: "BTCUSDT",
    lastUpdateId: 1000,
    bids: [[bid, 10], [bid - 10, 20]],
    asks: [[ask, 10], [ask + 10, 20]],
    bid,
    ask,
    spread,
    spreadBps: (spread / mid) * 10000,
    provenance: md.buildFixtureProvenance({ symbol: "BTCUSDT", capturedAtMs: FIXED_NOW }),
  }, overrides, "ob-test");
}

/** 按 code 精确匹配抛出的拒绝错误（message 内已带 [code]）。 */
function throwsCode(fn, code) {
  assert.throws(fn, new RegExp(code), `expected rejection code ${code}`);
}

// ---- 测试框架 ------------------------------------------------------------
let PASS = 0;
let FAIL = 0;
const failures = [];
async function test(name, fn) {
  try {
    await fn();
    PASS += 1;
    console.log(`[PASS] ${name}`);
  } catch (e) {
    FAIL += 1;
    const msg = (e && e.message) || String(e);
    failures.push({ name, msg });
    console.log(`[FAIL] ${name} :: ${msg}`);
  }
}

// =========================================================================
// 1) 三 store 正常追加 + 重启续接链
// =========================================================================
await test("1 三 store 正常追加 + 重启后续接链 PASS", async () => {
  const io = makeIO();
  const rid = "run-t1";
  const s1 = md.createSnapshotStore(opts(io, rid));
  const b1 = md.createBarStore(opts(io, rid));
  const o1 = md.createOrderbookJournal(opts(io, rid));

  assert.equal(s1.count, 0);
  assert.equal(s1.tip, "GENESIS");
  assert.equal(s1.genesis, "GENESIS");

  await s1.capture(); await s1.capture();
  await b1.capture(); await b1.capture();
  await o1.capture(); await o1.capture();
  assert.equal(s1.count, 2);
  assert.equal(b1.count, 2);
  assert.equal(o1.count, 2);

  // 原始 JSONL：首条 GENESIS，逐条 _prev === 上条 _hash，_hash 为 sha256 hex
  const raw = io.files.get(s1.path);
  const lines = raw.trim().split("\n");
  assert.equal(lines.length, 2);
  const first = JSON.parse(lines[0]);
  const second = JSON.parse(lines[1]);
  assert.equal(first._prev, "GENESIS");
  assert.equal(typeof first._hash, "string");
  assert.equal(first._hash.length, 64);
  assert.equal(second._prev, first._hash);
  assert.equal(second._hash, s1.tip);

  // 哈希溯源信封贯穿 snapshot / bar / orderbook 三类记录（fixture 前缀 mc_fx_、本地文件、不落库）
  const allRecs = [...s1.readAll(), ...b1.readAll(), ...o1.readAll()];
  assert.equal(allRecs.length, 6);
  for (const rec of allRecs) {
    assert.equal(rec.hash_scope, "raw_upstream_bytes");
    assert.equal(rec.hash_algorithm, "sha256");
    assert.equal(rec.store_kind, "file");
    assert.equal(rec.db_persisted, false);
    assert.ok(String(rec.market_context_id).startsWith("mc_fx_"));
    assert.ok(/^[0-9a-f]{64}$/.test(rec.marketSnapshotHash));
    assert.ok(rec.payload_byte_range && rec.payload_byte_range.real_upstream_status === "NOT_MEASURED");
  }

  // 重启：同 runId 同 IO 再实例化，构造时全量校验链 + 恢复 tip
  const s2 = md.createSnapshotStore(opts(io, rid));
  const b2 = md.createBarStore(opts(io, rid));
  const o2 = md.createOrderbookJournal(opts(io, rid));
  assert.equal(s2.count, 2);
  assert.equal(b2.count, 2);
  assert.equal(o2.count, 2);
  assert.equal(s2.tip, s1.tip);
  assert.equal(b2.tip, b1.tip);
  assert.equal(o2.tip, o1.tip);

  // 续接追加
  await s2.capture();
  await b2.capture();
  await o2.capture();
  assert.equal(s2.count, 3);
  assert.equal(b2.count, 3);
  assert.equal(o2.count, 3);
  assert.equal(s2.verifyChain().ok, true);
  assert.equal(b2.verifyChain().ok, true);
  assert.equal(o2.verifyChain().ok, true);
  // orderbook 续接仍连续：1000 / 1001 / 1002
  const obs = o2.readAll();
  assert.deepEqual(obs.map((r) => r.lastUpdateId), [1000, 1001, 1002]);
});

// =========================================================================
// 2) 篡改内容 / _prev 断链 -> 重算 hash 与断链检出
// =========================================================================
await test("2 篡改已有记录内容 -> 重算 hash 检出；_prev 写 GENESIS -> 断链检出", async () => {
  // 2a：改 body 内容但保留原 _hash -> HASH TAMPER
  {
    const io = makeIO();
    const rid = "run-t2a";
    const s = md.createSnapshotStore(opts(io, rid));
    await s.capture(); await s.capture();
    const lines = io.files.get(s.path).trim().split("\n");
    const obj = JSON.parse(lines[0]);
    obj.marksUsd.BTCUSDT = 999999; // 篡改内容，_hash 不动
    lines[0] = JSON.stringify(obj);
    io.files.set(s.path, lines.join("\n") + "\n");
    assert.throws(() => s.verifyChain(), /HASH TAMPER/);
    assert.throws(() => md.createSnapshotStore(opts(io, rid)), /HASH TAMPER/);
  }
  // 2b：把第二条 _prev 改成 GENESIS -> CHAIN BROKEN
  {
    const io = makeIO();
    const rid = "run-t2b";
    const s = md.createSnapshotStore(opts(io, rid));
    await s.capture(); await s.capture();
    const lines = io.files.get(s.path).trim().split("\n");
    const obj = JSON.parse(lines[1]);
    obj._prev = "GENESIS";
    lines[1] = JSON.stringify(obj);
    io.files.set(s.path, lines.join("\n") + "\n");
    assert.throws(() => s.verifyChain(), /CHAIN BROKEN/);
    assert.throws(() => md.createSnapshotStore(opts(io, rid)), /CHAIN BROKEN/);
  }
});

// =========================================================================
// 3) bar 时间倒退 / 重复 openTime / high<low -> 拒绝
// =========================================================================
await test("3 bar 时间倒退 / 重复 openTime / high<low -> 拒绝", async () => {
  const io = makeIO();
  const rid = "run-t3";
  const b = md.createBarStore(opts(io, rid));
  await b.capture();
  const base = b.readAll()[0];

  throwsCode(
    () => b.append(barRecord({ openTime: base.openTime - 1, closeTime: base.openTime + 59999 })),
    md.BAR_CODES.BAR_TIME_REGRESSION,
  );
  throwsCode(
    () => b.append(barRecord({ openTime: base.openTime, closeTime: base.openTime + 60000 })),
    md.BAR_CODES.BAR_DUPLICATE_OPENTIME,
  );
  throwsCode(
    () => b.append(barRecord({
      openTime: base.openTime + 60000,
      closeTime: base.openTime + 120000,
      high: 1,
      low: 100,
    })),
    md.BAR_CODES.BAR_OHLC_INVALID,
  );
  assert.equal(b.count, 1, "none of the rejected bars should be appended");
});

// =========================================================================
// 4) orderbook lastUpdateId 回退或跳号、bid>=ask -> 拒绝
// =========================================================================
await test("4 orderbook lastUpdateId 回退/跳号、bid>=ask -> 拒绝", async () => {
  const io = makeIO();
  const rid = "run-t4";
  const o = md.createOrderbookJournal(opts(io, rid));
  await o.capture(); // lastUpdateId 1000

  throwsCode(() => o.append(obRecord({ lastUpdateId: 999 })), md.ORDERBOOK_CODES.SEQUENCE_REGRESSION);
  throwsCode(() => o.append(obRecord({ lastUpdateId: 1005 })), md.ORDERBOOK_CODES.SEQUENCE_GAP);
  throwsCode(
    () => o.append(obRecord({
      lastUpdateId: 1001,
      bids: [[70020, 10]],
      asks: [[70010, 10]],
      bid: 70020,
      ask: 70010,
      spread: -10,
      spreadBps: -1.4,
    })),
    md.ORDERBOOK_CODES.BID_GE_ASK,
  );
  assert.equal(o.count, 1, "none of the rejected books should be appended");
});

// =========================================================================
// 5) fixture 被标 isReal:true -> quality-monitor 与 provenance-guard 均 FAIL
// =========================================================================
await test("5 fixture 标 isReal:true -> quality-monitor 与 provenance-guard 均判 FAIL", async () => {
  const prov = md.buildFixtureProvenance({ symbol: "BTCUSDT", capturedAtMs: FIXED_NOW });
  const bad = snapRecord({ provenance: { ...prov, isReal: true } });

  const g = md.guardRecord(bad);
  assert.equal(g.approved, false);
  assert.ok(
    [md.GUARD_CODES.ISREAL_DATAMODE_CONFLICT, md.GUARD_CODES.FIXTURE_MISLABELED_LIVE].includes(g.code),
    `unexpected guard code: ${g.code}`,
  );

  const q = md.evaluateQuality({ snapshots: [bad], nowMs: FIXED_NOW });
  assert.equal(q.verdict, "FAIL");
  assert.equal(q.ok, false);
  assert.ok(
    q.reasons.some((r) => r.code === md.QUALITY_CODES.ISREAL_DATAMODE_CONFLICT ||
      r.code === md.QUALITY_CODES.FIXTURE_MISLABELED_LIVE),
    "quality reasons must flag the fixture/real contradiction",
  );

  // 反例对照：正常 fixture 两者均放行
  const good = snapRecord();
  assert.equal(md.guardRecord(good).approved, true);
  assert.equal(md.evaluateQuality({ snapshots: [good], nowMs: FIXED_NOW }).verdict, "PASS");
});

// =========================================================================
// 6) 缺失实测值填 0 -> provenance-guard 判 FAIL
// =========================================================================
await test("6 缺失实测值填 0 -> provenance-guard 判 FAIL", async () => {
  const prov = md.buildFixtureProvenance({
    symbol: "BTCUSDT", capturedAtMs: FIXED_NOW, notMeasuredFields: ["spread"],
  });

  const fabricated = snapRecord({ provenance: prov, measurements: { spread: 0 } });
  const g = md.guardRecord(fabricated);
  assert.equal(g.approved, false);
  assert.equal(g.code, md.GUARD_CODES.MEASURED_VALUE_FABRICATED);

  // 诚实写法 null + NOT_MEASURED 声明 -> 放行
  const honest = snapRecord({ provenance: prov, measurements: { spread: null } });
  assert.equal(md.guardRecord(honest).approved, true);

  // 反向矛盾：声明 MEASURED 却为 null -> FAIL
  const missing = snapRecord({
    provenance: md.buildFixtureProvenance({ symbol: "BTCUSDT", capturedAtMs: FIXED_NOW }),
    measurementStatus: { price: "MEASURED" },
    measurements: { price: null },
  });
  assert.equal(md.guardRecord(missing).code, md.GUARD_CODES.MEASURED_VALUE_MISSING);
});

// =========================================================================
// 7) replay 确定性 + 含 fixture 标 containsFixture/绝不 LIVE
// =========================================================================
await test("7 replay 数据集确定性 + 含 fixture 时 containsFixture:true、绝不 LIVE", async () => {
  const io = makeIO();
  const rid = "run-t7";
  const s = md.createSnapshotStore(opts(io, rid));
  const b = md.createBarStore(opts(io, rid));
  const o = md.createOrderbookJournal(opts(io, rid));
  await s.capture(); await b.capture(); await o.capture();
  const snapshots = s.readAll();
  const bars = b.readAll();
  const orderbooks = o.readAll();

  const d1 = md.buildReplayDataset({ snapshots, bars, orderbooks });
  const d2 = md.buildReplayDataset({ snapshots, bars, orderbooks });
  assert.equal(d1.datasetHash, d2.datasetHash, "same input must produce identical hash");
  assert.equal(d1.datasetHash.length, 64);
  assert.equal(d1.recordCount, snapshots.length + bars.length + orderbooks.length);
  assert.equal(d1.perStream.snapshots, snapshots.length);
  assert.equal(d1.perStream.bars, bars.length);
  assert.equal(d1.perStream.orderbooks, orderbooks.length);

  // 全部为 fixture -> 整集标 FIXTURE，绝不 LIVE
  assert.equal(d1.isRealSummary.containsFixture, true);
  assert.equal(d1.label, "FIXTURE");
  assert.notEqual(d1.label, "LIVE");
  assert.equal(d1.isLive, false);
  assert.equal(d1.tradeable, false);
  assert.ok(d1.provenanceSummary.venues.includes("LOCAL_FIXTURE"));
  assert.ok(d1.provenanceSummary.dataModes.includes("fixture"));

  // 哈希溯源信封贯穿 replay：汇总 + datasetHash 覆盖这些字段
  assert.ok(d1.envelopeSummary.hashScopes.includes("raw_upstream_bytes"));
  assert.deepEqual(d1.envelopeSummary.storeKinds, ["file"]);
  assert.deepEqual(d1.envelopeSummary.dbPersistedValues, [false]);
  assert.equal(d1.envelopeSummary.marketContextIds.length, 3);
  assert.equal(d1.envelopeSummary.marketSnapshotHashesPresent, 3);

  const v = md.verifyReplayDataset(d1);
  assert.equal(v.ok, true);
  assert.equal(v.hashMatch, true);
  assert.equal(v.liveInvariantOk, true);

  // 内容敏感：改一条记录 -> hash 变化（证明 hash 覆盖规范化内容）
  const d3 = md.buildReplayDataset({
    snapshots: [{ ...snapshots[0], marksUsd: { BTCUSDT: 1 } }],
    bars,
    orderbooks,
  });
  assert.notEqual(d3.datasetHash, d1.datasetHash);

  // datasetHash 覆盖 hash_scope / market_context_id 等信封字段
  const d4 = md.buildReplayDataset({
    snapshots: [{ ...snapshots[0], hash_scope: "canonical_row" }],
    bars, orderbooks,
  });
  assert.notEqual(d4.datasetHash, d1.datasetHash);
  const d5 = md.buildReplayDataset({
    snapshots: [{ ...snapshots[0], market_context_id: "mc_fx_tampered" }],
    bars, orderbooks,
  });
  assert.notEqual(d5.datasetHash, d1.datasetHash);

  // 空集也绝不标 LIVE
  const empty = md.buildReplayDataset({});
  assert.equal(empty.label, "EMPTY");
  assert.equal(empty.isLive, false);
  assert.equal(empty.isRealSummary.containsFixture, false);
});

// =========================================================================
// 8) 零网络原语静态自审 + globalThis.fetch 守卫未被调用
// =========================================================================
await test("8 零网络原语：源码无网络取数/HTTP 模块/真实 URL；fetch 守卫未被调用", async () => {
  // 拼接构造禁用串，避免本文件自身出现这些字面量（自审自指）。
  const forbidden = [
    "fet" + "ch(",
    "node:ht" + "tp",
    "und" + "ici",
    "node:n" + "et",
    "node:t" + "ls",
    ":" + "//",
  ];
  const files = [...MODULE_FILES.map((f) => join(SRC_DIR, f)), TEST_FILE];
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    for (const pat of forbidden) {
      assert.ok(!src.includes(pat), `forbidden primitive ${JSON.stringify(pat)} found in ${f}`);
    }
  }
  assert.equal(networkFetchCalls, 0, "globalThis.fetch must never be invoked");
  // hashImpl 注入演示：换一个确定性哈希实现，链仍自洽
  {
    const io = makeIO();
    const rid = "run-t8-hash";
    const fakeHash = (s) => "H" + String(s).length;
    const s1 = md.createSnapshotStore(opts(io, rid, { hashImpl: fakeHash }));
    await s1.capture();
    assert.ok(typeof s1.tip === "string" && s1.tip.startsWith("H"), "injected hashImpl drives chain tip");
    const s2 = md.createSnapshotStore(opts(io, rid, { hashImpl: fakeHash }));
    assert.equal(s2.verifyChain().ok, true);
  }
});

// =========================================================================
// 9) hash_scope 非法值被拒绝（默认且仅允许 raw_upstream_bytes）
// =========================================================================
await test("9 hash_scope 非法值被拒绝（fail-closed，仅 raw_upstream_bytes）", async () => {
  const forbiddenScopes = [
    "redacted_response", "canonical_input", "canonical_row", "source_file", "bogus_scope",
  ];
  for (const scope of forbiddenScopes) {
    const rec = snapRecord({ hash_scope: scope });
    const g = md.guardRecord(rec);
    assert.equal(g.approved, false, `scope '${scope}' must be rejected by guardRecord`);
    assert.ok(
      [md.GUARD_CODES.HASH_SCOPE_FORBIDDEN, md.GUARD_CODES.HASH_SCOPE_UNKNOWN].includes(g.code),
      `unexpected code for scope '${scope}': ${g.code}`,
    );
    // 通过 store.append 也必须 fail-closed 拒绝，且不落链
    const io = makeIO();
    const s = md.createSnapshotStore(opts(io, `run-scope-${scope}`));
    assert.throws(() => s.append(rec), /HASH_SCOPE_FORBIDDEN|HASH_SCOPE_UNKNOWN/);
    assert.equal(s.count, 0, "rejected record must not be appended");
  }
  // 缺失 hash_scope 同样拒绝
  const noScope = snapRecord();
  delete noScope.hash_scope;
  assert.equal(md.guardRecord(noScope).code, md.GUARD_CODES.HASH_SCOPE_MISSING);
  // 合法值放行
  assert.equal(md.guardRecord(snapRecord()).approved, true);
  // 禁用别名（脱敏响应 / canonical 决策输入）不得充当快照原始字节哈希
  assert.equal(
    md.guardRecord(snapRecord({ response_payload_sha256: "x" })).code,
    md.GUARD_CODES.HASH_ALIAS_FORBIDDEN,
  );
  assert.equal(
    md.guardRecord(snapRecord({ input_snapshot_sha256: "x" })).code,
    md.GUARD_CODES.HASH_ALIAS_FORBIDDEN,
  );
  // 权威命名契约 / 未来接线目标登记（不得假装已落库）
  assert.equal(md.MARKET_SNAPSHOT_HASH_CONTRACT.runtimeField, "marketSnapshotHash");
  assert.equal(md.MARKET_SNAPSHOT_HASH_CONTRACT.reservedDbColumn, "market_snapshot_sha256");
  assert.equal(md.MARKET_SNAPSHOT_HASH_CONTRACT.reservedDbColumnStatus, "DECLARED_NOT_WIRED");
  assert.equal(md.MARKET_SNAPSHOT_HASH_CONTRACT.allowedScopeThisRound, "raw_upstream_bytes");
  assert.equal(md.AI_MARKET_CONTEXTS_BINDING, "DECLARED_NOT_WIRED");
  assert.deepEqual(md.FORBIDDEN_HASH_ALIASES, ["response_payload_sha256", "input_snapshot_sha256"]);
});

// =========================================================================
// 10) market_context_id 指向本地记录存在且 db_persisted===false
// =========================================================================
await test("10 market_context_id 本地可解析且 db_persisted===false（防伪造落库）", async () => {
  const io = makeIO();
  const rid = "run-t10";
  const s = md.createSnapshotStore(opts(io, rid));
  await s.capture();
  const rec = s.readAll()[0];
  assert.equal(rec.hash_scope, "raw_upstream_bytes");
  assert.ok(String(rec.market_context_id).startsWith("mc_fx_"), `unexpected prefix: ${rec.market_context_id}`);
  assert.equal(rec.store_kind, "file");
  assert.equal(rec.db_persisted, false);
  // 本地解析：market_context_id 指向的本地文件记录确实存在
  const resolved = s.resolveMarketContext(rec.market_context_id);
  assert.ok(resolved !== null, "market_context_id must resolve to a local record");
  assert.equal(resolved.db_persisted, false);
  assert.equal(resolved.store_kind, "file");
  assert.equal(resolved.market_context_id, rec.market_context_id);
  assert.equal(resolved.record_id, rec.record_id);
  // 重启后仍从本地链解析（不查 DB）
  const s2 = md.createSnapshotStore(opts(io, rid));
  const resolved2 = s2.resolveMarketContext(rec.market_context_id);
  assert.ok(resolved2 !== null);
  assert.equal(resolved2.db_persisted, false);
  // 不存在的 id 解析为 null
  assert.equal(s.resolveMarketContext("mc_fx_does_not_exist"), null);
  // 前缀派生：fixture mc_fx_ / real mctx-
  assert.ok(md.deriveMarketContextId({ isReal: false, recordId: "r1" }).startsWith("mc_fx_"));
  assert.ok(md.deriveMarketContextId({ isReal: true, recordId: "r1" }).startsWith("mctx-"));
  // 伪造落库声明一律拒绝（guard + store.append）
  assert.equal(md.guardRecord(snapRecord({ db_persisted: true })).code, md.GUARD_CODES.STORE_BINDING_INVALID);
  assert.equal(md.guardRecord(snapRecord({ store_kind: "db" })).code, md.GUARD_CODES.STORE_BINDING_INVALID);
  const io2 = makeIO();
  const s3 = md.createSnapshotStore(opts(io2, "run-t10-fake"));
  assert.throws(() => s3.append(snapRecord({ db_persisted: true })), /STORE_BINDING_INVALID/);
  assert.equal(s3.count, 0);
});

// ---- 收尾：如实汇总 ----------------------------------------------------
globalThis.fetch = originalFetch;

console.log("\n=== market-data.test SUMMARY ===");
console.log(`PASS=${PASS} FAIL=${FAIL}`);
if (failures.length) {
  for (const f of failures) console.log(`  - ${f.name} :: ${f.msg}`);
}
if (FAIL > 0 || networkFetchCalls !== 0) {
  console.log("RESULT=FAIL");
  process.exitCode = 1;
} else {
  console.log("RESULT=PASS");
}
