/**
 * QA-T3-1 (TRANSPORT / HTTP_ERROR / VENUE_BUSINESS structured failure_class)
 * + QA-T3-5 (Global host injection -> MARKET_SOURCE_NOT_VERIFIED).
 * UNKNOWN rows MUST still write provenance (H8) with no fabricated price.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  openDb, makeEnv, makeState, insertSession, insertAccount,
  controlledFetcher, REAL_BASE_PRICES, runCycle, rows, fetchEntrypoint,
} from "../lib/harness.mjs";
import { validateSourceHost, evaluateCycle, ERROR_CODES } from "../../src/market-guard.mjs";
import { EXCHANGE_US } from "../../src/market-contract.mjs";
import { v16Recorder, REPO_ROOT } from "./lib.mjs";

const R = v16Recorder("v16_t3_failure_classes", { coverage: "QA-T3-1 + QA-T3-5" });
const T = 1770000000000;
const db = openDb();
const st = await makeState({
  cash: 100000, initialCash: 100000,
  positions: { BTCUSDT: { qty: 1, avgPrice: 70000 } }, generation: 1,
});
await insertSession(db, "s1", { nowMs: T });
await insertAccount(db, "s1", st.state_json, st.state_hash, { version: 1, nowMs: T });

const plan = [
  { key: "c_throw", mode: "throw" },
  { key: "c_503", mode: "http503" },
  { key: "c_missing", mode: "missing", price: REAL_BASE_PRICES.BTCUSDT },
];
for (let i = 0; i < plan.length; i++) {
  const p = plan[i];
  const nowMs = T + i * 60000;
  const f = controlledFetcher({
    nowMs, serverTime: nowMs, mode: p.mode,
    prices: { BTCUSDT: p.price || REAL_BASE_PRICES.BTCUSDT },
  });
  const env = makeEnv(db, f, {});
  await runCycle(env, { db, fetcher: f, cycleKey: p.key, nowMs });
}

const joined = await rows(db,
  "SELECT s.equity, s.marks_json, p.cycle_key, p.failure_class, p.data_quality, p.error_code, p.http_status FROM paper_equity_snapshots s JOIN v45_snapshot_provenance p ON p.snapshot_id = s.snapshot_id WHERE s.session_id = 's1' ORDER BY s.created_at");
const byKey = Object.fromEntries(joined.map((r) => [r.cycle_key, r]));

R.check("three_unknown_rows_written", joined.length === 3, { count: joined.length });
R.check("QA_T3_1_transport_failure_class_structured",
  byKey.c_throw.failure_class === "TRANSPORT", { got: byKey.c_throw.failure_class, errorCode: byKey.c_throw.error_code });
R.check("QA_T3_1_http_error_failure_class_structured",
  byKey.c_503.failure_class === "HTTP_ERROR", { got: byKey.c_503.failure_class, errorCode: byKey.c_503.error_code });
R.check("QA_T3_1_venue_business_failure_class_structured",
  byKey.c_missing.failure_class === "VENUE_BUSINESS", { got: byKey.c_missing.failure_class, errorCode: byKey.c_missing.error_code });
R.check("unknown_rows_still_write_provenance",
  joined.length === 3 && joined.every((r) => r.data_quality === "UNKNOWN"),
  joined.map((r) => ({ c: r.cycle_key, dq: r.data_quality })));
R.check("unknown_rows_no_fabricated_price",
  joined.every((r) => r.equity === 0 && r.marks_json === "{}"),
  joined.map((r) => ({ c: r.cycle_key, eq: r.equity, marks: r.marks_json })));
R.check("honest_market_layer_error_codes_preserved",
  byKey.c_throw.error_code && byKey.c_503.error_code.includes("503") && byKey.c_missing.error_code,
  { throw: byKey.c_throw.error_code, http503: byKey.c_503.error_code, missing: byKey.c_missing.error_code });

// QA-T3-5: a Global (non-allowlist) host injected into the market identity.
// The literal lives in THIS test file (src/ must never contain it - H4/T7).
const globalHost = "api.binance.com";
const hostCheck = validateSourceHost({
  sourceHost: globalHost, exchange: EXCHANGE_US,
  fallbackUsed: false, usedLastGoodPrice: false, autoFallback: false,
});
R.check("QA_T3_5_global_host_injection_rejected",
  hostCheck.pass === false && hostCheck.code === ERROR_CODES.MARKET_SOURCE_NOT_VERIFIED,
  { host: globalHost, code: hostCheck.code });

const cyc = evaluateCycle({
  exchange: EXCHANGE_US, market: "spot", sourceHost: globalHost,
  priceTsSource: "exchange_serverTime", networkContext: "local_test_shim",
  contracts: [], priorMarketKeys: [], freshnessVerdict: "FRESH",
  httpStatus: 200, payloadSha256: "p", historyProvenance: [],
});
const sourceHostCheck = cyc.checks.find((c) => c.name === "source_host");
R.check("QA_T3_5_cycle_source_host_check_fails_closed",
  cyc.ok === false && sourceHostCheck && sourceHostCheck.code === ERROR_CODES.MARKET_SOURCE_NOT_VERIFIED,
  { ok: cyc.ok, firstFail: cyc.errorCode, sourceHostCheck });

// QA2-D1 regression: an UNKNOWN / missing network context must fail closed.
// Before the guard rule 8 fix this produced data_quality='VERIFIED' with
// network_context='UNKNOWN' (fail-open). The rule must reject both the
// UNKNOWN sentinel and non-string input, and pass a resolved context.
const cycUnknownCtx = evaluateCycle({
  exchange: EXCHANGE_US, market: "spot", sourceHost: "api.binance.us",
  priceTsSource: "exchange_serverTime", networkContext: "UNKNOWN",
  contracts: [], priorMarketKeys: [], freshnessVerdict: "FRESH",
  httpStatus: 200, payloadSha256: "p", historyProvenance: [],
});
const ctxCheck = cycUnknownCtx.checks.find((c) => c.name === "network_context");
R.check("QA2_D1_unknown_network_context_rejected",
  cycUnknownCtx.ok === false && ctxCheck &&
  ctxCheck.code === ERROR_CODES.MARKET_CONTEXT_UNRESOLVED &&
  cycUnknownCtx.failureClass === "UNKNOWN",
  { ok: cycUnknownCtx.ok, firstFail: cycUnknownCtx.errorCode, ctxCheck });

const cycMissingCtx = evaluateCycle({
  exchange: EXCHANGE_US, market: "spot", sourceHost: "api.binance.us",
  priceTsSource: "exchange_serverTime", networkContext: undefined,
  contracts: [], priorMarketKeys: [], freshnessVerdict: "FRESH",
  httpStatus: 200, payloadSha256: "p", historyProvenance: [],
});
R.check("QA2_D1_missing_network_context_rejected",
  cycMissingCtx.ok === false && cycMissingCtx.errorCode === ERROR_CODES.MARKET_CONTEXT_UNRESOLVED,
  { firstFail: cycMissingCtx.errorCode });

const cycGoodCtx = evaluateCycle({
  exchange: EXCHANGE_US, market: "spot", sourceHost: "api.binance.us",
  priceTsSource: "exchange_serverTime", networkContext: "local_test_shim",
  contracts: [], priorMarketKeys: [], freshnessVerdict: "FRESH",
  httpStatus: 200, payloadSha256: "p", historyProvenance: [],
});
const ctxCheckOk = cycGoodCtx.checks.find((c) => c.name === "network_context");
R.check("QA2_D1_resolved_network_context_passes_rule8",
  ctxCheckOk && ctxCheckOk.pass === true,
  { ctxCheck: ctxCheckOk });

// ---------------------------------------------------------------------------
// TEST-ONLY controlled fault injection (cloud E2E negatives). These drive the
// REAL fetch() entrypoint with ?fault=... — not a direct runCycle shortcut —
// so the injected fetcher wiring itself is under test. Each fault gets its own
// fresh DB; the 403 gate, the no-fault path and static isolation are checked.
// ---------------------------------------------------------------------------
const TF = Date.now();
const FAULT_URL = (fault, cycleKey) =>
  "https://edge.invalid/__v45/collect?cycle_key=" + encodeURIComponent(cycleKey) +
  "&now_ms=" + TF + (fault ? "&fault=" + encodeURIComponent(fault) : "");

async function runFault(fault, cycleKey) {
  const fdb = openDb();
  await insertSession(fdb, "s1", { nowMs: TF });
  await insertAccount(fdb, "s1", st.state_json, st.state_hash, { version: 1, nowMs: TF });
  const baseFetcher = controlledFetcher({
    nowMs: TF, serverTime: TF, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT },
  });
  const fenv = makeEnv(fdb, baseFetcher, { testTrigger: "true" });
  const res = await fetchEntrypoint(new Request(FAULT_URL(fault, cycleKey)), fenv);
  const prov = await rows(fdb, "SELECT * FROM v45_snapshot_provenance WHERE cycle_key = ?", cycleKey);
  const snap = await rows(fdb,
    "SELECT s.* FROM paper_equity_snapshots s JOIN v45_snapshot_provenance p ON p.snapshot_id = s.snapshot_id WHERE p.cycle_key = ?",
    cycleKey);
  return { res, prov, snap };
}

const ft = await runFault("transport", "fault-transport");
R.check("FAULT_transport_injected_outage_is_unknown_transport_row",
  ft.res.status === 200 && ft.prov.length === 1 &&
  ft.prov[0].data_quality === "UNKNOWN" && ft.prov[0].failure_class === "TRANSPORT" &&
  ft.snap.length === 1 && ft.snap[0].equity === 0 && ft.snap[0].marks_json === "{}",
  { status: ft.res.status, prov: ft.prov.length, dq: ft.prov[0] && ft.prov[0].data_quality,
    fc: ft.prov[0] && ft.prov[0].failure_class, errorCode: ft.prov[0] && ft.prov[0].error_code });

const f5 = await runFault("http503", "fault-http503");
R.check("FAULT_http503_injected_is_unknown_http_error_row",
  f5.res.status === 200 && f5.prov.length === 1 &&
  f5.prov[0].data_quality === "UNKNOWN" && f5.prov[0].failure_class === "HTTP_ERROR" &&
  String(f5.prov[0].error_code).includes("503") &&
  f5.snap.length === 1 && f5.snap[0].equity === 0,
  { status: f5.res.status, fc: f5.prov[0] && f5.prov[0].failure_class,
    errorCode: f5.prov[0] && f5.prov[0].error_code });

const fg = await runFault("global_host", "fault-global-host");
R.check("FAULT_global_host_rejected_as_unverified_market",
  fg.res.status === 200 && fg.prov.length === 1 &&
  fg.prov[0].data_quality === "UNKNOWN" &&
  ["MARKET_SOURCE_NOT_VERIFIED", "MARKET_MIXING_REJECTED", "MARKET_IDENTITY_INVALID"]
    .some((c) => String(fg.prov[0].error_code).includes(c)) &&
  fg.snap.length === 1 && fg.snap[0].equity === 0 && fg.snap[0].marks_json === "{}",
  { status: fg.res.status, errorCode: fg.prov[0] && fg.prov[0].error_code,
    dq: fg.prov[0] && fg.prov[0].data_quality,
    markContracts: fg.prov[0] && fg.prov[0].mark_contracts_json });

// 403 gate MUST sit before any fault handling; trigger false -> unreachable.
const deniedEnv = makeEnv(db, controlledFetcher({ nowMs: TF, serverTime: TF, prices: { BTCUSDT: REAL_BASE_PRICES.BTCUSDT } }), { testTrigger: "false" });
const denied = await fetchEntrypoint(new Request(FAULT_URL("transport", "denied")), deniedEnv);
R.check("FAULT_trigger_false_returns_403_before_fault_handling",
  denied.status === 403,
  { status: denied.status, note: "V45_TEST_TRIGGER !== 'true' must short-circuit before fault is read" });

// No fault param -> identical to V1.5: real VERIFIED cycle.
const fn = await runFault(null, "fault-none");
R.check("FAULT_no_fault_path_matches_v15_verified_behavior",
  fn.res.status === 200 && fn.prov.length === 1 &&
  fn.prov[0].data_quality === "VERIFIED" && fn.prov[0].failure_class === "NONE" &&
  fn.snap.length === 1 && fn.snap[0].mark_quality === "verified",
  { status: fn.res.status, dq: fn.prov[0] && fn.prov[0].data_quality,
    fc: fn.prov[0] && fn.prov[0].failure_class });

// UNKNOWN rows may be {}, NEVER "[]".
R.check("FAULT_unknown_rows_mark_contracts_json_is_object_not_array",
  [ft, f5, fg].every((f) => {
    try {
      const o = JSON.parse(f.prov[0].mark_contracts_json);
      return o !== null && typeof o === "object" && !Array.isArray(o);
    } catch { return false; }
  }),
  [ft, f5, fg].map((f) => f.prov[0] && f.prov[0].mark_contracts_json));

// Static isolation: scheduled() never references fault; src keeps identity
// label only (no Global host literal); trigger guard is present.
const colSrc = readFileSync(resolve(REPO_ROOT, "apps/equity_collector_v45/src/collector.mjs"), "utf8");
const schedBody = colSrc.slice(colSrc.indexOf("async function scheduled"), colSrc.indexOf("function jsonResponse"));
R.check("FAULT_scheduled_never_reads_fault_static",
  !/fault/.test(schedBody) && colSrc.includes("V45_TEST_TRIGGER"),
  { scheduledHasFault: /fault/.test(schedBody), triggerGuardPresent: colSrc.includes("V45_TEST_TRIGGER") });
R.check("FAULT_src_uses_identity_label_no_forbidden_global_host",
  !colSrc.includes("data-api.binance.vision") && colSrc.includes("Binance Global spot"),
  { note: "src top-level uses the identity label, never a Global host literal (H4/T7)" });

R.finish({ rows: joined.length, faults: ["transport", "http503", "global_host"] });
