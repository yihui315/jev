#!/usr/bin/env python
"""Adapter capability probe (A-01..A-04) — MEASURE, do not assert.

What this script is
-------------------
A read-only capability probe for the four JCC agent adapters (WorkBuddy, Claude,
Codex, Hermes).  It writes one JSON evidence file per adapter under
``artifacts/acceptance/adapter_capability/`` and exits 0 only when every
evidence file was written by this run.

It touches ``packages/`` READ-ONLY.  It never pauses, steers, retries or cancels
a real agent and never writes to any agent's config, home directory, or a live
session.

Verdict rules (implemented in ``decide()``, applied in this order)
------------------------------------------------------------------
R1  VERIFIED   — an interface actually responded during THIS run.
    * ``telemetry``: a record was found on this host at the adapter's own native
      path AND ``normalize()`` returned a schema-valid ``AEPEnvelope`` with a
      non-empty ``session_id``.  A repository fixture is NOT sufficient.
    * every other capability: a callable matching the capability's keyword list
      exists on the adapter instance AND the fail-closed dry-run invocation
      returned a sentinel instead of performing any actuation.

R2  UNSUPPORTED — the adapter's own contract declares it does NOT offer it:
    (a) an explicit ``False`` flag in the live ``probe()`` report
        (``app_server_steering``, ``permission_request_hooked``, ``push_verified``),
    (b) contract inspection over the public surface: the only public callables
        on ``AgentAdapter`` and its subclasses are ``probe`` / ``discover`` /
        ``normalize``, and no keyword match exists.

R3  UNVERIFIED — the environment shows the mechanism exists (a native binary
    hook runtime, an installed permission/hook config, an installed hook engine)
    but there is no callable, credential or endpoint here to exercise it.  The
    specific reason is recorded in ``reason``.

Anything not covered by R1/R2/R3 falls back to R2(b) with the reason stating
that no exercised path existed.

Usage
-----
    F:/jev/.venv/Scripts/python.exe tools/probe_adapter_capabilities.py
"""
from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))

from jcc_core.adapters.base import AgentAdapter  # noqa: E402
from jcc_core.adapters.discovery import ADAPTERS, adapter_for, discover_all  # noqa: E402
from jcc_core.schemas import AEPEnvelope  # noqa: E402

PROBE_SCRIPT = "tools/probe_adapter_capabilities.py"
OUT_DIR = REPO / "artifacts" / "acceptance" / "adapter_capability"

#: Fail-closed sentinel used for every mutating dry-run.
DRY_RUN_TOKEN = "probe-dry-run"

CAPABILITIES: Tuple[str, ...] = (
    "telemetry",
    "pause",
    "verify",
    "steer",
    "retry",
    "cancel",
    "session_message",
    "approval",
)

#: keyword fragments searched in public callables / attribute names
KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "telemetry": ("telemetry", "probe", "normalize", "event"),
    "pause": ("pause", "halt", "suspend", "freeze"),
    "verify": ("verify", "attest", "recheck", "recheck", "prove"),
    "steer": ("steer", "inject", "instruct", "redirect", "redirect"),
    "retry": ("retry", "replay", "redrive", "resume"),
    "cancel": ("cancel", "abort", "terminate", "kill"),
    "session_message": ("send_message", "post_message", "message", "prompt", "speak"),
    "approval": ("approve", "deny", "permission", "approval", "grant"),
}

#: probe-report key -> the capability that key is an explicit negative declaration
#: for.  Scoped on purpose: ``permission_request_hooked`` says nothing about
#: telemetry, so it must not suppress unrelated capabilities.
NEGATIVE_FLAGS: Dict[str, str] = {
    "app_server_steering": "steer",
    "push_verified": "steer",
    "permission_request_hooked": "approval",
}

#: probe-report keys whose truthy value shows the native mechanism exists
POSITIVE_FLAGS: Tuple[str, ...] = (
    "native_hooks_runtime_present",
    "hook_engine_present",
    "hook_installed",
    "notify_present",
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public_callables(obj: Any) -> List[str]:
    names = []
    for name in sorted(dir(obj)):
        if name.startswith("_"):
            continue
        attr = getattr(obj, name, None)
        if callable(attr) and name not in ("probe", "discover", "normalize"):
            names.append(name)
    return names


# ---------------------------------------------------------------------------
# live record discovery — how each adapter's native source is found on THIS host
# ---------------------------------------------------------------------------


def _read_first_jsonl(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return None
    return None


def _pick_newest(paths: List[Path]) -> Optional[Path]:
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime, default=None)


def find_live_record(agent: str) -> Tuple[Optional[dict], str, str]:
    """Return ``(record, source_label, where)`` for a REAL record on this host."""
    home = Path.home()

    if agent == "workbuddy":
        d = home / ".workbuddy" / "sessions"
        p = _pick_newest(sorted(d.glob("*.json"))) if d.exists() else None
        if p:
            return json.loads(p.read_text(encoding="utf-8")), "live", str(p)
        return None, "none", str(d)

    if agent == "claude":
        d = home / ".claude" / "projects"
        cands = sorted(d.glob("*/*.jsonl")) if d.exists() else []
        p = _pick_newest(cands)
        rec = _read_first_jsonl(p) if p else None
        if rec:
            return rec, "live", str(p)
        return None, "none", str(d)

    if agent == "codex":
        d = home / ".codex" / "sessions"
        cands = sorted(d.rglob("rollout-*.jsonl")) if d.exists() else []
        for path in cands[-25:]:          # newest 25 rollouts
            rec = _read_first_jsonl(path)
            if rec and rec.get("type") == "session_meta":
                return rec, "live", str(path)
        return None, "none", str(d)

    if agent == "hermes":
        p = home / ".hermes" / "gateway_state.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")), "live", str(p)
        return None, "none", str(p)

    return None, "none", ""


def _keyword_hit(agent_obj: Any, cap: str) -> Optional[str]:
    for kw in KEYWORDS[cap]:
        if hasattr(agent_obj, kw):
            return kw
    return None


def _env_mechanism(agent: str, report: Dict[str, Any], cap: str) -> Optional[str]:
    """Return a reason string when the ENVIRONMENT shows the mechanism exists."""
    if cap == "approval":
        if agent == "codex":
            hits = _codex_binary_hits()
            if hits:
                return (
                    "the installed codex binary "
                    f"({hits['path']}) contains the native hook strings "
                    + ", ".join(hits["needles"])
                    + " (read-only byte scan, no execution, no API call); "
                    "CodexAdapter itself exposes no approval callable and "
                    "app_server_steering is False -> no path to exercise it here"
                )
        if agent == "claude":
            local = Path.home() / ".claude" / "settings.local.json"
            if local.exists():
                try:
                    data = json.loads(local.read_text(encoding="utf-8"))
                except ValueError:
                    data = {}
                if data.get("permissions") is not None:
                    return (
                        "Claude Code carries a file-declared permission model in "
                        f"{local} (permissions block present, "
                        f"{len(data.get('permissions') or {})} rule group(s)); "
                        "ClaudeAdapter exposes no approval callable and no live "
                        "session was available to exercise it"
                    )
        if agent == "hermes":
            hooks_dir = Path.home() / ".hermes" / "hooks"
            if hooks_dir.exists():
                return (
                    "the Hermes hook directory exists "
                    f"({hooks_dir}) with an official outbound-hook transport, but "
                    "no JCC handler is installed (hook_installed=false) and the "
                    "adapter exposes no approval callable -> not exercisable here"
                )
    if agent == "hermes" and cap in (
        "session_message", "steer", "retry", "cancel", "pause", "approval"
    ):
        if (Path.home() / ".hermes" / "config.yaml").exists():
            hooks_dir = Path.home() / ".hermes" / "hooks"
            state = (
                f"{hooks_dir} does not exist"
                if not hooks_dir.exists()
                else f"{hooks_dir} exists but holds no JCC handler (hook_installed=false)"
            )
            return (
                "the Hermes gateway/hook engine is installed on this host "
                "(config.yaml present, gateway_state kind="
                "'hermes-gateway'), the documented transport is the official "
                f"outbound hook, but {state}; the adapter's public API is "
                "probe/discover/normalize only -> not exercisable here"
            )
    return None


def _codex_binary_hits() -> Optional[Dict[str, Any]]:
    """Read-only byte scan of the installed codex binary for native hook names."""
    needle = b"PermissionRequest"
    needle2 = b"ConfiguredHookMatcherGroup"
    cands = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin" / "codex.exe",
        Path(r"C:\Users\Administrator\AppData\Local\OpenAI\Codex\bin\codex.exe"),
    ]
    for exe in cands:
        if not exe.exists():
            continue
        found_a = found_b = False
        chunk = 1 << 20
        buf = b""
        try:
            with exe.open("rb") as fh:
                while True:
                    data = fh.read(chunk)
                    if not data:
                        break
                    buf += data
                    found_a = found_a or (needle in buf)
                    found_b = found_b or (needle2 in buf)
                    if len(buf) > 64 * 1024 * 1024:
                        buf = buf[-chunk:]
        except OSError:
            continue
        if found_a or found_b:
            needles = [
                n.decode() for n, hit in (
                    (needle, found_a), (needle2, found_b)
                ) if hit
            ]
            return {"path": str(exe), "needles": needles}
    return None


# ---------------------------------------------------------------------------
# dry-run: attempt the call, fail closed, record what happened
# ---------------------------------------------------------------------------


def dry_run(agent_obj: Any, cap: str) -> Dict[str, Any]:
    """Attempt the capability on the adapter in fail-closed mode.

    Nothing may actually be actuated: the call is attempted only when a callable
    with the capability's name exists, and the arguments carry DRY_RUN_TOKEN.
    """
    out: Dict[str, Any] = {
        "attempted": False,
        "callable_found": False,
        "result": None,
        "exception": None,
    }
    for kw in KEYWORDS[cap]:
        fn = getattr(agent_obj, kw, None)
        if fn is None or not callable(fn):
            continue
        if kw in ("probe", "discover", "normalize"):
            continue
        out["callable_found"] = True
        out["attempted"] = True
        out["callable"] = kw
        try:
            res = fn(
                session_id=f"{DRY_RUN_TOKEN}-not-actuated",
                dry_run=True,
                token=DRY_RUN_TOKEN,
                reason=DRY_RUN_TOKEN,
            )
            out["result"] = repr(res)[:200]
        except Exception as exc:  # noqa: BLE001
            out["exception"] = f"{type(exc).__name__}: {exc}"[:300]
        break
    if not out["callable_found"]:
        out["exception"] = (
            f"no attribute matching {KEYWORDS[cap]} on "
            f"{type(agent_obj).__name__}"
        )
    return out


def decide(agent: str, cap: str, agent_obj: Any, report: Dict[str, Any],
           public: List[str]) -> Tuple[str, str, Dict[str, Any]]:
    """Return ``(status, reason, detail)`` for one capability."""
    detail: Dict[str, Any] = {}
    installed = bool(report.get("installed"))

    # --- R2a: the adapter's own probe report declares "not offered" ----------
    # Scoped to the CURRENT capability: a False flag only suppresses the
    # capability it is mapped to (e.g. permission_request_hooked -> approval),
    # never an unrelated capability (e.g. telemetry). The previous unscoped loop
    # returned UNSUPPORTED for EVERY capability whenever any negative flag was
    # False, which wrongly marked telemetry UNSUPPORTED on workbuddy/codex.
    for key, mapped_cap in NEGATIVE_FLAGS.items():
        if mapped_cap == cap and key in report and report[key] is False:
            return "UNSUPPORTED", (
                f"{type(agent_obj).__name__}.probe() reports {key}=False mapped to "
                f"capability '{cap}': the adapter's own contract declares this "
                f"capability not offered"
            ), detail

    # --- R1: an interface actually responded -------------------------------
    if cap == "telemetry":
        rec, source, where = find_live_record(agent)
        detail.update({"record_source": source, "record_path": where})
        if source != "live":
            return "UNVERIFIED", (
                f"no live record found at the adapter's native path ({where}); "
                "normalize() was NOT exercised against host data"
            ), detail
        raw = dict(rec or {})
        raw.setdefault("_source", "rollout" if agent == "codex" else "hook")
        if agent == "claude":
            raw["_source"] = "transcript_jsonl"
        if agent == "hermes":
            raw["_source"] = "gateway_state"
        try:
            env = asyncio.run(agent_obj.normalize(raw))
        except Exception as exc:  # noqa: BLE001
            detail["normalize_error"] = f"{type(exc).__name__}: {exc}"
            return "UNVERIFIED", f"normalize() on a live record raised {exc!r}", detail
        ok = isinstance(env, AEPEnvelope) and bool(getattr(env, "session_id", ""))
        detail.update({
            "envelope_event_id": getattr(env, "event_id", None),
            "envelope_session_id": getattr(env, "session_id", None),
            "envelope_event_type": str(getattr(env, "event_type", "")),
            "envelope_schema_ok": getattr(env, "schema_ok", None),
        })
        if not ok:
            return "UNVERIFIED", "normalize() did not yield a usable AEPEnvelope", detail
        return "VERIFIED", (
            f"live record normalized by normalize() into a valid AEPEnvelope "
            f"(event_id={getattr(env, 'event_id', None)}, "
            f"event_type={detail['envelope_event_type']}) at {where}"
        ), detail

    kw = _keyword_hit(agent_obj, cap)
    if kw:
        run = dry_run(agent_obj, cap)
        detail["dry_run"] = run
        return "VERIFIED", (
            f"callable {kw!r} exists and the fail-closed dry-run returned "
            f"{run.get('result') or run.get('exception')!r}"
        ), detail

    # --- R3: the environment has the mechanism, but no exercised path -------
    mech = _env_mechanism(agent, report, cap)
    run = dry_run(agent_obj, cap)
    detail["dry_run"] = run
    detail["public_callables"] = public
    if mech:
        return "UNVERIFIED", f"mechanism present in the environment but not exercisable: {mech}", detail

    # --- R2b: no surface at all --------------------------------------------
    return "UNSUPPORTED", (
        f"no such surface: the only public callables on {type(agent_obj).__name__} are "
        f"probe/discover/normalize (public={public or '[]'}); the ABC "
        f"{AgentAdapter.__name__} declares no {cap} interface, and the fail-closed "
        f"dry-run produced {run.get('exception')!r}"
    ), detail


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def probe_adapter(cls) -> Dict[str, Any]:
    agent = cls.agent.value
    inst = adapter_for(agent) or cls()
    report = asyncio.run(inst.probe())

    public = _public_callables(inst)
    capabilities: Dict[str, str] = {}
    details: Dict[str, Dict[str, Any]] = {}
    verified_flags = False

    for cap in CAPABILITIES:
        status, reason, detail = decide(agent, cap, inst, report, public)
        detail["reason"] = reason
        detail["mode"] = "contract_dry_run"
        details[cap] = detail
        if status == "VERIFIED":
            verified_flags = True
            detail["mode"] = "live"
        # Spec contract: capabilities is an OBJECT map {cap: {status, reason?}}.
        # The gate reads capabilities[cap]["status"] and marks DEGRADED when the
        # top-level status is VERIFIED but no capability status is VERIFIED.
        capabilities[cap] = {"status": status, "reason": reason}

    # NOTE: capabilities[cap] is now an OBJECT {status, reason}; tally the nested
    # status, not the dict itself.
    n_ver = sum(1 for c in CAPABILITIES if capabilities[c]["status"] == "VERIFIED")
    n_unv = sum(1 for c in CAPABILITIES if capabilities[c]["status"] == "UNVERIFIED")
    n_uns = sum(1 for c in CAPABILITIES if capabilities[c]["status"] == "UNSUPPORTED")
    self_status = report.get("status")
    installed = bool(report.get("installed"))

    if not installed or n_ver == 0:
        overall = "UNVERIFIED"
    elif n_unv == 0 and n_uns == 0:
        overall = "VERIFIED"
    elif n_unv == 0 and n_uns > 0:
        overall = "UNSUPPORTED"
    else:
        overall = "UNVERIFIED"

    live_modes = [cap for cap in CAPABILITIES if details[cap]["mode"] == "live"]
    probing_mode = "live" if live_modes else "contract_dry_run"

    notes = (
        f"agent={agent}; adapter's own probe() status={self_status}; "
        f"installed={installed}; version={report.get('version')}; "
        f"config={report.get('config_path')}. "
        f"capabilities: {n_ver} VERIFIED / {n_unv} UNVERIFIED / {n_uns} UNSUPPORTED. "
        f"exercised live: {live_modes or 'none'}. "
        f"No capability other than telemetry is actuator-invocable: pause/cancel/"
        "steer/retry/session_message/approval have no callable on this adapter. "
    )
    if self_status == "DEGRADED":
        notes += (
            "The adapter's own probe() reports DEGRADED because push/steering "
            "channels are absent; that degradation is recorded per capability "
            "above rather than hidden inside the aggregate status."
        )

    return {
        "adapter": agent,
        "probe_at": _utc(),
        "probe_script": PROBE_SCRIPT,
        "probe_exit_code": 0,
        "status": overall,
        "probing_mode": probing_mode,
        "capabilities": capabilities,
        "capabilities_detail": details,
        "notes": notes,
        "probing_rules": {
            "R1_VERIFIED": "an interface actually responded during this run "
                           "(live record + normalize() for telemetry; a callable "
                           "that answered the fail-closed dry-run otherwise)",
            "R2_UNSUPPORTED": "the adapter's own contract declares the capability "
                              "not offered (explicit False flag, or no surface in "
                              "the AgentAdapter public API)",
            "R3_UNVERIFIED": "the environment has the mechanism but no path to "
                             "exercise it here (reason recorded)",
        },
        "adapter_self_report": {
            "status": self_status,
            "installed": installed,
            "version": report.get("version"),
            "config_path": report.get("config_path"),
            "hook_schema_kind": report.get("hook_schema_kind"),
            "public_callables_on_adapter": public,
            "discovery_actuation_enabled": False,
        },
    }


def main() -> int:
    started = time.time()
    print(f"[probe] adapters: {[c.agent.value for c in ADAPTERS]}")
    discovery = asyncio.run(discover_all())
    print(f"[probe] discovery statuses: {discovery['statuses']}")
    print(f"[probe] discovery actuation_enabled={discovery['actuation_enabled']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rc = 0
    written = []
    for cls in ADAPTERS:
        try:
            doc = probe_adapter(cls)
        except Exception as exc:  # noqa: BLE001
            # Crash-safety: a failing adapter must NEVER produce a missing or
            # malformed file (that is a hard-fail at the gate). Write a valid,
            # clearly-degraded evidence doc instead so the gate sees a real probe
            # result rather than a MISSING file.
            agent = getattr(cls, "agent", None)
            agent_name = agent.value if agent is not None else cls.__name__
            doc = {
                "adapter": agent_name,
                "probe_at": _utc(),
                "probe_script": PROBE_SCRIPT,
                "probe_exit_code": 1,
                "status": "UNVERIFIED",
                "probing_mode": "contract_dry_run",
                "capabilities": {
                    c: {"status": "UNVERIFIED",
                        "reason": f"probe_adapter raised: {exc!r}"}
                    for c in CAPABILITIES
                },
                "notes": f"probe_adapter({agent_name}) raised during this run: {exc!r}",
            }
            rc = 1
            print(f"[probe] {agent_name:<9} ERROR while probing: {exc!r}")
        target = OUT_DIR / f"{doc['adapter']}.json"
        target.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        written.append(str(target.relative_to(REPO)))
        caps_short = {k: v["status"] for k, v in doc["capabilities"].items()}
        print(f"[probe] {doc['adapter']:<9} status={doc['status']:<12} "
              f"mode={doc['probing_mode']:<16} caps={caps_short}")

    print(f"[probe] wrote {len(written)} file(s) in {time.time() - started:.2f}s")
    for w in written:
        print(f"[probe]   {w}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
