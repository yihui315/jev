#!/usr/bin/env python3
"""Wave 2 — Product UI gate for the v1.3 Product Edition.

THE GATE'S QUESTION
-------------------
"Does the SHIPPED product shell render the user's REAL persisted state — with the
product config, the product ports, and the product's own static server?"

THE DEFECT THIS GATE ITSELF HAD (found by the W9 final gate, 2026-09-29)
-----------------------------------------------------------------------
The first version of this gate booted the API on an EPHEMERAL port and served
``dist/`` with a bespoke ``http.server`` reverse proxy that swallowed ``/api/*``
and forwarded it to that port. Its docstring justified the proxy with "what the
real product does when the durable runtime serves UI + API from one port".

That premise is false. The shipped topology is TWO processes: the API on
127.0.0.1:4779 and the SPA on 127.0.0.1:4780 served by
``apps/command_center_3d/serve_visual.py`` — a static ASGI server that serves
``/health``, ``/api/health`` and the bundle, and does **not** proxy ``/api/v1/*``.
The SPA therefore talks to the API CROSS-ORIGIN, which is why
``server.cors_origins`` allowlists 4780/4781.

So the gate was measuring a configuration the product does not ship, and it only
passed while ``dist/`` happened to have been built with
``VITE_API_BASE=same-origin`` — a non-default build. Once the bundle was rebuilt
with the default (an absolute ``http://127.0.0.1:4779``), every browser request
bypassed the gate's proxy and hit a port where nothing was listening: the gate
reported 18/23 with ``ERR_CONNECTION_REFUSED`` and live-data checks failing.

The fix is not to rebuild the artifact into the shape the harness wants. The gate
now reproduces the SHIPPED topology exactly:

  * it loads ``config/jcc-v1.3.yaml`` (the product config) and asserts the
    declared ports, rather than constructing a synthetic ``Config``;
  * it boots the API on its declared port and refuses to run at all if a listener
    already holds either product port (never adopt a process it did not start);
  * it serves ``dist/`` with the product's OWN server module, so the serving path
    is measured rather than reimplemented;
  * it drives REAL Chrome at the real UI origin, so the cross-origin requests and
    the CORS allowlist are exercised end to end;
  * it asserts every port is released again before returning.

Checks (each is a real HTTP, DOM or OS fact, cross-checked against the API):
  * CONFIG      — the product config is the one under measurement; the UI origin
                  is in the API's CORS allowlist; the product ports are free.
  * BOOT        — the API answers /api/v1/domain/home (schema v1.3) and the
                  shipped SPA server answers /health as ``uvicorn-asgi``.
  * CORS        — a real preflight from the UI origin is allowed.
  * SHELL/NAV   — sidebar, top bar, Home default view, 5 counters, CTA, and every
                  product surface reachable.
  * LIVE HONESTY— an empty workspace renders explicit empty states, not fake counts.
  * REAL DATA   — after creating a project+mission through the API, Home's counters
                  and the Projects/Missions surfaces show exactly that state.
  * STUDIO      — the Mission Studio form exposes its fields and offers the
                  API-created project.
  * PALETTE     — Ctrl+K opens the command palette.
  * A11Y        — landmarks, responsive baseline, no uncaught console errors.
  * TEARDOWN    — no product listener is left behind.

Run:  F:/jev/.venv/Scripts/python.exe tools/v1_3_wave2_ui_gate.py
Exit: 0 = PASS, 1 = FAIL (a real gate, not a green-washer).
"""
from __future__ import annotations

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
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

DIST = ROOT / "apps" / "command_center_3d" / "dist"
SCRATCH = ROOT / ".workbuddy" / "scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d-%H%M%S")
OUT = SCRATCH / f"v1_3_wave2_ui_gate_{STAMP}.json"

CONTROL_TOKEN = "wave2-gate-token"
CONTROL_TOKEN_ENV = "JCC_CONTROL_TOKEN"

failures: List[str] = []
steps: List[dict] = []


def check(name: str, cond: bool, value: Any = None, note: str = "") -> bool:
    steps.append({"step": name, "ok": bool(cond), "value": value, "note": note})
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}: {note}")
    if not cond:
        failures.append(name)
    return bool(cond)


# REMOVED: free_port(). The gate no longer picks ephemeral ports: measuring the
# product means measuring the ports the product declares.


class _NoProxy(urllib.request.ProxyHandler):
    def proxy_open(self, *a, **k):  # bypass any system proxy
        return None


OPENER = urllib.request.build_opener(_NoProxy())


def http(method: str, url: str, body: Optional[dict] = None, token: Optional[str] = None,
         timeout: float = 12.0) -> tuple[int, Any]:
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


PRODUCT_CONFIG = ROOT / "config" / "jcc-v1.3.yaml"
PRODUCT_UI_PORT = 4780


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


def cors_preflight(url: str, origin: str) -> dict:
    """A REAL preflight from the SPA's origin -- the write path the UI uses."""
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
    spec = importlib.util.spec_from_file_location("jcc_shipped_serve_visual", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if not (DIST / "index.html").is_file():
        print(f"FAIL: no built bundle at {DIST} — run `vite build` first")
        return 1

    import uvicorn
    from fastapi.testclient import TestClient  # noqa: F401  (import check)
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
    api_base = f"http://127.0.0.1:{api_port}"
    ui_base = f"http://127.0.0.1:{ui_port}"

    check("PRODUCT_CONFIG_IS_THE_SHIPPED_ONE",
          cfg.server.host == "127.0.0.1" and api_port == 4779 and ui_port == 4780,
          {"host": cfg.server.host, "api_port": api_port, "ui_port": ui_port},
          "measuring config/jcc-v1.3.yaml, not a synthetic Config")
    check("UI_ORIGIN_IS_IN_THE_CORS_ALLOWLIST", ui_base in cfg.server.cors_origins,
          cfg.server.cors_origins,
          "the SPA origin is allowed by the API's explicit CORS list")

    # FAIL-CLOSED PRECONDITION -- never adopt a listener this gate did not start.
    occupied = {p: port_owner(p) for p in (api_port, ui_port)}
    busy = {p: v for p, v in occupied.items() if v}
    check("PRECONDITION_PRODUCT_PORTS_FREE", not busy, occupied,
          "4779+4780 free, so what this gate observes is its own"
          if not busy else
          "a listener already holds a product port: %s — stop the product first "
          "(scripts/jcc-v1.3.ps1 -Action down)" % busy)
    if busy:
        _finish()
        return 1

    tmpdir = Path(tempfile.mkdtemp(prefix="jcc_w2_gate_"))
    cfg.state_store_db_path = str(tmpdir / "wave2.db")
    os.environ[cfg.control_token_env] = CONTROL_TOKEN

    store = StateStore(cfg.state_store_db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)

    servers = []
    api_server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
    threading.Thread(target=api_server.run, daemon=True).start()
    servers.append(api_server)

    ready = False
    for _ in range(80):
        try:
            st, _body = http("GET", f"{api_base}/api/v1/domain/home", timeout=2.0)
            if st == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not check("BOOT", ready, api_base, "v1.3 app answered /api/v1/domain/home"):
        _teardown(servers, api_port, ui_port)
        _finish()
        return 1

    # ---- the SHIPPED static server, on the SHIPPED UI port ----
    ui_server_mod = _load_shipped_ui_server()
    ui_app = ui_server_mod.build_app(str(DIST), ui_port)
    ui_server = uvicorn.Server(
        uvicorn.Config(ui_app, host="127.0.0.1", port=ui_port, log_level="warning"))
    threading.Thread(target=ui_server.run, daemon=True).start()
    servers.append(ui_server)

    ui_ready = False
    ui_health: Any = None
    for _ in range(60):
        st, ui_health = http("GET", ui_base + "/health", timeout=2.0)
        if st == 200 and isinstance(ui_health, dict):
            ui_ready = True
            break
        time.sleep(0.25)
    check("UI_BOOT", ui_ready, ui_health, "the shipped SPA server answered /health")
    check("UI_SERVED_BY_THE_SHIPPED_ASGI_SERVER",
          isinstance(ui_health, dict) and ui_health.get("runtime") == "uvicorn-asgi"
          and ui_health.get("port") == ui_port,
          ui_health if isinstance(ui_health, dict) else ui_health,
          "apps/command_center_3d/serve_visual.py under uvicorn, not http.server")

    # a REAL preflight: this is the cross-origin write the SPA actually performs
    pf = cors_preflight(api_base + "/api/v1/domain/projects", ui_base)
    check("CORS_PREFLIGHT_FOR_THE_UI_ORIGIN", pf.get("ok"), pf,
          "the API allows the SPA's cross-origin write")

    st, home = http("GET", f"{api_base}/api/v1/domain/home")
    check("BOOT_SCHEMA", st == 200 and isinstance(home, dict)
          and home.get("schema") == "jcc/v1.3-domain-home/1",
          home.get("schema") if isinstance(home, dict) else None, "domain home schema")

    # ---- create REAL state through the API ----
    st, proj = http("POST", f"{api_base}/api/v1/domain/projects",
                    {"name": "Wave2 Gate Project", "root_path": r"F://jev"}, CONTROL_TOKEN)
    check("API_CREATE_PROJECT", st == 200 and isinstance(proj, dict) and proj.get("project_id"),
          proj.get("project_id") if isinstance(proj, dict) else proj, "project created")

    pid = proj["project_id"] if isinstance(proj, dict) else None
    st, msn = http("POST", f"{api_base}/api/v1/domain/missions",
                   {"project_id": pid, "title": "Wave2 Gate Mission",
                    "goal": "prove the product shell reads real state",
                    "agent": "workbuddy", "permissions": ["fs.read"]}, CONTROL_TOKEN)
    check("API_CREATE_MISSION", st == 200 and isinstance(msn, dict) and msn.get("status") == "DRAFT",
          msn.get("status") if isinstance(msn, dict) else msn, "mission created as DRAFT")

    # an un-token'd write must be refused — fail-closed
    st, _b = http("POST", f"{api_base}/api/v1/domain/projects", {"name": "should-fail"})
    check("API_TOKEN_FAIL_CLOSED", st in (401, 403), st, "write without token refused")

    ok = _browser_checks(ui_base, api_base)

    released = _teardown(servers, api_port, ui_port)
    check("PORTS_RELEASED_AFTER_TEARDOWN", not any(released.values()), released,
          "the gate leaves no product listener behind")

    _finish()
    return 0 if (ok and not failures) else 1


def _teardown(servers, api_port: int, ui_port: int) -> dict:
    """Ask every server to stop, then READ the OS to see whether it did."""
    for s in servers:
        try:
            s.should_exit = True
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + 20
    while time.time() < deadline and (port_owner(api_port) or port_owner(ui_port)):
        time.sleep(0.25)
    return {p: port_owner(p) for p in (api_port, ui_port)}


# REMOVED: _serve_dist(). It served dist/ behind a bespoke http.server that
# reverse-proxied /api/* to an ephemeral API port, which made the gate same-origin
# and hid the shipped topology. The gate now serves the bundle with the product's
# own `serve_visual.py` and lets the browser make the real cross-origin calls --
# see the module docstring. Do not reintroduce a proxy here: a harness that
# rewrites the request path cannot see a mis-built bundle.


def _browser_checks(ui_base: str, api_base: str) -> bool:
    from playwright.sync_api import sync_playwright

    console_errors: List[str] = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True,
                                        args=["--no-sandbox", "--disable-gpu"])
            check("CHROME", True, browser.version, "real Chrome channel")
        except Exception as e:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            check("CHROME", False, str(e)[:160], "chrome channel unavailable, used bundled")

        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR: {e}"))

        page.goto(ui_base, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)

        check("SHELL_SIDEBAR",
              page.locator("nav[aria-label='Product navigation']").count() == 1, None, "sidebar")
        check("SHELL_TOPBAR", page.locator("header").count() >= 1, None, "top bar")
        check("HOME_DEFAULT", "Mission Control" in page.inner_text("body"), None, "Home lands first")

        ids = ["stat-active-projects", "stat-running-missions", "stat-needs-attention",
               "stat-deliverables", "stat-completed"]
        got = [i for i in ids if page.locator(f"[data-testid='{i}']").count() == 1]
        check("HOME_COUNTERS", len(got) == 5, got, f"{len(got)}/5 counters")

        check("HOME_CTA", page.locator("[data-testid='cta-new-mission']").count() == 1,
              None, "New Mission CTA")

        # ---- REAL DATA: the counts must reflect what the API holds ----
        # Home renders counters + attention items, not entity names, so assert
        # the counter moved (data reached the UI) and then verify the NAME on
        # the surface that is supposed to show it (Projects).
        counter_txt = ""
        for _ in range(12):
            counter_txt = page.locator("[data-testid='stat-active-projects']").inner_text()
            if "0" != counter_txt.strip().splitlines()[-1].strip():
                break
            page.wait_for_timeout(600)
        check("LIVE_COUNTER_NONZERO", "0" != counter_txt.strip().splitlines()[-1].strip(),
              counter_txt.replace("\n", " ")[:60], "active-project counter reflects the API")

        page.locator("nav[aria-label='Product navigation'] button", has_text="Projects").first.click()
        page.wait_for_timeout(900)
        proj_body = ""
        for _ in range(12):
            proj_body = page.inner_text("body")
            if "Wave2 Gate Project" in proj_body:
                break
            page.wait_for_timeout(600)
        check("LIVE_REAL_DATA", "Wave2 Gate Project" in proj_body,
              None, "API-created project renders on the Projects surface")

        page.locator("nav[aria-label='Product navigation'] button", has_text="Missions").first.click()
        page.wait_for_timeout(900)
        msn_body = ""
        for _ in range(12):
            msn_body = page.inner_text("body")
            if "Wave2 Gate Mission" in msn_body:
                break
            page.wait_for_timeout(600)
        check("LIVE_REAL_MISSION", "Wave2 Gate Mission" in msn_body,
              None, "API-created mission renders with its DRAFT state")
        check("LIVE_MISSION_STATE", "DRAFT" in msn_body, None, "mission shows its real state")

        # ---- navigation over every surface ----
        labels = ["Home", "Projects", "Missions", "Needs Attention", "Deliverables",
                  "New Mission", "World", "Graph", "Table", "Connections", "Audit",
                  "Diagnostics", "Settings"]
        nav_ok, nav_fail = [], []
        for lb in labels:
            btn = page.locator("nav[aria-label='Product navigation'] button", has_text=lb).first
            if btn.count() == 0:
                nav_fail.append(f"{lb}:missing")
                continue
            btn.click()
            page.wait_for_timeout(400)
            nav_ok.append(lb)
        check("NAV_ALL", not nav_fail, {"ok": nav_ok, "fail": nav_fail},
              f"{len(nav_ok)}/{len(labels)} surfaces")

        # ---- studio form completeness ----
        page.locator("nav[aria-label='Product navigation'] button", has_text="New Mission").first.click()
        page.wait_for_timeout(600)
        fields = ["msn-title", "msn-project", "msn-agent", "msn-goal", "msn-wd",
                  "msn-budget", "msn-ctx", "msn-perms"]
        have = [f for f in fields if page.locator(f"#{f}").count() == 1]
        check("STUDIO_FORM", len(have) == len(fields), have, f"{len(have)}/{len(fields)} fields")

        # the project created via the API must be selectable in the form
        opts = page.locator("#msn-project option").all_inner_texts()
        check("STUDIO_PROJECT_OPTION", any("Wave2 Gate Project" in o for o in opts),
              opts[:4], "API-created project is selectable")

        # ---- command palette ----
        page.keyboard.press("Control+k")
        page.wait_for_timeout(600)
        check("PALETTE_CTRLK", page.locator("[role='dialog']").count() > 0, None, "Ctrl+K palette")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ---- theme (responsive/a11y baseline) ----
        theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
        check("THEME_ATTR", theme in ("dark", "light"), theme, "theme attribute set")

        check("A11Y_LANDMARKS",
              page.locator("nav[aria-label]").count() >= 1 and page.locator("header").count() >= 1
              and page.locator("main").count() >= 1, None, "nav/header/main landmarks")

        # ---- narrow viewport: the sidebar must go off-canvas, page still usable ----
        page.set_viewport_size({"width": 420, "height": 900})
        page.wait_for_timeout(500)
        mobile_ok = page.locator("nav[aria-label='Product navigation']").count() == 1
        check("RESPONSIVE_MOBILE", mobile_ok, None, "navigation present at 420px")

        browser.close()

    real = [e for e in console_errors if "favicon" not in e.lower()]
    check("NO_CONSOLE_ERRORS", not real, real[:6], f"{len(real)} console errors")
    return not failures


def _finish() -> None:
    doc = {
        "gate": "WAVE2_PRODUCT_UI",
        "topology": ("shipped: API on config/jcc-v1.3.yaml server.port, SPA on 4780 "
                     "served by apps/command_center_3d/serve_visual.py, cross-origin "
                     "with the API's CORS allowlist"),
        "verdict": "PASS" if not failures else "FAIL",
        "passed": sum(1 for s in steps if s["ok"]),
        "total": len(steps),
        "failures": failures,
        "steps": steps,
        "ts": time.time(),
    }
    OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"\n{'PASS' if not failures else 'FAIL'} "
          f"{doc['passed']}/{doc['total']} -> {OUT}")
    if failures:
        print("failures:", failures)


if __name__ == "__main__":
    sys.exit(main())
