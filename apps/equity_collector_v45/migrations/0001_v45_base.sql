-- =============================================================================
-- V45 Equity Collector — ISOLATED TEST schema (SQLite / D1 shim only).
--
-- HARD RULES:
--   * This schema is applied ONLY to a local isolated test DB. It is NEVER
--     applied to jev-paper-staging, Night Researcher production D1, or any
--     cloud database by this worker.
--   * paper_equity_snapshots below is FIELD/FIELD/CONSTRAINT-compatible with
--     the FROZEN migration 0005_equity_snapshots.sql (do not edit that file).
--   * The collector NEVER writes paper_accounts, paper_orders, paper_fills or
--     paper_server_events. Those tables exist here only so the zero-write
--     spy in T5 can prove no mutation happened.
--   * All money / ratio columns are 1e8 fixed-point INTEGER (see decimal.mjs).
-- =============================================================================

-- ---- minimal frozen-compatible ledger tables (setup fixtures only) ----------
CREATE TABLE IF NOT EXISTS paper_sessions (
  session_id TEXT PRIMARY KEY NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_accounts (
  session_id TEXT PRIMARY KEY NOT NULL,
  version INTEGER NOT NULL,
  state_json TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  last_command_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_server_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  command_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ---- FROZEN-COMPATIBLE snapshot table (verbatim from 0005) -----------------
CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
  snapshot_id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT NOT NULL,
  generation INTEGER NOT NULL DEFAULT 1,
  account_version INTEGER NOT NULL,
  state_hash TEXT NOT NULL,
  equity INTEGER NOT NULL,              -- 1e8 fixed point
  cash INTEGER NOT NULL,
  market_value INTEGER NOT NULL,
  unrealized_pnl INTEGER NOT NULL,
  realized_pnl INTEGER NOT NULL,
  equity_peak INTEGER NOT NULL,
  drawdown INTEGER NOT NULL,           -- 1e8 fixed point ratio
  mark_quality TEXT NOT NULL CHECK (mark_quality IN ('verified','unknown')),
  marks_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'binance-public-ticker',
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES paper_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_account
  ON paper_equity_snapshots(session_id, generation, created_at);
CREATE INDEX IF NOT EXISTS idx_equity_snapshots_quality
  ON paper_equity_snapshots(mark_quality, created_at);

-- ---- NEW: V45 provenance (foreign key -> frozen snapshot table) ------------
-- One row per snapshot. The frozen table is NOT modified; every extra audit
-- field lives here. INSERT OR IGNORE makes the whole cycle idempotent.
CREATE TABLE IF NOT EXISTS v45_snapshot_provenance (
  snapshot_id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  cycle_key TEXT NOT NULL,
  collector_version TEXT NOT NULL,
  engine_sha256 TEXT NOT NULL,
  formula_version TEXT NOT NULL,
  input_state_hash TEXT NOT NULL,
  input_marks_json TEXT NOT NULL DEFAULT '{}',
  initial_cash_fp INTEGER,             -- 1e8 fixed point, replay basis
  source_host TEXT NOT NULL,           -- api.binance.us
  endpoint TEXT NOT NULL,              -- /api/v3/ticker/price
  source_url TEXT NOT NULL,
  http_status INTEGER,                 -- null on network failure
  payload_sha256 TEXT,                 -- sha256 of the raw HTTP body
  price_ts TEXT NOT NULL,              -- ISO-8601 UTC price timestamp
  fetched_at TEXT NOT NULL,            -- ISO-8601 UTC local fetch time
  freshness_ms INTEGER NOT NULL,       -- age of the price
  freshness_verdict TEXT NOT NULL CHECK (freshness_verdict IN ('FRESH','STALE','UNKNOWN')),
  clock_skew_ms INTEGER,               -- |localNow - serverTime|, null if /time failed
  data_quality TEXT NOT NULL CHECK (data_quality IN ('VERIFIED','UNKNOWN')),
  mark_quality TEXT NOT NULL CHECK (mark_quality IN ('verified','unknown')),
  missing_marks_json TEXT NOT NULL DEFAULT '[]',
  time_probe_ok INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,                     -- honest failure reason, never fabricated
  latency_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(snapshot_id) REFERENCES paper_equity_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_v45_prov_session_cycle
  ON v45_snapshot_provenance(session_id, cycle_key);
CREATE INDEX IF NOT EXISTS idx_v45_prov_quality
  ON v45_snapshot_provenance(data_quality, fetched_at);
