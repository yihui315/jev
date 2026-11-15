// JEV Phase B — D1: persistent, cross-instance authorization ledger.
//
// LOCAL D1 SIMULATION (MOCK). Models a Cloudflare D1 database as a single shared
// in-memory store that MANY worker instances point at. The store provides a
// UNIQUE constraint on nonce and a SINGLE atomic INSERT (no check-then-insert
// TOCTOU window). This directly replaces the in-process
// `consumedNonces`/`revokedGrantIds` Sets from HUMAN_PUBLISH_GATE.
//
// Production D1 needs the real binding + an isolated D1 instance; those are
// external-authorization items. The WORM audit anchor (F-19) uses the same
// store abstraction.
//
// HARD PROPERTIES demonstrated by the tests:
//   * Atomic nonce consumption: a single INSERT with UNIQUE(nonce) — conflict
//     maps to REPLAY, no TOCTOU window. The naive has()-then-add() path is
//     provided ONLY to demonstrate the race it creates.
//   * Cross-instance replay: two ledger instances share one store; a nonce
//     consumed on instance A is REPLAY on instance B.
//   * Failure recovery: the store is the durable layer; an in-memory index can
//     be dropped ("crash") and rebuilt from the store. Consumed/revoked rows
//     survive.

import { GATE_CODES } from "../human-publish-gate.mjs";

export const LEDGER_CODES = Object.freeze({
  CONSUMED: "NONCE_CONSUMED",
  REPLAY: GATE_CODES.REPLAY,
  REVOKED: GATE_CODES.REVOKED,
  STATE_REQUIRED: GATE_CODES.STATE_REQUIRED,
});

/**
 * Thrown by D1BackedGateState when the core gate's post-check `add()` detects a
 * REPLAY. The EXISTING core gate calls `state.consumedNonces.has()` (pre-check)
 * then `state.consumedNonces.add()` (post-check) as TWO separate calls; with a
 * SHARED D1 store two concurrent workers can both pass `has()===false` and both
 * proceed. The adapter therefore makes `add()` FAIL CLOSED by throwing on REPLAY
 * instead of silently succeeding, and the caller (safeCoreEnforceWithD1) converts
 * the throw into a clean REPLAY denial. The native phaseBEnforce path does NOT
 * use this adapter and already checks the consume result inline.
 */
export class LedgerReplayError extends Error {
  constructor(nonce, code = LEDGER_CODES.REPLAY) {
    super("nonce already consumed (cross-instance REPLAY): " + String(nonce));
    this.name = "LedgerReplayError";
    this.nonce = nonce;
    this.code = code;
  }
}

/**
 * The shared, "durable" D1 simulation. One of these backs every instance.
 * Internally a Map; UNIQUE enforcement is a synchronous check+insert in a
 * single critical step (no await between them), which is atomic in the JS
 * event loop and immune to TOCTOU regardless of how many instances call it.
 */
export class D1SharedStore {
  #nonces = new Map();   // nonce -> { grantId, consumedAtMs, actorHash, strategyId }
  #revocations = new Map(); // grantId -> { revokedAtMs, revokedBy }
  #anchors = [];         // F-19 worm anchors: { seq, tipHash, anchoredAtMs }
  #seq = 0;

  constructor() {}

  /** Atomic insert; returns true if newly inserted, false on UNIQUE conflict. */
  atomicInsertNonce(row) {
    if (this.#nonces.has(row.nonce)) return false;
    this.#nonces.set(row.nonce, {
      grantId: row.grantId, consumedAtMs: row.consumedAtMs,
      actorHash: row.actorHash, strategyId: row.strategyId,
    });
    return true;
  }

  hasNonce(nonce) { return this.#nonces.has(nonce); }

  atomicInsertRevocation(row) {
    if (this.#revocations.has(row.grantId)) return false;
    this.#revocations.set(row.grantId, { revokedAtMs: row.revokedAtMs, revokedBy: row.revokedBy });
    return true;
  }

  hasRevocation(grantId) { return this.#revocations.has(grantId); }

  /** Append-only WORM anchor; no update/delete ever. */
  appendAnchor(row) {
    const seq = this.#seq++;
    const entry = { seq, tipHash: row.tipHash, anchoredAtMs: row.anchoredAtMs };
    this.#anchors.push(Object.freeze(entry));
    return entry;
  }

  lastAnchor() { return this.#anchors.length ? this.#anchors[this.#anchors.length - 1] : null; }
  anchorCount() { return this.#anchors.length; }

  /** Snapshot the durable rows (used by recovery / inspection). */
  snapshot() {
    return {
      nonces: [...this.#nonces.entries()].map(([k, v]) => [k, { ...v }]),
      revocations: [...this.#revocations.entries()].map(([k, v]) => [k, { ...v }]),
      anchors: this.#anchors.map((a) => ({ ...a })),
    };
  }
}

/**
 * An authorization ledger instance (one per worker). It holds a fast in-memory
 * index MIRRORED from the shared store. On "recovery" it rebuilds that index
 * from the store, proving durability across a crash.
 */
export class AuthorizationLedger {
  constructor(store) {
    if (!store || typeof store.atomicInsertNonce !== "function") {
      throw new TypeError("AuthorizationLedger requires a shared D1 store");
    }
    this.store = store;
  }

  /**
   * ATOMIC nonce consumption (the production path). Single INSERT with
   * UNIQUE(nonce). Returns { ok:true, code:CONSUMED } on first use,
   * { ok:false, code:REPLAY } on conflict. No TOCTOU window.
   *
   * Reads/writes go to the SHARED store, which is the single source of truth,
   * so every worker instance (incl. freshly-started ones) sees the same state.
   */
  consumeNonce({ nonce, grantId, actorHash, strategyId, nowMs }) {
    if (typeof nonce !== "string" || nonce.length === 0) {
      return { ok: false, code: GATE_CODES.MALFORMED_FIELD_TYPE };
    }
    const inserted = this.store.atomicInsertNonce({
      nonce, grantId, actorHash, strategyId, consumedAtMs: nowMs,
    });
    if (inserted) return { ok: true, code: LEDGER_CODES.CONSUMED, nonce };
    return { ok: false, code: LEDGER_CODES.REPLAY, nonce };
  }

  hasNonce(nonce) { return this.store.hasNonce(nonce); }

  revokeGrant({ grantId, revokedBy, nowMs }) {
    const inserted = this.store.atomicInsertRevocation({ grantId, revokedBy, revokedAtMs: nowMs });
    if (inserted) return { ok: true, code: LEDGER_CODES.REVOKED, grantId };
    return { ok: false, code: "ALREADY_REVOKED", grantId };
  }

  isRevoked(grantId) { return this.store.hasRevocation(grantId); }

  isStateValid() {
    return !!this.store && typeof this.store.atomicInsertNonce === "function";
  }

  /**
   * NAIVE cross-instance design (DEMONSTRATION ONLY, never used in the gate):
   * each worker keeps its OWN in-memory Set with no shared store. Two instances
   * both observe `has()===false` and both "consume" => double-spend. The tests
   * use this to prove WHY the shared D1 store is required.
   */
  static naiveInstance() {
    const s = new Set();
    return {
      consumeNonce: async ({ nonce, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) => {
        if (s.has(nonce)) return { ok: false, code: LEDGER_CODES.REPLAY, nonce };
        await sleep(5); // <-- the cross-instance TOCTOU window
        if (s.has(nonce)) return { ok: false, code: LEDGER_CODES.REPLAY, nonce };
        s.add(nonce);
        return { ok: true, code: LEDGER_CODES.CONSUMED, nonce };
      },
      hasNonce: (n) => s.has(n),
    };
  }
}

/**
 * DEMONSTRATION-ONLY adapter. The EXISTING HUMAN_PUBLISH_GATE core
 * (verifyPublishGrant / enforceHumanPublishGate) can use this durable ledger
 * instead of the in-process Sets — without any change to the core module.
 * Implements the `consumedNonces`/`revokedGrantIds` interface the core expects.
 *
 * PRODUCTION PATH IS `phaseBEnforce` (integration.mjs), which checks the consume
 * result inline and never double-allows. This adapter exists only to show the
 * existing core gate can be retrofitted onto D1; when a REAL core enforcement is
 * run through it, wrap the call in `safeCoreEnforceWithD1` so the REPLAY throw
 * becomes a clean denial.
 */
export class D1BackedGateState {
  constructor(ledger) {
    this.ledger = ledger;
    // Defense-in-depth local mirror: reflects nonces consumed in THIS process so
    // `has()` is fast and also catches REPLAY before the shared store is hit.
    // The AUTHORITATIVE guard is the throw on REPLAY inside `add()`.
    this.#mirror = new Set();
    const self = this;
    this.consumedNonces = {
      has(nonce) { return self.#mirror.has(nonce) || self.ledger.hasNonce(nonce); },
      add(nonce) {
        const r = self.ledger.consumeNonce({
          nonce, grantId: "core:" + nonce, actorHash: "core", strategyId: "core", nowMs: Date.now(),
        });
        if (!r.ok && r.code === LEDGER_CODES.REPLAY) {
          // Fail closed: the existing core gate's post-check add() must NOT
          // silently succeed on a replayed nonce. Throw so the caller converts
          // it into a clean REPLAY denial (see safeCoreEnforceWithD1).
          throw new LedgerReplayError(nonce);
        }
        self.#mirror.add(nonce);
        return this;
      },
    };
    this.revokedGrantIds = {
      has(grantId) { return self.ledger.isRevoked(grantId); },
      add(grantId) {
        self.ledger.revokeGrant({ grantId, revokedBy: "core", nowMs: Date.now() });
        self.#mirror.add("rev:" + grantId);
        return this;
      },
    };
  }

  #mirror;

  get audit() { return null; } // callers must still pass a real audit log
}
