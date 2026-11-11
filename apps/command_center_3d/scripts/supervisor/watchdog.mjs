// watchdog.mjs — 心跳文件 + 陈旧检测（注入时钟）；不创建 / 终止任何进程
import { FILES, resolvePath, atomicWriteJson, readJson } from './core.mjs';

export function createWatchdog(deps) {
  const { fsImpl, baseDir, nowMs } = deps;
  const beatPath = resolvePath(baseDir, FILES.heartbeat);
  const staleThresholdMs = deps.staleThresholdMs ?? 30000;

  // 仅写入一个心跳 state 文件，绝不创建 / 终止进程。
  function beat(meta = {}) {
    const prev = readJson(fsImpl, beatPath, null);
    const seq = prev && typeof prev.seq === 'number' ? prev.seq + 1 : 1;
    atomicWriteJson(fsImpl, beatPath, { lastBeatMs: nowMs(), seq, ...meta });
    return read();
  }

  function read() { return readJson(fsImpl, beatPath, null); }

  function check() {
    const hb = read();
    if (!hb || typeof hb.lastBeatMs !== 'number') {
      return { status: 'STALE', reason: 'NO_HEARTBEAT', lastBeatMs: null, ageMs: null };
    }
    const ageMs = nowMs() - hb.lastBeatMs;
    if (ageMs > staleThresholdMs) {
      return { status: 'STALE', reason: 'HEARTBEAT_AGE_EXCEEDED', lastBeatMs: hb.lastBeatMs, ageMs, seq: hb.seq };
    }
    return { status: 'OK', reason: 'FRESH', lastBeatMs: hb.lastBeatMs, ageMs, seq: hb.seq };
  }

  return { beat, check, read, beatPath, staleThresholdMs };
}
