-- JEV Phase B — durable authorization ledger + external audit anchor (NEW).
--
-- LOCATION RULE: lives in server/ai/migrations (outside the sha256-anchored
-- frozen engine tree). New file; does not touch any frozen migration.
--
-- WHAT THIS ADDS (mirrors design doc §4 D1 + §6 F-19)
--   grant_consumption  one row per consumed nonce (UNIQUE nonce => atomic,
--                      TOCTOU-free replay protection)
--   grant_revocation   one row per revoked grant (UNIQUE grant_id)
--   audit_anchor       append-only WORM anchor of the audit chain tip
--                      (no update/delete statements are provided)
--
-- All statements are idempotent so `wrangler d1 migrations apply` is safe.
-- HARD RULE: no credential/secret column may be added to any of these tables.

CREATE TABLE IF NOT EXISTS grant_consumption (
  nonce        TEXT PRIMARY KEY NOT NULL,         -- UNIQUE => atomic INSERT, conflict = REPLAY
  consumed_at_ms INTEGER NOT NULL,
  grant_id     TEXT NOT NULL,
  actor_hash   TEXT NOT NULL,
  strategy_id  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_grant_consumption_grant
  ON grant_consumption(grant_id);

CREATE TABLE IF NOT EXISTS grant_revocation (
  grant_id     TEXT PRIMARY KEY NOT NULL,         -- UNIQUE => idempotent revoke
  revoked_at_ms INTEGER NOT NULL,
  revoked_by   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_anchor (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,  -- append-only, monotonic
  tip_hash     TEXT NOT NULL,
  anchored_at_ms INTEGER NOT NULL
);

-- No UPDATE / DELETE statements are ever issued against audit_anchor; the
-- WORM guarantee is enforced by the application layer (WormAuditAnchor) and the
-- absence of any mutation DDL here.
