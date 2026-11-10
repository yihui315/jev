#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JEV V1.5 P3 market-coverage probe (READ-ONLY public market data).

Probes two Binance public market-data hosts for four symbols across five
endpoints. Writes one evidence JSON per HTTP request plus INDEX.json and
NETWORK_CONTROL.json.

Hard constraints honoured:
  - No credentials are read or sent (credentials_sent=false in every record).
  - No remote write operations; only HTTP GET to public read-only endpoints.
  - Nothing is fabricated: a failed request is recorded as TRANSPORT_FAILURE.
  - No third-party exchange prices are substituted for another market.
  - Standard library only (urllib); no `requests` dependency.

Reproduce:
  python D:/jev-v15-wt/v15-market/probe_market.py
  (requires env HTTP_PROXY/HTTPS_PROXY to be set for outbound access)
"""
import os
import sys
import json
import time
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT = r"D:/jev-v15-wt/v15-market/evidence"
os.makedirs(OUT, exist_ok=True)

PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
UA = "jev-v15-market-auditor/1.5 (read-only public data probe)"

SOURCES = {
    "binance_us": {
        "base": "https://api.binance.us",
        "exchange": "Binance.US",
        "market": "spot",
        "note": "V1.4 designated source; US-regulated market, DISTINCT from Binance Global",
    },
    "binance_global_vision": {
        "base": "https://data-api.binance.vision",
        "exchange": "Binance Global (data-api.binance.vision)",
        "market": "spot",
        "note": "Binance official public market-data host for the GLOBAL spot market",
    },
}

SYMBOLS = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"]
ENDPOINT_NAMES = ["exchangeInfo", "ticker_price", "bookTicker", "trades", "time"]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def endpoint_path(name, symbol):
    return {
        "exchangeInfo": f"/api/v3/exchangeInfo?symbol={symbol}",
        "ticker_price": f"/api/v3/ticker/price?symbol={symbol}",
        "bookTicker": f"/api/v3/ticker/bookTicker?symbol={symbol}",
        "trades": f"/api/v3/trades?symbol={symbol}&limit=1",
        "time": "/api/v3/time",
    }[name]


def parse_summary(name, data):
    """Extract key real fields from parsed JSON. Never invents values."""
    out = {}
    try:
        if isinstance(data, dict) and "code" in data and "msg" in data:
            out["api_error_code"] = data.get("code")
            out["api_error_msg"] = data.get("msg")
            return out
        if name == "time":
            st = data.get("serverTime")
            out["serverTime"] = st
            out["serverTime_utc"] = (
                datetime.fromtimestamp(st / 1000, timezone.utc).isoformat(timespec="milliseconds")
                if isinstance(st, int) else None
            )
        elif name == "exchangeInfo":
            syms = data.get("symbols") or []
            if syms:
                s = syms[0]
                out = {
                    "symbol": s.get("symbol"),
                    "status": s.get("status"),
                    "baseAsset": s.get("baseAsset"),
                    "quoteAsset": s.get("quoteAsset"),
                    "isSpotTradingAllowed": s.get("isSpotTradingAllowed"),
                    "permissions": s.get("permissions"),
                }
        elif name == "ticker_price":
            out = {
                "symbol": data.get("symbol"),
                "price": data.get("price"),
                "price_unit": "quote_asset per 1 base_asset (see exchangeInfo.quoteAsset)",
            }
        elif name == "bookTicker":
            out = {
                "symbol": data.get("symbol"),
                "bidPrice": data.get("bidPrice"),
                "bidQty": data.get("bidQty"),
                "askPrice": data.get("askPrice"),
                "askQty": data.get("askQty"),
            }
        elif name == "trades":
            if isinstance(data, list) and data:
                t = data[0]
                ts = t.get("time")
                out = {
                    "trade_count": len(data),
                    "last_trade_id": t.get("id"),
                    "last_trade_price": t.get("price"),
                    "last_trade_qty": t.get("qty"),
                    "last_trade_time_ms": ts,
                    "last_trade_time_utc": (
                        datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(timespec="milliseconds")
                        if isinstance(ts, int) else None
                    ),
                    "isBuyerMaker": t.get("isBuyerMaker"),
                }
            else:
                out["trade_count"] = 0 if isinstance(data, list) else None
    except Exception as e:
        out["parse_warning"] = f"{type(e).__name__}: {e}"
    return out


def classify(status, parsed):
    if parsed is None:
        return f"HTTP_{status}"
    if isinstance(parsed, dict) and "code" in parsed and "msg" in parsed:
        return "API_ERROR"
    if status == 200:
        return "OK"
    return f"HTTP_{status}"


def probe(job):
    source_key, src, symbol, name = job
    path = endpoint_path(name, symbol)
    url = src["base"] + path
    req_local_ms = int(time.time() * 1000)
    rec = {
        "probe_schema": "jev-v15-market-evidence/1.0",
        "source_key": source_key,
        "exchange": src["exchange"],
        "market": src["market"],
        "exchange_note": src["note"],
        "symbol": symbol,  # "SERVER" for the source-level /time probe
        "endpoint": name,
        "request": {
            "method": "GET",
            "url": url,
            "path": path,
            "probe_ts_utc": now_iso(),
            "probe_ts_epoch_ms": req_local_ms,
            "user_agent": UA,
            "proxy": PROXY,
            "credentials_sent": False,
        },
        "response": None,
        "derived": {},
    }
    ph = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY} if PROXY else {})
    opener = urllib.request.build_opener(ph)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    t0 = time.time()
    try:
        r = opener.open(req, timeout=25)
        body = r.read()
        status = r.status
        hdrs = {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        body = e.read()
        status = e.code
        hdrs = {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:
        rec["response"] = {"transport_error": {"type": type(e).__name__, "msg": str(e)}}
        rec["derived"] = {
            "ok": False,
            "classification": "TRANSPORT_FAILURE",
            "latency_s": round(time.time() - t0, 3),
            "note": "No HTTP response obtained; upstream/proxy CONNECT failure. "
                    "NOT evidence of listing status on the exchange.",
        }
        return rec, job

    sha = hashlib.sha256(body).hexdigest()
    text = body.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    rec["response"] = {
        "http_status": status,
        "headers": {
            "server": hdrs.get("server"),
            "content_type": hdrs.get("content-type"),
            "date": hdrs.get("date"),
            "x_mbx_uuid": hdrs.get("x-mbx-uuid"),
            "x_mbx_used_weight": hdrs.get("x-mbx-used-weight"),
            "x_mbx_used_weight_1m": hdrs.get("x-mbx-used-weight-1m"),
            "cf_ray": hdrs.get("cf-ray"),
            "cf_mitigated": hdrs.get("cf-mitigated"),
            "retry_after": hdrs.get("retry-after"),
            "cache_control": hdrs.get("cache-control"),
            "via": hdrs.get("via"),
        },
        "body_len_bytes": len(body),
        "body_sha256": sha,
        "body_sha256_short16": sha[:16],
        "body_excerpt_500": text[:500],
        "is_json": parsed is not None,
    }
    d = rec["derived"]
    d["parsed_summary"] = parse_summary(name, parsed) if parsed is not None else {}
    d["ok"] = classify(status, parsed) == "OK"
    d["classification"] = classify(status, parsed)
    d["latency_s"] = round(time.time() - t0, 3)
    d["is_cloudflare_html_block"] = bool(
        parsed is None and "cloudflare" in text.lower() and status in (403, 503)
    )
    if name == "time" and isinstance(parsed, dict) and isinstance(parsed.get("serverTime"), int):
        # approximate skew: includes ~RTT/2; good enough to detect gross clock drift
        d["clock_skew_ms"] = parsed["serverTime"] - req_local_ms
    return rec, job


def network_control():
    """Short direct (no-proxy) control probes to localise the failure layer."""
    out = {"probe_schema": "jev-v15-network-control/1.0", "ts_utc": now_iso(), "proxy_env": PROXY, "controls": []}
    for url in ["https://example.com", "https://api.binance.us/api/v3/time",
                "https://data-api.binance.vision/api/v3/time"]:
        for use_proxy in (False, True):
            ph = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY} if (use_proxy and PROXY) else {})
            op = urllib.request.build_opener(ph)
            t0 = time.time()
            item = {"url": url, "via_proxy": use_proxy}
            try:
                r = op.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=8)
                b = r.read()
                item.update(http_status=r.status, server=r.headers.get("server"),
                            content_type=r.headers.get("content-type"),
                            body_sha256_short16=hashlib.sha256(b).hexdigest()[:16],
                            body_excerpt_120=b[:120].decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                item.update(http_status=e.code, server=e.headers.get("server"),
                            error=f"HTTPError {e.code}")
            except Exception as e:
                item.update(error=f"{type(e).__name__}: {e}")
            item["latency_s"] = round(time.time() - t0, 3)
            out["controls"].append(item)
    return out


def main():
    started = now_iso()
    jobs = []
    for sk, src in SOURCES.items():
        jobs.append((sk, src, "SERVER", "time"))
        for sym in SYMBOLS:
            for name in ENDPOINT_NAMES:
                if name == "time":
                    continue
                jobs.append((sk, src, sym, name))

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(probe, j) for j in jobs]
        for f in as_completed(futs):
            rec, job = f.result()
            results.append(rec)
            sk, src, sym, name = job
            fn = f"{sk}__{sym}__{name}.json"
            with open(os.path.join(OUT, fn), "w", encoding="utf-8") as fh:
                json.dump(rec, fh, ensure_ascii=False, indent=2)

    # deterministic index
    results.sort(key=lambda r: (r["source_key"], r["symbol"], r["endpoint"]))
    index_rows = []
    for r in results:
        d = r["derived"]
        ps = d.get("parsed_summary", {})
        index_rows.append({
            "evidence_file": f"{r['source_key']}__{r['symbol']}__{r['endpoint']}.json",
            "exchange": r["exchange"],
            "market": r["market"],
            "symbol": r["symbol"],
            "endpoint": r["endpoint"],
            "http_status": (r["response"] or {}).get("http_status"),
            "server": ((r["response"] or {}).get("headers") or {}).get("server"),
            "body_sha256_short16": (r["response"] or {}).get("body_sha256_short16"),
            "classification": d.get("classification"),
            "api_error_code": ps.get("api_error_code"),
            "key_fields": {k: ps[k] for k in ps if k in (
                "status", "baseAsset", "quoteAsset", "price", "bidPrice", "askPrice",
                "last_trade_price", "last_trade_time_ms", "serverTime", "api_error_msg",
                "clock_skew_ms") and ps[k] is not None},
        })
    index = {
        "probe_schema": "jev-v15-market-index/1.0",
        "generated_ts_utc": started,
        "finished_ts_utc": now_iso(),
        "environment": {
            "python": sys.version.split()[0],
            "proxy": PROXY,
            "http_proxy": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "",
            "no_proxy": os.environ.get("NO_PROXY") or "",
            "credentials_used": False,
            "remote_writes": False,
        },
        "sources": SOURCES,
        "symbols": SYMBOLS,
        "total_requests": len(results),
        "ok_count": sum(1 for x in index_rows if x["classification"] == "OK"),
        "transport_failure_count": sum(1 for x in index_rows if x["classification"] == "TRANSPORT_FAILURE"),
        "api_error_count": sum(1 for x in index_rows if x["classification"] == "API_ERROR"),
        "rows": index_rows,
    }
    with open(os.path.join(OUT, "INDEX.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "NETWORK_CONTROL.json"), "w", encoding="utf-8") as fh:
        json.dump(network_control(), fh, ensure_ascii=False, indent=2)

    # concise stdout summary
    print(f"total={len(results)} ok={index['ok_count']} "
          f"transport_failure={index['transport_failure_count']} api_error={index['api_error_count']}")
    for row in index_rows:
        print(f"  {row['exchange'][:34]:34s} | {row['symbol']:14s} | {row['endpoint']:13s} | "
              f"http={row['http_status']} | {row['classification']:18s} | "
              f"err={row['api_error_code']} | sha16={row['body_sha256_short16']}")


if __name__ == "__main__":
    main()
