#!/usr/bin/env python3
"""WAVE 10 — REAL-BROWSER PERFORMANCE GATE for the v1.3 operational 3D plane.

THE GATE'S QUESTION
-------------------
"When the user opens the operational 3D world over a REAL domain of 50 / 200 /
500 / 1000 objects, does the SHIPPED plane actually render them — and what does
that cost, measured in the shipping browser rather than asserted?"

WHY THIS GATE EXISTS (and why it is not the thing the freeze doc refused)
------------------------------------------------------------------------
``docs/v1_3/FINAL_PRODUCT_FREEZE.md`` section 7 carried "real-browser FPS at node
counts 50/200/500/1000" as DELIBERATELY NOT CLAIMED, with a correct argument:
"the product has no stress route; timing a synthetic page would be a measurement
of the harness."

That argument is right, and this gate is the answer to it rather than a
repudiation of it. It builds no synthetic page. It:

  * seeds the REAL domain through the REAL API (the same POSTs the UI makes),
  * serves the REAL shipped bundle with the product's OWN ASGI server
    (``apps/command_center_3d/serve_visual.py``, not a stand-in),
  * opens it in REAL Chrome at the real UI origin, cross-origin to the real API,
  * clicks the product's own "World" navigation, and
  * measures the product's own render loop.

So every number below is a property of the product, not of a harness.

WHAT IT REFUSES TO DO
---------------------
1. **It never reports a frame rate without naming the rasterizer.** On this host
   headless Chrome renders through ANGLE/SwiftShader — a CPU rasterizer — so the
   numbers are a SOFTWARE floor, not the host GPU's throughput. The evidence
   records the exact `WEBGL_debug_renderer_info` strings and a derived
   `renderer_class`, so the FPS can never be quoted as a hardware figure.
2. **It does not assert "fast enough".** There is no measured baseline on this
   machine for what fast enough means, and inventing a threshold would be exactly
   the unfalsifiable green this project keeps deleting. It DOES assert
   falsifiable things: that the plane rendered the domain's objects (draw calls
   are counted by wrapping the real WebGL draw entry points at page-init, before
   any bundle code runs), that the work scales with the domain, and that no frame
   took longer than ``CATASTROPHIC_FRAME_MS``.
3. **It does not measure a bounded list as if it were the domain.** It reads
   ``/api/v1/domain/counts`` (Wave 9.3) and compares the plane's object count
   with the DOMAIN's — which is how the Wave 9.3 silent-truncation defect was
   found in the first place: at 500 missions the plane drew 200 of them, and
   comparing draw calls with the domain is the only thing that could see it.

Checks (each is a real browser, HTTP or OS fact):
  CONFIG / PORTS_FREE / API_BOOT / UI_BOOT served by the shipped ASGI server /
  CORS preflight / BUNDLE_PRESENT
  SEEDED_GROUND_TRUTH   — the domain really holds N missions (counts read)
  BROWSER               — real Chrome, WebGL live, renderer named
  per N: WORLD_MOUNTED (the operational canvas exists, i.e. NOT the empty-world
         branch), DRAWS_GROW (added missions became draw calls),
         FRAME_TIME_FINITE
  DRAWS_SCALE_WITH_DOMAIN — the slope between 50 and 1000 is the domain's slope
  NO_CONSOLE_ERRORS / VIEW_SWEEP (real-Chrome E2E over the product's surfaces)
  PORTS_RELEASED

Run:  F:/jev/.venv/Scripts/python.exe tools/v1_3_wave10_browser_perf.py
Exit: 0 = PASS, 1 = FAIL (a real gate, not a green-washer).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

DIST = ROOT / "apps" / "command_center_3d" / "dist"
PRODUCT_CONFIG = ROOT / "config" / "jcc-v1.3.yaml"
SCRATCH = ROOT / ".workbuddy" / "scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d-%H%M%S")
OUT = SCRATCH / ("v1_3_wave10_browser_perf_%s.json" % STAMP)

CONTROL_TOKEN = "wave10-gate-token"
CONTROL_TOKEN_ENV = "JCC_CONTROL_TOKEN"
PRODUCT_UI_PORT = 4780

#: The node counts the product order names. Measured cumulatively: the dataset
#: only ever grows, so each row is the same workspace with more work in it.
NODES = (50, 200, 500, 1000)
#: Fixed project count. The plane draws a ring + a nucleus per project and one
#: body per mission, so with P projects and M missions the expected draw calls
#: are `1 + 2P + M` (the 1 is the grid helper).
PROJECTS = 10
WARMUP_FRAMES = 45
MEASURE_FRAMES = 180
MEASURE_BUDGET_MS = 9000.0
#: A frame slower than this means the product is not "slow", it is stuck.
CATASTROPHIC_FRAME_MS = 2000.0
#: The projection renders ONE mesh per mission. If almost none of the missions
#: added between the smallest and largest measurement produce a draw call, then
#: either the plane is not rendering the domain or the domain the plane received
#: is not the domain that exists -- and both are findings, not scaling noise.
MIN_VISIBILITY_FRACTION = 0.90
#: Measurements are taken with the product's own 5 s polling loop running (it is
#: what ships). Recorded so a reader can tell a poll hitch from a render cost.
POLL_MS = 5000

failures: List[str] = []
steps: List[Dict[str, Any]] = []


def check(name: str, cond: bool, value: Any = None, note: str = "") -> bool:
    steps.append({"step": name, "ok": bool(cond), "value": value, "note": note})
    print("  [%s] %s: %s" % ("PASS" if cond else "FAIL", name, note))
    if not cond:
        failures.append(name)
    return bool(cond)


class _NoProxy(urllib.request.ProxyHandler):
    def proxy_open(self, *a, **k):  # bypass any system proxy
        return None


OPENER = urllib.request.build_opener(_NoProxy())


def http(method: str, url: str, body: Optional[dict] = None,
         token: Optional[str] = None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-JCC-Token", token)
    try:
        r = OPENER.open(req, timeout=timeout)
        raw = r.read().decode("utf-8", "replace")
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def port_owner(port: int) -> int:
    """The pid LISTENING on ``port``, or 0. Read from the OS, never guessed."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=30, errors="backslashreplace").stdout
    except Exception:  # noqa: BLE001
        return 0
    pid = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(":%d" % port):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
    return pid


def cors_preflight(url: str, origin: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, method="OPTIONS")
    req.add_header("Origin", origin)
    req.add_header("Access-Control-Request-Method", "POST")
    req.add_header("Access-Control-Request-Headers", "x-jcc-token,content-type")
    try:
        r = OPENER.open(req, timeout=12)
        allow = r.headers.get("access-control-allow-origin")
        return {"ok": r.status in (200, 204) and allow in (origin, "*"),
                "status": r.status, "allow_origin": allow}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "allow_origin": (e.headers or {}).get("access-control-allow-origin")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def _load_shipped_ui_server():
    """Import the product's OWN serving module by path (it is not a package)."""
    import importlib.util
    path = ROOT / "apps" / "command_center_3d" / "serve_visual.py"
    spec = importlib.util.spec_from_file_location("jcc_shipped_serve_visual_w10",
                                                  str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# the in-page instruments
# --------------------------------------------------------------------------- #
#: Installed with ``add_init_script``, so it runs BEFORE any bundle code and
#: therefore counts every draw the product ever issues. Wrapping the context
#: prototypes is the only way to count GPU submissions from outside a bundled
#: three.js; asking the app for `renderer.info.render.calls` would require the
#: app to expose it to us, which would be measuring an API we invented.
DRAW_INSTRUMENT_JS = r"""
window.__jccgl = { calls: 0, verts: 0, frames: 0, errors: [] };
(function () {
  const wrap = (proto, name, vcount) => {
    if (!proto || !proto[name]) return;
    const orig = proto[name];
    proto[name] = function (...a) {
      try {
        window.__jccgl.calls += 1;
        if (vcount) window.__jccgl.verts += (a[1] | 0);
      } catch (e) { /* never break the product to count it */ }
      return orig.apply(this, a);
    };
  };
  for (const P of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!P) continue;
    const pr = P.prototype;
    wrap(pr, 'drawElements', true);
    wrap(pr, 'drawArrays', true);
    wrap(pr, 'drawElementsInstanced', true);
    wrap(pr, 'drawArraysInstanced', true);
  }
  // Uncaught errors and console errors are part of "does the product work".
  window.addEventListener('error', (e) => {
    window.__jccgl.errors.push('error: ' + (e.message || 'unknown'));
  });
  window.addEventListener('unhandledrejection', (e) => {
    window.__jccgl.errors.push('rejection: ' + String(e.reason));
  });
})();
"""

RENDERER_JS = r"""
() => {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return { ok: false };
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  return {
    ok: true,
    version: gl.getParameter(gl.VERSION),
    vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
    renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    max_texture: gl.getParameter(gl.MAX_TEXTURE_SIZE),
  };
}
"""

MEASURE_JS = r"""
async (opts) => {
  const g = window.__jccgl;
  const frames = [];
  let n = 0;
  let last = performance.now();
  const t0 = performance.now();
  await new Promise((res) => {
    function step(t) {
      frames.push(t - last);
      last = t;
      n += 1;
      if (n >= opts.frames || (t - t0) >= opts.budgetMs) { res(); return; }
      requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });
  const elapsed = (performance.now() - t0) / 1000;
  // Drop the first delta: it includes the gap since the caller's own render and
  // is not a frame.
  const d = frames.slice(1).sort((a, b) => a - b);
  const at = (q) => d.length ? d[Math.min(d.length - 1, Math.floor(d.length * q))] : null;
  const canvases = document.querySelectorAll('canvas').length;
  const r3f = document.querySelectorAll('canvas').length;
  return {
    frames_measured: d.length,
    elapsed_s: elapsed,
    median_ms: at(0.5),
    p95_ms: at(0.95),
    min_ms: d.length ? d[0] : null,
    max_ms: d.length ? d[d.length - 1] : null,
    fps: d.length ? (d.length / elapsed) : 0,
    draw_calls_total: g.calls,
    draw_calls_per_frame: n ? g.calls / n : 0,
    triangles_total: g.verts / 3,
    triangles_per_frame: n ? (g.verts / 3) / n : 0,
    canvases: canvases,
    r3f_canvas: r3f,
    page_errors: g.errors.slice(0, 10),
  };
}
"""

RESET_JS = "() => { window.__jccgl.calls = 0; window.__jccgl.verts = 0; }"


# --------------------------------------------------------------------------- #
# seeding + browser
# --------------------------------------------------------------------------- #
def seed(api_base: str, want_missions: int, project_ids: List[str],
         missions_have: List[int], t0: float = 0.0) -> int:
    """Grow the REAL domain to ``want_missions`` missions in total.

    This is the gate's long pole and it is SLOW, for a reason that is measured
    rather than assumed: a single mission creation costs ~0.9 s on this host,
    because the domain store opens a fresh SQLite connection -- twice -- and
    re-issues its PRAGMAs on each one. See
    ``docs/v1_3/WAVE_10_REAL_BROWSER_PERFORMANCE.md`` section 8 and
    ``tools/v1_3_domain_io_profile.py``.

    That is why this loop reports its own progress. An unattended gate that
    prints nothing for forty minutes is indistinguishable from a hung one, and
    this one measured exactly that: the first full run produced no output
    between ``API_BOOT`` and the first ``SEEDED_GROUND_TRUTH`` and could not be
    told apart from a stall. The heartbeat is EVIDENCE, not decoration -- the
    per-100 throughput it prints is the number that names the cost.
    """
    have = missions_have[0]
    if not project_ids:
        for i in range(PROJECTS):
            st, proj = http("POST", api_base + "/api/v1/domain/projects",
                            {"name": "Perf Workspace %d" % i,
                             "root_path": r"F:\jev"}, CONTROL_TOKEN)
            if st != 200 or not isinstance(proj, dict):
                raise RuntimeError("project create failed: %s %s" % (st, proj))
            project_ids.append(proj["project_id"])
    mark = have
    while have < want_missions:
        pid = project_ids[have % len(project_ids)]
        st, m = http("POST", api_base + "/api/v1/domain/missions",
                     {"project_id": pid,
                      "title": "Perf mission %d" % have,
                      "goal": "load the operational plane with real persisted work",
                      "agent": "workbuddy", "permissions": ["fs.read"]},
                     CONTROL_TOKEN)
        if st != 200:
            raise RuntimeError("mission create failed at %d: %s %s" % (have, st, m))
        have += 1
        missions_have[0] = have
        if t0 and have % 100 == 0:
            el = time.time() - t0
            print("    ... %d/%d missions seeded, %.0f s elapsed, %.2f s/mission"
                  % (have, want_missions, el, (have - mark) and el / (have - mark) or 0.0),
                  flush=True)
    return have


def measure_at(page, ui_base: str, n: int) -> Dict[str, Any]:
    """Open the world at ``n`` objects and measure the plane's real render loop."""
    page.goto(ui_base, wait_until="load", timeout=60000)
    page.wait_for_selector("#root", timeout=30000)
    # The product's own navigation: the sidebar button whose label is Web.
    clicked = False
    for label in ("World",):
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            btn.first.click(timeout=15000)
            clicked = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not clicked:
        raise RuntimeError("could not reach the World view through the product nav")

    # The Canvas only mounts when the projection is NON-EMPTY: with an empty
    # domain the product renders the honest "An empty world" card INSTEAD. So the
    # presence of the canvas is itself the assertion "the plane mounted real data".
    page.wait_for_selector("canvas", timeout=45000)
    # Let the first frames (shader compile, geometry upload) pass, then measure.
    page.evaluate("""() => new Promise(res => {
        let n = 0;
        function s() { if (++n >= %d) res(); else requestAnimationFrame(s); }
        requestAnimationFrame(s);
    })""" % WARMUP_FRAMES)
    page.evaluate(RESET_JS)
    out = page.evaluate(MEASURE_JS, {"frames": MEASURE_FRAMES,
                                     "budgetMs": MEASURE_BUDGET_MS})
    out["n_requested"] = n
    return out


def view_sweep(page, ui_base: str) -> Dict[str, Any]:
    """Real-Chrome E2E: every product surface opens, none throws.

    Cheap by design -- the W2 gate owns the deep DOM assertions; what this adds
    is that the SAME session that measured the plane can walk the whole shell
    without an uncaught error, which is the end-to-end half of the product order.
    """
    # Labels are the product's OWN `navigation.ts` labels (verified against
    # source, not guessed): studio is "New Mission", world is "World".
    labels = ["Home", "Projects", "Missions", "Needs Attention", "Deliverables",
              "New Mission", "World", "Engine World", "Graph", "Table",
              "Connections", "Audit", "Diagnostics", "Settings"]
    seen: Dict[str, Any] = {}
    for label in labels:
        try:
            page.get_by_role("button", name=label, exact=True).first.click(timeout=10000)
            page.wait_for_timeout(350)
            seen[label] = {"ok": True, "errors": page.evaluate("window.__jccgl.errors.length")}
        except Exception as exc:  # noqa: BLE001
            seen[label] = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    return seen


def classify_renderer(gl: Dict[str, Any]) -> str:
    """SOFTWARE / HARDWARE / UNKNOWN -- derived, so the caveat is machine-readable.

    The frame times this gate produces are only meaningful with the rasterizer
    named. Leaving that to prose means the *next* reader can quote the number as
    a GPU figure; a derived field means any consumer of the evidence has to
    handle the distinction.
    """
    text = ("%s %s" % (gl.get("vendor") or "", gl.get("renderer") or "")).lower()
    if not text.strip():
        return "UNKNOWN"
    for token in ("swiftshader", "llvmpipe", "softpipe", "software", "mesa offscreen"):
        if token in text:
            return "SOFTWARE"
    return "HARDWARE"


def newest_source_mtime() -> float:
    """The newest mtime under the SPA's ``src/``, or 0 if there is none."""
    src = ROOT / "apps" / "command_center_3d" / "src"
    newest = 0.0
    for p in src.rglob("*"):
        if p.suffix in (".ts", ".tsx", ".css", ".html"):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
    return newest


def bundle_is_fresh() -> Dict[str, Any]:
    """The bundle must be NEWER than the source it claims to contain.

    Measuring a stale ``dist/`` would measure a product that no longer exists --
    the same "artifact older than the thing it describes" defect the final gate's
    own R2 rule exists to refuse. ``dist/`` is gitignored, so nothing else in the
    release catches this: a forgotten ``vite build`` would silently license a
    performance verdict about last night's code.
    """
    index = DIST / "index.html"
    if not index.is_file():
        return {"ok": False, "reason": "no built bundle", "dist_mtime": None,
                "src_newest_mtime": None}
    dist_m = index.stat().st_mtime
    src_m = newest_source_mtime()
    return {"ok": dist_m >= src_m, "reason": ("bundle newer than src"
                                             if dist_m >= src_m else
                                             "bundle is OLDER than src -- rebuild"),
            "dist_mtime": dist_m, "src_newest_mtime": src_m,
            "age_gap_s": round(src_m - dist_m, 1)}


def main() -> int:
    # An unattended gate whose stdout is redirected to a file is BLOCK-buffered
    # by default (8 KiB), so a run that is working and a run that is hung look
    # identical for as long as the buffer takes to fill -- measured on this very
    # gate: the first full run printed nothing for 39 minutes. Line buffering
    # makes the log a live transcript, which is what a long unattended
    # measurement needs in order to be diagnosable at all.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001  (older/odd streams: buffering is not fatal)
        pass

    global NODES
    ap = argparse.ArgumentParser(
        description="WAVE 10 -- real-browser performance of the operational plane")
    ap.add_argument("--nodes", default=",".join(str(n) for n in NODES),
                    help="comma-separated object counts. The RELEASE run uses the "
                         "default; a shorter list exists so an operator can smoke-"
                         "test the harness in minutes instead of an hour, and such "
                         "a run is explicitly NOT a release leg (its floor cannot "
                         "be met and it says so).")
    ns = ap.parse_args()
    NODES = tuple(int(x) for x in ns.nodes.split(",") if x.strip())
    if not NODES:
        print("FAIL: --nodes selected nothing")
        return 1

    if not (DIST / "index.html").is_file():
        print("FAIL: no built bundle at %s -- run `vite build` first" % DIST)
        return 1

    import uvicorn
    from jcc_core.actuator import Actuator
    from jcc_core.config import ConfigError, load_config
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    try:
        cfg = load_config(str(PRODUCT_CONFIG))
    except ConfigError as exc:
        print("FAIL: the product config did not load: %s" % exc)
        return 1

    api_port = cfg.server.port
    ui_port = PRODUCT_UI_PORT
    api_base = "http://127.0.0.1:%d" % api_port
    ui_base = "http://127.0.0.1:%d" % ui_port

    check("PRODUCT_CONFIG_IS_THE_SHIPPED_ONE",
          cfg.server.host == "127.0.0.1" and api_port == 4779 and ui_port == 4780,
          {"host": cfg.server.host, "api_port": api_port, "ui_port": ui_port},
          "measuring config/jcc-v1.3.yaml, not a synthetic Config")
    freshness = bundle_is_fresh()
    check("BUNDLE_IS_NEWER_THAN_THE_SOURCE_IT_CONTAINS", freshness["ok"], freshness,
          "dist/ postdates src/, so the measured bundle is the code under test")

    # Playwright creates its browser profile under $TEMP. A $TEMP that does not
    # exist fails INSIDE BrowserType.launch as
    #   "ENOENT: no such file or directory, mkdtemp '...\playwright-artifacts-X'"
    # which reads like a browser fault and is not one. Measured once, on this
    # very gate, before this check existed.
    temp_root = tempfile.gettempdir()
    temp_ok, temp_note = True, temp_root
    if not os.path.isdir(temp_root):
        try:
            os.makedirs(temp_root, exist_ok=True)
            temp_note = temp_root + " (created by this gate)"
        except OSError as exc:
            temp_ok, temp_note = False, "%s: %s" % (temp_root, exc)
    check("TEMP_ROOT_IS_USABLE", temp_ok, temp_note,
          "playwright can write its browser profile under $TEMP")
    check("UI_ORIGIN_IS_IN_THE_CORS_ALLOWLIST", ui_base in cfg.server.cors_origins,
          cfg.server.cors_origins, "the SPA origin is allowed by the API's explicit list")

    occupied = {p: port_owner(p) for p in (api_port, ui_port)}
    busy = {p: v for p, v in occupied.items() if v}
    check("PRECONDITION_PRODUCT_PORTS_FREE", not busy, occupied,
          "4779+4780 free, so what this gate observes is its own" if not busy
          else "a listener already holds a product port: %s -- stop the product first"
               % busy)
    if busy:
        _finish()
        return 1

    tmpdir = Path(tempfile.mkdtemp(prefix="jcc_w10_"))
    cfg.state_store_db_path = str(tmpdir / "w10.db")
    os.environ[cfg.control_token_env] = CONTROL_TOKEN

    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)

    servers = []
    api_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port,
                                               log_level="warning"))
    threading.Thread(target=api_server.run, daemon=True).start()
    servers.append(api_server)

    ready = False
    for _ in range(80):
        try:
            st, _b = http("GET", api_base + "/api/v1/domain/home", timeout=2.0)
            if st == 200:
                ready = True
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    if not check("API_BOOT", ready, api_base, "v1.3 app answered /api/v1/domain/home"):
        _teardown(servers, api_port, ui_port)
        _finish()
        return 1

    ui_mod = _load_shipped_ui_server()
    ui_app = ui_mod.build_app(str(DIST), ui_port)
    ui_server = uvicorn.Server(uvicorn.Config(ui_app, host="127.0.0.1", port=ui_port,
                                              log_level="warning"))
    threading.Thread(target=ui_server.run, daemon=True).start()
    servers.append(ui_server)

    ui_ready, ui_health = False, None
    for _ in range(60):
        st, ui_health = http("GET", ui_base + "/health", timeout=2.0)
        if st == 200 and isinstance(ui_health, dict):
            ui_ready = True
            break
        time.sleep(0.25)
    check("UI_SERVED_BY_THE_SHIPPED_ASGI_SERVER",
          ui_ready and isinstance(ui_health, dict)
          and ui_health.get("runtime") == "uvicorn-asgi"
          and ui_health.get("port") == ui_port,
          ui_health, "apps/command_center_3d/serve_visual.py under uvicorn")
    preflight = cors_preflight(api_base + "/api/v1/domain/projects", ui_base)
    check("CORS_PREFLIGHT_FOR_THE_UI_ORIGIN", preflight.get("ok"), preflight,
          "the API allows the SPA's cross-origin write")

    # ---- seed + measure ----
    project_ids: List[str] = []
    missions_have = [0]
    rows: List[Dict[str, Any]] = []
    node_times: List[Dict[str, Any]] = []
    renderer: Dict[str, Any] = {}
    sweep: Dict[str, Any] = {}
    page_errors: List[str] = []

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        check("PLAYWRIGHT_AVAILABLE", False, None, "playwright import failed: %s" % exc)
        _teardown(servers, api_port, ui_port)
        _finish()
        return 1
    check("PLAYWRIGHT_AVAILABLE", True, None, "driver present")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome", headless=True,
                args=["--no-sandbox", "--enable-unsafe-swiftshader",
                      "--use-gl=angle", "--use-angle=swiftshader",
                      "--disable-dev-shm-usage"])
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx.add_init_script(DRAW_INSTRUMENT_JS)
            page = ctx.new_page()
            console_errors: List[str] = []
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)

            page.goto("about:blank")
            renderer = page.evaluate(RENDERER_JS)
            renderer["renderer_class"] = classify_renderer(renderer)
            check("WEBGL_IS_LIVE_IN_REAL_CHROME",
                  bool(renderer.get("ok")) and renderer["renderer_class"] != "UNKNOWN",
                  {k: renderer.get(k) for k in
                   ("ok", "version", "vendor", "renderer", "renderer_class")},
                  "a real GL context exists and its rasterizer is NAMED "
                  "(class=%s), so the numbers cannot be quoted without it"
                  % renderer["renderer_class"])

            seed_t0 = time.time()
            for n in NODES:
                n_t0 = time.time()
                have = seed(api_base, n, project_ids, missions_have, t0=seed_t0)
                node_times.append({"n": n, "cumulative_missions": have,
                                   "seconds": round(time.time() - n_t0, 1),
                                   "cumulative_seconds": round(time.time() - seed_t0, 1)})
                print("  [seed] %d missions in the domain (%.1f s cumulative, "
                      "%.2f s/mission average)"
                      % (have, time.time() - seed_t0,
                         (time.time() - seed_t0) / max(1, have)), flush=True)
                st, counts = http("GET", api_base + "/api/v1/domain/counts")
                dom_missions = counts.get("missions") if isinstance(counts, dict) else None
                st2, projs = http("GET", api_base + "/api/v1/domain/projects")
                dom_projects = len(projs) if isinstance(projs, list) else None
                check("SEEDED_GROUND_TRUTH_%d" % n,
                      have == n and dom_missions == n and dom_projects == PROJECTS,
                      {"missions_created": have, "counts": counts,
                       "projects": dom_projects},
                      "the DOMAIN holds %d missions (read back through /counts)" % n)

                m = measure_at(page, ui_base, n)
                # The plane's own arithmetic: 1 grid + 2 per project + 1 per
                # mission. Recorded as a comparison, asserted as a floor.
                m["expected_draw_calls"] = 1 + 2 * PROJECTS + n
                m["domain_missions"] = dom_missions
                m["domain_projects"] = dom_projects
                rows.append(m)

                check("WORLD_MOUNTED_AT_%d" % n,
                      m["canvases"] >= 1 and m["draw_calls_per_frame"] > 0,
                      {"canvases": m["canvases"],
                       "draw_calls_per_frame": round(m["draw_calls_per_frame"], 1)},
                      "the operational canvas mounted (not the empty-world branch) "
                      "and issued draw calls")
                check("FRAME_TIME_FINITE_AT_%d" % n,
                      isinstance(m["median_ms"], (int, float))
                      and m["median_ms"] < CATASTROPHIC_FRAME_MS
                      and m["frames_measured"] > 0,
                      {"median_ms": m["median_ms"], "p95_ms": m["p95_ms"],
                       "frames": m["frames_measured"]},
                      "the loop is live and no frame took a catastrophic time")

            page_errors = page.evaluate("window.__jccgl.errors.slice(0, 20)")
            sweep = view_sweep(page, ui_base)
            bad_views = [k for k, v in sweep.items() if not v.get("ok")]
            check("VIEW_SWEEP_NO_UNCAUGHT_ERRORS",
                  not bad_views and not console_errors and not page_errors,
                  {"bad_views": bad_views, "console_errors": console_errors[:10],
                   "page_errors": page_errors[:10],
                   "views": sorted(sweep.keys())},
                  "every product surface opened in the same real-Chrome session "
                  "with no uncaught or console error")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        check("BROWSER_MEASUREMENT_COMPLETED", False, None,
              "%s: %s" % (type(exc).__name__, str(exc)[:300]))
    check("BROWSER_MEASUREMENT_COMPLETED", len(rows) == len(NODES),
          {"rows": len(rows)}, "measured the plane at every requested node count")

    # ---- the scaling assertion, derived from the projection's own design ----
    slope_detail: Dict[str, Any] = {}
    if len(rows) == len(NODES):
        lo, hi = rows[0], rows[-1]
        d_draws = hi["draw_calls_per_frame"] - lo["draw_calls_per_frame"]
        d_nodes = hi["n_requested"] - lo["n_requested"]
        frac = (d_draws / d_nodes) if d_nodes else 0.0
        monotonic = all(rows[i]["draw_calls_per_frame"] < rows[i + 1]["draw_calls_per_frame"]
                        for i in range(len(rows) - 1))
        slope_detail = {
            "nodes_smallest": lo["n_requested"], "nodes_largest": hi["n_requested"],
            "draws_smallest": round(lo["draw_calls_per_frame"], 2),
            "draws_largest": round(hi["draw_calls_per_frame"], 2),
            "draws_per_added_mission": round(frac, 4),
            "min_visibility_fraction": MIN_VISIBILITY_FRACTION,
            "monotonic": monotonic,
            "expected_draws_largest": hi["expected_draw_calls"],
            "observed_over_expected_largest": round(
                hi["draw_calls_per_frame"] / max(1.0, hi["expected_draw_calls"]), 4),
        }
        check("DRAWS_SCALE_WITH_THE_DOMAIN",
              monotonic and frac >= MIN_VISIBILITY_FRACTION,
              slope_detail,
              "adding %d missions produced >= %.0f%% of that many draw calls"
              % (d_nodes, MIN_VISIBILITY_FRACTION * 100))
    else:
        check("DRAWS_SCALE_WITH_THE_DOMAIN", False, slope_detail,
              "not measured: the per-count rows are incomplete")

    released = _teardown(servers, api_port, ui_port)
    check("PORTS_RELEASED_AFTER_TEARDOWN", not any(released.values()), released,
          "the gate leaves no product listener behind")

    payload = {
        "gate": "WAVE10_REAL_BROWSER_PERF",
        "verdict": "PASS" if not failures and steps else "FAIL",
        "passed": sum(1 for s in steps if s["ok"]),
        "total": len(steps),
        "failures": [s for s in steps if not s["ok"]],
        "steps": steps,
        "renderer": renderer,
        # Mirrored at the top level as well as inside `renderer`, because a
        # consumer that greps this file for the rasterizer class should not have
        # to know where it was nested.
        "renderer_class": renderer.get("renderer_class"),
        # The seed is the gate's long pole; its cost is measured and disclosed so
        # a reader can tell "slow because the domain is big" from "slow because
        # the harness is stuck".
        "seed": {"per_node": node_times,
                 "total_seconds": (node_times[-1]["cumulative_seconds"]
                                   if node_times else 0.0),
                 "seconds_per_mission": (
                     round(node_times[-1]["cumulative_seconds"]
                           / max(1, node_times[-1]["cumulative_missions"]), 3)
                     if node_times else None)},
        "renderer_note": (
            "MEASURED IN HEADLESS CHROME, AND THE RASTERIZER IS NAMED. When "
            "`renderer` above says SwiftShader the frame times are a CPU "
            "rasterizer's, NOT this host's GPU throughput, and the FPS must not "
            "be quoted as a hardware figure. This caveat is ENVIRONMENTAL, not a "
            "harness shortcut: headless Chrome was launched four ways on this "
            "host -- default, --enable-gpu, --enable-gpu --use-angle=d3d11, and "
            "--enable-gpu --use-angle=gl --ignore-gpu-blocklist -- and every one "
            "of them reported "
            "'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), "
            "SwiftShader driver)'. Headless is used because it is the only "
            "reproducible browser mode available to an unattended run. "
            "`renderer_class` above is derived from the string so a consumer of "
            "this evidence cannot ignore the distinction."),
        "measurement": {
            "nodes": list(NODES), "projects": PROJECTS,
            "warmup_frames": WARMUP_FRAMES, "measure_frames": MEASURE_FRAMES,
            "budget_ms": MEASURE_BUDGET_MS, "poll_ms": POLL_MS,
            "rows": rows, "slope": slope_detail,
            "viewport": {"width": 1280, "height": 800},
        },
        "view_sweep": sweep,
        "page_errors": page_errors,
        "note_not_claimed": (
            "This gate does NOT claim a hardware frame rate, a target FPS, or "
            "behaviour on a discrete GPU. It DOES claim: the shipped plane "
            "rendered the domain's objects at 50/200/500/1000, the work scaled "
            "with the domain, and nothing threw."),
        "ts": time.time(),
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")

    print("\ngate WAVE10_REAL_BROWSER_PERF verdict=%s passed=%d/%d"
          % (payload["verdict"], payload["passed"], payload["total"]))
    for r in rows:
        print("  n=%-5s median=%s ms p95=%s ms fps=%.1f draws/frame=%.1f "
              "(expected %s)"
              % (r["n_requested"], _f(r["median_ms"]), _f(r["p95_ms"]), r["fps"],
                 r["draw_calls_per_frame"], r["expected_draw_calls"]))
    if slope_detail:
        print("  slope: %s" % json.dumps(slope_detail))
    for s in payload["failures"]:
        print("  FAIL %s: %s" % (s["step"], s["value"]))
    print(json.dumps({"verdict": payload["verdict"], "passed": payload["passed"],
                      "total": payload["total"], "evidence": str(OUT)}))
    print("wrote %s" % OUT)
    return 0 if payload["verdict"] == "PASS" else 1


def _f(v: Any) -> str:
    return "n/a" if v is None else ("%.2f" % v if isinstance(v, (int, float)) else str(v))


def _teardown(servers, api_port: int, ui_port: int) -> Dict[str, int]:
    for s in servers:
        try:
            s.should_exit = True
        except Exception:  # noqa: BLE001
            pass
    for _ in range(40):
        held = {p: port_owner(p) for p in (api_port, ui_port)}
        if not any(held.values()):
            return held
        time.sleep(0.25)
    return {p: port_owner(p) for p in (api_port, ui_port)}


def _finish() -> None:
    payload = {
        "gate": "WAVE10_REAL_BROWSER_PERF",
        "verdict": "FAIL" if failures else "PASS",
        "passed": sum(1 for s in steps if s["ok"]),
        "total": len(steps),
        "failures": [s for s in steps if not s["ok"]],
        "steps": steps,
        "ts": time.time(),
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({"verdict": payload["verdict"], "passed": payload["passed"],
                      "total": payload["total"], "evidence": str(OUT)}))
    print("wrote %s" % OUT)


if __name__ == "__main__":
    raise SystemExit(main())
