// JEV Phase B — F-19: external audit anchor (WORM) + startup recovery read +
// mismatch fail-closed + tamper/rollback detection.
//
// LOCAL MOCK of an external WORM (write-once-read-many) anchor service. It
// appends chain-tip hashes and NEVER allows update/delete. On startup the
// instance recomputes its in-memory chain tip and compares it to the most
// recent anchor; any mismatch is a fail-closed AUDIT_CHAIN_ANCHOR_MISMATCH.
//
// Production F-19 needs a real external anchor (D1 audit_anchor table or an
// RFC3161 timestamping / cold-read-only store) + an isolated recovery drill.
// Those are external-authorization items.

export const ANCHOR_CODES = Object.freeze({
  ANCHORED: "AUDIT_ANCHOR_WRITTEN",
  MISMATCH: "AUDIT_CHAIN_ANCHOR_MISMATCH",
  NO_ANCHOR: "AUDIT_ANCHOR_NONE",
  WORM_IMMUTABLE: "WORM_ANCHOR_IMMUTABLE",
});

/**
 * Local WORM anchor store. Append-only by construction: no update/delete API
 * is exposed (and the object is frozen). Each anchor records the chain tip
 * hash at a monotonic sequence number.
 */
export class WormAuditAnchor {
  #entries = [];
  #seq = 0;

  constructor() { Object.freeze(this); }

  /** Append a chain-tip hash. Returns the anchored entry. */
  anchor(tipHash, nowMs) {
    if (typeof tipHash !== "string" || tipHash.length === 0) {
      throw new TypeError("ANCHOR_TIP_HASH_REQUIRED");
    }
    const entry = Object.freeze({
      seq: this.#seq++,
      tipHash,
      anchoredAtMs: Number.isFinite(nowMs) ? nowMs : Date.now(),
    });
    this.#entries.push(entry);
    return entry;
  }

  last() { return this.#entries.length ? this.#entries[this.#entries.length - 1] : null; }
  count() { return this.#entries.length; }
  entries() { return this.#entries.map((e) => ({ ...e })); }

  /** WORM guarantee: no caller can mutate or delete an anchor. */
  update() { return false; }
  delete() { return false; }
  clear() { return false; }
}

/**
 * Startup recovery verification. Recomputes the in-memory chain tip and
 * compares to the last external anchor. Unknown audit state → fail closed.
 *
 * @param {object} opts
 * @param {WormAuditAnchor} opts.worm  external anchor store
 * @param {string} opts.recomputedTipHash  recomputed in-memory chain tip
 * @param {boolean} opts.auditStateKnown  is the in-memory chain readable?
 */
export function verifyAnchorOnStartup({ worm, recomputedTipHash, auditStateKnown = true }) {
  if (!worm || typeof worm.last !== "function") {
    return { ok: false, code: ANCHOR_CODES.MISMATCH, detail: "anchor store unavailable" };
  }
  if (!auditStateKnown) {
    // We cannot even read our own audit chain — fail closed.
    return { ok: false, code: ANCHOR_CODES.MISMATCH, detail: "audit chain unreadable" };
  }
  const last = worm.last();
  if (!last) {
    // No prior anchor. If our in-memory chain is non-empty, the anchor was
    // lost/rolled back (fail closed); only a truly empty chain (first boot)
    // is acceptable.
    if (recomputedTipHash) {
      return { ok: false, code: ANCHOR_CODES.MISMATCH, detail: "anchor missing but audit chain non-empty (rollback/loss)" };
    }
    return { ok: true, code: ANCHOR_CODES.NO_ANCHOR, detail: "no prior anchor" };
  }
  if (last.tipHash !== recomputedTipHash) {
    return {
      ok: false,
      code: ANCHOR_CODES.MISMATCH,
      detail: { anchored: last.tipHash, recomputed: recomputedTipHash, seq: last.seq },
    };
  }
  return { ok: true, code: "AUDIT_ANCHOR_VERIFIED", detail: { seq: last.seq } };
}
