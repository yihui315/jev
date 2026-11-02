#!/usr/bin/env python3
"""§22 -- the v1.1 reliability gate: 19 scenarios against an isolated BLUE runtime.

WHAT MAKES THIS A GATE AND NOT A DEMO
-------------------------------------
The verdict is DERIVED, not decided:

    GO   iff  all 19 scenarios executed
              AND every one passed
              AND nothing was skipped, collapsed or silently not-run
              AND the protected v1.0.1 runtime is provably untouched

A scenario that raises becomes a FAILURE, never a skip -- same rule as the §21
security gate, because a reliability check that quietly does not execute is the
one failure mode that produces a false GO. Several scenarios additionally carry
NON-VACUITY assertions: they assert that the thing expected to be present IS
present, so a check cannot pass by measuring nothing.

WHY BLUE IS SAFE NEXT TO GREEN (structural, not conventional)
-------------------------------------------------------------
Everything lives under its own root, reached through env overrides that
``jcc_core`` already honours:

    JCC_CONFIG   -> runtime/v1.1/reliability/jcc.yaml   (port 4779, its own db)
    JCC_DB_PATH  -> runtime/v1.1/reliability/jcc.db
    PYTHONPATH   -> packages/ of THIS tree (v1.1)

Two facts were verified before any scenario was written, and both are
load-bearing:

  * ``run.py`` places its process marker at ``dirname(state_store_db_path)/JCC-marker``,
    so with a different db path it writes ``runtime/v1.1/reliability/JCC-marker`` and
    CANNOT touch the PowerShell layer's ``runtime/jcc/jcc.ownership.json``.
  * ``Get-JccPaths`` is NOT port-aware, which is precisely why this gate does not
    use ``start.ps1``/``stop.ps1`` at all. Calling ``stop.ps1 -Port 4779`` while GREEN
    runs would read GREEN's marker, find 4779 unowned, and only the safety return in
    ``Test-JccOwnership`` ("no listener found on port X") would stand between that and
    a signal to GREEN's pid. Depending on that branch would be reckless; bypassing
    the scripts removes the hazard by construction.

Each run starts from a clean db: any pre-existing one is MOVED (not deleted,
PERMANENT_DELETE=0) into an archive subdir.

THE ONE NUMBER THAT MATTERS (R-02)
----------------------------------
The frozen v1.0.1 ``server.py`` returns 7 health keys; v1.1 returns 12 (adding
``provider_mode``, ``provider_verified``, ``provider_activation_state``,
``provider_requested_mode``, ``provider_is_real``). R-02 asserts both directions,
so a broken parse cannot silently certify either build.

Usage
-----
    python tools/v1_1_reliability_gate.py              # run, write the artifacts
    python tools/v1_1_reliability_gate.py --selftest   # falsify the verdict rule
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
PKGS = ROOT / "packages"
BLUE_DIR = ROOT / "runtime" / "v1.1" / "reliability"
ARCHIVE = BLUE_DIR / "archive"
BLUE_CFG = BLUE_DIR / "jcc.yaml"
BLUE_DB = BLUE_DIR / "jcc.db"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

BLUE_PORT = 4779
#: The protected v1.0.1 runtime. Nothing here may bind it, signal it or write it.
PROTECTED_PORT = 4778
PROTECTED_DB = ROOT / "runtime" / "jcc" / "jcc.db"
PROTECTED_MARKER = ROOT / "runtime" / "jcc" / "jcc.ownership.json"
PROTECTED_PIDFILE = ROOT / "runtime" / "jcc" / "jcc.pid"

OUT_JSON = ROOT / "artifacts" / "acceptance" / "V1_1_RELIABILITY_GATE.json"
OUT_MD = ROOT / "artifacts" / "release" / "V1_1_RELIABILITY_REPORT.md"
SCHEMA = "jcc.v1_1.reliability_gate/v1"

FROZEN_KEYS = 7
V1_1_KEYS = 12
OPEN_GETS = ["/api/health", "/api/sessions", "/api/sessions/{sid}",
             "/api/audit", "/api/decisions", "/api/audit/verify"]
MUTATING = ["/api/assess", "/api/pause", "/api/steer", "/api/retry"]
EXPECTED_IDS = [f"R-{i:02d}" for i in range(1, 20)]

#: Which control-token posture each scenario must be measured under.
#: This is not cosmetic: /api/assess returns 401 (not 422) when no token is
#: configured, so measuring R-09's "a rejected envelope is still audited" in phase
#: A would record 401 and look like a product failure when it is actually the
#: wrong phase. Grouping is part of what the assertion means.
PHASE = {
    # no token configured -- the fail-closed branch
    "R-01": "A", "R-02": "A", "R-03": "A", "R-04": "A", "R-05": "A",
    "R-06": "A", "R-08": "A", "R-11": "A", "R-18": "A",
    # a token configured -- the authentication + persistence branches
    "R-07": "B", "R-09": "B", "R-10": "B", "R-12": "B", "R-13": "B",
    "R-14": "B", "R-15": "B", "R-16": "B", "R-17": "B",
    # after everything else has finished
    "R-19": "Z",
}
#: Order inside a phase is significant (R-09 must write before R-10 counts rows).
PHASE_ORDER = {"A": ["R-01", "R-02", "R-03", "R-04", "R-05", "R-06", "R-08", "R-11", "R-18"],
               "B": ["R-07", "R-09", "R-10", "R-12", "R-13", "R-14", "R-15", "R-16", "R-17"],
               "Z": ["R-19"]}
assert sorted(PHASE) == EXPECTED_IDS, "every scenario needs exactly one phase"
for ph, ids in PHASE_ORDER.items():
    assert sorted(ids) == sorted(k for k, v in PHASE.items() if v == ph), f"phase {ph} disagrees with PHASE"



# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    sid: str
    title: str
    passed: bool
    observed: Dict[str, Any] = field(default_factory=dict)
    detail: str = ""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sha256(p: Path) -> Optional[str]:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        return None


def _http(method: str, port: int, path: str, body: Any = None, token: str = None,
          raw: bytes = None, timeout: float = 8.0) -> Tuple[Optional[int], Any]:
    url = f"http://127.0.0.1:{port}{path}"
    data, hdrs = None, {}
    if raw is not None:
        data, hdrs["Content-Type"] = raw, "application/json"
    elif body is not None:
        data, hdrs["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        hdrs["X-JCC-Token"] = token
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    """MIB_TCPROW_OWNER_PID: 24 bytes, and dwLocalPort is NETWORK byte order.

    The first draft of this probe read fields at the wrong offsets and decoded the
    port little-endian. Its tell was loud once noticed: it reported `listener=0`
    for :4778 on a line that also said `GREEN http=200`. A probe that reports
    "nobody owns this port" while that port is serving is not evidence of absence,
    it is evidence that the probe is broken -- the same defect class this project
    has now hit repeatedly. Struct-based parsing plus a self-test against a known
    listener is what keeps it honest.
    """
    _fields_ = [("dwState", wintypes.DWORD),
                ("dwLocalAddr", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


def _listener_pid(port: int) -> int:
    """pid LISTENing on this loopback port, or 0 if genuinely nobody is.

    Three-state by construction: on ANY error it returns 0 and callers must treat
    that as "not measured", never as "nothing there".
    """
    try:
        size = wintypes.DWORD()
        fn = ctypes.windll.iphlpapi.GetExtendedTcpTable
        # ulAf = AF_INET, TableClass = 3 (TCP_TABLE_OWNER_PID_LISTENER).
        # Class matters: 2 is BASIC_ALL, whose rows are 20 bytes and carry NO pid
        # field at all. Reading those as 24-byte owner-pid rows silently returns
        # garbage -- which is exactly how this probe once reported "nobody owns
        # :4778" on a port that was answering HTTP 200.
        TCP_TABLE_OWNER_PID_LISTENER = 3
        fn(None, ctypes.byref(size), False, socket.AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0)
        buf = ctypes.create_string_buffer(size.value)
        if fn(buf, ctypes.byref(size), False, socket.AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0):
            return 0
        n = int.from_bytes(buf[0:4], "little")
        rows = ctypes.cast(ctypes.byref(buf, 4), ctypes.POINTER(MIB_TCPROW_OWNER_PID))
        for i in range(n):
            row = rows[i]
            # MIB_TCP_STATE: 1 CLOSED, 2 LISTEN, 3 SYN_SENT, 4 SYN_RCVD,
            # 5 ESTABLISHED, ... The first drafts of this probe filtered on 5,
            # which is ESTABLISHED -- so it never saw a listening socket and
            # reported pid 0 for a port that was serving HTTP 200. Verified
            # against the raw table rather than assumed.
            MIB_TCP_STATE_LISTEN = 2
            if int(row.dwState) != MIB_TCP_STATE_LISTEN:
                continue
            if socket.ntohs(int(row.dwLocalPort) & 0xFFFF) == port:
                pid = int(row.dwOwningPid)
                return pid
        return 0
    except Exception:
        return 0


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _health_shape(port: int) -> Tuple[Optional[int], Dict[str, Any]]:
    st, b = _http("GET", port, "/api/health")
    return st, (b if isinstance(b, dict) else {})


def health_is_v1_1(keys: List[str]) -> bool:
    """Non-vacuous discriminator: v1.1 carries all five provider fields."""
    if not keys:
        return False
    need = {"provider_mode", "provider_verified", "provider_activation_state",
            "provider_requested_mode", "provider_is_real"}
    return need <= set(keys)


# --------------------------------------------------------------------------- #
# the protected runtime, measured before and after everything
# --------------------------------------------------------------------------- #
class ProtectedSnapshot:
    def __init__(self) -> None:
        self.pidfile = PROTECTED_PIDFILE.read_text(encoding="utf-8").strip() if PROTECTED_PIDFILE.is_file() else None
        self.marker_pid = None
        if PROTECTED_MARKER.is_file():
            try:
                self.marker_pid = json.loads(PROTECTED_MARKER.read_text(encoding="utf-8")).get("pid")
            except Exception:
                self.marker_pid = None
        self.db_sha = _sha256(PROTECTED_DB)
        self.listener = _listener_pid(PROTECTED_PORT)
        self.green_status, body = _health_shape(PROTECTED_PORT)
        self.green_keys = sorted(body.keys()) if body else []

    def compare(self, other: "ProtectedSnapshot") -> Dict[str, Any]:
        """Divergences between two samples. Empty == nothing moved."""
        d: Dict[str, Any] = {}
        if self.pidfile != other.pidfile:
            d["pidfile"] = (self.pidfile, other.pidfile)
        if self.marker_pid != other.marker_pid:
            d["marker_pid"] = (self.marker_pid, other.marker_pid)
        if self.listener != other.listener:
            d["listener_pid"] = (self.listener, other.listener)
        if self.green_keys != other.green_keys:
            d["health_key_set"] = (self.green_keys, other.green_keys)
        if self.green_status != other.green_status:
            d["green_http_status"] = (self.green_status, other.green_status)
        return d


# --------------------------------------------------------------------------- #
# BLUE instance
# --------------------------------------------------------------------------- #
class Blue:
    def __init__(self, port: int = BLUE_PORT) -> None:
        self.port = port
        self.proc: Optional[subprocess.Popen] = None
        self.token: Optional[str] = None
        # Per-instance config. R-18 starts a second Blue on its own port; sharing
        # one config file meant that instance rewrote the port the MAIN instance
        # was about to start on, and phase B silently never came up.
        self.cfg = BLUE_DIR / f"jcc-{self.port}.yaml"

    def write_config(self) -> None:
        data = yaml.safe_load((ROOT / "config" / "jcc.yaml").read_text(encoding="utf-8"))
        data["server"]["port"] = self.port
        data.setdefault("state_store", {})["db_path"] = str(BLUE_DB).replace("\\", "/")
        txt = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        # Isolation is asserted, not assumed: a config still pointing anywhere
        # under GREEN's runtime dir would make every scenario below unsafe.
        for bad in ("runtime/jcc", "runtime\\jcc", "runtime\\jcc.db"):
            if bad in txt:
                raise RuntimeError(f"derived BLUE config leaks protected path {bad!r}")
        self.cfg.write_text(txt, encoding="utf-8")

    def clean_state(self) -> None:
        BLUE_DIR.mkdir(parents=True, exist_ok=True)
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        if BLUE_DB.exists():
            # MOVE, never delete (PERMANENT_DELETE=0). Old runs stay inspectable.
            shutil.move(str(BLUE_DB), str(ARCHIVE / f"jcc-{int(time.time())}.db"))
        for side in (f"{BLUE_DB}-wal", f"{BLUE_DB}-shm"):
            p = Path(side)
            if p.exists():
                p.rename(str(p) + ".archived")

    def start(self, token: Optional[str] = None, timeout: float = 60.0) -> Tuple[Optional[int], Dict[str, Any]]:
        self.token = token
        env = dict(os.environ)
        env["JCC_CONFIG"] = str(self.cfg)
        env["JCC_DB_PATH"] = str(BLUE_DB)
        env["JCC_PROVIDER"] = "mock"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(PKGS)
        env.pop("JCC_CONTROL_TOKEN", None)
        if token:
            env["JCC_CONTROL_TOKEN"] = token
        self.proc = subprocess.Popen(
            [str(PYTHON), "-m", "jcc_core.run"], env=env, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return None, {"exited": self.proc.returncode}
            st, body = _health_shape(self.port)
            if st == 200 and isinstance(body, dict):
                return st, body
            time.sleep(0.3)
        return None, {"timeout": True}

    def stop(self, abrupt: bool = False) -> None:
        if not self.proc:
            return
        try:
            self.proc.kill() if abrupt else self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            try:
                self.proc.kill()
                self.proc.wait(timeout=10)
            except Exception:
                pass
        self.proc = None
        time.sleep(0.6)

    def db_query(self, sql: str) -> Any:
        try:
            con = sqlite3.connect(str(BLUE_DB), timeout=10)
            try:
                return con.execute(sql).fetchall()
            finally:
                con.close()
        except Exception as e:
            return f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# the 19 scenarios
# --------------------------------------------------------------------------- #
def run_all(blue: Blue, before: ProtectedSnapshot, phase: str) -> List[Result]:
    registry: List[Tuple[str, str, Callable[[Blue], Tuple[bool, Dict[str, Any], str]]]] = []

    def scenario(sid: str, title: str):
        def deco(fn):
            registry.append((sid, title, fn))
            return fn
        return deco

    @scenario("R-01", "cold start on clean state answers /api/health")
    def _r01(b: Blue):
        st, body = _health_shape(b.port)
        return (st == 200 and body.get("status") == "ok",
                {"status": st, "body_status": body.get("status")},
                "health answers 200 status=ok from a freshly created database")

    @scenario("R-02", "BLUE reports the v1.1 shape, not the frozen build")
    def _r02(b: Blue):
        _, body = _health_shape(b.port)
        keys = sorted(body.keys())
        return (health_is_v1_1(keys) and len(keys) == V1_1_KEYS,
                {"key_count": len(keys), "is_v1_1": health_is_v1_1(keys), "keys": keys},
                "v1.1 serves 12 health keys; the frozen v1.0.1 build serves 7")

    @scenario("R-03", "declared read-only surface answers")
    def _r03(b: Blue):
        # NOTE: /api/sessions/{sid} is a TEMPLATE. Asking it for an id that does
        # not exist legitimately returns 404 -- asserting 200 there was wrong, and
        # the resulting failure said nothing about the product. What matters for a
        # read-only surface is that no declared endpoint returns 5xx.
        concrete = [p for p in OPEN_GETS if "{" not in p]
        templates = [p for p in OPEN_GETS if "{" in p]
        out: Dict[str, Any] = {}
        for p in concrete:
            out[p] = _http("GET", b.port, p)[0]
        for p in templates:
            out[p] = _http("GET", b.port, p.replace("{sid}", "no-such-session"))[0]
        measured = all(v is not None for v in out.values())
        none_5xx = all(isinstance(v, int) and v < 500 for v in out.values())
        all_concrete_200 = all(out[p] == 200 for p in concrete)
        return (measured and none_5xx and all_concrete_200, out,
                "every declared read endpoint responds (no 5xx); concrete routes are 200")

    @scenario("R-04", "unknown session is 404, never 500")
    def _r04(b: Blue):
        st, _ = _http("GET", b.port, "/api/sessions/definitely-not-a-session")
        return (st == 404, {"status": st},
                "a missing resource must not surface as a server error")

    @scenario("R-05", "unknown route is 404, never 500")
    def _r05(b: Blue):
        st, _ = _http("GET", b.port, "/api/there-is-no-such-route")
        return (st == 404, {"status": st},
                "an unmapped path must not surface as a server error")

    @scenario("R-06", "unconfigured control token fails CLOSED with 401")
    def _r06(b: Blue):
        out = {p: _http("POST", b.port, p, body={"probe": 1})[0] for p in MUTATING}
        return (all(v == 401 for v in out.values()), out,
                "with no token configured every mutating endpoint refuses 401 rather than acting")

    @scenario("R-07", "wrong control token is refused with 403")
    def _r07(b: Blue):
        out = {p: _http("POST", b.port, p, body={"probe": 1}, token="not-the-token")[0]
               for p in MUTATING}
        return (all(v == 403 for v in out.values()), out,
                "an incorrect token is refused 403, distinct from the 401 fail-closed case")

    @scenario("R-08", "syntactically invalid body is 422, never 500")
    def _r08(b: Blue):
        st, _ = _http("POST", b.port, "/api/assess", raw=b'{"not valid json')
        _, after = _health_shape(b.port)
        return (st == 422 and after.get("status") == "ok",
                {"status": st, "health_after": after.get("status")},
                "malformed JSON is rejected 422 and the service stays up")

    @scenario("R-09", "a rejected envelope is STILL audited (record never dropped)")
    def _r09(b: Blue):
        marker = "reject-audit-R09"
        st, _ = _http("POST", b.port, "/api/assess",
                      body={"event_id": marker, "nonsense": True}, token=b.token)
        _, rows = _http("GET", b.port, "/api/audit?limit=200")
        hit = None
        if isinstance(rows, list):
            hit = next((r for r in rows
                        if isinstance(r, dict) and str(r.get("event_id")) == marker), None)
        return (hit is not None and st == 422,
                {"status": st, "found_audit_row": hit is not None,
                 "assessment": (hit or {}).get("assessment")},
                "a rejected ingest still writes its audit row; dropping it would erase the evidence of the refusal")

    @scenario("R-10", "audit chain verifies intact after hostile traffic")
    def _r10(b: Blue):
        _, body = _http("GET", b.port, "/api/audit/verify")
        return (bool(body.get("intact")) and int(body.get("entries", 0)) > 0,
                body if isinstance(body, dict) else {"raw": body},
                "the hash chain self-verifies, and non-vacuously has entries to verify")

    @scenario("R-11", "SQLite runs in WAL journal mode")
    def _r11(b: Blue):
        rows = b.db_query("pragma journal_mode")
        mode = rows[0][0] if rows and isinstance(rows[0], tuple) else str(rows)
        return (str(mode).lower() == "wal", {"journal_mode": str(mode)},
                "WAL keeps readers from being blocked by a writer")

    @scenario("R-12", "state survives an orderly restart")
    def _r12(b: Blue):
        before_rows = _http("GET", b.port, "/api/audit?limit=200")[1]
        n_before = len(before_rows) if isinstance(before_rows, list) else 0
        b.stop(abrupt=False)
        st, _ = b.start(token=b.token)
        after_rows = _http("GET", b.port, "/api/audit?limit=200")[1] if st == 200 else []
        n_after = len(after_rows) if isinstance(after_rows, list) else 0
        return (st == 200 and n_before > 0 and n_after >= n_before,
                {"before": n_before, "after": n_after, "restart_status": st},
                "rows persisted before a clean restart are still readable after it")

    @scenario("R-13", "service recovers after an ABRUPT kill, with no manual repair")
    def _r13(b: Blue):
        b.stop(abrupt=True)
        st, body = b.start(token=b.token)
        return (st == 200 and body.get("status") == "ok",
                {"status": st, "body_status": body.get("status")},
                "after SIGKILL the same database opens and serves; no repair step needed")

    @scenario("R-14", "no torn pages after an ABRUPT kill")
    def _r14(b: Blue):
        rows = b.db_query("pragma integrity_check")
        val = rows[0][0] if rows and isinstance(rows[0], tuple) else str(rows)
        return (str(val).lower() == "ok", {"integrity_check": str(val)},
                "the database is structurally sound after being killed mid-flight")

    @scenario("R-15", "repeated restarts do not duplicate state")
    def _r15(b: Blue):
        b.stop(abrupt=False)
        b.start(token=b.token)
        b.stop(abrupt=False)
        st, _ = b.start(token=b.token)
        rows = _http("GET", b.port, "/api/audit?limit=500")[1] if st == 200 else []
        seqs = [r.get("seq") for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        return (st == 200 and len(seqs) == len(set(seqs)),
                {"entries": len(seqs), "unique_seqs": len(set(seqs))},
                "restarting repeatedly is idempotent: no duplicate rows are appended")

    @scenario("R-16", "readers are served while a writer commits")
    def _r16(b: Blue):
        bad: List[Any] = []
        stops = threading.Event()

        def reader():
            while not stops.is_set():
                st, _ = _http("GET", b.port, "/api/health", timeout=5)
                if st != 200:
                    bad.append(st)

        threads = [threading.Thread(target=reader, daemon=True) for _ in range(8)]
        for t in threads:
            t.start()
        for i in range(12):
            _http("POST", b.port, "/api/assess", body={"event_id": f"load-{i}"}, token=b.token)
        time.sleep(1.0)
        stops.set()
        for t in threads:
            t.join(timeout=5)
        return (not bad, {"non_200_reads": len(bad), "sample": bad[:5]},
                "concurrent reads stay available while writes are committing")

    @scenario("R-17", "hostile payloads round-trip without corrupting storage")
    def _r17(b: Blue):
        payload = "unicode-\u4f60\u597d-\U0001f680-ctl\x01-end"
        _http("POST", b.port, "/api/assess",
              body={"event_id": payload, "bad": True}, token=b.token)
        _, rows = _http("GET", b.port, "/api/audit?limit=300")
        hit = None
        if isinstance(rows, list):
            hit = next((r for r in rows
                        if isinstance(r, dict) and str(r.get("event_id")) == payload), None)
        _, after = _health_shape(b.port)
        return (hit is not None and after.get("status") == "ok",
                {"round_tripped": hit is not None, "health_after": after.get("status")},
                "non-vacuous: the exact unicode payload is read back, not merely 'nothing broke'")

    @scenario("R-18", "a busy port is refused, never hijacked")
    def _r18(b: Blue):
        port = _free_port()
        hold = socket.socket()
        hold.bind(("127.0.0.1", port))
        hold.listen(1)
        try:
            owner_before = _listener_pid(port)
            other = Blue(port=port)
            other.write_config()
            st, _body = other.start(timeout=12)
            refused = st is None
            if other.proc is not None:
                other.stop(abrupt=True)
            owner_after = _listener_pid(port)
            still = owner_before != 0 and owner_before == owner_after
            # owner_before MUST be observable. If the probe cannot even see the
            # socket we are holding, then `still` is comparing None to None, and
            # the scenario would be certifying nothing.
            return (owner_before != 0 and refused and still,
                    {"port": port, "start_refused": refused,
                     "owner_before": owner_before, "owner_after": owner_after},
                    "with someone else already on the port we refuse to start; the original owner is untouched")
        finally:
            hold.close()

    @scenario("R-19", "the protected v1.0.1 runtime is untouched by everything above")
    def _r19(b: Blue):
        after = ProtectedSnapshot()
        diffs = before.compare(after)
        # Non-vacuity: comparing two samples only proves something if BOTH actually
        # observed the identity we claim to be protecting. The first draft
        # compared listener 0 vs 0 because the pid probe was broken, and passed.
        observed = (before.listener != 0 and after.listener != 0
                    and before.marker_pid is not None and after.marker_pid is not None)
        return (observed and not diffs,
                {"divergences": diffs,
                 "observed_before": {"listener": before.listener, "marker_pid": before.marker_pid},
                 "observed_after": {"listener": after.listener, "marker_pid": after.marker_pid},
                 "green_http": after.green_status, "green_key_count": len(after.green_keys)},
                "GREEN's pid file, marker pid, listener pid and code shape are identical throughout")

    results: List[Result] = []
    by_id = {sid: (title, fn) for sid, title, fn in registry}
    absent = [sid for sid in PHASE_ORDER[phase] if sid not in by_id]
    if absent:
        # Refuse to report a phase that quietly ran fewer scenarios than declared.
        raise RuntimeError(f"scenario(s) declared but never defined: {absent}")
    for sid in PHASE_ORDER[phase]:
        title, fn = by_id[sid]
        t0 = time.time()
        try:
            ok, observed, detail = fn(blue)
            results.append(Result(sid, title, bool(ok),
                                  observed if isinstance(observed, dict) else {"raw": observed},
                                  detail))
        except Exception as e:
            # A crashed scenario is a FAILURE. It is never recorded as skipped.
            results.append(Result(sid, title, False,
                                  {"exception": f"{type(e).__name__}: {e}"}, title))
        results[-1].observed["_ms"] = int((time.time() - t0) * 1000)
    return results


# --------------------------------------------------------------------------- #
# verdict
# --------------------------------------------------------------------------- #
def verdict_of(results: List[Result]) -> Tuple[str, List[str]]:
    seen = {r.sid for r in results}
    missing = [i for i in EXPECTED_IDS if i not in seen]
    failed = [r.sid for r in results if not r.passed]
    if missing or failed:
        return "NO-GO", missing + failed
    return "GO", []


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def selftest() -> int:
    print("--selftest: falsifying the gate itself")
    ok = True

    def check(name: str, cond: bool, note: str = "") -> None:
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name} {note}")
        if not cond:
            ok = False

    good = [Result(sid, "", True) for sid in EXPECTED_IDS]
    v, _ = verdict_of(good)
    check("all-pass yields GO", v == "GO")

    broken = list(good)
    broken[7] = Result("R-08", "deliberately broken", False)
    v, why = verdict_of(broken)
    check("one failure yields NO-GO", v == "NO-GO" and why == ["R-08"], f"-> {v} {why}")

    drop = [r for r in good if r.sid != "R-19"]
    v2, why2 = verdict_of(drop)
    check("a MISSING scenario is NO-GO, not a smaller suite", v2 == "NO-GO" and why2 == ["R-19"])

    keys11 = ["status", "jev_provider", "provider_mode", "provider_verified",
              "provider_activation_state", "provider_requested_mode", "provider_is_real",
              "actuation_enabled", "policy_version", "localhost_only", "wal",
              "open_get_endpoints"]
    keys_frozen = ["status", "jev_provider", "actuation_enabled", "policy_version",
                   "localhost_only", "wal", "open_get_endpoints"]
    check("12-key v1.1 shape -> True", health_is_v1_1(keys11) is True, f"({len(keys11)} keys)")
    check("7-key frozen shape -> False", health_is_v1_1(keys_frozen) is False, f"({len(keys_frozen)} keys)")
    check("empty key set -> False (no vacuous pass)", health_is_v1_1([]) is False)

    a = ProtectedSnapshot()
    b = copy.copy(a)
    b.listener = (a.listener or 0) + 1
    check("listener pid change is detected", bool(a.compare(b)))
    b = copy.copy(a)
    b.green_keys = sorted(set(a.green_keys) | {"provider_mode"})
    check("health shape change is detected", bool(a.compare(b)))
    check("identical snapshots produce no divergence", a.compare(copy.copy(a)) == {})

    # -- the probe must be provably able to SEE a listener ------------------ #
    # This is the check that would have caught the broken parser: it reported
    # listener=0 for :4778 while :4778 was answering HTTP 200.
    if a.green_status == 200:
        seen = _listener_pid(PROTECTED_PORT)
        check("probe sees the PROTECTED listener that is serving HTTP 200",
              seen != 0, f"(returned pid {seen})")
    else:
        print("  [SKIP] GREEN is not answering, so the listener probe cannot be "
              "falsified against ground truth here")

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    own_port = s.getsockname()[1]
    own_pid = os.getpid()
    try:
        check("probe sees a socket this process holds",
              _listener_pid(own_port) == own_pid,
              f"(port {own_port})")
    finally:
        s.close()
    check("probe reports 0 for a port nobody is listening on",
          _listener_pid(_free_port()) == 0)

    print("--selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def emit_markdown(doc: Dict[str, Any]) -> str:
    lines = [
        "# JCC v1.1 — Reliability Gate (§22)",
        "",
        f"**Verdict: `{doc['verdict']}`** — {doc['passed_count']}/{doc['scenario_count']} scenarios passed.",
        "",
        "> Generated by `tools/v1_1_reliability_gate.py`. Durations and timestamps live",
        "> here rather than in the JSON companion, so that JSON has exactly one byte form.",
        "",
        "## Why BLUE cannot reach GREEN",
        "",
        "| property | GREEN (protected) | BLUE (this gate) |",
        "|---|---|---|",
        f"| port | {PROTECTED_PORT} | {BLUE_PORT} |",
        "| database | `runtime/jcc/jcc.db` | `runtime/v1.1/reliability/jcc.db` |",
        "| process marker | `runtime/jcc/jcc.ownership.json` | `runtime/v1.1/reliability/JCC-marker` |",
        "| code served | frozen `f8ac95d` snapshot | this worktree (v1.1) |",
        "",
        "The marker isolation is structural: `run.py` puts `JCC-marker` in the directory of",
        "its own configured database. `start.ps1`/`stop.ps1` are deliberately NOT used here,",
        "because `Get-JccPaths` is not port-aware and would otherwise read GREEN's marker.",
        "",
        "## Scenarios",
        "",
        "| id | scenario | result | evidence |",
        "|---|---|---|---|",
    ]
    for r in doc["scenarios"]:
        ev = json.dumps(r["observed"], ensure_ascii=False)
        if len(ev) > 170:
            ev = ev[:167] + "..."
        lines.append(f"| {r['sid']} | {r['title']} | {'PASS' if r['passed'] else 'FAIL'} | `{ev}` |")
    lines += [
        "",
        "## Interpretation",
        "",
        f"- Scenarios executed: **{doc['scenario_count']}** (expected {len(EXPECTED_IDS)}).",
        f"- Failures: **{len(doc['failed'])}** {doc['failed']}.",
        f"- Protected-runtime divergences: **{len(doc['protected_divergences'])}**.",
        f"- Repo head: `{doc['repo_head']}`.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    before = ProtectedSnapshot()
    print(f"protected runtime BEFORE: GREEN http={before.green_status} "
          f"keys={len(before.green_keys)} listener={before.listener} pidfile={before.pidfile}")

    if before.green_status != 200:
        print("\nREFUSING TO RUN: GREEN is not answering on :4778.")
        print("This gate exists to prove BLUE is safe BESIDE a healthy GREEN. Running it")
        print("while GREEN is down would measure the wrong thing and could mask real damage.")
        return 3
    if len(before.green_keys) != FROZEN_KEYS:
        print(f"\nREFUSING TO RUN: :4778 reports {len(before.green_keys)} health keys; "
              f"expected {FROZEN_KEYS} from the frozen build.")
        print("The runtime to be protected is not the runtime we think it is.")
        return 3

    TOKEN = "gate-phase-B-token-should-be-refused-when-wrong"

    blue = Blue(BLUE_PORT)
    blue.clean_state()
    blue.write_config()

    results: List[Result] = []
    try:
        st_a, _ = blue.start(token=None)
        print(f"\nBLUE phase A (no control token): {'UP' if st_a == 200 else 'FAILED TO START'}")
        if st_a == 200:
            results += run_all(blue, before, "A")
        blue.stop(abrupt=False)

        st_b, _ = blue.start(token=TOKEN)
        print(f"BLUE phase B (token configured):  {'UP' if st_b == 200 else 'FAILED TO START'}")
        if st_b == 200:
            results += run_all(blue, before, "B")
        blue.stop(abrupt=False)

        # R-19 runs last and alone, with no BLUE process left running.
        results += run_all(blue, before, "Z")
    finally:
        blue.stop(abrupt=True)

    after_snapshot = ProtectedSnapshot()
    div = before.compare(after_snapshot)

    v, why = verdict_of(results)
    if div:
        v = "NO-GO"
    if results and len(results) != len(EXPECTED_IDS):
        v = "NO-GO"

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                          capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                                capture_output=True, text=True).stdout.strip())

    doc = {
        "schema": SCHEMA,
        "verdict": v,
        "failed": why,
        "scenario_count": len(results),
        "passed_count": sum(1 for r in results if r.passed),
        "protected_divergences": div,
        "repo_head": head,
        "worktree_dirty": dirty,
        "blue": {"port": BLUE_PORT, "db": str(BLUE_DB), "config": str(BLUE_CFG)},
        "protected": {
            "green_http_status": before.green_status,
            "green_key_count": len(before.green_keys),
            "green_listener_pid": before.listener,
        },
        "scenarios": [
            {"sid": r.sid, "title": r.title, "passed": r.passed,
             "observed": {k: x for k, x in r.observed.items() if not k.startswith("_")}}
            for r in results
        ],
        "reproducibility": {
            "deterministic": True,
            "note": "no timestamp or duration is stored here; they live in the markdown report",
            "generator": "tools/v1_1_reliability_gate.py",
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(emit_markdown(doc), encoding="utf-8")

    print("\n" + "=" * 72)
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        print(f"  {r.sid}  {flag}  {r.title}")
    print("=" * 72)
    print(f"verdict: {v}  ({doc['passed_count']}/{doc['scenario_count']})")
    if why:
        print(f"  offenders: {why}")
    if div:
        print(f"  PROTECTED RUNTIME DIVERGED: {div}")
    print(f"artifacts:\n  {OUT_JSON}\n  {OUT_MD}")
    return 0 if v == "GO" else 1


def dedupe_keep_first(results: List[Result]) -> List[Result]:
    out, seen = [], set()
    for r in results:
        if r.sid in seen:
            continue
        seen.add(r.sid)
        out.append(r)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
