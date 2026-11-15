/**
 * Test runner: executes each test as a REAL child process, captures the true
 * stdout / stderr / exit code, and persists them verbatim to evidence/*.json.
 * No result is synthesized: verdict is parsed from the child's RESULT_JSON
 * line (or inferred from a non-zero exit code).
 */
import { spawnSync } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const EVIDENCE = resolve(ROOT, "evidence");
mkdirSync(EVIDENCE, { recursive: true });

const TESTS = [
  ["T0", "t0_market_probe.mjs", "market_probe"],
  ["T1", "t1_multi_cycle.mjs", "multi_cycle"],
  ["T2", "t2_idempotency.mjs", "idempotency"],
  ["T3", "t3_outage_recovery.mjs", "outage_recovery"],
  ["T4", "t4_account_isolation.mjs", "account_isolation"],
  ["T5", "t5_replay_readonly.mjs", "replay_readonly"],
  ["T6", "t6_session_failclosed.mjs", "session_failclosed"],
  ["T7", "t7_static_audit.mjs", "static_audit"],
];

const results = [];
for (const [id, file, label] of TESTS) {
  const r = spawnSync(process.execPath, [resolve(HERE, file)], {
    cwd: ROOT,
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
    id, file, label,
    exitCode, verdict,
    stdout,
    stderr,
    result: parsed,
    capturedAt: new Date().toISOString(),
    node: process.version,
  };
  writeFileSync(resolve(EVIDENCE, `${id}_${label}.json`), JSON.stringify(record, null, 2));
  results.push(record);
  console.log(`[${id}] exit=${exitCode} verdict=${verdict} evidence=${id}_${label}.json`);
  if (verdict !== "PASS") {
    console.log(`   stdout(tail): ${stdout.slice(-500)}`);
    console.log(`   stderr(tail): ${stderr.slice(-300)}`);
  }
}

const allPass = results.every((r) => r.verdict === "PASS");
const summary = {
  generatedAt: new Date().toISOString(),
  node: process.version,
  overall: allPass ? "PASS" : "FAIL",
  counts: {
    total: results.length,
    pass: results.filter((r) => r.verdict === "PASS").length,
    fail: results.filter((r) => r.verdict !== "PASS").length,
  },
  results: results.map((r) => ({
    id: r.id, file: r.file, label: r.label,
    exitCode: r.exitCode, verdict: r.verdict,
    evidence: `${r.id}_${r.label}.json`,
    checks: r.result ? r.result.checks.map((c) => ({ name: c.name, pass: c.pass })) : null,
  })),
};
writeFileSync(resolve(EVIDENCE, "summary.json"), JSON.stringify(summary, null, 2));
console.log("OVERALL=" + summary.overall + " " + JSON.stringify(summary.counts));
process.exit(allPass ? 0 : 1);
