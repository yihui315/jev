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

// THE OPERATIONAL LIMIT, BELOW THE CEILING. Two different numbers with two
// different jobs, and the distinction is the whole point of `⑯c`:
//   ABSOLUTE_MAX — a hard ceiling. Nothing may override it (not intent, not
//                  config, not env). A record longer than this is a bug or an
//                  attack, never a configuration choice.
//   CURRENT_MAX  — what we write today. Lowering it must never make yesterday's
//                  ledger unreadable: records between CURRENT_MAX and
//                  ABSOLUTE_MAX are legal HISTORY. Refusing them on read would
//                  turn "we tightened the limit" into "we bricked the ledger".
// Anything larger than CURRENT_MAX is CLAMPED on the way in, never silently.
export const CURRENT_MAX = 1024;

// The bounded re-verify window. ONE number, three named uses, deliberately NOT
// independent: the previous version reused a single `K` as both a read count and
// a record count, so "the window" silently changed width the moment the hot path
// gained one more read. Naming the unit is what stops that drift.
//
//   MAX_ENTRIES_BEFORE_FULL_REVERIFY  records appended since the last full verify
//   MAX_BYTES_BEFORE_FULL_REVERIFY    bytes appended since the last full verify
//   MAX_OPS_BEFORE_FULL_REVERIFY      cursor reads *including* appends — without
//                                     it, a term that only reads would never
//                                     re-verify
//
// Reaching ANY of the three forces a full re-verify. So does a cursor whose
// byteLength or mtimeMs stops matching the file, and so does any lease handover
// (see invalidate()).
//
// WHAT THESE BOUNDS ARE, EXACTLY: a DETECTION-DELAY CEILING for an attacker who
// can forge mtime. Someone who can rewrite the ledger can also call `utimes`, so
// mtime is a detection aid, not a security boundary — it collapses the window to
// "the next read" for an ordinary edit, and it collapses to nothing against a
// deliberate one. The boundaries that survive a deliberate attacker are the two
// full passes: one per lease acquisition, and one every K entries / M bytes.
// Do NOT relax K on the strength of mtime having been added.
//
// WHICH BOUND ACTUALLY BINDS: measured rows are 230-307 bytes, so 256 entries is
// ~68-79 KiB — well under M. The ENTRY bound therefore binds first and M is a
// backstop that only matters if row width balloons. That is asserted, not
// commented, in the suite (`㉔`), because a comment about row width goes stale
// the day someone adds a field.
export const MAX_ENTRIES_BEFORE_FULL_REVERIFY = 256;
export const MAX_BYTES_BEFORE_FULL_REVERIFY = 262144;              // 256 KiB
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

function parseLedger(body, absoluteMax) {
  const text = body.toString('utf8');
  const out = [];
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().length === 0) continue;
    // Measured on BYTES, not characters: the ceiling is about what a record costs
    // on disk, and a line of CJK is three times its length in characters.
    const lineBytes = Buffer.byteLength(line, 'utf8');
    if (absoluteMax && lineBytes > absoluteMax) {
      throw fail('LEDGER_ENTRY_OVER_ABSOLUTE_MAX',
        'line ' + i + ' is ' + lineBytes + ' bytes, over the hard ceiling of ' + absoluteMax
        + '. This is NOT a permanent exception for old records: no set of values may override '
        + 'the ceiling, so the record is refused and the ledger is unreadable until a human '
        + 'inspects it.', { line: i, bytes: lineBytes, absoluteMax });
    }
    try { out.push(JSON.parse(line)); }
    catch (_e) { throw new Error('LEDGER_PARSE_FAILED at line ' + i); }
  }
  return out;
}

export function createLedgerIo(deps, opts = {}) {
  const { fsImpl, baseDir, nowMs } = deps;
  const ledgerPath = resolvePath(baseDir, FILES.ledger);
  const anchorPath = resolvePath(baseDir, FILES.anchor);
  // An unknown option key used to be ignored silently. That is not a style
  // problem: a caller that passes a renamed option still reads as "I bounded the
  // window", and the module quietly uses the default. Measured consequence — a
  // new module paired with an old test file made every window assertion pass
  // while the window was 64x wider than the caller believed. Refuse the key.
  const ALLOWED_OPTS = [
    'maxEntriesBeforeFullReverify', 'maxBytesBeforeFullReverify', 'maxOpsBeforeFullReverify',
    'currentMax',
  ];
  for (const k of Object.keys(opts)) {
    if (!ALLOWED_OPTS.includes(k)) {
      throw fail('LEDGER_IO_UNKNOWN_OPT',
        'unknown option "' + k + '" (allowed: ' + ALLOWED_OPTS.join(', ') + ')');
    }
  }
  // The ceiling is enforced AT LOAD, not documented. A caller may lower the
  // operational limit; nothing may raise it past ABSOLUTE_MAX, and a caller that
  // tries gets an error instead of a silently-wider record.
  const currentMax = opts.currentMax ?? CURRENT_MAX;
  if (!Number.isInteger(currentMax) || currentMax <= 0) {
    throw fail('CURRENT_MAX_INVALID', 'currentMax must be a positive integer, got ' + String(currentMax));
  }
  if (currentMax > ABSOLUTE_MAX) {
    throw fail('CURRENT_MAX_EXCEEDS_ABSOLUTE',
      'currentMax=' + currentMax + ' is above the hard ceiling ABSOLUTE_MAX=' + ABSOLUTE_MAX
      + '. The ceiling is not overridable by intent, config or env.');
  }
  const K_ENTRIES = opts.maxEntriesBeforeFullReverify ?? MAX_ENTRIES_BEFORE_FULL_REVERIFY;
  const K_BYTES = opts.maxBytesBeforeFullReverify ?? MAX_BYTES_BEFORE_FULL_REVERIFY;
  const K_OPS = opts.maxOpsBeforeFullReverify ?? MAX_OPS_BEFORE_FULL_REVERIFY;

  let st = null;         // see open()
  let termStartCount = 0;
  let termStartBytes = 0;
  let readsSinceVerify = 0;
  const stats = {
    fullVerifies: 0, appends: 0, forcedReverifies: 0, adopts: 0,
    truncations: 0, discardedBytes: 0, invalidations: 0, shortages: 0, headMismatches: 0,
    clamps: 0,
  };
  // Bounded ring of clamp events. A clamp that is not recorded is indistinguishable
  // from a payload that was accepted — and the caller's data was replaced by a
  // digest, so "we changed what you asked us to store" must always be visible.
  const clampRecords = [];
  let lastRecovery = null;

  function exists(p) { return fsImpl.existsSync(p); }
  // The cursor records BOTH byteLength and mtimeMs, because length alone cannot
  // see a same-length in-place rewrite.
  function statOf(p) { return exists(p) ? fsImpl.statSync(p) : { byteLength: 0, mtimeMs: 0 }; }

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
      entries = parseLedger(body, ABSOLUTE_MAX);
      verifyChain(entries);
    } catch (e) {
      if (e && typeof e.code === 'string' && e.code.startsWith('LEDGER_')) throw e;
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
      } else if (body.length === anchor.byteLength) {
        // SAME LENGTH IS NOT SAME HISTORY. The anchor already stores `count` and
        // `headHash`; until this branch existed they were only TYPE-CHECKED and
        // never COMPARED — so the anchor constrained length and nothing else, and
        // a rewrite that recomputed the whole chain to a byte count could not be
        // seen. "Stored but never compared" is more expensive than absent: it
        // reads as a binding that exists.
        //
        // WHAT THIS DOES AND DOES NOT BUY (do not merge these two sentences):
        //   * it catches a ledger rewritten without a matching rewrite of the
        //     anchor — including a naive full-chain rehash, which is exactly the
        //     kill chain of `SHORT_TRUNCATED` minus the length change;
        //   * it does NOT stop an attacker who can also rewrite the anchor. Both
        //     files live in the same trust domain, so this closes a SUBSET, it
        //     does not make the ledger tamper-proof. That needs an external
        //     anchor outside the writer's reach (F-19), which does not exist yet.
        // A crash between append and anchor makes the ledger LONGER, which is the
        // branch above — so this branch cannot misfire on crash recovery.
        const head = entries.length ? entries[entries.length - 1]._hash : GENESIS;
        if (entries.length !== anchor.count || head !== anchor.headHash) {
          stats.headMismatches++;
          throw fail('LEDGER_ANCHOR_HEAD_MISMATCH',
            'the ledger is exactly as long as the anchor expects but is not the anchored history: '
            + 'records=' + entries.length + ' (anchor says ' + anchor.count + '), head='
            + String(head).slice(0, 12) + '... (anchor says ' + String(anchor.headHash).slice(0, 12) + '...)',
            { recordsNow: entries.length, recordsAnchored: anchor.count });
        }
      }
    }

    // mtime is read AFTER the torn-tail truncation above, so the cursor records
    // the timestamp of the bytes that are now on disk, not of the ones we cut.
    const opened = statOf(ledgerPath);
    st = {
      entries,
      headHash: entries.length ? entries[entries.length - 1]._hash : GENESIS,
      byteLength: body.length,
      mtimeMs: opened.mtimeMs,
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
    const cur = statOf(ledgerPath);
    if (cur.byteLength !== st.byteLength || cur.mtimeMs !== st.mtimeMs) return open();
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
  function buildLine(business) {
    const rest = { ...business, _prev: st.headHash };
    const entry = { ...rest, _hash: sha256(canonicalize(rest)) };
    return { entry, line: canonicalize(entry) + '\n' };
  }

  function recordClamp(rec) {
    stats.clamps++;
    clampRecords.push(rec);
    if (clampRecords.length > 32) clampRecords.shift();
    if (typeof deps.logClamp === 'function') deps.logClamp(rec);
  }

  function append(business, opts2 = {}) {
    ensureFresh();
    if (!opts2.skipBound) checkBounds();

    // WRITE-SIDE CLAMP, AND NEVER SILENTLY. A record over CURRENT_MAX has its
    // payload replaced by {clamped, digest, bytes} and the event is recorded with
    // requested/applied. The alternative — accepting it — makes the operational
    // limit a comment; the alternative — throwing — turns a large cycle result
    // into a dead supervisor. What is not allowed is doing either without a trace.
    let built = buildLine(business);
    let bytes = Buffer.byteLength(built.line, 'utf8');
    if (bytes > currentMax) {
      const requested = bytes;
      const payload = business.payload;
      const clamped = {
        ...business,
        payload: {
          clamped: true,
          digest: sha256(canonicalize(payload)),
          bytes: Buffer.byteLength(canonicalize(payload), 'utf8'),
        },
      };
      built = buildLine(clamped);
      bytes = Buffer.byteLength(built.line, 'utf8');
      recordClamp({
        requested, applied: bytes, kind: String(business.kind),
        runId: business.runId, cycle: business.cycle, currentMax,
      });
      if (bytes > currentMax) {
        // Not clampable under the limit (e.g. a runaway runId). The ceiling still
        // governs what may reach the disk at all.
        throw fail('LEDGER_ENTRY_OVER_CURRENT_MAX',
          'CURRENT_MAX_CLAMPED requested=' + requested + ' applied=' + bytes
          + ' currentMax=' + currentMax + ' kind=' + String(business.kind)
          + ' — still over the operational limit after clamping');
      }
    }
    const entry = built.entry;
    const line = built.line;
    if (bytes > ABSOLUTE_MAX) {
      throw fail('LEDGER_ENTRY_OVER_ABSOLUTE_MAX',
        'bytes=' + bytes + ' max=' + ABSOLUTE_MAX);
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
    // Take the new length and timestamp FROM THE FILESYSTEM rather than by
    // arithmetic: our own append moved both, and a cursor that computes them
    // instead of reading them can drift from reality by one field forever.
    const after = statOf(ledgerPath);
    st.byteLength = after.byteLength;
    st.mtimeMs = after.mtimeMs;
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
    ledgerPath, anchorPath, absoluteMax: ABSOLUTE_MAX, currentMax,
    clamps: () => clampRecords.slice(),
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
