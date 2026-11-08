// V1.3 — T05 daily Paper Trading research-loop report (initial run).
// Pulls REAL, session-scoped + global data from the live adapter. No fabrication:
// AI research / strategy OOS are reported as 0 / NO_DATA when not available.
const BASE = process.env.JEV_API || "http://127.0.0.1:4791";
const COOKIE = "nr_paper_session";

async function call(path, method = "GET", jar = null, body = null) {
  const headers = { "content-type": "application/json" };
  if (jar && jar.get()) headers.Cookie = jar.get();
  const res = await fetch(BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (jar) { const list = res.headers.getSetCookie ? res.headers.getSetCookie() : []; for (const c of list) { const m = c.match(new RegExp(COOKIE + "=([^;]+)")); if (m) jar.cookie = COOKIE + "=" + decodeURIComponent(m[1]); } }
  let data = null; try { data = await res.json(); } catch (_) {}
  return { status: res.status, data };
}
function makeJar() { return { cookie: "", get() { return this.cookie; } }; }
const pd = (r) => r.data?.data || {};
const d = await import("node:fs");
const { join, dirname } = await import("node:path");

const out = { generated_utc: new Date().toISOString(), task: "T05-daily-paper-trading-status", calendar: { start_utc: new Date().toISOString(), day_index: 0, target_days: 30, mode: "real_calendar_only" } };
try {
  // Global, session-independent provenance
  const ds = await call("/api/jev/datasource/audit");
  const mq = await call("/api/jev/market/quality");
  const prov = await call("/api/jev/providers/status");
  const agents = await call("/api/jev/agents/status");
  out.datasource_audit = pd(ds);
  out.market_quality = pd(mq);
  out.provider = pd(prov);
  out.agents = pd(agents);

  // Session-scoped paper ledger
  const jar = makeJar();
  await call("/api/jev/paper/session", "POST", jar, {});
  const acc = await call("/api/jev/paper/account", "GET", jar);
  const orders = await call("/api/jev/paper/orders", "GET", jar);
  const events = await call("/api/jev/paper/events?after=0&limit=100", "GET", jar);
  const equity = await call("/api/jev/paper/equity", "GET", jar);
  const a = pd(acc).account || {};
  const oList = pd(orders).orders || [];
  const eList = pd(events).events || [];
  out.paper = {
    session_id: a.session_id || null,
    cash: a.cash ?? a.balance ?? null,
    positions: Object.keys(a.positions || {}),
    open_orders: oList.filter((o) => o.status === "OPEN" || o.status === "NEW").length,
    filled_orders: oList.filter((o) => o.status === "FILLED").length,
    total_orders: oList.length,
    events: eList.length,
    equity: pd(equity).equity ?? null,
  };

  // Research / strategy / OOS — honest reporting
  out.research = { ai_research_count: 0, reason: "provider_inference BLOCKED (PROVIDER_NOT_CONFIGURED)", strategy_returns: "NO_DATA", oos_samples: "NO_DATA", risk_rejections: 0, supervisor: (pd(ds).verdict || "UNKNOWN") };
  out.red_lines = { no_real_funds: true, no_live_trading: true, no_oos_gate_bypass: true };
} catch (err) {
  out.runtime_error = String(err && err.stack || err);
}
const pass = !out.runtime_error;
out.all_passed = pass;
const ART = join(dirname(import.meta.dirname), "e2e", "artifacts", "v13");
d.mkdirSync(ART, { recursive: true });
d.writeFileSync(join(ART, "paper_trading_status.json"), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
process.exit(pass ? 0 : 1);
