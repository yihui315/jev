/**
 * V45 market marks fetcher — REAL quotes ONLY, FAIL-CLOSED.
 *
 * Primary source is HARDCODED to Binance.US (api.binance.us /api/v3).
 *   - data-api.binance.vision is FORBIDDEN (WAF-403 at the edge, AND it lists
 *     ORDIUSDT/1000SATSUSDT which are NOT listed on Binance.US -> would wrongly
 *     mark them as priced).
 *   - NEVER fall back to another venue.
 *   - NEVER fabricate a price. NEVER reuse a previous "good" price.
 *   - On failure / missing symbol / timeout / non-2xx / non-positive price /
 *     STALE data -> mark_quality='unknown' (data_quality='UNKNOWN') and the
 *     effective marks fed to the computation are EMPTY (equity=0), so no
 *     unverified number can ever enter a peak/drawdown calculation.
 *
 * The whole function is STATELESS: each cycle fetches fresh, nothing cached.
 * `fetcher` is injectable (env.__fetch / options.fetcher) so local tests can
 * drive controlled ticker responses; production uses global fetch on the
 * Cloudflare edge (no proxy, no API key, no token).
 */
import { D, U } from "./vendor/decimal.mjs";
import {
  MARKET_BASE, MARKET_HOST, MARKET_ENDPOINT, TIME_ENDPOINT, MARKET_VENUE,
  SOURCE, VENUE_SUPPORTED_SYMBOLS, ALLOWED_ENDPOINTS,
  FRESH_THRESHOLD_MS, FETCH_TIMEOUT_MS, USER_AGENT,
} from "./constants.mjs";
import { sha256Hex } from "./hash.mjs";

/**
 * Pure freshness classifier (same semantics as the V1.4 gateway
 * classifyFreshness): non-ok / non-2xx => UNKNOWN; >60s => STALE; else FRESH.
 */
export function classifyFreshness({ ok, httpStatus, ageMs, skewMs, staleThresholdMs = FRESH_THRESHOLD_MS }) {
  if (!ok || !(httpStatus >= 200 && httpStatus < 300)) return "UNKNOWN";
  const basis = skewMs != null ? skewMs : (ageMs != null ? ageMs : null);
  if (basis == null) return "UNKNOWN";
  return basis > staleThresholdMs ? "STALE" : "FRESH";
}

/** One hardened GET. Rejects any host/endpoint outside the verified allowlist. */
async function rawFetch(fetcher, url) {
  const u = new URL(url);
  if (u.protocol !== "https:" || u.hostname !== MARKET_HOST || !ALLOWED_ENDPOINTS.has(u.pathname)) {
    throw new Error("MARKET_SOURCE_NOT_VERIFIED");
  }
  const t0 = Date.now();
  const res = await fetcher(url, {
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    headers: { "user-agent": USER_AGENT, accept: "application/json", "cache-control": "no-cache" },
  });
  const text = await res.text();
  let dateHeader = null;
  try { dateHeader = res.headers && res.headers.get ? res.headers.get("date") : null; } catch { /* ignore */ }
  return { ok: !!res.ok, status: Number(res.status), text, dateHeader, latencyMs: Date.now() - t0 };
}

/**
 * Fetch real marks for `heldSymbols`.
 * Returns { marks, marksJson, markQuality, source, missing, ...provenance }.
 * `marks` values are 1e8 fixed-point BigInt (what computeEquitySnapshot wants).
 */
export async function fetchMarket(fetcher, heldSymbols, { nowMs = Date.now() } = {}) {
  const unique = [...new Set((heldSymbols || []).map(String).filter(Boolean))].sort();
  const fetchedAt = new Date(nowMs).toISOString();

  // (1) venue whitelist: unlisted symbols are NEVER queried elsewhere.
  const querySymbols = unique.filter((s) => VENUE_SUPPORTED_SYMBOLS.includes(s));
  const unlisted = unique.filter((s) => !VENUE_SUPPORTED_SYMBOLS.includes(s));

  // (2) best-effort /api/v3/time for price timestamp + clock skew.
  let serverTimeMs = null, timeProbeOk = false, timeErr = null;
  try {
    const tr = await rawFetch(fetcher, `${MARKET_BASE}${TIME_ENDPOINT}`);
    if (tr.ok && tr.status >= 200 && tr.status < 300) {
      const j = JSON.parse(tr.text);
      const st = Number(j.serverTime);
      if (Number.isFinite(st)) { serverTimeMs = st; timeProbeOk = true; }
      else timeErr = "TIME_NO_SERVERTIME";
    } else timeErr = "TIME_HTTP_" + tr.status;
  } catch (e) { timeErr = "TIME_" + String((e && e.name) || e); }

  // (3) ticker request for whitelisted held symbols ONLY.
  let httpStatus = null, payloadSha256 = null, latencyMs = 0, errorCode = null;
  let tickerOk = false, tickerDateHeader = null;
  const rawMarks = {};
  // V1.6 M3: an upstream response may declare a DIFFERENT exchange/market in
  // its payload. That is a market-mixing attempt, not a quote. The whole cycle
  // must fail closed BEFORE any mark is admitted (QA-M3 / QA-T3-5).
  let foreignMarketDeclared = null;
  let sourceUrl = `${MARKET_BASE}${TIME_ENDPOINT}`;
  if (querySymbols.length) {
    const url = `${MARKET_BASE}${MARKET_ENDPOINT}?symbols=${encodeURIComponent(JSON.stringify(querySymbols))}`;
    sourceUrl = url;
    try {
      const r = await rawFetch(fetcher, url);
      httpStatus = r.status;
      latencyMs = r.latencyMs;
      payloadSha256 = await sha256Hex(r.text);
      tickerDateHeader = r.dateHeader;
      if (!(r.ok && r.status >= 200 && r.status < 300)) errorCode = "MARKET_HTTP_" + r.status;
      else {
        const parsed = JSON.parse(r.text);
        const items = Array.isArray(parsed) ? parsed : [parsed];
        for (const it of items) {
          if (!it || typeof it.symbol !== "string") continue;
          const declaredExchange = it.exchange ?? it.venue ?? null;
          const declaredHost = it.source_host ?? it.sourceHost ?? null;
          const foreignExchange = declaredExchange != null &&
            String(declaredExchange) !== "Binance.US" &&
            String(declaredExchange) !== MARKET_VENUE;
          const foreignHost = declaredHost != null && String(declaredHost) !== MARKET_HOST;
          if (foreignExchange || foreignHost) {
            foreignMarketDeclared = { symbol: it.symbol, exchange: declaredExchange, host: declaredHost };
            continue;
          }
          const price = D(it.price);       // 1e8 fixed-point BigInt
          if (price > 0n) rawMarks[it.symbol] = price;
        }
        tickerOk = true;
      }
    } catch (e) { errorCode = "MARKET_" + String((e && e.name) || e); }
    if (!errorCode && foreignMarketDeclared) {
      // NEVER partially adopt: one foreign-market item poisons the whole cycle.
      for (const k of Object.keys(rawMarks)) delete rawMarks[k];
      errorCode = "MARKET_SOURCE_NOT_VERIFIED";
    }
  } else if (unlisted.length) {
    errorCode = "NO_VENUE_QUOTABLE_SYMBOLS";   // e.g. ORDI / 1000SATS only
  } else {
    tickerOk = true;                          // cash-only account, no marks needed
    errorCode = null;
  }

  // (4) price timestamp + freshness (fail-closed: STALE is NOT usable).
  // V1.6 (contract 3.1): record the price timestamp SOURCE explicitly.
  //   /time success -> exchange_serverTime ; parsed HTTP Date -> http_date ;
  //   otherwise -> received_at (local clock), which is NEVER VERIFIED.
  let priceTsMs, priceTsSource;
  if (serverTimeMs != null) { priceTsMs = serverTimeMs; priceTsSource = "exchange_serverTime"; }
  else if (tickerDateHeader) {
    const d = Date.parse(tickerDateHeader);
    if (Number.isFinite(d)) { priceTsMs = d; priceTsSource = "http_date"; }
    else { priceTsMs = nowMs; priceTsSource = "received_at"; }
  } else { priceTsMs = nowMs; priceTsSource = "received_at"; }
  const clockSkewMs = serverTimeMs != null ? Math.abs(nowMs - serverTimeMs) : null;
  const freshnessMs = Math.abs(nowMs - priceTsMs);
  const freshnessVerdict = classifyFreshness({
    ok: tickerOk && !errorCode, httpStatus: httpStatus == null ? 200 : httpStatus,
    ageMs: freshnessMs, skewMs: clockSkewMs,
  });

  // (5) usability: all held symbols must be quoted AND data must be FRESH.
  const rawMissing = unique.filter((s) => !(rawMarks[s] > 0n));
  const usable = !errorCode && tickerOk && rawMissing.length === 0 && freshnessVerdict === "FRESH";
  if (!errorCode && rawMissing.length) errorCode = "MISSING_OR_UNLISTED_SYMBOL";
  if (!errorCode && freshnessVerdict !== "FRESH") errorCode = "MARKET_STALE";

  // Effective marks fed to the computation: EMPTY when not usable.
  // This is the anti-fabrication / anti-fallback guarantee.
  const marks = usable ? rawMarks : {};
  const missing = unique.filter((s) => !(marks[s] > 0n));
  const marksJson = JSON.stringify(Object.fromEntries(
    Object.entries(marks).map(([k, v]) => [k, U(v)])
  ));
  const markQuality = usable ? "verified" : "unknown";
  const dataQuality = usable ? "VERIFIED" : "UNKNOWN";

  return {
    marks, marksJson, markQuality, dataQuality, missing,
    source: SOURCE,
    sourceHost: MARKET_HOST, endpoint: MARKET_ENDPOINT, sourceUrl,
    venue: MARKET_VENUE,
    // V1.6: a payload item that declared a non-US market poisons the cycle.
    // Exposed as a boolean so collector.mjs can feed it to the §3 guard
    // (rule 6, MARKET_SOURCE_NOT_VERIFIED) for defense in depth.
    foreignMarketDeclared: foreignMarketDeclared != null,
    httpStatus, payloadSha256,
    priceTs: new Date(priceTsMs).toISOString(),
    priceTsSource,
    fetchedAt,
    freshnessMs, freshnessVerdict, clockSkewMs,
    timeProbeOk, timeErr, errorCode, latencyMs,
  };
}
