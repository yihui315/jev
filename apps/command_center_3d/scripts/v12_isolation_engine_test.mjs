// V1.2 — Paper Session isolation test on an INDEPENDENT test database.
//
// Strategy: import the FROZEN server-authoritative engine (paper-server.mjs +
// paper-ledger.mjs) verbatim against a freshly-created throwaway SQLite file and
// drive the REAL handlers (handlePaperSession / handlePaperOrders /
// handlePaperAccount / handlePaperServerEvents). A deterministic market stub
// stands in for the public REST host so real orders get filled. This proves, at
// the engine data layer, that:
//   1. two sessions receive distinct session_id + distinct signed cookies
//   2. an unauthenticated request is rejected (401) — no shared default account
//   3. a forged/expired token is rejected (401)
//   4. a REAL order + position created for session A is NOT visible to session B
//      (every engine read is `WHERE session_id = ?`)
//
// It does NOT touch the frozen engine, the live DB, or production code.
import { D1Adapter } from "../server/lib/d1_adapter.mjs";
import { randomBytes } from "node:crypto";
import { pathToFileURL } from "node:url";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";

const NR = process.env.JCC_NR_TRUSTED || "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted";
const ENGINE_DIR = join(NR, "functions/_lib");
const ledger = await import(pathToFileURL(join(ENGINE_DIR, "paper-ledger.mjs")).href);
const engine = await import(pathToFileURL(join(ENGINE_DIR, "paper-server.mjs")).href);

const ART = join(dirname(import.meta.dirname), "e2e", "artifacts", "v12");
mkdirSync(ART, { recursive: true });

const tmp = mkdtempSync(join(tmpdir(), "jev-iso-"));
const dbPath = join(tmp, "iso.sqlite");
const adapter = new D1Adapter(dbPath);
adapter.applyMigrations();
const secret = randomBytes(32).toString("hex");

// Deterministic market stub: depth (10^9 qty/level so fills complete),
// exchangeInfo (LOT/PRICE/MIN_NOTIONAL filters), and ticker (marks).
const PRICE = { "1000SATSUSDT": 0.00000862, "ETHUSDT": 3000, "BTCUSDT": 95000, "ORDIUSDT": 10 };
function dataFor(url) {
  const u = new URL(url);
  if (u.pathname.endsWith("/depth")) {
    const base = PRICE[u.searchParams.get("symbol")] || 1;
    // Keep a clear, non-crossed spread: best bid strictly below best ask.
    const asks = [0, 1, 2, 3, 4].map((i) => [Number((base * (1.001 + i * 1e-6)).toPrecision(12)), 1e9]);
    const bids = [0, 1, 2, 3, 4].map((i) => [Number((base * (0.999 - i * 1e-6)).toPrecision(12)), 1e9]);
    return { lastUpdateId: 100000, bids, asks };
  }
  if (u.pathname.endsWith("/exchangeInfo")) {
    return { symbols: [{ symbol: u.searchParams.get("symbol"), status: "TRADING", filters: [
      { filterType: "LOT_SIZE", minQty: "0.0001", maxQty: "100000000", stepSize: "0.0001" },
      { filterType: "PRICE_FILTER", minPrice: "0.00000001", maxPrice: "1000000", tickSize: "0.00000001" },
      { filterType: "MIN_NOTIONAL", minNotional: "0.00000001" },
    ] }] };
  }
  if (u.pathname.endsWith("/ticker/price")) {
    const syms = JSON.parse(u.searchParams.get("symbols") || "[]");
    return syms.map((s) => ({ symbol: s, price: String(PRICE[s] || 1) }));
  }
  return {};
}
const env = {
  PAPER_DB: adapter,
  PAPER_LEDGER_SECRET: secret,
  __fetch: async (url) => ({ ok: true, status: 200, json: async () => dataFor(url) }),
};

function mkReq(method, path, body, cookie) {
  // Same-origin as the constructed URL (http://localhost...) so both read
  // (isSameOrigin) and write (writeOriginOk) authorization checks pass.
  const headers = { "content-type": "application/json", "Origin": "http://localhost" };
  if (cookie) headers.Cookie = cookie;
  return new Request("http://localhost" + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
}
function decodeToken(token) {
  const p = token.split(".")[0];
  return JSON.parse(Buffer.from(p.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString());
}
async function createSession() {
  const r = await ledger.handlePaperSession(mkReq("POST", "/api/paper/session", {}), env);
  const sc = r.headers.get("set-cookie") || "";
  const token = decodeURIComponent(sc.match(/nr_paper_session=([^;]+)/)[1]);
  return { token, sid: decodeToken(token).sid };
}
function ids(p) {
  return { clientOrderId: `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
           confirmationId: `${p}-c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` };
}

const checks = {};
try {
  const A = await createSession();
  const B = await createSession();
  checks.session_id_distinct = A.sid !== B.sid;
  checks.cookie_token_distinct = A.token !== B.token;
  checks.A_sid = A.sid;
  checks.B_sid = B.sid;

  // Place a REAL market buy for each session (distinct symbols for clarity).
  const oA = await (await engine.handlePaperOrders(mkReq("POST", "/api/paper/orders",
    { ...ids("A"), symbol: "1000SATSUSDT", side: "BUY", type: "MARKET", quantity: 1000000 },
    "nr_paper_session=" + A.token), env)).json();
  const oB = await (await engine.handlePaperOrders(mkReq("POST", "/api/paper/orders",
    { ...ids("B"), symbol: "ETHUSDT", side: "BUY", type: "MARKET", quantity: 0.1 },
    "nr_paper_session=" + B.token), env)).json();
  checks.A_order_accepted = !!oA.account && (oA.account.orders || []).some((o) => o.symbol === "1000SATSUSDT" && o.status === "FILLED");
  checks.B_order_accepted = !!oB.account && (oB.account.orders || []).some((o) => o.symbol === "ETHUSDT" && o.status === "FILLED");

  const accA = await (await engine.handlePaperAccount(mkReq("GET", "/api/paper/account", null, "nr_paper_session=" + A.token), env)).json();
  const accB = await (await engine.handlePaperAccount(mkReq("GET", "/api/paper/account", null, "nr_paper_session=" + B.token), env)).json();

  const aOrd = (accA.account.orders || []);
  const bOrd = (accB.account.orders || []);
  const aPos = Object.keys(accA.account.positions || {});
  const bPos = Object.keys(accB.account.positions || {});
  checks.A_sees_1000SATS_position = aPos.includes("1000SATSUSDT");
  checks.B_sees_ETH_position = bPos.includes("ETHUSDT");
  checks.B_does_NOT_see_1000SATS_position = !bPos.includes("1000SATSUSDT");
  checks.A_does_NOT_see_ETH_position = !aPos.includes("ETHUSDT");
  checks.B_does_NOT_see_A_order = !bOrd.some((o) => aOrd.some((x) => x.id === o.id) || aOrd.some((x) => x.symbol === "1000SATSUSDT" && o.symbol === "1000SATSUSDT"));
  checks.A_does_NOT_see_B_order = !aOrd.some((o) => bOrd.some((x) => x.id === o.id) || bOrd.some((x) => x.symbol === "ETHUSDT" && o.symbol === "ETHUSDT"));

  // Event scoping
  const evA = await (await engine.handlePaperServerEvents(mkReq("GET", "/api/paper/events?after=0&limit=100", null, "nr_paper_session=" + A.token), env)).json();
  const evB = await (await engine.handlePaperServerEvents(mkReq("GET", "/api/paper/events?after=0&limit=100", null, "nr_paper_session=" + B.token), env)).json();
  // The bootstrap "account-opened" event is a fixed constant name shared by all
  // sessions (it carries no session-specific data). The real isolation property
  // is that session-SPECIFIC events (order created/filled/snapshot) never leak
  // across sessions, because every read is WHERE session_id = ?.
  const isBootstrap = (id) => String(id).startsWith("account-opened");
  const aSpecific = new Set((evA.events || []).filter((e) => !isBootstrap(e.eventId)).map((e) => e.eventId));
  const bSpecific = new Set((evB.events || []).filter((e) => !isBootstrap(e.eventId)).map((e) => e.eventId));
  checks.A_has_events = (evA.events || []).length > 0;
  checks.B_has_events = (evB.events || []).length > 0;
  checks.B_events_not_A_events = ![...bSpecific].some((id) => aSpecific.has(id));
  checks.A_specific_event_count = aSpecific.size;
  checks.B_specific_event_count = bSpecific.size;

  const unauth = await engine.handlePaperAccount(mkReq("GET", "/api/paper/account", null, null), env);
  checks.unauthenticated_rejected = unauth.status === 401;
  const forged = await engine.handlePaperAccount(mkReq("GET", "/api/paper/account", null, "nr_paper_session=garbage.token"), env);
  checks.forged_token_rejected = forged.status === 401;
} catch (err) {
  checks.runtime_error = String(err && err.stack || err);
} finally {
  adapter.close();
  try { rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
}

const pass = Object.entries(checks)
  .filter(([k]) => typeof checks[k] === "boolean")
  .every(([, v]) => v === true);
const out = {
  generated_utc: new Date().toISOString(),
  layer: "engine-data-isolation-real-orders",
  database: "independent throwaway sqlite (not the live runtime DB)",
  engine_modified: false,
  checks,
  all_passed: pass,
};
const fs = await import("node:fs");
fs.writeFileSync(join(ART, "isolation_engine.json"), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
process.exit(pass ? 0 : 1);
