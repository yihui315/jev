// scripts/run-jev-verification.mjs
//
// JEV V2.0 — single-shot, reproducible verification orchestrator.
//
// Runs the ENTIRE phase test matrix in one invocation, feeds REAL results into
// the honest status model, and writes a consolidated report. This is the
// actionable form of the directive "verify JEV phase progress and blockers
// hourly" — it actually executes the real suites; it does not invent results.
//
// Usage: node scripts/run-jev-verification.mjs
// No network. No file mutation except the report artifact.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

import { buildReport, formatReport } from "../apps/command_center_3d/server/ai/jev-status.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const AI = path.join(ROOT, "apps", "command_center_3d");

/** Resolve a file by basename anywhere under root (defensive against renames). */
function findFile(root, basename) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      if (e.name === "node_modules" || e.name === ".git") continue;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(full);
      else if (e.name === basename) return full;
    }
  }
  return null;
}

// Phase, label, rel (path relative to apps/command_center_3d). `rel` is preferred;
// basename findFile is only a fallback if the explicit path is missing.
//
// NOTE: the standalone "A N1-N32" and "C t10" suites are intentionally NOT
// listed here — they are suites #10 and #11 of the Phase A case-count floor
// (runPhaseAFloor below), which covers all 215 P0/P1/P2/P3P4 cases. Listing
// them again would double-count. The remaining JEV-phase suites (B/B-adversarial/
// C-gateway/D/E-F/G) are distinct from the floor and run directly.
const SUITES = [
  { phase: "B", label: "Phase B isolated", rel: "scripts/tests/phase-b.test.mjs" },
  { phase: "B", label: "Aegis adversarial", rel: "scripts/tests/phase-b-adversarial.test.mjs" },
  { phase: "C", label: "Provider gateway", rel: "server/ai/provider-gateway/tests/run-all.mjs" },
  { phase: "D", label: "Trade-loop verify", rel: "scripts/tests/phase-d/phase-d-verify.mjs" },
  { phase: "E/F", label: "Supervisor simulation", rel: "server/ai/supervisor/tests/simulation.test.mjs" },
  { phase: "E/F", label: "Supervisor gate", rel: "server/ai/supervisor/tests/supervisor-gate.test.mjs" },
  { phase: "G", label: "Commercial verdict", rel: "server/ai/commercial/tests/commercial.test.mjs" },
];

/** Parse pass/fail counts from a suite's combined output (last match wins). */
function parseCounts(out) {
  let pass = 0;
  let fail = 0;
  const passRe = /pass[ =](\d+)/gi;
  const failRe = /fail[ =](\d+)/gi;
  let m;
  while ((m = passRe.exec(out))) pass = parseInt(m[1], 10);
  while ((m = failRe.exec(out))) fail = parseInt(m[1], 10);
  return { pass, fail };
}

function runSuite(suite) {
  // Prefer the explicit relative path; fall back to a basename search only if
  // it is missing (guards against the two run-all.mjs collision).
  const explicit = path.join(AI, suite.rel);
  const file = fs.existsSync(explicit)
    ? explicit
    : findFile(AI, path.basename(suite.rel));
  if (!file) {
    return { ...suite, pass: 0, fail: 1, exit: 1, missing: true };
  }
  // spawnSync never throws; captures BOTH stdout and stderr (run-all.mjs prints
  // its SUMMARY/RESULT lines to stderr, so we must merge them to parse counts).
  const r = spawnSync("node", [file], {
    cwd: ROOT,
    encoding: "utf8",
    timeout: 120000,
  });
  const out = (r.stdout || "") + "\n" + (r.stderr || "");
  const exit = r.status ?? 1;
  const { pass, fail } = parseCounts(out);
  return { ...suite, pass, fail, exit };
}

// Phase A — the full P0/P1/P2/P3P4 gap-closure gate (11 suites, frozen 215
// case-count floor). This is the authoritative offline acceptance gate from
// .github/workflows/ci.yml. We replicate its EXACT invocation: run each suite
// from the repo root with its full apps/... path, tee the combined
// stdout+stderr to a per-slug log, then run gate-assertion-floor.mjs against
// that log dir. The floor is FAIL-CLOSED: any suite that loses assertions,
// grows without a floor-table bump, or emits a failing summary is a hard
// violation — so a silent regression is caught (the previous narrow N1-N32-only
// coverage hid a real Y9 conflict introduced by Phase B's D1 nonce consumption).
const PHASE_A_GATE = [
  ["apps/command_center_3d/server/ai/tests/day14-p0-contracts.mjs", "1-p0-contracts"],
  ["apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs", "2-p0-runner"],
  ["apps/equity_collector_v45/tests/market-data.test.mjs", "3-market-data"],
  ["apps/command_center_3d/server/ai/evidence/tests/inference-evidence.test.mjs", "4-inference-evidence"],
  ["apps/command_center_3d/scripts/day14-research/p3-contracts.mjs", "5-p3-contracts"],
  ["apps/command_center_3d/scripts/supervisor/supervisor.test.mjs", "6-supervisor"],
  ["apps/command_center_3d/scripts/check-no-fake-live.mjs", "7-no-fake-live"],
  ["apps/command_center_3d/server/tests/http-body-read.mjs", "8-api-body-read"],
  ["apps/command_center_3d/scripts/tests/final10-judge.test.mjs", "9-final10-judge"],
  ["apps/command_center_3d/scripts/tests/human-publish-gate.test.mjs", "10-human-publish-gate"],
  ["apps/command_center_3d/server/ai/tests/t10-provider-interface.test.mjs", "11-provider-interface"],
];

function runPhaseAFloor() {
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "jev-gate-"));
  for (const [rel, slug] of PHASE_A_GATE) {
    const file = path.join(ROOT, rel);
    const r = spawnSync("node", [file], { cwd: ROOT, encoding: "utf8", timeout: 120000 });
    fs.writeFileSync(
      path.join(logDir, slug + ".log"),
      (r.stdout || "") + "\n" + (r.stderr || ""),
      "utf8",
    );
  }
  const fr = spawnSync("node", [path.join(AI, "scripts", "gate-assertion-floor.mjs"), logDir], {
    cwd: ROOT,
    encoding: "utf8",
    timeout: 120000,
  });
  const fout = (fr.stdout || "") + "\n" + (fr.stderr || "");

  const okM = fout.match(/GATE_FLOOR_OK suites=(\d+) total_cases=(\d+) exit=(\d+)/);
  const failM = fout.match(/GATE_FLOOR_FAILED suites_checked=(\d+) violations=(\d+) total_cases_seen=(\d+)/);
  const violations = [...fout.matchAll(/GATE_FLOOR_VIOLATION ([^\n]+)/g)].map((m) => m[1]);

  let pass = 0;
  let fail = 0;
  let exit = fr.status ?? 1;
  if (okM) {
    pass = Number(okM[2]);
    fail = 0;
    exit = 0;
  } else if (failM) {
    pass = Number(failM[3]);
    fail = Number(failM[2]);
    exit = 1;
  }
  return {
    phase: "A",
    label: "Phase A floor (11 suites / 215-case gate)",
    pass,
    fail,
    exit,
    floorViolations: violations,
  };
}

function main() {
  const phaseA = runPhaseAFloor();
  const results = [phaseA, ...SUITES.map(runSuite)];
  const report = buildReport({
    testResults: results,
    generatedAt: new Date().toISOString(),
  });

  const md = formatReport(report);

  // Write the artifact (evidence dir under server/ai).
  const outDir = path.join(AI, "server", "ai", "evidence");
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, "JEV_VERIFICATION_REPORT.md");
  fs.writeFileSync(outPath, md, "utf8");

  // Echo to stdout.
  console.log(md);
  console.log("");
  console.log(`REPORT WRITTEN: ${outPath}`);
  console.log(`CONSOLIDATED: totalPass=${report.totalPass} totalFail=${report.totalFail} allGreen=${report.allGreen}`);

  // Exit non-zero ONLY if a suite actually failed (so CI/hooks can gate on it).
  process.exit(report.allGreen ? 0 : 1);
}

main();
