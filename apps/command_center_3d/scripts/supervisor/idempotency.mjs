// idempotency.mjs — 以 (runId, cycle) 为键的 append-only 执行账本
//
// THREE record kinds, and their ORDER is the safety property, not a detail:
//
//   lease_epoch     <- which lease acquisition this work runs under (fencing token)
//   cycle_intent    <- "I am about to run the body for this cycle"  (fsync'd)
//   cycle_completion<- "the body for this cycle finished"
//
// D-14-0 was: the side effect happened at supervisor.mjs:69 and the only record
// was written at :73. A crash in between left the world changed and the ledger
// silent, so `resume` said "cycle N never ran" and the next tick ran it again —
// at-most-once quietly became at-least-once with no evidence at all. The intent
// record exists so that the gap is always visible from the ledger alone.
import {
  FILES, resolvePath, readJsonl, verifyChain, appendChainEntryDurable, sha256,
} from './core.mjs';

export const KIND = Object.freeze({
  epoch: 'lease_epoch',
  intent: 'cycle_intent',
  completion: 'cycle_completion',
});

export function createIdempotency(deps) {
  const { fsImpl, baseDir, nowMs } = deps;
  const ledgerPath = resolvePath(baseDir, FILES.ledger);

  function entries() { return readJsonl(fsImpl, ledgerPath); }

  // 每次读取都先全量校验链（fail-closed）。
  function hasCompleted(runId, cycle) {
    return cycleState(runId, cycle) === 'COMPLETED';
  }

  // The recovery question is not "is it done?" but "could it have happened?".
  // There are three answers and collapsing the middle one is exactly how a
  // re-run gets authorised:
  //   ABSENT    -> no intent, nothing can have happened, safe to run
  //   IN_DOUBT  -> intent on disk, no completion: the body MAY already have run
  //   COMPLETED -> completion on disk
  function cycleState(runId, cycle) {
    const all = entries();
    verifyChain(all);
    let sawIntent = false;
    let sawCompletion = false;
    let intentEpoch = null;
    for (const e of all) {
      if (e.runId !== runId || e.cycle !== cycle) continue;
      if (e.kind === KIND.intent) { sawIntent = true; intentEpoch = e.epoch; }
      else if (e.kind === KIND.completion) sawCompletion = true;
    }
    if (sawCompletion) return 'COMPLETED';
    if (sawIntent) return 'IN_DOUBT';
    return 'ABSENT';
  }

  // Fencing token: a monotonically increasing counter derived from the ledger
  // itself, so it survives a restart without a second file to keep in sync and
  // cannot be reset by deleting local state (deleting the ledger is already a
  // chain violation, not a reset).
  function recordLeaseEpoch({ runId, ownerToken, acquiredAtMs }) {
    const all = entries();
    verifyChain(all);
    const epoch = all.filter((e) => e.kind === KIND.epoch).length + 1;
    // The token never enters the ledger in clear: the epoch identifies the term,
    // the digest lets an investigator correlate it without the ledger becoming a
    // credential store. (Ledger files get copied into evidence bundles.)
    return appendChainEntryDurable(fsImpl, ledgerPath, {
      kind: KIND.epoch,
      runId,
      epoch,
      ownerTokenDigest: sha256(String(ownerToken)),
      acquiredAtMs: acquiredAtMs ?? nowMs(),
    });
  }

  // WRITE-AHEAD. Returns only after the line is on stable storage.
  function recordIntent(runId, cycle, info = {}) {
    const all = entries();
    verifyChain(all);
    const dup = all.some((e) => e.kind === KIND.intent && e.runId === runId && e.cycle === cycle);
    // A second intent for the same cycle is not a retry, it is a contract
    // violation: intents are written at most once, and the caller is supposed to
    // have asked cycleState() first.
    if (dup) throw new Error('INTENT_ALREADY_EXISTS: ' + runId + '#' + cycle);
    return appendChainEntryDurable(fsImpl, ledgerPath, {
      kind: KIND.intent,
      runId,
      cycle,
      epoch: info.epoch ?? null,
      intentAtMs: nowMs(),
    });
  }

  // 重复 (runId, cycle) 一律拒绝：不二次写入、不二次执行周期。
  function recordCompletion(runId, cycle, payload = {}) {
    const all = entries();
    verifyChain(all);
    const dup = all.some((e) => e.kind === KIND.completion && e.runId === runId && e.cycle === cycle);
    if (dup) throw new Error('DUPLICATE_CYCLE_REJECTED: ' + runId + '#' + cycle);
    // Completion without a matching intent means the ordering was bypassed
    // somewhere upstream. Accepting it would re-create D-14-0 in a new shape:
    // a completion with no write-ahead record looks recoverable after a later
    // crash, so a re-run gets authorised. Refuse instead.
    const hasIntent = all.some((e) => e.kind === KIND.intent && e.runId === runId && e.cycle === cycle);
    if (!hasIntent) throw new Error('COMPLETION_WITHOUT_INTENT: ' + runId + '#' + cycle);
    const intent = all.find((e) => e.kind === KIND.intent && e.runId === runId && e.cycle === cycle);
    return appendChainEntryDurable(fsImpl, ledgerPath, {
      kind: KIND.completion,
      runId,
      cycle,
      epoch: intent ? intent.epoch : null,
      completedAtMs: nowMs(),
      payload,
    });
  }

  function verify() { return verifyChain(entries()); }

  return {
    hasCompleted, cycleState, recordLeaseEpoch, recordIntent, recordCompletion,
    verify, entries, ledgerPath,
  };
}
