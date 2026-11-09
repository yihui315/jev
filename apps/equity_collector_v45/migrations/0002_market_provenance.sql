-- =============================================================================
-- V45 Equity Collector - V1.6 MIGRATION 0002: market provenance (v1 -> v2).
-- Applied to a DB created by 0001_v45_base.sql. This is a VERSIONED migration,
-- NOT the final schema (see schema.sql). Never applied to staging/production D1.
--
-- Historical rows CANNOT prove their market identity, so every new column is
-- written EXPLICITLY as UNKNOWN / HISTORY_UNPROVEN. We refuse to fabricate
-- 'Binance.US' / 'spot' / 'exchange_serverTime' for history. The v1 table is
-- RENAMED (never DROPped) so the historical evidence is preserved verbatim.
-- =============================================================================

-- (1) Create the v2 table: original 27 columns + 8 new columns (with CHECKs)
--     and a self-consistent (exchange | market) partition key.
CREATE TABLE IF NOT EXISTS v45_snapshot_provenance_v2 (
  snapshot_id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  cycle_key TEXT NOT NULL,
  collector_version TEXT NOT NULL,
  engine_sha256 TEXT NOT NULL,
  formula_version TEXT NOT NULL,
  input_state_hash TEXT NOT NULL,
  input_marks_json TEXT NOT NULL DEFAULT '{}',
  initial_cash_fp INTEGER,
  source_host TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  source_url TEXT NOT NULL,
  http_status INTEGER,
  payload_sha256 TEXT,
  price_ts TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  freshness_ms INTEGER NOT NULL,
  freshness_verdict TEXT NOT NULL CHECK (freshness_verdict IN ('FRESH','STALE','UNKNOWN')),
  clock_skew_ms INTEGER,
  data_quality TEXT NOT NULL CHECK (data_quality IN ('VERIFIED','UNKNOWN')),
  mark_quality TEXT NOT NULL CHECK (mark_quality IN ('verified','unknown')),
  missing_marks_json TEXT NOT NULL DEFAULT '[]',
  time_probe_ok INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  latency_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  -- ---- V1.6 market-provenance columns (M1/M2/M3) ---------------------------
  exchange TEXT NOT NULL CHECK (exchange IN ('Binance.US','Binance Global spot','UNKNOWN')),
  market TEXT NOT NULL CHECK (market IN ('spot','perp','UNKNOWN')),
  price_ts_source TEXT NOT NULL CHECK (price_ts_source IN ('exchange_serverTime','trade_time','http_date','received_at','UNKNOWN')),
  network_context TEXT NOT NULL CHECK (length(network_context) > 0),
  venue TEXT NOT NULL,
  failure_class TEXT NOT NULL CHECK (failure_class IN ('NONE','TRANSPORT','HTTP_ERROR','VENUE_BUSINESS','STALE','MARKET_MIXING','HISTORY_UNPROVEN','UNIT_MISMATCH','PAYLOAD_UNVERIFIED','UNKNOWN')),
  mark_contracts_json TEXT NOT NULL DEFAULT '{}',
  market_partition_key TEXT NOT NULL CHECK (market_partition_key = exchange || '|' || market),
  FOREIGN KEY(snapshot_id) REFERENCES paper_equity_snapshots(snapshot_id)
);

-- (2) Backfill v1 rows. New columns are explicit UNKNOWN / HISTORY_UNPROVEN.
INSERT OR IGNORE INTO v45_snapshot_provenance_v2 (
  snapshot_id, session_id, generation, cycle_key, collector_version, engine_sha256, formula_version, input_state_hash, input_marks_json, initial_cash_fp, source_host, endpoint, source_url, http_status, payload_sha256, price_ts, fetched_at, freshness_ms, freshness_verdict, clock_skew_ms, data_quality, mark_quality, missing_marks_json, time_probe_ok, error_code, latency_ms, created_at,
  exchange, market, price_ts_source, network_context, venue, failure_class, mark_contracts_json, market_partition_key
)
SELECT
  snapshot_id, session_id, generation, cycle_key, collector_version, engine_sha256, formula_version, input_state_hash, input_marks_json, initial_cash_fp, source_host, endpoint, source_url, http_status, payload_sha256, price_ts, fetched_at, freshness_ms, freshness_verdict, clock_skew_ms, data_quality, mark_quality, missing_marks_json, time_probe_ok, error_code, latency_ms, created_at,
  'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'HISTORY_UNPROVEN', '{}', 'UNKNOWN|UNKNOWN'
FROM v45_snapshot_provenance;

-- (3) Keep the historical evidence: rename v1 -> legacy. DO NOT DROP.
ALTER TABLE v45_snapshot_provenance RENAME TO v45_snapshot_provenance_v1_legacy;

-- (4) Promote v2 to the canonical table name.
ALTER TABLE v45_snapshot_provenance_v2 RENAME TO v45_snapshot_provenance;

-- (5) Rebuild indexes on the canonical table. The two original indexes still
--     belong to the legacy table after the rename, so drop + recreate them,
--     and add the V1.6 partition index (session,generation,exchange,market,quality).
DROP INDEX IF EXISTS idx_v45_prov_session_cycle;
DROP INDEX IF EXISTS idx_v45_prov_quality;
CREATE INDEX IF NOT EXISTS idx_v45_prov_session_cycle
  ON v45_snapshot_provenance(session_id, cycle_key);
CREATE INDEX IF NOT EXISTS idx_v45_prov_quality
  ON v45_snapshot_provenance(data_quality, fetched_at);
CREATE INDEX IF NOT EXISTS idx_v45_prov_partition
  ON v45_snapshot_provenance(session_id, generation, exchange, market, data_quality);
