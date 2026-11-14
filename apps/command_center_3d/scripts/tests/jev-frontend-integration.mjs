// JEV Trading Desk — Frontend Integration (联调) Test
//
// Target: the LOCAL facade the six active pages (src/trading/*) actually talk to
// via the Vite proxy -> http://127.0.0.1:4791/api/jev/*.
//
// Exercises the REAL API input contracts the frontend sends, against the real
// facade + frozen server-authoritative paper engine. Does NOT modify source.
// Uses the live runtime SQLite DB (creates throwaway sessions).
//
// Safe: no real trading, no real money, no external paid API. Market reads are
// public Binance REST; if offline they fail-closed to MARKET_UNAVAILABLE. Paper
// fills are simulated server-side.
//
// Run:  node scripts/tests/jev-frontend-integration.mjs   (facade must be up)
// Env:  JEV_API (default http://127.0.0.1:4791)
import { writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";

const BASE = process.env.JEV_API || "http://127.0.0.1:4791";
const COOKIE = "nr_paper_session";

let cookie = "";
function absorb(res) {
  const list = typeof res.headers.getSetCookie === "function" ? res.headers.getSetCookie() : [];
  for (const c of list) {
    const m = c.match(new RegExp(COOKIE + "=([^;]+)"));
    if (m) cookie = COOKIE + "=" + decodeURIComponent(m[1]);
  }
}
async function call(path, method, body, anon = false) {
  const headers = { "content-type": "application/json" };
  if (!anon && cookie) headers.Cookie = cookie;
  const res = await fetch(BASE + path, {
    method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  absorb(res);
  let j = null; try { j = await res.json(); } catch (_) {}
  return { status: res.status, j };
}

const results = [];
const gaps = [];
function check(name, pass, detail) {
  results.push({ name, pass: !!pass, detail });
  console.log(`[${pass ? "PASS" : "FAIL"}] ${name}${detail ? "  — " + detail : ""}`);
}
function gap(name, detail) {
  gaps.push({ name, detail });
  console.log(`[GAP ] ${name}  — ${detail}`);
}

const uid = () => Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
const ids = () => { const p = uid(); return { clientOrderId: "it-" + p, confirmationId: "c-" + p }; };

async function run() {
  // ---------- A. Status / read endpoints (GET, no auth, no session) ----------
  const readEndpoints = [
    ["health", "/api/jev/health", (d) => d.service === "JEV Trading Desk API"],
    ["system/status", "/api/jev/system/status", (d) => d.red_lines && d.red_lines.real_money === false],
    ["providers/status", "/api/jev/providers/status", (d) => d.verdict === "BLOCKED"],
    ["agents/status", "/api/jev/agents/status", (d) => d.verdict === "BLOCKED"],
    ["strategies/status", "/api/jev/strategies/status", (d) => d.verdict === "NO_DATA"],
    ["datasource/audit", "/api/jev/datasource/audit", () => true],
    ["market/quality", "/api/jev/market/quality", () => true],
    ["reference/design", "/api/jev/reference/design", () => true],
  ];
  for (const [name, path, assert] of readEndpoints) {
    const r = await call(path, "GET");
    const ok = r.status === 200 && r.j && r.j.ok === true &&
      r.j.schema === "jcc.jev.trading_api/v1" && assert(r.j.data || {});
    check("GET " + path, ok, `status=${r.status} ok=${r.j?.ok}`);
  }

  // ---------- B. Market data (GET, query params) ----------
  const marketCases = [
    ["market/ticker", "/api/jev/market/ticker?symbols=" + encodeURIComponent(JSON.stringify(["BTCUSDT", "ETHUSDT"]))],
    ["market/klines", "/api/jev/market/klines?symbol=BTCUSDT&interval=1h&limit=10"],
    ["market/depth", "/api/jev/market/depth?symbol=BTCUSDT&limit=20"],
    ["market/trades", "/api/jev/market/trades?symbol=BTCUSDT&limit=10"],
    ["market/exchangeInfo", "/api/jev/market/exchangeInfo?symbol=BTCUSDT"],
  ];
  for (const [name, path] of marketCases) {
    const r = await call(path, "GET");
    const honest = r.j && (r.j.ok === true || (r.j.ok === false && r.j.code === "MARKET_UNAVAILABLE"));
    check("GET " + path, honest, `status=${r.status} code=${r.j?.code} label=${r.j?.meta?.label}`);
  }
  // Local facade LENIENTLY defaults invalid interval/limit (cloud facade rejects).
  const badKlines = await call("/api/jev/market/klines?symbol=BTCUSDT&interval=INVALID", "GET");
  const badDepth = await call("/api/jev/market/depth?symbol=BTCUSDT&limit=9999", "GET");
  const lenient = badKlines.status < 500 && badDepth.status < 500;
  check("GET market invalid interval/limit handled (lenient default, no 5xx)", lenient,
    `klines=${badKlines.status} depth=${badDepth.status}`);
  gap("market param validation diverges", "local facade silently defaults invalid interval/limit (klines->1h, depth->20); Cloudflare facade rejects. 联调 target uses local lenient behavior.");

  // ---------- C. Paper trading (session cookie required) ----------
  const s = await call("/api/jev/paper/session", "POST", {});
  check("POST /api/jev/paper/session", s.status === 200 && !!cookie, `status=${s.status} cookie=${!!cookie}`);

  const acc = await call("/api/jev/paper/account", "GET");
  check("GET /api/jev/paper/account (authed)", acc.status === 200 && !!(acc.j?.data?.account),
    `status=${acc.status} hasAccount=${!!acc.j?.data?.account}`);

  const o = ids();
  const ord = await call("/api/jev/paper/orders", "POST",
    { ...o, symbol: "ETHUSDT", side: "BUY", type: "MARKET", quantity: 0.01 });
  const orderId = ord.j?.data?.order?.id;
  check("POST /api/jev/paper/orders (MARKET BUY)", ord.status === 201 && !!orderId,
    `status=${ord.status} orderId=${orderId} statusField=${ord.j?.data?.order?.status}`);
  // The order is reflected in the creation response's account projection.
  const inCreate = (ord.j?.data?.account?.orders || []).some((x) => x.id === orderId);
  check("created order reflected in account projection", inCreate, `statusField=${ord.j?.data?.order?.status}`);

  const ordList = await call("/api/jev/paper/orders", "GET");
  check("GET /api/jev/paper/orders (valid projection)", ordList.status === 200 &&
    Array.isArray(ordList.j?.data?.orders), `status=${ordList.status}`);

  // Cancel happy-path: open BUY LIMIT below market stays OPEN, then cancel -> CANCELED.
  const c = ids();
  const lim = await call("/api/jev/paper/orders", "POST",
    { ...c, symbol: "BTCUSDT", side: "BUY", type: "LIMIT", quantity: 0.001, limitPrice: 60000 });
  const limId = lim.j?.data?.order?.id;
  const limOpen = lim.j?.data?.order?.status === "OPEN";
  check("POST /api/jev/paper/orders (LIMIT BUY open)", lim.status === 201 && limOpen,
    `status=${lim.status} orderId=${limId} statusField=${lim.j?.data?.order?.status}`);
  if (limOpen) {
    const can = await call("/api/jev/paper/orders/" + limId + "/cancel", "POST", { clientActionId: "cancel-" + uid() });
    const cancelOk = can.status === 200 && can.j?.data?.status === "CANCELED";
    check("POST /api/jev/paper/orders/{id}/cancel -> CANCELED", cancelOk, `status=${can.status} result=${can.j?.data?.status}`);
  }

  const ev = await call("/api/jev/paper/events?after=0&limit=100", "GET");
  check("GET /api/jev/paper/events", ev.status === 200 && Array.isArray(ev.j?.data?.events),
    `status=${ev.status} count=${(ev.j?.data?.events || []).length}`);
  const rep = await call("/api/jev/paper/replay", "GET");
  check("GET /api/jev/paper/replay", rep.status === 200, `status=${rep.status}`);
  const eq = await call("/api/jev/paper/equity", "GET");
  check("GET /api/jev/paper/equity", eq.status === 200, `status=${eq.status}`);
  const aud = await call("/api/jev/paper/audit", "GET");
  check("GET /api/jev/paper/audit (session-scoped)", aud.status === 200 && aud.j?.data?.session_scoped === true,
    `status=${aud.status} session_scoped=${aud.j?.data?.session_scoped}`);

  // Negative paper
  const anonAcc = await call("/api/jev/paper/account", "GET", undefined, true);
  check("GET /api/jev/paper/account (anon) -> 401", anonAcc.status === 401, `status=${anonAcc.status}`);
  const anonAud = await call("/api/jev/paper/audit", "GET", undefined, true);
  check("GET /api/jev/paper/audit (anon) -> 401", anonAud.status === 401, `status=${anonAud.status}`);
  const badSym = await call("/api/jev/paper/orders", "POST",
    { ...ids(), symbol: "DOGEUSDT", side: "BUY", type: "MARKET", quantity: 1 });
  check("POST order invalid symbol -> INVALID_ORDER", badSym.status === 400 && badSym.j?.code === "INVALID_ORDER",
    `status=${badSym.status} code=${badSym.j?.code}`);
  const missField = await call("/api/jev/paper/orders", "POST",
    { symbol: "BTCUSDT", side: "BUY", type: "MARKET", quantity: 0.001 });
  check("POST order missing clientOrderId -> INVALID_ORDER", missField.status === 400 && missField.j?.code === "INVALID_ORDER",
    `status=${missField.status} code=${missField.j?.code}`);
  const marketWithLimit = await call("/api/jev/paper/orders", "POST",
    { ...ids(), symbol: "BTCUSDT", side: "BUY", type: "MARKET", quantity: 0.001, limitPrice: 100 });
  check("POST MARKET with limitPrice -> INVALID_ORDER", marketWithLimit.status === 400 && marketWithLimit.j?.code === "INVALID_ORDER",
    `status=${marketWithLimit.status} code=${marketWithLimit.j?.code}`);
  // Facade auto-synthesizes a valid clientActionId, so the only cancel-negative
  // reachable via the facade is an unknown order id -> ORDER_NOT_FOUND.
  const badCancel = await call("/api/jev/paper/orders/" + uid() + "/cancel", "POST", { clientActionId: "x" });
  check("POST cancel unknown order id -> ORDER_NOT_FOUND", badCancel.status === 404 && badCancel.j?.code === "ORDER_NOT_FOUND",
    `status=${badCancel.status} code=${badCancel.j?.code}`);
  const big = await call("/api/jev/paper/orders", "POST",
    { ...ids(), symbol: "BTCUSDT", side: "BUY", type: "MARKET", quantity: 0.001, pad: "x".repeat(70000) });
  check("POST order oversized body -> 413", big.status === 413, `status=${big.status}`);

  // ---------- D. Routes present in Cloudflare facade but ABSENT in local facade ----------
  for (const p of ["/api/jev/market/status", "/api/jev/market/diagnose", "/api/jev/market/probe"]) {
    const r = await call(p, "GET");
    gap("route absent in local facade: " + p, `status=${r.status} (404); implemented in Cloudflare facade only; no active frontend consumer`);
    check("route " + p + " (local-facade gap, documented)", r.status === 404, `status=${r.status}`);
  }
  const ai = await call("/api/jev/ai/providers", "GET");
  gap("AI gateway routes absent in local facade: /api/jev/ai/*", `status=${ai.status}; implemented in Cloudflare facade (gateway-routes.mjs); no active frontend consumer`);
  check("route /api/jev/ai/providers (local-facade gap, documented)", ai.status === 404, `status=${ai.status}`);
}

try { await run(); }
catch (err) { check("runtime_error", false, String((err && err.stack) || err)); }

const passed = results.filter((r) => r.pass).length;
const failed = results.length - passed;
const allPass = failed === 0;
const out = {
  generated_utc: new Date().toISOString(),
  layer: "frontend-integration-jev-facade",
  api_base: BASE,
  totals: { checks: results.length, passed, failed, all_passed: allPass },
  results, gaps,
};
try {
  const art = join(dirname(new URL(import.meta.url).pathname), "e2e", "artifacts", "jev_frontend_integration");
  mkdirSync(art, { recursive: true });
  writeFileSync(join(art, "result.json"), JSON.stringify(out, null, 2));
} catch (_) {}
console.log(`\n=== 联调 summary: ${passed}/${results.length} checks passed (all_passed=${allPass}); ${gaps.length} facade gaps recorded ===`);
process.exit(allPass ? 0 : 1);
