-- JEV V4.2 PHASE 4 — strategy domain persistence.
--
-- Design rule: FAIL CLOSED. Nothing in these tables may be interpreted as a
-- passing backtest / OOS result unless `verified = 1`, and `verified` can only
-- be set by a server-side recomputation from raw trades (see
-- functions/_lib/strategy-registry.mjs). Client-supplied metrics are never
-- written to backtest_results / oos_results by the HTTP API.
--
-- Monetary / ratio values use the same 1e8 fixed-point INTEGER convention as
-- 0003_fixed_point.sql. Timestamps are ISO-8601 UTC TEXT.
-- All statements are idempotent so `wrangler d1 migrations apply` is safe.

CREATE TABLE IF NOT EXISTS strategy_versions (
  strategy_id TEXT PRIMARY KEY NOT NULL,
  version TEXT NOT NULL,
  name TEXT NOT NULL,
  family TEXT NOT NULL,
  source TEXT NOT NULL,                 -- e.g. 'python:strategies.py'
  source_sha256 TEXT NOT NULL,          -- real sha256 of the strategy source file
  params_json TEXT NOT NULL DEFAULT '{}',
  lifecycle TEXT NOT NULL DEFAULT 'candidate'
    CHECK (lifecycle IN ('candidate','backtest','oos','paper','suspended','retired')),
  frozen INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs (
  run_id TEXT PRIMARY KEY NOT NULL,
  strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  source TEXT NOT NULL,                 -- 'python-backtest-engine' | 'six-role-agent'
  status TEXT NOT NULL DEFAULT 'recorded'
    CHECK (status IN ('recorded','verified','failed','suspended')),
  symbols_json TEXT NOT NULL DEFAULT '[]',
  window_from TEXT,
  window_to TEXT,
  input_sha256 TEXT,                    -- fingerprint of the real input dataset
  engine_sha256 TEXT,                   -- fingerprint of the backtest engine source
  note TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(strategy_id) REFERENCES strategy_versions(strategy_id)
);

CREATE TABLE IF NOT EXISTS backtest_results (
  result_id TEXT PRIMARY KEY NOT NULL,
  run_id TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  strategy_pass INTEGER NOT NULL DEFAULT 0 CHECK (strategy_pass IN (0,1)),
  verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
  verification_note TEXT,
  computed_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES research_runs(run_id),
  FOREIGN KEY(strategy_id) REFERENCES strategy_versions(strategy_id)
);

CREATE TABLE IF NOT EXISTS oos_results (
  result_id TEXT PRIMARY KEY NOT NULL,
  run_id TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  folds INTEGER NOT NULL DEFAULT 0,
  oos_trades INTEGER NOT NULL DEFAULT 0,
  oos_win_rate INTEGER NOT NULL DEFAULT 0,      -- 1e8 fixed point
  oos_profit_factor INTEGER NOT NULL DEFAULT 0, -- 1e8 fixed point
  oos_pass INTEGER NOT NULL DEFAULT 0 CHECK (oos_pass IN (0,1)),
  verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
  verification_note TEXT,
  computed_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES research_runs(run_id),
  FOREIGN KEY(strategy_id) REFERENCES strategy_versions(strategy_id)
);

CREATE TABLE IF NOT EXISTS risk_rejections (
  rejection_id TEXT PRIMARY KEY NOT NULL,
  strategy_id TEXT NOT NULL,
  run_id TEXT,
  gate TEXT NOT NULL,                    -- e.g. 'strategy.release', 'auto.pause'
  reason TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_performance (
  day TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  trades INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  gross_pnl INTEGER NOT NULL DEFAULT 0,   -- 1e8 fixed point
  fees INTEGER NOT NULL DEFAULT 0,        -- 1e8 fixed point
  net_pnl INTEGER NOT NULL DEFAULT 0,     -- 1e8 fixed point
  win_rate INTEGER NOT NULL DEFAULT 0,    -- 1e8 fixed point
  max_drawdown_pct INTEGER NOT NULL DEFAULT 0, -- 1e8 fixed point
  source TEXT NOT NULL DEFAULT 'paper_fills',
  computed_at TEXT NOT NULL,
  PRIMARY KEY(day, strategy_id)
);

CREATE INDEX IF NOT EXISTS idx_research_runs_strategy ON research_runs(strategy_id, created_at);
CREATE INDEX IF NOT EXISTS idx_backtest_results_run ON backtest_results(run_id, strategy_id);
CREATE INDEX IF NOT EXISTS idx_oos_results_run ON oos_results(run_id, strategy_id);
CREATE INDEX IF NOT EXISTS idx_risk_rejections_strategy ON risk_rejections(strategy_id, created_at);

-- Real, verifiable seed: the two strategies actually implemented in the
-- repository. Hashes are sha256 of strategies.py at the commit introducing
-- this migration. lifecycle stays 'candidate' and verified stays 0 in all
-- result tables: registration is NOT evidence of a passing backtest/OOS.
INSERT OR IGNORE INTO strategy_versions
  (strategy_id, version, name, family, source, source_sha256, params_json, lifecycle, frozen, created_at, updated_at)
VALUES
  ('fng_ema_mean_reversion', 'v1', 'FngEmaMeanReversion', 'mean-reversion',
   'python:strategies.py',
   '1cb505bc3007780698b6625c2fb7b31bcef7b2405cc7e79f7cdfaf058d0d86e0',
   '{}', 'candidate', 1, '2026-11-05T00:00:00.000Z', '2026-11-05T00:00:00.000Z'),
  ('brc20_rsi_mean_reversion', 'v1', 'Brc20RsiMeanReversion', 'mean-reversion',
   'python:strategies.py',
   '1cb505bc3007780698b6625c2fb7b31bcef7b2405cc7e79f7cdfaf058d0d86e0',
   '{}', 'candidate', 1, '2026-11-05T00:00:00.000Z', '2026-11-05T00:00:00.000Z');
