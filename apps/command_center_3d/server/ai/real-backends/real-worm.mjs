// JEV Phase B — F-19 REAL WORM audit anchor adapter (drop-in for
// phase-b/audit-anchor.mjs mock).
//
// FAIL-CLOSED: requires REAL_WORM_ENDPOINT + REAL_WORM_API_KEY. When unconfigured
// the constructor throws NotConfiguredError. The adapter writes chain-tip hashes
// to an EXTERNAL write-once store (RFC3161 timestamping / object-lock cold store
// / a dedicated append-only ledger service). The caller injects a `client` so the
// module itself never opens network connections.

import { NotConfiguredError } from "./errors.mjs";

/**
 * Read + validate the real WORM configuration from the environment.
 */
export function loadRealWormConfig(env = process.env) {
  const endpoint = env.REAL_WORM_ENDPOINT;
  const apiKey = env.REAL_WORM_API_KEY;
  const configured = !!(endpoint && apiKey);
  return Object.freeze({ endpoint, apiKey, configured });
}

export class RealWormAnchor {
  /**
   * @param {object} [config]  loadRealWormConfig() by default
   * @param {object} client    an external WORM client with append/last/count
   */
  constructor(config, client) {
    this.config = config || loadRealWormConfig();
    if (!this.config.configured) {
      throw new NotConfiguredError("REAL_WORM", "set REAL_WORM_ENDPOINT, REAL_WORM_API_KEY");
    }
    if (!client || typeof client.append !== "function") {
      throw new TypeError("RealWormAnchor requires a WORM client with append()");
    }
    this.client = client;
  }

  /** Append-only write-once anchor. Returns the external receipt. */
  async anchor(tipHash, nowMs) {
    if (typeof tipHash !== "string" || tipHash.length === 0) {
      throw new TypeError("ANCHOR_TIP_HASH_REQUIRED");
    }
    return this.client.append({ tipHash, anchoredAtMs: nowMs });
  }

  async last() { return this.client.last(); }
  async count() { return this.client.count(); }

  /**
   * Startup recovery verification (F-19). Recomputed in-memory chain tip is
   * compared to the last external anchor; mismatch => fail-closed.
   */
  async verifyOnStartup({ recomputedTipHash, auditStateKnown = true }) {
    if (!auditStateKnown) {
      return { ok: false, code: "AUDIT_CHAIN_ANCHOR_MISMATCH", detail: "audit chain unreadable" };
    }
    const last = await this.client.last();
    if (!last) {
      if (recomputedTipHash) {
        return { ok: false, code: "AUDIT_CHAIN_ANCHOR_MISMATCH", detail: "anchor missing but chain non-empty" };
      }
      return { ok: true, code: "AUDIT_ANCHOR_NONE" };
    }
    if (last.tipHash !== recomputedTipHash) {
      return {
        ok: false,
        code: "AUDIT_CHAIN_ANCHOR_MISMATCH",
        detail: { anchored: last.tipHash, recomputed: recomputedTipHash },
      };
    }
    return { ok: true, code: "AUDIT_ANCHOR_VERIFIED" };
  }
}
