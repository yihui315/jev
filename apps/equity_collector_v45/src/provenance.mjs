/**
 * v45_snapshot_provenance row builder + idempotent INSERT.
 * The frozen paper_equity_snapshots table is NOT altered: every extra audit
 * field lives in this new table, keyed by snapshot_id (FK).
 */
import { COLLECTOR_VERSION, FORMULA_VERSION } from "./constants.mjs";

export function buildProvenance({ snapshot, market, cycleKey, engineSha256, initialCashFp, nowMs }) {
  return {
    snapshot_id: snapshot.snapshotId,
    session_id: snapshot.sessionId,
    generation: snapshot.generation,
    cycle_key: cycleKey,
    collector_version: COLLECTOR_VERSION,
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
  };
}

const COLUMNS = [
  "snapshot_id", "session_id", "generation", "cycle_key", "collector_version",
  "engine_sha256", "formula_version", "input_state_hash", "input_marks_json",
  "initial_cash_fp", "source_host", "endpoint", "source_url", "http_status",
  "payload_sha256", "price_ts", "fetched_at", "freshness_ms", "freshness_verdict",
  "clock_skew_ms", "data_quality", "mark_quality", "missing_marks_json",
  "time_probe_ok", "error_code", "latency_ms", "created_at",
];

export function provenanceInsertStatement(p) {
  const placeholders = COLUMNS.map(() => "?").join(", ");
  return {
    sql: `INSERT OR IGNORE INTO v45_snapshot_provenance (${COLUMNS.join(", ")}) VALUES (${placeholders})`,
    values: COLUMNS.map((c) => p[c]),
  };
}
