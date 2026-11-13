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
const SUITES = [
  { phase: "A", label: "N1-N32 reverse gate", rel: "scripts/tests/human-publish-gate.test.mjs" },
  { phase: "B", label: "Phase B isolated", rel: "scripts/tests/phase-b.test.mjs" },
  { phase: "B", label: "Aegis adversarial", rel: "scripts/tests/phase-b-adversarial.test.mjs" },
  { phase: "C", label: "Provider gateway", rel: "server/ai/provider-gateway/tests/run-all.mjs" },
  { phase: "C", label: "t10 contract", rel: "server/ai/tests/t10-provider-interface.test.mjs" },
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

function main() {
  const results = SUITES.map(runSuite);
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
