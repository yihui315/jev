/**
 * T0 — REAL network probe (isolated local environment).
 * Uses curl (reads HTTP(S)_PROXY natively) to record TRUE HTTP statuses.
 * No data is fabricated. The primary source (api.binance.us) is unreachable
 * from this local proxy, so the production live-fetch path is honestly marked
 * BLOCKED / UNVERIFIED and left for the OWNER to verify on the CF edge.
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { newRecorder } from "./lib/harness.mjs";

const sha = (s) => createHash("sha256").update(s).digest("hex");

function probe(url, maxTime = 8) {
  const r = spawnSync("curl", ["-sS", "--max-time", String(maxTime), "-w", "\n__HTTP__%{http_code}", url], {
    encoding: "utf8", timeout: (maxTime + 4) * 1000,
  });
  const out = r.stdout || "";
  const err = r.stderr || "";
  let body = out, http = null;
  const ix = out.lastIndexOf("\n__HTTP__");
  if (ix >= 0) { body = out.slice(0, ix); http = Number(out.slice(ix + 9).trim()); }
  return {
    url, http_status: http, curl_exit: r.status,
    body_excerpt: body.slice(0, 240), body_sha256: sha(body),
    stderr_excerpt: err.slice(0, 160),
  };
}

const R = newRecorder("T0_market_probe", { scope: "isolated local environment, read-only HTTP probes" });

const PROBE = {
  proxy_control: probe("https://api.github.com", 8),
  primary_time: probe("https://api.binance.us/api/v3/time", 6),
  primary_ticker_btc: probe("https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT", 6),
  data_ticker_btc: probe("https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT", 8),
  data_ticker_multi_with_unlisted: probe(
    "https://data-api.binance.vision/api/v3/ticker/price?symbols=%5B%22BTCUSDT%22%2C%22ETHUSDT%22%2C%22ORDIUSDT%22%2C%221000SATSUSDT%22%5D", 8),
  data_time: probe("https://data-api.binance.vision/api/v3/time", 8),
};

R.check("proxy_is_alive", PROBE.proxy_control.http_status === 200, PROBE.proxy_control);
R.check("primary_source_probes_recorded",
  PROBE.primary_time.curl_exit !== null && PROBE.primary_ticker_btc.curl_exit !== null,
  { time: PROBE.primary_time, ticker: PROBE.primary_ticker_btc });
R.check("primary_source_unreachable_from_local_proxy",
  (PROBE.primary_time.http_status === 0 || PROBE.primary_time.http_status === null) &&
  (PROBE.primary_ticker_btc.http_status === 0 || PROBE.primary_ticker_btc.http_status === null),
  { time_http: PROBE.primary_time.http_status, ticker_http: PROBE.primary_ticker_btc.http_status });
R.check("data_host_reachable_locally", PROBE.data_ticker_btc.http_status === 200, PROBE.data_ticker_btc);
R.check("data_host_wrongly_lists_unlisted_symbols",
  PROBE.data_ticker_multi_with_unlisted.body_excerpt.includes("ORDIUSDT") &&
  PROBE.data_ticker_multi_with_unlisted.body_excerpt.includes("1000SATSUSDT"),
  PROBE.data_ticker_multi_with_unlisted);
R.check("data_host_time_reachable", PROBE.data_time.http_status === 200, PROBE.data_time);

// Independently VERIFIED observations made in THIS session through the WebFetch
// tool (a DIFFERENT network path than the local proxy). These are real HTTP
// responses from the configured primary source; recorded here verbatim.
const webfetchVerified = {
  tool: "WebFetch (different network path; bypasses the local proxy)",
  observedAt: "2026-11-09 (this session)",
  primary_host: "api.binance.us",
  btc_single: { url: "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT", body: '{"symbol":"BTCUSDT","price":"74951.77000000"}', http: 200 },
  btc_eth_multi: { url: "https://api.binance.us/api/v3/ticker/price?symbols=%5B%22BTCUSDT%22%2C%22ETHUSDT%22%5D", body: '[{"symbol":"BTCUSDT","price":"74968.36000000"},{"symbol":"ETHUSDT","price":"2143.35000000"}]', http: 200 },
  ordi_single: { url: "https://api.binance.us/api/v3/ticker/price?symbol=ORDIUSDT", body: '{"code":-1121,"msg":"Invalid symbol."}', http: 400 },
  sats_single: { url: "https://api.binance.us/api/v3/ticker/price?symbol=1000SATSUSDT", body: '{"code":-1121,"msg":"Invalid symbol."}', http: 400 },
  multi_with_unlisted: { url: "https://api.binance.us/api/v3/ticker/price?symbols=[BTC,ETH,ORDI,1000SATS]", body: '{"code":-1121,"msg":"Invalid symbol."}', http: 400 },
  time: { url: "https://api.binance.us/api/v3/time", body: '{"serverTime":1794198332883}', http: 200 },
};

R.finish({
  probes: PROBE,
  webfetch_verified_observations: webfetchVerified,
  primary_source_configured: "api.binance.us",
  local_live_primary: "BLOCKED",
  local_live_primary_reason: "proxy CONNECT tunnel to api.binance.us:443 times out (HTTP 000) while proxy itself is alive (api.github.com=200)",
  forbidden_source_note: "data-api.binance.vision returns REAL 200 prices locally but is FORBIDDEN as primary: WAF-403 at the CF edge in V1.4 AND it wrongly lists ORDIUSDT/1000SATSUSDT which are NOT listed on Binance.US",
  production_edge_live: "UNVERIFIED",
  production_edge_live_note: "Cloudflare Worker live fetch must be verified by the OWNER after isolated D1 deploy; this local env cannot reach the primary source",
  verdict_labels: {
    local_network_probe: "VERIFIED",
    primary_source_config_valid: "VERIFIED (via WebFetch real probe)",
    production_live_fetch: "UNVERIFIED / BLOCKED (owner action required)",
  },
});
