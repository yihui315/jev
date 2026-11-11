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
//
// All byte handling now lives in ledger-io.mjs (one verified tail cursor per term).
// This file owns the RECORD SEMANTICS only: which kinds exist, in what order, and
// what is allowed to follow what.
import { sha256 } from './core.mjs';
import { createLedgerIo } from './ledger-io.mjs';

export const KIND = Object.freeze({
  epoch: 'lease_epoch',
  intent: 'cycle_intent',
  completion: 'cycle_completion',
});

export function createIdempotency(deps, ioArg) {
  const { nowMs } = deps;
  const io = ioArg || createLedgerIo(deps);
  const ledgerPath = io.ledgerPath;

  function entries() { return io.entries(); }

  // 每次读取都先校验链（fail-closed）。走游标后这是 O(1)，链的校验由
  // ledger-io 在开任期时和窗口到期时完成。
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
    return io.cycleState(runId, cycle);
  }

  // Fencing token: a monotonically increasing counter derived from the ledger
  // itself, so it survives a restart without a second file to keep in sync and
  // cannot be reset by deleting local state (deleting the ledger is already a
  // chain violation, not a reset).
  function recordLeaseEpoch({ runId, ownerToken, acquiredAtMs }) {
    const epoch = io.epochCount() + 1;
    // The token never enters the ledger in clear: the epoch identifies the term,
    // the digest lets an investigator correlate it without the ledger becoming a
    // credential store. (Ledger files get copied into evidence bundles.)
    return io.append({
      kind: KIND.epoch,
      runId,
      epoch,
      ownerTokenDigest: sha256(String(ownerToken)),
      acquiredAtMs: acquiredAtMs ?? nowMs(),
    });
  }

  // WRITE-AHEAD. Returns only after the line is on stable storage.
  function recordIntent(runId, cycle, info = {}) {
    const state = io.cycleState(runId, cycle);
    // A second intent for the same cycle is not a retry, it is a contract
    // violation: intents are written at most once, and the caller is supposed to
    // have asked cycleState() first.
    if (state !== 'ABSENT') throw new Error('INTENT_ALREADY_EXISTS: ' + runId + '#' + cycle);
    return io.append({
      kind: KIND.intent,
      runId,
      cycle,
      epoch: info.epoch ?? null,
      intentAtMs: nowMs(),
    });
  }

  // 重复 (runId, cycle) 一律拒绝：不二次写入、不二次执行周期。
  function recordCompletion(runId, cycle, payload = {}) {
    const state = io.cycleState(runId, cycle);
    if (state === 'COMPLETED') throw new Error('DUPLICATE_CYCLE_REJECTED: ' + runId + '#' + cycle);
    // Completion without a matching intent means the ordering was bypassed
    // somewhere upstream. Accepting it would re-create D-14-0 in a new shape:
    // a completion with no write-ahead record looks recoverable after a later
    // crash, so a re-run gets authorised. Refuse instead.
    if (state !== 'IN_DOUBT') throw new Error('COMPLETION_WITHOUT_INTENT: ' + runId + '#' + cycle);
    return io.append({
      kind: KIND.completion,
      runId,
      cycle,
      epoch: io.intentEpoch(runId, cycle),
      completedAtMs: nowMs(),
      payload,
    });
  }

  // Explicit, unfiltered chain verification. Kept as a full pass on purpose: it is
  // the caller saying "I want to know about the whole file right now", not the hot
  // path (the hot path uses the cursor).
  function verify() {
    io.open();
    return { intact: true, count: io.count(), head: io.headHash() };
  }

  return {
    hasCompleted, cycleState, recordLeaseEpoch, recordIntent, recordCompletion,
    verify, entries, ledgerPath, io,
  };
}
