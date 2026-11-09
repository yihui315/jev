/**
 * v45_snapshot_provenance row builder + idempotent INSERT.
 * The frozen paper_equity_snapshots table is NOT altered: every extra audit
 * field lives in this table, keyed by snapshot_id (FK).
 *
 * V1.6: COLUMNS gains the 8 market-provenance columns (contract 1.2). They are
 * supplied by the caller via `v16` (built by collector.mjs from the guard).
 * INSERT OR IGNORE is UNCHANGED, so the generated SQL keeps its
 * "INSERT OR IGNORE INTO" prefix (V1.5 T7) and the cycle stays idempotent.
 */
import { PROVENANCE_COLLECTOR_VERSION, FORMULA_VERSION } from "./constants.mjs";

export function buildProvenance({ snapshot, market, cycleKey, engineSha256, initialCashFp, nowMs, v16 }) {
  const v = v16 || {};
  return {
    snapshot_id: snapshot.snapshotId,
    session_id: snapshot.sessionId,
    generation: snapshot.generation,
    cycle_key: cycleKey,
    collector_version: PROVENANCE_COLLECTOR_VERSION,
    engine_sha256: engineSha256,
    formula_version: FORMULA_VERSION,
    input_state_hash: snapshot.stateHash,
    input_marks_json: snapshot.marksJson,
    initial_cash_fp: initialCashFp,
    source_host: market.sourceHost,
    endpoint: market.endpoint,
    source_url: market.sourceUrl,
    http_status: market.httpStatus,
    payload_sha256: market.payloadSha256,
    price_ts: market.priceTs,
    fetched_at: market.fetchedAt,
    freshness_ms: market.freshnessMs,
    freshness_verdict: market.freshnessVerdict,
    clock_skew_ms: market.clockSkewMs,
    data_quality: market.dataQuality,
    mark_quality: snapshot.markQuality,
    missing_marks_json: JSON.stringify(market.missing),
    time_probe_ok: market.timeProbeOk ? 1 : 0,
    error_code: market.errorCode,
    latency_ms: market.latencyMs,
    created_at: new Date(nowMs).toISOString(),
    // ---- V1.6 market-provenance columns (M1/M2/M3) -------------------------
    exchange: v.exchange,
    market: v.market,
    price_ts_source: v.price_ts_source,
    network_context: v.network_context,
    venue: v.venue,
    failure_class: v.failure_class,
    mark_contracts_json: v.mark_contracts_json,
    market_partition_key: v.market_partition_key,
  };
}

const COLUMNS = [
  "snapshot_id", "session_id", "generation", "cycle_key", "collector_version",
  "engine_sha256", "formula_version", "input_state_hash", "input_marks_json",
  "initial_cash_fp", "source_host", "endpoint", "source_url", "http_status",
  "payload_sha256", "price_ts", "fetched_at", "freshness_ms", "freshness_verdict",
  "clock_skew_ms", "data_quality", "mark_quality", "missing_marks_json",
  "time_probe_ok", "error_code", "latency_ms", "created_at",
  // ---- V1.6 market-provenance columns --------------------------------------
  "exchange", "market", "price_ts_source", "network_context", "venue",
  "failure_class", "mark_contracts_json", "market_partition_key",
];

export function provenanceInsertStatement(p) {
  const placeholders = COLUMNS.map(() => "?").join(", ");
  return {
    sql: `INSERT OR IGNORE INTO v45_snapshot_provenance (${COLUMNS.join(", ")}) VALUES (${placeholders})`,
    values: COLUMNS.map((c) => p[c]),
  };
}
