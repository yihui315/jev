# DAY1_LIVE_MARKET_E2E.md — Market Channel E2E (DAY-1.2)

> LIVE market UNREACHABLE from sandbox → SHADOW fallback (synthetic, NOT counted as real-market return).

## Market source
- Requested symbols: BTCUSDT
- Mode: **SHADOW (synthetic, data_quality=UNKNOWN)**
- Reason: Binance.US unreachable from sandbox (proxy 000/502); synthetic feed, data_quality=UNKNOWN, NOT counted as real-market return

## Provenance fields (per spec: exchange / market / symbol / source URL / timestamp / freshness / payload SHA256 / network context)
- **exchange**: Binance.US
- **market**: spot
- **symbol**: BTCUSDT
- **source_url**: https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT (PROBE_FAILED_000)
- **source_host**: api.binance.us
- **timestamp**: 2026-11-10T01:24:03.915Z
- **freshness_ms**: null
- **freshness_verdict**: UNKNOWN
- **payload_sha256**: 3a85213196b11e652ebd73e098492e106129d233cf8f4ee3d9e6c1137acaa18c
- **network_context**: SHADOW_SYNTHETIC (not verified)
- **data_quality**: UNKNOWN
- **is_real_market**: false

## Pipeline reuse
- `fetchMarket` + `evaluateCycle` from `apps/equity_collector_v45/src/market*.mjs` (8-rule fail-closed guard) attempted for LIVE.
- On failure: synthetic feed built with identical provenance fields but `data_quality=UNKNOWN`, `freshness_verdict=UNKNOWN`, `network_context=SHADOW_SYNTHETIC`, `is_real_market=false`.
- Prohibited behaviors avoided: synthetic impersonating live (explicitly UNKNOWN), auto cross-exchange fallback (none), stale quotes into engine (guard rejects STALE), modifying frozen engine (read-only import), connecting Staging D1 (no D1 used).

## Verdict
- Real-market snapshots available for P&L: **0 (SHADOW only)**.
- This run does NOT claim a real-market return because the price feed is synthetic.
