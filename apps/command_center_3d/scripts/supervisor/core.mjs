// core.mjs — P6 内部共享基础（全部依赖注入，默认纯内存 fixture）
// 本文件不注册定时任务、不创建子进程、不发起网络请求、不触碰真实文件系统 / 凭据 / 资金。
import { createHash, randomUUID } from 'node:crypto';
import { posix as pathPosix } from 'node:path';

export const GENESIS = 'GENESIS';
export const BASE_DIR = '/supervisor';

export const FILES = Object.freeze({
  lock: 'leader.lock',
  ledger: 'idempotency.jsonl',
  killSwitch: 'KILL_SWITCH',
  costState: 'cost-state.json',
  heartbeat: 'heartbeat.json',
});

export function resolvePath(baseDir, name) {
  return pathPosix.join(baseDir || BASE_DIR, name);
}

function sortedReplacer(_key, value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const out = {};
    for (const k of Object.keys(value).sort()) out[k] = value[k];
    return out;
  }
  return value;
}

// 规范化 JSON：键名逐层排序，保证同一逻辑对象永远得到同一 sha256。
export function canonicalize(value) {
  return JSON.stringify(value, sortedReplacer);
}

export function sha256(text) {
  return createHash('sha256').update(String(text), 'utf8').digest('hex');
}

// 纯内存文件系统 fixture：实现 fsImpl 注入契约所需的最小子集。
export function createMemoryFs(initial) {
  // ---- BYTES, NOT STRINGS ----
  // `files` must hold Buffers. When it held strings, `asText()` decoded on the way
  // IN, so a half-written multi-byte character could not be represented at all:
  // writing the first byte of `中` (e4 b8 ad) stored `ef bf bd` (U+FFFD, 3 bytes)
  // instead of `e4` (1 byte). Every judgement that depends on "where exactly did
  // the write stop" — lastGood.byteLength cut points, torn-tail detection — was
  // therefore computed on the wrong byte count, and a truncation test would pass
  // or fail for a reason unrelated to the defect. Decode on READ, never on write.
  const files = new Map();
  const toBuf = (d) => (Buffer.isBuffer(d) ? d : Buffer.from(String(d), 'utf8'));
  if (initial) for (const [k, v] of Object.entries(initial)) files.set(k, toBuf(v));
  let nextFd = 1;
  // fd -> { key, append, pos }. TWO things must be remembered, and the first
  // version of this fix only got one of them:
  //   * the open MODE: `writeSync` used to overwrite the whole file regardless of
  //     how it was opened, so `openSync(p,'a') + writeSync + fsyncSync` truncated
  //     the ledger to one line in here while appending on a real disk.
  //   * the FILE POSITION: real `fs.writeSync(fd, buf)` writes at the current
  //     offset and advances it, so two writes on one fd are two successive
  //     appends. Without `pos`, a record written in two syscalls could not be
  //     modelled, and any test of that shape would be green for the wrong reason.
  const fds = new Map();
  // Observability counters. "fsync was called" must be ASSERTABLE, not inferred
  // from "nothing threw" — an fsync that is silently dropped is exactly the
  // defect these counters exist to make visible.
  const stats = { writeCalls: 0, fsyncCalls: 0, truncateCalls: 0 };

  return {
    existsSync(p) { return files.has(String(p)); },
    // Decode on read only. No encoding => return the raw Buffer, because callers
    // that reason about byte offsets must not be handed a lossy decode.
    readFileSync(p, encoding) {
      const key = String(p);
      if (!files.has(key)) { const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e; }
      const buf = files.get(key);
      return encoding ? buf.toString(encoding) : buf;
    },
    writeFileSync(p, data) { files.set(String(p), toBuf(data)); },
    appendFileSync(p, data) {
      const key = String(p);
      const merged = Buffer.concat([files.get(key) || Buffer.alloc(0), toBuf(data)]);
      files.set(key, merged);
      // An open fd on this path now points at the new end, like a real append to a
      // file another descriptor is positioned in.
      for (const [, ent] of fds) if (ent.key === key) ent.pos = merged.length;
    },
    unlinkSync(p) {
      const key = String(p);
      if (!files.has(key)) { const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e; }
      files.delete(key);
    },
    renameSync(a, b) {
      const ka = String(a), kb = String(b);
      if (!files.has(ka)) { const e = new Error('ENOENT: ' + ka); e.code = 'ENOENT'; throw e; }
      files.set(kb, files.get(ka));   // moves the Buffer, not a decoded string
      files.delete(ka);
    },
    // 'wx' 为原子排他创建：文件已存在则抛 EEXIST，用于租约选举。
    openSync(p, flags) {
      const key = String(p);
      const f = String(flags || 'r');
      if (f.includes('x') && files.has(key)) {
        const e = new Error('EEXIST: ' + key); e.code = 'EEXIST'; throw e;
      }
      const isAppend = f.startsWith('a');
      let pos = 0;
      if (f.includes('w') || f.includes('x')) {
        files.set(key, Buffer.alloc(0));
        pos = 0;
      } else if (isAppend) {
        if (!files.has(key)) files.set(key, Buffer.alloc(0));
        pos = files.get(key).length;
      } else if (f.startsWith('r') && !files.has(key)) {
        const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e;
      }
      const fd = nextFd++;
      fds.set(fd, { key, append: isAppend, pos });
      return fd;
    },
    closeSync(fd) { fds.delete(fd); },
    // Writes at the descriptor's current offset and advances it. On an 'a' fd the
    // write always lands at end-of-file (O_APPEND semantics), matching the kernel.
    writeSync(fd, data) {
      const ent = fds.get(fd);
      if (ent === undefined) return;
      stats.writeCalls++;
      const add = toBuf(data);
      const cur = files.get(ent.key) || Buffer.alloc(0);
      let next;
      if (ent.append) {
        next = Buffer.concat([cur, add]);
        ent.pos = next.length;
      } else {
        const at = Math.max(0, Math.min(ent.pos, cur.length));
        next = Buffer.concat([cur.subarray(0, at), add, cur.subarray(at + add.length)]);
        ent.pos = at + add.length;
      }
      files.set(ent.key, next);
      return add.length;
    },
    fsyncSync(fd) {
      // Durability is not observable in a memory fixture; what IS observable, and
      // what the tests assert, is that the call happened on the right path.
      if (!fds.has(fd)) { const e = new Error('EBADF: ' + fd); e.code = 'EBADF'; throw e; }
      stats.fsyncCalls++;
    },
    // Byte-exact. Callers must still cut at a line boundary: slicing mid-codepoint
    // yields U+FFFD on the next decode, turning a clean truncation into a fresh
    // parse error. The point of storing Buffers is that the truncation POINT is
    // honest, not that decoding a half character is meaningful.
    truncateSync(p, len) {
      const key = String(p);
      if (!files.has(key)) { const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e; }
      stats.truncateCalls++;
      const cur = files.get(key);
      const n = len === undefined ? 0 : Math.max(0, Math.min(Number(len), cur.length));
      files.set(key, cur.subarray(0, n));
      // A descriptor positioned past the new end must not keep a stale offset.
      for (const [, ent] of fds) if (ent.key === key && ent.pos > n) ent.pos = n;
      return n;
    },
    statSync(p) {
      const key = String(p);
      if (!files.has(key)) { const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e; }
      const bytes = files.get(key).length;
      return { size: bytes, byteLength: bytes };
    },
    fsyncCallCount() { return stats.fsyncCalls; },
    truncateCallCount() { return stats.truncateCalls; },
    writeCallCount() { return stats.writeCalls; },
    mkdirSync() {},
  };
}

// PERMANENT NEGATIVE CONTROL — the fixture as it was BEFORE the byte model.
//
// Kept importable on purpose. Two cases at the bottom of supervisor.test.mjs
// assert that THIS model produces the WRONG bytes. Without that, a later
// "simplification" back to string storage would turn every tearing test green,
// and green is the most easily accepted wrong state. Same shape as the permanent
// negative control in server/tests/http-body-read.mjs (case 4).
//
// Two defects it reproduces, both measured:
//   1. `writeSync` ignores the open mode and overwrites the whole file, so two
//      consecutive writes on one fd leave only the second one.
//   2. It decodes on write, so the first byte of `中` (e4 b8 ad) is stored as
//      ef bf bd (U+FFFD, 3 bytes) instead of e4 (1 byte). A torn multi-byte
//      character cannot exist in this model at all.
export function createLegacyStringFs(initial) {
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
    openSync(p, flags) {
      const key = String(p);
      const f = String(flags || 'r');
      if (f.includes('x') && files.has(key)) { const e = new Error('EEXIST: ' + key); e.code = 'EEXIST'; throw e; }
      if (f.includes('w') || f.includes('x')) files.set(key, '');
      else if (f.startsWith('a')) { if (!files.has(key)) files.set(key, ''); }
      else if (f.startsWith('r') && !files.has(key)) { const e = new Error('ENOENT: ' + key); e.code = 'ENOENT'; throw e; }
      const fd = nextFd++;
      fds.set(fd, key);
      return fd;
    },
    closeSync(fd) { fds.delete(fd); },
    writeSync(fd, data) { const key = fds.get(fd); if (key !== undefined) files.set(key, (files.get(key) || '') + asText(data)); },
    unlinkSync(p) { files.delete(String(p)); },
  };
}

export function createMemoryClock(startMs = 0) {
  let t = startMs;
  return {
    nowMs: () => t,
    set: (v) => { t = v; return t; },
    advance: (by) => { t += by; return t; },
    peek: () => t,
  };
}

// 默认周期执行体：纯内存 no-op fixture，不触网、不下单、不读凭据、不产出实测数值。
export function defaultCycleImpl() {
  return async () => ({
    ok: true,
    dataLabel: 'FIXTURE_SYNTHETIC',
    note: 'in-memory no-op fixture; no external calls, no orders, no measured values',
  });
}

// 时钟 / 文件系统 / 周期执行体 / 休眠一律注入；默认纯内存。
// sleepImpl 仅作为注入契约保留：本轮不启动守护循环，因此永不调用（无真实等待）。
export function makeDeps(overrides = {}) {
  const clock = overrides.clock || createMemoryClock(overrides.startMs ?? 0);
  return {
    nowMs: overrides.nowMs || clock.nowMs,
    fsImpl: overrides.fsImpl || createMemoryFs(),
    cycleImpl: overrides.cycleImpl || defaultCycleImpl(),
    sleepImpl: overrides.sleepImpl || (async () => {}),
    baseDir: overrides.baseDir || BASE_DIR,
    ownerToken: overrides.ownerToken || randomUUID(),
    leaseTtlMs: overrides.leaseTtlMs ?? 60000,
    staleThresholdMs: overrides.staleThresholdMs ?? 30000,
    clock,
  };
}

export function readJsonl(fsImpl, filePath) {
  let raw;
  try { raw = fsImpl.readFileSync(filePath); }
  catch (e) { if (e.code === 'ENOENT') return []; throw e; }
  const text = Buffer.isBuffer(raw) ? raw.toString('utf8') : String(raw);
  return text.split('\n').filter((l) => l.trim().length > 0).map((line, i) => {
    try { return JSON.parse(line); }
    catch (_e) { throw new Error('LEDGER_PARSE_FAILED at line ' + i); }
  });
}

// 全量校验 append-only 链：_prev 必须衔接上一条 _hash，_hash 必须由本条内容重算。
// 篡改 / 断链 / 删除任一行都会 throw（fail-closed）。
export function verifyChain(entries) {
  // TYPE GUARD FIRST. Without it, a caller that still passes a wrapper object
  // (`{entries:[...], tailTorn:false}`) makes `0 < undefined` false, so the loop
  // runs zero times and this function returns {intact:true, count:undefined} —
  // a SILENT PASS. Measured: verifyChain({entries:[{a:1}],tailTorn:false,tornBytes:0})
  // -> {"intact":true,"head":"GENESIS"}. Changing readJsonl's return shape is
  // exactly when this happens, which is why the guard is part of the same change.
  if (!Array.isArray(entries)) throw new Error('CHAIN_INPUT_NOT_ARRAY');
  let prev = GENESIS;
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (!e || typeof e !== 'object') throw new Error('CHAIN_ENTRY_INVALID at ' + i);
    if (typeof e._prev !== 'string' || typeof e._hash !== 'string') throw new Error('CHAIN_META_MISSING at ' + i);
    if (e._prev !== prev) throw new Error('CHAIN_BROKEN at ' + i);
    const { _hash: storedHash, ...rest } = e;
    if (sha256(canonicalize(rest)) !== storedHash) throw new Error('CHAIN_TAMPERED at ' + i);
    prev = storedHash;
  }
  return { intact: true, count: entries.length, head: prev };
}

export function appendChainEntry(fsImpl, filePath, business) {
  const entries = readJsonl(fsImpl, filePath);
  verifyChain(entries);
  const prev = entries.length ? entries[entries.length - 1]._hash : GENESIS;
  const rest = { ...business, _prev: prev };
  const _hash = sha256(canonicalize(rest));
  const entry = { ...rest, _hash };
  fsImpl.appendFileSync(filePath, canonicalize(entry) + '\n');
  return entry;
}

// state 原子写：先写临时文件再 rename 覆盖，避免半写状态。
export function atomicWriteJson(fsImpl, filePath, value) {
  const tmp = String(filePath) + '.tmp';
  fsImpl.writeFileSync(tmp, canonicalize(value));
  fsImpl.renameSync(tmp, filePath);
}

export function readJson(fsImpl, filePath, fallback = null) {
  let raw;
  try { raw = fsImpl.readFileSync(filePath); }
  catch (e) { if (e.code === 'ENOENT') return fallback; throw e; }
  const text = Buffer.isBuffer(raw) ? raw.toString('utf8') : String(raw);
  if (text.trim().length === 0) return fallback;
  try { return JSON.parse(text); }
  catch (_e) { throw new Error('STATE_PARSE_FAILED: ' + String(filePath)); }
}
