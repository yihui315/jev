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
    const completed = new Set();
    const intended = new Set();
    for (const e of all) {
      if (e.runId !== runId || typeof e.cycle !== 'number') continue;
      if (e.kind === 'cycle_completion') {
        completed.add(e.cycle);
        matched++;
        if (e.cycle > lastCycle) lastCycle = e.cycle;
      } else if (e.kind === 'cycle_intent') {
        intended.add(e.cycle);
      }
    }
    // An intent with no completion means the cycle body may already have had its
    // side effect. These are reported, never skipped: skipping them would move
    // nextCycle past work of unknown outcome, which is a re-run authorisation
    // wearing a different hat.
    const inDoubtCycles = [...intended].filter((c) => !completed.has(c)).sort((a, b) => a - b);
    return {
      runId,
      lastCycle,
      nextCycle: lastCycle + 1,
      matched,
      verified: v.intact,
      recordCount: v.count,
      inDoubtCycles,
      blocked: inDoubtCycles.length > 0,
    };
  }

  return { resume, ledgerPath };
}
