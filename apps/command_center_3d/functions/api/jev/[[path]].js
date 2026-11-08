// JEV Cloud Staging — unified auth gateway + Paper Trading API (Cloudflare Pages Function).
//
// WHAT THIS IS
//   The Cloudflare port of the Node facade (trading_api.mjs) + auth gateway
//   (preview-gateway.mjs), re-expressed for the Workers runtime. It does NOT
//   reimplement matching, accounting, sessions or market data. It imports the
//   FROZEN engine from server/engine/_lib in place and routes to its real
//   handlers. The engine is bound to the REAL D1 database via env.PAPER_DB.
//   NO engine logic is modified (engine_modified:false).
//
// HARD RULES (fail-closed)
//   * ENABLE_REAL_TRADING must be 'false' (else 500, no trading surface)
//   * JEV_PREVIEW_USER / JEV_PREVIEW_PASSWORD required (>=20 chars) — Basic auth
//   * PAPER_LEDGER_SECRET required (stable session signing key)
//   * market reads only to data-api.binance.vision (verified public REST)
//   * no fabricated AI inference, strategy returns, OOS samples or fills
//   * no second account / ledger / credential / market-data system
//
// RUNTIME COMPAT
//   * nodejs_compat provides process.env / crypto globals used by the engine.
//   * The engine uses the D1-shaped API (prepare/bind/first/all/run/batch); the
//     real D1 binding satisfies it natively (no node:sqlite shim on Cloudflare).

import { json, isSameOrigin, requireConfig, readSession, handlePaperSession } from "../../../server/engine/_lib/paper-ledger.mjs";
import * as engine from "../../../server/engine/_lib/paper-server.mjs";

const VERIFIED_HOST = "data-api.binance.vision";
const MARKET_BASE = `https://${VERIFIED_HOST}/api/v3`;
const ALLOWED_ENDPOINTS = new Set([
  "/api/v3/ticker/price", "/api/v3/ticker/24hr", "/api/v3/klines",
  "/api/v3/depth", "/api/v3/trades", "/api/v3/exchangeInfo",
]);
const ENGINE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const COLLECTOR_ONLY_SYMBOLS = ["BNBUSDT", "SOLUSDT"];

// ---- auth (constant-time-ish sha256 digest compare) -----------------------
function sha256Hex(str) {
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(str))
    .then((b) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join(""));
}
function timingSafeEqualString(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
async function safeAuth(header, user, password) {
  if (typeof header !== "string" || !header.startsWith("Basic ") || header.length > 2048) return false;
  let decoded;
  try { decoded = new TextDecoder().decode(Buffer.from(header.slice(6), "base64")); }
  catch { return false; }
  const ix = decoded.indexOf(":");
  if (ix <= 0) return false;
  const incoming = await sha256Hex(decoded);
  const expected = await sha256Hex(`${user}:${password}`);
  return timingSafeEqualString(incoming, expected);
}

// ---- market provenance -----------------------------------------------------
async function fetchVerified(rawUrl) {
  const u = new URL(String(rawUrl));
  if (u.protocol !== "https:" || u.hostname !== VERIFIED_HOST ||
      !ALLOWED_ENDPOINTS.has(u.pathname)) {
    throw new Error("MARKET_SOURCE_NOT_VERIFIED: " + u.toString());
  }
  const started = Date.now();
  const fetchedAt = new Date().toISOString();
  const res = await fetch(u.toString(), {
    signal: AbortSignal.timeout(9000),
    headers: { "user-agent": "JEV-Trading-Desk/1.0 (public REST)" },
  });
  const bytes = new Uint8Array(await res.arrayBuffer());
  const sha256 = await sha256Hex(new TextDecoder().decode(bytes));
  const latencyMs = Date.now() - started;
  if (!res.ok) throw new Error("MARKET_HTTP_" + res.status);
  let payload;
  try { payload = JSON.parse(new TextDecoder().decode(bytes)); }
  catch { throw new Error("MARKET_PAYLOAD_NOT_JSON"); }
  const meta = {
    source: u.toString(), source_host: u.hostname, endpoint: u.pathname,
    fetched_at_utc: fetchedAt, latency_ms: latencyMs, bytes: bytes.length,
    sha256, http_status: res.status,
    channel: "REST_POLLING", label: "LIVE", data_class: "REAL_MARKET_DATA",
    no_synthetic_data: true, no_api_key_required: true,
    websocket_note: "WebSocket is BLOCKED; this is REST polling, never WS LIVE",
  };
  return { payload, meta, bytes };
}
async function engineFetch(rawUrl) {
  const { payload } = await fetchVerified(String(rawUrl));
  return new Response(JSON.stringify(payload), {
    status: 200, headers: { "content-type": "application/json" },
  });
}

// ---- envelope + helpers ----------------------------------------------------
function utcNow() { return new Date().toISOString(); }
function envelope({ ok = true, data, error, code, meta = {}, status = 200, headers = {} }) {
  const body = {
    schema: "jcc.jev.trading_api/v1", ok,
    ...(data !== undefined ? { data } : {}),
    ...(error ? { error, code: code || "API_ERROR" } : {}),
    meta: { generated_utc: utcNow(), ...meta },
  };
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });
}
function metaFallback(url) {
  return { source: url, channel: "REST_POLLING", fetched_at_utc: utcNow(), no_synthetic_data: true };
}

// Honest, committed-at-build status. No external evidence files exist on
// Cloudflare; we ship the truthful UNVERIFIED/BLOCKED/NO_DATA verdict, never a
// fabricated pass.
function systemStatus() {
  return {
    verdict: "UNVERIFIED",
    gate: {
      independent_audit: { verdict: "UNVERIFIED", verified_checks: null, failed_checks: null },
      paper_gate_runs: 0, server_engine: "VERIFIED",
    },
    day: null,
    work_lines: [],
    supervisor: { ticks: null, last_heartbeat_utc: null, actions_total: null, blocked_observed: null },
    engine: {
      file: "server/engine/_lib/paper-server.mjs",
      sha256: null,
      allowed_symbols: ENGINE_SYMBOLS,
      collector_only_symbols: COLLECTOR_ONLY_SYMBOLS,
      note: "BNB/SOL have collector data but are NOT tradable in the frozen engine whitelist",
    },
    limits: {
      websocket: "BLOCKED", ai_six_roles: "BLOCKED",
      strategy_oos: "NO_DATA", thirty_day: "UNVERIFIED",
      market_marks: "UNAVAILABLE (Binance HTTP 403 from Cloudflare edge; orders rejected without a mark — no synthetic fills)",
      gate_fills: "0 server fills yet (no acceptance driver run on this deployment)",
    },
    red_lines: { real_money: false, real_exchange_orders: false, production_deploy: false, auto_strategy_promotion: false },
  };
}
function providersStatus() {
  return {
    verdict: "BLOCKED", code: "PROVIDER_NOT_CONFIGURED",
    credentials: 0, real_inference_records: 0,
    providers: ["MiniMax", "Qwen", "DeepSeek", "CustomOpenAI", "Jev-System-One"]
      .map((id) => ({ id, configured: false, status: "NOT_CONFIGURED", last4: null })),
    secret_store: { path: "Cloudflare Worker secret PAPER_LEDGER_SECRET only; no second credential system",
      note: "No key is ever sent to the browser or written to a log." },
    report_verdict_present: true,
    report_excerpt: "Verdict: BLOCKED — no provider configured on this staging.",
    work_line: { id: "W2", status: "BLOCKED", blocked: true },
    naming_discrepancy: {
      order_roles: ["SPOTTER", "ANALYST", "STRATEGIST", "RISK", "EXECUTOR", "CLOSER"],
      code_roles: ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"],
      exact_match: false, mapping: "UNVERIFIED - human decision required",
    },
  };
}
function agentsStatus() {
  const p = providersStatus();
  return {
    verdict: "BLOCKED", code: "PROVIDER_NOT_CONFIGURED",
    workflow: ["SPOTTER", "ANALYST", "STRATEGIST", "RISK", "EXECUTOR", "CLOSER"].map((role, i) => ({
      order: i + 1, role, code_role: p.naming_discrepancy.code_roles[i] ?? null,
      status: "BLOCKED", state: "NOT_CONFIGURED", provider: null, model: null,
      request_id: null, input_tokens: null, output_tokens: null, finish_reason: null,
      latency_ms: null, decision: null, risk_rejection: null,
      note: "UNKNOWN, never 0. No real inference has been executed.",
    })),
    inference_records: [], disagreements: [], risk_rejections: [], final_decisions: [],
    audit_log: [{ at_utc: utcNow(), event: "PROVIDER_NOT_CONFIGURED", verdict: "BLOCKED" }],
    naming_discrepancy: p.naming_discrepancy,
  };
}
function strategiesStatus() {
  return {
    verdict: "NO_DATA", code: "NO_REAL_OOS_SAMPLES",
    strategies: [], ranking: [], backtest_runs: 0, oos_samples: 0, monte_carlo_runs: 0,
    performance: { net_return: null, max_drawdown: null, win_rate: null, profit_factor: null, sharpe: null, total_trades: null },
    sample_foundation: { bars_total: null, bars_realtime: null, bars_backfill: null, gaps: null,
      note: "Real bar counts are sample foundation, NOT strategy performance." },
    rules: ["没有真实 OOS 样本不生成排名", "未通过 OOS 永不晋级", "训练集不得冒充 OOS", "不输出无依据的收益/胜率/回撤"],
    work_line: { id: "W4", status: "BLOCKED", blocked: true },
  };
}
function dataSourceAudit() {
  return [
    { id: "market_rest", label: "UNAVAILABLE", channel: "REST_POLLING", source: VERIFIED_HOST, status: "UNAVAILABLE", updated_utc: utcNow(), note: "Cloudflare 边缘出口被 Binance 拒绝（HTTP 403）；当前无法取得实时盘口/标记价。订单在无标记时被引擎诚实拒绝，绝不合成成交。" },
    { id: "market_websocket", label: "BLOCKED", channel: "WEBSOCKET", source: null, status: "BLOCKED", note: "/ws -> 404; stream.binance.com:9443 -> timeout" },
    { id: "market_backfill", label: "NO_DATA", channel: "LOCAL_LEDGER", source: "v45 hash-chained bars ledger", status: "NO_DATA", count: 0, note: "Not present on Cloudflare; never counted as live." },
    { id: "paper_engine", label: "LIVE", channel: "SERVER_ENGINE", source: "frozen paper-server.mjs (imported in place)", status: "VERIFIED" },
    { id: "provider_inference", label: "BLOCKED", channel: "PROVIDER_CENTER", source: "Worker secret store", status: "BLOCKED", count: 0 },
    { id: "strategy_oos", label: "NO_DATA", channel: "BACKTEST_ENGINE", source: "strategy-registry.mjs", status: "NO_DATA", count: 0 },
    { id: "supervisor", label: "UNVERIFIED", channel: "LOCAL_EVIDENCE", source: "runtime state", status: "UNVERIFIED" },
  ];
}

// ---- read-only authoritative DB projection (D1-native) ---------------------
async function paperAudit(preq, db, env) {
  const auth = await readSession(preq, env);
  // D1's prepare().bind().all()/.first() are async — must await, and guard
  // `.results` (D1 returns {results} but be defensive like the engine does).
  const rows = async (sql, ...args) => (await db.prepare(sql).bind(...args).all()).results || [];
  const count = async (sql, ...args) => Number((await db.prepare(sql).bind(...args).first())?.n || 0);
  const sid = auth?.session?.sid || null;
  const where = sid ? "WHERE session_id = ?" : "";
  const a = sid ? [sid] : [];
  return {
    session_scoped: !!sid, session_id: sid, session_valid: !!auth?.session,
    counts: {
      sessions: await count("SELECT COUNT(*) AS n FROM paper_sessions"),
      accounts: await count("SELECT COUNT(*) AS n FROM paper_accounts"),
      orders: await count("SELECT COUNT(*) AS n FROM paper_orders"),
      fills: await count("SELECT COUNT(*) AS n FROM paper_fills"),
      server_events: await count("SELECT COUNT(*) AS n FROM paper_server_events"),
    },
    orders: await rows(`SELECT * FROM paper_orders ${where} ORDER BY created_at DESC LIMIT 100`, ...a),
    fills: await rows(`SELECT * FROM paper_fills ${where} ORDER BY created_at DESC LIMIT 100`, ...a),
    events: await rows(`SELECT seq, event_id, command_id, event_type, created_at FROM paper_server_events ${where} ORDER BY seq DESC LIMIT 100`, ...a),
  };
}

// ---- router ----------------------------------------------------------------
function buildEngineRequest(request, pathname, search, bodyText) {
  const url = new URL(request.url);
  const origin = url.origin;
  const headers = { origin, "content-type": "application/json" };
  const cookie = request.headers.get("cookie");
  if (cookie) headers.cookie = cookie;
  return new Request(origin + pathname + (search || ""), {
    method: request.method,
    headers,
    body: request.method === "POST" || request.method === "PUT" ? (bodyText || "{}") : undefined,
  });
}

async function handleApi(request, env, url) {
  const pathname = url.pathname;
  const search = url.search;
  let bodyText = "";
  if (request.method === "POST" || request.method === "PUT") {
    bodyText = await request.text();
  }
  const db = env.PAPER_DB;

  try {
    if (pathname === "/api/jev/health") {
      return envelope({ data: {
        service: "JEV Trading Desk API", version: "1.0-cloudflare", origin: url.origin,
        engine_imported_from: "server/engine/_lib/paper-server.mjs", engine_modified: false,
        market_host: VERIFIED_HOST, market_channel: "REST_POLLING",
        engine_symbols: ENGINE_SYMBOLS, collector_only_symbols: COLLECTOR_ONLY_SYMBOLS,
        db: "D1:jev-paper-staging", real_money: false, real_exchange_orders: false,
      }, meta: { source: "cloudflare function", channel: "SERVER_ENGINE", label: "LIVE" } });
    }
    if (pathname === "/api/jev/system/status") return envelope({ data: systemStatus(), meta: { source: "committed honest status", channel: "SERVER_ENGINE", label: "UNVERIFIED" } });
    if (pathname === "/api/jev/providers/status") return envelope({ data: providersStatus(), meta: { label: "BLOCKED" } });
    if (pathname === "/api/jev/agents/status") return envelope({ data: agentsStatus(), meta: { label: "BLOCKED" } });
    if (pathname === "/api/jev/strategies/status") return envelope({ data: strategiesStatus(), meta: { label: "NO_DATA" } });
    if (pathname === "/api/jev/datasource/audit") return envelope({ data: dataSourceAudit(), meta: {} });
    if (pathname === "/api/jev/market/quality") return envelope({ data: { note: "NO_DATA on Cloudflare staging; real bar counts not migrated" }, meta: { label: "NO_DATA" } });
    if (pathname === "/api/jev/reference/design") return envelope({ data: { verdict: "UNVERIFIED", note: "reference pack not hosted on Cloudflare" }, meta: {} });

    if (pathname.startsWith("/api/jev/market/")) {
      const kind = pathname.slice("/api/jev/market/".length);
      const symbol = (url.searchParams.get("symbol") || "BTCUSDT").toUpperCase();
      let u;
      if (kind === "ticker") {
        const symbols = url.searchParams.get("symbols") || JSON.stringify(ENGINE_SYMBOLS);
        u = `${MARKET_BASE}/ticker/24hr?symbols=${encodeURIComponent(symbols)}`;
      } else if (kind === "klines") {
        const interval = /^[0-9]+[mhdw]$/.test(url.searchParams.get("interval") || "") ? url.searchParams.get("interval") : "1h";
        const limit = Math.min(1000, Math.max(10, Number(url.searchParams.get("limit") || 200)));
        u = `${MARKET_BASE}/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}`;
      } else if (kind === "depth") {
        const limit = [5, 10, 20, 50, 100, 500, 1000].includes(Number(url.searchParams.get("limit"))) ? Number(url.searchParams.get("limit")) : 20;
        u = `${MARKET_BASE}/depth?symbol=${encodeURIComponent(symbol)}&limit=${limit}`;
      } else if (kind === "trades") {
        const limit = Math.min(1000, Math.max(1, Number(url.searchParams.get("limit") || 50)));
        u = `${MARKET_BASE}/trades?symbol=${encodeURIComponent(symbol)}&limit=${limit}`;
      } else if (kind === "exchangeInfo") {
        u = `${MARKET_BASE}/exchangeInfo?symbol=${encodeURIComponent(symbol)}`;
      } else {
        return envelope({ ok: false, error: "UNKNOWN_MARKET_ENDPOINT", status: 404 });
      }
      try {
        const { payload, meta } = await fetchVerified(u);
        return envelope({ data: payload, meta });
      } catch (err) {
        return envelope({ ok: false, error: String(err.message || err), code: "MARKET_UNAVAILABLE", meta: { ...metaFallback(u), label: "UNAVAILABLE" }, status: 502 });
      }
    }

    if (pathname === "/api/jev/paper/audit") {
      const preq = buildEngineRequest(request, "/api/paper/account", "", bodyText);
      const auth = await readSession(preq, env);
      if (auth.error) return auth.error;
      return envelope({ data: await paperAudit(preq, db, env), meta: { source: "authoritative engine D1 (read-only projection, session-scoped)", channel: "SERVER_ENGINE", label: "LIVE", session_scoped: true } });
    }
    if (pathname === "/api/jev/paper/equity") {
      const preq = buildEngineRequest(request, "/api/paper/account", "", bodyText);
      const auth = await readSession(preq, env);
      const sid = auth?.session?.sid || null;
      const table = await db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_equity_snapshots'").first();
      const rows = sid && table
        ? ((await db.prepare("SELECT * FROM paper_equity_snapshots WHERE session_id = ? ORDER BY created_at ASC LIMIT 500").bind(sid).all()).results || [])
        : [];
      return envelope({ data: { snapshots: rows, count: rows.length, table_present: !!table, session_scoped: !!sid, equity_curve_synthesized: false }, meta: { source: "paper_equity_snapshots (server-written, read-only)", channel: "SERVER_ENGINE", label: rows.length ? "LIVE" : "NO_DATA" } });
    }
    if (pathname === "/api/jev/paper/orders" && request.method === "GET") {
      const preq = buildEngineRequest(request, "/api/paper/account", "", bodyText);
      const r = await engine.handlePaperAccount(preq, env);
      const body = await r.clone().json().catch(() => null);
      return envelope({
        ok: !!(body && body.ok), data: {
          orders: body?.account?.orders || [], trades: body?.account?.trades || [],
          version: body?.version ?? null, stateHash: body?.stateHash ?? null,
        }, error: body?.error, meta: { source: "server-authoritative account state (orders/trades projection)", channel: "SERVER_ENGINE", label: "LIVE", http_status: r.status }, status: r.status,
      });
    }

    const routes = {
      "/api/jev/paper/session": (r) => handlePaperSession(r, env),
      "/api/jev/paper/account": (r) => engine.handlePaperAccount(r, env),
      "/api/jev/paper/orders": (r) => engine.handlePaperOrders(r, env),
      "/api/jev/paper/events": (r) => engine.handlePaperServerEvents(r, env),
      "/api/jev/paper/replay": (r) => engine.handlePaperReplay(r, env),
      "/api/jev/paper/market/tick": (r) => engine.handlePaperMarketTick(r, env),
    };
    if (routes[pathname]) {
      const preq = buildEngineRequest(request, pathname.replace("/api/jev", "/api"), search, bodyText);
      const r = await routes[pathname](preq);
      return engineResponse(r, { route: pathname });
    }

    // /api/jev/paper/orders/{id}/cancel
    const m = pathname.match(/^\/api\/jev\/paper\/orders\/([^/]+)\/cancel$/);
    if (m) {
      const id = decodeURIComponent(m[1]);
      let incoming = {};
      try { incoming = bodyText ? JSON.parse(bodyText) : {}; } catch { incoming = {}; }
      const caValid = typeof incoming.clientActionId === "string" && /^[a-zA-Z0-9._:-]{8,100}$/.test(incoming.clientActionId);
      const clientActionId = caValid ? incoming.clientActionId : "cancel-" + utcNow() + "-" + crypto.randomUUID().slice(0, 8);
      const preq = buildEngineRequest(request, "/api/paper/orders/" + id + "/cancel", "", JSON.stringify({ clientActionId }));
      const r = await engine.handlePaperOrderCancel(preq, env, id);
      return engineResponse(r, { route: "order_cancel" });
    }

    return envelope({ ok: false, error: "NOT_FOUND", code: "UNKNOWN_ROUTE", status: 404 });
  } catch (err) {
    return envelope({ ok: false, error: String(err.message || err), code: "ADAPTER_EXCEPTION", status: 500 });
  }
}

async function engineResponse(r, extraMeta = {}) {
  let body = null;
  try { body = await r.clone().json(); } catch { /* non-json */ }
  const resp = envelope({
    ok: r.ok && body?.ok !== false, data: body,
    error: body?.ok === false ? (body.error || body.code || "ENGINE_REJECTED") : undefined,
    code: body?.code || (r.ok ? undefined : "ENGINE_HTTP_" + r.status),
    meta: { source: "server-authoritative paper engine (frozen, imported in place)", channel: "SERVER_ENGINE", label: "LIVE", engine_modified: false, http_status: r.status, ...extraMeta },
    status: r.status,
  });
  const cookies = typeof r.headers.getSetCookie === "function" ? r.headers.getSetCookie() : [];
  for (const c of cookies) resp.headers.append("set-cookie", c);
  return resp;
}

// ---- entry ----------------------------------------------------------------
export async function onRequest(context) {
  const { request, env } = context;
  // Fail-closed red lines.
  if (env.ENABLE_REAL_TRADING !== "false") return envelope({ ok: false, error: "ENABLE_REAL_TRADING_MUST_BE_FALSE", code: "RED_LINE", status: 500 });
  if (!env.JEV_PREVIEW_USER || !env.JEV_PREVIEW_PASSWORD || env.JEV_PREVIEW_PASSWORD.length < 20)
    return envelope({ ok: false, error: "PREVIEW_CREDENTIALS_REQUIRED", code: "RED_LINE", status: 500 });
  if (!env.PAPER_LEDGER_SECRET) return envelope({ ok: false, error: "LEDGER_SECRET_REQUIRED", code: "RED_LINE", status: 503 });

  // Defensive: surface the ledger secret to process.env for any engine code that
  // reads it there (nodejs_compat maps Worker env onto process.env).
  if (!globalThis.process) globalThis.process = {};
  globalThis.process.env = globalThis.process.env || {};
  globalThis.process.env.PAPER_LEDGER_SECRET = env.PAPER_LEDGER_SECRET;
  globalThis.process.env.ENABLE_REAL_TRADING = "false";

  // Basic auth gate.
  const ok = await safeAuth(request.headers.get("authorization"), env.JEV_PREVIEW_USER, env.JEV_PREVIEW_PASSWORD);
  if (!ok) return new Response(JSON.stringify({ ok: false, error: "PREVIEW_AUTH_REQUIRED" }), {
    status: 401, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", "www-authenticate": 'Basic realm="JEV Staging"' },
  });

  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/jev")) return handleApi(request, env, url);

  // Non-API paths are not served by this Function; Pages serves static assets.
  return new Response("Not Found", { status: 404 });
}
