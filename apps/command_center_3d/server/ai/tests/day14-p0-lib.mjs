/**
 * P0-F Independent QA — shared library.
 *
 * IMPORTANT (discipline):
 *  - This library NEVER imports or mutates the real reports/day14-artifacts tree.
 *    Every runner-level test executes inside an isolated temp sandbox that is a
 *    COPY of the minimal subtree. Fault injection happens on the COPY only.
 *  - No network egress: globalThis.fetch is hard-blocked in the test process and
 *    in every spawned child driver. Any attempt is recorded to stderr.
 *  - A tiny transparent harness is used instead of hiding results behind
 *    qa.pass: each check prints [PASS]/[FAIL] with real evidence and the process
 *    exit code reflects the number of failures.
 *
 * Owner: independent-qa (P0-F). Read-only with respect to implementation code.
 */
import {
  mkdtempSync, cpSync, mkdirSync, writeFileSync, readFileSync, existsSync,
  readdirSync, rmSync,
} from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

// ---- no-network guard (the QA harness itself must not touch the network) ----
const NONET = "P0-QA-NONET: network egress is forbidden in this QA process";
globalThis.fetch = (...args) => {
  try { console.error("P0-QA-NONET-ATTEMPT " + String(args[0])); } catch { /* ignore */ }
  throw new Error(NONET);
};

export const sha256Hex = (s) => createHash("sha256").update(s).digest("hex");

const HERE = dirname(fileURLToPath(import.meta.url));

/** Walk up from the test dir until we find the repo root (marker file). */
export function findRepoRoot(start = HERE) {
  let d = start;
  for (let i = 0; i < 12; i++) {
    if (existsSync(join(d, "apps", "command_center_3d", "scripts", "day14-runner.mjs"))) return d;
    const up = dirname(d);
    if (up === d) break;
    d = up;
  }
  throw new Error("repo root not found from " + start);
}

export const REPO = findRepoRoot();
export const RUNNER_SRC = join(REPO, "apps", "command_center_3d", "scripts", "day14-runner.mjs");
export const AI_DIR = join(REPO, "apps", "command_center_3d", "server", "ai");
export const LIB_DIR = join(REPO, "apps", "command_center_3d", "vendor", "nr_trusted", "functions", "_lib");
export const SRC_DIR = join(REPO, "apps", "equity_collector_v45", "src");

/** Pin a result to the exact implementation under test. */
export function implStamp() {
  let gitHead = "UNKNOWN", dirty = "UNKNOWN";
  try {
    const r = spawnSync("git", ["rev-parse", "--short", "HEAD"], { cwd: REPO, encoding: "utf8" });
    gitHead = (r.stdout || "").trim() || "UNKNOWN";
    const s = spawnSync("git", ["status", "--porcelain", "--", "apps/command_center_3d/scripts/day14-runner.mjs"], { cwd: REPO, encoding: "utf8" });
    dirty = (s.stdout || "").trim().length > 0 ? "WORKTREE_MODIFIED" : "CLEAN";
  } catch { /* ignore */ }
  const runnerSha = sha256Hex(readFileSync(RUNNER_SRC)).slice(0, 12);
  return { gitHead, dirty, runnerSha };
}

// ---- tiny transparent test harness -------------------------------------------------
export function harness(title) {
  const cases = [];
  return {
    test(name, fn) { cases.push({ name, fn }); },
    async run() {
      console.log("=== " + title + " ===");
      console.log("IMPL " + JSON.stringify(implStamp()));
      let pass = 0, fail = 0;
      for (const c of cases) {
        try {
          await c.fn();
          console.log("  [PASS] " + c.name);
          pass++;
        } catch (e) {
          console.log("  [FAIL] " + c.name + " :: " + (e && e.message ? e.message : String(e)));
          fail++;
        }
      }
      console.log("SUMMARY " + title + ": " + pass + " pass, " + fail + " fail");
      if (fail > 0) process.exitCode = 1;
      return { pass, fail };
    },
  };
}

// ---- sandbox: isolated copy of the minimal subtree --------------------------------
export const JOURNALS = ["snapshots.jsonl", "decisions.jsonl", "risk.jsonl", "provider_health.jsonl", "equity.jsonl"];

const DRIVER_SRC = `// P0-QA child driver (isolated sandbox only).
import { writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
// Hard no-network guard: any fetch attempt is recorded and throws.
globalThis.fetch = (...a) => {
  try { console.error("P0-QA-NONET-ATTEMPT " + String(a[0])); } catch {}
  throw new Error("P0-QA-NONET: network egress is forbidden in this QA child");
};
const QA_OUT = process.env.QA_OUT;
const JN = ["snapshots.jsonl","decisions.jsonl","risk.jsonl","provider_health.jsonl","equity.jsonl"];
for (const n of JN) { const p = join(QA_OUT, n); if (!existsSync(p)) writeFileSync(p, ""); }
let mod;
try {
  mod = await import("./day14-runner.mjs");
} catch (e) {
  console.log("QA_IMPORT_FAILED=" + (e && e.message ? e.message : String(e)));
  process.exitCode = 3;
}
if (mod) {
  let opts = {};
  try { opts = JSON.parse(process.env.QA_RUN_OPTS || "{}"); } catch (e) { console.log("QA_OPTS_PARSE_FAILED=" + e.message); }
  const scenario = process.env.QA_SCENARIO || "";
  if (scenario === "unverified") {
    // Inject a transport failure => market unverified => fail-closed NO_ACTION.
    opts.fetchImpl = async () => { throw new Error("P0-QA injected transport failure (no market)"); };
  } else if (scenario === "provider_error") {
    // Inject a provider timeout / transport failure (no valid AI decision).
    opts.providerImpl = async () => { throw new Error("P0-QA injected provider timeout/parse failure"); };
  } else if (scenario === "parse_failure") {
    // Inject an unparseable / missing provider intent (parse failure, no throw).
    opts.providerImpl = async () => ({ ok: true, kind: "fixture", model: "fixture-no-llm", status: "PARSE_FAILED", latencyMs: 0, costUsd: 0, intent: null });
  } else if (scenario === "costly_provider") {
    // Each cycle adds 3 USD; cumulative > 5 budget must pause (proves cumulative).
    opts.providerImpl = async () => ({ ok: true, kind: "fixture", model: "fixture-no-llm", status: "FIXTURE_NO_LLM_CALL", latencyMs: 0, costUsd: 3, intent: { action: "HOLD", symbol: "BTCUSDT", quantity: null, confidence: 0, reason: "qa-cost-injection" } });
  } else if (scenario === "buy_provider") {
    // Valid BUY intent -> must reach RISK_APPROVED via full verified context.
    opts.providerImpl = async () => ({ ok: true, kind: "fixture", model: "qa-buy-fixture", status: "QA_BUY_INTENT", latencyMs: 0, costUsd: 0, intent: { action: "BUY", symbol: "BTCUSDT", quantity: 0.01, confidence: 0.5, reason: "qa-buy-intent" } });
  } else if (scenario === "bad_symbol_provider") {
    // BUY on a non-whitelisted symbol -> must be rejected by formal gate.
    opts.providerImpl = async () => ({ ok: true, kind: "fixture", model: "qa-badsymbol-fixture", status: "QA_BAD_SYMBOL", latencyMs: 0, costUsd: 0, intent: { action: "BUY", symbol: "ORDIUSDT", quantity: 0.01, confidence: 0.99, reason: "qa-bad-symbol" } });
  } else if (scenario === "riskcheck_null") {
    // Injection point requested by implementation owner: formal gate unavailable.
    // runDay14 must fail-closed BEFORE writing any record.
    opts.riskCheckImpl = null;
  }
  try {
    const r = await mod.runDay14(opts);
    console.log("QA_RUN_RESULT=" + JSON.stringify(r));
  } catch (e) {
    console.log("QA_RUN_THREW=" + (e && e.message ? e.message : String(e)));
    process.exitCode = 4;
  }
}
`;

/**
 * Build an isolated sandbox. Copies (never mutates) the real files.
 * opts.breakFormalGate: replace the copied risk-gate.mjs with a module that
 *   throws on import, to inject a formal-gate import failure.
 */
// Exact legacy freeze values (day14-exp-v1). The real file in reports/ is
// authoritative when present; this literal is a self-contained fallback.
const LEGACY_FREEZE = {
  schema: "day14-exp-v1", realMarket: true, provider: "MiniMax-M3",
  networkContext: "sandbox_proxy_egress_binance_us", maxCycles: 60,
  virtualCapitalUsdt: 10000, maxNotionalPerTrade: 200, maxTrades: 6,
  maxExposurePct: 0.2, dailyLossBreachPct: 1, drawdownBreachPct: 2,
  callBudgetUsd: 5, feeRate: 0.001, slippagePct: 0.015,
  enableRealTrading: false, allowBuySell: false,
  riskGateModule: "server/ai/risk-gate.mjs",
};

export function buildSandbox(opts = {}) {
  const SB = mkdtempSync(join(tmpdir(), "jev-p0-qa-"));
  const scriptsDir = join(SB, "apps", "command_center_3d", "scripts");
  const aiDir = join(SB, "apps", "command_center_3d", "server", "ai");
  const libDir = join(SB, "apps", "command_center_3d", "vendor", "nr_trusted", "functions", "_lib");
  const srcDir = join(SB, "apps", "equity_collector_v45", "src");
  const OUT = join(SB, "reports", "day14-artifacts");
  mkdirSync(scriptsDir, { recursive: true });
  mkdirSync(aiDir, { recursive: true });
  mkdirSync(libDir, { recursive: true });
  mkdirSync(srcDir, { recursive: true });
  mkdirSync(OUT, { recursive: true });

  // copy the implementation under test (whatever is currently on disk)
  const runnerDst = join(scriptsDir, "day14-runner.mjs");
  if (typeof opts.runnerSource === "string") writeFileSync(runnerDst, opts.runnerSource);
  else cpSync(RUNNER_SRC, runnerDst);
  cpSync(AI_DIR, aiDir, { recursive: true });
  cpSync(LIB_DIR, libDir, { recursive: true });
  cpSync(SRC_DIR, srcDir, { recursive: true });

  if (opts.breakFormalGate) {
    writeFileSync(join(aiDir, "risk-gate.mjs"),
      'throw new Error("P0-QA injected formal-gate import failure");\n');
  }

  // Pre-seed the legacy freeze (read-only baseline). The new runner throws if
  // it is missing. We COPY the real file when available, else write the literal.
  if (!opts.noFreeze) {
    const realFreeze = join(REPO, "reports", "day14-artifacts", "config.freeze.json");
    if (existsSync(realFreeze)) cpSync(realFreeze, join(OUT, "config.freeze.json"));
    else writeFileSync(join(OUT, "config.freeze.json"), JSON.stringify(LEGACY_FREEZE, null, 2));
  }

  const driver = join(scriptsDir, "day14-p0-driver.mjs");
  writeFileSync(driver, DRIVER_SRC);

  return {
    SB, OUT, driver, runnerPath: runnerDst,
    cleanup: () => { try { rmSync(SB, { recursive: true, force: true }); } catch { /* ignore */ } },
  };
}

/** Apply a string mutation to the copied runner (for mutation-testing my own
 *  assertions — proves the test would FAIL if the protection were removed). */
export function mutateRunner(sb, from, to) {
  const src = readFileSync(sb.runnerPath, "utf8");
  if (!src.includes(from)) throw new Error("mutateRunner: pattern not found: " + from.slice(0, 60));
  writeFileSync(sb.runnerPath, src.replace(from, to));
}

// ---- layout-aware artifact locators (work for both ebc3fcc flat layout and
//      the new runs/<runId>/ layout) -------------------------------------------
function activeRunId(OUT) {
  const p = join(OUT, "active-run.json");
  if (!existsSync(p)) return null;
  try { return JSON.parse(readFileSync(p, "utf8")).runId || null; } catch { return null; }
}
export function activeRunDir(OUT) {
  const id = activeRunId(OUT);
  return id ? join(OUT, "runs", id) : null;
}
/** Find a journal / artifact by name, preferring the active run dir. */
export function findArtifact(OUT, name) {
  const dir = activeRunDir(OUT);
  const inRun = dir ? join(dir, name) : null;
  if (inRun && existsSync(inRun)) return inRun;
  const flat = join(OUT, name);
  if (existsSync(flat)) return flat;
  return inRun || flat; // preferred path even if missing (so callers can detect absence)
}
export function readState(OUT) {
  const p = findArtifact(OUT, "state.json");
  return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : null;
}

// F-12: count any (blocked) network-egress attempts observed in child drivers.
// The guard replaces globalThis.fetch in parent AND child; an attempt would log
// "P0-QA-NONET-ATTEMPT" to stderr and throw. 0 hits => zero external calls.
let NONET_HITS = 0;
let CHILD_RUNS = 0;
export function nonetAttempts() { return NONET_HITS; }
export function childRuns() { return CHILD_RUNS; }

/** Spawn the runner inside a sandbox. Returns {code, stdout, stderr}. */
export function runChild(sb, runOpts = { dry: true, cycles: 3 }, extraEnv = {}) {
  const env = {
    ...process.env,
    QA_OUT: sb.OUT,
    QA_RUN_OPTS: JSON.stringify(runOpts),
    ...extraEnv,
  };
  const r = spawnSync(process.execPath, [sb.driver], {
    cwd: sb.SB, env, encoding: "utf8", timeout: 40000, maxBuffer: 8 * 1024 * 1024,
  });
  const stderr = r.stderr || "";
  CHILD_RUNS++;
  NONET_HITS += (stderr.match(/P0-QA-NONET-ATTEMPT/g) || []).length;
  return {
    code: r.status,
    signal: r.signal,
    error: r.error ? String(r.error.message || r.error) : null,
    stdout: r.stdout || "",
    stderr,
  };
}

// ---- JSONL + reference hash-chain helpers -----------------------------------------
export function readLines(path) {
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8").split("\n").filter((l) => l.trim().length > 0);
}
export function readJsonl(path) {
  return readLines(path).map((l) => { try { return JSON.parse(l); } catch { return { __parse_error: l }; } });
}
export function writeJsonl(path, objects) {
  writeFileSync(path, objects.map((o) => (typeof o === "string" ? o : JSON.stringify(o))).join("\n") + (objects.length ? "\n" : ""));
}

/** Build a sha256-chained JSONL using the documented formula:
 *  hash = sha256(prev + "|" + JSON.stringify(rec)); line = {...rec, _prev, _hash}. */
export function chainLines(records) {
  let prev = "GENESIS";
  return records.map((rec) => {
    const hash = sha256Hex(prev + "|" + JSON.stringify(rec));
    const line = { ...rec, _prev: prev, _hash: hash };
    prev = hash;
    return line;
  });
}

/** Independent reference validator for the append-only chain. */
export function verifyChainFile(path) {
  const lines = readLines(path);
  if (lines.length === 0) return { ok: true, count: 0, error: null };
  let prev = "GENESIS";
  for (let i = 0; i < lines.length; i++) {
    let o;
    try { o = JSON.parse(lines[i]); } catch { return { ok: false, count: lines.length, error: "line " + i + " not JSON", at: i }; }
    if (o._prev !== prev) return { ok: false, count: lines.length, error: "line " + i + " _prev != previous _hash (chain break)", at: i, gotPrev: o._prev, expectedPrev: prev };
    const rec = { ...o };
    delete rec._prev; delete rec._hash;
    const expect = sha256Hex(o._prev + "|" + JSON.stringify(rec));
    if (expect !== o._hash) return { ok: false, count: lines.length, error: "line " + i + " _hash mismatch (tampered content)", at: i };
    prev = o._hash;
  }
  return { ok: true, count: lines.length, error: null };
}

/** Read the runner source (static checks). */
export function runnerSource() { return readFileSync(RUNNER_SRC, "utf8"); }

/** Read a historical runner source from git (for baseline / "test has teeth" proofs). */
export function runnerSourceAt(ref) {
  const r = spawnSync("git", ["show", ref + ":apps/command_center_3d/scripts/day14-runner.mjs"],
    { cwd: REPO, encoding: "utf8", maxBuffer: 4 * 1024 * 1024 });
  if (r.status !== 0) throw new Error("git show " + ref + " failed: " + (r.stderr || ""));
  return r.stdout;
}

/** Basic assertion helpers with clear messages. */
export function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
export function logEvidence(label, value) {
  const s = typeof value === "string" ? value : JSON.stringify(value);
  console.log("    EVIDENCE " + label + " = " + s);
}
