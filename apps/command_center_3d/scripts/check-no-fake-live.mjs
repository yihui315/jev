/**
 * check-no-fake-live.mjs — P7 static assertions (Node stdlib only).
 *
 * Why this exists: `apps/command_center_3d/node_modules` is MISSING on this
 * host, so `tsc --noEmit` / `vitest run` cannot be executed and TypeScript
 * type-checking is UNVERIFIED. This script is the honest substitute: pure
 * Node stdlib, static source assertions, no network, no dependencies.
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
check("EXTRA", "Y1 v16-market-provenance.yml: original branch + 2 new branches; PR rule unchanged", () => {
  const l = yamlLines(REL.prov);
  assert.ok(l.some((x) => /feature\/v16-market-provenance-hardening/.test(x)), "original branch entry removed");
  assert.ok(l.some((x) => /feature\/v16-p0-gap-closure/.test(x)), "new branch missing");
  assert.ok(l.some((x) => /feature\/v16-\*\*/.test(x)), "wildcard branch missing");
  assert.ok(l.some((x) => /release\/jcc-v1\.3-final/.test(x)), "PR target rule changed");
});
check("EXTRA", "Y2 ci.yml: 5 gate scripts each test -f hard-fail; tsc/vitest kept; no continue-on-error", () => {
  const l = yamlLines(REL.ci);
  const scripts = [
    "day14-p0-contracts.mjs", "day14-p0-runner.mjs", "market-data.test.mjs",
    "inference-evidence.test.mjs", "p3-contracts.mjs",
  ];
  for (const s of scripts) {
    assert.ok(l.some((x) => x.includes(s) && /test -f/.test(x)), `${s}: missing test -f hard-fail`);
    assert.ok(l.some((x) => x.includes(s) && /node /.test(x)), `${s}: missing node invocation`);
  }
  assert.ok(l.some((x) => /npx tsc --noEmit/.test(x)), "tsc step removed");
  assert.ok(l.some((x) => /npx vitest run/.test(x)), "vitest step removed");
  // Anchor on the `- name:` line (NOT the preceding comment block, which
  // literally contains the words "No continue-on-error: ..."). The gate step
  // must not carry a real `continue-on-error:` YAML key; comment lines are
  // documentation, not configuration, so they are excluded from this check.
  // The real-data-probe job's `continue-on-error: true` is after `end` and is
  // intentionally preserved (task A: existing steps untouched).
  const start = l.findIndex((x) => /-\s*name:\s*P0\/P1\/P2\/P3P4 gap-closure gate/.test(x));
  const end = l.findIndex((x, i) => i > start && /^  real-data-probe:/.test(x));
  assert.ok(start !== -1 && end !== -1, "gate step block not found");
  assert.ok(
    !l.slice(start, end).some((x) => !x.trim().startsWith("#") && /^\s*continue-on-error\s*:/.test(x)),
    "gate step must NOT have continue-on-error"
  );
});
console.log("NOTE: Y1/Y2 are pure-text structure self-checks, NOT a YAML parser run");
console.log("      (pyyaml / ruby-yaml / yq / js-yaml all unavailable in this environment).");

console.log(`RESULT: CORE_ASSERTIONS=${core} EXTRA_ASSERTIONS=${extra} TOTAL=${core + extra} exit=0`);
