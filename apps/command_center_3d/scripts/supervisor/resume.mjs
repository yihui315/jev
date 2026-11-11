// resume.mjs — 断点恢复：先全量校验链，再返回 lastCycle
import { FILES, resolvePath, readJsonl, verifyChain } from './core.mjs';

export function createResume(deps) {
  const { fsImpl, baseDir } = deps;
  const ledgerPath = resolvePath(baseDir, FILES.ledger);

  function resume(runId) {
    const all = readJsonl(fsImpl, ledgerPath);
    // 链被篡改 / 断链一律 throw（fail-closed），绝不部分恢复、绝不返回 lastCycle。
    const v = verifyChain(all);
    let lastCycle = -1;          // -1 表示无历史；绝不把缺失写成 0
    let matched = 0;
    for (const e of all) {
      if (e.kind === 'cycle_completion' && e.runId === runId && typeof e.cycle === 'number') {
        matched++;
        if (e.cycle > lastCycle) lastCycle = e.cycle;
      }
    }
    return {
      runId,
      lastCycle,
      nextCycle: lastCycle + 1,
      matched,
      verified: v.intact,
      recordCount: v.count,
    };
  }

  return { resume, ledgerPath };
}
