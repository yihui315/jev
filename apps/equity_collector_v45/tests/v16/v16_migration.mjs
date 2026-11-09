/**
 * 0001 -> 0002 migration: historical rows get ALL new columns as UNKNOWN /
 * HISTORY_UNPROVEN (never disguised as US/spot), the v1 legacy table is kept
 * (never DROPped), indexes are rebuilt, and the unprovable history row is NOT
 * counted in peak. 0001 itself must be the verbatim bfb3206 baseline blob.
 */
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { D1Shim } from "../lib/harness.mjs";
import { selectHistoryPeak } from "../../src/partition.mjs";
import { validateHistoryProvenance, ERROR_CODES } from "../../src/market-guard.mjs";
import {
  v16Recorder, BASE_MIGRATION_PATH, PROVENANCE_MIGRATION_PATH,
  SNAP_COLS, ddlBlock, REPO_ROOT,
} from "./lib.mjs";

const R = v16Recorder("v16_migration", { coverage: "0001 -> 0002" });
const baseSql = readFileSync(BASE_MIGRATION_PATH, "utf8");
const migSql = readFileSync(PROVENANCE_MIGRATION_PATH, "utf8");

const db = new D1Shim(baseSql);
// Build a V1.5-era DB with ONE historical VERIFIED row.
db.db.prepare("INSERT INTO paper_sessions (session_id, created_at, expires_at, revoked_at) VALUES (?,?,?,?)")
  .run("s1", "2020-01-01T00:00:00.000Z", "2099-01-01T00:00:00.000Z", null);
const snap = {
  snapshot_id: "hist1", session_id: "s1", generation: 1, account_version: 1,
  state_hash: "h", equity: 123456789, cash: 0, market_value: 0,
  unrealized_pnl: 0, realized_pnl: 0, equity_peak: 123456789, drawdown: 0,
  mark_quality: "verified", marks_json: "{}", source: "x", created_at: "t",
};
db.db.prepare(`INSERT INTO paper_equity_snapshots (${SNAP_COLS.join(",")}) VALUES (${SNAP_COLS.map(() => "?").join(",")})`)
  .run(...SNAP_COLS.map((c) => snap[c]));
const OLD_COLS = [
  "snapshot_id", "session_id", "generation", "cycle_key", "collector_version",
  "engine_sha256", "formula_version", "input_state_hash", "input_marks_json",
  "initial_cash_fp", "source_host", "endpoint", "source_url", "http_status",
  "payload_sha256", "price_ts", "fetched_at", "freshness_ms", "freshness_verdict",
  "clock_skew_ms", "data_quality", "mark_quality", "missing_marks_json",
  "time_probe_ok", "error_code", "latency_ms", "created_at",
];
const hist = {
  snapshot_id: "hist1", session_id: "s1", generation: 1, cycle_key: "old",
  collector_version: "v45-equity-collector/1.0.0", engine_sha256: "e",
  formula_version: "v45-equity-fp/1", input_state_hash: "h", input_marks_json: "{}",
  initial_cash_fp: 0, source_host: "api.binance.us", endpoint: "/api/v3/ticker/price",
  source_url: "https://api.binance.us/x", http_status: 200, payload_sha256: "p",
  price_ts: "t", fetched_at: "t", freshness_ms: 0, freshness_verdict: "FRESH",
  clock_skew_ms: 0, data_quality: "VERIFIED", mark_quality: "verified",
  missing_marks_json: "[]", time_probe_ok: 1, error_code: null, latency_ms: 0,
  created_at: "t",
};
db.db.prepare(`INSERT INTO v45_snapshot_provenance (${OLD_COLS.join(",")}) VALUES (${OLD_COLS.map(() => "?").join(",")})`)
  .run(...OLD_COLS.map((c) => hist[c]));

// Apply the versioned migration.
db.db.exec(migSql);

const legacy = db.db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='v45_snapshot_provenance_v1_legacy'").all();
R.check("legacy_table_preserved", legacy.length === 1, { tables: legacy });
const legacyRow = db.db.prepare("SELECT * FROM v45_snapshot_provenance_v1_legacy WHERE snapshot_id='hist1'").get();
R.check("legacy_row_preserved_verbatim",
  !!legacyRow && legacyRow.data_quality === "VERIFIED" && legacyRow.collector_version === "v45-equity-collector/1.0.0",
  { found: !!legacyRow, dq: legacyRow && legacyRow.data_quality });

const cur = db.db.prepare("SELECT * FROM v45_snapshot_provenance WHERE snapshot_id='hist1'").get();
R.check("history_new_columns_all_unknown",
  ["exchange", "market", "price_ts_source", "network_context", "venue"].every((c) => cur[c] === "UNKNOWN"),
  { exchange: cur.exchange, market: cur.market, price_ts_source: cur.price_ts_source, network_context: cur.network_context, venue: cur.venue });
R.check("history_failure_class_history_unproven",
  cur.failure_class === "HISTORY_UNPROVEN", { failure_class: cur.failure_class });
R.check("history_mark_contracts_json_empty",
  cur.mark_contracts_json === "{}", { mark_contracts_json: cur.mark_contracts_json });
R.check("history_partition_key_self_consistent",
  cur.market_partition_key === "UNKNOWN|UNKNOWN" &&
  cur.market_partition_key === cur.exchange + "|" + cur.market,
  { market_partition_key: cur.market_partition_key });
R.check("history_data_quality_preserved_verified",
  cur.data_quality === "VERIFIED", { data_quality: cur.data_quality });
const idx = db.db.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_v45_prov%' ORDER BY name").all().map((r) => r.name);
R.check("three_indexes_rebuilt_after_migration", idx.length === 3, { indexes: idx });

// The unprovable history row must NEVER count in a peak (any partition).
const pUS = await selectHistoryPeak(db, { sessionId: "s1", generation: 1, exchange: "Binance.US", market: "spot" });
const pUnk = await selectHistoryPeak(db, { sessionId: "s1", generation: 1, exchange: "UNKNOWN", market: "UNKNOWN" });
R.check("history_row_excluded_from_peak", pUS === null && pUnk === null, { pUS, pUnk });

// Rule 7 guard rejects a VERIFIED-claimed but unprovable history row.
const hc = validateHistoryProvenance([{
  data_quality: "VERIFIED", failure_class: "HISTORY_UNPROVEN",
  exchange: "UNKNOWN", market: "UNKNOWN",
  price_ts_source: "UNKNOWN", network_context: "UNKNOWN",
}]);
R.check("history_provenance_guard_rejects",
  hc.pass === false && hc.code === ERROR_CODES.HISTORY_PROVENANCE_UNPROVEN, { code: hc.code });

// 0001 is verbatim bfb3206 baseline; paper_equity_snapshots DDL unchanged.
const g = spawnSync("git", ["-C", REPO_ROOT, "show", "bfb3206:apps/equity_collector_v45/schema.sql"], { encoding: "utf8" });
R.check("0001_is_verbatim_baseline_blob", g.status === 0 && g.stdout === baseSql,
  { gitExit: g.status, gitBytes: Buffer.byteLength(g.stdout || ""), fileBytes: Buffer.byteLength(baseSql) });

R.finish({ legacyKept: legacy.length === 1, historyEquity: snap.equity, pUS, pUnk });
