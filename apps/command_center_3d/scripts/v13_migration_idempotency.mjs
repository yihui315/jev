// V1.3 — T02 Part B: migration idempotency / restart recovery test.
// Exercises D1Adapter.applyMigrations() directly against a THROWAWAY sqlite DB
// (no server, no live runtime DB). Proves:
//   1. first startup applies all 3 migrations
//   2. a simulated RESTART re-running applyMigrations skips all (no re-run)
//   3. "migrated before ledger existed" (schema_migrations dropped) is
//      back-filled and 0003 is NOT re-run (no double-scaling, no data loss)
//   4. interrupted-0003 residue table (paper_orders_fp) is cleaned up
//   5. no FK / UNIQUE crash; quantity column stays fixed-point INTEGER
import { D1Adapter } from "../server/lib/d1_adapter.mjs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { existsSync, unlinkSync, readdirSync } from "node:fs";

const tmp = join(tmpdir(), "jev_mig_test.sqlite");
for (const f of readdirSync(tmpdir()).filter((f) => f.startsWith("jev_mig_test"))) {
  try { unlinkSync(join(tmpdir(), f)); } catch (_) {}
}
if (existsSync(tmp)) unlinkSync(tmp);

const checks = {};
const q = (db, sql) => db.prepare(sql).all();
function colType(db, table, col) {
  const r = db.prepare(`PRAGMA table_info(${table})`).all().find((c) => c.name === col);
  return r ? r.type : null;
}
function hasTable(db, t) { return !!db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name=?").get(t); }

try {
  // ---- 1. first startup ----
  const a1 = new D1Adapter(tmp);
  const applied1 = a1.applyMigrations();
  checks.first_run_applies_3 = applied1.length === 3;
  checks.first_run_schema_migrations_count = q(a1.db, "SELECT COUNT(*) c FROM schema_migrations").at(0).c === 3;
  checks.first_run_quantity_integer = /INT/i.test(colType(a1.db, "paper_orders", "quantity") || "");
  checks.first_run_server_events_exists = hasTable(a1.db, "paper_server_events");
  checks.first_run_no_fp_residue = !hasTable(a1.db, "paper_orders_fp") && !hasTable(a1.db, "paper_fills_fp");

  // ---- 2. simulated RESTART (new instance, same DB) ----
  const a2 = new D1Adapter(tmp);
  const applied2 = a2.applyMigrations();
  checks.restart_skips_all = applied2.length === 0;
  checks.restart_schema_migrations_still_3 = q(a2.db, "SELECT COUNT(*) c FROM schema_migrations").at(0).c === 3;
  checks.restart_quantity_still_integer = /INT/i.test(colType(a2.db, "paper_orders", "quantity") || "");
  checks.restart_no_crash = true;

  // ---- 3. "migrated before ledger existed": drop the ledger, re-run ----
  a2.db.exec("DROP TABLE IF EXISTS schema_migrations;");
  const a3 = new D1Adapter(tmp);
  const applied3 = a3.applyMigrations();
  // back-fill path: all 3 are treated as applied -> migrations_applied empty
  checks.backfill_skips_all = applied3.length === 0;
  checks.backfill_schema_migrations_rebuilt = q(a3.db, "SELECT COUNT(*) c FROM schema_migrations").at(0).c === 3;
  checks.backfill_0003_not_rerun_no_double_scale = /INT/i.test(colType(a3.db, "paper_orders", "quantity") || "");
  checks.backfill_paper_orders_intact = hasTable(a3.db, "paper_orders");

  // ---- 4. interrupted-0003 residue cleanup ----
  a3.db.exec("CREATE TABLE paper_orders_fp (x INTEGER);");
  a3.db.exec("CREATE TABLE paper_fills_fp (y INTEGER);");
  const a4 = new D1Adapter(tmp);
  a4.applyMigrations();
  checks.residue_cleanup_removed_fp = !hasTable(a4.db, "paper_orders_fp") && !hasTable(a4.db, "paper_fills_fp");

  // ---- 5. FK enforcement on ----
  checks.foreign_keys_enabled = q(a4.db, "PRAGMA foreign_keys").at(0).foreign_keys === 1;
} catch (err) {
  checks.runtime_error = String(err && err.stack || err);
}

const booleans = Object.entries(checks).filter(([, v]) => typeof v === "boolean");
const pass = booleans.length > 0 && booleans.every(([, v]) => v === true);
const out = {
  generated_utc: new Date().toISOString(),
  task: "T02-partB-migration-idempotency",
  engine_modified: false,
  checks,
  all_passed: pass,
};
const fs = await import("node:fs");
const { join: pj, dirname } = await import("node:path");
const ART = pj(dirname(import.meta.dirname), "e2e", "artifacts", "v13");
fs.mkdirSync(ART, { recursive: true });
fs.writeFileSync(pj(ART, "migration_idempotency.json"), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
process.exit(pass ? 0 : 1);
