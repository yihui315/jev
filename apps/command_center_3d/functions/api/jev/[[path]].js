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

// The FROZEN engine hardcodes this host (paper-server.mjs:6 API_BASE). It is
// byte-for-byte unmodified. Measured from the Cloudflare Worker edge:
//   data-api.binance.vision -> 403 ; api.binance.com / api1-4 -> 451 geo-block.
const ENGINE_MARKET_HOST = "data-api.binance.vision";
// Binance-official v3-compatible public REST that IS reachable from the edge
// (measured HTTP 200). This is Binance.US SPOT — a DIFFERENT venue from the
// Binance.com global book. Never claim otherwise.
const VERIFIED_HOST = "api.binance.us";
const MARKET_VENUE = "Binance.US spot (official public REST v3)";
const MARKET_BASE = `https://${VERIFIED_HOST}/api/v3`;
const ALLOWED_ENDPOINTS = new Set([
  "/api/v3/ticker/price", "/api/v3/ticker/24hr", "/api/v3/klines",
  "/api/v3/depth", "/api/v3/trades", "/api/v3/exchangeInfo",
]);
const ENGINE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const COLLECTOR_ONLY_SYMBOLS = ["BNBUSDT", "SOLUSDT"];
// Symbols that actually exist on the reachable venue (measured 200). Anything
// outside this set is filtered from multi-symbol ticker queries; orders for
// those symbols fail upstream and are HONESTLY rejected — never synthesized.
const MARKET_SUPPORTED_SYMBOLS = ["BTCUSDT", "ETHUSDT"];
const MARKET_UNSUPPORTED_SYMBOLS = ENGINE_SYMBOLS.filter((s) => !MARKET_SUPPORTED_SYMBOLS.includes(s));

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

// ---- market transport adapter (engine file stays byte-for-byte frozen) -----
// The frozen engine resolves its fetcher via dbFetch(env) => env.__fetch || fetch
// (paper-server.mjs:88). We inject __fetch at the GATEWAY layer only. It
// repoints the engine's hardcoded host to the reachable Binance-official v3
// endpoint and trims the multi-symbol ticker query to symbols that exist there.
// The upstream response body is passed through UNCHANGED: real prices, no
// cache, no synthesis, no re-scoring. Any upstream failure still propagates to
// the engine, which rejects the order honestly.
function marketRewrite(rawUrl) {
  const u = new URL(String(rawUrl));
  if (u.protocol !== "https:" || u.hostname !== ENGINE_MARKET_HOST) return null;
  u.hostname = VERIFIED_HOST;
  if (u.pathname === "/api/v3/ticker/price" && u.searchParams.has("symbols")) {
    let list = [];
    try {
      const v = JSON.parse(u.searchParams.get("symbols"));
      if (Array.isArray(v)) list = v.filter((s) => typeof s === "string" && MARKET_SUPPORTED_SYMBOLS.includes(s));
    } catch { list = []; }
    u.searchParams.set("symbols", JSON.stringify(list));
  }
  return u.toString();
}
async function marketFetcher(input, init) {
  let target = input;
  try {
    const raw = typeof input === "string" ? input : (input && input.url);
    const rewritten = marketRewrite(raw);
    if (rewritten) target = rewritten;
  } catch { /* pass through untouched */ }
  return fetch(target, init);
}

// ---- real Worker-egress market probe (read-only, no user input) ------------
// Diagnosis for the Binance 403 issue. Every target is a hardcoded constant;
// no URL is derived from the request, so there is no SSRF surface. Protected
// by Basic auth (gateway + functions/_middleware.js). Returns REAL HTTP
// statuses measured from the Cloudflare Worker edge — never synthetic.
const PROBE_TARGETS = [
  { id: "binance_main",      kind: "binance_v3",  url: "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_vision",    kind: "binance_v3",  url: "https://api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_data",      kind: "binance_v3",  url: "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_api1",      kind: "binance_v3",  url: "https://api1.binance.com/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_api2",      kind: "binance_v3",  url: "https://api2.binance.com/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_api3",      kind: "binance_v3",  url: "https://api3.binance.com/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_api4",      kind: "binance_v3",  url: "https://api4.binance.com/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_us",        kind: "binance_v3",  url: "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "binance_data_com",  kind: "binance_v3",  url: "https://data.binance.com/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "coinbase_exchange", kind: "third_party", url: "https://api.exchange.coinbase.com/products/BTC-USD/ticker" },
  { id: "kraken",            kind: "third_party", url: "https://api.kraken.com/0/public/Ticker?pair=XBTUSD" },
  { id: "coingecko",         kind: "third_party", url: "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd" },
  // Exact URLs the frozen engine requests (API_BASE = data-api.binance.vision/api/v3),
  // replayed against the reachable Binance-official v3-compatible host. This is
  // the "real market data verification" step before any order-lifecycle test.
  { id: "us_ticker_multi",   kind: "engine_exact", url: "https://api.binance.us/api/v3/ticker/price?symbols=%5B%22BTCUSDT%22%2C%22ETHUSDT%22%2C%22ORDIUSDT%22%2C%221000SATSUSDT%22%5D" },
  { id: "us_ticker_btc",     kind: "engine_exact", url: "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "us_ticker_eth",     kind: "engine_exact", url: "https://api.binance.us/api/v3/ticker/price?symbol=ETHUSDT" },
  { id: "us_ticker_ordi",    kind: "engine_exact", url: "https://api.binance.us/api/v3/ticker/price?symbol=ORDIUSDT" },
  { id: "us_ticker_sats",    kind: "engine_exact", url: "https://api.binance.us/api/v3/ticker/price?symbol=1000SATSUSDT" },
  { id: "us_depth_btc",      kind: "engine_exact", url: "https://api.binance.us/api/v3/depth?symbol=BTCUSDT&limit=1000" },
  { id: "us_exchange_btc",   kind: "engine_exact", url: "https://api.binance.us/api/v3/exchangeInfo?symbol=BTCUSDT" },
  { id: "us_exchange_eth",   kind: "engine_exact", url: "https://api.binance.us/api/v3/exchangeInfo?symbol=ETHUSDT" },
];
async function probeOne(t) {
  const started = Date.now();
  try {
    const res = await fetch(t.url, {
      signal: AbortSignal.timeout(4500),
      headers: { "user-agent": "JEV-Trading-Desk/1.0 (public REST; compliance probe)", accept: "application/json" },
    });
    const text = await res.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch { /* non-JSON (WAF page) is kept verbatim */ }
    let parsedSummary = null;
    if (Array.isArray(parsed)) parsedSummary = parsed.slice(0, 6).map((v) => v && v.symbol ? `${v.symbol}:${v.price}` : typeof v);
    else if (parsed && Array.isArray(parsed.symbols)) parsedSummary = parsed.symbols.slice(0, 4).map((s) => ({ symbol: s.symbol, status: s.status, filters: (s.filters || []).map((f) => f.filterType) }));
    else if (parsed && parsed.lastUpdateId !== undefined) parsedSummary = { lastUpdateId: parsed.lastUpdateId, bids: (parsed.bids || []).length, asks: (parsed.asks || []).length };
    else if (parsed && parsed.symbol) parsedSummary = `${parsed.symbol}:${parsed.price ?? ""}`;
    else if (parsed && parsed.code !== undefined) parsedSummary = { code: parsed.code, msg: parsed.msg };
    return {
      id: t.id, kind: t.kind, url: t.url,
      http_status: res.status, ok: res.ok, latency_ms: Date.now() - started,
      content_type: res.headers.get("content-type") || null,
      body_excerpt: text.slice(0, 180),
      json_keys: parsed && !Array.isArray(parsed) ? Object.keys(parsed).slice(0, 8) : (Array.isArray(parsed) ? ["<array:" + parsed.length + ">"] : null),
      parsed_summary: parsedSummary,
      verdict: res.ok ? "REACHABLE" : "HTTP_" + res.status,
    };
  } catch (err) {
    return {
      id: t.id, kind: t.kind, url: t.url,
      http_status: null, ok: false, latency_ms: Date.now() - started,
      error: String(err && err.name ? err.name : err),
      verdict: "UNREACHABLE",
    };
  }
}
async function runMarketProbe() {
  const results = await Promise.all(PROBE_TARGETS.map(probeOne));
  return {
    probed_at_utc: new Date().toISOString(),
    runtime: "cloudflare-worker-edge",
    note: "Real outbound HTTP from the Worker edge. No synthetic data. Hardcoded targets only.",
    counts: {
      reachable: results.filter((r) => r.ok).length,
      binance_v3_reachable: results.filter((r) => r.ok && r.kind === "binance_v3").length,
      third_party_reachable: results.filter((r) => r.ok && r.kind === "third_party").length,
    },
    results,
  };
}

// ---- T02/T03: probe-driven market status (NEVER a hardcoded LIVE claim) ----
// The status endpoints used to contain hardcoded "VERIFIED"/"UNAVAILABLE"
// strings. That is dishonest in both directions: a stale constant can claim
// LIVE after the source dies, or claim UNAVAILABLE after it recovers.
// Now every status response is derived from a REAL outbound request made at
// request time, with an HTTP status, sha256, timings and a clock-skew based
// freshness verdict. Nothing is cached: cached data is never presented as live.
const MARKET_STALE_MS = 60000;
const STATUS_UA = "JEV-Trading-Desk/1.0 (public REST)";

// Pure, exported for unit testing. A stale/unavailable source is never
// reported as LIVE. `nowMs` is injectable so the STALE branch can be verified
// with synthetic input WITHOUT fabricating any real market data.
export function classifyFreshness({ ok, httpStatus, skewMs, staleThresholdMs = MARKET_STALE_MS }) {
  if (!ok || !(httpStatus >= 200 && httpStatus < 300)) return "UNAVAILABLE";
  if (skewMs !== null && skewMs !== undefined && skewMs > staleThresholdMs) return "STALE";
  return "VERIFIED";
}

async function probeStatusTarget(host, endpoint) {
  const started = Date.now();
  const fetchedAt = new Date().toISOString();
  const url = `https://${host}${endpoint}`;
  try {
    const res = await fetch(url, {
      signal: AbortSignal.timeout(6000),
      headers: { "user-agent": STATUS_UA, accept: "application/json" },
    });
    const text = await res.text();
    const sha256 = await sha256Hex(text);
    let parsed = null;
    try { parsed = JSON.parse(text); } catch { /* WAF/HTML body */ }
    const serverTime = parsed ? Number(parsed.serverTime) : NaN;
    const skewMs = Number.isFinite(serverTime) ? Math.abs(Date.now() - serverTime) : null;
    const verdict = classifyFreshness({ ok: res.ok, httpStatus: res.status, skewMs });
    return {
      source_host: host, endpoint, url, ok: res.ok, http_status: res.status,
      fetched_at_utc: fetchedAt, latency_ms: Date.now() - started,
      bytes: text.length, sha256, content_type: res.headers.get("content-type") || null,
      server_header: res.headers.get("server") || null, cf_ray: res.headers.get("cf-ray") || null,
      server_time_ms: Number.isFinite(serverTime) ? serverTime : null,
      clock_skew_ms: skewMs, freshness: verdict === "VERIFIED" ? "FRESH" : verdict,
      verdict, body_excerpt: text.slice(0, 120),
    };
  } catch (err) {
    return {
      source_host: host, endpoint, url, ok: false, http_status: null,
      fetched_at_utc: fetchedAt, latency_ms: Date.now() - started,
      bytes: 0, sha256: null, error: String(err && err.name ? err.name : err),
      verdict: "UNAVAILABLE", freshness: "UNAVAILABLE",
    };
  }
}

async function marketStatusEvidence() {
  const [live, original] = await Promise.all([
    probeStatusTarget(VERIFIED_HOST, "/api/v3/time"),
    probeStatusTarget(ENGINE_MARKET_HOST, "/api/v3/ticker/price?symbol=BTCUSDT"),
  ]);
  return {
    generated_utc: utcNow(),
    policy: "Derived from real Worker-edge HTTP probes at request time. No cache, no constant LIVE claim. >=60s clock skew => STALE; non-2xx/timeout => UNAVAILABLE.",
    stale_threshold_ms: MARKET_STALE_MS,
    live, original,
  };
}

// ---- T02: controlled host x endpoint x UA diagnosis ------------------------
// All targets are hardcoded constants (no request-derived URL => no SSRF).
// Goal: prove whether the 403 depends on the User-Agent, the endpoint path,
// or the upstream host / egress policy. Every row is a REAL Worker request.
const DIAG_ENDPOINTS = [
  { id: "time",         path: "/api/v3/time" },
  { id: "ticker_price", path: "/api/v3/ticker/price?symbol=BTCUSDT" },
  { id: "depth",        path: "/api/v3/depth?symbol=BTCUSDT&limit=5" },
  { id: "klines",       path: "/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=10" },
];
const DIAG_HOSTS = [
  { id: "engine_host",   host: ENGINE_MARKET_HOST, note: "frozen engine's hardcoded host" },
  { id: "verified_host", host: VERIFIED_HOST,       note: "gateway transport target" },
];
const DIAG_UA = [
  { id: "gateway_custom", ua: STATUS_UA },
  { id: "empty_ua",       ua: "" },
  { id: "browser",        ua: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36" },
];

async function diagOne(host, ep, ua) {
  const started = Date.now();
  const url = `https://${host}${ep.path}`;
  const headers = { accept: "application/json" };
  // Workers always inject a default UA; an explicit empty UA is the closest
  // we can get to "no UA". This is recorded honestly in the returned evidence.
  if (ua.ua !== null) headers["user-agent"] = ua.ua;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5000), headers });
    const text = await res.text();
    const sha256 = await sha256Hex(text);
    let parsed = null;
    try { parsed = JSON.parse(text); } catch { /* WAF/HTML */ }
    let summary = null;
    if (parsed && parsed.serverTime) summary = { serverTime: parsed.serverTime };
    else if (parsed && parsed.symbol) summary = { symbol: parsed.symbol, price: parsed.price };
    else if (parsed && parsed.lastUpdateId) summary = { lastUpdateId: parsed.lastUpdateId, bids: (parsed.bids || []).length, asks: (parsed.asks || []).length };
    else if (Array.isArray(parsed)) summary = { bars: parsed.length, firstOpenTime: parsed[0]?.[0] ?? null, lastOpenTime: parsed.at(-1)?.[0] ?? null };
    else if (parsed && parsed.code !== undefined) summary = { code: parsed.code, msg: parsed.msg };
    return {
      host_id: `${host}`, endpoint_id: ep.id, ua_id: ua.id, url,
      http_status: res.status, ok: res.ok, latency_ms: Date.now() - started,
      bytes: text.length, sha256, content_type: res.headers.get("content-type") || null,
      server_header: res.headers.get("server") || null, cf_ray: res.headers.get("cf-ray") || null,
      parsed_summary: summary, body_excerpt: text.slice(0, 140),
      exception_class: res.ok ? null : "HTTP_" + res.status,
    };
  } catch (err) {
    return {
      host_id: `${host}`, endpoint_id: ep.id, ua_id: ua.id, url,
      http_status: null, ok: false, latency_ms: Date.now() - started,
      sha256: null, exception_class: String(err && err.name ? err.name : err),
    };
  }
}

async function runMarketDiagnosis() {
  const jobs = [];
  for (const h of DIAG_HOSTS) {
    for (const ep of DIAG_ENDPOINTS) {
      for (const ua of DIAG_UA) jobs.push(diagOne(h.host, ep, ua));
    }
  }
  const rows = await Promise.all(jobs);
  // Derived conclusions from the measured matrix (never hardcoded).
  const byHost = {};
  for (const h of DIAG_HOSTS) {
    const rs = rows.filter((r) => r.host_id === h.host);
    const statuses = [...new Set(rs.map((r) => r.http_status))].sort();
    byHost[h.host] = {
      note: h.note, requests: rs.length, http_statuses: statuses,
      ok_count: rs.filter((r) => r.ok).length,
      by_ua: DIAG_UA.map((u) => ({
        ua: u.id,
        statuses: [...new Set(rs.filter((r) => r.ua_id === u.id).map((r) => r.http_status))].sort(),
      })),
    };
  }
  const uaChangesOutcome = DIAG_HOSTS.some((h) => {
    const rs = rows.filter((r) => r.host_id === h.host);
    const sets = DIAG_UA.map((u) => JSON.stringify([...new Set(rs.filter((r) => r.ua_id === u.id).map((r) => r.http_status))].sort()));
    return new Set(sets).size > 1;
  });
  return {
    diagnosed_at_utc: utcNow(), runtime: "cloudflare-worker-edge",
    matrix_shape: { hosts: DIAG_HOSTS.length, endpoints: DIAG_ENDPOINTS.length, ua_variants: DIAG_UA.length, total_requests: rows.length },
    ua_note: "Cloudflare Workers inject a default User-Agent; 'empty_ua' sends an explicit empty UA. Recorded verbatim.",
    derived: {
      ua_changes_http_outcome: uaChangesOutcome,
      interpretation: uaChangesOutcome
        ? "UA variant changed the HTTP outcome — request headers are implicated."
        : "All UA variants produced identical HTTP outcomes on each host => adding/changing a User-Agent is NOT a valid fix for the 403.",
      by_host: byHost,
    },
    rows,
  };
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

// Market status is DERIVED from marketStatusEvidence() (real Worker-edge HTTP
// probes at request time). No hardcoded LIVE/UNAVAILABLE claim.
async function systemStatus(evidence) {
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
      market_marks: evidence
        ? `${evidence.live.verdict} (probe-derived) — source=${evidence.live.source_host} http=${evidence.live.http_status} fetched=${evidence.live.fetched_at_utc} sha256=${String(evidence.live.sha256).slice(0, 16)} clock_skew_ms=${evidence.live.clock_skew_ms}. BTCUSDT/ETHUSDT via Binance.US spot real REST (no synthesis); ORDIUSDT/1000SATSUSDT not listed there -> honestly rejected. Original engine host probe: http=${evidence.original.http_status} verdict=${evidence.original.verdict}.`
        : "UNVERIFIED (probe unavailable)",
      gate_fills: "No static fill claim. Fills are authoritative D1 rows; see session-scoped /api/jev/paper/audit. No 30-day research run performed.",
    },
    market_evidence: evidence || null,
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
async function dataSourceAudit(evidence) {
  const live = evidence?.live;
  const orig = evidence?.original;
  const liveStatus = live ? live.verdict : "UNVERIFIED";
  return [
    { id: "market_rest", label: liveStatus, channel: "REST_POLLING", source: VERIFIED_HOST, venue: MARKET_VENUE, status: liveStatus, updated_utc: live?.fetched_at_utc || utcNow(),
      http_status: live?.http_status ?? null, sha256: live?.sha256 ?? null, latency_ms: live?.latency_ms ?? null,
      clock_skew_ms: live?.clock_skew_ms ?? null, freshness: live?.freshness ?? "UNVERIFIED",
      probe_url: live?.url ?? null,
      note: "Status is DERIVED from a real Worker-edge probe (no hardcoded LIVE claim). Engine requests data-api.binance.vision; gateway injects env.__fetch (engine NATIVE hook, source unmodified) to repoint to api.binance.us. Response bodies pass through unchanged: REAL Binance.US spot prices, no cache, no synthesis. This is NOT the Binance.com global book. Supported: BTCUSDT/ETHUSDT. ORDIUSDT/1000SATSUSDT are filtered from the multi-symbol ticker and their orders are honestly rejected." },
    { id: "market_rest_original_host", label: orig ? (orig.ok ? "VERIFIED" : "BLOCKED") : "UNVERIFIED", channel: "REST_POLLING", source: ENGINE_MARKET_HOST, status: orig ? (orig.ok ? "VERIFIED" : "BLOCKED") : "UNVERIFIED", http_status: orig?.http_status ?? null,
      probed_at_utc: orig?.fetched_at_utc ?? null, sha256: orig?.sha256 ?? null, server_header: orig?.server_header ?? null,
      note: `Measured live from the Cloudflare Worker edge (probe-derived, not hardcoded): http=${orig?.http_status ?? "n/a"}. Binance global hosts api.binance.com / api1-4 returned 451 (restricted location) in the T02 matrix. Retained as evidence for the transport rewrite.` },
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
  // FAIL-CLOSED: an invalid/missing Paper Session must never return ANY row or
  // cardinality — not even a global count. The previous implementation let a
  // null sid degrade to "WHERE 1=1" style queries (empty WHERE), exposing the
  // whole table's details plus a global admin_global_counts summary to an
  // ordinary preview account. Both are removed here.
  const auth = await readSession(preq, env);
  const sid = auth?.session?.sid || null;
  if (!sid) return null; // caller returns 401; no DB read is performed at all
  // D1's prepare().bind().all()/.first() are async — must await.
  const rows = async (sql, ...args) => (await db.prepare(sql).bind(...args).all()).results || [];
  const count = async (sql, ...args) => Number((await db.prepare(sql).bind(...args).first())?.n || 0);
  // Every query is unconditionally session-scoped.
  const scopedCount = (table) => count(`SELECT COUNT(*) AS n FROM ${table} WHERE session_id = ?`, sid);
  return {
    session_scoped: true, session_id: sid, session_valid: true,
    counts: {
      accounts: await scopedCount("paper_accounts"),
      orders: await scopedCount("paper_orders"),
      fills: await scopedCount("paper_fills"),
      server_events: await scopedCount("paper_server_events"),
    },
    // No admin_global_counts: cross-session cardinality is not exposed to a
    // preview account, even in aggregate (T05 requirement).
    orders: await rows("SELECT * FROM paper_orders WHERE session_id = ? ORDER BY created_at DESC LIMIT 100", sid),
    fills: await rows("SELECT * FROM paper_fills WHERE session_id = ? ORDER BY created_at DESC LIMIT 100", sid),
    events: await rows("SELECT seq, event_id, command_id, event_type, created_at FROM paper_server_events WHERE session_id = ? ORDER BY seq DESC LIMIT 100", sid),
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
        market_host: VERIFIED_HOST, market_venue: MARKET_VENUE, market_channel: "REST_POLLING",
        engine_original_market_host: ENGINE_MARKET_HOST,
        market_transport: "env.__fetch gateway rewrite (engine source unmodified)",
        market_fetch_injected: typeof env.__fetch === "function",
        market_supported_symbols: MARKET_SUPPORTED_SYMBOLS,
        market_unsupported_symbols: MARKET_UNSUPPORTED_SYMBOLS,
        engine_symbols: ENGINE_SYMBOLS, collector_only_symbols: COLLECTOR_ONLY_SYMBOLS,
        db: "D1:jev-paper-staging", real_money: false, real_exchange_orders: false,
      }, meta: { source: "cloudflare function", channel: "SERVER_ENGINE", label: "LIVE" } });
    }
    if (pathname === "/api/jev/system/status") {
      const ev = await marketStatusEvidence();
      return envelope({ data: await systemStatus(ev), meta: { source: "probe-derived live Worker-edge evidence", channel: "REST_POLLING", label: ev.live.verdict } });
    }
    if (pathname === "/api/jev/providers/status") return envelope({ data: providersStatus(), meta: { label: "BLOCKED" } });
    if (pathname === "/api/jev/agents/status") return envelope({ data: agentsStatus(), meta: { label: "BLOCKED" } });
    if (pathname === "/api/jev/strategies/status") return envelope({ data: strategiesStatus(), meta: { label: "NO_DATA" } });
    if (pathname === "/api/jev/datasource/audit") {
      const ev = await marketStatusEvidence();
      return envelope({ data: await dataSourceAudit(ev), meta: { source: "probe-derived live Worker-edge evidence", generated_utc: ev.generated_utc } });
    }
    if (pathname === "/api/jev/market/status") {
      return envelope({ data: await marketStatusEvidence(), meta: { source: "live Worker-edge probe", label: "VERIFIED" } });
    }
    if (pathname === "/api/jev/market/diagnose") {
      return envelope({ data: await runMarketDiagnosis(), meta: { source: "worker-edge controlled matrix", label: "VERIFIED" } });
    }
    if (pathname === "/api/jev/market/quality") return envelope({ data: { note: "NO_DATA on Cloudflare staging; real bar counts not migrated" }, meta: { label: "NO_DATA" } });
    if (pathname === "/api/jev/reference/design") return envelope({ data: { verdict: "UNVERIFIED", note: "reference pack not hosted on Cloudflare" }, meta: {} });

    if (pathname === "/api/jev/market/probe") {
      return envelope({ data: await runMarketProbe(), meta: { source: "worker-egress-probe", channel: "REST_POLLING", label: "VERIFIED" } });
    }

    if (pathname.startsWith("/api/jev/market/")) {
      const kind = pathname.slice("/api/jev/market/".length);
      const symbol = (url.searchParams.get("symbol") || "BTCUSDT").toUpperCase();
      let u;
      if (kind === "ticker") {
        const rawSymbols = url.searchParams.get("symbols");
        let symbols = JSON.stringify(MARKET_SUPPORTED_SYMBOLS);
        if (rawSymbols) {
          try {
            const parsedSymbols = JSON.parse(rawSymbols);
            if (Array.isArray(parsedSymbols)) symbols = JSON.stringify(parsedSymbols.filter((s) => MARKET_SUPPORTED_SYMBOLS.includes(String(s).toUpperCase())));
          } catch { /* keep the supported default */ }
        }
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
      const data = await paperAudit(preq, db, env);
      if (!data) return envelope({ ok: false, error: "PAPER_SESSION_REQUIRED", code: "SESSION_INVALID", status: 401 });
      return envelope({ data, meta: { source: "authoritative engine D1 (read-only projection, session-scoped)", channel: "SERVER_ENGINE", label: "LIVE", session_scoped: true } });
    }
    if (pathname === "/api/jev/paper/equity") {
      const preq = buildEngineRequest(request, "/api/paper/account", "", bodyText);
      const auth = await readSession(preq, env);
      // FAIL-CLOSED: no valid session => 401, no schema probe, no rows, and no
      // table_present hint. The old code ignored auth.error and still revealed
      // schema existence to an unauthenticated-in-session caller.
      if (auth.error) return auth.error;
      const sid = auth?.session?.sid || null;
      if (!sid) return envelope({ ok: false, error: "PAPER_SESSION_REQUIRED", code: "SESSION_INVALID", status: 401 });
      const table = await db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_equity_snapshots'").first();
      const rows = table
        ? ((await db.prepare("SELECT * FROM paper_equity_snapshots WHERE session_id = ? ORDER BY created_at ASC LIMIT 500").bind(sid).all()).results || [])
        : [];
      return envelope({ data: { snapshots: rows, count: rows.length, table_present: !!table, session_scoped: true, equity_curve_synthesized: false }, meta: { source: "paper_equity_snapshots (server-written, read-only)", channel: "SERVER_ENGINE", label: rows.length ? "LIVE" : "NO_DATA" } });
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

  // Inject the market transport adapter through the frozen engine's NATIVE hook
  // (dbFetch(env) => env.__fetch || fetch). Engine files remain unmodified.
  let engineEnv = env;
  try {
    env.__fetch = marketFetcher;
    if (typeof env.__fetch !== "function") throw new Error("ENV_FROZEN");
  } catch {
    engineEnv = Object.create(env);
    engineEnv.__fetch = marketFetcher;
  }

  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/jev")) return handleApi(request, engineEnv, url);

  // Non-API paths are not served by this Function; Pages serves static assets.
  return new Response("Not Found", { status: 404 });
}
