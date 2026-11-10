/**
 * QA-T7-2 - schema.sql column set == FIELD_CONTRACT; all 8 new columns carry
 * their CHECK / DEFAULT clauses; paper_equity_snapshots DDL is byte-identical
 * to 0001; 0001 is byte-identical to the bfb3206 baseline blob; 0002 backfills
 * UNKNOWN / HISTORY_UNPROVEN and keeps the legacy table (no DROP).
 */
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { D1Shim } from "../lib/harness.mjs";
import {
  v16Recorder, FIELD_CONTRACT, SCHEMA_PATH, BASE_MIGRATION_PATH,
  PROVENANCE_MIGRATION_PATH, ddlBlock, REPO_ROOT,
} from "./lib.mjs";

const R = v16Recorder("v16_t7_schema_contract", { coverage: "QA-T7-2: schema/migration contract" });
const schemaText = readFileSync(SCHEMA_PATH, "utf8");
const baseText = readFileSync(BASE_MIGRATION_PATH, "utf8");
const migText = readFileSync(PROVENANCE_MIGRATION_PATH, "utf8");

// Column set from the FINAL schema (openDb reads exactly this file).
const db = new D1Shim(schemaText);
const cols = db.db.prepare("PRAGMA table_info(v45_snapshot_provenance)").all().map((r) => r.name);
R.check("schema_column_set_equals_field_contract",
  JSON.stringify(cols) === JSON.stringify(FIELD_CONTRACT),
  { actualCount: cols.length, expectedCount: FIELD_CONTRACT.length, actual: cols });

// 8 new columns must carry their CHECK / DEFAULT clauses in schema.sql.
const CHECK_LITERALS = [
  ["exchange", "exchange TEXT NOT NULL CHECK (exchange IN ('Binance.US','Binance Global spot','UNKNOWN'))"],
  ["market", "market TEXT NOT NULL CHECK (market IN ('spot','perp','UNKNOWN'))"],
  ["price_ts_source", "price_ts_source TEXT NOT NULL CHECK (price_ts_source IN ('exchange_serverTime','trade_time','http_date','received_at','UNKNOWN'))"],
  ["network_context", "network_context TEXT NOT NULL CHECK (length(network_context) > 0)"],
  ["venue", "venue TEXT NOT NULL"],
  ["failure_class", "failure_class TEXT NOT NULL CHECK (failure_class IN ("],
  ["mark_contracts_json", "mark_contracts_json TEXT NOT NULL DEFAULT '{}'"],
  ["market_partition_key", "market_partition_key TEXT NOT NULL CHECK (market_partition_key = exchange || '|' || market)"],
];
for (const [name, lit] of CHECK_LITERALS) {
  R.check("check_present_" + name, schemaText.includes(lit), { literal: lit });
}

// paper_equity_snapshots DDL block byte-identical to 0001.
const schemaDdl = Buffer.from(ddlBlock(schemaText), "utf8");
const baseDdl = Buffer.from(ddlBlock(baseText), "utf8");
R.check("snapshots_ddl_byte_identical_to_0001",
  schemaDdl.length === baseDdl.length && Buffer.compare(schemaDdl, baseDdl) === 0,
  { schemaBytes: schemaDdl.length, baseBytes: baseDdl.length });

// 0001 is the verbatim bfb3206 baseline blob (independent git read).
const g = spawnSync("git", ["-C", REPO_ROOT, "show", "bfb3206:apps/equity_collector_v45/schema.sql"], { encoding: "utf8" });
R.check("0001_is_verbatim_baseline_blob",
  g.status === 0 && g.stdout === baseText,
  { gitExit: g.status, gitBytes: Buffer.byteLength(g.stdout || ""), fileBytes: Buffer.byteLength(baseText) });

// 0002 contract: all 8 columns, UNKNOWN backfill, HISTORY_UNPROVEN, legacy kept.
R.check("0002_defines_all_8_new_columns",
  FIELD_CONTRACT.slice(-8).every((c) => migText.includes(c)),
  { cols: FIELD_CONTRACT.slice(-8) });
R.check("0002_backfills_unknown_and_history_unproven",
  migText.includes("'HISTORY_UNPROVEN'") && migText.includes("'UNKNOWN|UNKNOWN'") &&
  /'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN'/.test(migText),
  { note: "history must NOT be disguised as Binance.US/spot/exchange_serverTime" });
R.check("0002_renames_v1_to_legacy_never_drops",
  migText.includes("RENAME TO v45_snapshot_provenance_v1_legacy") && !/DROP\s+TABLE/i.test(migText),
  { hasLegacyRename: migText.includes("RENAME TO v45_snapshot_provenance_v1_legacy") });
R.check("0002_rebuilds_partition_index",
  migText.includes("session_id, generation, exchange, market, data_quality"),
  { note: "V1.6 partition index" });

R.finish({ columnCount: cols.length });
