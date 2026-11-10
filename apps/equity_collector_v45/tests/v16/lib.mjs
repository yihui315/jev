/**
 * V1.6 test support. NEW file under tests/v16/ - the V1.5 tests/lib/* files
 * are byte-frozen and are NEVER modified or re-exported here.
 */
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";

const HERE = dirname(fileURLToPath(import.meta.url));
export const APP_ROOT = resolve(HERE, "../..");
export const REPO_ROOT = resolve(HERE, "../../../..");
export const EVIDENCE_DIR = resolve(REPO_ROOT, "reports/evidence/v16");
export const MIGRATIONS_DIR = resolve(APP_ROOT, "migrations");
export const SCHEMA_PATH = resolve(APP_ROOT, "schema.sql");
export const BASE_MIGRATION_PATH = resolve(MIGRATIONS_DIR, "0001_v45_base.sql");
export const PROVENANCE_MIGRATION_PATH = resolve(MIGRATIONS_DIR, "0002_market_provenance.sql");
mkdirSync(EVIDENCE_DIR, { recursive: true });

/** Exact v45_snapshot_provenance column set (V1.5 27 + V1.6 8). */
export const FIELD_CONTRACT = [
  "snapshot_id", "session_id", "generation", "cycle_key", "collector_version",
  "engine_sha256", "formula_version", "input_state_hash", "input_marks_json",
  "initial_cash_fp", "source_host", "endpoint", "source_url", "http_status",
  "payload_sha256", "price_ts", "fetched_at", "freshness_ms", "freshness_verdict",
  "clock_skew_ms", "data_quality", "mark_quality", "missing_marks_json",
  "time_probe_ok", "error_code", "latency_ms", "created_at",
  "exchange", "market", "price_ts_source", "network_context", "venue",
  "failure_class", "mark_contracts_json", "market_partition_key",
];

export const NEW_COLS = FIELD_CONTRACT.slice(-8);
export const PROV_COLS = FIELD_CONTRACT;
export const SNAP_COLS = [
  "snapshot_id", "session_id", "generation", "account_version", "state_hash",
  "equity", "cash", "market_value", "unrealized_pnl", "realized_pnl",
  "equity_peak", "drawdown", "mark_quality", "marks_json", "source", "created_at",
];

/** Recorder: emits RESULT_JSON: to stdout AND writes the evidence JSON file. */
export function v16Recorder(testId, meta = {}) {
  const r = { testId, meta, checks: [], startedAt: new Date().toISOString() };
  r.check = (name, cond, detail = null) => r.checks.push({ name, pass: !!cond, detail });
  r.finish = (extra = {}) => {
    const pass = r.checks.length > 0 && r.checks.every((c) => c.pass);
    const out = {
      testId,
      verdict: pass ? "PASS" : "FAIL",
      startedAt: r.startedAt,
      finishedAt: new Date().toISOString(),
      meta: r.meta,
      checks: r.checks,
      extra,
    };
    // "group_" prefix keeps this file distinct from the runner's canonical
    // <ID>_<label>.json on case-insensitive filesystems (Windows).
    writeFileSync(resolve(EVIDENCE_DIR, "group_" + testId + ".json"), JSON.stringify(out, null, 2));
    process.stdout.write("RESULT_JSON:" + JSON.stringify(out) + "\n");
    process.exit(pass ? 0 : 1);
  };
  return r;
}

/** Extract the paper_equity_snapshots CREATE TABLE block (for byte comparison). */
export function ddlBlock(text) {
  const start = text.indexOf("CREATE TABLE IF NOT EXISTS paper_equity_snapshots (");
  const end = text.indexOf("\n);", start) + 3;
  return text.slice(start, end);
}

/** Complete, schema-valid v1.6 provenance row; override any field per fixture. */
export function baseProv(overrides = {}) {
  return {
    snapshot_id: "fx", session_id: "s1", generation: 1, cycle_key: "fx",
    collector_version: "v45-equity-collector/1.0.0", engine_sha256: "e",
    formula_version: "v45-equity-fp/1", input_state_hash: "h", input_marks_json: "{}",
    initial_cash_fp: 0, source_host: "api.binance.us", endpoint: "/api/v3/ticker/price",
    source_url: "https://api.binance.us/api/v3/ticker/price", http_status: 200,
    payload_sha256: "p", price_ts: "t", fetched_at: "t", freshness_ms: 0,
    freshness_verdict: "FRESH", clock_skew_ms: 0, data_quality: "VERIFIED",
    mark_quality: "verified", missing_marks_json: "[]", time_probe_ok: 1,
    error_code: null, latency_ms: 0, created_at: "t",
    exchange: "Binance.US", market: "spot", price_ts_source: "exchange_serverTime",
    network_context: "local_test_shim", venue: "Binance.US spot",
    failure_class: "NONE", mark_contracts_json: "{}",
    market_partition_key: "Binance.US|spot",
    ...overrides,
  };
}

/**
 * Insert a schema-valid snapshot + provenance fixture row into a V1.6 DB.
 * Uses the D1 shim's underlying node:sqlite handle for synchronous setup.
 * The caller must have inserted the session (FK) already.
 */
export function insertFixture(db, o = {}) {
  const id = o.snapshotId;
  const s = {
    snapshot_id: id, session_id: o.sessionId || "s1", generation: o.generation || 1,
    account_version: 1, state_hash: "h", equity: o.equity ?? 0,
    cash: 0, market_value: 0, unrealized_pnl: 0, realized_pnl: 0,
    equity_peak: 0, drawdown: 0, mark_quality: o.markQuality || "verified",
    marks_json: "{}", source: "x", created_at: o.createdAt || "t",
  };
  db.db.prepare(
    `INSERT INTO paper_equity_snapshots (${SNAP_COLS.join(",")}) VALUES (${SNAP_COLS.map(() => "?").join(",")})`
  ).run(...SNAP_COLS.map((c) => s[c]));
  const p = baseProv({ snapshot_id: id, ...(o.prov || {}) });
  db.db.prepare(
    `INSERT OR IGNORE INTO v45_snapshot_provenance (${PROV_COLS.join(",")}) VALUES (${PROV_COLS.map(() => "?").join(",")})`
  ).run(...PROV_COLS.map((c) => p[c]));
  return { id, s, p };
}
