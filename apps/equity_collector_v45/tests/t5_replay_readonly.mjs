/**
 * T5 — Historical replay, frozen-engine ZERO-WRITE proof, engine hash anchor,
 * and Worker entrypoints (scheduled + controlled fetch).
 */
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import {
  ROOT, openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, runCycle, rows,
  newRecorder, hashRow, scheduled, fetchEntrypoint,
} from "./lib/harness.mjs";
import { ENGINE_ANCHORS, ENGINE_ANCHOR_PATHS } from "../src/engine-anchor.mjs";
import { computeEngineSha256 } from "../src/hash.mjs";
import { replay } from "../src/replay.mjs";
import { D } from "../src/vendor/decimal.mjs";

const R = newRecorder("T5_replay_readonly", {
  proves: ["pure replay", "zero writes to frozen ledger tables", "engine sha256 anchor", "scheduled/fetch entrypoints"],
});
const T = 1770000000000;
const INITIAL_CASH_FP = Number(D(100000));
const shaFile = (p) => createHash("sha256").update(readFileSync(p)).digest("hex");
const db = openDb();

const st = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});
await insertSession(db, "s1", { nowMs: T });
await insertAccount(db, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

// ---- engine hash anchors (frozen files must be byte-unchanged) ------------
const anchorCheck = [];
for (const [key, path] of Object.entries(ENGINE_ANCHOR_PATHS)) {
  const actual = shaFile(path);
  anchorCheck.push({ file: key, expected: ENGINE_ANCHORS[key], actual, match: actual === ENGINE_ANCHORS[key] });
  R.check(`frozen_unchanged_${key}`, actual === ENGINE_ANCHORS[key], { file: key, expected: ENGINE_ANCHORS[key], actual });
}
const vendorDecimal = readFileSync(resolve(ROOT, "src/vendor/decimal.mjs"));
const vendorEquity = readFileSync(resolve(ROOT, "src/vendor/equity-snapshot.mjs"));
const frozenDecimal = readFileSync(ENGINE_ANCHOR_PATHS["server/engine/_lib/decimal.mjs"]);
const frozenEquity = readFileSync(ENGINE_ANCHOR_PATHS["server/engine/_lib/equity-snapshot.mjs"]);
R.check("vendor_decimal_byte_identical", Buffer.compare(vendorDecimal, frozenDecimal) === 0);
R.check("vendor_equity_byte_identical", Buffer.compare(vendorEquity, frozenEquity) === 0);

// ---- accounts read-only snapshot + write spy -------------------------------
const beforeAccounts = await rows(db, "SELECT session_id,version,state_json,state_hash,last_command_id,created_at,updated_at FROM paper_accounts ORDER BY session_id");
const beforeHash = await hashRow(beforeAccounts);
db.setPhase("collector");
db.clearWriteLog();

const plan = [
  { key: "t5-c0", mode: "ok", price: REAL_BASE_PRICES.BTCUSDT },
  { key: "t5-c1", mode: "throw" },
  { key: "t5-c2", mode: "ok", price: "72000.00000000" },
];
for (let i = 0; i < plan.length; i++) {
  const p = plan[i];
  const nowMs = T + i * 60000;
  const fetcher = controlledFetcher({ nowMs, serverTime: nowMs, mode: p.mode, prices: { BTCUSDT: p.price || REAL_BASE_PRICES.BTCUSDT } });
  const env = makeEnv(db, fetcher, {});
  await runCycle(env, { db, fetcher, cycleKey: p.key, nowMs });
}

const afterAccounts = await rows(db, "SELECT session_id,version,state_json,state_hash,last_command_id,created_at,updated_at FROM paper_accounts ORDER BY session_id");
const afterHash = await hashRow(afterAccounts);
R.check("accounts_fully_unchanged", beforeHash === afterHash, { beforeHash, afterHash });

const FORBIDDEN = ["paper_accounts", "paper_server_events", "paper_orders", "paper_fills"];
const ALLOWED = ["paper_equity_snapshots", "v45_snapshot_provenance"];
const forbiddenWrites = db.writeLog.filter((w) => FORBIDDEN.includes(w.table));
R.check("zero_writes_to_frozen_ledger_tables", forbiddenWrites.length === 0,
  { forbidden: forbiddenWrites, total_writes: db.writeLog.length });
R.check("only_allowed_tables_written",
  db.writeLog.length > 0 && db.writeLog.every((w) => ALLOWED.includes(w.table)),
  { tables: [...new Set(db.writeLog.map((w) => w.table))], count: db.writeLog.length });
R.check("no_order_state_mutation",
  (await rows(db, "SELECT * FROM paper_orders")).length === 0 && (await rows(db, "SELECT * FROM paper_server_events")).length === 0);

// ---- pure replay of every stored row ---------------------------------------
const snaps = await rows(db, "SELECT * FROM paper_equity_snapshots ORDER BY created_at ASC, snapshot_id ASC");
const rp = replay(snaps, { initialCashFp: INITIAL_CASH_FP });
R.check("replay_count_3", rp.count === 3, { count: rp.count });
R.check("replay_all_rows_match", rp.allMatch, rp.rows);
R.check("replay_unknown_rows_do_not_touch_peak",
  rp.rows.filter((r) => r.mark_quality === "unknown").every((r) => r.recomputed_peak === 0 && r.recomputed_dd === 0),
  rp.rows.map((r) => ({ q: r.mark_quality, peak: r.recomputed_peak, dd: r.recomputed_dd })));

// ---- engine_sha256 recorded on each provenance row -------------------------
const engineSha = await computeEngineSha256();
const provs = await rows(db, "SELECT * FROM v45_snapshot_provenance");
R.check("engine_sha256_recorded_and_matches",
  provs.length === 3 && provs.every((p) => p.engine_sha256 === engineSha),
  { engineSha, recorded: provs.map((p) => p.engine_sha256) });
R.check("collector_version_and_formula_recorded",
  provs.every((p) => p.collector_version === "v45-equity-collector/1.0.0" && p.formula_version === "v45-equity-fp/1"),
  { versions: [...new Set(provs.map((p) => p.collector_version))], formulas: [...new Set(provs.map((p) => p.formula_version))] });
R.check("input_basis_recorded",
  provs.every((p) => p.input_state_hash === st.state_hash && p.input_marks_json !== null && p.initial_cash_fp === INITIAL_CASH_FP),
  provs.map((p) => ({ state: p.input_state_hash, marks: p.input_marks_json, icf: p.initial_cash_fp })));

// ---- scheduled() entrypoint ------------------------------------------------
const dbS = openDb();
await insertSession(dbS, "s1", { nowMs: T });
await insertAccount(dbS, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });
const fS = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } });
const envS = makeEnv(dbS, fS, {});
const schedResult = await scheduled({ scheduledTime: T }, envS);
R.check("scheduled_entrypoint_runs_one_cycle",
  schedResult && schedResult.written === 1 && schedResult.verified === 1 &&
  (await rows(dbS, "SELECT * FROM paper_equity_snapshots")).length === 1,
  schedResult && { written: schedResult.written, verified: schedResult.verified, cycleKey: schedResult.cycleKey });

// ---- controlled fetch() entrypoint (disabled by default) ------------------
const dbF = openDb();
await insertSession(dbF, "s1", { nowMs: T });
await insertAccount(dbF, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });
const fF = controlledFetcher({ nowMs: T, serverTime: T, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } });
const envDisabled = makeEnv(dbF, fF, {});   // testTrigger defaults to "false"
const r403 = await fetchEntrypoint(new Request("https://isolated.test/__v45/collect?cycle_key=fx&now_ms=" + T), envDisabled);
R.check("fetch_trigger_disabled_by_default_403", r403.status === 403, { status: r403.status });
const envEnabled = makeEnv(dbF, fF, { testTrigger: "true" });
const r200 = await fetchEntrypoint(new Request("https://isolated.test/__v45/collect?cycle_key=fx&now_ms=" + T), envEnabled);
const body200 = await r200.json();
R.check("fetch_trigger_enabled_works", r200.status === 200 && body200.ok === true && body200.written >= 1,
  { status: r200.status, written: body200.written });
const r404 = await fetchEntrypoint(new Request("https://isolated.test/other"), envEnabled);
R.check("fetch_unknown_path_404", r404.status === 404, { status: r404.status });

R.finish({
  engine_anchors: anchorCheck,
  engine_sha256: engineSha,
  accounts_before_hash: beforeHash,
  accounts_after_hash: afterHash,
  write_log: db.writeLog.map((w) => ({ phase: w.phase, table: w.table })),
  replay: rp,
  scheduled: schedResult,
  fetch_entrypoint: { disabled_status: r403.status, enabled_status: r200.status, enabled_written: body200.written, unknown_path_status: r404.status },
  verdict_labels: {
    replay_arithmetic: "VERIFIED",
    frozen_engine_zero_write: "VERIFIED (local spy + unchanged account hash)",
    engine_sha256_anchor: "VERIFIED (re-read from disk)",
    worker_entrypoints: "VERIFIED (local shim)",
    cloud_d1_deploy: "UNVERIFIED (owner action)",
  },
});
