// qa-verify / INDEPENDENT QA — six-page fidelity, red lines, and static
// integrity assertions for the Trading Cockpit upgrade.
//
// SCOPE DISCIPLINE (updated for the team-lead P1 ruling, task #7)
// ----------------
// The "no real legacy import / no mock identifier" rule applies to EVERY
// product file changed by this upgrade — which now ALSO includes four of the
// six pre-existing pages (rewritten to import the new local provenance helper)
// and the new src/trading/provenance.ts. The scan surface was EXPANDED, not
// narrowed. `deriveStatusLabel` is our OWN implementation and is therefore
// ALLOWED; what is forbidden is (1) importing it from the C-legacy
// src/views/trading tree, (2) the mock identifiers USE_MOCK / mockData /
// MARKET_DATA_SOURCE, and (3) losing any fail-closed branch in provenance.ts.
// The exact changed-file set is cross-checked against git (worktree + committed diff).
//
// Comments are stripped with a real state machine (strings/templates/regex are
// protected) BEFORE the legacy words are looked for. MarketPanel.tsx and
// TradingCockpit.tsx mention `deriveStatusLabel` ONLY inside comments, and
// vite.config.ts mentions `USE_MOCK` ONLY inside comments — the self-check
// proves the stripper removes them rather than the assertion being weakened.
//
// Node builtins are loaded through a non-literal dynamic import (no @types/node
// in tsconfig types, so no static resolution / no @ts-ignore / no .skip).

import { describe, it, expect } from "vitest";

const nodeFs = (await import(/* @vite-ignore */ ["node", "fs"].join(":"))) as {
  readFileSync: (path: string, encoding: string) => string;
  existsSync: (path: string) => boolean;
};
const nodeCp = (await import(/* @vite-ignore */ ["node", "child_process"].join(":"))) as {
  execSync: (cmd: string, opts: { encoding: string }) => string;
};

const read = (p: string): string => nodeFs.readFileSync(p, "utf8");
const git = (args: string): string => nodeCp.execSync(`git ${args}`, { encoding: "utf8" });

const SIX = ["AICommandCenter", "TradingWorkbench", "MarketUniverse3D", "StrategyArena", "PaperAccount", "AIExpertTeam"];
const SIX_IDS = ["command", "workbench", "universe", "strategy", "account", "team"];

// Four pages rewritten by the P1 provenance fix; the other two stay untouched.
const P1_FOUR = ["AICommandCenter", "TradingWorkbench", "MarketUniverse3D", "PaperAccount"];

// Exact set of product-code files added/modified by this upgrade, excluding
// QA *.test.ts files added by qa-verify. Cross-checked against the live git
// tree (uncommitted status + committed diff vs DELIVERY_BASE) in a test below.
// The last five entries are the P1 (task #7) provenance fix — adding them
// EXPANDS the L226 "ZERO legacy words" scan surface to 18 files.
const CHANGED_FILES = [
  "src/trading/App.tsx",
  "src/trading/components/Shell.tsx",
  "src/trading/pages/TradingCockpit.tsx",
  "src/trading/metrics/contracts.ts",
  "src/trading/components/cockpit/CostAttribution.tsx",
  "src/trading/components/cockpit/ExecutionLedger.tsx",
  "src/trading/components/cockpit/MarketPanel.tsx",
  "src/trading/components/cockpit/MetricStrip.tsx",
  "src/trading/components/cockpit/ProviderComparison.tsx",
  "src/trading/components/cockpit/index.ts",
  "src/trading/hooks/useHotkeys.ts",
  "src/trading/hooks/useResizablePanels.ts",
  "vite.config.ts",
  "src/trading/pages/AICommandCenter.tsx",
  "src/trading/pages/TradingWorkbench.tsx",
  "src/trading/pages/MarketUniverse3D.tsx",
  "src/trading/pages/PaperAccount.tsx",
  "src/trading/provenance.ts",
];

// Three-class semantics (team-lead P1 ruling — a TIGHTENING, not a relaxation):
//  (1) forbidden real import paths into the C-legacy views/trading tree;
//  (2) forbidden mock identifiers in new/changed code;
//  (3) `deriveStatusLabel` is ALLOWED (our own helper), but its import source
//      and the fail-closed export are asserted separately below. The old
//      `deriveStatusLabel` word produced FALSE POSITIVES on the legitimate
//      local implementation, so it is replaced by stronger, precise checks.
const FORBIDDEN_IMPORT_PATHS = ["src/views/trading", "../../views/trading/components/PageContainer"];
const FORBIDDEN_IDENTIFIERS = ["USE_MOCK", "mockData", "MARKET_DATA_SOURCE"];
const LEGACY_WORDS = [...FORBIDDEN_IMPORT_PATHS, ...FORBIDDEN_IDENTIFIERS];
const legacyHits = (s: string): string[] => LEGACY_WORDS.filter((w) => s.includes(w));

// Strip // and /* */ comments while preserving string / template / regex
// contents. A `//` inside a quoted string (e.g. an http:// URL) is NOT a
// comment and must survive. Newlines inside block comments are preserved so
// line-oriented assertions remain meaningful.
function stripComments(src: string): string {
  let out = "";
  let i = 0;
  const n = src.length;
  type State = "code" | "line" | "block" | "sq" | "dq" | "tmpl";
  let state: State = "code";
  while (i < n) {
    const c = src[i];
    const c2 = src[i + 1];
    if (state === "code") {
      if (c === "/" && c2 === "/") { state = "line"; i += 2; continue; }
      if (c === "/" && c2 === "*") { state = "block"; i += 2; continue; }
      if (c === "'") { state = "sq"; out += c; i++; continue; }
      if (c === '"') { state = "dq"; out += c; i++; continue; }
      if (c === "`") { state = "tmpl"; out += c; i++; continue; }
      out += c; i++; continue;
    }
    if (state === "line") {
      if (c === "\n") { state = "code"; out += c; }
      i++; continue;
    }
    if (state === "block") {
      if (c === "*" && c2 === "/") { state = "code"; i += 2; continue; }
      if (c === "\n") out += c;
      i++; continue;
    }
    const quote = state === "sq" ? "'" : state === "dq" ? '"' : "`";
    if (c === "\\") { out += c; if (i + 1 < n) out += src[i + 1]; i += 2; continue; }
    if (c === quote) { state = "code"; out += c; i++; continue; }
    out += c; i++; continue;
  }
  return out;
}

interface Changed { status: string; path: string }
// 本批升级起点（升级前 HEAD）。白名单校验必须同时在「未提交」与「已提交」
// 两种状态下成立：改动一旦 commit，git status 对这些路径就变空，只看 status
// 会产生假红（2026-11-15 实测 2 failed / 1 failed suite）。`--relative` 让
// committed 路径与 CHANGED_FILES 同前缀。这不是放宽：判定集合只会变大。
const DELIVERY_BASE = "23e482f";

function gitChangedPaths(): Changed[] {
  const SCOPE = "src/trading server/trading_api.mjs vite.config.ts";
  const worktree = git(`status --short -- ${SCOPE}`)
    .split(/\r?\n/).filter((l) => l.trim().length > 0).map((l) => ({
      status: l.slice(0, 2).trim(),
      path: l.slice(3).trim().replace(/\\/g, "/").replace(/\/$/, ""),
    }));

  let committed: Changed[] = [];
  try {
    committed = git(`diff --relative --name-only ${DELIVERY_BASE} HEAD -- ${SCOPE}`)
      .split(/\r?\n/).filter((l) => l.trim().length > 0).map((p) => ({
        status: "M",
        path: p.trim().replace(/\\/g, "/").replace(/\/$/, ""),
      }));
  } catch {
    // 基线对象不可达（浅克隆等）时不静默放行：缺文件会让下面的断言如实报红。
    committed = [];
  }

  const seen = new Set<string>();
  return [...worktree, ...committed].filter((c) => {
    if (seen.has(c.path)) return false;
    seen.add(c.path);
    return true;
  });
}

describe("QA harness sanity (so a green result is meaningful)", () => {
  it("runs from the CC3D project root; every evidence path resolves", () => {
    expect(nodeFs.existsSync("package.json")).toBe(true);
    expect(nodeFs.existsSync("src/trading/App.tsx")).toBe(true);
    expect(nodeFs.existsSync("scripts/day14-runner.mjs")).toBe(true);
    expect(nodeFs.existsSync("server/trading_api.mjs")).toBe(true);
    expect(nodeFs.existsSync("vite.config.ts")).toBe(true);
    for (const f of CHANGED_FILES) expect({ f, exists: nodeFs.existsSync(f) }).toEqual({ f, exists: true });
  });

  it("stripComments removes comments but preserves strings and real code", () => {
    const probe = [
      'import { x } from "ok";',
      "// USE_MOCK mockData deriveStatusLabel src/views/trading",
      "/* deriveStatusLabel USE_MOCK */",
      'const url = "http://example.com//USE_MOCK";',
      "const real = mockData;",
    ].join("\n");
    const stripped = stripComments(probe);
    expect(stripped).not.toContain("// USE_MOCK");
    expect(stripped).toContain("http://example.com//USE_MOCK"); // string survived
    expect(stripped).toContain("const real = mockData;");        // real code survived
    expect(legacyHits(stripped).length).toBeGreaterThan(0);      // real usage still detected
    expect(legacyHits(stripComments("// only a comment: mockData"))).toEqual([]);
  });

  it("the changed whitelist matches the real git working tree", () => {
    const paths = gitChangedPaths().map((c) => c.path);
    for (const f of CHANGED_FILES) {
      // git collapses an untracked directory into a single directory entry,
      // so a file may be reported via an ancestor directory.
      const covered = paths.some((p) => f === p || f.startsWith(p + "/"));
      expect({ file: f, covered }).toEqual({ file: f, covered: true });
    }
  });
});

describe("six-page fidelity", () => {
  it("all six page files exist, are non-stub, and really read /api/jev/*", () => {
    for (const name of SIX) {
      const p = `src/trading/pages/${name}.tsx`;
      expect(nodeFs.existsSync(p)).toBe(true);
      const s = read(p);
      const lines = s.split(/\r?\n/).length;
      expect({ name, lines_over_40: lines > 40 }).toEqual({ name, lines_over_40: true });
      expect(s).toMatch(new RegExp(`export\\s+function\\s+${name}\\b`));
      expect(s).toContain("useJevQuery"); // real adapter reads, not a stub
      expect(s).toContain("topbar");
    }
  });

  it("Shell.NAV still lists the six original ids with nums 1..6, plus cockpit as 7th", () => {
    const s = read("src/trading/components/Shell.tsx");
    const start = s.indexOf("export const NAV");
    const end = s.indexOf("export function Sidebar");
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    const nav = s.slice(start, end);
    const ids = [...nav.matchAll(/id:\s*"([a-z]+)"/g)].map((m) => m[1]);
    for (const id of SIX_IDS) expect(ids).toContain(id);
    expect(ids.filter((id) => SIX_IDS.includes(id))).toHaveLength(6);
    expect(ids).toContain("cockpit");
    for (let i = 0; i < 6; i++) {
      expect(nav).toMatch(new RegExp(`id:\\s*"${SIX_IDS[i]}",\\s*num:\\s*${i + 1}\\b`));
    }
    expect(nav).toMatch(/id:\s*"cockpit",\s*num:\s*7\b/);
  });

  it("App.tsx keeps all six hash routes reachable; readRoute still validates via NAV", () => {
    const s = read("src/trading/App.tsx");
    for (const id of SIX_IDS) expect(s).toContain(`page === "${id}"`);
    expect(s).toContain('page === "cockpit"');

    const m = s.match(/const ROUTE_RE\s*=\s*(\/[^\n]+\/);/);
    expect(m).not.toBeNull();
    const re = new RegExp((m as RegExpMatchArray)[1].slice(1, -1));
    for (const id of SIX_IDS) expect(re.test("#/" + id)).toBe(true);
    expect(re.test("#/cockpit")).toBe(true);
    expect(re.test("#/bogus9")).toBe(false);

    expect(s).toContain("NAV.some((n) => n.id === id)");   // membership guard
    expect(s).toContain('const id = m ? (m[1] as PageId) : "cockpit"'); // default = cockpit
    expect(s).toContain('addEventListener("hashchange"'); // refresh restore
    for (const name of SIX) expect(s).toContain(`import { ${name} }`);
    expect(s).toContain("import { TradingCockpit }");
  });

  // team-lead P1 ruling (task #7): the premise "six pages were NOT modified"
  // is now INVALID — four pages were legitimately rewritten to import the new
  // local provenance helper. This is NOT a relaxation: the old "untouched"
  // check plus the "TradingWorkbench keeps a legacy import" assumption are
  // replaced by a STRICTER set — (a) the two genuinely untouched pages are
  // still absent from git status, (b) ALL six pages must contain ZERO legacy
  // import paths / mock identifiers after comment stripping, (c) the four P1
  // pages must import deriveStatusLabel ONLY from ../provenance, (d)
  // provenance.ts must export it with every fail-closed branch intact, and
  // (e) the C-legacy system must remain byte-identical (fix did NOT touch it).
  it("two pages (StrategyArena/AIExpertTeam) remain untouched; four P1 pages are legitimately modified", () => {
    const paths = gitChangedPaths().map((c) => c.path);
    for (const name of ["StrategyArena", "AIExpertTeam"]) {
      const p = `src/trading/pages/${name}.tsx`;
      expect({ name, p, in_status: paths.includes(p) }).toEqual({ name, p, in_status: false });
    }
    const changed = new Set(CHANGED_FILES);
    for (const name of P1_FOUR) {
      const p = `src/trading/pages/${name}.tsx`;
      expect(changed.has(p)).toBe(true);       // whitelist explicitly covers them
      expect(paths).toContain(p);              // git really reports them changed
    }
  });

  it("ALL six pages contain ZERO legacy import paths / mock identifiers after comment stripping", () => {
    const offenders: { file: string; hits: string[] }[] = [];
    for (const name of SIX) {
      const p = `src/trading/pages/${name}.tsx`;
      const code = stripComments(read(p));
      const hits = LEGACY_WORDS.filter((w) => code.includes(w));
      if (hits.length) offenders.push({ file: p, hits });
    }
    expect(offenders).toEqual([]);
  });

  it("four P1 pages import deriveStatusLabel ONLY from ../provenance (never src/views/trading)", () => {
    for (const name of P1_FOUR) {
      const s = stripComments(read(`src/trading/pages/${name}.tsx`));
      expect(s).toContain('from "../provenance"');
      expect(s).toContain("deriveStatusLabel");
      expect({ name, legacy: s.includes("views/trading") }).toEqual({ name, legacy: false });
    }
  });

  it("provenance.ts exports deriveStatusLabel, has no mock identifiers, keeps ALL fail-closed branches", () => {
    const code = stripComments(read("src/trading/provenance.ts"));
    expect(code).toMatch(/export\s+function\s+deriveStatusLabel\b/);
    for (const w of LEGACY_WORDS) expect({ w, hit: code.includes(w) }).toEqual({ w, hit: false });
    // fail-closed rules must survive verbatim (no mock short-circuit re-added)
    expect(code).toContain('"NO_DATA"');
    expect(code).toContain('"BACKFILL"');
    expect(code).toMatch(/m\.label\s*===\s*"NO_DATA"\s*\|\|\s*m\.label\s*===\s*"UNAVAILABLE"\s*\|\|\s*m\.label\s*===\s*"BLOCKED"/);
    expect(code).toContain('"VERIFIED"');
    expect(code).toContain("provenanceComplete");
    expect(code).toContain("sourceNamed");
    expect(code).toMatch(/m\.label\s*===\s*"LIVE"\s*&&\s*provenanceComplete\s*&&\s*sourceNamed/);
  });

  it("C-legacy PageContainer.tsx and paperApi.ts stay byte-identical to HEAD (provenance fix did NOT touch C system)", () => {
    const C_LEGACY = ["src/views/trading/components/PageContainer.tsx", "src/views/trading/services/paperApi.ts"];
    const tracked = git(`ls-files -- ${C_LEGACY.join(" ")}`);
    for (const f of C_LEGACY) expect(tracked).toContain(f);
    const prefix = git("rev-parse --show-prefix").trim();
    for (const f of C_LEGACY) {
      const worktree = git(`hash-object -- ${f}`).trim();
      const head = git(`rev-parse HEAD:${prefix}${f}`).trim();
      expect({ f, worktree, head }).toEqual({ f, worktree: head, head });
      expect(worktree).toMatch(/^[0-9a-f]{40}$/);
    }
    expect(git(`status --short -- ${C_LEGACY.join(" ")}`).trim()).toBe("");
  });
});

describe("no REAL legacy imports in the NEW code (comments stripped)", () => {
  it("raw changed sources DO contain the words — but only in comments", () => {
    const mp = read("src/trading/components/cockpit/MarketPanel.tsx");
    expect(mp).toContain("deriveStatusLabel");
    expect(mp).toContain("USE_MOCK");
    expect(stripComments(mp)).not.toContain("deriveStatusLabel");
    expect(stripComments(mp)).not.toContain("USE_MOCK");

    const tc = read("src/trading/pages/TradingCockpit.tsx");
    expect(tc).toContain("deriveStatusLabel");
    expect(stripComments(tc)).not.toContain("deriveStatusLabel");

    const vc = read("vite.config.ts");
    expect(vc).toContain("USE_MOCK");
    expect(stripComments(vc)).not.toContain("USE_MOCK");
  });

  it("ZERO legacy words remain in any changed file after comment stripping", () => {
    // Scan surface EXPANDED to 18 files (incl. four P1 pages + provenance.ts).
    const offenders: { file: string; hits: string[] }[] = [];
    for (const f of CHANGED_FILES) {
      const hits = legacyHits(stripComments(read(f)));
      if (hits.length) offenders.push({ file: f, hits });
    }
    expect(offenders).toEqual([]);
  });
});

describe("frozen files byte-identical to HEAD", () => {
  const FROZEN = ["src/trading/api/client.ts", "server/trading_api.mjs"];

  it("git status --short and git diff --stat are EMPTY for both files", () => {
    const status = git(`status --short -- ${FROZEN.join(" ")}`);
    const diff = git(`diff --stat -- ${FROZEN.join(" ")}`);
    expect({ name: "git status --short", out: status.trim() })
      .toEqual({ name: "git status --short", out: "" });
    expect({ name: "git diff --stat", out: diff.trim() })
      .toEqual({ name: "git diff --stat", out: "" });
  });

  it("both files are tracked; worktree blob sha === HEAD blob sha", () => {
    const tracked = git(`ls-files -- ${FROZEN.join(" ")}`);
    for (const f of FROZEN) expect(tracked).toContain(f);
    const prefix = git("rev-parse --show-prefix").trim();
    for (const f of FROZEN) {
      const worktree = git(`hash-object -- ${f}`).trim();
      const head = git(`rev-parse HEAD:${prefix}${f}`).trim();
      expect({ f, worktree, head }).toEqual({ f, worktree, head: worktree });
      expect(worktree).toMatch(/^[0-9a-f]{40}$/);
    }
  });

  it("client.ts itself has no legacy mock import (unchanged adapter contract)", () => {
    const s = stripComments(read("src/trading/api/client.ts"));
    expect(legacyHits(s)).toEqual([]);
    expect(s).toContain('export const API_BASE = "/api/jev"');
  });
});

describe("compliance red lines", () => {
  it("scripts/day14-runner.mjs keeps enableRealTrading:false and allowBuySell:false", () => {
    const code = stripComments(read("scripts/day14-runner.mjs"));
    expect(code).toMatch(/enableRealTrading\s*:\s*false/);
    expect(code).toMatch(/allowBuySell\s*:\s*false/);
    expect(code).toContain("Object.freeze({ enableRealTrading: false, allowBuySell: false })");
  });

  it("Shell.tsx keeps the compliance copy and the WS-BLOCKED-never-LIVE rule", () => {
    const s = read("src/trading/components/Shell.tsx");
    expect(s).toContain("禁止真实资金");
    expect(s).toContain("禁止真实交易所下单");
    expect(s).toContain("禁止客户端定价");
    expect(s).toContain("禁止自动策略晋级");
    expect(s).toMatch(/WebSocket 通道/);
    expect(s).toContain("BLOCKED");
    expect(s).toMatch(/绝不会显示为\s*WebSocket\s*LIVE/);
  });

  it("TradingCockpit.tsx labels the WS channel as REST_POLLING, never WS LIVE", () => {
    const s = read("src/trading/pages/TradingCockpit.tsx");
    expect(s).toContain("REST_POLLING");
    expect(s).toContain("绝非 WS LIVE");
    expect(s).toContain("WebSocket");
  });
});


describe("professional terminal polish — regression contracts", () => {
  it("shows source provenance and a fail-closed real-order gate", () => {
    const s = read("src/trading/pages/TradingCockpit.tsx");
    expect(s).toContain('className="ck-terminal-ribbon"');
    expect(s).toContain("MARKET DATA");
    expect(s).toContain("PAPER ACCOUNT");
    expect(s).toContain("AI PROVIDERS");
    expect(s).toContain("SYSTEM HEALTH");
    expect(s).toContain("REAL-ORDER GATE");
    expect(s).toContain('h.real_money === false && h.real_exchange_orders === false');
    expect(s).toContain('h.real_money === true || h.real_exchange_orders === true');
    expect(s).toContain("安全字段不完整");
    expect(s).toContain("安全 Gate 未通过");
  });

  it("renders seven headline KPIs from the unified metrics contract", () => {
    const s = read("src/trading/components/cockpit/MetricStrip.tsx");
    for (const label of [
      "账户权益 · EQUITY",
      "可用现金 · CASH",
      "净收益 · NET P&L",
      "投资回报率 · ROI",
      "策略胜率 · WIN RATE",
      "订单成交率 · FILL RATE",
      "最大回撤 · MAX DD",
    ]) expect(s).toContain(label);
    for (const field of [
      "s.equity.display",
      "s.cash.display",
      "s.metrics.netPnl",
      "s.metrics.roi.display",
      "s.metrics.winRate.display",
      "s.metrics.fillRate.display",
      "s.metrics.maxDrawdown.display",
    ]) expect(s).toContain(field);
    expect(s).toContain('aria-label="核心绩效指标"');
    expect(s).toContain('data-domain={props.domain}');
  });

  it("keeps the terminal ribbon and KPI grid responsive at desktop, tablet and mobile widths", () => {
    const s = read("src/trading/styles.css");
    expect(s).toContain(".ck-terminal-ribbon");
    expect(s).toContain(".ck-status-grid");
    expect(s).toContain(".ck-kpi-strip");
    expect(s).toContain("@media (max-width: 1600px)");
    expect(s).toContain("@media (max-width: 1180px)");
    expect(s).toContain("@media (max-width: 640px)");
  });
});


describe("mobile navigation and min-content regression", () => {
  it("collapses the fixed sidebar into a horizontally scrollable top rail on phones", () => {
    const s = read("src/trading/styles.css");
    expect(s).toContain("@media (max-width: 640px)");
    expect(s).toMatch(/\.jev-app\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
    expect(s).toMatch(/\.jev-nav\s*\{[^}]*flex-direction:\s*row/s);
    expect(s).toMatch(/\.jev-nav\s*\{[^}]*overflow-x:\s*auto/s);
    expect(s).toMatch(/\.jev-side-foot\s*\{\s*display:\s*none/s);
  });

  it("removes min-content expansion from the mobile Cockpit workspace and bottom grid", () => {
    const s = read("src/trading/styles.css");
    expect(s).toMatch(/\.cockpit-workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
    expect(s).toMatch(/\.cockpit-bottom\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
    expect(s).toMatch(/\.cockpit-hsplit\s*\{\s*flex-direction:\s*column/s);
    expect(s).toContain("Mobile layout correction — 390px viewport");
  });
});
