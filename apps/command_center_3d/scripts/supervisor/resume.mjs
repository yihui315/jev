// resume.mjs — 断点恢复：先全量校验链，再返回 lastCycle
//
// The public `resume()` is the "tell me the truth about this run right now" call:
// it forces a full verify and can throw. `runCycle` deliberately does NOT use it on
// the hot path — it uses the ledger cursor's O(1) runState(), which is kept honest
// by the bounded re-verify window. Same answers, different cost, and the difference
// is stated rather than hidden.
import { createLedgerIo } from './ledger-io.mjs';

export function createResume(deps, ioArg) {
  const io = ioArg || createLedgerIo(deps);
  const ledgerPath = io.ledgerPath;

  function resume(runId) {
    // Force the full pass: corruption must throw here, never be papered over by a
    // cached cursor. This is the recovery/status API, not the per-cycle hot path.
    io.open();
    const rs = io.runState(runId);
    return {
      runId,
      lastCycle: rs.lastCycle,
      nextCycle: rs.nextCycle,
      matched: rs.matched,
      verified: true,
      recordCount: io.count(),
      inDoubtCycles: rs.inDoubtCycles,
      blocked: rs.blocked,
    };
  }

  return { resume, ledgerPath, io };
}
