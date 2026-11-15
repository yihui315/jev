/**
 * V1.6 test runner. Executes each group as a REAL child process, captures the
 * true stdout / stderr / exit code, and persists them verbatim to
 * reports/evidence/v16/*.json. No result is synthesized: the verdict is parsed
 * from the child's RESULT_JSON line. Groups also write their own evidence JSON
 * via tests/v16/lib.mjs:v16Recorder.
 */
import { spawnSync } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../../..");
const EVIDENCE = resolve(REPO_ROOT, "reports/evidence/v16");
mkdirSync(EVIDENCE, { recursive: true });

const TESTS = [
  ["V16_T1", "v16_t1_field_contract.mjs", "field_contract"],
  ["V16_T2", "v16_t2_idempotency_fields.mjs", "idempotency_fields"],
  ["V16_T3", "v16_t3_failure_classes.mjs", "failure_classes"],
  ["V16_T7", "v16_t7_schema_contract.mjs", "schema_contract"],
  ["V16_M", "v16_m1_m7_mixing.mjs", "m1_m7_mixing"],
  ["V16_U", "v16_u1_u5_units.mjs", "u1_u5_units"],
  ["V16_MIG", "v16_migration.mjs", "migration"],
  ["V16_NEG", "v16_negative_mixed_cycle.mjs", "negative_mixed_cycle"],
];

const results = [];
for (const [id, file, label] of TESTS) {
  const r = spawnSync(process.execPath, [resolve(HERE, file)], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    env: process.env,
    timeout: 180000,
  });
  const stdout = r.stdout || "";
  const stderr = r.stderr || "";
  const exitCode = r.status;
  let parsed = null;
  const line = stdout.split("\n").find((l) => l.startsWith("RESULT_JSON:"));
  if (line) {
    try { parsed = JSON.parse(line.slice("RESULT_JSON:".length)); } catch { /* keep null */ }
  }
  const verdict = parsed ? parsed.verdict : (exitCode === 0 ? "PASS_NO_RESULT_JSON" : "FAIL");
  const record = {
    id, file, label, exitCode, verdict, stdout, stderr,
    result: parsed, capturedAt: new Date().toISOString(), node: process.version,
  };
  // Per-group evidence uses the canonical <ID>_<label>.json name required by
  // CI. The group's own self-written file is group_<testId>.json (lib.mjs), so
  // the two never collide on case-insensitive filesystems.
  const evidenceFile = `${id}_${label}.json`;
  writeFileSync(resolve(EVIDENCE, evidenceFile), JSON.stringify(record, null, 2));
  results.push(record);
  const checks = parsed ? parsed.checks : [];
  console.log(`[${id}] exit=${exitCode} verdict=${verdict} checks=${checks.length} evidence=${evidenceFile}`);
  if (verdict !== "PASS") {
    console.log(`   stdout(tail): ${stdout.slice(-700)}`);
    console.log(`   stderr(tail): ${stderr.slice(-400)}`);
  }
}

const allPass = results.every((r) => r.verdict === "PASS");
const totalChecks = results.reduce((n, r) => n + (r.result ? r.result.checks.length : 0), 0);
const totalPassed = results.reduce((n, r) => n + (r.result ? r.result.checks.filter((c) => c.pass).length : 0), 0);
const summary = {
  generatedAt: new Date().toISOString(),
  node: process.version,
  overall: allPass ? "PASS" : "FAIL",
  counts: {
    total: results.length,
    pass: results.filter((r) => r.verdict === "PASS").length,
    fail: results.filter((r) => r.verdict !== "PASS").length,
  },
  assertions: { total: totalChecks, passed: totalPassed, failed: totalChecks - totalPassed },
  results: results.map((r) => ({
    id: r.id, file: r.file, label: r.label,
    exitCode: r.exitCode, verdict: r.verdict,
    evidence: `${r.id}_${r.label}.json`,
    checks: r.result ? r.result.checks.map((c) => ({ name: c.name, pass: c.pass })) : null,
  })),
};
writeFileSync(resolve(EVIDENCE, "summary.json"), JSON.stringify(summary, null, 2));
console.log("OVERALL=" + summary.overall + " " + JSON.stringify(summary.counts) + " assertions=" + JSON.stringify(summary.assertions));
process.exit(allPass ? 0 : 1);
