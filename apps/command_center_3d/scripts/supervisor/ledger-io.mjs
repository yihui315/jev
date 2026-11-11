// ledger-io.mjs — the single owner of ledger bytes.
//
// Two things live here that used to be spread across idempotency/resume, and both
// are consequences of taking the write-ahead ordering seriously:
//
// 1. A VERIFIED TAIL CURSOR. Before this file existed, every record write did
//    `readFileSync + verifyChain` twice (once by the caller, once inside the
//    append), and every read path did it again. Per cycle that was 8 full passes
//    over a growing file, i.e. per-cycle cost proportional to ledger length —
//    measured: 2.17/4.33/8.37 ms per cycle at 100/200/400 cycles on the prior
//    build, and 50.9 ms at 400 cycles after write-ahead landed. A 24/7 supervisor
//    on a 60 s cadence reaches 1 s/cycle in days, not years.
//    Now: ONE full verify per term (at lease acquisition), O(1) appends after, and
//    a cheap `statSync` length check every cycle so an external writer is still
//    noticed without reading the file.
//
//    Cost claim, stated exactly: appends are O(1); the periodic re-verify makes the
//    amortised cost O(n/K) per cycle rather than O(1) strictly. With K=256 that is
//    ~2000x below the 8-full-passes-per-cycle it replaces, but it is NOT constant,
//    and saying "O(1)" would be the same kind of overclaim as a stale number.
//
// 2. RECOVERY WITH AN ANCHOR. The hash chain is structurally blind to a suffix
//    truncation: delete the last N records and every remaining link still
//    verifies. No amount of chain checking fixes that, so the expected byte length
//    is recorded in a separate anchor file. That is the only way "fewer bytes than
//    there should be" can be noticed at all.
//
// WHY A SHORT LEDGER IS A HARD STOP, NOT A REPAIRABLE STATE. The anchor knows how
// many bytes and how many records SHOULD be there; it does not know WHICH
// (runId, cycle) pairs they were. `{count, byteLength, headHash, lastCompletion}`
// cannot reconstruct the missing set. So a short ledger means an UNKNOWN set of
// cycles whose side effects may already have happened, and "the ledger does not
// mention it" is not "it did not happen" — that is D-14-0 wearing a new coat.
// Therefore SHORT_TRUNCATED throws, and no path advances the anchor on the way
// out: rewriting the anchor to the truncated length would destroy the only
// evidence that anything was lost, which is strictly worse than not noticing.
// The escape hatch for a real incident is a human with an explicit operator
// decision, not a recovery mode that guesses.
//
// THE CURSOR IS BOUNDED ON PURPOSE. Moving the full verify from every write to
// every term would trade a bounded cost for an unbounded trust window: a term that
// runs for a million cycles would never re-verify. The three MAX_* bounds below
// cap that window, so the guarantee is "the chain was fully re-verified at most
// this many records / bytes / operations ago", which is a bounded claim, not a
// hopeful one.
import {
  FILES, resolvePath, GENESIS, canonicalize, sha256, verifyChain,
  atomicWriteJson, readJson,
} from './core.mjs';

// ONE definition, referenced by both the write-side clamp and the read-side limit.
// Nothing may override it: not intent, not config, not env. (There is no other
// 4096 in this module's decision path; the number in
// server/tests/http-body-read.mjs:286 is the HTTP body cap — the same integer for
// a different reason, and deliberately NOT shared as a constant, because tying two
// unrelated limits together makes one of them impossible to change.)
export const ABSOLUTE_MAX = 4096;

// The bounded re-verify window. ONE number, three named uses, deliberately NOT
// independent: the previous version reused a single `K` as both a read count and
// a record count, so "the window" silently changed width the moment the hot path
// gained one more read. Naming the unit is what stops that drift.
//
//   MAX_ENTRIES_BEFORE_FULL_REVERIFY  records appended since the last full verify
//   MAX_BYTES_BEFORE_FULL_REVERIFY    bytes appended since the last full verify
//   MAX_OPS_BEFORE_FULL_REVERIFY      cursor reads *including* appends — without
//                                     it, a term that only reads would never
//                                     re-verify and a same-length tamper would
//                                     live forever
//
// Reaching ANY of the three forces a full re-verify, and so does a cursor whose
// recorded byteLength stops matching the file, and so does any lease handover
// (see invalidate()). At ~3 appends and ~4 reads per cycle, 256 operations is
// ~36 cycles; at ~380 bytes per record, 1 MiB is ~2700 records. The byte bound
// therefore binds first in practice, and the sentence to quote is "fully verified
// at most 1 MiB of appends ago" — not "recently".
export const MAX_ENTRIES_BEFORE_FULL_REVERIFY = 256;
export const MAX_BYTES_BEFORE_FULL_REVERIFY = 1048576;              // 1 MiB
export const MAX_OPS_BEFORE_FULL_REVERIFY = MAX_ENTRIES_BEFORE_FULL_REVERIFY;

export const RECOVER = Object.freeze({
  EMPTY: 'EMPTY',
  CLEAN: 'CLEAN',
  TORN_TAIL_TRUNCATED: 'TORN_TAIL_TRUNCATED',
  ADOPTED_TAIL: 'ADOPTED_TAIL',
  // Terminal, not a repair: reaching this mode always throws. Kept in the enum so
  // the value that appears in the error and in `recovery()` is the same string.
  SHORT_TRUNCATED: 'SHORT_TRUNCATED',
});

const NL = 0x0a;

function fail(code, message, extra) {
  const err = new Error(code + ': ' + message);
  err.code = code;
  if (extra) Object.assign(err, extra);
  return err;
}

function parseLedger(body) {
  const text = body.toString('utf8');
  const out = [];
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().length === 0) continue;
    try { out.push(JSON.parse(line)); }
    catch (_e) { throw new Error('LEDGER_PARSE_FAILED at line ' + i); }
  }
  return out;
}

export function createLedgerIo(deps, opts = {}) {
  const { fsImpl, baseDir, nowMs } = deps;
  const ledgerPath = resolvePath(baseDir, FILES.ledger);
  const anchorPath = resolvePath(baseDir, FILES.anchor);
  const K_ENTRIES = opts.maxEntriesBeforeFullReverify ?? MAX_ENTRIES_BEFORE_FULL_REVERIFY;
  const K_BYTES = opts.maxBytesBeforeFullReverify ?? MAX_BYTES_BEFORE_FULL_REVERIFY;
  const K_OPS = opts.maxOpsBeforeFullReverify ?? MAX_OPS_BEFORE_FULL_REVERIFY;

  let st = null;         // see open()
  let termStartCount = 0;
  let termStartBytes = 0;
  let readsSinceVerify = 0;
  const stats = {
    fullVerifies: 0, appends: 0, forcedReverifies: 0, adopts: 0,
    truncations: 0, discardedBytes: 0, invalidations: 0, shortages: 0,
  };
  let lastRecovery = null;

  function exists(p) { return fsImpl.existsSync(p); }
  function sizeOf(p) { return exists(p) ? fsImpl.statSync(p).byteLength : 0; }

  function readAnchor() { return readJson(fsImpl, anchorPath, null); }

  function writeAnchor(extra) {
    atomicWriteJson(fsImpl, anchorPath, {
      count: st.entries.length,
      headHash: st.headHash,
      byteLength: st.byteLength,
      lastCompletion: extra && extra.lastCompletion ? extra.lastCompletion : st.lastCompletion,
      updatedAtMs: nowMs(),
    });
  }

  // Full read + verify + classify. O(n) — called once per term, and again only if
  // the cursor's byte length stops matching the file.
  function open() {
    stats.fullVerifies++;
    const raw = exists(ledgerPath) ? fsImpl.readFileSync(ledgerPath) : Buffer.alloc(0);
    const anchor = readAnchor();

    // An anchor that parses but constrains nothing is the same failure as no
    // anchor at all, only quieter: `if (anchor && typeof anchor.byteLength ===
    // 'number')` silently skips a `{}` and then reports CLEAN. Fail closed.
    const anchorValid = !!anchor
      && typeof anchor.byteLength === 'number'
      && typeof anchor.count === 'number'
      && typeof anchor.headHash === 'string';
    if (anchor && !anchorValid) {
      throw fail('LEDGER_ANCHOR_INVALID',
        'anchor exists but does not constrain anything (byteLength / count / headHash missing or mistyped)',
        { anchor });
    }

    let mode = RECOVER.CLEAN;
    let discardedBytes = 0;
    let body = raw;

    // (a) TORN TAIL — the file does not end in a newline, so the last record is
    //     not known to be complete. This is checked BEFORE anything else and does
    //     not need the anchor: "does not end in \n" is visible in the bytes.
    //     Safe to repair because a torn record was never a complete record: the
    //     `\n` boundary is respected, so no whole record is dropped and no whole
    //     (runId, cycle) pair disappears without its intent surviving.
    if (raw.length > 0 && raw[raw.length - 1] !== NL) {
      let cut = raw.length - 1;
      while (cut > 0 && raw[cut] !== NL) cut--;
      body = raw.subarray(0, cut === 0 ? 0 : cut + 1);
      discardedBytes = raw.length - body.length;
      mode = RECOVER.TORN_TAIL_TRUNCATED;
      stats.truncations++;
      stats.discardedBytes += discardedBytes;
      fsImpl.truncateSync(ledgerPath, body.length);
    }

    // (b) Chain verification. A parse failure or a broken link is LEDGER_TAMPERED,
    //     never a silent skip and never a partial recovery.
    let entries;
    try {
      entries = parseLedger(body);
      verifyChain(entries);
    } catch (e) {
      if (e && e.code === 'LEDGER_TAMPERED') throw e;
      throw fail('LEDGER_TAMPERED', e && e.message ? e.message : String(e), { cause_: e });
    }

    // (c) AN ANCHOR IS MANDATORY FOR A NON-EMPTY LEDGER. Only a genuine first
    //     start (nothing on disk at all) may run without one. Deleting the anchor
    //     must not be a way to switch this control off — before this check it was
    //     exactly that: the whole comparison was skipped and the mode reported
    //     CLEAN, which is the loudest possible way to be wrong.
    if (!anchorValid && entries.length > 0) {
      throw fail('LEDGER_ANCHOR_MISSING',
        'ledger has ' + entries.length + ' record(s) but no valid anchor; the expected tail is unknown, '
        + 'so a truncation cannot be distinguished from normal state',
        { entriesNow: entries.length });
    }

    let lastCompletion = null;
    for (let i = entries.length - 1; i >= 0 && !lastCompletion; i--) {
      const e = entries[i];
      if (e.kind === 'cycle_completion') lastCompletion = { runId: e.runId, cycle: e.cycle };
    }

    // (d) ANCHOR COMPARISON — only the anchor can see "bytes that should be here
    //     are not". Shorter is terminal. Longer means writes landed after the
    //     anchor was last updated (crash between append and anchor), which is
    //     admissible ONLY if the extra records chain onto the anchored head.
    if (anchorValid) {
      if (body.length < anchor.byteLength) {
        const shortageBytes = anchor.byteLength - body.length;
        const entriesMissing = anchor.count - entries.length;
        stats.shortages++;
        lastRecovery = {
          mode: RECOVER.SHORT_TRUNCATED, discardedBytes, adopted: false,
          anchorPresent: true, shortageBytes, entriesMissing,
        };
        // No truncate, no demote, no anchor rewrite. The bytes on disk are the
        // evidence; the one thing we must not do is "repair" them into agreement.
        throw fail('LEDGER_SHORT_TRUNCATED',
          'ledger is ' + shortageBytes + ' bytes / ' + entriesMissing + ' record(s) shorter than the anchor; '
          + 'which (runId, cycle) pairs are missing cannot be reconstructed, so running any cycle would '
          + 'bet at-most-once on a guess',
          { shortageBytes, entriesMissing, entriesNow: entries.length, entriesAnchored: anchor.count });
      } else if (body.length > anchor.byteLength && entries.length > 0) {
        const tail = entries.slice(anchor.count);
        const chained = tail.length > 0 && tail[0]._prev === anchor.headHash;
        if (!chained) {
          throw fail('LEDGER_TAMPERED', 'tail does not chain to anchored head');
        }
        mode = RECOVER.ADOPTED_TAIL;
        stats.adopts++;
      }
    }

    st = {
      entries,
      headHash: entries.length ? entries[entries.length - 1]._hash : GENESIS,
      byteLength: body.length,
      lastCompletion,
      epochCount: 0,
      idx: new Map(),      // `${runId}#${cycle}` -> { intent:bool, completion:bool, epoch }
      runs: new Map(),     // runId -> { lastCycle, matched, completed:Set, intended:Set }
    };
    for (const e of entries) indexEntry(e);

    lastRecovery = { mode, discardedBytes, adopted: mode === RECOVER.ADOPTED_TAIL, anchorPresent: anchorValid };
    termStartCount = entries.length;
    termStartBytes = body.length;
    // Reached only on CLEAN / TORN_TAIL_TRUNCATED / ADOPTED_TAIL. The two terminal
    // modes threw above, so this call can never overwrite the anchor with a
    // length that has just been declared untrustworthy.
    if (!anchorValid || mode !== RECOVER.CLEAN || (anchor && anchor.byteLength !== body.length)) {
      writeAnchor({ lastCompletion });
    }
    return st;
  }

  function slotOf(runId, cycle) {
    const k = runId + '#' + cycle;
    let s = st.idx.get(k);
    if (!s) { s = { intent: false, completion: false, epoch: null }; st.idx.set(k, s); }
    return s;
  }
  function runOf(runId) {
    let r = st.runs.get(runId);
    if (!r) { r = { lastCycle: -1, matched: 0, completed: new Set(), intended: new Set() }; st.runs.set(runId, r); }
    return r;
  }

  function indexEntry(e) {
    if (!e || typeof e.runId !== 'string') return;
    if (e.kind === 'lease_epoch') {
      st.epochCount++;
    } else if (e.kind === 'cycle_intent') {
      const slot = slotOf(e.runId, e.cycle);
      slot.intent = true;
      slot.epoch = e.epoch ?? null;
      if (typeof e.cycle === 'number') runOf(e.runId).intended.add(e.cycle);
    } else if (e.kind === 'cycle_completion') {
      slotOf(e.runId, e.cycle).completion = true;
      if (typeof e.cycle === 'number') {
        const r = runOf(e.runId);
        r.completed.add(e.cycle);
        r.matched++;
        if (e.cycle > r.lastCycle) r.lastCycle = e.cycle;
      }
    }
  }

  // O(1). Detects any external change to the ledger — including a truncation made
  // *during* a term — without reading the file. A same-length in-place tamper is
  // NOT visible here; that is what the bounded re-verify window below is for.
  function ensureFresh() {
    if (!st) return open();
    // Bound the trust window by OPERATION COUNT as well: a term that only reads
    // must still re-verify eventually, otherwise a read-only process would never
    // notice a same-length tamper.
    if (++readsSinceVerify >= K_OPS) {
      readsSinceVerify = 0;
      stats.forcedReverifies++;
      return open();
    }
    const actual = sizeOf(ledgerPath);
    if (actual !== st.byteLength) return open();
    return st;
  }

  function checkBounds() {
    if (st.entries.length - termStartCount >= K_ENTRIES || st.byteLength - termStartBytes >= K_BYTES) {
      stats.forcedReverifies++;
      return open();
    }
    return st;
  }

  // Lease handover voids the cursor. The previous holder may have written bytes
  // this cursor never saw, and `ensureFresh` compares LENGTH only — so a
  // same-length write by the previous holder would survive the handover and this
  // process would keep appending onto a chain whose head it never verified.
  // Taking over an expired lease is the one moment where "someone else may have
  // written" is known to be true, so it is the one moment we must pay for a full
  // pass. Called from supervisor.mjs on `lease.tookOverExpired`.
  function invalidate() {
    st = null;
    readsSinceVerify = 0;
    stats.invalidations++;
  }

  // O(1) amortised: no read, no chain re-verify. The chain is re-verified when the
  // term opens and again when the bound is hit; the entry chained onto is the one
  // this object just wrote, so the link is correct by construction.
  function append(business, opts2 = {}) {
    ensureFresh();
    if (!opts2.skipBound) checkBounds();
    const rest = { ...business, _prev: st.headHash };
    const entry = { ...rest, _hash: sha256(canonicalize(rest)) };
    const line = canonicalize(entry) + '\n';
    const bytes = Buffer.byteLength(line, 'utf8');
    if (bytes > ABSOLUTE_MAX) {
      const err = new Error('LEDGER_ENTRY_OVER_ABSOLUTE_MAX: bytes=' + bytes + ' max=' + ABSOLUTE_MAX);
      err.code = 'LEDGER_ENTRY_OVER_ABSOLUTE_MAX';
      throw err;
    }
    const fd = fsImpl.openSync(ledgerPath, 'a');
    try {
      fsImpl.writeSync(fd, line);
      fsImpl.fsyncSync(fd);          // durability BEFORE the caller proceeds
    } finally {
      fsImpl.closeSync(fd);
    }
    stats.appends++;
    st.entries.push(entry);
    st.headHash = entry._hash;
    st.byteLength += bytes;
    indexEntry(entry);
    if (entry.kind === 'cycle_completion') st.lastCompletion = { runId: entry.runId, cycle: entry.cycle };
    writeAnchor({});
    return entry;
  }

  function cycleState(runId, cycle) {
    ensureFresh();
    const slot = st.idx.get(runId + '#' + cycle);
    if (!slot) return 'ABSENT';
    if (slot.completion) return 'COMPLETED';
    if (slot.intent) return 'IN_DOUBT';
    return 'ABSENT';
  }

  function runState(runId) {
    ensureFresh();
    const r = st.runs.get(runId);
    if (!r) return { runId, lastCycle: -1, nextCycle: 0, matched: 0, inDoubtCycles: [], blocked: false };
    const inDoubtCycles = [...r.intended].filter((c) => !r.completed.has(c)).sort((a, b) => a - b);
    return {
      runId,
      lastCycle: r.lastCycle,
      nextCycle: r.lastCycle + 1,
      matched: r.matched,
      inDoubtCycles,
      blocked: inDoubtCycles.length > 0,
    };
  }

  return {
    ledgerPath, anchorPath, absoluteMax: ABSOLUTE_MAX,
    open, ensureFresh, invalidate, append, cycleState, runState,
    entries: () => ensureFresh().entries,
    headHash: () => ensureFresh().headHash,
    count: () => ensureFresh().entries.length,
    byteLength: () => ensureFresh().byteLength,
    epochCount: () => ensureFresh().epochCount,
    intentEpoch: (runId, cycle) => {
      ensureFresh();
      const slot = st.idx.get(runId + '#' + cycle);
      return slot ? slot.epoch : null;
    },
    recovery: () => lastRecovery,
    stats: () => ({ ...stats }),
    bounds: () => ({
      maxEntriesBeforeFullReverify: K_ENTRIES,
      maxBytesBeforeFullReverify: K_BYTES,
      maxOpsBeforeFullReverify: K_OPS,
    }),
  };
}
