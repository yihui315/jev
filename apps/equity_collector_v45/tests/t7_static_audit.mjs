/**
 * T7 (supplementary evidence) — STATIC compliance audit of the deployed src/.
 * Verifies by source inspection that the collector:
 *   - never references the FORBIDDEN data-api.binance.vision host,
 *   - never calls the frozen module's network functions (fetchMarks /
 *     collectEquitySnapshots / buildEquityRiskBasis),
 *   - never writes paper_accounts / paper_server_events / orders / fills,
 *   - uses ONLY INSERT OR IGNORE for its writes (idempotency),
 *   - derives snapshot ids from the 5 deterministic fields only,
 *   - enforces the ENABLE_REAL_TRADING red line,
 *   - contains no console logging / credential references.
 */
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { ROOT, newRecorder } from "./lib/harness.mjs";
import { snapshotInsertStatement } from "../src/compute.mjs";
import { provenanceInsertStatement } from "../src/provenance.mjs";

const R = newRecorder("T7_static_audit", { scope: "src/*.mjs (vendor/ excluded from the 'own code' checks)" });
const srcDir = resolve(ROOT, "src");
const ownFiles = readdirSync(srcDir).filter((f) => f.endsWith(".mjs"));
const own = ownFiles.map((f) => ({ file: f, text: readFileSync(resolve(srcDir, f), "utf8") }));
const joined = own.map((o) => o.text).join("\n");

/** Strip comments so the audit inspects EXECUTABLE code, not prose in comments. */
function stripComments(src) {
  let out = "", i = 0, state = "code";
  while (i < src.length) {
    const c = src[i], n = src[i + 1];
    if (state === "code") {
      if (c === "/" && n === "/") { state = "line"; i += 2; continue; }
      if (c === "/" && n === "*") { state = "block"; i += 2; continue; }
      if (c === '"' || c === "'" || c === "`") { state = "str"; out += c; i++; continue; }
      out += c; i++;
    } else if (state === "line") {
      if (c === "\n") { state = "code"; out += "\n"; }
      i++;
    } else if (state === "block") {
      if (c === "*" && n === "/") { state = "code"; i += 2; continue; }
      i++;
    } else {
      if (c === "\\") { out += c; if (i + 1 < src.length) out += src[i + 1]; i += 2; continue; }
      if (c === '"' || c === "'" || c === "`") { state = "code"; out += c; i++; continue; }
      out += c; i++;
    }
  }
  return out;
}
const codeOnly = stripComments(joined);

R.check("no_forbidden_host_in_own_src", !codeOnly.includes("data-api.binance.vision"),
  { files: ownFiles, note: "comments explaining the ban are allowed; executable references are not" });
R.check("primary_host_is_binance_us", codeOnly.includes('"api.binance.us"'));
R.check("no_fetchMarks_call", !/fetchMarks\s*\(/.test(codeOnly));
R.check("no_collectEquitySnapshots_call", !/collectEquitySnapshots\s*\(/.test(codeOnly));
R.check("no_buildEquityRiskBasis_call", !/buildEquityRiskBasis\s*\(/.test(codeOnly));
R.check("no_write_to_paper_accounts",
  !/INSERT\s+(?:OR\s+\w+\s+)?INTO\s+paper_accounts|UPDATE\s+paper_accounts|DELETE\s+FROM\s+paper_accounts/i.test(codeOnly));
R.check("no_write_to_server_events_or_orders",
  !/INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(?:paper_server_events|paper_orders|paper_fills)|UPDATE\s+(?:paper_server_events|paper_orders|paper_fills)/i.test(codeOnly));

// Verify the ACTUAL generated SQL (not source string literals) is idempotent.
const fakeSnap = {
  snapshotId: "x", sessionId: "s", generation: 1, accountVersion: 1, stateHash: "h",
  equity: 0, cash: 0, marketValue: 0, unrealizedPnl: 0, realizedPnl: 0,
  equityPeak: 0, drawdown: 0, markQuality: "unknown", marksJson: "{}",
  source: "x", createdAt: "t",
};
const sSql = snapshotInsertStatement(fakeSnap).sql;
const pSql = provenanceInsertStatement({ snapshot_id: "x" }).sql;
R.check("all_writes_are_insert_or_ignore",
  /^INSERT OR IGNORE INTO/i.test(sSql) && /^INSERT OR IGNORE INTO/i.test(pSql),
  { snapshot_sql: sSql.slice(0, 60), provenance_sql: pSql.slice(0, 60) });

// The deterministic id canonical form must contain ONLY the 5 stable fields.
const hashText = own.find((o) => o.file === "hash.mjs").text;
const idBlock = hashText.slice(hashText.indexOf("export async function deterministicSnapshotId"),
  hashText.indexOf("/** sha256 of a canonical"));
R.check("deterministic_id_uses_only_5_stable_fields",
  ["session_id", "generation", "cycle_key", "account_version", "state_hash"].every((k) => idBlock.includes(k)) &&
  !/price|equity|nowMs|Date\.now|marks|drawdown/i.test(idBlock),
  { note: "id must not include prices, marks, equity or wall-clock time" });

R.check("real_trading_red_line_enforced", codeOnly.includes("ENABLE_REAL_TRADING_MUST_BE_FALSE"));
R.check("no_console_logging_in_src", !/\bconsole\.(log|info|warn|error)\s*\(/.test(codeOnly));
R.check("no_credential_references_in_src",
  !/PAPER_LEDGER_SECRET|JEV_PREVIEW_PASSWORD|api[_-]?key|bearer\s+token/i.test(codeOnly));
R.check("wrangler_binding_is_EQ_DB",
  readFileSync(resolve(ROOT, "wrangler.toml.example"), "utf8").includes('binding = "EQ_DB"'));
R.check("wrangler_marks_isolated_db",
  /DO NOT point this binding at "jev-paper-staging"/.test(readFileSync(resolve(ROOT, "wrangler.toml.example"), "utf8")) &&
  /Night Researcher/.test(readFileSync(resolve(ROOT, "wrangler.toml.example"), "utf8")));

R.finish({
  own_files: ownFiles,
  generated_insert_prefixes: [sSql.slice(0, 40), pSql.slice(0, 40)],
  verdict_labels: { static_compliance: "VERIFIED (source inspection)" },
});
