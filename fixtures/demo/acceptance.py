"""Acceptance artifact generator for Demo Mode (S-05).

Demo Mode claims three things, and this generator measures all three:

1. **Determinism.** It replays the four-agent fixture set into two INDEPENDENT
   fresh databases and digests the derived chain (assessments, actions,
   directives, audit hashes) both times. The two digests must match. A fixture
   set that is merely *fixed on disk* but produces a different chain per run is
   not reproducible evidence, and asserting "the fixtures are deterministic"
   without replaying them twice is the kind of claim this artifact exists to
   falsify.

2. **Coverage.** All four agents (workbuddy / claude / codex / hermes) must
   appear in the derived sessions, not just in the manifest.

3. **It cannot be mistaken for a real agent.** The chain must produce only inert
   directives (``acted == false``), and the DEMO label must be rendered on the
   screens an operator would actually look at -- fetched over real HTTP from a
   real UI in front of a real control plane.

Writes ``artifacts/acceptance/demo_e2e.json``::

    {"schema": ..., "provenance": {...}, "agents_covered": [...],
     "deterministic_replay": {"ok": true, "run1_digest": ..., "run2_digest": ...},
     "all_directives_inert": true, "demo_label_pages": [...],
     "screens": [...], "passed": true, "status": "PASS"}

WHY THERE IS NO TIMESTAMP: this artifact is consumed by the final gate as E-01
and S-05 evidence, and a wall-clock field would make the gate's own clean-tree
provenance check unsatisfiable. The payload is a function of the fixture DATA and
the product SOURCE, so re-running it over the same bytes yields the same bytes.

Run::

    F:/jev/.venv/Scripts/python -m fixtures.demo.acceptance
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

# --- importability (same bootstrap the Control UI uses) --------------------- #
HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
for _p in (str(REPO), str(REPO / "apps"), str(REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from control_ui._harness import (  # noqa: E402
    build_app,
    find_free_port,
    get,
    start_api_server,
    start_ui_server,
    stop_api_server,
    stop_ui_server,
)
from fixtures.demo.load import is_demo_run, load_demo_envelopes  # noqa: E402
from jcc_core.schemas import AEPEnvelope  # noqa: E402

DEMO_AGENTS = ("workbuddy", "claude", "codex", "hermes")
#: Pages an operator would look at to decide "is this real?". The DEMO label must
#: be on every one of them.
DEMO_LABEL_PAGES = {
    "dashboard": "/",
    "sessions": "/sessions",
    "session_detail": None,       # filled in with the first demo session id
    "decisions": "/decisions",
    "audit": "/audit",
}
UNREACHABLE_MARKER = "API UNREACHABLE"

#: Fields the ingest path derives from the WALL CLOCK of the ingest process, and
#: which therefore cannot be reproduced by replaying the fixture data.
#:
#: This declaration is the honest half of the determinism claim. The fixture
#: manifest used to say a replay "reproduces byte-identical assessments,
#: directives and audit rows" -- measurement on 2026-09-26 showed that is FALSE:
#: ``packages/jcc_core/core.py`` line 223 takes ``now = time.time()`` and passes
#: it to the directive (line 297), to the session's ``updated_at`` (line 327) and
#: to the audit tape (line 340); the tape's ``entry_hash`` (``audit.py`` line 185)
#: hashes that timestamp, so every audit hash differs between two replays.
#:
#: WHY THE PRODUCT IS NOT CHANGED TO FIX THIS. The obvious "fix" is to feed the
#: event's own timestamp into ``now``. That would be a REGRESSION: on the live
#: plane the audit ``timestamp`` answers "when did this system see this event",
#: and a late-delivered event must not backdate its own audit row -- that is a
#: tamper-evidence property, traded away for demo neatness. The non-determinism
#: is therefore declared, bounded and checked here rather than removed.
#:
#: The declaration is load-bearing: a replay that differs ANYWHERE ELSE is a
#: FAIL, so a newly introduced wall-clock dependency cannot hide behind it.
VOLATILE_FIELDS = {
    "session.updated_at": {
        "origin": "packages/jcc_core/core.py",
        "assigned_at_line": 327,
        "expression": "sess.updated_at = now",
        "why": "ingest process wall clock",
    },
    "directive.timestamp": {
        "origin": "packages/jcc_core/core.py",
        "assigned_at_line": 297,
        "expression": "Directive(..., timestamp=now, ...)",
        "why": "ingest process wall clock",
    },
    "audit.timestamp": {
        "origin": "packages/jcc_core/core.py",
        "assigned_at_line": 340,
        "expression": "self.store.append_audit(..., timestamp=now, ...)",
        "why": "ingest process wall clock",
    },
    "audit.entry_hash": {
        "origin": "packages/jcc_core/audit.py",
        "assigned_at_line": 185,
        "expression": "_hash(_canon(payload)) over payload['timestamp']",
        "why": "hash of the row, which contains the wall-clock timestamp above",
    },
    "audit.prev_hash": {
        "origin": "packages/jcc_core/audit.py",
        "assigned_at_line": 172,
        "expression": "prev_hash = row[0] if row else GENESIS",
        "why": "the previous row's entry_hash, so it inherits that field's "
               "volatility transitively (row 0, which links to GENESIS, is stable)",
    },
    "volatile_clock_source": {
        "origin": "packages/jcc_core/core.py",
        "assigned_at_line": 223,
        "expression": "now = time.time()",
        "why": "the single wall-clock input all four fields above derive from",
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _chain_of(engine) -> Dict[str, Any]:
    """Projection of the derived chain, split into STABLE and VOLATILE parts.

    Deliberately excludes anything that is an input of the RUN rather than of
    the product: no ports, no temp paths, no pids. What is left is the derived
    chain -- and that chain is then split, because part of it is a function of
    the ingest process's clock (see ``VOLATILE_FIELDS``). Splitting it here
    rather than dropping the volatile fields on the floor is what makes the
    determinism claim checkable: the comparator compares ``stable``, and a
    difference that appears in ``volatile`` is attributed to a NAMED source
    rather than quietly ignored.
    """
    sessions = sorted(engine.store.list_sessions(), key=lambda s: s["session_id"])
    audit = sorted(engine.store.get_audit(limit=10000),
                   key=lambda a: (a.get("seq") or 0))
    directives = sorted(engine.store.get_directives(),
                        key=lambda d: str(d.get("directive_id")))

    stable = {
        "sessions": [
            {
                "session_id": s["session_id"],
                "agent": s.get("agent"),
                "project": s.get("project"),
                "status": s.get("status"),
                "event_count": s.get("event_count"),
                "evidence_count": s.get("evidence_count"),
                "band": (s.get("current_assessment") or {}).get("band"),
                "risk_score": (s.get("current_assessment") or {}).get("risk_score"),
                "blocker_kind": (s.get("current_assessment") or {}).get("blocker_kind"),
                "action": (s.get("current_directive") or {}).get("action"),
                "evidence_hash": (s.get("current_directive") or {}).get("evidence_hash"),
                "directive_id": (s.get("current_directive") or {}).get("directive_id"),
            }
            for s in sessions
        ],
        "audit": [
            {
                "seq": a.get("seq"),
                "event_id": a.get("event_id"),
                "session_id": a.get("session_id"),
                "agent": a.get("agent"),
                "project": a.get("project"),
                "assessment": a.get("assessment"),
                "directive": a.get("directive"),
                "evidence_hash": a.get("evidence_hash"),
            }
            for a in audit
        ],
        "directives": [
            {
                "directive_id": d.get("directive_id"),
                "session_id": d.get("session_id"),
                "event_id": d.get("event_id"),
                "action": d.get("action"),
                "acted": d.get("acted"),
                "actuation_reason": d.get("actuation_reason"),
                "policy_version": d.get("policy_version"),
                "evidence_hash": d.get("evidence_hash"),
            }
            for d in directives
        ],
    }
    volatile = {
        "sessions": [{"session_id": s["session_id"],
                      "updated_at": s.get("updated_at")} for s in sessions],
        "audit": [{"seq": a.get("seq"), "timestamp": a.get("timestamp"),
                   "entry_hash": a.get("entry_hash"),
                   "prev_hash": a.get("prev_hash")} for a in audit],
        "directives": [{"directive_id": d.get("directive_id"),
                        "timestamp": d.get("timestamp")} for d in directives],
    }
    return {"stable": stable, "volatile": volatile, "sessions": stable["sessions"],
            "audit": stable["audit"], "directives": stable["directives"]}


def _replay() -> Dict[str, Any]:
    """One full fixture replay into a fresh database. Returns the chain."""
    os.environ.pop("JCC_CONTROL_TOKEN", None)
    tmp = Path(tempfile.mkdtemp(prefix="jcc-demo-acc-"))
    _app, engine, _store, _cfg = build_app(tmp)
    for env in load_demo_envelopes():
        engine.ingest(AEPEnvelope(**env))
    return {"chain": _chain_of(engine), "engine": engine}


def _provenance() -> Dict[str, Any]:
    return {
        "generator": "fixtures/demo/acceptance.py",
        "generator_sha256": _sha256(HERE),
        "loader": "fixtures/demo/load.py",
        "loader_sha256": _sha256(REPO / "fixtures" / "demo" / "load.py"),
        "manifest": "fixtures/demo/manifest.json",
        "manifest_sha256": _sha256(REPO / "fixtures" / "demo" / "manifest.json"),
        # The volatile-field declaration points at product source; these hashes
        # pin the justification to the bytes it was made about, so a later edit
        # to core.py/audit.py makes the declaration visibly stale instead of
        # silently wrong.
        "volatile_origin_sha256": {
            "packages/jcc_core/core.py": _sha256(REPO / "packages" / "jcc_core" / "core.py"),
            "packages/jcc_core/audit.py": _sha256(REPO / "packages" / "jcc_core" / "audit.py"),
        },
        "command": "python -m fixtures.demo.acceptance",
        "deterministic": True,
    }


def _diff_paths(a: Any, b: Any, prefix: str = "") -> List[str]:
    """Path-wise difference between two projections; [] when identical."""
    out: List[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out.extend(_diff_paths(a.get(k), b.get(k), f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{prefix}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out.extend(_diff_paths(x, y, f"{prefix}[{i}]"))
    elif a != b:
        out.append(f"{prefix}: {a!r} != {b!r}")
    return out


def main() -> int:
    problems: List[str] = []

    run1 = _replay()
    run2 = _replay()
    d1 = _digest(run1["chain"]["stable"])
    d2 = _digest(run2["chain"]["stable"])
    replay_ok = d1 == d2
    if not replay_ok:
        diff = _diff_paths(run1["chain"]["stable"], run2["chain"]["stable"])
        problems.append(f"replay is not deterministic outside the declared "
                        f"volatile fields ({len(diff)} difference(s)): "
                        + "; ".join(diff[:4]))
    v1 = _digest(run1["chain"]["volatile"])
    v2 = _digest(run2["chain"]["volatile"])
    volatile_moved = v1 != v2
    # The declaration is only load-bearing if the fields it names really do move.
    # If they stopped moving, either the product changed (and the declaration is
    # stale) or the projection stopped looking at them -- both worth surfacing.
    volatile_note = ("declared volatile fields differ between the two replays, "
                     "as the declaration predicts"
                     if volatile_moved else
                     "declared volatile fields did NOT differ between the two "
                     "replays; the declaration may be stale")
    if not volatile_moved:
        problems.append(volatile_note)

    engine = run1["engine"]
    sessions = run1["chain"]["sessions"]
    agents_covered = sorted({s["agent"] for s in sessions})
    missing_agents = [a for a in DEMO_AGENTS if a not in agents_covered]
    if missing_agents:
        problems.append(f"agents with no derived session: {', '.join(missing_agents)}")
    bad_demo = [s["session_id"] for s in sessions if not is_demo_run(s)]
    if bad_demo:
        problems.append(f"sessions not classified DEMO: {', '.join(bad_demo)}")

    directives = run1["chain"]["directives"]
    acted = [d["directive_id"] for d in directives if d.get("acted") is not False]
    all_inert = bool(directives) and not acted
    if not directives:
        problems.append("replay produced no directives")
    if acted:
        problems.append(f"{len(acted)} demo directive(s) with acted != false: "
                        f"{', '.join(acted[:3])}")

    # --- real HTTP, real UI, real API: where does DEMO actually render? ------ #
    os.environ.pop("JCC_CONTROL_TOKEN", None)
    tmp = Path(tempfile.mkdtemp(prefix="jcc-demo-ui-"))
    app, engine2, _store, _cfg = build_app(tmp)
    for env in load_demo_envelopes():
        engine2.ingest(AEPEnvelope(**env))
    sessions2 = sorted(engine2.store.list_sessions(), key=lambda s: s["session_id"])
    first_sid = sessions2[0]["session_id"] if sessions2 else None

    api_port = find_free_port()
    api_server, api_thread = start_api_server(app, api_port)
    ui_port = find_free_port()
    ui_httpd, _ = start_ui_server(f"http://127.0.0.1:{api_port}", ui_port)
    ui_base = f"http://127.0.0.1:{ui_port}"

    screens: List[Dict[str, Any]] = []
    labelled: List[str] = []
    try:
        for name, path in DEMO_LABEL_PAGES.items():
            if path is None:
                path = f"/sessions/{first_sid}" if first_sid else "/sessions"
            status, body = get(f"{ui_base}{path}")
            has_label = "DEMO" in body
            # `non_trivial` rather than a raw byte count: the rendered pages
            # legitimately contain the audit timestamps and entry hashes, whose
            # string lengths vary by a few characters between runs, so a byte
            # count is NOT reproducible while "this page is not empty" is.
            # Storing the number would have made this artifact drift by one byte
            # per run and broken the clean-tree check it is supposed to support.
            screens.append({
                "name": name, "path": path, "status": status,
                "non_trivial": len(body.encode("utf-8")) > 300,
                "unreachable": UNREACHABLE_MARKER in body,
                "demo_label_present": has_label,
            })
            if has_label:
                labelled.append(name)
            if status != 200:
                problems.append(f"{name}: HTTP {status}")
            if UNREACHABLE_MARKER in body:
                problems.append(f"{name}: rendered API UNREACHABLE")
    finally:
        stop_ui_server(ui_httpd)
        stop_api_server(api_server, api_thread)

    missing_lbl = [p for p in DEMO_LABEL_PAGES if p not in labelled]
    if missing_lbl:
        problems.append(f"DEMO label absent on: {', '.join(missing_lbl)}")

    out = {
        "schema": "jcc-demo-mode-evidence/2",
        "provenance": _provenance(),
        "agents_declared": list(DEMO_AGENTS),
        "agents_covered": agents_covered,
        "sessions": sessions,
        "session_count": len(sessions),
        "directive_count": len(directives),
        "all_directives_inert": all_inert,
        "deterministic_replay": {
            "ok": replay_ok,
            "scope": "every derived field EXCEPT the declared volatile set",
            "run1_digest": d1,
            "run2_digest": d2,
            # The volatile DIGESTS are deliberately not recorded: they are hashes
            # of wall-clock values, so printing them would make this artifact
            # non-reproducible -- the exact defect it exists to detect. The
            # observable fact (did the declared fields move?) is what is stored.
            "volatile_fields_moved": volatile_moved,
            "volatile_fields": VOLATILE_FIELDS,
            "volatile_note": volatile_note,
            "method": "two independent replays into two fresh databases; sha256 of "
                      "the canonical projection of sessions+audit+directives, split "
                      "into stable/volatile so the excluded fields are named, "
                      "sourced and hash-pinned rather than dropped",
        },
        "demo_label_pages": sorted(labelled),
        "screens": screens,
        "problems": problems,
        "passed": not problems,
        "status": "PASS" if not problems else "FAIL",
    }

    artifact_dir = REPO / "artifacts" / "acceptance"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "demo_e2e.json"
    artifact_path.write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"sessions        : {len(sessions)} ({', '.join(agents_covered)})")
    print(f"directives      : {len(directives)} (all inert: {all_inert})")
    print(f"replay digest   : {d1[:16]} {'==' if replay_ok else '!='} {d2[:16]}")
    print(f"DEMO label on   : {', '.join(sorted(labelled))}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    print(f"\nwrote {artifact_path}  status={out['status']}")
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
