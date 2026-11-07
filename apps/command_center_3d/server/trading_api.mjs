// JEV Trading Desk V1.0 - unified backend data adapter (local HTTP facade).
//
// WHAT THIS IS
//   A thin, localhost-only HTTP facade over the EXISTING server-authoritative
//   Paper Trading engine. It does NOT reimplement matching, accounting, sessions
//   or market data. It imports the frozen engine in place and routes to its
//   real handlers. Every response carries provenance metadata so the frontend
//   can label LIVE / BACKFILL / BLOCKED / NO DATA honestly.
//
// HARD RULES
//   * binds 127.0.0.1 only
//   * no Provider API keys, no real exchange orders, no real money
//   * market reads go to the single verified public REST host (REST polling,
//     NOT WebSocket - WS is BLOCKED and is never labelled LIVE)
//   * no fabricated AI inference, strategy returns, OOS samples or fills
//   * no second account / ledger / credential / market-data system
//
// Exit codes: 0 normal, 2 startup failure.
import { createServer } from "node:http";
import { createHash, randomBytes } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync, appendFileSync, readdirSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

const ROOT = resolve(import.meta.dirname);
const RUNTIME = join(ROOT, "runtime");
const PORT = Number(process.env.JCC_DESK_API_PORT || 4791);
const HOST = "127.0.0.1";
const ORIGIN = `http://${HOST}:${PORT}`;
const ENGINE_DIR = resolve(process.env.JCC_ENGINE_DIR ||
  "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted/functions/_lib");
const V46 = resolve(process.env.JCC_V46_ROOT || "D:/jev/artifacts/v46");
const V45 = resolve(process.env.JCC_V45_ROOT || "D:/jev/artifacts/v45");
const NR_TRUSTED = resolve(process.env.JCC_NR_TRUSTED ||
  "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted");
process.env.JCC_NR_TRUSTED = NR_TRUSTED;

const VERIFIED_HOST = "data-api.binance.vision";
const MARKET_BASE = `https://${VERIFIED_HOST}/api/v3`;
const ALLOWED_ENDPOINTS = new Set([
  "/api/v3/ticker/price", "/api/v3/ticker/24hr", "/api/v3/klines",
  "/api/v3/depth", "/api/v3/trades", "/api/v3/exchangeInfo",
]);
const ENGINE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const COLLECTOR_ONLY_SYMBOLS = ["BNBUSDT", "SOLUSDT"];

mkdirSync(RUNTIME, { recursive: true });

const engineUrl = pathToFileURL(join(ENGINE_DIR, "paper-server.mjs")).href;
const ledgerUrl = pathToFileURL(join(ENGINE_DIR, "paper-ledger.mjs")).href;
const engine = await import(engineUrl);
const ledger = await import(ledgerUrl);
const { D1Adapter } = await import(pathToFileURL(join(ROOT, "lib/d1_adapter.mjs")).href);

// ---- authoritative DB (SAME schema, SAME engine, no second ledger) ---------
const dbPath = join(RUNTIME, "trading_desk.sqlite");
const adapter = new D1Adapter(dbPath);
adapter.applyMigrations();

// Local in-process session signing key. This is a RUNTIME PARAMETER, not a
// Provider credential, and is never sent to the browser or logged.
const secretPath = join(RUNTIME, ".session_secret");
let sessionSecret;
if (existsSync(secretPath)) {
  sessionSecret = readFileSync(secretPath, "utf8").trim();
} else {
  sessionSecret = randomBytes(32).toString("hex");
  writeFileSync(secretPath, sessionSecret, { encoding: "utf8", mode: 0o600 });
}

// ---- market provenance -----------------------------------------------------
const PROVENANCE_LOG = join(RUNTIME, "market_provenance.jsonl");
if (!existsSync(PROVENANCE_LOG)) writeFileSync(PROVENANCE_LOG, "", "utf8");

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
  const bytes = Buffer.from(await res.arrayBuffer());
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const latencyMs = Date.now() - started;
  if (!res.ok) throw new Error("MARKET_HTTP_" + res.status);
  let payload;
  try { payload = JSON.parse(bytes.toString("utf8")); }
  catch (_) { throw new Error("MARKET_PAYLOAD_NOT_JSON"); }
  const meta = {
    source: u.toString(), source_host: u.hostname, endpoint: u.pathname,
    fetched_at_utc: fetchedAt, latency_ms: latencyMs, bytes: bytes.length,
    sha256, http_status: res.status,
    channel: "REST_POLLING", label: "LIVE", data_class: "REAL_MARKET_DATA",
    no_synthetic_data: true, no_api_key_required: true,
    websocket_note: "WebSocket is BLOCKED; this is REST polling, never WS LIVE",
  };
  try { appendFileSync(PROVENANCE_LOG, JSON.stringify({ ...meta, at_utc: fetchedAt }) + "\n", "utf8"); }
  catch (_) { /* provenance logging must never break a read */ }
  return { payload, meta, bytes };
}

// The engine reads its own book through env.__fetch. Same verified host.
async function engineFetch(rawUrl, init) {
  const { bytes } = await fetchVerified(String(rawUrl));
  return new Response(bytes, { status: 200, headers: { "content-type": "application/json" } });
}

const env = {
  PAPER_DB: adapter,
  PAPER_LEDGER_SECRET: sessionSecret,
  __fetch: engineFetch,
};

// ---- helpers ---------------------------------------------------------------
function utcNow() { return new Date().toISOString(); }

function envelope({ ok = true, data, error, code, meta = {}, status = 200, headers = {} }) {
  const body = {
    schema: "jcc.jev.trading_api/v1",
    ok,
    ...(data !== undefined ? { data } : {}),
    ...(error ? { error, code: code || "API_ERROR" } : {}),
    meta: { generated_utc: utcNow(), ...meta },
  };
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });
}

function evidence(rel, fallback = null) {
  const p = join(V46, rel);
  try { return JSON.parse(readFileSync(p, "utf8")); }
  catch (_) { return fallback; }
}
function evidence45(rel, fallback = null) {
  try { return JSON.parse(readFileSync(join(V45, rel), "utf8")); }
  catch (_) { return fallback; }
}

const SOURCE_LOCAL = {
  source: "V4.6 runtime evidence (local JSON)", channel: "LOCAL_EVIDENCE",
  label: "VERIFIED", no_synthetic_data: true,
};

function readBody(req) {
  return new Promise((resolve) => {
    let s = "";
    req.on("data", (c) => { s += c; });
    req.on("end", () => resolve(s));
  });
}

function buildEngineRequest(req, pathname, search, bodyText) {
  const headers = { origin: ORIGIN, "content-type": "application/json" };
  if (req.headers.cookie) headers.cookie = req.headers.cookie;
  return new Request(ORIGIN + pathname + search, {
    method: req.method,
    headers,
    body: req.method === "POST" || req.method === "PUT" ? (bodyText || "{}") : undefined,
  });
}

async function engineResponse(r, extraMeta = {}) {
  let body = null;
  try { body = await r.clone().json(); } catch (_) {}
  const resp = envelope({
    ok: r.ok && body?.ok !== false,
    data: body,
    error: body?.ok === false ? (body.error || body.code || "ENGINE_REJECTED") : undefined,
    code: body?.code || (r.ok ? undefined : "ENGINE_HTTP_" + r.status),
    meta: {
      source: "server-authoritative paper engine (frozen upstream, imported in place)",
      channel: "SERVER_ENGINE", label: "LIVE", engine_modified: false,
      http_status: r.status, ...extraMeta,
    },
    status: r.status,
  });
  const cookies = typeof r.headers.getSetCookie === "function"
    ? r.headers.getSetCookie() : (r.headers.get("set-cookie") ? [r.headers.get("set-cookie")] : []);
  for (const c of cookies) resp.headers.append("set-cookie", c);
  return resp;
}

// ---- read-only authoritative DB projection for account / audit tables ------
async function paperAudit(preq) {
  const auth = await ledger.readSession(preq, env);
  const rows = (sql, ...args) => adapter.db.prepare(sql).all(...args).map((r) => ({ ...r }));
  const count = (sql) => Number(adapter.db.prepare(sql).get()?.n || 0);
  const sid = auth?.session?.sid || null;
  const where = sid ? "WHERE session_id = ?" : "";
  const a = sid ? [sid] : [];
  return {
    session_scoped: !!sid,
    session_id: sid,
    session_valid: !!auth?.session,
    counts: {
      sessions: count("SELECT COUNT(*) AS n FROM paper_sessions"),
      accounts: count("SELECT COUNT(*) AS n FROM paper_accounts"),
      orders: count("SELECT COUNT(*) AS n FROM paper_orders"),
      fills: count("SELECT COUNT(*) AS n FROM paper_fills"),
      server_events: count("SELECT COUNT(*) AS n FROM paper_server_events"),
    },
    orders: rows(`SELECT * FROM paper_orders ${where} ORDER BY created_at DESC LIMIT 100`, ...a),
    fills: rows(`SELECT * FROM paper_fills ${where} ORDER BY created_at DESC LIMIT 100`, ...a),
    events: rows("SELECT seq, event_id, command_id, event_type, created_at FROM paper_server_events ORDER BY seq DESC LIMIT 100"),
  };
}

// ---- status endpoints built from REAL evidence -----------------------------
function systemStatus() {
  const audit = evidence("runtime/v46_audit_result.json", {});
  const daily = evidence("runtime/v46_daily_status.json", {});
  const tasks = evidence("v46_tasks.json", {});
  const state = evidence("runtime/v46_supervisor_state.json", {});
  const baseline = evidence("runtime/upstream_baseline.json", {});
  const gateRuns = (() => {
    try { return readdirSync(join(V46, "runtime/gate_runs"), { withFileTypes: true })
      .filter((e) => e.isDirectory()).map((e) => e.name); }
    catch (_) { return []; }
  })();
  return {
    verdict: audit.verdict || "UNVERIFIED",
    gate: {
      independent_audit: { verdict: audit.verdict, verified_checks: audit.verified_checks,
                           failed_checks: audit.failed_checks },
      paper_gate_runs: gateRuns.length,
      server_engine: "VERIFIED",
    },
    day: daily.thirty_day_clock || null,
    work_lines: tasks.work_lines || [],
    supervisor: {
      ticks: state.ticks, last_heartbeat_utc: state.last_heartbeat_utc,
      actions_total: state.actions_total, blocked_observed: state.blocked_observed,
    },
    engine: {
      file: "functions/_lib/paper-server.mjs",
      sha256: baseline.files?.paper_server_mjs?.sha256 || null,
      allowed_symbols: ENGINE_SYMBOLS,
      collector_only_symbols: COLLECTOR_ONLY_SYMBOLS,
      note: "BNB/SOL have collector data but are NOT tradable in the frozen engine whitelist",
    },
    limits: {
      websocket: "BLOCKED",
      ai_six_roles: "BLOCKED",
      strategy_oos: "NO_DATA",
      thirty_day: daily.thirty_day_clock ? `Day ${daily.thirty_day_clock.day}/30` : "UNVERIFIED",
      gate_fills: "14 server fills came from the deterministic acceptance driver, NOT six-role AI decisions",
    },
    red_lines: { real_money: false, real_exchange_orders: false,
                 production_deploy: false, auto_strategy_promotion: false },
  };
}

function providersStatus() {
  const tasks = evidence("v46_tasks.json", {});
  const w2 = (tasks.work_lines || []).find((w) => w.id === "W2") || {};
  let reportText = "";
  try { reportText = readFileSync(join(V45, "PROVIDER_E2E_REPORT.md"), "utf8"); } catch (_) {}
  return {
    verdict: "BLOCKED", code: "PROVIDER_NOT_CONFIGURED",
    credentials: 0, real_inference_records: 0,
    providers: ["MiniMax", "Qwen", "DeepSeek", "CustomOpenAI", "Jev-System-One"]
      .map((id) => ({ id, configured: false, status: "NOT_CONFIGURED", last4: null })),
    secret_store: {
      path: "packages/jcc_core/secret_store.py (existing DPAPI SecretStore)",
      note: "No second credential system. No key is ever sent to the browser or written to a log.",
    },
    report_verdict_present: /Verdict: BLOCKED/.test(reportText),
    report_excerpt: reportText.split("\n").slice(0, 8).join("\n"),
    work_line: { id: w2.id, status: w2.status, blocked: w2.blocked },
    naming_discrepancy: {
      order_roles: ["SPOTTER", "ANALYST", "STRATEGIST", "RISK", "EXECUTOR", "CLOSER"],
      code_roles: ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"],
      exact_match: false, mapping: "UNVERIFIED - human decision required",
    },
  };
}

const SIX_ROLES = ["SPOTTER", "ANALYST", "STRATEGIST", "RISK", "EXECUTOR", "CLOSER"];
function agentsStatus() {
  const p = providersStatus();
  return {
    verdict: "BLOCKED", code: "PROVIDER_NOT_CONFIGURED",
    workflow: SIX_ROLES.map((role, i) => ({
      order: i + 1, role,
      code_role: p.naming_discrepancy.code_roles[i] ?? null,
      status: "BLOCKED", state: "NOT_CONFIGURED",
      provider: null, model: null, request_id: null,
      input_tokens: null, output_tokens: null, finish_reason: null,
      latency_ms: null, decision: null, risk_rejection: null,
      note: "UNKNOWN, never 0. No real inference has been executed.",
    })),
    inference_records: [], disagreements: [], risk_rejections: [], final_decisions: [],
    audit_log: [
      { at_utc: "2026-11-07T05:44:58Z", event: "PROVIDER_NOT_CONFIGURED",
        evidence: "D:/jev/artifacts/v45/PROVIDER_E2E_REPORT.md", verdict: "BLOCKED" },
    ],
    naming_discrepancy: p.naming_discrepancy,
  };
}

function strategiesStatus() {
  const q = evidence("runtime/v46_market_quality_latest.json", {});
  const tasks = evidence("v46_tasks.json", {});
  const w4 = (tasks.work_lines || []).find((w) => w.id === "W4") || {};
  return {
    verdict: "NO_DATA", code: "NO_REAL_OOS_SAMPLES",
    strategies: [], ranking: [], backtest_runs: 0, oos_samples: 0,
    monte_carlo_runs: 0,
    performance: { net_return: null, max_drawdown: null, win_rate: null,
                   profit_factor: null, sharpe: null, total_trades: null },
    sample_foundation: {
      bars_total: q.bars?.total ?? null, bars_realtime: q.bars?.realtime ?? null,
      bars_backfill: q.bars?.backfill ?? null,
      gaps: Array.isArray(q.gaps) ? q.gaps.length : null,
      note: "Real bar counts are sample foundation, NOT strategy performance. "
          + "BACKFILL bars are excluded from live increments.",
    },
    rules: ["没有真实 OOS 样本不生成排名", "未通过 OOS 永不晋级",
            "训练集不得冒充 OOS", "不输出无依据的收益/胜率/回撤"],
    work_line: { id: w4.id, status: w4.status, blocked: w4.blocked },
  };
}

function dataSourceAudit() {
  const q = evidence("runtime/v46_market_quality_latest.json", {});
  return [
    { id: "market_rest", label: "LIVE", channel: "REST_POLLING",
      source: VERIFIED_HOST, status: "VERIFIED", updated_utc: q.generated_utc || null,
      note: "Real public REST. WebSocket is NOT used." },
    { id: "market_websocket", label: "BLOCKED", channel: "WEBSOCKET", source: null,
      status: "BLOCKED", note: "/ws -> 404; stream.binance.com:9443 -> timeout" },
    { id: "market_backfill", label: "BACKFILL", channel: "LOCAL_LEDGER",
      source: "V4.5 hash-chained bars ledger", status: "VERIFIED",
      count: q.bars?.backfill ?? 0, note: "Never counted as live increments" },
    { id: "paper_engine", label: "LIVE", channel: "SERVER_ENGINE",
      source: "frozen paper-server.mjs (imported in place)", status: "VERIFIED" },
    { id: "provider_inference", label: "BLOCKED", channel: "PROVIDER_CENTER",
      source: "existing DPAPI SecretStore", status: "BLOCKED", count: 0 },
    { id: "strategy_oos", label: "NO_DATA", channel: "BACKTEST_ENGINE",
      source: "backtest_engine.py / strategy-registry.mjs", status: "NO_DATA", count: 0 },
    { id: "supervisor", label: "VERIFIED", channel: "LOCAL_EVIDENCE",
      source: "runtime/v46_supervisor_state.json", status: "VERIFIED" },
  ];
}

function referenceDesign() {
  let report = null;
  const p = "C:/Users/Administrator/Downloads/JEV_Trading_Desk_Reference_Pack/assets/download_report.json";
  try { report = JSON.parse(readFileSync(p, "utf8")); } catch (_) {}
  return {
    verdict: report && report.length === 6 && report.every((r) => r.status === "DOWNLOADED")
      ? "VERIFIED" : "UNVERIFIED",
    images: (report || []).map((r) => ({ id: r.id, file: r.file, bytes: r.bytes,
                                          sha256: r.sha256, source: r.source })),
    tokens: {
      background: "#070B12", surface: "#0D141F", surface_alt: "#111B29",
      border: "#1B2A3B", text: "#E4EDF7", muted: "#7C8DA3",
      up: "#22D3A6", down: "#FF5A6A", accent: "#3B82F6", amber: "#F5B544",
      density: "compact", numeric_font: "ui-monospace, Consolas, monospace",
    },
    usage: "Layout / hierarchy / density reference only. No screenshot is shipped as a product asset.",
  };
}

// ---- router ----------------------------------------------------------------
async function handle(req, res) {
  const u = new URL(req.url, ORIGIN);
  const pathname = u.pathname;
  const search = u.search;
  let bodyText = "";
  if (req.method === "POST" || req.method === "PUT") bodyText = await readBody(req);

  try {
    // health (no session needed)
    if (pathname === "/api/jev/health") {
      return envelope({ data: {
        service: "JEV Trading Desk API", version: "1.0", origin: ORIGIN,
        engine_imported_from: engineUrl, engine_modified: false,
        market_host: VERIFIED_HOST, market_channel: "REST_POLLING",
        engine_symbols: ENGINE_SYMBOLS, collector_only_symbols: COLLECTOR_ONLY_SYMBOLS,
        db: dbPath, real_money: false, real_exchange_orders: false,
      }, meta: { source: "local adapter", channel: "SERVER_ENGINE", label: "LIVE" } });
    }

    // read-only evidence / status
    if (pathname === "/api/jev/system/status")
      return envelope({ data: systemStatus(), meta: SOURCE_LOCAL });
    if (pathname === "/api/jev/providers/status")
      return envelope({ data: providersStatus(), meta: { ...SOURCE_LOCAL, label: "BLOCKED" } });
    if (pathname === "/api/jev/agents/status")
      return envelope({ data: agentsStatus(), meta: { ...SOURCE_LOCAL, label: "BLOCKED" } });
    if (pathname === "/api/jev/strategies/status")
      return envelope({ data: strategiesStatus(), meta: { ...SOURCE_LOCAL, label: "NO_DATA" } });
    if (pathname === "/api/jev/datasource/audit")
      return envelope({ data: dataSourceAudit(), meta: SOURCE_LOCAL });
    if (pathname === "/api/jev/market/quality")
      return envelope({ data: evidence("runtime/v46_market_quality_latest.json", {}),
                        meta: { ...SOURCE_LOCAL, label: "BACKFILL" } });
    if (pathname === "/api/jev/reference/design")
      return envelope({ data: referenceDesign(), meta: SOURCE_LOCAL });

    // real market REST read-through
    if (pathname.startsWith("/api/jev/market/")) {
      const kind = pathname.slice("/api/jev/market/".length);
      const symbol = (u.searchParams.get("symbol") || "BTCUSDT").toUpperCase();
      let url;
      if (kind === "ticker") {
        const symbols = u.searchParams.get("symbols") || JSON.stringify(ENGINE_SYMBOLS);
        url = `${MARKET_BASE}/ticker/24hr?symbols=${encodeURIComponent(symbols)}`;
      } else if (kind === "klines") {
        const interval = /^[0-9]+[mhdw]$/.test(u.searchParams.get("interval") || "")
          ? u.searchParams.get("interval") : "1h";
        const limit = Math.min(1000, Math.max(10, Number(u.searchParams.get("limit") || 200)));
        url = `${MARKET_BASE}/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}`;
      } else if (kind === "depth") {
        const limit = [5, 10, 20, 50, 100, 500, 1000].includes(Number(u.searchParams.get("limit")))
          ? Number(u.searchParams.get("limit")) : 20;
        url = `${MARKET_BASE}/depth?symbol=${encodeURIComponent(symbol)}&limit=${limit}`;
      } else if (kind === "trades") {
        const limit = Math.min(1000, Math.max(1, Number(u.searchParams.get("limit") || 50)));
        url = `${MARKET_BASE}/trades?symbol=${encodeURIComponent(symbol)}&limit=${limit}`;
      } else if (kind === "exchangeInfo") {
        url = `${MARKET_BASE}/exchangeInfo?symbol=${encodeURIComponent(symbol)}`;
      } else {
        return envelope({ ok: false, error: "UNKNOWN_MARKET_ENDPOINT", status: 404 });
      }
      try {
        const { payload, meta } = await fetchVerified(url);
        return envelope({ data: payload, meta });
      } catch (err) {
        return envelope({ ok: false, error: String(err.message || err),
                          code: "MARKET_UNAVAILABLE",
                          meta: { ...metaFallback(url), label: "UNAVAILABLE" }, status: 502 });
      }
    }

    // paper engine routes - the ONLY order path; browser never computes fills
    if (pathname === "/api/jev/paper/audit") {
      const preq = buildEngineRequest(req, "/api/paper/account", "", bodyText);
      return envelope({ data: await paperAudit(preq),
                        meta: { source: "authoritative engine SQLite (read-only projection)",
                                channel: "SERVER_ENGINE", label: "LIVE" } });
    }
    // Real equity snapshots ONLY. When the table is empty the answer is an
    // empty array (NO DATA), never a synthesized curve.
    if (pathname === "/api/jev/paper/equity") {
      const preq = buildEngineRequest(req, "/api/paper/account", "", bodyText);
      const auth = await ledger.readSession(preq, env);
      const sid = auth?.session?.sid || null;
      // The frozen engine's first three migrations do not create this table.
      // A missing table is NO DATA, not an adapter error and never a curve.
      const table = adapter.db.prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_equity_snapshots'"
      ).get();
      const rows = sid && table
        ? adapter.db.prepare("SELECT * FROM paper_equity_snapshots WHERE session_id = ? ORDER BY captured_at ASC LIMIT 500")
            .all(sid).map((r) => ({ ...r }))
        : [];
      return envelope({ data: { snapshots: rows, count: rows.length,
                                table_present: !!table, session_scoped: !!sid,
                                equity_curve_synthesized: false },
                        meta: { source: "paper_equity_snapshots (server-written, read-only)",
                                channel: "SERVER_ENGINE", label: rows.length ? "LIVE" : "NO_DATA" } });
    }
    // GET order/trade lists are a server-side projection of the engine's own
    // account state. POST is forwarded verbatim to the frozen order handler.
    if (pathname === "/api/jev/paper/orders" && req.method === "GET") {
      const preq = buildEngineRequest(req, "/api/paper/account", "", bodyText);
      const r = await engine.handlePaperAccount(preq, env);
      const body = await r.clone().json().catch(() => null);
      return envelope({
        ok: !!(body && body.ok), data: {
          orders: body?.account?.orders || [], trades: body?.account?.trades || [],
          version: body?.version ?? null, stateHash: body?.stateHash ?? null,
        },
        error: body?.error,
        meta: { source: "server-authoritative account state (orders/trades projection)",
                channel: "SERVER_ENGINE", label: "LIVE", http_status: r.status },
        status: r.status,
      });
    }
    const routes = {
      "/api/jev/paper/session": (r) => ledger.handlePaperSession(r, env),
      "/api/jev/paper/account": (r) => engine.handlePaperAccount(r, env),
      "/api/jev/paper/orders": (r) => engine.handlePaperOrders(r, env),
      "/api/jev/paper/events": (r) => engine.handlePaperServerEvents(r, env),
      "/api/jev/paper/replay": (r) => engine.handlePaperReplay(r, env),
      "/api/jev/paper/market/tick": (r) => engine.handlePaperMarketTick(r, env),
    };
    if (routes[pathname]) {
      const preq = buildEngineRequest(req, pathname.replace("/api/jev", "/api"), search, bodyText);
      const r = await routes[pathname](preq);
      return engineResponse(r, { route: pathname });
    }
    if (pathname.startsWith("/api/jev/paper/orders/") && pathname.endsWith("/cancel")) {
      const id = decodeURIComponent(pathname.split("/")[5] || "");
      const preq = buildEngineRequest(req, "/api/paper/orders/" + id + "/cancel", "", bodyText);
      const r = await engine.handlePaperOrderCancel(preq, env, id);
      return engineResponse(r, { route: "order_cancel" });
    }

    return envelope({ ok: false, error: "NOT_FOUND", code: "UNKNOWN_ROUTE", status: 404 });
  } catch (err) {
    return envelope({ ok: false, error: String(err.message || err),
                      code: "ADAPTER_EXCEPTION", status: 500 });
  }
}

function metaFallback(url) {
  return { source: url, channel: "REST_POLLING", fetched_at_utc: utcNow(),
           no_synthetic_data: true };
}

const server = createServer((req, res) => {
  handle(req, res).then((response) => {
    res.statusCode = response.status;
    // Headers.forEach() joins repeated headers with ", ", which CORRUPTS
    // multiple Set-Cookie values. Forward cookies as an array instead.
    const cookies = typeof response.headers.getSetCookie === "function"
      ? response.headers.getSetCookie() : [];
    response.headers.forEach((v, k) => {
      if (k.toLowerCase() !== "set-cookie") res.setHeader(k, v);
    });
    if (cookies.length) res.setHeader("set-cookie", cookies);
    response.arrayBuffer().then((buf) => res.end(Buffer.from(buf)));
  }).catch((err) => {
    res.statusCode = 500;
    res.setHeader("content-type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: String(err.message || err), code: "FATAL" }));
  });
});

server.listen(PORT, HOST, () => {
  console.log(`[jev-desk-api] listening ${ORIGIN}`);
  console.log(`[jev-desk-api] engine=${engineUrl}`);
  console.log(`[jev-desk-api] db=${dbPath}`);
  console.log(`[jev-desk-api] market=${VERIFIED_HOST} (REST polling; WebSocket BLOCKED)`);
});
process.on("SIGTERM", () => { adapter.close(); server.close(() => process.exit(0)); });
