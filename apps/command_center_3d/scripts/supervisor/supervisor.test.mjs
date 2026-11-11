// supervisor.test.mjs — 手写断言；node <path> 必须 exit 0 才算通过
// 纯内存 fixture，DECLARED_NOT_WIRED：不启动守护进程、不触网、不下单、不碰真实资金。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as S from './index.mjs';
import { createMemoryFs, createLegacyStringFs } from './core.mjs';

// 被静态扫描的实现文件（明确列举，不含本测试文件本身）。
const IMPLEMENTATION_FILES = [
  'core.mjs', 'leader-election.mjs', 'idempotency.mjs', 'resume.mjs',
  'kill-switch.mjs', 'cost-monitor.mjs', 'watchdog.mjs', 'supervisor.mjs', 'index.mjs',
];

function srcOf(name) {
  return readFileSync(new URL('./' + name, import.meta.url), 'utf8');
}

function ctx(overrides = {}) {
  const clock = overrides.clock || S.createMemoryClock(overrides.startMs ?? 0);
  const deps = S.makeDeps({ ...overrides, clock });
  return { deps, clock };
}

let PASS = 0, FAIL = 0;
const lines = [];
async function test(name, fn) {
  try { await fn(); PASS++; lines.push('PASS  ' + name); }
  catch (e) { FAIL++; lines.push('FAIL  ' + name + '\n        ' + (e && e.message ? e.message : String(e))); }
}

// ① Kill Switch 生效时 cycleImpl 调用 0 次，且不会自动清除
await test('① Kill Switch 生效时 cycleImpl 调用次数为 0，且不会自动清除', async () => {
  let calls = 0;
  const { deps, clock } = ctx({ cycleImpl: async () => { calls++; return { ok: true }; } });
  const sup = S.createSupervisor(deps);
  const ks = sup.subsystems.killSwitch;
  ks.arm('ops-token-1');
  assert.equal(ks.isHalted(), true);
  clock.advance(1e9);                       // 时钟推进远超任何可能超时
  const r1 = await sup.runCycle({ runId: 'run-A', cycle: 0 });
  const r2 = await sup.runCycle({ runId: 'run-A', cycle: 1 });
  assert.equal(r1.status, 'HALTED');
  assert.equal(r2.status, 'HALTED');
  assert.equal(r1.cycleExecuted, false);
  assert.equal(calls, 0, 'cycleImpl 调用次数必须为 0');
  assert.equal(ks.isHalted(), true, '不得自动清除');
  assert.equal(ks.clear('wrong-token').cleared, false, '错误 token 不得清除');
  assert.equal(ks.isHalted(), true);
  assert.throws(() => ks.arm(undefined), /TOKEN/);
  assert.equal(ks.clear('ops-token-1').cleared, true);
  assert.equal(ks.isHalted(), false);
});

// ② 重复 (runId,cycle) 被拒绝、cycleImpl 只执行一次
await test('② 重复 (runId,cycle) 被拒绝、cycleImpl 只执行一次', async () => {
  let calls = 0;
  const { deps } = ctx({ cycleImpl: async () => { calls++; return { ok: true, dataLabel: 'FIXTURE_SYNTHETIC' }; } });
  const sup = S.createSupervisor(deps);
  const r1 = await sup.runCycle({ runId: 'run-B', cycle: 7 });
  const r2 = await sup.runCycle({ runId: 'run-B', cycle: 7 });
  assert.equal(r1.status, 'COMPLETED');
  assert.equal(r2.status, 'DUPLICATE');
  assert.equal(r2.cycleExecuted, false);
  assert.equal(calls, 1, 'cycleImpl 只能执行一次');
  const r3 = await sup.runCycle({ runId: 'run-B', cycle: 8 });
  assert.equal(r3.status, 'COMPLETED');
  assert.equal(calls, 2);
  assert.throws(() => sup.subsystems.idempotency.recordCompletion('run-B', 7, {}), /DUPLICATE/);

  // 早退路径必须一条记录都不写。entries() 每次全量重读账本，一个不前进的调用方
  // 若能靠重复提交把账本撑大，就是可用性上的自我放大。
  const rawBefore = deps.fsImpl.readFileSync(sup.subsystems.idempotency.ledgerPath, 'utf8');
  await sup.runCycle({ runId: 'run-B', cycle: 7 });      // duplicate
  await sup.runCycle({ runId: 'run-B', cycle: 8 });      // duplicate
  const rawAfter = deps.fsImpl.readFileSync(sup.subsystems.idempotency.ledgerPath, 'utf8');
  assert.equal(rawAfter, rawBefore, '重复提交不得增长账本');
});

// ③ 重启后从 lastCycle 正确续接
await test('③ 重启后从 lastCycle 正确续接', async () => {
  const fsImpl = S.createMemoryFs();        // 共享持久层 = 重启后仍在
  const c1 = S.createMemoryClock(0);
  const sup1 = S.createSupervisor(S.makeDeps({ fsImpl, clock: c1 }));
  await sup1.runCycle({ runId: 'run-C', cycle: 0 });
  await sup1.runCycle({ runId: 'run-C', cycle: 1 });
  await sup1.runCycle({ runId: 'run-C', cycle: 2 });
  const info = sup1.resume('run-C');
  assert.equal(info.lastCycle, 2);
  assert.equal(info.nextCycle, 3);
  const c2 = S.createMemoryClock(1000);
  let calls = 0;
  const sup2 = S.createSupervisor(S.makeDeps({
    fsImpl, clock: c2,
    cycleImpl: async () => { calls++; return { ok: true, dataLabel: 'FIXTURE_SYNTHETIC' }; },
  }));
  assert.equal(sup2.resume('run-C').lastCycle, 2);
  const r = await sup2.runNextCycle({ runId: 'run-C' });
  assert.equal(r.status, 'COMPLETED');
  assert.equal(r.cycle, 3, '必须从 lastCycle+1 续接');
  assert.equal(calls, 1);
  assert.equal(sup2.resume('run-C').lastCycle, 3);
});

// ④ 账本内容被篡改 → throw，不返回任何 lastCycle
await test('④ 账本内容被篡改 / 断链 → throw，不返回任何 lastCycle', async () => {
  const { deps } = ctx();
  const sup = S.createSupervisor(deps);
  await sup.runCycle({ runId: 'run-D', cycle: 0 });
  await sup.runCycle({ runId: 'run-D', cycle: 1 });
  const ledgerPath = sup.subsystems.idempotency.ledgerPath;
  const raw = deps.fsImpl.readFileSync(ledgerPath, 'utf8');
  const rows = raw.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
  rows[0].cycle = 999;                     // 内容篡改，但不重算 _hash
  deps.fsImpl.writeFileSync(ledgerPath, rows.map((r) => JSON.stringify(r)).join('\n') + '\n');
  let threw = false, leaked = null;
  try { leaked = sup.resume('run-D'); } catch (_e) { threw = true; }
  assert.equal(threw, true, '篡改必须 throw');
  assert.equal(leaked, null, '不得返回任何 lastCycle');
  let calls = 0;
  const sup2 = S.createSupervisor(S.makeDeps({
    fsImpl: deps.fsImpl,
    cycleImpl: async () => { calls++; return { ok: true }; },
  }));
  await assert.rejects(() => sup2.runNextCycle({ runId: 'run-D' }), /CHAIN/);
  assert.equal(calls, 0, '链损坏时不得执行周期');

  // 断链：删除中间一行 → 后续 _prev 对不上
  const { deps: d2 } = ctx();
  const s2 = S.createSupervisor(d2);
  await s2.runCycle({ runId: 'run-D2', cycle: 0 });
  await s2.runCycle({ runId: 'run-D2', cycle: 1 });
  await s2.runCycle({ runId: 'run-D2', cycle: 2 });
  const lp2 = s2.subsystems.idempotency.ledgerPath;
  const rows2 = d2.fsImpl.readFileSync(lp2, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
  rows2.splice(1, 1);
  d2.fsImpl.writeFileSync(lp2, rows2.map((r) => JSON.stringify(r)).join('\n') + '\n');
  assert.throws(() => s2.resume('run-D2'), /CHAIN/);
});

// ⑤ 租约未过期时不得接管、过期后可接管
await test('⑤ 租约未过期时不得接管、过期后可接管', async () => {
  const clock = S.createMemoryClock(0);
  const { deps } = ctx({ clock });
  const el = S.createLeaderElection(deps);
  const a = el.acquireLease({ ownerToken: 'owner-A', leaseTtlMs: 1000 });
  assert.equal(a.acquired, true);
  assert.equal(a.leaseExpiryMs, 1000);
  clock.set(500);
  const b1 = el.acquireLease({ ownerToken: 'owner-B', leaseTtlMs: 1000 });
  assert.equal(b1.acquired, false);
  assert.equal(b1.reason, 'LEASE_HELD_NOT_EXPIRED');
  clock.set(1000);                          // 恰好等于 expiry：仍不得接管（严格 >）
  const b2 = el.acquireLease({ ownerToken: 'owner-B', leaseTtlMs: 1000 });
  assert.equal(b2.acquired, false);
  clock.set(1001);
  const b3 = el.acquireLease({ ownerToken: 'owner-B', leaseTtlMs: 1000 });
  assert.equal(b3.acquired, true);
  assert.equal(b3.tookOverExpired, true);
  assert.equal(el.inspectLease().ownerToken, 'owner-B');
});

// ⑥ 非持有者释放锁不删除他人锁
await test('⑥ 非持有者释放锁不删除他人锁', async () => {
  const { deps } = ctx();
  const el = S.createLeaderElection(deps);
  assert.equal(el.acquireLease({ ownerToken: 'owner-A', leaseTtlMs: 5000 }).acquired, true);
  const before = el.inspectLease();
  const bad = el.releaseLease('intruder-token');
  assert.equal(bad.released, false);
  assert.equal(bad.reason, 'OWNER_MISMATCH');
  assert.equal(deps.fsImpl.existsSync(el.lockPath), true, '他人锁不得被删除');
  assert.deepEqual(el.inspectLease(), before, '锁内容不得改变');
  assert.equal(el.releaseLease('owner-A').released, true);
  assert.equal(deps.fsImpl.existsSync(el.lockPath), false);
});

// ⑦ 未实测成本 null + NOT_MEASURED，断言无 0 / 无回填 / 无合并
await test('⑦ 未实测成本 amount===null 且 note 含 NOT_MEASURED；无 0 / 无回填 / 无合并', async () => {
  const { deps } = ctx();
  const m = S.createCostMonitor(deps, { budgetLimit: 1000 });
  const snap = m.record({ budget: 100, estimated: 90 });   // 不提供 actual
  assert.equal(snap.actual.amount, null);
  assert.equal(snap.actual.status, 'NOT_MEASURED');
  assert.ok(String(snap.actual.note).includes('NOT_MEASURED'));
  assert.ok(String(snap.actual.note).includes('UNKNOWN, never 0'));
  for (const v of [snap.budget.amount, snap.estimated.amount, snap.actual.amount, snap.budgetLimit]) {
    assert.notEqual(v, 0, '不得出现 0');
  }
  assert.notEqual(snap.actual.amount, snap.budget.amount, '不得回填预算值');
  assert.notEqual(snap.actual.amount, snap.estimated.amount, '不得回填估算值');
  assert.equal(snap.actual.amount, null);
  assert.deepEqual(snap.columns, ['budget', 'estimated', 'actual']);
  assert.equal(snap.budget.amount, 100);
  assert.equal(snap.estimated.amount, 90);
  assert.equal(snap.merged, false);
  assert.equal(m.assertNoMerge(snap), true, '合规对象必须通过');
  assert.throws(() => m.assertNoMerge({ cost: 5, budget: snap.budget, estimated: snap.estimated, actual: snap.actual }), /COST|MERGE/);
  assert.throws(() => m.assertNoMerge({ ...snap, actual: { ...snap.actual, amount: snap.budget.amount } }), /COST|MERGE/);
  assert.throws(() => m.assertNoMerge({ ...snap, actual: { ...snap.actual, amount: 0 } }), /COST|MERGE|0/);
});

// ⑧ 0 样本 → NO_DATA 非 0
await test('⑧ 0 样本 → NO_DATA 而非 0', async () => {
  const { deps } = ctx();
  const m = S.createCostMonitor(deps);
  const snap = m.snapshot();
  assert.equal(snap.status, 'NO_DATA');
  assert.equal(snap.sampleCount, 0);
  for (const key of ['budget', 'estimated', 'actual']) {
    assert.equal(snap[key].status, 'NO_DATA');
    assert.equal(snap[key].amount, null, key + ' 缺失值不得写 0');
    assert.notEqual(snap[key].amount, 0);
  }
  assert.ok(String(snap.actual.note).includes('UNKNOWN, never 0'));
  assert.equal(m.assertNoMerge(snap), true);
});

// ⑨ 心跳陈旧检测
await test('⑨ 心跳陈旧检测（注入时钟，不创建 / 终止进程）', async () => {
  const clock = S.createMemoryClock(0);
  const { deps } = ctx({ clock, staleThresholdMs: 100 });
  const w = S.createWatchdog(deps);
  assert.equal(w.check().status, 'STALE');
  assert.equal(w.check().reason, 'NO_HEARTBEAT');
  w.beat({ runId: 'r', cycle: 0 });
  assert.equal(w.check().status, 'OK');
  clock.advance(50);
  assert.equal(w.check().status, 'OK');
  clock.advance(51);                       // age = 101 > 100
  const stale = w.check();
  assert.equal(stale.status, 'STALE');
  assert.equal(stale.reason, 'HEARTBEAT_AGE_EXCEEDED');
  w.beat();                                // 显式心跳恢复，而非自动
  assert.equal(w.check().status, 'OK');
});

// ⑩ 源码静态扫描 + DAEMON_BINDING
await test('⑩ 源码无 daemon/进程/网络调用，且 DAEMON_BINDING===DECLARED_NOT_WIRED', async () => {
  const all = IMPLEMENTATION_FILES.map(srcOf).join('\n');
  const banned = [
    { name: 'setInterval', re: /\bsetInterval\s*\(/ },
    { name: 'spawn/exec/child_process', re: /\bspawn(Sync)?\s*\(|\bexec(Sync)?\s*\(|child_process/ },
    { name: 'process kill', re: /process\.kill|\.kill\s*\(|\bkill\s*\(/ },
    { name: 'fetch(', re: /\bfetch\s*\(/ },
    { name: 'node:http', re: /node:http/ },
    { name: 'real URL', re: /https?:\/\// },
  ];
  for (const b of banned) {
    assert.equal(b.re.test(all), false, '源码不得包含禁用调用: ' + b.name);
  }
  assert.equal(S.DAEMON_BINDING, 'DECLARED_NOT_WIRED');
  assert.ok(all.includes('DECLARED_NOT_WIRED'));
  // The stage LIST is asserted here; the stage ORDER is asserted in ⑲e from the
  // only place where it means something — inside the side effect, reading the
  // disk. `STAGES.indexOf('cycleIntent') < STAGES.indexOf('cycleImpl')` would be
  // a declaration compared against a declaration: it can only fail if someone
  // edits the same array twice, so it proves nothing about behaviour.
  assert.deepEqual(S.STAGES, [
    'killSwitch', 'lease', 'idempotency', 'resume', 'leaseEpoch',
    'cycleIntent', 'cycleImpl', 'record', 'heartbeat',
  ]);
});

// ⑪ fixture 数据不得标 LIVE / REAL，缺失值不得写 0
await test('⑪ fixture 数据不得标 LIVE / REAL，缺失值不得写 0', async () => {
  const { deps } = ctx();
  const out = await deps.cycleImpl({ runId: 'r', cycle: 0, nowMs: deps.nowMs() });
  assert.notEqual(out.dataLabel, 'LIVE');
  assert.notEqual(out.dataLabel, 'REAL');
  assert.equal(out.dataLabel, 'FIXTURE_SYNTHETIC');
  for (const v of Object.values(out)) assert.notEqual(v, 0);
  const sup = S.createSupervisor(deps);
  const r = await sup.runCycle({ runId: 'run-E', cycle: 0 });
  assert.equal(r.status, 'COMPLETED');
  assert.deepEqual(r.trace, S.STAGES, '编排顺序必须固定');
  const rows = deps.fsImpl.readFileSync(sup.subsystems.idempotency.ledgerPath, 'utf8')
    .split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
  // One completed cycle now leaves three records, and the ORDER of the kinds on
  // disk is itself the evidence: epoch -> intent -> completion. Asserting the
  // kind sequence (not just the count) is what makes a reordering visible.
  assert.deepEqual(rows.map((x) => x.kind),
    [S.LEDGER_KIND.epoch, S.LEDGER_KIND.intent, S.LEDGER_KIND.completion]);
  const done = rows[rows.length - 1];
  assert.notEqual(done.payload.cycleResultLabel, 'LIVE');
  assert.notEqual(done.payload.cycleResultLabel, 'REAL');
});

// ⑫ 成本 state 跨重启累计（原子写）
await test('⑫ 成本 state 跨重启累计（原子写）', async () => {
  const fsImpl = S.createMemoryFs();
  const m1 = S.createCostMonitor(S.makeDeps({ fsImpl }));
  m1.record({ budget: 10, estimated: 8, actual: 7 });
  m1.record({ budget: 10, estimated: 8 });            // 未实测
  const m2 = S.createCostMonitor(S.makeDeps({ fsImpl }));   // 重启：新建实例共享 fsImpl
  const s = m2.snapshot();
  assert.equal(s.sampleCount, 2);
  assert.equal(s.budget.amount, 20);
  assert.equal(s.estimated.amount, 16);
  assert.equal(s.actual.amount, 7);
  assert.equal(s.actual.status, 'MEASURED');
  assert.equal(m2.assertNoMerge(s), true);
});

// ⑬ 空账本 resume 返回 lastCycle=-1（缺失不写 0）
await test('⑬ 空账本 resume 返回 lastCycle=-1（缺失不写 0）', async () => {
  const { deps } = ctx();
  const sup = S.createSupervisor(deps);
  const info = sup.resume('run-empty');
  assert.equal(info.lastCycle, -1);
  assert.equal(info.nextCycle, 0);
  assert.equal(info.verified, true);
  assert.equal(info.recordCount, 0);
});

// ⑭ 原生链工具：append→verify 通过，篡改 _hash / 断链均 throw
await test('⑭ 原生链工具：append→verify 通过，篡改 / 断链均 throw', async () => {
  const { deps } = ctx();
  const p = '/x/ledger.jsonl';
  const e1 = S.appendChainEntry(deps.fsImpl, p, { a: 1 });
  const e2 = S.appendChainEntry(deps.fsImpl, p, { a: 2 });
  assert.equal(e2._prev, e1._hash);
  assert.equal(S.verifyChain(S.readJsonl(deps.fsImpl, p)).intact, true);
  const rows = S.readJsonl(deps.fsImpl, p);
  const bad1 = rows.map((r) => ({ ...r }));
  bad1[0]._hash = 'deadbeef';
  assert.throws(() => S.verifyChain(bad1), /CHAIN/);
  const bad2 = rows.map((r) => ({ ...r }));
  bad2[1]._prev = 'GENESIS';
  assert.throws(() => S.verifyChain(bad2), /CHAIN/);
});

// ⑮ 非数组入参必须抛 CHAIN_INPUT_NOT_ARRAY（否则 readJsonl 改签名后漏改的调用点
//    会让 0 < undefined 为假 ⇒ 循环 0 次 ⇒ 返回 {intact:true,count:undefined} 静默通过）
await test('⑮ verifyChain 非数组入参一律 throw（防静默通过）', async () => {
  const { deps } = ctx();
  const p = '/x/ledger-guard.jsonl';
  S.appendChainEntry(deps.fsImpl, p, { a: 1 });
  const rows = S.readJsonl(deps.fsImpl, p);
  assert.equal(S.verifyChain(rows).intact, true, '数组入参必须通过');

  // 包裹对象：正是 readJsonl 改签名后漏改的调用点会传的形态
  assert.throws(
    () => S.verifyChain({ entries: rows, tailTorn: false, tornBytes: 0 }),
    /CHAIN_INPUT_NOT_ARRAY/,
  );
  assert.throws(() => S.verifyChain(null), /CHAIN_INPUT_NOT_ARRAY/);
  assert.throws(() => S.verifyChain(undefined), /CHAIN_INPUT_NOT_ARRAY/);
  assert.throws(() => S.verifyChain('[]'), /CHAIN_INPUT_NOT_ARRAY/);
});

// ⑯ 内存 fs 必须尊重 open 模式（㉛）。旧实现的 writeSync 无视 flag 整文件覆盖，
//    于是 openSync(p,'a')+writeSync+fsyncSync 在 fixture 里把账本截成一条，
//    而真实磁盘是追写 —— 同一份代码在两边语义分叉，耐久性用例会假绿。
//    实测旧行为：两条历史条目被换成 "APPENDED\n"，readJsonl 抛 LEDGER_PARSE_FAILED；
//    若覆盖内容恰为合法 entry，verifyChain 仍返回 {intact:true,count:1}（丢了 2 条还是 intact）。
await test('⑯ 内存 fs 尊重 open 模式：a 追写 / w 截断 / fsync 可计数', async () => {
  const { deps } = ctx();
  const p = '/x/ledger-mode.jsonl';
  S.appendChainEntry(deps.fsImpl, p, { a: 1 });
  S.appendChainEntry(deps.fsImpl, p, { a: 2 });
  const before = S.readJsonl(deps.fsImpl, p).length;
  assert.equal(before, 2, '前置：两条历史条目');

  // 'a' 必须追写：前序条目一条都不能少
  const fdA = deps.fsImpl.openSync(p, 'a');
  deps.fsImpl.writeSync(fdA, '');
  deps.fsImpl.fsyncSync(fdA);
  deps.fsImpl.closeSync(fdA);
  assert.equal(S.readJsonl(deps.fsImpl, p).length, 2, 'a 模式不得丢失前序条目');
  assert.equal(S.verifyChain(S.readJsonl(deps.fsImpl, p)).intact, true, 'a 模式后链仍完整');

  // fsync 必须可被断言（"没抛错"不算通过）
  const fsyncAfter = deps.fsImpl.fsyncCallCount();
  assert.ok(fsyncAfter >= 1, 'fsync 必须被实际调用并可计数，实际 ' + fsyncAfter);

  // 'w' 必须仍然截断 —— 否则"尊重模式"就无从谈起（两种都追写也是错的）
  const fdW = deps.fsImpl.openSync(p, 'w');
  deps.fsImpl.writeSync(fdW, 'X');
  deps.fsImpl.closeSync(fdW);
  assert.equal(deps.fsImpl.readFileSync(p, 'utf8'), 'X', 'w 模式必须截断');

  // 同一 fd 连续两次写 = 两次接力（真实 fs.writeSync 按偏移推进），不是覆盖
  const fdW2 = deps.fsImpl.openSync(p, 'w');
  deps.fsImpl.writeSync(fdW2, '{"seq":1}\n');
  deps.fsImpl.writeSync(fdW2, '{"seq":2}\n');
  deps.fsImpl.closeSync(fdW2);
  assert.equal(deps.fsImpl.readFileSync(p, 'utf8'), '{"seq":1}\n{"seq":2}\n',
    '两次 writeSync 必须接力，后一次不得抹掉前一次');
});

// ⑰ 永久负对照（位置模型）：旧 string fixture 两次 writeSync 只剩最后一条
await test('⑰ 负对照：旧 string fixture 丢失接力写（本 fixture 不得如此）', async () => {
  const legacy = createLegacyStringFs();
  const real = deps_real();
  const seq = ['{"seq":1}\n', '{"seq":2}\n'];
  for (const fs of [legacy, real]) {
    const fd = fs.openSync('/x/pos.jsonl', 'w');
    for (const s of seq) fs.writeSync(fd, s);
    fs.closeSync(fd);
  }
  const legacyText = legacy.readFileSync('/x/pos.jsonl');
  const realText = real.readFileSync('/x/pos.jsonl', 'utf8');
  assert.equal(legacyText, '{"seq":2}\n', '旧模型确实只剩最后一条（负对照成立）');
  assert.equal(realText, '{"seq":1}\n{"seq":2}\n', '本 fixture 必须两条都在');
  assert.notEqual(legacyText, realText, '两者必须可分，否则负对照没有牙齿');
});

// ⑱ 永久负对照（字节模型）：旧 string fixture 表示不出"撕裂在中途的半字节"
await test('⑱ 负对照：旧 string fixture 把半字节解成 U+FFFD（本 fixture 不得如此）', async () => {
  const legacy = createLegacyStringFs();
  const real = deps_real();
  const full = Buffer.from('中', 'utf8');          // e4 b8 ad
  assert.equal(full.length, 3);
  const head = full.subarray(0, 1);                 // e4
  for (const fs of [legacy, real]) {
    const fd = fs.openSync('/x/torn.jsonl', 'w');
    fs.writeSync(fd, head);
    fs.closeSync(fd);
  }
  const legacyBytes = Buffer.from(legacy.readFileSync('/x/torn.jsonl'), 'utf8');
  const realBytes = real.readFileSync('/x/torn.jsonl');
  assert.equal(legacyBytes.toString('hex'), 'efbfbd', '旧模型确实产出 U+FFFD 三字节（负对照成立）');
  assert.equal(realBytes.toString('hex'), 'e4', '本 fixture 必须保住那一个字节');
  assert.equal(realBytes.length, 1, '字节数必须是 1，否则按字节切点全部算错');
});

// ⑲e 写入序的正面判据：观测点放在副作用内部。
//     这一批断言依赖的是「完整记录在盘上的顺序」，不依赖字节保真 —— 撕裂点/半字节
//     类用例（⑲c/⑲d）需要 fixture 存 Buffer，本批不需要：一条完整 JSONL 记录要么
//     整行在盘上、要么整行不在，与它是怎么编码的无关。
//     D-14-0 的失效形态是「整条记录缺失」，所以判据也只能是「整条记录在不在」。
//     反面（恒真式）写法是 STAGES.indexOf('cycleIntent') < STAGES.indexOf('cycleImpl')，
//     那是拿声明比声明；这里的判据是拿磁盘比行为。
await test('⑲e 写入序：副作用发生时 cycle_intent 已在盘上且已 fsync（D-14-0 正面判据）', async () => {
  const fsImpl = S.createMemoryFs();
  const deps = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
  const sup = S.createSupervisor(deps);
  const ledgerPath = sup.subsystems.idempotency.ledgerPath;
  let lastKindAtEffect = null;
  let completionAtEffect = null;
  let fsyncAtEffect = -1;

  deps.cycleImpl = async () => {
    // 观测点在副作用内部：这里能看到什么，崩溃时盘上就留下什么。
    const rows = S.readJsonl(fsImpl, ledgerPath);
    lastKindAtEffect = rows.length ? rows[rows.length - 1].kind : null;
    completionAtEffect = rows.some((e) => e.kind === S.LEDGER_KIND.completion);
    fsyncAtEffect = fsImpl.fsyncCallCount();
    return { ok: true, dataLabel: 'FIXTURE_SYNTHETIC' };
  };

  const r = await sup.runCycle({ runId: 'run-W1', cycle: 0 });
  assert.equal(r.status, 'COMPLETED');
  assert.equal(lastKindAtEffect, S.LEDGER_KIND.intent,
    '副作用开始时盘上最后一条必须是 cycle_intent，实际 ' + lastKindAtEffect);
  assert.equal(completionAtEffect, false, '副作用开始时不得已有 completion');
  assert.ok(fsyncAtEffect >= 2, 'epoch 与 intent 各一次 fsync 才允许进入副作用，实际 ' + fsyncAtEffect);
});

// ⑲f 崩溃窗口：intent 在、completion 不在 ⇒ 重启后判 IN_DOUBT，不重跑
await test('⑲f 崩溃在 intent 与 completion 之间：重启判 IN_DOUBT，绝不重跑周期体', async () => {
  const fsImpl = S.createMemoryFs();
  const sup1 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) }));
  const idem = sup1.subsystems.idempotency;
  // 构造"掉电现场"：只有 epoch + intent，没有 completion。恢复逻辑关心的是这个
  // 盘上状态本身，而不是它由什么机制产生 —— 这正是 recordCompletion 写下去之前
  // 进程被杀时留下的字节。
  idem.recordLeaseEpoch({ runId: 'run-W2', ownerToken: 'tok-crashed', acquiredAtMs: 0 });
  idem.recordIntent('run-W2', 0, { epoch: 1 });

  let calls = 0;
  const sup2 = S.createSupervisor(S.makeDeps({
    fsImpl, clock: S.createMemoryClock(5000),
    cycleImpl: async () => { calls++; return { ok: true }; },
  }));
  const info = sup2.resume('run-W2');
  assert.deepEqual(info.inDoubtCycles, [0], 'in-doubt 周期必须被点名，不能静默');
  assert.equal(info.blocked, true);
  assert.equal(info.nextCycle, 0, '不得把 in-doubt 的周期跳过去当成已完成');
  assert.equal(info.lastCycle, -1, 'IN_DOUBT 不是 completion：不写 0、也不冒充 1');

  const r = await sup2.runNextCycle({ runId: 'run-W2' });
  assert.equal(r.status, 'IN_DOUBT');
  assert.equal(r.cycleExecuted, false);
  assert.equal(r.sideEffectMayHaveOccurred, true,
    '必须明说副作用可能已发生；只报 cycleExecuted:false 是谎报');
  assert.equal(calls, 0, 'in-doubt 周期绝不允许重跑（at-most-once）');
  // 而且 in-doubt 的早退同样不得写任何记录：判据必须是"账本没变"，不是"我没看到新记录"。
  const after = S.readJsonl(fsImpl, sup2.subsystems.idempotency.ledgerPath);
  assert.equal(after.length, 2, 'in-doubt 早退不得追加记录（epoch+intent 共 2 条）');
  assert.equal(after.filter((e) => e.kind === S.LEDGER_KIND.epoch).length, 1,
    '早退路径不得声明新任期');
});

// ⑲g completion 必须有配对 intent，否则抛（防止上游绕过写入序）
await test('⑲g 无 intent 的 completion 一律抛，且别人的 intent 不算数', async () => {
  const { deps } = ctx();
  const sup = S.createSupervisor(deps);
  const idem = sup.subsystems.idempotency;
  assert.throws(() => idem.recordCompletion('run-W3', 0, {}), /COMPLETION_WITHOUT_INTENT/);
  // 相邻周期有 intent 也不行：配对是 (runId, cycle) 级别的，不是"账本里有 intent"
  idem.recordIntent('run-W3', 1, { epoch: 1 });
  assert.throws(() => idem.recordCompletion('run-W3', 0, {}), /COMPLETION_WITHOUT_INTENT/);
  assert.equal(idem.cycleState('run-W3', 0), 'ABSENT');
  assert.equal(idem.cycleState('run-W3', 1), 'IN_DOUBT');
});

// ⑲h epoch 是单调栅栏令牌，且 intent/completion 必须带上当时的 epoch
await test('⑲h lease_epoch 单调递增，intent 与 completion 携带同一 epoch', async () => {
  const { deps } = ctx();
  const sup = S.createSupervisor(deps);
  const r1 = await sup.runCycle({ runId: 'run-W4', cycle: 0 });
  const r2 = await sup.runCycle({ runId: 'run-W4', cycle: 1 });
  assert.equal(r1.epoch, 1);
  assert.equal(r2.epoch, 2);
  assert.ok(r2.epoch > r1.epoch, 'epoch 必须严格递增（否则栅栏令牌可被重放）');
  const rows = S.readJsonl(deps.fsImpl, sup.subsystems.idempotency.ledgerPath);
  assert.deepEqual(rows.filter((e) => e.kind === S.LEDGER_KIND.epoch).map((e) => e.epoch), [1, 2]);
  assert.deepEqual(rows.filter((e) => e.kind === S.LEDGER_KIND.intent).map((e) => e.epoch), [1, 2]);
  assert.deepEqual(rows.filter((e) => e.kind === S.LEDGER_KIND.completion).map((e) => e.epoch), [1, 2]);
});

// ⑲i 账本不是凭据库：租约持有者令牌只能以摘要形式落盘
await test('⑲i 租约令牌不得以明文进账本（只存摘要）', async () => {
  const token = 'owner-token-should-never-appear-9f3a';
  const fsImpl = S.createMemoryFs();
  const deps = S.makeDeps({ fsImpl, ownerToken: token });
  const sup = S.createSupervisor(deps);
  await sup.runCycle({ runId: 'run-W5', cycle: 0 });
  const raw = fsImpl.readFileSync(sup.subsystems.idempotency.ledgerPath, 'utf8');
  assert.equal(raw.includes(token), false, '账本里出现明文令牌（账本会被拷进证据包）');
  const ep = S.readJsonl(fsImpl, sup.subsystems.idempotency.ledgerPath)
    .filter((e) => e.kind === S.LEDGER_KIND.epoch);
  assert.equal(ep.length, 1);
  assert.equal(ep[0].ownerTokenDigest, S.sha256(token));
  assert.equal(ep[0].ownerToken, undefined);
});

function deps_real() { return createMemoryFs(); }

console.log('=== P6 Supervisor 本地骨架验收（纯内存 fixture / DECLARED_NOT_WIRED）===');
for (const l of lines) console.log(l);
console.log('RESULT PASS=' + PASS + ' FAIL=' + FAIL);
if (FAIL > 0) process.exitCode = 1;
