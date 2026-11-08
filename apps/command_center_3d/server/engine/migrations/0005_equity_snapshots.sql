-- JEV V4.3 / PHASE 4 Step 2 - mark-to-market equity snapshots and trusted
-- verification provenance. FAIL CLOSED by construction.
--
-- Hard rules:
--  * A snapshot NEVER mutates paper_accounts.state_json / state_hash / version.
--    The trading replay chain (paper_server_events) stays byte-for-byte intact.
--  * mark_quality='unknown' rows are AUDIT ONLY and must never be used by the
--    20% drawdown gate. Missing real marks is a BLOCKER, not an approximation.
--  * Drawdown is per (session_id, generation). Losses of one independent
--    account are never offset by profits of another: the gate uses the WORST
--    account, never a summed equity curve.
--  * verified=1 on backtest/oos rows is NOT sufficient for release. A
--    verification_receipts row with a real trust anchor is also required.
--
-- Monetary / ratio columns use the same 1e8 fixed-point INTEGER convention as
-- 0003_fixed_point.sql and 0004_strategy_registry.sql. Timestamps ISO-8601 UTC.

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

-- Independent append-only strategy evidence chain with its own hash link.
-- Deliberately NOT written into paper_server_events: replay there reconstructs
-- account state from paper.server.account.snapshot events and must stay clean.
CREATE TABLE IF NOT EXISTS strategy_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  prev_hash TEXT,
  hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_events_strategy
  ON strategy_events(strategy_id, seq);

-- Replay protection for trusted service-to-service requests (HMAC path).
CREATE TABLE IF NOT EXISTS verification_nonces (
  nonce TEXT PRIMARY KEY NOT NULL,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

-- Provenance receipt. A `verified=1` backtest/OOS row without a receipt whose
-- trust anchor is REAL (GitHub Actions OIDC or an approved service HMAC run)
-- must never contribute to a release decision.
CREATE TABLE IF NOT EXISTS verification_receipts (
  run_id TEXT PRIMARY KEY NOT NULL,
  auth_method TEXT NOT NULL,
  trusted INTEGER NOT NULL DEFAULT 0 CHECK (trusted IN (0,1)),
  repository TEXT,
  workflow_ref TEXT,
  commit_sha TEXT,
  workflow_run_id TEXT,
  bundle_sha256 TEXT NOT NULL,
  engine_sha256 TEXT,
  strategy_source_sha256 TEXT,
  input_sha256 TEXT,
  actor TEXT,
  verified_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES research_runs(run_id)
);
