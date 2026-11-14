// JEV Phase B — D1 REAL ledger adapter (drop-in for phase-b/ledger.mjs mock).
//
// FAIL-CLOSED: requires REAL_D1_ACCOUNT_ID + REAL_D1_DATABASE_ID + REAL_D1_API_TOKEN.
// When unconfigured, the constructor throws NotConfiguredError. The adapter talks
// to a Cloudflare D1 binding via the standard `prepare().bind().run()` / `.first()`
// API. UNIQUE(nonce) + INSERT OR IGNORE gives atomic consumption with no TOCTOU;
// `changes === 0` maps to REPLAY. This is the production-correct path for D1.
//
// IMPORTANT: this module never instantiates network clients itself. The caller
// injects a D1 `client` (real binding in production, or an in-memory fake in the
// contract test). That injection boundary is what keeps it testable + fail-closed.

import { NotConfiguredError } from "./errors.mjs";

/**
 * Read + validate the real D1 configuration from the environment.
 */
export function loadRealD1Config(env = process.env) {
  const accountId = env.REAL_D1_ACCOUNT_ID;
  const databaseId = env.REAL_D1_DATABASE_ID;
  const apiToken = env.REAL_D1_API_TOKEN;
  const configured = !!(accountId && databaseId && apiToken);
  return Object.freeze({ accountId, databaseId, apiToken, configured });
}

/** The D1 schema the adapter expects. Apply once on a real D1 instance. */
export const REAL_D1_SCHEMA = `
CREATE TABLE IF NOT EXISTS auth_nonces (
  nonce        TEXT PRIMARY KEY UNIQUE,
  grant_id     TEXT NOT NULL,
  actor_hash   TEXT NOT NULL,
  strategy_id  TEXT NOT NULL,
  consumed_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_revocations (
  grant_id     TEXT PRIMARY KEY UNIQUE,
  revoked_by   TEXT NOT NULL,
  revoked_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_anchors (
  seq           INTEGER PRIMARY KEY AUTOINCREMENT,
  tip_hash      TEXT NOT NULL,
  anchored_at_ms INTEGER NOT NULL
);
`;

export class RealD1Ledger {
  /**
   * @param {object} [config]   loadRealD1Config() by default
   * @param {object} client     a Cloudflare D1 binding (or injected fake)
   */
  constructor(config, client) {
    this.config = config || loadRealD1Config();
    if (!this.config.configured) {
      throw new NotConfiguredError(
        "REAL_D1",
        "set REAL_D1_ACCOUNT_ID, REAL_D1_DATABASE_ID, REAL_D1_API_TOKEN",
      );
    }
    if (!client || typeof client.prepare !== "function") {
      throw new TypeError("RealD1Ledger requires a D1 client with prepare()");
    }
    this.client = client;
  }

  /** Atomic consume via INSERT OR IGNORE; changes===0 => REPLAY (no TOCTOU). */
  async consumeNonce({ nonce, grantId, actorHash, strategyId, nowMs }) {
    if (typeof nonce !== "string" || nonce.length === 0) {
      return { ok: false, code: "MALFORMED_FIELD_TYPE" };
    }
    const info = await this.client.prepare(
      "INSERT OR IGNORE INTO auth_nonces " +
      "(nonce,grant_id,actor_hash,strategy_id,consumed_at_ms) VALUES (?,?,?,?,?)",
    ).bind(nonce, grantId, actorHash, strategyId, nowMs).run();
    if (info.changes === 0) return { ok: false, code: "REPLAY", nonce };
    return { ok: true, code: "NONCE_CONSUMED", nonce };
  }

  async hasNonce(nonce) {
    const r = await this.client.prepare("SELECT 1 FROM auth_nonces WHERE nonce=?")
      .bind(nonce).first();
    return !!r;
  }

  async revokeGrant({ grantId, revokedBy, nowMs }) {
    const info = await this.client.prepare(
      "INSERT OR IGNORE INTO auth_revocations (grant_id,revoked_by,revoked_at_ms) VALUES (?,?,?)",
    ).bind(grantId, revokedBy, nowMs).run();
    if (info.changes === 0) return { ok: false, code: "ALREADY_REVOKED", grantId };
    return { ok: true, code: "REVOKED", grantId };
  }

  async isRevoked(grantId) {
    const r = await this.client.prepare("SELECT 1 FROM auth_revocations WHERE grant_id=?")
      .bind(grantId).first();
    return !!r;
  }

  async appendAnchor({ tipHash, nowMs }) {
    const info = await this.client.prepare(
      "INSERT INTO audit_anchors (tip_hash,anchored_at_ms) VALUES (?,?)",
    ).bind(tipHash, nowMs).run();
    return {
      seq: info.lastRowId ?? info.meta?.last_row_id,
      tipHash,
      anchoredAtMs: nowMs,
    };
  }

  async lastAnchor() {
    const r = await this.client.prepare(
      "SELECT seq,tip_hash,anchored_at_ms FROM audit_anchors ORDER BY seq DESC LIMIT 1",
    ).bind().first();
    return r || null;
  }

  async anchorCount() {
    const r = await this.client.prepare("SELECT COUNT(*) AS c FROM audit_anchors").bind().first();
    return r?.c ?? 0;
  }
}
