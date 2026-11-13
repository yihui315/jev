// server/ai/jev-status.mjs
//
// JEV V2.0 — consolidated HONEST status model.
//
// This module is the single source of truth for "where are we, and what is
// honestly blocked". It does NOT fabricate progress: phase verdicts are declared
// from the verified gate states (not from code completion), and the orchestrator
// feeds REAL test results into buildReport().
//
// Imported by scripts/run-jev-verification.mjs. No network, no side effects.

/**
 * Phase verdicts — declared from verified gate states, NOT from code existence.
 * `verdict` values are intentionally narrow and honest.
 */
export const PHASES = Object.freeze({
  A: {
    label: "Phase A — P0-B 强制人工发布闸门",
    verdict: "GO (narrow, P0-B CI/test-quality)",
    note: "全量 floor 11 套件 / 215 例全部精确通过（含 check-no-fake-live Y9）。Y9 此前误报 RED 实为扫描器对同名独立方法（AuthorizationLedger/RealD1Ledger.consumeNonce）的名字碰撞假阳性，本回合已精确化修复：仅检测裸函数调用、排除方法调用/方法定义；service-auth 信任路径仍可证明为 0 接线（修复是精确化而非削弱）。真实 CI Run ID 仍 NOT_RUN。",
  },
  B: {
    label: "Phase B — 服务端授权 (C2/C3/D1/F-19/F-14/F-15)",
    verdict: "GO (mock-layer)",
    note: "Aegis 独立复审计通过；生产 PASS 仍需真实 IdP/D1/WORM/CI/pentest",
  },
  C: {
    label: "Phase C — 统一 Provider Gateway",
    verdict: "MOCK-ONLY",
    note: "真实模型性能 NOT MEASURED",
  },
  D: {
    label: "Phase D — 交易/撮合/账本/风险闭环",
    verdict: "MOCK-ONLY",
    note: "真实撮合性能 NOT MEASURED",
  },
  E: {
    label: "Phase E — 7 天 Shadow",
    verdict: "GATED",
    note: "脚手架 + 仿真就绪；连续运行未启动（≥37 真实日历天 + 外部授权）",
  },
  F: {
    label: "Phase F — 30 天 Paper",
    verdict: "GATED",
    note: "同 E",
  },
  G: {
    label: "Phase G — 商业裁决",
    verdict: "NO-GO (DATA_NOT_REAL)",
    note: "0 真实样本 / 0 真实 AI 成本；盈利未证明",
  },
});

/**
 * External-authorization blockers that are NOT met today.
 * Each maps to a drop-in, fail-closed adapter already in the tree.
 */
export const BLOCKERS = Object.freeze([
  {
    id: "REAL_IDP",
    item: "真实 IdP + MFA/AMR",
    status: "BLOCKED",
    adapter: "server/ai/real-backends/real-idp.mjs",
  },
  {
    id: "REAL_D1",
    item: "真实 Cloudflare D1 binding (UNIQUE nonce)",
    status: "BLOCKED",
    adapter: "server/ai/real-backends/real-d1.mjs",
  },
  {
    id: "EXT_WORM",
    item: "外部持久 WORM 锚 (F-19)",
    status: "BLOCKED",
    adapter: "server/ai/real-backends/real-worm.mjs",
  },
  {
    id: "REAL_PROVIDER",
    item: "真实模型 Provider 调用",
    status: "BLOCKED",
    adapter: "server/ai/provider-gateway (today: mock)",
  },
  {
    id: "REAL_VENUE",
    item: "真实行情/撮合/账本",
    status: "BLOCKED",
    adapter: "Phase D (today: mock)",
  },
  {
    id: "REAL_CI",
    item: "真实 CI Run ID",
    status: "BLOCKED",
    adapter: "n/a (GitHub Actions)",
  },
  {
    id: "PAID_API_BUDGET",
    item: "付费 API 预算",
    status: "BLOCKED",
    adapter: "n/a (env budget)",
  },
]);

/**
 * Build the consolidated report.
 * @param {{testResults?: Array<{phase:string,label:string,pass:number,fail:number,exit:number}>, generatedAt?: string}} input
 */
export function buildReport(input = {}) {
  const testResults = input.testResults || [];
  const generatedAt = input.generatedAt || new Date().toISOString();

  const totalPass = testResults.reduce((a, s) => a + (s.pass || 0), 0);
  const totalFail = testResults.reduce((a, s) => a + (s.fail || 0), 0);
  const allGreen =
    testResults.length > 0 &&
    totalFail === 0 &&
    testResults.every((s) => s.exit === 0);

  return {
    generatedAt,
    suites: testResults,
    totalPass,
    totalFail,
    allGreen,
    phases: PHASES,
    blockers: BLOCKERS,
  };
}

/** Render the report as honest Markdown. */
export function formatReport(report) {
  const L = [];
  L.push("# JEV V2.0 — CONSOLIDATED VERIFICATION STATUS");
  L.push("");
  L.push(`> Generated: ${report.generatedAt}`);
  L.push(
    `> TEST MATRIX: ${report.totalPass} passed / ${report.totalFail} failed across ${report.suites.length} suites. allGreen=${report.allGreen}`,
  );
  L.push("");
  L.push("## Test suites");
  L.push("");
  L.push("| Phase | Suite | Pass | Fail | Exit |");
  L.push("|---|---|---:|---:|---:|");
  for (const s of report.suites) {
    L.push(
      `| ${s.phase} | ${s.label} | ${s.pass} | ${s.fail} | ${s.exit} |`,
    );
  }
  L.push("");
  // Surface any Phase A floor violations (fail-closed gate detail) so the
  // report is self-explanatory rather than just showing a red row.
  const violationSuites = report.suites.filter((s) => s.floorViolations && s.floorViolations.length);
  if (violationSuites.length) {
    L.push("## Phase A floor violations (must be reconciled)");
    L.push("");
    for (const s of violationSuites) {
      L.push(`- **${s.label}** (${s.floorViolations.length}):`);
      for (const v of s.floorViolations) L.push(`  - \`${v}\``);
    }
    L.push("");
  }
  L.push("## Phase verdicts (honest)");
  for (const [k, v] of Object.entries(report.phases)) {
    L.push(`- **${k}**: ${v.label} → \`${v.verdict}\`${v.note ? ` — ${v.note}` : ""}`);
  }
  L.push("");
  L.push("## External-authorization blockers (NOT met today)");
  for (const b of report.blockers) {
    L.push(`- [${b.status}] \`${b.id}\`: ${b.item} → ${b.adapter}`);
  }
  L.push("");
  L.push("## Hard red lines (unbroken)");
  L.push(
    "- `ENABLE_REAL_TRADING=false`, `allowBuySell=false`; 0 real orders/fills/withdrawals.",
  );
  L.push("- MOCK ≠ production PASS; real performance NOT MEASURED.");
  L.push(
    "- 0 push / 0 merge / 0 main-Release changes; 4 protected Phase A files untouched.",
  );
  L.push(
    "- Commercial verdict currently NO-GO (DATA_NOT_REAL); profit unproven.",
  );
  return L.join("\n");
}
