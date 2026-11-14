/**
 * check-no-fake-live.mjs — P7 static assertions (Node stdlib only).
 *
 * Why this exists: the P7 "no fabricated live state" invariants must be
 * checkable BEFORE `npm ci` and WITHOUT any dependency or network, because
 * this gate is the FIRST run step in ci.yml (F-21). It therefore uses pure
 * Node stdlib and static source assertions.
 *
 * P0-B status update (2026-11-12): dependencies ARE now installed locally
 * (`npm ci` EXIT=0 / 239 packages, lockfile byte-identical) and both
 * `npx tsc --noEmit` and `npx vitest run` EXIT=0 (22 files / 217 passed /
 * 3 skipped). `vite.config.ts` excludes these hand-written `node` runners
 * from vitest collection. Real GitHub Actions remains NOT_RUN: 0 push,
 * 0 Run ID. This script is a dependency-free PRE-FLIGHT gate, not a
 * substitute for, or evidence of, a real CI run.
 *
 * It is a STATIC assertion script, not a unit test runner. It reads source
 * text and asserts the "no fabricated state" invariants hold in the P7 files.
 *
 * Run: node apps/command_center_3d/scripts/check-no-fake-live.mjs
 *      (exit 0 = all assertions passed, non-zero = an assertion failed)
 */
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const HERE = dirname(fileURLToPath(import.meta.url));          // .../scripts
const APP = dirname(HERE);                                     // .../apps/command_center_3d
const REPO = dirname(dirname(APP));                             // repo root

let core = 0;
let extra = 0;
function check(group, name, fn) {
  fn();
  if (group === "CORE") core++;
  else extra++;
  console.log(`  [PASS][${group}] ${name}`);
}
function read(rel) {
  const p = join(REPO, rel);
  return existsSync(p) ? readFileSync(p, "utf8") : null;
}
/** Strip block comments and full-line // comments so prose is not a false positive. */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const REL = {
  mockDecisions: "apps/command_center_3d/src/data/mockDecisions.ts",
  badge: "apps/command_center_3d/src/components/ProvenanceBadge.tsx",
  desk: "apps/command_center_3d/src/components/TradingDesk.tsx",
  pageContainer: "apps/command_center_3d/src/views/trading/components/PageContainer.tsx",
  paperAccount: "apps/command_center_3d/src/trading/pages/PaperAccount.tsx",
  tradingWorkbench: "apps/command_center_3d/src/trading/pages/TradingWorkbench.tsx",
  aiCommandCenter: "apps/command_center_3d/src/trading/pages/AICommandCenter.tsx",
  marketUniverse: "apps/command_center_3d/src/trading/pages/MarketUniverse3D.tsx",
  app: "apps/command_center_3d/src/App.tsx",
  ci: ".github/workflows/ci.yml",
  prov: ".github/workflows/v16-market-provenance.yml",
  serverApi: "apps/command_center_3d/server/trading_api.mjs",
};

const src = {};
for (const [k, rel] of Object.entries(REL)) src[k] = read(rel);

console.log("=== check-no-fake-live.mjs — P7 static assertions (Node stdlib only) ===");
console.log("FILE_PRESENCE " + JSON.stringify(Object.fromEntries(Object.entries(src).map(([k, v]) => [k, v !== null]))));
if (src.mockDecisions === null) {
  console.log("INFO: src/data/mockDecisions.ts ABSENT at this HEAD — the brief's path came");
  console.log("      from a research transcription and was wrong; no file was invented");
  console.log("      and no fictitious 'fix' of two absent LIVE tags is claimed.");
}

const PAGES = ["paperAccount", "tradingWorkbench", "aiCommandCenter", "marketUniverse"];

// ---- CORE A1: hardcoded LIVE tags are ZERO in the 4 trading pages -----------
check("CORE", "A1 hardcoded label=\"LIVE\" is ZERO across the 4 trading pages", () => {
  for (const k of PAGES) {
    assert.ok(src[k] !== null, `${k} missing`);
    const code = stripComments(src[k]);
    assert.ok(!/label="LIVE"/.test(code), `${k} still has label="LIVE"`);
    assert.ok(!/["']LIVE["']/.test(code), `${k} still has a LIVE string literal`);
  }
});

// ---- CORE A2: all 16 tags now derive from provenance -----------------------
check("CORE", "A2 all 16 prior LIVE tags replaced by provenance-driven deriveStatusLabel()", () => {
  const expected = { paperAccount: 8, tradingWorkbench: 6, aiCommandCenter: 1, marketUniverse: 1 };
  let total = 0;
  for (const [k, n] of Object.entries(expected)) {
    const got = (src[k].match(/deriveStatusLabel\(/g) || []).length;
    assert.equal(got, n, `${k}: expected ${n} deriveStatusLabel() calls, got ${got}`);
    total += got;
  }
  assert.equal(total, 16, "all 16 hardcoded LIVE tags must be replaced");
  assert.ok(src.marketUniverse.includes('"BACKFILL"'), "BACKFILL branch must be preserved, never upgraded to LIVE");
  assert.ok(/USE_MOCK/.test(src.pageContainer) && /MARKET_DATA_SOURCE/.test(src.pageContainer),
    "label must derive from USE_MOCK + MARKET_DATA_SOURCE");
});

// ---- CORE A3: TradingDesk two-shape union + NOT_MEASURED ------------------
check("CORE", "A3 TradingDesk.tsx uses the two-shape discriminated union + NOT_MEASURED", () => {
  const d = src.desk;
  assert.ok(d !== null, "TradingDesk.tsx missing");
  assert.ok(/export type DeskCard\s*=/.test(d), "DeskCard union type missing");
  assert.ok(/available:\s*true/.test(d) && /available:\s*false/.test(d), "both union shapes must exist");
  assert.ok(/if \(!card\.available\)/.test(d), "available:false branch missing");
  assert.ok(d.includes("NOT_MEASURED"), "NOT_MEASURED literal missing");
  assert.ok(!/\?\?\s*0/.test(stripComments(d)), "no ?? 0 zero fallback");
  assert.ok(!/reconcile:\s*['"]MATCH/.test(d), "no hardcoded MATCH verdict");
  assert.ok(/TRADING_DESK_MOUNT\s*=\s*"DECLARED_NOT_WIRED"/.test(d), "mount status must be registered");
});

// ---- CORE A4: missing provenance never resolves REAL ----------------------
check("CORE", "A4 ProvenanceBadge never resolves REAL for missing provenance", () => {
  const b = src.badge;
  assert.ok(b !== null, "ProvenanceBadge.tsx missing");
  assert.ok(/if \(!provenance\) return "NOT_MEASURED";/.test(b), "missing provenance must early-return NOT_MEASURED");
  const guard = b.indexOf('return "NOT_MEASURED";');
  const real = b.indexOf('return "REAL";');
  assert.ok(guard !== -1 && real !== -1 && guard < real, "REAL must not be reachable before the missing-provenance guard");
  assert.ok(/isReal === true/.test(b), "REAL requires explicit isReal===true evidence");
  assert.equal((b.match(/return "REAL";/g) || []).length, 1, "exactly one REAL return, behind the guard");
});

// ---- CORE A5: CONNECTED is evidence-only, NOT globally forbidden ----------
check("CORE", "A5 CONNECTED is evidence-only: mock/fixture can NEVER produce it; 3 fields distinct", () => {
  const b = src.badge;
  assert.ok(/export type ProviderVerdict\s*=\s*"BLOCKED" \| "UNVERIFIED"/.test(b), "verdict field missing");
  assert.ok(/export type ProviderStatus\s*=/.test(b) && /NOT_CONFIGURED/.test(b) && /CONFIGURED_LOCAL_ONLY/.test(b),
    "provider status (credential fact) field missing");
  assert.ok(/export type TestProviderStatus\s*=\s*"CONNECTED" \| "BLOCKED" \| "FAILED"/.test(b),
    "testProvider status field missing");
  // CONNECTED is a LEGAL, evidence-derived state — it is NOT a forbidden word.
  assert.ok(b.includes('"CONNECTED"'), "CONNECTED must exist as an evidence-derived state");
  // ...but fixture / mock / missing-credential states must resolve to BLOCKED first.
  assert.ok(/if \(input\.fixture \|\| !input\.hasCredentials\) return "BLOCKED";/.test(b),
    "fixture/mock/missing-credential early-BLOCKED guard missing");
  const blockedGuard = b.indexOf('return "BLOCKED";');
  const connected = b.indexOf('return "CONNECTED";');
  assert.ok(blockedGuard !== -1 && connected !== -1 && blockedGuard < connected,
    "CONNECTED must be gated behind real-evidence checks");
  assert.ok(/evidenceComplete/.test(b) && /response_sha256/.test(b) && /http_status/.test(b) && /fetched_at/.test(b),
    "real probe evidence fields required");
  assert.ok(/export function providerNeedsAction/.test(b), "real-provider-shown + unverified-card alarm missing");
  // Deliberately NO assertion that 'CONNECTED' is absent from source (that would be wrong).
});

// ---- CORE A6: bar count is NOT strategy performance -----------------------
check("CORE", "A6 bar / backfill counts are NOT presented as strategy performance", () => {
  const d = src.desk;
  assert.ok(!/oos_samples|backtest_runs|win_rate|backfill.*(pnl|profit)/i.test(d),
    "desk must not treat bar counts as performance");
  assert.ok(/bar 数是样本基础、不是策略绩效/.test(d), "desk must state bar-count is not performance");
  const api = src.serverApi;
  assert.ok(api !== null, "server/trading_api.mjs missing");
  assert.ok(/sample_foundation/.test(api), "server sample_foundation contract missing");
  assert.ok(/net_return:\s*null/.test(api), "server performance fields must stay null");
});

// ---- CORE A7: no hardcoded equity / MATCH in P7 + non-test src ------------
check("CORE", "A7 no hardcoded equity:10000 / reconcile:'MATCH' in P7 + non-test src", () => {
  const banned = [/equity:\s*10000/, /equity\s*=\s*10000/, /reconcile:\s*['"]MATCH/];
  const p7 = [src.badge, src.desk, src.pageContainer, src.app, ...PAGES.map((k) => src[k])];
  for (const t of p7) {
    if (t === null) continue;
    for (const re of banned) assert.ok(!re.test(t), `P7 source contains ${re}`);
  }
  const files = [];
  (function walk(dir) {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(ts|tsx)$/.test(e.name) && !/\.test\.(ts|tsx)$/.test(e.name)) files.push(p);
    }
  })(join(APP, "src"));
  for (const f of files) {
    const t = readFileSync(f, "utf8");
    for (const re of banned) assert.ok(!re.test(t), `${f} contains ${re}`);
  }
  console.log(`      scanned non-test src files: ${files.length}`);
});

// ---- EXTRA A8: B.4 wiring + B.2 N/A ---------------------------------------
check("EXTRA", "A8 ProvenanceBadge wired into PageContainer.tsx; src/App.tsx untouched (B.2=N/A)", () => {
  const pc = src.pageContainer;
  assert.ok(pc !== null && pc.includes("ProvenanceBadge"), "badge not wired into PageContainer");
  assert.ok(/<ProvenanceBadge provenance=/.test(pc), "badge not rendered in PageContainer");
  assert.ok(/export function deriveStatusLabel/.test(pc), "provenance label helper missing");
  assert.ok(!src.app.includes("ProvenanceBadge"), "src/App.tsx must be untouched (B.2=N/A)");
});

/**
 * Drop full-line comments so a text assertion can only be satisfied by LIVE code.
 * YAML uses `#`; the JS inside a `run: |` block scalar uses `//`. Both forms were
 * actually used to defeat an earlier version of these guards (see Y2 / Y3 / Y4),
 * so this helper is the shared discipline rather than a one-off.
 */
function stripLineComments(lines) {
  return lines.filter((x) => !/^\s*(\/\/|#)/.test(x));
}

// ---- YAML structural self-check (NOT a YAML parser) ------------------------
function yamlLines(rel) {
  const t = read(rel);
  assert.ok(t !== null, rel + " missing");
  const lines = t.split("\n");
  lines.forEach((ln, i) => {
    assert.ok(!ln.includes("\t"), `${rel}:${i + 1} has a TAB`);
    assert.equal((ln.match(/\[/g) || []).length, (ln.match(/\]/g) || []).length,
      `${rel}:${i + 1} unbalanced [ ]`);
  });
  return lines;
}

/**
 * F-23: return the branch list of a workflow trigger (`push:` / `pull_request:`).
 *
 * Deliberately structure-aware but NOT a YAML parser: it walks the `on:` block,
 * stops at the next 2-space trigger key or at the next top-level key, skips
 * full-line comments, and reads the inline `branches: [ "a", "b" ]` form the
 * repo uses. If a workflow is later rewritten to the block-list form
 * (`branches:\n  - a`) this returns [] and every caller assertion fails LOUD —
 * it fails closed, never silently passes.
 */
function workflowTriggerBranches(lines, key) {
  const start = lines.findIndex((x) => new RegExp("^  " + key + ":\\s*$").test(x));
  if (start === -1) return null;
  const out = [];
  for (let i = start + 1; i < lines.length; i++) {
    const ln = lines[i];
    if (/^\S/.test(ln)) break;          // left the `on:` block entirely
    if (/^  \S/.test(ln)) break;        // next trigger key (e.g. workflow_dispatch:)
    if (/^\s*#/.test(ln)) continue;     // prose is not configuration
    const m = ln.match(/branches\s*:\s*\[([^\]]*)\]/);
    if (m) {
      for (const raw of m[1].split(",")) {
        const v = raw.trim().replace(/^["']|["']$/g, "");
        if (v) out.push(v);
      }
    }
  }
  return out;
}

check("EXTRA", "Y1 v16-market-provenance.yml: v16 branches + feature/jev-** push; PR keeps release rule AND gains feature/v16-**", () => {
  const l = yamlLines(REL.prov);

  const push = workflowTriggerBranches(l, "push");
  const pr = workflowTriggerBranches(l, "pull_request");
  assert.ok(push !== null, "v16: `push:` trigger block not found");
  assert.ok(pr !== null, "v16: `pull_request:` trigger block not found");

  // --- pre-existing coverage must remain (additive-only rule) ---
  assert.ok(push.includes("feature/v16-market-provenance-hardening"), "original branch entry removed");
  assert.ok(push.includes("feature/v16-p0-gap-closure"), "new branch missing");
  assert.ok(push.includes("feature/v16-**"), "wildcard branch missing");

  // --- F-22-C: the integration branch family must actually trigger this gate ---
  assert.ok(push.includes("feature/jev-**"),
    "F-22-C: push trigger must cover feature/jev-** (e.g. feature/jev-v2-ops-integration)");

  // --- C14: a PR whose BASE is a v16 branch must run this workflow ---
  assert.ok(pr.includes("feature/v16-**"),
    "C14: pull_request trigger must cover feature/v16-** or the PR runs zero checks");
  // ...and the same for a PR whose BASE is another ops branch. This entry was
  // added to the workflow and left UNASSERTED: deleting it kept all 13 cases
  // green, which is exactly the F-23 "a guard nobody counts" defect reintroduced
  // by the commit that fixed F-23. Asserted here so the entry cannot be dropped
  // silently.
  assert.ok(pr.includes("feature/jev-**"),
    "F-22-C: pull_request trigger must cover feature/jev-** or an ops-base PR runs zero checks");
  // ...and the release-target rule is RETAINED, not traded away. This is the
  // stronger form of the old `l.some(x => /release\/jcc-v1\.3-final/)` check:
  // it now asserts the entry is inside `pull_request.branches`, not merely
  // somewhere in the file.
  assert.ok(pr.includes("release/jcc-v1.3-final"),
    "PR target rule changed — release/jcc-v1.3-final must be RETAINED in pull_request.branches");

});

// ---- Y3 (R15 / F-25): the v16 Track B assertion floor must survive ----------
// F-25 added a frozen `suites/checks` floor to the Track B step in
// v16-market-provenance.yml, closing the same defect class as C12/F-22-G on the
// second workflow. But the floor is only text in a YAML file: a later "cleanup"
// could delete it and the step would silently return to trusting exit codes
// alone. This check makes that deletion a red test.
//
// WHY THIS IS ITS OWN `check()` AND NOT A FEW MORE LINES INSIDE Y1: an assertion
// that lives inside another assertion is unguarded — delete it and the enclosing
// check still passes. Making it a separate check puts it under the C12/F-22-G
// exact-count floor (`7-no-fake-live`), so deleting THIS check is itself a hard
// failure. That is the whole point of having a floor; it would be self-defeating
// to add guards in a way the floor cannot see.
check("EXTRA", "Y3 v16 Track B: assertion-count floor present in LIVE code, numbers frozen, every V16_AI_* failure fatal, no continue-on-error", () => {
  const l = yamlLines(REL.prov);
  const live = stripLineComments(l);
  const v16Live = live.join("\n");

  // A text-only grep can be satisfied by the very comment-out that disables the
  // code: my first version used `l.join("\n")` and stayed GREEN with the floor
  // commented out, because the string it looked for was still present, just dead.
  assert.ok(/V16_AI_CHECK_COUNT_CHANGED/.test(v16Live),
    "R15/F-25: v16 Track B assertion-count floor is GONE — the step is back to " +
    "trusting exit codes only, so a suite that silently loses checks would pass");
  assert.ok(/s\.counts\.suites\s*!==\s*10\s*\|\|\s*s\.counts\.checks\s*!==\s*120/.test(v16Live),
    "R15/F-25: v16 Track B floor numbers drifted from the frozen suites=10 / checks=120");

  // The floor is only a floor if violating it is fatal. Guard this as an
  // INVARIANT over the WHOLE workflow, not just the Track B lines: every
  // `console.error('V1.5_...` / `console.error('V16_...` must carry
  // `process.exit(1)` on the same line. Softening ANY of the eight (V1.5 95
  // assertions, V16 8 groups, V16 104 assertions, and Track B's three) used to
  // go unnoticed — found because a mis-targeted mutation softened one of them
  // and the narrower, Track-B-only guard stayed green.
  const v16Errs = live.filter((x) => /console\.error\(\s*['"](V1\.5_|V16_)/.test(x));
  assert.ok(v16Errs.length >= 8,
    `R15/F-25: expected at least 8 fatal V1.5_/V16_ guards in this workflow, found ${v16Errs.length}`);
  const soft = v16Errs.filter((x) => !/process\.exit\(\s*[1-9]/.test(x));
  assert.ok(soft.length === 0,
    "R15/F-25: a V1.5_/V16_ error is no longer fatal on its own line (log-and-continue): " +
    JSON.stringify(soft.map((s) => s.trim())));

  // The floor must stay hard-fail: no continue-on-error on the Track B step.
  const tbStart = l.findIndex((x) => /-\s*name:\s*Track B/.test(x));
  assert.ok(tbStart !== -1, "v16 Track B step not found");
  let tbEnd = l.length;
  for (let i = tbStart + 1; i < l.length; i++) {
    if (/^      - (name|uses):/.test(l[i])) { tbEnd = i; break; }
  }
  const tbStep = l.slice(tbStart, tbEnd);
  const tbLive = stripLineComments(tbStep);
  assert.ok(tbLive.length > 3, "v16 Track B step looks truncated — refusing to certify its shape");
  assert.ok(!tbLive.some((x) => /^\s*continue-on-error\s*:/.test(x)),
    "v16 Track B step must NOT have continue-on-error");
});

// ---- Y4 (F-26): the floor must read evidence produced by THIS run -----------
// The evidence JSON is TRACKED IN GIT. Without a precondition the verifier
// certifies whatever copy the checkout contains. Proven end-to-end (verbatim step
// extracted from the YAML and run under `bash -eo pipefail`, the same shell
// Actions uses): replacing the runner with a no-op that exits 0 and writes
// nothing STILL prints V16_AI_CONTRACT_OK and exits 0. With the `rm -f`
// precondition the same no-op fails closed (MODULE_NOT_FOUND, exit 1), while the
// real path stays green (120/120 checks).
check("EXTRA", "Y4 v16 Track B: evidence JSON is deleted BEFORE the runner runs, so a stale git-tracked copy cannot certify a run that never happened", () => {
  const l = yamlLines(REL.prov);
  const runnerIdx = l.findIndex((x) => /^\s*node server\/ai\/tests\/run-all\.mjs\s*$/.test(x));
  assert.ok(runnerIdx !== -1, "F-26: cannot locate the Track B `node server/ai/tests/run-all.mjs` line");
  const preRunner = stripLineComments(l.slice(0, runnerIdx)).join("\n");
  assert.ok(/rm\s+-f\s+\.\.\/\.\.\/reports\/evidence\/v16_ai\/v16_ai_contract_tests\.json/.test(preRunner),
    "F-26: the Track B evidence file is no longer deleted before the runner runs — a stale " +
    "git-tracked JSON can certify a run that never happened (this is a proven fail-open)");
  // Deleting it must not be paired with a tolerated failure: if the evidence is
  // missing afterwards the step must die, not warn. `bash -e` gives that for
  // free, but assert the runner is invoked plainly (no `|| true`, no `set +e`).
  const runnerLine = l[runnerIdx];
  assert.ok(!/\|\|\s*true/.test(runnerLine) && !/;\s*true\s*$/.test(runnerLine),
    "F-26: the Track B runner line tolerates its own failure (`|| true`) — evidence may be missing silently");
});

// ---- Y5 (R22): the V1.6 step has the identical fail-open, same fix required --
// `reports/evidence/v16/summary.json` is tracked in git too. Verified the same
// way as F-26: with the `rm -f` removed and `node tests/v16/run-all.mjs` replaced
// by a no-op, the step printed V16_SUITE_OK ... assertions={"total":104,...} and
// exited 0 — the stale copy certified a run that never happened, and it even
// satisfied the newly-frozen 104 because the stale file carries 104. With the
// `rm -f` present the same no-op dies MODULE_NOT_FOUND; the real path is green
// (8/8 groups, 104/104 assertions).
// This is a separate check for the same reason as Y3/Y4: a guard nobody counts
// is a guard nobody keeps.
check("EXTRA", "Y5 v16 V1.6: summary.json is deleted BEFORE tests/v16/run-all.mjs, closing the same stale-evidence fail-open as Track B", () => {
  const l = yamlLines(REL.prov);
  const runnerIdx = l.findIndex((x) => /^\s*node tests\/v16\/run-all\.mjs\s*$/.test(x));
  assert.ok(runnerIdx !== -1, "R22: cannot locate the V1.6 `node tests/v16/run-all.mjs` line");
  const preRunner = stripLineComments(l.slice(0, runnerIdx)).join("\n");
  assert.ok(/rm\s+-f\s+\.\.\/\.\.\/reports\/evidence\/v16\/summary\.json/.test(preRunner),
    "R22: the V1.6 evidence file is no longer deleted before the runner runs — " +
    "reports/evidence/v16/summary.json is tracked in git, so a stale copy can " +
    "certify a run that never happened (verified fail-open, same as F-26)");
  const runnerLine = l[runnerIdx];
  assert.ok(!/\|\|\s*true/.test(runnerLine), "R22: the V1.6 runner line tolerates its own failure (`|| true`)");
});

/**
 * F-23 — declared gate suite set. This is the single source of truth Y2 compares
 * the workflow against. Every entry here MUST appear in ci.yml exactly once, as
 * a line-start `node <path>` invocation guarded by a `test -f <path>`.
 *
 * Keep in sync with the `SUITES` table in scripts/gate-assertion-floor.mjs.
 */
const GATE_SUITES = [
  "apps/command_center_3d/server/ai/tests/day14-p0-contracts.mjs",
  "apps/command_center_3d/server/ai/tests/day14-p0-runner.mjs",
  "apps/equity_collector_v45/tests/market-data.test.mjs",
  "apps/command_center_3d/server/ai/evidence/tests/inference-evidence.test.mjs",
  "apps/command_center_3d/scripts/day14-research/p3-contracts.mjs",
  "apps/command_center_3d/scripts/supervisor/supervisor.test.mjs",
  "apps/command_center_3d/scripts/check-no-fake-live.mjs",
  "apps/command_center_3d/server/tests/http-body-read.mjs",
  "apps/command_center_3d/scripts/tests/final10-judge.test.mjs",
  "apps/command_center_3d/scripts/tests/human-publish-gate.test.mjs",
  "apps/command_center_3d/server/ai/tests/t10-provider-interface.test.mjs",
];

check("EXTRA", "Y2 ci.yml: gate suites == declared gate suite set (comment-proof), each test -f hard-fail; tsc/vitest kept; gate is first run step; no continue-on-error", () => {
  const l = yamlLines(REL.ci);

  // ---- F-23 fix: STRIP comments, ANCHOR at line start, compare SETS --------
  // The old rule was `l.some(x => x.includes(s) && /node /.test(x))`, which
  // matched a commented-out line just as happily as a live one, tolerated a
  // wrong declared count ("5 gate scripts"), and could not notice a suite moved
  // somewhere else. Collect only real configuration lines instead.
  const invocations = [];
  for (const raw of l) {
    if (/^\s*#/.test(raw)) continue;                  // full-line comment = prose
    const m = raw.match(/^\s*node\s+(\S+\.mjs)\b/);   // line-start anchored
    if (m) invocations.push(m[1]);
  }

  assert.equal(new Set(invocations).size, invocations.length,
    `duplicate gate suite invocation: ${JSON.stringify(invocations)}`);

  const got = [...new Set(invocations)].sort();
  const want = [...GATE_SUITES].sort();
  const missing = want.filter((x) => !got.includes(x));
  const unexpected = got.filter((x) => !want.includes(x));
  // EQUALITY, not `>=` and not a bare count: an added, removed, renamed or
  // commented-out suite must all fail. Commenting a line out drops it from
  // `got`, so `missing` becomes non-empty — that is the bypass this closes.
  assert.ok(missing.length === 0 && unexpected.length === 0,
    "ci.yml gate suite set drifted from the declared set:\n" +
    `  missing(${missing.length}): ${JSON.stringify(missing)}\n` +
    `  unexpected(${unexpected.length}): ${JSON.stringify(unexpected)}\n` +
    `  declared count=${GATE_SUITES.length} found count=${got.length}`);

  // ---- per-suite `test -f` hard-fail must also survive comment-stripping ---
  for (const s of GATE_SUITES) {
    assert.ok(
      l.some((x) => !/^\s*#/.test(x) && x.includes(s) && /test -f/.test(x)),
      `${s}: missing test -f hard-fail`
    );
  }

  // ---- the pipeline's non-gate guardrails must remain present ---------------
  assert.ok(l.some((x) => !/^\s*#/.test(x) && /npx tsc --noEmit/.test(x)), "tsc step removed");
  assert.ok(l.some((x) => !/^\s*#/.test(x) && /npx vitest run/.test(x)), "vitest step removed");

  // ---- the floor table must stay in sync with the declared gate suite set ---
  // The assertion-count floor lives in a separate script that cannot read this
  // list; without this guard a new suite could be added to ci.yml + GATE_SUITES
  // while the floor silently kept checking only the old ones (the new suite
  // would then be back to "trust the exit code").
  //
  // COUNT IS NOT ENOUGH. An earlier version compared only the NUMBER of
  // `slug:` entries. Adding `9-foo` to ci.yml + GATE_SUITES while deleting
  // `1-p0-contracts` from the floor table keeps the count equal, so it stays
  // GREEN while a whole suite silently loses its case-count floor. That is the
  // same "add without registering" bypass this block was written to close,
  // one level up — so it is closed the same way: by SET equality, exactly like
  // the ci.yml suite comparison above.
  //
  // The floor table is keyed by slug, which is a label, not a path; the file a
  // slug guards is named in its `label:`. So the comparison is on basenames,
  // and basename uniqueness is asserted first — otherwise two suites could map
  // to one floor entry and the mapping would stop being injective.
  const floorSrc = read("apps/command_center_3d/scripts/gate-assertion-floor.mjs");
  assert.ok(floorSrc !== null, "scripts/gate-assertion-floor.mjs missing");

  const floorSlugList = [...floorSrc.matchAll(/^\s{4}slug:\s*"([^"]+)"/gm)].map((m) => m[1]);
  assert.equal(new Set(floorSlugList).size, floorSlugList.length,
    `duplicate slug in the floor table: ${JSON.stringify(floorSlugList)}`);

  // A `label:` with no `.mjs` would silently drop out of the set below and then
  // show up as "missing" — fail-closed, which is what we want, but say so.
  const floorLabels = [...floorSrc.matchAll(/^\s{4}label:\s*"[^"]*?([A-Za-z0-9_.-]+\.mjs)/gm)].map((m) => m[1]);
  assert.equal(floorLabels.length, floorSlugList.length,
    `${floorSlugList.length} floor suites but only ${floorLabels.length} labels name a .mjs file`);

  const wantBase = GATE_SUITES.map((p) => p.split("/").pop());
  assert.equal(new Set(wantBase).size, wantBase.length,
    `two gate suites share a basename, so a basename-keyed floor comparison would be ambiguous: ${JSON.stringify(wantBase)}`);

  const floorSet = new Set(floorLabels);
  const missingInFloor = wantBase.filter((b) => !floorSet.has(b));
  const unexpectedInFloor = [...floorSet].filter((b) => !wantBase.includes(b));
  assert.ok(missingInFloor.length === 0 && unexpectedInFloor.length === 0,
    "gate-assertion-floor.mjs drifted from ci.yml/GATE_SUITES:\n" +
    `  floor declares ${floorSlugList.length} suites, GATE_SUITES declares ${GATE_SUITES.length}\n` +
    `  missing from floor(${missingInFloor.length}): ${JSON.stringify(missingInFloor)}\n` +
    `  unexpected in floor(${unexpectedInFloor.length}): ${JSON.stringify(unexpectedInFloor)}`);

  // ---- F-22-C: ci.yml must publish checks on PRs based on feature/**** -----
  const ciPush = workflowTriggerBranches(l, "push");
  const ciPr = workflowTriggerBranches(l, "pull_request");
  assert.ok(ciPush !== null && ciPush.includes("feature/**"), "ci.yml push must still cover feature/**");
  assert.ok(ciPr !== null && ciPr.includes("feature/**"),
    "F-22-C: ci.yml pull_request.branches must include feature/** or a PR based on an integration branch shows 0 checks");

  // ---- F-21 regression guard: the gate must be the FIRST run step ----------
  // F-21 was "the gate is reported as `skipped` whenever an upstream step is
  // red". The fix is ordering, so the ordering is what gets asserted: the gate
  // step must precede npm ci / tsc / build / vitest. This makes a future
  // re-shuffle of ci.yml a red test instead of a silent loss of the gate.
  //
  // All lookups are bounded to the `build-and-test` job: a future job that also
  // happens to contain an "Install" or "Typecheck" step must not be able to
  // make this check pass (or fail) by accident.
  const jobStart = l.findIndex((x) => /^  build-and-test:\s*$/.test(x));
  assert.ok(jobStart !== -1, "build-and-test job not found");
  const jobEndRel = l.findIndex((x, i) => i > jobStart && /^  [A-Za-z0-9_-]+:\s*$/.test(x));
  const JOB_END = jobEndRel === -1 ? l.length : jobEndRel;
  const inJob = (re) => {
    for (let i = jobStart; i < JOB_END; i++) if (re.test(l[i])) return i;
    return -1;
  };

  const start = inJob(/-\s*name:\s*P0\/P1\/P2\/P3P4 gap-closure gate/);
  assert.ok(start !== -1, "gate step block not found inside build-and-test");
  const installIdx = inJob(/^\s*- name: Install\s*$/);
  assert.ok(installIdx !== -1, "Install step not found inside build-and-test");
  assert.ok(start < installIdx,
    "F-21: the gate must run BEFORE `npm ci` (Install) so an install failure cannot mask it");
  for (const stepName of ["Typecheck", "Build", "Unit tests"]) {
    const i = inJob(new RegExp("^\\s*- name: " + stepName));
    assert.ok(i !== -1, `step "${stepName}" not found inside build-and-test`);
    assert.ok(start < i, `F-21: the gate must run before the "${stepName}" step`);
  }

  // ---- hard-fail semantics: no continue-on-error inside the gate step ------
  // Anchor on the `- name:` line (NOT the preceding comment block, which
  // literally contains the words "No continue-on-error: ..."). The gate step
  // must not carry a real `continue-on-error:` YAML key; comment lines are
  // documentation, not configuration, so they are excluded from this check.
  // The real-data-probe job's `continue-on-error: true` is out of JOB_END and is
  // intentionally preserved (task A: existing steps untouched).
  assert.ok(
    !l.slice(start, JOB_END).some((x) => !x.trim().startsWith("#") && /^\s*continue-on-error\s*:/.test(x)),
    "gate step / job must NOT have continue-on-error"
  );
  console.log(`      ci.yml gate suites: ${got.length} (declared ${GATE_SUITES.length}), all with test -f hard-fail`);
  console.log(`      ci.yml gate step index ${start} < Install ${installIdx} (F-21 ordering asserted)`);
});
// -----------------------------------------------------------------------------
// Y9 — REGISTRATION, NOT A SAFETY CLAIM.
//
// (Named Y9 so the floor comment, the report section and any mutation list can
// reference it BY NAME. Two facts sharing one name is how "three lines are not
// three guards" happens a second time.
// A literal-tautology scan is a DIFFERENT check. It is not written yet, so it
// gets no number here — citing a number for a check that does not exist
// announces something that is not there, which is the same error as quoting a
// future floor value as if it had been measured.)
//
// The frozen engine ships a complete service-to-service trust implementation:
// HMAC-SHA256 over `v1.<ts>.<nonce>.<actor>.<sha256(rawBody)>`, a GitHub OIDC
// verifier, single-use nonce bookkeeping, and the canonical payload builder.
// NOTHING IN THIS TREE CALLS ANY OF IT. Measured, not assumed:
//
//   verifyServiceSignature  verifyGithubOidc  consumeNonce
//   canonicalServicePayload  signServiceBody                    -> 0 call sites
//
// `service-auth.mjs` is imported exactly twice, both times only for
// `sha256Hex` (strategy-events.mjs, verification.mjs).
//
// This case asserts the ZERO, because the zero is a FACT ABOUT THIS TREE and a
// fact that nothing else in the repo records falsifiably. When someone wires the
// trust path for real, this goes red — which is the only moment at which the
// acceptance report, this file and any coverage claim must be updated together.
// Deleting the case turns a recorded fact back into prose.
//
// It is ALSO not the claim "0 call sites is safe": an unreachable verifier
// rejects nothing, so the trusted-write path this engine implies does not exist
// here at all. That absence must never be read as "authentication is set up".
//
// LIMIT — quote this wherever the case is cited. This is a NAME scan. It cannot
// see `svc["verifyServiceSignature"](...)`, `const v = verifyServiceSignature;
// v(...)`, or a re-export consumed under another name. It catches the MOST
// LIKELY wiring, not ALL wiring. CLEARANCE CONDITION: the day this repo contains
// a name-indirect service-auth invocation, this case is void and must be
// replaced by an AST pass or a runtime probe. A green here must not be read as
// "no wiring exists" after that point.
//
// PRECISION REFINEMENT (2026-11-13): the naive `\bconsumeNonce\s*\(` pattern
// false-flagged two UNRELATED, same-named single-use-nonce methods that exist
// in this tree — `AuthorizationLedger.consumeNonce` (server/ai/phase-b/ledger.mjs)
// and `RealD1Ledger.consumeNonce` (server/ai/real-backends/real-d1.mjs). Both are
// method definitions (`consumeNonce({...}) {`) and method calls (`obj.consumeNonce`),
// never the bare standalone service-auth function. The pattern was tightened to
// match ONLY a bare, unqualified call that is not a method definition (see
// SERVICE_AUTH_CONSUME_NONCE). This is a precision fix, NOT a weakening: the
// invariant "the service-auth trust path has 0 call sites" is fully preserved,
// and the service-auth module remains provably unwired (0 bare calls outside its
// own file). The fixture above now asserts the two independent method shapes are
// NOT counted, so any future regression of the precision rule is itself a red.
const SERVICE_AUTH_STANDALONE = new RegExp(
  "\\b(verifyServiceSignature|verifyGithubOidc|canonicalServicePayload|signServiceBody)\\s*\\(",
  "g",
);
// `consumeNonce` is a GENERIC single-use-nonce name. Two UNRELATED implementations
// live in this tree that are NOT the service-auth trust path:
//   * AuthorizationLedger.consumeNonce  — server/ai/phase-b/ledger.mjs (only imports GATE_CODES)
//   * RealD1Ledger.consumeNonce         — server/ai/real-backends/real-d1.mjs (only imports errors.mjs)
// Both are method definitions (`consumeNonce({...}) {`) and method calls
// (`obj.consumeNonce(...)`), and MUST NOT be mistaken for the standalone
// service-auth `consumeNonce`. The service-auth `consumeNonce` (server/engine/_lib/
// service-auth.mjs) is a bare EXPORTED function used UNQUALIFIED, so a genuine
// trust-path call site is a BARE, unqualified call that is NOT a method definition.
//   * `(?<![.\w])`  — not preceded by `.` (method call) or an identifier char (prefix).
//   * `(?![^()]*\)\s*\{)` — not a method/property definition `name(...) {`.
// This is a PRECISION FIX (2026-11-13), not a weakening: the invariant "the
// service-auth trust path has 0 call sites" is fully preserved; we only stopped
// falsely accusing same-named independent implementations. The service-auth
// trust path itself remains provably UNWIRED (0 bare calls outside its own file).
const SERVICE_AUTH_CONSUME_NONCE = new RegExp(
  "(?<![.\\w])consumeNonce\\s*\\((?![^()]*\\)\\s*\\{)",
  "g",
);
const SERVICE_AUTH_DEFINING = join("server", "engine", "_lib", "service-auth.mjs");
const SERVICE_AUTH_SELF = fileURLToPath(import.meta.url);
const SCAN_SKIP_DIRS = new Set(["node_modules", ".git", "dist", "build", "coverage", ".gate", "artifacts", "reports"]);
const SCAN_CODE_EXT = /\.(mjs|cjs|js|mts|cts|ts|jsx|tsx)$/;

function bareCode(src) {
  return stripComments(src)
    .split("\n")
    .map((ln) => {
      const at = ln.indexOf("//");
      if (at === -1) return ln;
      if (at > 0 && ln[at - 1] === ":") return ln;   // do not cut "http://" / "https://"
      return ln.slice(0, at);
    })
    .join("\n");
}

/** Policy, as a pure function of a path. Asserted by the fixture below. */
function isScannedSourcePath(p) {
  if (p.split(/[\\/]+/).some((seg) => SCAN_SKIP_DIRS.has(seg))) return false;
  return SCAN_CODE_EXT.test(p);
}

/**
 * Pure: an in-memory file table -> the call sites in it. Deliberately NOT a
 * directory walk, so the detector can be exercised without creating, and then
 * deleting, a directory on the host — a control must not introduce permission,
 * concurrency or cleanup failure modes that have nothing to do with the fact it
 * is checking. `files` is [{ path, text }].
 */
function scanServiceAuthCallSites(files) {
  const hits = [];
  for (const f of files) {
    if (f.path === SERVICE_AUTH_SELF) continue;              // this case quotes the names
    if (f.path.endsWith(SERVICE_AUTH_DEFINING)) continue;    // the definitions themselves
    if (!isScannedSourcePath(f.path)) continue;
    for (const m of bareCode(f.text).matchAll(SERVICE_AUTH_STANDALONE)) hits.push({ file: f.path, symbol: m[1] });
    // consumeNonce is scanned with the precise (method-aware) pattern above.
    for (const m of bareCode(f.text).matchAll(SERVICE_AUTH_CONSUME_NONCE)) hits.push({ file: f.path, symbol: "consumeNonce" });
  }
  return hits;
}

/** Read the tree into the same in-memory shape. SKIP_DIRS is honoured here too,
 *  purely so the walk does not descend into node_modules; the authoritative
 *  policy lives in isScannedSourcePath, which the fixture pins. A walk that
 *  diverged from the policy could only make this case LESS sensitive. */
function readSourceTree(root) {
  const files = [];
  (function walk(dir) {
    let entries;
    try { entries = readdirSync(dir, { withFileTypes: true }); } catch (_) { return; }
    for (const e of entries) {
      const p = join(dir, e.name);
      if (e.isDirectory()) { if (!SCAN_SKIP_DIRS.has(e.name)) walk(p); continue; }
      if (!e.isFile() || !SCAN_CODE_EXT.test(e.name)) continue;
      let text;
      try { text = readFileSync(p, "utf8"); } catch (_) { continue; }
      files.push({ path: p, text });
    }
  })(root);
  return files;
}

check("EXTRA", "Y9 the service-auth trust path is UNREACHABLE here (0 call sites — a recorded fact)", () => {
  // The detector is exercised on a FIXTURE before it is trusted on the repo.
  // Otherwise "0 call sites" would be indistinguishable from "the scanner is
  // broken", and the comment-stripping branch would never have run once:
  // MEASURED on today's tree, bypassing that stripping still yields 0, because
  // no comment in the tree currently quotes a call. A fixture is what turns
  // "it works" into a falsifiable claim. It also pins the path policy (EXT +
  // SKIP_DIRS), which is otherwise an unexercised filter.
  const fixture = [
    { path: "a.mjs", text: "const x = verifyServiceSignature(req);" },
    { path: "b.md", text: "verifyServiceSignature(nope)" },                          // EXT filter
    { path: "c.mjs", text: "// verifyServiceSignature(commented)" },                 // comment strip
    { path: "node_modules/d.mjs", text: "verifyServiceSignature(nope)" },            // SKIP_DIRS
    { path: "e.mjs", text: "consumeNonceMaybe(); const y = consumeNonce;" },         // prefix, not a call
    // --- precision guards (2026-11-13): same-named independent methods MUST NOT count ---
    { path: "f.mjs", text: "ledger.consumeNonce({ nonce: 'x' });" },                 // method call -> excluded
    { path: "g.mjs", text: "async consumeNonce({ nonce }) { return true; }" },        // method def  -> excluded
  ];
  const fx = scanServiceAuthCallSites(fixture);
  assert.equal(fx.length, 1,
    `detector self-test: expected exactly 1 call site in the fixture, got ${fx.length} ` +
    `(${JSON.stringify(fx.map((h) => h.symbol + "@" + h.file))}) — the scanner is not doing what this case claims`);
  assert.equal(fx[0].symbol, "verifyServiceSignature");
  assert.ok(fx[0].file === "a.mjs", `detector self-test picked the wrong file: ${fx[0].file}`);

  const hits = scanServiceAuthCallSites(readSourceTree(REPO));
  assert.equal(hits.length, 0,
    "the frozen service-auth trust path now HAS call sites. This is the moment the\n" +
    "  acceptance report, the coverage claim and this case must be updated TOGETHER:\n" +
    hits.map((h) => `    ${h.symbol}() called from ${h.file}`).join("\n"));
  console.log("      REGISTERED, not guaranteed: verifyServiceSignature / verifyGithubOidc /");
  console.log("      consumeNonce / canonicalServicePayload / signServiceBody have 0 call sites.");
  console.log("      An unreachable verifier rejects nothing — this records an absence.");
});
// ---- Y6 (Phase A / P0): trigger coverage must be proven on CONCRETE refs -----
// Y1/Y2 assert the LITERAL presence of "feature/**" / "feature/v16-**" strings.
// A literal is not coverage: it stays green if the wildcard is misspelled, if a
// later negation removes the match, or if the branch family is renamed. Y6
// glob-matches the two refs that actually matter against BOTH workflows:
//   * push on the integration branch feature/jev-v2-ops-integration (F-22-C)
//   * pull_request BASE of PR #7, feature/v16-market-provenance-hardening (C14)
// It is a separate named check so the C12/F-22-G case floor counts it: deleting
// Y6 is then itself a hard red instead of a silent loss of the guard.
function triggerGlobMatches(list, ref) {
  assert.ok(Array.isArray(list) && list.length > 0,
    "trigger branch list for " + ref + " is empty or unparsed - fail closed");
  return list.some((g) => {
    const escaped = g.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
    return new RegExp("^" + escaped + "$").test(ref);
  });
}

check("EXTRA", "Y6 Phase A: both workflows glob-match the integration branch on push and PR #7 base on pull_request (C14 / F-22-C, not a literal string check)", () => {
  const ciL = yamlLines(REL.ci);
  const provL = yamlLines(REL.prov);
  const HEAD_REF = "feature/jev-v2-ops-integration";
  const PR7_BASE = "feature/v16-market-provenance-hardening";
  assert.ok(triggerGlobMatches(workflowTriggerBranches(ciL, "push"), HEAD_REF),
    "F-22-C: ci.yml push does not match " + HEAD_REF);
  assert.ok(triggerGlobMatches(workflowTriggerBranches(provL, "push"), HEAD_REF),
    "F-22-C: v16-market-provenance.yml push does not match " + HEAD_REF);
  assert.ok(triggerGlobMatches(workflowTriggerBranches(ciL, "pull_request"), PR7_BASE),
    "F-22-C: ci.yml pull_request base does not match " + PR7_BASE);
  assert.ok(triggerGlobMatches(workflowTriggerBranches(provL, "pull_request"), PR7_BASE),
    "C14: v16-market-provenance.yml pull_request base does not match " + PR7_BASE);
});
console.log("NOTE: Y1/Y2/Y3/Y4/Y5/Y6/Y9 are pure-text structure self-checks, NOT a YAML parser run");
console.log("      (pyyaml / ruby-yaml / yq / js-yaml all unavailable in this environment).");

// Naming (R19): these are NAMED CHECK BLOCKS (`check(...)` cases), not `assert.*`
// statements. A single case can contain many assertions, so "TOTAL"
// here must never be read as an assertion count. See the header note.
console.log(`RESULT: CORE_CASES=${core} EXTRA_CASES=${extra} TOTAL_CASES=${core + extra} exit=0`);
