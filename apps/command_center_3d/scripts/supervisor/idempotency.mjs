// idempotency.mjs — 以 (runId, cycle) 为键的 append-only 执行账本
import { FILES, resolvePath, readJsonl, verifyChain, appendChainEntry } from './core.mjs';

export function createIdempotency(deps) {
  const { fsImpl, baseDir, nowMs } = deps;
  const ledgerPath = resolvePath(baseDir, FILES.ledger);

  function entries() { return readJsonl(fsImpl, ledgerPath); }

  // 每次读取都先全量校验链（fail-closed）。
  function hasCompleted(runId, cycle) {
    const all = entries();
    verifyChain(all);
    return all.some((e) => e.kind === 'cycle_completion' && e.runId === runId && e.cycle === cycle);
  }

  // 重复 (runId, cycle) 一律拒绝：不二次写入、不二次执行周期。
  function recordCompletion(runId, cycle, payload = {}) {
    const all = entries();
    verifyChain(all);
    const dup = all.some((e) => e.kind === 'cycle_completion' && e.runId === runId && e.cycle === cycle);
    if (dup) throw new Error('DUPLICATE_CYCLE_REJECTED: ' + runId + '#' + cycle);
    return appendChainEntry(fsImpl, ledgerPath, {
      kind: 'cycle_completion',
      runId,
      cycle,
      completedAtMs: nowMs(),
      payload,
    });
  }

  function verify() { return verifyChain(entries()); }

  return { hasCompleted, recordCompletion, verify, entries, ledgerPath };
}
