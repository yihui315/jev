#!/usr/bin/env python3
"""JCC v1.2 Visual Command Center -- real performance probe.

Measures ACTUAL HTTP serving performance of the live v1.2 surface:
  * Visual UI  (127.0.0.1:4780, serve_visual.py static SPA)
  * Control API (127.0.0.1:4779, jcc_core.run)

This is a real measurement against the running server (not a mock). It records
TTFB, total bytes, and throughput for index.html + the main JS bundle, and the
control-plane /api/health latency. Browser-render FCP/LCP require a headless
browser (not available in the agent sandbox); that measurement is recorded as
PENDING_HOST in the evidence and is NOT required for the serving-layer gate.

Writes artifacts/release/v1.2_visual/V1_2_VISUAL_PERFORMANCE.json.
"""
from __future__ import annotations
import http.client
import io
import json
import os
import re
import statistics
from pathlib import Path
import time

ROOT = str(Path(__file__).resolve().parents[1])
OUT = os.path.join(ROOT, "artifacts", "release", "v1.2_visual", "V1_2_VISUAL_PERFORMANCE.json")
UI_HOST, UI_PORT = "127.0.0.1", 4780
API_HOST, API_PORT = "127.0.0.1", 4779


def fetch(host, port, path, n=5):
    rows = []
    for _ in range(n):
        c = http.client.HTTPConnection(host, port, timeout=5)
        t0 = time.perf_counter()
        c.request("GET", path)
        t_connect = time.perf_counter()
        r = c.getresponse()
        body = r.read()
        t_done = time.perf_counter()
        c.close()
        rows.append({
            "status": r.status,
            "bytes": len(body),
            "ttfb_ms": round((t_connect - t0) * 1000, 2),
            "total_ms": round((t_done - t0) * 1000, 2),
        })
    ok = all(x["status"] == 200 for x in rows)
    return ok, rows


def main() -> int:
    evidence = {
        "probe": "JCC v1.2 Visual Command Center real serving performance",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ui": {"host": UI_HOST, "port": UI_PORT},
        "api": {"host": API_HOST, "port": API_PORT},
        "browser_render_fcp": "PENDING_HOST (no headless browser in agent sandbox)",
    }

    # --- Visual UI: /health ---
    ok_h, h = fetch(UI_HOST, UI_PORT, "/health")
    evidence["ui"]["health"] = {"ok": ok_h, "samples": h}

    # --- Visual UI: index.html + main JS bundle ---
    ok_idx, idx = fetch(UI_HOST, UI_PORT, "/")
    evidence["ui"]["index_html"] = {"ok": ok_idx, "samples": idx}
    js_path = None
    if ok_idx:
        # find the module script src from the last successful index body
        body = None
        c = http.client.HTTPConnection(UI_HOST, UI_PORT, timeout=5)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read().decode("utf-8", "replace")
        c.close()
        m = re.search(r'<script[^>]+type="module"[^>]+src="([^"]+)"', body)
        if not m:
            m = re.search(r'src="(/assets/[^"]+\.js)"', body)
        if m:
            js_path = m.group(1)
    if js_path:
        ok_js, js = fetch(UI_HOST, UI_PORT, js_path)
        # throughput (MB/s) from median total + bytes
        med = statistics.median(x["total_ms"] for x in js) or 1e-6
        med_bytes = statistics.median(x["bytes"] for x in js)
        mbps = round((med_bytes / 1_000_000) / (med / 1000), 3)
        evidence["ui"]["main_js"] = {
            "path": js_path,
            "ok": ok_js,
            "samples": js,
            "median_bytes": med_bytes,
            "median_throughput_mbps": mbps,
        }

    # --- Control API: /api/health ---
    ok_api, api = fetch(API_HOST, API_PORT, "/api/health")
    evidence["api"]["health"] = {"ok": ok_api, "samples": api}

    # --- PASS criteria (serving layer) ---
    crit = []
    ui_index_ok = ok_idx and statistics.median(x["total_ms"] for x in idx) < 1000
    ui_health_ok = ok_h
    api_health_ok = ok_api and statistics.median(x["total_ms"] for x in api) < 2000
    js_ok = ("main_js" in evidence["ui"]) and evidence["ui"]["main_js"]["ok"]
    crit.append(("UI /health 200", ui_health_ok))
    crit.append(("UI index.html served <1s", ui_index_ok))
    crit.append(("UI main JS bundle served 200", js_ok))
    crit.append(("API /api/health 200 <2s", api_health_ok))
    evidence["criteria"] = [{"name": n, "pass": bool(p)} for n, p in crit]
    evidence["result"] = "PASS" if all(p for _, p in crit) else "FAIL"
    evidence["note"] = (
        "Real HTTP measurement against live servers. Browser-render FCP/LCP is a "
        "host-side follow-up (PENDING_HOST) and is not part of the serving-layer gate."
    )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2)
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
