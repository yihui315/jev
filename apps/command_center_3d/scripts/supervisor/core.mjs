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
