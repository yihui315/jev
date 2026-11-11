// supervisor.test.mjs — 手写断言；node <path> 必须 exit 0 才算通过
// 纯内存 fixture，DECLARED_NOT_WIRED：不启动守护进程、不触网、不下单、不碰真实资金。
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import * as S from './index.mjs';
import { createMemoryFs } from './core.mjs';

// 被静态扫描的实现文件 = 本目录下除测试文件以外的每一个模块。
//
// 这里原本是一份手写九人名单，而 ledger-io.mjs（331 行，账本字节的唯一所有者）
// 不在里面 —— 于是 `banned` 那段对本轮最新、最要紧的实现文件一行都没扫。
// 和「verify-engine-vendor 命中 0 个文件」「第 9 套件不存在」是同一族：
// 守卫的 SCOPE 靠手维护，就会悄悄停止覆盖它本该覆盖的东西。
// 枚举之后必须断言数量：否则删掉文件反而让扫描"更干净"。
const IMPLEMENTATION_FILES = readdirSync(new URL('./', import.meta.url))
  .filter((n) => n.endsWith('.mjs') && !/\.test\.mjs$/.test(n))
  .sort();
// Frozen by equality. Measured: 10 modules (core, leader-election, idempotency,
// resume, kill-switch, cost-monitor, watchdog, supervisor, index, ledger-io).
const IMPLEMENTATION_FILES_EXPECTED = [
  'core.mjs', 'cost-monitor.mjs', 'idempotency.mjs', 'index.mjs', 'kill-switch.mjs',
  'leader-election.mjs', 'ledger-io.mjs', 'resume.mjs', 'supervisor.mjs', 'watchdog.mjs',
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
  // 枚举本身必须先被断言：如果 readdirSync 指错了目录，上面那句 filter 会得到
  // 空数组，于是下面每一条 banned 都在"什么都没扫"的情况下全部通过。
  // EQUALITY, not `>=`. `>=` makes every newly added file exempt by default — which
  // is exactly how ledger-io.mjs (331 lines, the single owner of ledger bytes)
  // ended up outside the scan: the list was hand-maintained and nothing failed when
  // it fell behind. Adding a module must be a deliberate edit of this line.
  assert.deepEqual(IMPLEMENTATION_FILES, IMPLEMENTATION_FILES_EXPECTED,
    '实现文件集合变了：新增/删除模块必须在这里显式改一行（否则扫描范围会静默漂移），'
    + '实际 [' + IMPLEMENTATION_FILES.join(', ') + ']');
  assert.ok(IMPLEMENTATION_FILES.includes('ledger-io.mjs'),
    'ledger-io.mjs 必须被扫描覆盖：它拥有全部账本字节，是本轮最大的一块实现');
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

  // 同一 fd 两次写必须接力，这条主张不在这里断言：它的归属是 ⑰b。
  // 一条用例只证一件事（R-23）—— 放在这里会让"位置语义坏了"同时红两条，
  // 于是 ⑰b 永远拿不到独立定位证据。
});

// ─── 负对照的锚：逐字原文 + sha256 锁 ──────────────────────────────────────────
// 取自 d1fc94f（= dbd6368^，字节模型落地前的最后一个提交）。复现命令：
//   git show d1fc94f:apps/command_center_3d/scripts/supervisor/core.mjs \
//     | sed -n '/^export function createMemoryFs/,/^}$/p' | sha256sum
//   -> df6f2cf07e910bffb9d2558786c805bb0a5e59e3a3add3a9e44ddda517ef8094   (1992 字节 / 53 行)
// 为什么必须是原文：⑰a/⑱a 的全部杀伤力来自「旧模型确实是这样」。手打复述证明的
// 是作者记得什么，不是旧实现是什么 —— 实测那份复述缺 mkdirSync/renameSync，
// 且 unlinkSync 由抛 ENOENT 变成静默删除。
// 锁是 tamper-EVIDENT，不是 tamper-proof：改这份字面量会出现在 diff 里，连锁一起
// 改是一次两处编辑，评审看得见。
const LEGACY_FS_SOURCE_SHA256 = 'df6f2cf07e910bffb9d2558786c805bb0a5e59e3a3add3a9e44ddda517ef8094';
const LEGACY_FS_SOURCE = `export function createMemoryFs(initial) {
  const files = new Map();
  if (initial) for (const [k, v] of Object.entries(initial)) files.set(k, String(v));
  let nextFd = 1;
  const fds = new Map();
  const asText = (d) => (Buffer.isBuffer(d) ? d.toString('utf8') : String(d));

  return {
    existsSync(p) { return files.has(String(p)); },
    readFileSync(p) {
      const key = String(p);
      if (!files.has(key)) { const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e; }
      return files.get(key);
    },
    writeFileSync(p, data) { files.set(String(p), asText(data)); },
    appendFileSync(p, data) {
      const key = String(p);
      files.set(key, (files.get(key) || '') + asText(data));
    },
    unlinkSync(p) {
      const key = String(p);
      if (!files.has(key)) { const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e; }
      files.delete(key);
    },
    renameSync(a, b) {
      const ka = String(a), kb = String(b);
      if (!files.has(ka)) { const e = new Error('ENOENT: ' + ka); e.code = 'ENOENT'; throw e; }
      files.set(kb, files.get(ka));
      files.delete(ka);
    },
    // 'wx' 为原子排他创建：文件已存在则抛 EEXIST，用于租约选举。
    openSync(p, flags) {
      const key = String(p);
      const f = String(flags || 'r');
      if (f.includes('x') && files.has(key)) {
        const e = new Error('EEXIST: ' + key); e.code = 'EEXIST'; throw e;
      }
      if (f.includes('w') || f.includes('x')) {
        files.set(key, '');
      } else if (f.startsWith('a')) {
        if (!files.has(key)) files.set(key, '');
      } else if (f.startsWith('r') && !files.has(key)) {
        const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e;
      }
      const fd = nextFd++;
      fds.set(fd, key);
      return fd;
    },
    closeSync(fd) { fds.delete(fd); },
    writeSync(fd, data) { const key = fds.get(fd); if (key !== undefined) files.set(key, asText(data)); },
    mkdirSync() {},
  };
}
`;

function legacyStringFs() {
  assert.equal(S.sha256(LEGACY_FS_SOURCE), LEGACY_FS_SOURCE_SHA256,
    '负对照的字面量已经不是 d1fc94f 的原文了。改对照就是在调整答案 —— '
    + '若确实要换锚，先给出新提交的 git show 输出与新的 sha256sum');
  // `new Function` 不接受 `export`；去掉它不能改变封印，所以封印校验在原文上做。
  const factory = new Function(LEGACY_FS_SOURCE.replace(/^export /, '') + '\nreturn createMemoryFs;')();
  return factory();
}

// 这三条正是当年那份"手打复述"打错的地方。它们不是重复断言，是"这不是复述"的证明。
function assertLegacyIsVerbatim(fs) {
  assert.equal(typeof fs.mkdirSync, 'function', '原文有 mkdirSync；缺了说明这是复述而不是原文');
  assert.equal(typeof fs.renameSync, 'function', '原文有 renameSync；缺了说明这是复述而不是原文');
  assert.throws(() => fs.unlinkSync('/x/never-created'), /ENOENT/,
    '原文的 unlinkSync 对不存在的路径抛 ENOENT；静默删除 = 复述');
}

// ⑰a/⑰b 与 ⑱a/⑱b —— 拆开的原因不是洁癖，是归因。
// 原来 ⑰ 一条里装了三件互相独立的主张：legacy 必须错 / real 必须对 / 两者不等。
// 后果实测：把 real 侧的位置语义改坏，⑰ 也红，而失败消息说的是"旧 string fixture
// 丢失接力写"——把排查指向了没坏的那一半。红的颜色对、红的理由错。
// 现在：a 侧 = 负对照（错的模型必须错），b 侧 = 正向判据（对的实现必须对）。
// 两者各自都能被一条只红自己的变异打死。
await test('⑰a 负对照 legacy 侧：旧 string fixture 必须丢失接力写', async () => {
  const legacy = legacyStringFs();
  assertLegacyIsVerbatim(legacy);
  const fd = legacy.openSync('/x/pos-legacy.jsonl', 'w');
  legacy.writeSync(fd, '{"seq":1}\n');
  legacy.writeSync(fd, '{"seq":2}\n');
  legacy.closeSync(fd);
  assert.equal(legacy.readFileSync('/x/pos-legacy.jsonl'), '{"seq":2}\n',
    '旧 string 模型确实只剩最后一条（负对照成立）');
});

await test('⑰b 正向判据 real 侧：本 fixture 必须保住接力写', async () => {
  // deps_real() 就是 deps.fsImpl 的默认实现；直接用被测对象，不引第三方副本
  const real = deps_real();
  const fd = real.openSync('/x/pos.jsonl', 'w');
  real.writeSync(fd, '{"seq":1}\n');
  real.writeSync(fd, '{"seq":2}\n');
  real.closeSync(fd);
  assert.equal(real.readFileSync('/x/pos.jsonl', 'utf8'), '{"seq":1}\n{"seq":2}\n',
    '两次 writeSync 必须接力，后一次不得抹掉前一次');
});

await test('⑱a 负对照 legacy 侧：旧 string fixture 必须把半字节解成 U+FFFD', async () => {
  const legacy = legacyStringFs();
  assertLegacyIsVerbatim(legacy);
  const head = Buffer.from('中', 'utf8').subarray(0, 1);      // e4
  const fd = legacy.openSync('/x/torn-legacy.jsonl', 'w');
  legacy.writeSync(fd, head);
  legacy.closeSync(fd);
  const bytes = Buffer.from(legacy.readFileSync('/x/torn-legacy.jsonl'), 'utf8');
  assert.equal(bytes.toString('hex'), 'efbfbd', '旧模型确实产出 U+FFFD 三字节（负对照成立）');
  assert.equal(bytes.length, 3, 'U+FFFD 是 3 字节，这正是它不能代表半字节的原因');
});

await test('⑱b 正向判据 real 侧：本 fixture 必须逐字节保真', async () => {
  const real = deps_real();
  const full = Buffer.from('中', 'utf8');                     // e4 b8 ad
  assert.equal(full.length, 3, '前置：`中` 是三字节，否则本用例在测别的东西');
  const head = full.subarray(0, 1);                           // e4
  const fd = real.openSync('/x/torn.jsonl', 'w');
  real.writeSync(fd, head);
  real.closeSync(fd);
  const bytes = real.readFileSync('/x/torn.jsonl');
  assert.equal(bytes.toString('hex'), 'e4', '本 fixture 必须保住那一个字节');
  assert.equal(bytes.length, 1, '字节数必须是 1，否则按字节切点全部算错');
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
  // STAGES 里没有 in-doubt 这个标签，所以早退在 trace 上"看不见原因"。这条断言把
  // "看不见原因但看得见停在哪"钉死：trace 恰好停在只读段，一个写阶段都没进。
  // 归因方式是 3 段前缀（返回值），不是读日志文本（C32）。
  assert.deepEqual(r.trace, ['killSwitch', 'lease', 'idempotency'],
    'in-doubt 必须在任何写阶段之前早退');
  // 而且 in-doubt 的早退同样不得写任何记录：判据必须是"账本没变"，不是"我没看到新记录"。
  const after = S.readJsonl(fsImpl, sup2.subsystems.idempotency.ledgerPath);
  assert.equal(after.length, 2, 'in-doubt 早退不得追加记录（epoch+intent 共 2 条）');
  assert.equal(after.filter((e) => e.kind === S.LEDGER_KIND.epoch).length, 1,
    '早退路径不得声明新任期');

  // ── 宣称是"任何写阶段之前"，就必须覆盖"任何写" ────────────────────────────
  // 复现过的绕过：在早退分支、return 之前插一条 watchdog.beat()（写心跳、不 push
  // 标签、不进任何 STAGES 写阶段）。上面的判据全绿：账本 2 条没变、trace 3 段没变。
  // 「判据比宣称窄一档，而窄掉的那一档恰好是利用入口」——所以判据换成
  // 路径 × 字节 全量快照，那是对 writeFileSync/renameSync 这条路的唯一覆盖方式。
  //
  // 不要用 writeCallCount() 兜底：atomicWriteJson 走 writeFileSync + renameSync，
  // 不经过 openSync/writeSync，所以那个计数器对这种写永远 delta=0。用不覆盖目标
  // 路径的仪表兜底，等于给漏洞发合格证。这条是实测的，不是推理的。
  const BASE = S.BASE_DIR;
  const before2 = snapshotDir(fsImpl, BASE);
  assert.ok(Object.keys(before2).length >= 2,
    '前置：快照必须真的看到文件（空快照 = 这条用例在测空气），实际 '
    + JSON.stringify(Object.keys(before2)));
  const r2 = await sup2.runNextCycle({ runId: 'run-W2' });
  const after2 = snapshotDir(fsImpl, BASE);
  assert.equal(r2.status, 'IN_DOUBT');

  // 白名单必须是空数组。in-doubt 状态下进程不得被看成"活着"：
  //   * 若它继续心跳 => watchdog 报 OK、租约被续 => 没人接管，集群带着绿灯停摆；
  //   * 若它不心跳   => 租约过期、别人接管、接管者读到 IN_DOUBT 也停 => 整个集群
  //     停在同一个 in-doubt 上。
  // 后者是 IN_DOUBT 无出口的必然形态，不是活锁：没有进展正是设计，而且它是响的
  // （runNextCycle 返回 status=IN_DOUBT + sideEffectMayHaveOccurred）。别让后人
  // 把它当 bug "修"成心跳保活。
  // 要放宽，先改这段注释，再改下一行的长度断言 —— 它钉死"最多一项"。
  const IN_DOUBT_EXIT_WHITELIST = [];
  assert.equal(IN_DOUBT_EXIT_WHITELIST.length, 0, '白名单最多一项；两项以上就退化成"随便写"');
  const allowed = (rel) => IN_DOUBT_EXIT_WHITELIST.some((w) => rel.endsWith('/' + w));

  assert.deepEqual(Object.keys(after2).sort(), Object.keys(before2).sort(),
    'IN_DOUBT 早退不得新建或删除任何文件');
  const changed = Object.keys(before2).filter((k) => before2[k] !== after2[k] && !allowed(k));
  assert.deepEqual(changed, [],
    'IN_DOUBT 早退路径上不得有任何已知文件字节发生变化，实际变化: ' + JSON.stringify(changed));
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

// ⑲c 撕裂尾巴：完整 JSON 但缺结尾换行，也必须走截断路径并报 discarded_bytes。
//     这条不是"顺手兼容一下换行"：修复前那种文件在 verifyChain 处直接抛断链，
//     整个账本报废，恢复只能靠人工删文件——而那等于把"记录缺失"变成"没人看见"。
await test('⑲c 结尾缺换行：完整 JSON 也按撕裂尾巴截断，并报 discarded_bytes', async () => {
  const fsImpl = S.createMemoryFs();
  const clock = S.createMemoryClock(0);
  const sup = S.createSupervisor(S.makeDeps({ fsImpl, clock }));
  await sup.runCycle({ runId: 'run-T', cycle: 0 });
  const p = sup.subsystems.ledgerIo.ledgerPath;
  const good = fsImpl.readFileSync(p);
  const goodLen = good.length;

  // 追加一条"完整 JSON、只是没写结尾换行"的记录（_prev 是假的：它确实是一次
  // 没写完的写，不该被当成正常记录）
  const extra = Buffer.from(JSON.stringify({
    kind: 'cycle_completion', runId: 'run-T', cycle: 1, _prev: 'not-the-head', _hash: 'nope',
  }), 'utf8');
  fsImpl.writeFileSync(p, Buffer.concat([good, extra]));

  // 前置：修复前这条路是死的 —— 链校验直接抛，账本不可读
  assert.throws(() => S.verifyChain(S.readJsonl(fsImpl, p)), /CHAIN/,
    '前置：未截断时链必须校验失败（否则本用例在测空气）');

  const sup2 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(1000) }));
  const info = sup2.resume('run-T');            // open() 在这里发生
  const rec = sup2.subsystems.ledgerIo.recovery();
  assert.equal(rec.mode, S.RECOVER.TORN_TAIL_TRUNCATED);
  assert.equal(rec.discardedBytes, extra.length, 'discarded_bytes 必须精确等于被丢掉的字节数');
  assert.equal(fsImpl.readFileSync(p).length, goodLen, '文件必须回到最后一条完整记录处');
  assert.equal(info.lastCycle, 0, '截断后仍可续跑：cycle 0 是完成的');
  assert.equal(info.verified, true);
});

// ⑲d 尾巴完整但 _prev 接不上：这是"外来记录"，不是"没写完的记录"。
//     区别必须体现在行为上：撕裂可以截断后继续；接不上只能 fail-closed，
//     而且不得静默丢弃证据（字节要原样留着给人查）。
await test('⑲d 尾巴 _prev 接不上：LEDGER_TAMPERED，且不得静默截断掉证据', async () => {
  const fsImpl = S.createMemoryFs();
  const clock = S.createMemoryClock(0);
  const sup = S.createSupervisor(S.makeDeps({ fsImpl, clock }));
  await sup.runCycle({ runId: 'run-U', cycle: 0 });
  const p = sup.subsystems.ledgerIo.ledgerPath;
  const good = fsImpl.readFileSync(p);

  // 完整记录 + 结尾换行 + _prev 指向一个不存在的头
  const foreign = Buffer.from(JSON.stringify({
    kind: 'cycle_completion', runId: 'run-U', cycle: 1, _prev: 'deadbeef', _hash: 'cafe',
  }) + '\n', 'utf8');
  fsImpl.writeFileSync(p, Buffer.concat([good, foreign]));
  const tamperedLen = good.length + foreign.length;

  const sup2 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(1000) }));
  assert.throws(() => sup2.resume('run-U'), /CHAIN_BROKEN|LEDGER_TAMPERED/);
  assert.equal(fsImpl.readFileSync(p).length, tamperedLen,
    '接不上不等于没写完：不得把它截断掉（截断=销毁证据）');
  await assert.rejects(() => sup2.runNextCycle({ runId: 'run-U' }), /CHAIN_BROKEN|LEDGER_TAMPERED/,
    '恢复路径也不得放行');
});

// ⑳ 同长度原地篡改不改变 byteLength，所以游标的作废条件必须多一个维度：mtimeMs。
//    契约是「**下一次读取**被抓到」，不是「K 次读取内」——后者属于 ⑳b（伪造 mtime 的攻击者）。
//    这条同时是 mtime 边界的证据：①能改文件的人也能改 mtime（utimes），所以 mtime 只是
//    检测辅助，不是安全边界；②真正的边界是「取租约必全量重验」+「每 K 条 / M 字节强制重验」。
await test('⑳ 同长度原地篡改必须在**下一次读取**被抓到（mtime 变 ⇒ 作废游标）', async () => {
  const build = async (runId) => {
    const fsImpl = S.createMemoryFs();
    const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
    const io = S.createLedgerIo(base);
    const sup = S.createSupervisor({ ...base, ledgerIo: io });
    for (let c = 0; c < 3; c++) await sup.runCycle({ runId, cycle: c });
    const p = io.ledgerPath;
    const raw = fsImpl.readFileSync(p);
    const mtimeMs = fsImpl.statSync(p).mtimeMs;
    const at = raw.indexOf(Buffer.from(runId, 'utf8'));
    assert.ok(at >= 0, '前置：必须能在盘上找到待篡改的 runId');
    const mangled = Buffer.from(raw);
    mangled.write(runId.slice(0, -1) + 'Z', at, 'utf8');
    fsImpl.writeFileSync(p, mangled);
    assert.equal(fsImpl.readFileSync(p).length, raw.length,
      '前置：篡改必须保持字节数不变（否则抓到它的是长度比对，与本用例无关）');
    return { fsImpl, io, p, mtimeMs };
  };

  // 主臂：mtime 变了 ⇒ 下一次读取就作废游标 ⇒ 全量重验 ⇒ 链断必抛
  const a = await build('run-V');
  assert.notEqual(a.fsImpl.statSync(a.p).mtimeMs, a.mtimeMs,
    '前置：mtime 必须已经变了（这是本用例唯一的检测面）');
  assert.throws(() => a.io.entries(), /CHAIN|LEDGER_TAMPERED/,
    '同长度篡改必须在**下一次读取**就被抓到，不能等到窗口到期');

  // 对照：把 mtime 伪造成原值（utimes 是真实能力）⇒ 同一次读取抓不到。
  // 没有这条对照，"⑳ 抓到了"说不清是 mtime 起了作用还是别的东西红了。
  const b = await build('run-X');
  b.fsImpl.utimesSync(b.p, b.mtimeMs);
  let controlThrew = false;
  try { b.io.entries(); } catch (_e) { controlThrew = true; }
  assert.equal(controlThrew, false,
    '对照：伪造 mtime 后同一次读取确实抓不到（这才证明 ⑳ 抓它的是 mtime，不是长度）');
});

// ⑳b 能改 ledger 的人也一定想得到改 mtime。对**这种**攻击者，mtime 归零，
//     剩下的上界只有 K 条 / M 字节 —— 这才是 K/M 的真实定位：检测延迟上界。
await test('⑳b 伪造 mtime 的攻击者：K/M 上界仍必须在 K 条追加内抓到', async () => {
  const K = 4;
  const setup = async (opts, runId) => {
    const fsImpl = S.createMemoryFs();
    const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
    const io = S.createLedgerIo(base, opts);
    const sup = S.createSupervisor({ ...base, ledgerIo: io });
    for (let c = 0; c < 2; c++) await sup.runCycle({ runId, cycle: c });
    const p = io.ledgerPath;
    const raw = fsImpl.readFileSync(p);
    const mtimeMs = fsImpl.statSync(p).mtimeMs;
    const at = raw.indexOf(Buffer.from(runId, 'utf8'));
    assert.ok(at >= 0, '前置：必须能找到 runId');
    const mangled = Buffer.from(raw);
    mangled.write(runId.slice(0, -1) + 'Z', at, 'utf8');
    fsImpl.writeFileSync(p, mangled);
    fsImpl.utimesSync(p, mtimeMs);             // 伪造：游标不会因为 mtime 作废
    assert.equal(fsImpl.readFileSync(p).length, raw.length, '前置：等长');
    return { fsImpl, io, sup, runId };
  };

  const real = await setup({ maxEntriesBeforeFullReverify: K }, 'run-K1');
  let threwAt = -1;
  for (let i = 1; i <= K; i++) {
    try { await real.sup.runCycle({ runId: real.runId, cycle: 100 + i }); }
    catch (e) {
      if (/CHAIN|LEDGER_TAMPERED/.test(String(e && e.message))) { threwAt = i; break; }
      throw e;
    }
  }
  assert.ok(threwAt > 0, '伪造 mtime 的篡改仍必须在 ' + K + ' 个周期内被抓到（实际没抓到）');

  // 对照：窗口无界时同样的追加次数抓不到 —— 抓到它的是 K，不是 statSync
  const ctl = await setup({
    maxEntriesBeforeFullReverify: 1e9, maxBytesBeforeFullReverify: 1e9,
  }, 'run-K2');
  let controlThrew = false;
  for (let i = 1; i <= K; i++) {
    try { await ctl.sup.runCycle({ runId: ctl.runId, cycle: 100 + i }); }
    catch (_e) { controlThrew = true; break; }
  }
  assert.equal(controlThrew, false,
    '对照：无界窗口下伪造 mtime 的篡改确实抓不到（这才证明 ⑳b 抓它的是 K）');
});

// ㉔ K 与 M 的关系是断言，不是注释。注释会过期、行宽会变，断言不会。
//    若 M <= K × 行宽，字节界永远够不到，两个限制只有一个在工作 ——
//    而那正是"看起来双保险、实际单保险"的形状。
await test('㉔ M 必须大于 K × 实测最大行宽（否则两个限制只有一个在工作）', async () => {
  const fsImpl = S.createMemoryFs();
  const sup = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) }));
  for (let c = 0; c < 3; c++) await sup.runCycle({ runId: 'run-WIDTH', cycle: c });
  const raw = fsImpl.readFileSync(sup.subsystems.ledgerIo.ledgerPath).toString('utf8');
  const maxRow = raw.split('\n').filter((l) => l.trim())
    .reduce((mx, l) => Math.max(mx, Buffer.byteLength(l, 'utf8')), 0);
  assert.ok(maxRow > 0, '前置：必须量到行宽（量不到 = 本用例在测空气）');
  const room = S.MAX_ENTRIES_BEFORE_FULL_REVERIFY * maxRow;
  assert.ok(S.MAX_BYTES_BEFORE_FULL_REVERIFY > room,
    'M=' + S.MAX_BYTES_BEFORE_FULL_REVERIFY + ' 必须大于 K×实测最大行宽='
    + S.MAX_ENTRIES_BEFORE_FULL_REVERIFY + '×' + maxRow + '=' + room
    + '；否则字节界永远够不到，K/M 只有一个在工作');
});

// ㉕ 未知窗口参数必须拒，不能静默退回默认值。
//     实测后果（不是假设）：模块改名了参数、测试文件还在传旧名，于是"我限了窗口"
//     这句话在调用方成立、在被调方不成立，两边都读绿。一个 64× 宽的窗口靠一个
//     拼写错误就长出来了，而且没有任何断言会红。
await test('㉕ 未知窗口参数一律 throw（静默退回默认值会让窗口声明与实现分叉）', async () => {
  const fsImpl = S.createMemoryFs();
  const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
  // 旧名：正是"改名之后调用方没跟着改"的形态
  assert.throws(() => S.createLedgerIo(base, { reverifyEveryRecords: 4 }), /LEDGER_IO_UNKNOWN_OPT/);
  assert.throws(() => S.createLedgerIo(base, { maxEntriesBeforeReverify: 4 }), /LEDGER_IO_UNKNOWN_OPT/);
  // 合法名必须仍然可用（否则上面两条只是在测"构造函数永远抛"）
  const ok = S.createLedgerIo(base, { maxEntriesBeforeFullReverify: 4 });
  assert.equal(ok.bounds().maxEntriesBeforeFullReverify, 4);
});

// ⑯c₁ 4096 是天花板，1024 是今天写的量。两者混成一个数，就会出现"降限把昨天的账本
//      变成不可读"或者"上限只是一个注释"这两种失败。clamp 必须**非静默**：
//      调用方交来的 payload 被换成了摘要，这件事不记录就等于偷偷改了数据。
await test('⑯c₁ 超 CURRENT_MAX 的写入被裁剪且必须留痕（requested/applied 可断言）', async () => {
  assert.equal(S.ABSOLUTE_MAX, 4096, '硬天花板');
  assert.equal(S.CURRENT_MAX, 1024, '今天的操作上限');
  assert.ok(S.CURRENT_MAX <= S.ABSOLUTE_MAX, '不变式：操作上限不得超过天花板');

  const logged = [];
  const fsImpl = S.createMemoryFs();
  const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0), logClamp: (r) => logged.push(r) });
  const io = S.createLedgerIo(base);
  const payload = { blob: 'x'.repeat(3000), cycleResultLabel: 'FIXTURE_SYNTHETIC' };
  const entry = io.append({ kind: 'cycle_completion', runId: 'run-CL', cycle: 0, epoch: 1, payload });

  const raw = fsImpl.readFileSync(io.ledgerPath).toString('utf8');
  const line = raw.split('\n').filter((l) => l.trim())[0];
  const wrote = Buffer.byteLength(line, 'utf8');
  assert.ok(wrote <= S.CURRENT_MAX, '写进去的记录不得超过 CURRENT_MAX，实际 ' + wrote);
  assert.equal(entry.payload.clamped, true, '被裁剪的 payload 必须自称被裁剪');
  assert.equal(entry.payload.digest, S.sha256(S.canonicalize(payload)),
    '摘要必须是原 payload 的摘要（否则"裁剪"变成了"丢弃"）');
  assert.equal(entry.payload.bytes, Buffer.byteLength(S.canonicalize(payload), 'utf8'),
    '必须记下原 payload 的字节数');

  // 非静默：回调 + 环形缓冲两处都要有，且 requested > applied
  assert.equal(logged.length, 1, 'logClamp 必须被调用一次');
  const rec = io.clamps()[0];
  assert.equal(rec.requested, logged[0].requested);
  assert.ok(rec.requested > rec.applied, 'requested=' + rec.requested + ' 必须大于 applied=' + rec.applied);
  assert.equal(rec.kind, 'cycle_completion');

  // 裁剪之后链必须仍然自洽（哈希是对裁剪后的内容算的，不是先算后改）
  assert.equal(S.verifyChain(S.readJsonl(fsImpl, io.ledgerPath)).intact, true,
    '裁剪后的账本必须能通过链校验');

  // 天花板不可被调用方抬起来：这是在加载时强制的不变式，不是注释
  assert.throws(() => S.createLedgerIo(base, { currentMax: 8192 }), /CURRENT_MAX_EXCEEDS_ABSOLUTE/,
    '任何调用方都不得把操作上限抬到天花板之上');
});

// ⑯c₃a 降限不能把昨天的账本变砖：1024 < bytes <= 4096 的历史记录**合法**，必须仍可续跑。
await test('⑯c₃a 1024 < bytes <= 4096 的历史记录仍可续跑（降限不是砖）', async () => {
  const fsImpl = S.createMemoryFs();
  const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
  const io = S.createLedgerIo(base);
  const payload = { blob: 'y'.repeat(2000), cycleResultLabel: 'FIXTURE_SYNTHETIC' };
  const rest = { kind: 'cycle_completion', runId: 'run-HI', cycle: 0, epoch: 1, completedAtMs: 0, payload, _prev: S.GENESIS };
  const entry = { ...rest, _hash: S.sha256(S.canonicalize(rest)) };
  const line = S.canonicalize(entry) + '\n';
  const bytes = Buffer.byteLength(line, 'utf8');
  assert.ok(bytes > S.CURRENT_MAX && bytes <= S.ABSOLUTE_MAX,
    '前置：这条历史记录必须落在 (CURRENT_MAX, ABSOLUTE_MAX]，实际 ' + bytes);
  fsImpl.writeFileSync(io.ledgerPath, Buffer.from(line, 'utf8'));
  fsImpl.writeFileSync(io.anchorPath, S.canonicalize({
    count: 1, headHash: entry._hash, byteLength: bytes, lastCompletion: { runId: 'run-HI', cycle: 0 }, updatedAtMs: 0,
  }));

  const sup = S.createSupervisor({ ...base, ledgerIo: S.createLedgerIo(base) });
  const info = sup.resume('run-HI');
  assert.equal(info.lastCycle, 0, '4096 时代写下的记录必须仍被认作已完成');
  assert.equal(info.verified, true);
});

// ⑯c₃b 但天花板之上的历史记录**没有**永久豁免：不设例外，读到就 fail-closed。
await test('⑯c₃b 超过 ABSOLUTE_MAX 的历史记录 fail-closed，不开永久例外', async () => {
  const fsImpl = S.createMemoryFs();
  const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
  const io = S.createLedgerIo(base);
  const payload = { blob: 'z'.repeat(5000) };
  const rest = { kind: 'cycle_completion', runId: 'run-OV', cycle: 0, epoch: 1, completedAtMs: 0, payload, _prev: S.GENESIS };
  const entry = { ...rest, _hash: S.sha256(S.canonicalize(rest)) };
  const line = S.canonicalize(entry) + '\n';
  const bytes = Buffer.byteLength(line, 'utf8');
  assert.ok(bytes > S.ABSOLUTE_MAX, '前置：必须超过天花板，实际 ' + bytes);
  fsImpl.writeFileSync(io.ledgerPath, Buffer.from(line, 'utf8'));
  fsImpl.writeFileSync(io.anchorPath, S.canonicalize({
    count: 1, headHash: entry._hash, byteLength: bytes, lastCompletion: null, updatedAtMs: 0,
  }));

  // 前置：连链本身都是自洽的 —— 红点必须落在"超上限"，不是"断链"
  assert.equal(S.verifyChain(S.readJsonl(fsImpl, io.ledgerPath)).intact, true);
  const sup = S.createSupervisor({ ...base, ledgerIo: S.createLedgerIo(base) });
  assert.throws(() => sup.resume('run-OV'), /LEDGER_ENTRY_OVER_ABSOLUTE_MAX/,
    '天花板之上没有"历史豁免"：不然天花板就是一个可以随时间失效的注释');
});

// ㉖ 等长不等于同史。锚点里早就存着 headHash 与 count，却只做类型检查、从不比较——
//    "存下来却不用"比"没有"更贵：它读起来像一个已经生效的绑定。
//    能力边界写在代码注释里：这挡的是"改了账本但没改锚点"，挡不住同时改锚点的人。
await test('㉖ 等长但整链重算过的账本 ⇒ LEDGER_ANCHOR_HEAD_MISMATCH', async () => {
  const rewrite = async (runId, mutate) => {
    const fsImpl = S.createMemoryFs();
    const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
    const io = S.createLedgerIo(base);
    const sup = S.createSupervisor({ ...base, ledgerIo: io });
    for (let c = 0; c < 2; c++) await sup.runCycle({ runId, cycle: c });
    const p = io.ledgerPath;
    const before = fsImpl.readFileSync(p);
    const rows = S.readJsonl(fsImpl, p);
    mutate(rows);
    // 整链重算：能写文件的人也能重算，链是内容哈希、不是签名
    let prev = S.GENESIS;
    for (const r of rows) {
      r._prev = prev;
      const { _hash, ...rest } = r;
      r._hash = S.sha256(S.canonicalize(rest));
      prev = r._hash;
    }
    const text = rows.map((r) => S.canonicalize(r)).join('\n') + '\n';
    fsImpl.writeFileSync(p, Buffer.from(text, 'utf8'));
    return { fsImpl, io, p, beforeLen: before.length, afterLen: Buffer.byteLength(text, 'utf8') };
  };

  const t = await rewrite('run-RW', (rows) => {
    const i = rows.findIndex((r) => r.kind === S.LEDGER_KIND.epoch);
    rows[i].epoch = 2;                       // 1 → 2：等长
  });
  assert.equal(t.afterLen, t.beforeLen, '前置：重写必须保持字节数不变（否则抓到它的是长度）');
  // 前置：重写后的链**自洽**，所以这条用例不可能因为"断链"而红
  assert.equal(S.verifyChain(S.readJsonl(t.fsImpl, t.p)).intact, true,
    '前置：整链重算后必须自洽（这正是链看不见的形态）');
  const sup2 = S.createSupervisor(S.makeDeps({ fsImpl: t.fsImpl, clock: S.createMemoryClock(1000) }));
  assert.throws(() => sup2.resume('run-RW'), /LEDGER_ANCHOR_HEAD_MISMATCH/,
    '等长重写必须被锚点的 head/count 比较抓到');

  // 对照：崩溃恢复（append 完但锚点没更新）会让账本**更长**，必须仍走 ADOPTED_TAIL，
  // 不得被这道门误杀。没有这条对照，这道门会把正常恢复打死而没人知道。
  const fsC = S.createMemoryFs();
  const baseC = S.makeDeps({ fsImpl: fsC, clock: S.createMemoryClock(0) });
  const ioC = S.createLedgerIo(baseC);
  const supC = S.createSupervisor({ ...baseC, ledgerIo: ioC });
  await supC.runCycle({ runId: 'run-AD', cycle: 0 });
  S.appendChainEntry(fsC, ioC.ledgerPath, {           // 写进去了，但锚点没跟着更新
    kind: S.LEDGER_KIND.completion, runId: 'run-AD', cycle: 1, epoch: 1, completedAtMs: 0, payload: {},
  });
  const ioC2 = S.createLedgerIo(baseC);
  const infoC = ioC2.runState('run-AD');
  assert.equal(ioC2.recovery().mode, S.RECOVER.ADOPTED_TAIL,
    '追赶上的尾巴必须仍是 ADOPTED_TAIL，不是失配');
  assert.equal(infoC.lastCycle, 1, '采纳之后必须看得见新周期');
});

// ㉗ 锚点存在但不约束任何东西 —— 和"没有锚点"是两个洞，必须报两个不同的错。
//     由来：锚点里早就存着 count / headHash / byteLength，但"存着"与"缺了要失败"
//     是两件事。只要允许某个字段缺席，缺席就会表达"不适用"，那个字段上的比较就被
//     静默跳过 —— 和 ㉑b（删文件关控制）同一形状，只是搬进了同一个 JSON 对象里。
//     team-lead 的判词：这第三个字段要是允许缺，就等于把刚堵上的洞在同一个文件里
//     又开了一次。所以这里不测"坏锚点会怎样"，而测"缺席是否被当成不适用"。
await test('㉗ 锚点字段缺席或类型错 ⇒ LEDGER_ANCHOR_INVALID（缺席不得表达"不适用"）', async () => {
  const fsImpl = S.createMemoryFs();
  const sup = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) }));
  await sup.runCycle({ runId: 'run-AV', cycle: 0 });
  await sup.runCycle({ runId: 'run-AV', cycle: 1 });
  const io = sup.subsystems.ledgerIo;
  const good = JSON.parse(fsImpl.readFileSync(io.anchorPath).toString('utf8'));
  const ledgerBytes = fsImpl.readFileSync(io.ledgerPath).length;

  // 前置：这一份锚点是真的约束得住的（三字段齐、值对、能开）
  assert.equal(typeof good.count, 'number');
  assert.equal(typeof good.headHash, 'string');
  assert.equal(good.byteLength, ledgerBytes, '前置：锚点字节数 == 实际字节数');
  assert.equal(S.createLedgerIo(S.makeDeps({ fsImpl, clock: S.createMemoryClock(1) })).count(), good.count,
    '前置：完好锚点必须能开（否则下面"改坏它"证明不了任何事）');

  // 账本全程一个字节都不动，链也完好 —— 于是这条用例只可能因为"锚点不约束"而红
  const ledgerBefore = fsImpl.readFileSync(io.ledgerPath);
  assert.equal(S.verifyChain(S.readJsonl(fsImpl, io.ledgerPath)).intact, true);

  const variants = [
    ['空对象（三字段全缺）', {}],
    ['只有 byteLength（count 与 headHash 缺席）', { byteLength: ledgerBytes }],
    ['count 类型错（字符串）', { byteLength: ledgerBytes, count: String(good.count), headHash: good.headHash }],
    ['count 为 null（typeof 是 object，不是 number）', { byteLength: ledgerBytes, count: null, headHash: good.headHash }],
    ['headHash 类型错（数字）', { byteLength: ledgerBytes, count: good.count, headHash: 1234 }],
    ['byteLength 类型错（字符串）', { byteLength: String(ledgerBytes), count: good.count, headHash: good.headHash }],
  ];
  for (const [label, anchorObj] of variants) {
    fsImpl.writeFileSync(io.anchorPath, Buffer.from(S.canonicalize(anchorObj), 'utf8'));
    const sup2 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(2000) }));
    assert.throws(() => sup2.resume('run-AV'), /LEDGER_ANCHOR_INVALID/,
      label + '：必须报 LEDGER_ANCHOR_INVALID，不得把缺席当成"这个字段不适用"');
    assert.equal(fsImpl.readFileSync(io.ledgerPath).equals(ledgerBefore), true,
      label + '：拒绝的过程中账本不得被改动');
  }

  // 三个错不许互相吞并。合成一个大类会让"删掉锚点"和"锚点是坏的"读起来是同一件事，
  // 而它们的处置完全不同：前者可能是误删（补回来即可），后者说明有人写过这个文件。
  fsImpl.unlinkSync(io.anchorPath);
  const sup3 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(3000) }));
  let absentCode = null;
  try { sup3.resume('run-AV'); } catch (e) { absentCode = e && e.code; }
  assert.equal(absentCode, 'LEDGER_ANCHOR_MISSING',
    '缺文件必须是 MISSING 而不是 INVALID，实际 ' + String(absentCode));

  // 完好锚点放回去之后必须重新放行 —— 否则"拒绝"变成永久状态，
  // 而一条永远红的检查会在三天内被人关掉。
  fsImpl.writeFileSync(io.anchorPath, Buffer.from(S.canonicalize(good), 'utf8'));
  const sup4 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(4000) }));
  assert.equal(sup4.resume('run-AV').lastCycle, 1, '完好锚点必须恢复正常，不得残留拒绝');
});

// ㉑a 整周期被截掉 + 锚点在 ⇒ 抛。
//     这是 D-14-0 的复活形态，也是修复前实测到的重复副作用：截掉两个完整周期，
//     intent 与 completion 在同一段字节里一起没了 ⇒ 账本说"没开始过" ⇒ 放行重跑。
//     锚点只知道"少了多少字节/多少条"，不知道少的是哪些 (runId, cycle)，所以在未知
//     集合上放行重跑 = 把 at-most-once 交给运气。降级成 IN_DOUBT 也不行：
//     哪些周期是 IN_DOUBT 本身也是未知的。
await test('㉑a 整周期截断（锚点在）⇒ LEDGER_SHORT_TRUNCATED，且不得执行周期体', async () => {
  const fsImpl = S.createMemoryFs();
  const clock = S.createMemoryClock(0);
  const sup = S.createSupervisor(S.makeDeps({ fsImpl, clock }));
  for (let c = 0; c < 5; c++) await sup.runCycle({ runId: 'run-S', cycle: c });
  const io = sup.subsystems.ledgerIo;
  const raw = fsImpl.readFileSync(io.ledgerPath);
  const anchorRow = JSON.parse(fsImpl.readFileSync(io.anchorPath).toString('utf8'));
  assert.equal(anchorRow.count, 15, '前置：5 个周期 × 3 条记录');
  assert.equal(anchorRow.byteLength, raw.length, '前置：锚点的字节数必须等于实际字节数');

  // 在记录边界截到 9 条 = 只剩周期 0/1/2
  const rows = raw.toString('utf8').split('\n').filter((l) => l.trim());
  const cutBuf = Buffer.from(rows.slice(0, 9).join('\n') + '\n', 'utf8');
  assert.ok(cutBuf.length < raw.length, '前置：必须真的变短');
  fsImpl.writeFileSync(io.ledgerPath, cutBuf);

  // 前置：剩下 9 条的链是自洽的 —— 否则这条用例只是在测断链检测，
  // 而链对后缀截断结构性失明正是本用例存在的理由。
  assert.equal(S.verifyChain(S.readJsonl(fsImpl, io.ledgerPath)).intact, true,
    '前置：截断后剩余记录必须能通过链校验（链看不见后缀截断）');

  let calls = 0;
  const sup2 = S.createSupervisor(S.makeDeps({
    fsImpl, clock: S.createMemoryClock(1000),
    cycleImpl: async () => { calls++; return { ok: true }; },
  }));
  assert.throws(() => sup2.resume('run-S'), /LEDGER_SHORT_TRUNCATED/,
    '缺口状态下的 resume 必须 throw，不得返回一个"看起来正常"的 lastCycle');
  await assert.rejects(() => sup2.runCycle({ runId: 'run-S', cycle: 3 }), /LEDGER_SHORT_TRUNCATED/,
    '周期 3 的副作用可能已经发生过；不得因为账本里没有它而重跑');
  assert.equal(calls, 0, '未知缺口上不得执行周期体（修复前这里是 1）');
  assert.equal(fsImpl.readFileSync(io.ledgerPath).length, cutBuf.length,
    '拒绝之后账本字节必须原样保留：既不许截断，也不许"补齐"');
});

// ㉑b 删掉锚点 + 账本非空 ⇒ 抛。
//     修复前这一条是整段 `if (anchor && typeof anchor.byteLength === 'number')` 被
//     整个跳过，然后报 CLEAN —— 关掉一道控制的最省事办法就是删掉它的证据文件。
await test('㉑b 账本非空但锚点缺失 ⇒ LEDGER_ANCHOR_MISSING（删锚点不是关控制）', async () => {
  const fsImpl = S.createMemoryFs();
  const clock = S.createMemoryClock(0);
  const sup = S.createSupervisor(S.makeDeps({ fsImpl, clock }));
  await sup.runCycle({ runId: 'run-AM', cycle: 0 });
  await sup.runCycle({ runId: 'run-AM', cycle: 1 });
  const io = sup.subsystems.ledgerIo;
  const ledgerBefore = fsImpl.readFileSync(io.ledgerPath);
  fsImpl.unlinkSync(io.anchorPath);

  // 前置：账本本身完好、字节与删除锚点之前完全一致 —— 唯一被拿掉的只有锚点
  assert.equal(fsImpl.readFileSync(io.ledgerPath).equals(ledgerBefore), true);
  assert.equal(S.verifyChain(S.readJsonl(fsImpl, io.ledgerPath)).intact, true,
    '前置：链完好，所以这条用例只可能因为锚点缺失而红');

  const sup2 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(1000) }));
  assert.throws(() => sup2.resume('run-AM'), /LEDGER_ANCHOR_MISSING/);

  // 对照：真正的首次启动（账本也为空）必须允许通过 —— 否则"必须有锚点"就变成了
  // "永远起不来"，而一条永远红的用例和保护是两件事。
  const fresh = S.createMemoryFs();
  const sup3 = S.createSupervisor(S.makeDeps({ fsImpl: fresh, clock: S.createMemoryClock(0) }));
  const info = sup3.resume('run-AM');
  assert.equal(info.lastCycle, -1, '首次启动：空账本 + 无锚点必须放行');
  assert.equal(info.recordCount, 0);
});

// ㉑c 被拒之后不得销毁证据。修复前实测：下一次写入会把锚点改写成截断后的长度，
//     此后再没人能发现少了两个周期 —— 这比"没检测到"更坏，它把"曾经能检测到"也消掉了。
await test('㉑c 被拒之后锚点字节仍是原值，再开仍然拒（不得自我修复）', async () => {
  const fsImpl = S.createMemoryFs();
  const clock = S.createMemoryClock(0);
  const sup = S.createSupervisor(S.makeDeps({ fsImpl, clock }));
  for (let c = 0; c < 4; c++) await sup.runCycle({ runId: 'run-EV', cycle: c });
  const io = sup.subsystems.ledgerIo;
  const anchorBefore = fsImpl.readFileSync(io.anchorPath);
  const anchorLen = anchorBefore.length;
  const rows = fsImpl.readFileSync(io.ledgerPath).toString('utf8').split('\n').filter((l) => l.trim());
  fsImpl.writeFileSync(io.ledgerPath, Buffer.from(rows.slice(0, 6).join('\n') + '\n', 'utf8'));

  const sup2 = S.createSupervisor(S.makeDeps({ fsImpl, clock: S.createMemoryClock(1000) }));
  assert.throws(() => sup2.resume('run-EV'), /LEDGER_SHORT_TRUNCATED/);

  const anchorAfter = fsImpl.readFileSync(io.anchorPath);
  assert.equal(anchorAfter.length, anchorLen, '锚点字节数不得改变');
  assert.equal(anchorAfter.equals(anchorBefore), true,
    '锚点内容必须逐字节不变：被拒的那次读取不许顺手把锚点改成"看起来一致"');
  const rec = sup2.subsystems.ledgerIo.recovery();
  assert.equal(rec.mode, S.RECOVER.SHORT_TRUNCATED);
  assert.ok(rec.shortageBytes > 0, '必须记下少了多少字节，实际 ' + rec.shortageBytes);

  // 第二次仍然拒：如果第一次的失败路径把锚点"修好"了，这里就会放过
  assert.throws(() => sup2.resume('run-EV'), /LEDGER_SHORT_TRUNCATED/,
    '第二次必须仍然拒绝 —— 否则第一次的拒绝只是延迟执行');
  assert.equal(fsImpl.readFileSync(io.anchorPath).equals(anchorBefore), true);
});

// ㉒ 租约易主必须作废游标。
//     游标只比对字节 LENGTH，而"上一个持有者写过"是接管瞬间唯一确定为真的时刻：
//     等长写入能穿过 statSync，于是本进程会接着往一条自己从未校验过的链上追加。
await test('㉒ 租约易主作废游标：上一个持有者写的等长篡改，接管后第一次读取就抓到', async () => {
  const plantedExpiredLease = (fsImpl, sup) => {
    fsImpl.writeFileSync(sup.subsystems.election.lockPath, S.canonicalize({
      ownerToken: 'owner-X', leaseExpiryMs: 500, acquiredAtMs: 0, ttlMs: 500, tookOverExpired: false,
    }));
  };

  // 对照组：同样接管，但账本没被动过 ⇒ 必须正常完成，且 tookOverExpired 必须为真。
  // 没有这条对照，"㉒ 红了"可能只是因为接管根本没发生（那测的是别的）。
  const fsC = S.createMemoryFs();
  const clockC = S.createMemoryClock(0);
  const supC = S.createSupervisor(S.makeDeps({
    fsImpl: fsC, clock: clockC, ownerToken: 'owner-A', leaseTtlMs: 1000,
    cycleImpl: async () => ({ ok: true }),
  }));
  await supC.runCycle({ runId: 'run-TK', cycle: 0 });
  plantedExpiredLease(fsC, supC);
  clockC.set(100000);
  const rC = await supC.runCycle({ runId: 'run-TK', cycle: 1 });
  assert.equal(rC.lease.tookOverExpired, true, '对照：接管必须真的发生过');
  assert.equal(rC.status, 'COMPLETED');

  // 实验组：同样的接管，但盘上是等长原地篡改
  const fsImpl = S.createMemoryFs();
  const clock = S.createMemoryClock(0);
  const sup = S.createSupervisor(S.makeDeps({
    fsImpl, clock, ownerToken: 'owner-A', leaseTtlMs: 1000,
    cycleImpl: async () => ({ ok: true }),
  }));
  await sup.runCycle({ runId: 'run-TK', cycle: 0 });
  const io = sup.subsystems.ledgerIo;
  plantedExpiredLease(fsImpl, sup);
  const raw = fsImpl.readFileSync(io.ledgerPath);
  const mtimeMs = fsImpl.statSync(io.ledgerPath).mtimeMs;
  const at = raw.indexOf(Buffer.from('run-TK', 'utf8'));
  assert.ok(at >= 0, '前置：必须能在盘上找到待篡改的 runId');
  const mangled = Buffer.from(raw);
  mangled.write('run-ZZ', at, 'utf8');
  fsImpl.writeFileSync(io.ledgerPath, mangled);
  // mtime 也要伪造，否则抓到它的是 mtime（⑳ 的判据）而不是"易主作废游标"。
  // 本用例要证的正是：**在 mtime 被抹平的情况下**，易主仍然强制一次全量重验。
  fsImpl.utimesSync(io.ledgerPath, mtimeMs);
  assert.equal(fsImpl.readFileSync(io.ledgerPath).length, raw.length,
    '前置：篡改必须等长，长度比对才看不见');
  assert.equal(fsImpl.statSync(io.ledgerPath).mtimeMs, mtimeMs,
    '前置：mtime 必须已被抹平，否则本用例在测 ⑳ 而不是 ㉒');
  clock.set(100000);

  await assert.rejects(() => sup.runCycle({ runId: 'run-TK', cycle: 1 }), /CHAIN|LEDGER_TAMPERED/,
    '接管后必须立即全量重验：游标不作废就会往坏链上接着写，且要到窗口到期才发现');
});

// ㉓ 只读进程也必须到期。没有 append 时 entries/bytes 两个边界永远不动，
//    唯一能让窗口到期的是 ops 计数 —— 而这条分支原本一条证据都没有。
await test('㉓ 只读路径也必须有界：无 append 时 ops 窗口必须强制重验', async () => {
  const OPS = 3;
  const setup = async (opts, runId) => {
    const fsImpl = S.createMemoryFs();
    const base = S.makeDeps({ fsImpl, clock: S.createMemoryClock(0) });
    const io = S.createLedgerIo(base, opts);
    const sup = S.createSupervisor({ ...base, ledgerIo: io });
    for (let c = 0; c < 2; c++) await sup.runCycle({ runId, cycle: c });
    return { fsImpl, io };
  };
  // mtime 必须一起伪造，否则抓到它的是 mtime（⑳ 的判据），不是 ops 窗口。
  // 本用例要证的正是"没有 mtime 可用时，只读进程也不会永远看不见"。

  const { fsImpl, io } = await setup({
    maxEntriesBeforeFullReverify: 1e9, maxBytesBeforeFullReverify: 1e9, maxOpsBeforeFullReverify: OPS,
  }, 'run-RO');
  const raw = fsImpl.readFileSync(io.ledgerPath);
  const at = raw.indexOf(Buffer.from('run-RO', 'utf8'));
  assert.ok(at >= 0, '前置：必须能在盘上找到待篡改的 runId');
  const mtimeMs = fsImpl.statSync(io.ledgerPath).mtimeMs;
  const mangled = Buffer.from(raw);
  mangled.write('run-RQ', at, 'utf8');
  fsImpl.writeFileSync(io.ledgerPath, mangled);
  fsImpl.utimesSync(io.ledgerPath, mtimeMs);        // 伪造：排除 mtime 这条检测面
  assert.equal(fsImpl.readFileSync(io.ledgerPath).length, raw.length, '前置：篡改必须等长');

  let threwAt = -1;
  for (let i = 1; i <= OPS + 1; i++) {
    try { io.count(); } catch (e) {
      if (/CHAIN|LEDGER_TAMPERED/.test(String(e && e.message))) { threwAt = i; break; }
      throw e;
    }
  }
  assert.ok(threwAt > 0, '只读 ' + (OPS + 1) + ' 次内必须抓到等长篡改（没有任何 append 参与）');

  // 对照：ops 窗口无界时同样次数的只读读不到 —— 抓它的是 ops 窗口，不是 statSync
  const { fsImpl: fs2, io: io2 } = await setup({
    maxEntriesBeforeFullReverify: 1e9, maxBytesBeforeFullReverify: 1e9, maxOpsBeforeFullReverify: 1e9,
  }, 'run-RO');
  const raw2 = fs2.readFileSync(io2.ledgerPath);
  const at2 = raw2.indexOf(Buffer.from('run-RO', 'utf8'));
  assert.ok(at2 >= 0, '前置：对照组也必须找到 runId');
  const mtime2 = fs2.statSync(io2.ledgerPath).mtimeMs;
  const m2 = Buffer.from(raw2);
  m2.write('run-RQ', at2, 'utf8');
  fs2.writeFileSync(io2.ledgerPath, m2);
  fs2.utimesSync(io2.ledgerPath, mtime2);
  let controlThrew = false;
  for (let i = 0; i < OPS + 1; i++) { try { io2.count(); } catch (_e) { controlThrew = true; break; } }
  assert.equal(controlThrew, false, '对照：ops 窗口无界时同样次数的只读确实抓不到');
});

function snapshotDir(fsImpl, dir) {
  const out = {};
  for (const p of fsImpl.listSync(dir)) {
    out[p] = S.sha256(fsImpl.readFileSync(p).toString('hex'));
  }
  return out;
}

function deps_real() { return createMemoryFs(); }

console.log('=== P6 Supervisor 本地骨架验收（纯内存 fixture / DECLARED_NOT_WIRED）===');
for (const l of lines) console.log(l);
console.log('RESULT PASS=' + PASS + ' FAIL=' + FAIL);
if (FAIL > 0) process.exitCode = 1;
