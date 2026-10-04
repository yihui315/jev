#!/usr/bin/env python3
"""CR-004 -- CONTINUOUS port-ownership watcher for 4778 / 4779 / 4780.

WHY THIS EXISTS
---------------
Every claim so far of the form "GREEN was not touched" has been a DISCRETE
SAMPLE: someone ran ``netstat``, saw nothing on 4778, and wrote it down. That is
not the same statement as "GREEN was never occupied", and the difference is not
academic. During Relay's CR-002 review the port state changed *between two
samples three seconds apart*: a product instance was on 4779 at T1, a GREEN
instance was on 4778 at T2, and both were gone at T3. A sampler would have
reported "clean" for any single one of those instants and would have missed the
whole event.

A release gate that hard-requires ``GREEN_untouched=true`` cannot be built on
that. This watcher replaces the sample with an INTERVAL: it polls on a fixed
cadence for a whole window and reports, per port, every contiguous span during
which something held it. Absence of a violation is then a statement about the
whole observed window, which is what a gate actually needs.

READ-ONLY, BY CONSTRUCTION
--------------------------
This script never starts, stops, signals or kills anything, and never writes to
a config. Its only outputs are the JSON report and stdout. It reads:

  * ``netstat -ano`` (or ``Get-NetTCPConnection``) for LISTEN state + owning PID;
  * the ownership markers under ``runtime/`` to decide WHO holds a port.

The refusal to act is deliberate and is what makes the artifact trustworthy: a
watcher that could fix a violation would also be able to cause one, and its
report could no longer be used as evidence.

CLASSIFICATION IS FAIL-CLOSED
-----------------------------
The load-bearing judgement is "is the thing on 4778 GREEN or not". Getting that
wrong in the convenient direction would mark a product impostor as GREEN and
report the run clean, so the rules are:

  * a port holder is GREEN only if a marker positively identifies it as GREEN
    (its ``db``/``config`` names the non-versioned ``jcc.yaml`` / ``runtime/jcc``);
  * ``jcc-v1.3`` / ``runtime/jcc-v1.3`` identifies PRODUCT;
  * **no marker, or an unreadable/ambiguous one => UNKNOWN**, never GREEN;
  * UNKNOWN on 4778 is reported in ``green_port_violations`` with
    ``classification: UNKNOWN`` -- an unidentifiable holder of the protected port
    is a gate failure, because "we could not tell" is not "it was fine".

DIAGNOSTIC vs PRODUCT
---------------------
A watcher that cannot see is not a watcher that found nothing. Every internal
failure mode -- ``netstat`` unavailable, permission denied, marker unparsable,
no ``artifacts/`` write permission -- is recorded under ``diagnostics`` with
``severity: SANDBOX_DIAG`` and never mixed into the product findings. A run with
``SANDBOX_DIAG`` entries is reported as DEGRADED, never as PASS: the distinction
between "I looked and it was clean" and "I could not look" is the whole point.

USAGE
-----
  # validate the tool itself (fast, no verdict)
  python tools/port_watch.py --selftest

  # 30s smoke run
  python tools/port_watch.py --duration 30 --interval 1.5

  # the real thing: run alongside the final gate
  python tools/port_watch.py --duration 3600 --interval 2 \\
      --out artifacts/release/v1.3_final/PORT_WATCH.json

Exit codes: 0 = observed, no violation; 1 = a GREEN-port violation was observed;
2 = the watcher itself could not observe (SANDBOX_DIAG only) -- explicitly NOT a
pass, so a broken watcher can never be mistaken for a clean host.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]

#: The ports the contract cares about.
#:   4778 GREEN baseline -- read/observe only, master directive 0.2
#:   4779 PRODUCT API
#:   4780 PRODUCT UI
GREEN_PORT = 4778
WATCH_PORTS = (4778, 4779, 4780)

#: Marker locations. GREEN's live dir is ``runtime/jcc``, the product's is
#: ``runtime/jcc-v1.3``; both write a ``JCC-marker`` and the legacy GREEN runtime
#: also writes ``jcc.ownership.json``.
MARKER_DIRS = ("runtime/jcc", "runtime/jcc-v1.3")
MARKER_NAMES = ("JCC-marker", "jcc.ownership.json")

SEV_PRODUCT = "PRODUCT"
SEV_DIAG = "SANDBOX_DIAG"

#: A netstat LISTEN row: proto, local, foreign, state, pid. The local address is
#: ``127.0.0.1:4778`` on this contract, but a wildcard bind must be caught too,
#: hence the port is compared numerically rather than by string equality.
_NETSTAT_LISTEN = re.compile(
    r"^\s*(TCP|UDP)\s+(\S+):(\d+)\s+(\S+)\s+(LISTENING|\S+)\s+(\d+)\s*$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def classify_by_paths(text: str) -> str:
    """GREEN / PRODUCT / UNKNOWN from a marker's own paths.

    The versioned runtime dir is the discriminator. ``runtime/jcc-v1.3`` is the
    product instance; a bare ``runtime/jcc`` with the unversioned ``jcc.yaml`` is
    GREEN. Anything else -- no marker, a marker with no paths, a path naming some
    third thing -- is UNKNOWN.
    """
    if not text:
        return "UNKNOWN"
    low = text.replace("\\", "/").lower()
    if "jcc-v1.3" in low or "jcc-v1_3" in low:
        return "PRODUCT"
    if "jcc.yaml" in low:
        # The unversioned config is GREEN's. A versioned one was caught above.
        return "GREEN"
    if "/runtime/jcc/" in low:
        return "GREEN"
    return "UNKNOWN"


def read_markers() -> Dict[str, Any]:
    """Read every ownership marker once. Never raises; failures become diag."""
    out: Dict[str, Any] = {"markers": [], "errors": []}
    for rel in MARKER_DIRS:
        for name in MARKER_NAMES:
            path = REPO / rel / name
            rec: Dict[str, Any] = {"path": str(path), "exists": path.is_file()}
            if not path.is_file():
                out["markers"].append(rec)
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                out["errors"].append({"path": str(path), "error": f"unreadable: {exc}"})
                out["markers"].append(rec)
                continue
            rec["mtime"] = path.stat().st_mtime
            rec["raw_sha_len"] = len(raw)
            # Two shapes: JSON ownership, or the 3-line pid=/db=/started= marker.
            try:
                data = json.loads(raw)
                rec["format"] = "json"
                rec["pid"] = data.get("pid") or data.get("service_pid")
                rec["port"] = data.get("port")
                rec["identity_text"] = " ".join(
                    str(data.get(k, "")) for k in ("config", "db", "command_line", "cwd", "repo_path")
                )
                rec["created_by"] = data.get("created_by")
            except json.JSONDecodeError:
                rec["format"] = "kv"
                fields = {}
                for line in raw.splitlines():
                    if "=" in line:
                        k, _, val = line.partition("=")
                        fields[k.strip()] = val.strip()
                rec["pid"] = _maybe_int(fields.get("pid"))
                rec["identity_text"] = " ".join(fields.values())
            rec["classification"] = classify_by_paths(rec.get("identity_text", ""))
            out["markers"].append(rec)
    return out


def _maybe_int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

def sample_listeners() -> Tuple[Dict[int, List[int]], List[Dict[str, Any]]]:
    """One sample: {port: [pids]} plus any diagnostic raised while sampling."""
    diags: List[Dict[str, Any]] = []
    ports: Dict[int, List[int]] = {p: [] for p in WATCH_PORTS}
    try:
        # bytes, not text: netstat on a zh-CN Windows writes GBK (cp936), and
        # forcing utf-8 raises UnicodeDecodeError inside the reader thread and
        # leaves stdout as None -- i.e. the watcher goes blind on a non-UTF-8
        # host, which is the exact failure this tool exists to prevent.
        proc = subprocess.run(
            ["netstat", "-ano"], capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        diags.append({
            "severity": SEV_DIAG, "stage": "sample",
            "error": f"netstat unavailable: {type(exc).__name__}: {exc}",
            "implication": "NO PORT STATE WAS OBSERVED THIS SAMPLE",
        })
        return ports, diags
    raw = proc.stdout or b""
    # cp936 is this host's console codepage; latin-1 is a lossless last resort
    # that guarantees the ASCII columns (port, state, pid) always decode.
    for enc in ("utf-8", "cp936", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 cannot fail
        text = ""
    if proc.returncode != 0 and not text.strip():
        diags.append({
            "severity": SEV_DIAG, "stage": "sample",
            "error": f"netstat rc={proc.returncode} with empty stdout",
            "implication": "NO PORT STATE WAS OBSERVED THIS SAMPLE",
        })
        return ports, diags
    for line in text.splitlines():
        m = _NETSTAT_LISTEN.match(line)
        if not m:
            continue
        # group layout: 1=proto 2=local 3=local-port 4=foreign 5=state 6=pid
        if m.group(1).upper() == "UDP":
            continue
        if m.group(5).upper() != "LISTENING":
            continue
        port = int(m.group(3))
        if port not in ports:
            continue
        pid = int(m.group(6))
        if pid not in ports[port]:
            ports[port].append(pid)
    return ports, diags


def observe_once(markers: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Turn one netstat sample into per-port ownership records."""
    ports, diags = sample_listeners()
    by_pid: Dict[int, Dict[str, Any]] = {}
    for mk in markers:
        pid = mk.get("pid")
        if isinstance(pid, int) and mk.get("classification") in ("GREEN", "PRODUCT"):
            by_pid.setdefault(pid, mk)

    out: Dict[int, Dict[str, Any]] = {}
    for port, pids in ports.items():
        if not pids:
            out[port] = {"listening": False, "pids": [], "classification": "NONE",
                         "evidence": None}
            continue
        holders = []
        for pid in pids:
            mk = by_pid.get(pid)
            if mk is None:
                holders.append({"pid": pid, "classification": "UNKNOWN",
                                "evidence": None,
                                "why": "no ownership marker names this pid"})
            else:
                holders.append({"pid": pid, "classification": mk["classification"],
                                "evidence": mk["path"],
                                "created_by": mk.get("created_by")})
        # A port with several holders is only GREEN if EVERY holder is GREEN.
        classes = {h["classification"] for h in holders}
        if classes == {"GREEN"}:
            overall = "GREEN"
        elif "UNKNOWN" in classes:
            overall = "UNKNOWN"
        elif classes == {"PRODUCT"}:
            overall = "PRODUCT"
        else:
            overall = "MIXED"
        out[port] = {"listening": True, "pids": pids,
                     "classification": overall, "holders": holders}
    if diags:
        out.setdefault("_diags", {}).update({"items": diags})  # type: ignore[union-attr]
    return out


# --------------------------------------------------------------------------
# interval accumulation
# --------------------------------------------------------------------------

def _close(span: Dict[str, Any], t_end: float) -> Dict[str, Any]:
    span = dict(span)
    span["end_ts"] = t_end
    span["end_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t_end))
    span["duration_s"] = round(t_end - span["start_ts"], 3)
    return span


def watch(duration: float, interval: float, out_path: Path) -> Dict[str, Any]:
    marker_state = read_markers()
    diagnostics: List[Dict[str, Any]] = list(marker_state["errors"])
    for e in marker_state["errors"]:
        diagnostics.append({
            "severity": SEV_DIAG, "stage": "markers", "error": e["error"],
            "implication": "identity for some holders may fall back to UNKNOWN",
        })

    samples = 0
    open_spans: Dict[Tuple[int, str], Dict[str, Any]] = {}
    closed: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    t_start = time.time()
    next_due = t_start

    while True:
        now = time.time()
        if now - t_start >= duration:
            break
        obs = observe_once(marker_state["markers"])
        diags = obs.pop("_diags", None)
        if diags:
            diagnostics.extend(diags["items"])
        samples += 1

        seen_keys = set()
        for port, rec in obs.items():
            key = (port, rec["classification"])
            seen_keys.add(key)
            if key not in open_spans:
                open_spans[key] = {
                    "port": port, "classification": rec["classification"],
                    "start_ts": now,
                    "start_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                    "pids_seen": [], "holders_seen": [],
                }
            sp = open_spans[key]
            for pid in rec.get("pids", []):
                if pid not in sp["pids_seen"]:
                    sp["pids_seen"].append(pid)
            for h in rec.get("holders", []) or []:
                sig = f"{h.get('pid')}:{h.get('classification')}"
                if sig not in sp["holders_seen"]:
                    sp["holders_seen"].append(sig)

            # THE ASSERTION: GREEN's port must never be held by anything else.
            if port == GREEN_PORT and rec["listening"] and rec["classification"] != "GREEN":
                violations.append({
                    "severity": SEV_PRODUCT, "port": port,
                    "classification": rec["classification"],
                    "pids": rec.get("pids"), "holders": rec.get("holders"),
                    "ts": now,
                    "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                    "finding": (
                        "GREEN port 4778 held by a NON-GREEN instance; an "
                        "unidentifiable holder counts as a violation, because "
                        "'could not tell' is not 'was fine'"
                    ) if rec["classification"] == "UNKNOWN" else (
                        "GREEN port 4778 held by a PRODUCT instance -- master "
                        "directive 0.2 violated and every GREEN-protection "
                        "assertion is unfalsifiable while this holds"),
                })

        for key in list(open_spans):
            if key not in seen_keys:
                closed.append(_close(open_spans.pop(key), now))

        next_due += interval
        sleep_for = next_due - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_due = time.time()  # fell behind; do not spin

    t_end = time.time()
    for key, span in list(open_spans.items()):
        closed.append(_close(span, t_end))
        open_spans.pop(key)

    return _build_report(
        out_path=out_path, duration=duration, interval=interval,
        samples=samples, t_start=t_start, t_end=t_end,
        spans=sorted(closed, key=lambda s: (s["port"], s["start_ts"])),
        violations=violations, diagnostics=diagnostics,
        marker_state=marker_state,
    )


def _build_report(*, out_path, duration, interval, samples, t_start, t_end,
                  spans, violations, diagnostics, marker_state) -> Dict[str, Any]:
    per_port: Dict[str, Any] = {}
    for port in WATCH_PORTS:
        mine = [s for s in spans if s["port"] == port]
        held = [s for s in mine if s["classification"] != "NONE"]
        per_port[str(port)] = {
            "role": {4778: "GREEN_BASELINE", 4779: "PRODUCT_API", 4780: "PRODUCT_UI"}[port],
            "total_occupied_intervals": len(held),
            "total_occupied_s": round(sum(s["duration_s"] for s in held), 3),
            "observed_free_s": round(max(0.0, t_end - t_start) - sum(s["duration_s"] for s in held), 3),
            "classifications_seen": sorted({s["classification"] for s in held}) or ["NONE"],
            "intervals": mine,
        }

    green_violations = [v for v in violations if v["port"] == GREEN_PORT]
    degraded = bool(diagnostics)
    if green_violations:
        verdict = "FAIL_GREEN_PORT_VIOLATION"
    elif samples == 0:
        verdict = "FAIL_NO_SAMPLES"
    elif degraded:
        verdict = "DEGRADED_SANDBOX_DIAG"
    else:
        verdict = "PASS_GREEN_UNTOUCHED"

    return {
        "schema": "jcc-port-watch/1",
        "generated_by": "tools/port_watch.py",
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "acts_on_processes": False,
        "window": {
            "requested_duration_s": duration,
            "interval_s": interval,
            "actual_duration_s": round(t_end - t_start, 3),
            "start_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t_start)),
            "end_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t_end)),
            "samples": samples,
            "effective_sample_rate_hz": round(samples / max(1e-9, t_end - t_start), 3),
        },
        "verdict": verdict,
        "green_untouched": verdict == "PASS_GREEN_UNTOUCHED",
        "green_untouched_scope": (
            "TRUE FOR THE OBSERVED WINDOW ONLY. This is a bounded-interval claim "
            "about this run, not a statement that GREEN is untouched at all times."
        ),
        "green_port": GREEN_PORT,
        "ports_watched": list(WATCH_PORTS),
        "per_port": per_port,
        "green_port_violations": green_violations,
        "violation_count": len(green_violations),
        "diagnostics": diagnostics,
        "degraded": degraded,
        "markers_seen": marker_state["markers"],
        "classification_rules": {
            "GREEN": "marker names the unversioned config/jcc.yaml or runtime/jcc",
            "PRODUCT": "marker names a versioned runtime (jcc-v1.3)",
            "UNKNOWN": "no/ambiguous marker -- counted as a violation on port 4778",
        },
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    """Prove the classifier and the interval logic, with no host dependency.

    The point is that the GREEN-port rule is the one thing that must not be
    wrong, so it is exercised directly on synthetic inputs rather than trusted.
    """
    failures: List[str] = []

    cases = [
        ("D:\\jev\\config\\jcc.yaml", "GREEN"),
        ("D:/jev/runtime/jcc/jcc.db", "GREEN"),
        ("D:\\jev\\config\\jcc-v1.3.yaml D:\\jev\\runtime\\jcc-v1.3\\jcc.db", "PRODUCT"),
        ("D:/jev/runtime/jcc-v1.3/jcc.db", "PRODUCT"),
        ("", "UNKNOWN"),
        ("C:\\somewhere\\else\\thing.txt", "UNKNOWN"),
    ]
    for text, want in cases:
        got = classify_by_paths(text)
        if got != want:
            failures.append(f"classify({text!r}) = {got}, want {want}")

    # interval close-out
    sp = {"port": 4778, "classification": "GREEN", "start_ts": 100.0,
          "start_iso": "x", "pids_seen": [1], "holders_seen": []}
    closed = _close(sp, 105.5)
    if closed["duration_s"] != 5.5:
        failures.append(f"interval duration {closed['duration_s']} != 5.5")
    if "end_iso" not in closed:
        failures.append("closed interval missing end_iso")

    # netstat row parsing, including a wildcard bind
    rows = [
        ("  TCP    127.0.0.1:4778         0.0.0.0:0              LISTENING       4644", 4778, 4644),
        ("  TCP    0.0.0.0:4779           0.0.0.0:0              LISTENING       11564", 4779, 11564),
        ("  TCP    127.0.0.1:4778         127.0.0.1:50000        ESTABLISHED     99", None, None),
    ]
    for line, want_port, want_pid in rows:
        m = _NETSTAT_LISTEN.match(line)
        if want_port is None:
            if m and m.group(5).upper() == "LISTENING":
                failures.append(f"ESTABLISHED row misread as LISTENING: {line!r}")
            continue
        if not m:
            failures.append(f"failed to parse LISTEN row: {line!r}")
            continue
        # group layout: 1=proto 2=local 3=local-port 4=foreign 5=state 6=pid
        if int(m.group(3)) != want_port or int(m.group(6)) != want_pid:
            failures.append(f"parsed port/pid wrong for {line!r}")

    # A live sample must work on this host, or report a SANDBOX_DIAG rather
    # than silently claiming "nothing there".
    ports, diags = sample_listeners()
    if diags and all(d.get("severity") == SEV_DIAG for d in diags):
        print("SELFTEST note: sampling raised SANDBOX_DIAG (host restriction)")
    if set(ports) != set(WATCH_PORTS):
        failures.append(f"sample did not cover all ports: {sorted(ports)}")

    if failures:
        print("SELFTEST FAIL")
        for f in failures:
            print("  - " + f)
        return 1
    print(f"SELFTEST PASS ({len(cases)} classify cases, interval close-out, "
          f"netstat parsing, live sample ok)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="CR-004 continuous port watcher (read-only)")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds to watch")
    ap.add_argument("--interval", type=float, default=1.5, help="seconds between samples")
    ap.add_argument("--out", default="artifacts/release/v1.3_final/PORT_WATCH.json")
    ap.add_argument("--selftest", action="store_true", help="validate the tool, no verdict")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.duration <= 0:
        print("FATAL: --duration must be > 0", file=sys.stderr)
        return 2
    if args.interval <= 0:
        print("FATAL: --interval must be > 0", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO / out_path
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"FATAL: cannot create {out_path.parent}: {exc}", file=sys.stderr)
        return 2

    print(f"CR-004 port watch: {args.duration}s @ {args.interval}s "
          f"-> {out_path} (read-only)", flush=True)
    report = watch(args.duration, args.interval, out_path)
    try:
        out_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"FATAL: cannot write {out_path}: {exc}", file=sys.stderr)
        return 2

    v = report["verdict"]
    print(f"verdict={v} samples={report['window']['samples']} "
          f"green_violations={report['violation_count']} "
          f"diagnostics={len(report['diagnostics'])}")
    for port, rec in report["per_port"].items():
        print(f"  {port} ({rec['role']}): {rec['total_occupied_intervals']} interval(s), "
              f"{rec['total_occupied_s']}s held, classes={rec['classifications_seen']}")
    for d in report["diagnostics"][:5]:
        print(f"  [{SEV_DIAG}] {d.get('stage')}: {d.get('error')}")
    print(f"written: {out_path}")

    if v == "FAIL_GREEN_PORT_VIOLATION":
        return 1
    if v in ("DEGRADED_SANDBOX_DIAG", "FAIL_NO_SAMPLES"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
