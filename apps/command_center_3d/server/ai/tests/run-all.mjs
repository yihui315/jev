// V1.6 Track B - mock contract test runner.
//
// Usage: node apps/command_center_3d/server/ai/tests/run-all.mjs
// Writes evidence JSON to reports/evidence/v16_ai/v16_ai_contract_tests.json
// Exit code 0 = all checks pass, 1 = at least one failure (no fake PASS).

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createSuite } from "./lib/harness.mjs";

import * as t1 from "./t1_schema.mjs";
import * as t2 from "./t2_provider_errors.mjs";
import * as t3 from "./t3_risk_gate.mjs";
import * as t4 from "./t4_idempotency.mjs";
import * as t5 from "./t5_data_mode.mjs";
import * as t6 from "./t6_persistence_attribution.mjs";
import * as t7 from "./t7_redaction.mjs";
import * as t8 from "./t8_role_mapping.mjs";
import * as t9 from "./t9_gateway_routes.mjs";
import * as e2e from "./e2e_real_engine.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..", "..", "..");
const OUT_DIR = join(REPO_ROOT, "reports", "evidence", "v16_ai");
const OUT_FILE = join(OUT_DIR, "v16_ai_contract_tests.json");
const MODULES = [t1, t2, t3, t4, t5, t6, t7, t8, t9, e2e];

function write(line) { process.stdout.write(line + "\n"); }

async function main() {
  const startedAt = new Date().toISOString();
  const results = [];
  for (const mod of MODULES) {
    const suite = createSuite(mod.id, mod.title);
    const t0 = Date.now();
    try {
      await mod.run(suite);
    } catch (err) {
      suite.checks.push({ name: "suite_execution", pass: false, detail: String((err && err.stack) || err) });
    }
    const durationMs = Date.now() - t0;
    const passed = suite.checks.filter((c) => c.pass).length;
    const failed = suite.checks.length - passed;
    results.push({ id: suite.id, title: suite.title, duration_ms: durationMs, passed, failed, checks: suite.checks });
    write((failed === 0 ? "[PASS] " : "[FAIL] ") + suite.id + "  checks=" + suite.checks.length + " passed=" + passed + " failed=" + failed + " " + durationMs + "ms");
    if (failed > 0) {
      for (const c of suite.checks.filter((x) => !x.pass)) write("       x " + c.name + " :: " + String(c.detail).slice(0, 300));
    }
  }

  const allChecks = results.flatMap((r) => r.checks);
  const passed = allChecks.filter((c) => c.pass).length;
  const failed = allChecks.length - passed;
  const evidence = {
    overall: failed === 0 ? "PASS" : "FAIL",
    generated_utc: new Date().toISOString(),
    started_utc: startedAt,
    runner: "apps/command_center_3d/server/ai/tests/run-all.mjs",
    environment: { node: process.version, platform: process.platform, arch: process.arch, network_calls: 0 },
    counts: { suites: results.length, checks: allChecks.length, passed, failed },
    results: results.map((r) => ({ id: r.id, title: r.title, duration_ms: r.duration_ms, passed: r.passed, failed: r.failed, checks: r.checks })),
  };

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT_FILE, JSON.stringify(evidence, null, 2) + "\n", "utf8");
  write("");
  write("TOTAL checks=" + allChecks.length + " passed=" + passed + " failed=" + failed + " => " + evidence.overall);
  write("EVIDENCE " + OUT_FILE.replace(/\\/g, "/"));
  return failed === 0 ? 0 : 1;
}

main().then((code) => { process.exitCode = code; }).catch((err) => {
  write("RUNNER_EXCEPTION " + String((err && err.stack) || err));
  process.exitCode = 2;
});
