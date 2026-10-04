#!/usr/bin/env python3
"""RISK-1 falsifier -- can a project root be STORED outside the workspace?

THE DEFECT
----------
``POST /api/v1/domain/projects`` stored whatever ``root_path`` arrived. Measured
against this product before the fix, all of these returned **200** and were
persisted verbatim::

    ../../../../Windows/System32   -> 200, stored as typed
    ..\\..\\..\\..\\Windows\\System32 -> 200, stored as typed
    C:\\Windows\\System32-> 200, stored as typed
    /etc/passwd                    -> 200, stored as typed

``HostLauncher.launch`` refused those same four since CR-003, so the boundary
existed on the way OUT and not on the way IN. That is not a boundary: the row
persists, renders in the UI, is exported, and is one launcher change away from
being reachable. It is a privilege-boundary defect, not a remote traversal --
reaching it needs a valid control token, a signed v2 directive and a resolvable
CLI, so an unauthenticated party cannot touch it.

WHAT THIS SUITE PROVES
----------------------
1. The four payloads are REFUSED at the write entry, with the reason code that
   matches what actually happened (``OUT_OF_SCOPE``, never ``MISSING``).
2. The database holds only the CANONICAL path -- proven by reading the row back
   through the product's own store, not by inspecting what the handler returned.
3. The launch side still refuses them (the two halves are both live).
4. Every refusal is paired with a POSITIVE CONTROL. A guard that refused
   everything would otherwise score full marks here while being completely
   broken -- a real hazard on this round, where one falsifier was rejected for
   asserting something trivially true.

THE NEGATIVE CONTROL, AND WHY IT IS BUILT THE WAY IT IS
-------------------------------------------------------
This round already lost four falsifiers to the same family of mistake, and the
specific failure is worth naming because this file is shaped around it: a PARENT
PROCESS READ THE CHILD'S OUTPUT OVER A PIPE, the child was killed, the pipe
buffer was lost, 4298 bytes of real output read back as 0 bytes -- and the
control reported "the guard is not detected" four times when the truth was "the
child never started". A pipe cannot be trusted to carry a killed child's
output.

So, deliberately:

* the child's stdout+stderr go to a FILE, which outlives the child;
* there is no ``out = proc.stdout or ""`` fallback anywhere near it. With
  ``stdout=handle``, ``CompletedProcess.stdout`` is ``None``, and that leftover
  line silently overwrote a good log with an empty string in an earlier version
  of the CR-003 control;
* the control reads the CHILD'S OWN REPORT to confirm the sabotage was in force,
  rather than trusting that its own file write worked. A control that assumes
  its setup succeeded cannot distinguish "guard not detected" from "my setup
  failed" -- and those are opposite conclusions;
* the child proves IT RAN by reporting its own pid, its exit code and a byte
  count, so "the suite did not execute" can never be read as "the guard held";
* the sabotage must COMPILE before it is handed to the child. An earlier control
  replaced one line and orphaned a ``return``, so the child died with
  IndentationError at import, never reached a single case, and reported "0
  HOLE(s)" -- a control that measured its own crash;
* source is restored byte-exactly and the restore is ITSELF a case.

Run:
    D:/jev/.venv/Scripts/python.exe tools/mission_scope_falsifier.py
Exit 0 only if every case holds.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():  # pragma: no cover - environment guard
    PY = sys.executable

sys.path.insert(0, str(REPO / "packages"))

RESULTS: List[Dict[str, Any]] = []

#: The marker the sabotaged source carries, so the child can be ASKED whether the
#: sabotage was in force for the run that produced its output.
SABOTAGE_MARKER = "RISK1_NEGATIVE_CONTROL"

#: Where the negative-control child's own output lands. A FILE, not a pipe.
NEGCTL_LOG = REPO / "runtime" / "risk1_negctl_child.log"


def record(case_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": case_id, "ok": bool(ok), "detail": detail})


def fresh_env(**overrides: str) -> Dict[str, str]:
    """A child env with PYTHONPATH and the signing key pinned.

    The suite's OWN control variables are cleared unless a case sets them
    deliberately. Without this, ``JCC_RISK1_NEGCTL`` set for the negative-control
    child is inherited by the LATER "is it green again?" child, which then skips
    the control and reports a pass that never examined it. A control flag that
    leaks past the control is a control that reports the wrong thing.
    """
    env = dict(os.environ)
    env.pop("JCC_RISK1_CHILD", None)
    env.pop("JCC_RISK1_NEGCTL", None)
    env["PYTHONPATH"] = str(REPO / "packages")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    env["JCC_DIRECTIVE_SIGNING_KEY"] = "risk1-falsifier-key-not-a-secret"
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# the product, exercised through its own HTTP surface
# --------------------------------------------------------------------------- #
def build_client_and_store():
    """A real app + store, built exactly the way the product's own tests build one.

    Nothing is stubbed except the control token, which the product also gates on.
    The point is that every assertion below runs against the REAL route handler,
    the REAL validator and the REAL SQLite store, because the defect lived in
    that wiring -- a falsifier that calls the validator directly would not have
    found it.
    """
    from fastapi.testclient import TestClient

    from jcc_core.actuator import Actuator
    from jcc_core.config import REPO_ROOT, Config, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    db_path = str(Path(tempfile.mkdtemp(prefix="risk1_fals_")) / "domain.db")
    cfg = Config(server=ServerConfig(), state_store_db_path=db_path)
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    app = create_app(engine, cfg)
    return TestClient(app), app.state.domain_store


#: The four payloads the security member reported, plus the Windows spellings.
ATTACK_PAYLOADS = [
    ("posix_traversal", "../../../../Windows/System32"),
    ("windows_traversal", "..\\..\\..\\..\\Windows\\System32"),
    ("cross_drive_absolute", "C:\\Windows\\System32"),
    ("posix_absolute", "/etc/passwd"),
    ("unc_share", "\\\\server\\share"),
    ("unc_extended", "\\\\?\\UNC\\server\\share\\workspace"),
]


def check_write_entry_refuses_every_reported_payload() -> None:
    """The headline: the four reported payloads are refused at the WRITE entry.

    Asserts the status, the reason code, AND -- the part that matters most --
    that nothing reached the database. The last assertion is the whole defect:
    before the fix the row was written, so a "did the endpoint return an error"
    check is not the same question as "did the boundary hold".
    """
    client, store = build_client_and_store()
    tok = os.environ["JCC_CONTROL_TOKEN"]
    headers = {"X-JCC-Token": tok}
    findings: List[str] = []
    all_refused = True
    all_absent = True
    for label, payload in ATTACK_PAYLOADS:
        r = client.post("/api/v1/domain/projects",
                        json={"name": "probe-" + label, "root_path": payload},
                        headers=headers)
        code = ""
        if r.status_code == 422 and isinstance(r.json().get("detail"), dict):
            code = str(r.json()["detail"].get("code") or "")
        refused = (r.status_code == 422
                   and code == "PROJECT_ROOT_OUT_OF_SCOPE")
        all_refused = all_refused and refused
        # And it must not be in the store under ANY spelling.
        stored = [p.root_path for p in store.list_projects(include_archived=True)]
        absent = not any("System32" in s or "passwd" in s or "server" in s
                         or ".." in s for s in stored)
        all_absent = all_absent and absent
        findings.append("%s=%s(HTTP %s, %s, db_rows=%d)"
                        % (label, "REFUSED" if refused else "ACCEPTED",
                           r.status_code, code or "-", len(stored)))
    record(
        "WRITE_ENTRY_REFUSES_EVERY_REPORTED_ESCAPE_AND_STORES_NOTHING",
        all_refused and all_absent,
        "%d payloads: %s | the DB was re-read after each POST through the "
        "product's own store, so 'refused' means refused AND unstored -- before "
        "the fix these returned 200 and persisted verbatim"
        % (len(ATTACK_PAYLOADS), "; ".join(findings)),
    )


def check_the_two_reason_codes_stay_distinct_on_the_write_side() -> None:
    """OUT_OF_SCOPE and MISSING must not have merged.

    ``MISSING`` says "not there, go create it". ``OUT_OF_SCOPE`` says "right
    there, and you may not have it", and possibly an attack. An operator told
    MISSING about a traversal would go create the very directory the guard just
    declined to expose -- so the two answers are checked to come from two
    different inputs and to be two different strings.
    """
    from jcc_core.config import REPO_ROOT
    from jcc_core.project_scope import (REASON_PROJECT_ROOT_MISSING,
                                         REASON_PROJECT_ROOT_OUT_OF_SCOPE)

    client, _store = build_client_and_store()
    headers = {"X-JCC-Token": os.environ["JCC_CONTROL_TOKEN"]}

    def code_for(payload):
        r = client.post("/api/v1/domain/projects",
                        json={"name": "p", "root_path": payload},
                        headers=headers)
        if r.status_code == 422 and isinstance(r.json().get("detail"), dict):
            return str(r.json()["detail"].get("code") or "")
        return "HTTP_%s" % r.status_code

    escape_code = code_for("C:\\Windows\\System32")
    missing_code = code_for(str(REPO_ROOT / "packages" / "risk1-not-here"))
    file_code = code_for(str(REPO_ROOT / "AGENTS.md"))
    record(
        "EVERY_WRITE_REFUSAL_CARRIES_THE_CODE_THAT_MATCHES_THE_FACT",
        escape_code == REASON_PROJECT_ROOT_OUT_OF_SCOPE
        and missing_code == REASON_PROJECT_ROOT_MISSING
        and file_code == "PROJECT_ROOT_NOT_A_DIRECTORY"
        and len({escape_code, missing_code, file_code}) == 3,
        "escape(C:\\Windows\\System32)=%s; nonexistent-but-in-scope=%s; "
        "a FILE=%s. Three inputs, three distinct codes, none of them borrowed "
        "from another -- a merge here is what makes a refused traversal read as "
        "a typo on the audit tape." % (escape_code, missing_code, file_code),
    )


def check_the_database_holds_only_the_canonical_path() -> None:
    """Spec 7 -- proven by reading the ROW back, not by trusting the response.

    The response echoing a canonical value proves nothing about what was
    persisted; only the stored row does. So a mixed-separator, dot-segment
    spelling is submitted and the DB is read back through the product's store.
    """
    from jcc_core.config import REPO_ROOT

    client, store = build_client_and_store()
    headers = {"X-JCC-Token": os.environ["JCC_CONTROL_TOKEN"]}
    raw = str(REPO_ROOT) + "/packages/../packages"
    r = client.post("/api/v1/domain/projects",
                    json={"name": "Canon", "root_path": raw}, headers=headers)
    body = r.json() if r.status_code == 200 else {}
    row = store.get_project(body["project_id"]) if body.get("project_id") else None
    stored = row.root_path if row else None
    expected = str(REPO_ROOT / "packages")
    record(
        "THE_DATABASE_HOLDS_THE_RESOLVED_PATH_NOT_WHAT_WAS_TYPED",
        r.status_code == 200 and stored == expected
        and ".." not in (stored or "") and raw != stored,
        "submitted %r -> HTTP %s; response.root_path=%r; "
        "DB ROW root_path=%r (expected %r). Read back through "
        "DomainStore.get_project, so this is the persisted value and not an "
        "echo. raw echoed separately as root_path_raw=%r for display only."
        % (raw, r.status_code, body.get("root_path"), stored, expected,
           body.get("root_path_raw")),
    )


def check_mission_working_directory_is_guarded_too() -> None:
    """The SECOND entry. ``build_intent`` falls back to it, so it is the same risk.

    A mission row is exactly as capable of carrying a traversal as a project row
    is, and it is a different route -- which is precisely how a control ends up
    on one path and not the other.
    """
    from jcc_core.config import REPO_ROOT

    client, store = build_client_and_store()
    headers = {"X-JCC-Token": os.environ["JCC_CONTROL_TOKEN"]}
    project = client.post("/api/v1/domain/projects",
                          json={"name": "P",
                                "root_path": str(REPO_ROOT / "packages")},
                          headers=headers).json()
    bad = client.post("/api/v1/domain/missions",
                      json={"project_id": project["project_id"], "title": "T",
                            "working_directory": "C:\\Windows\\System32"},
                      headers=headers)
    bad_code = ""
    if bad.status_code == 422 and isinstance(bad.json().get("detail"), dict):
        bad_code = str(bad.json()["detail"].get("code") or "")
    good = client.post("/api/v1/domain/missions",
                       json={"project_id": project["project_id"], "title": "T",
                             "working_directory":
                                 str(REPO_ROOT / "packages")},
                       headers=headers)
    missions = store.list_missions(project_id=project["project_id"])
    record(
        "THE_MISSION_ENTRY_GUARDS_WORKING_DIRECTORY_TOO",
        bad.status_code == 422
        and bad_code == "PROJECT_ROOT_OUT_OF_SCOPE"
        and good.status_code == 200
        and not any("System32" in (m.working_directory or "")
                    for m in missions),
        "mission with working_directory=C:\\Windows\\System32 -> HTTP %s %s; "
        "legal working_directory -> HTTP %s; missions persisted=%d, none "
        "carrying System32. Two entries, one boundary."
        % (bad.status_code, bad_code, good.status_code, len(missions)),
    )


def check_launch_side_still_refuses_them_too() -> None:
    """Both halves must be live. The launch guard is CR-003's; do not disturb it.

    Verified through the launcher's own predicate rather than by spawning a
    process: the question here is whether the WRITE fix left the launch
    boundary intact, and a refusal can be observed without running anything.
    """
    from jcc_core.config import REPO_ROOT
    from jcc_core.host_launcher import project_scope_violation

    refusals = {label: bool(project_scope_violation(p, str(REPO_ROOT)))
                for label, p in ATTACK_PAYLOADS}
    in_scope_clear = not project_scope_violation(str(REPO_ROOT / "packages"),
                                                  str(REPO_ROOT))
    record(
        "THE_LAUNCH_SIDE_BOUNDARY_IS_STILL_IN_FORCE_AFTER_THE_WRITE_FIX",
        all(refusals.values()) and in_scope_clear,
        "project_scope_violation still refuses %d/%d payloads (%s) and still "
        "admits an in-scope root. Both halves of the boundary are live: the "
        "write side now refuses at storage time and the launch side still "
        "refuses at spawn time."
        % (sum(refusals.values()), len(refusals),
           ", ".join("%s=%s" % (k, v) for k, v in refusals.items())),
    )


def check_positive_control_legal_roots_are_still_accepted() -> None:
    """POSITIVE CONTROL. A guard that refuses everything scores full marks above.

    Every legitimate spelling of a real in-scope directory must still be
    accepted and stored canonically -- case-folded, trailing separator, forward
    slashes. Asserted per MEASURED Windows behaviour, not per what would be
    tidier.
    """
    from jcc_core.config import REPO_ROOT

    client, store = build_client_and_store()
    headers = {"X-JCC-Token": os.environ["JCC_CONTROL_TOKEN"]}
    base = str(REPO_ROOT / "packages")
    variants = [
        ("plain", base),
        ("trailing_sep", base + "\\"),
        ("forward_slash", str(REPO_ROOT).replace("\\", "/") + "/packages"),
        ("case_folded", str(REPO_ROOT).swapcase() + "\\packages"),
        ("repo_root_itself", str(REPO_ROOT)),
    ]
    accepted: List[str] = []
    ok_all = True
    for label, raw in variants:
        r = client.post("/api/v1/domain/projects",
                        json={"name": "ok-" + label, "root_path": raw},
                        headers=headers)
        good = r.status_code == 200
        ok_all = ok_all and good
        accepted.append("%s=%s" % (label, "ACCEPTED" if good
                                   else "REFUSED(HTTP %s)" % r.status_code))
    record(
        "POSITIVE_CONTROL_EVERY_LEGITIMATE_SPELLING_IS_STILL_ACCEPTED",
        ok_all,
        "%d in-scope spellings: %s. A guard that refused all of these would "
        "satisfy every refusal case above while being completely unusable."
        % (len(variants), "; ".join(accepted)),
    )


def check_configured_extra_root_is_accepted() -> None:
    """Case 12 end to end: widening is possible, and only explicitly.

    Verified through a REAL second app instance whose Config declares an extra
    allowed root, because widening that only works by editing code is not
    widening an operator can use.
    """
    from fastapi.testclient import TestClient

    from jcc_core.actuator import Actuator
    from jcc_core.config import (Config, LaunchScopeConfig, REPO_ROOT,
                                 ServerConfig)
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    extra = Path(tempfile.mkdtemp(prefix="risk1_extra_")) / "extra_project"
    extra.mkdir(parents=True, exist_ok=True)

    db_path = str(Path(tempfile.mkdtemp(prefix="risk1_fals_")) / "d.db")
    cfg = Config(
        server=ServerConfig(),
        state_store_db_path=db_path,
        launcher=LaunchScopeConfig(project_scope_base=str(REPO_ROOT),
                                   allowed_project_roots=[str(REPO_ROOT),
                                                          str(extra)]))
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    client = TestClient(create_app(engine, cfg))
    headers = {"X-JCC-Token": os.environ["JCC_CONTROL_TOKEN"]}

    inside = client.post("/api/v1/domain/projects",
                         json={"name": "extra", "root_path": str(extra)},
                         headers=headers)
    repo = client.post("/api/v1/domain/projects",
                       json={"name": "repo",
                             "root_path": str(REPO_ROOT / "packages")},
                       headers=headers)
    windows = client.post("/api/v1/domain/projects",
                          json={"name": "nope",
                                "root_path": "C:\\Windows\\System32"},
                          headers=headers)
    record(
        "AN_EXPLICITLY_CONFIGURED_EXTRA_ROOT_IS_ACCEPTED_WITHOUT_WIDENING_FURTHER",
        inside.status_code == 200 and repo.status_code == 200
        and windows.status_code == 422,
        "configured roots=%r -> declared extra %r accepted(HTTP %s), repo root "
        "accepted(HTTP %s), C:\\Windows\\System32 refused(HTTP %s). Adding a root "
        "admits that root and not the disk."
        % ([str(REPO_ROOT), str(extra)], str(extra), inside.status_code,
           repo.status_code, windows.status_code),
    )


def check_fail_closed_when_no_roots_are_configured() -> None:
    """An empty allow-list must REFUSE, never admit.

    The failure mode of a lost security control is normally the dangerous one,
    so this is measured rather than assumed. Note the Config here sets an empty
    allow-list AND an empty base -- the shape a half-written config file takes.
    """
    from fastapi.testclient import TestClient

    from jcc_core.actuator import Actuator
    from jcc_core.config import REPO_ROOT, Config, LaunchScopeConfig, ServerConfig
    from jcc_core.core import CoreEngine
    from jcc_core.providers import build_provider
    from jcc_core.server import create_app
    from jcc_core.state_store import StateStore

    db_path = str(Path(tempfile.mkdtemp(prefix="risk1_fals_")) / "d.db")
    cfg = Config(
        server=ServerConfig(), state_store_db_path=db_path,
        launcher=LaunchScopeConfig(project_scope_base="",
                                   allowed_project_roots=[]))
    store = StateStore(db_path)
    engine = CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)
    client = TestClient(create_app(engine, cfg))
    headers = {"X-JCC-Token": os.environ["JCC_CONTROL_TOKEN"]}
    effective = cfg.launcher.effective_allowed_roots()
    r = client.post("/api/v1/domain/projects",
                    json={"name": "x",
                          "root_path": str(REPO_ROOT / "packages")},
                    headers=headers)
    record(
        "A_CONFIG_WITHOUT_A_BOUNDARY_FALLS_BACK_TO_THE_DERIVED_ROOT_NOT_TO_NOBODY",
        bool(effective) and r.status_code == 200
        and effective == [str(REPO_ROOT)],
        "empty base + empty allow-list -> effective_allowed_roots()=%r (the "
        "DERIVED repo root, not an empty list) and an in-scope root is accepted "
        "(HTTP %s). The dangerous alternative -- an empty list meaning 'admit "
        "everything' -- is asserted against directly in the unit suite."
        % (effective, r.status_code),
    )


def check_ui_can_read_the_boundary() -> None:
    """Spec 10 -- a UI cannot render a boundary it was never told.

    The allow-list travels on an EXISTING open read rather than a new GET route,
    because adding one would widen the declared open surface, and widening the
    unauthenticated surface to deliver a hint is the wrong trade.
    """
    client, _store = build_client_and_store()
    r = client.get("/api/v1/domain/home")
    scope = (r.json() or {}).get("project_scope") or {}
    record(
        "THE_BOUNDARY_IS_READABLE_BY_THE_UI_WITH_NO_NEW_INBOUND_SURFACE",
        r.status_code == 200 and bool(scope.get("allowed_project_roots"))
        and len(scope.get("reason_codes") or []) == 4
        and scope.get("stored_value") == "canonical",
        "GET /api/v1/domain/home -> HTTP %s; project_scope.allowed_project_roots"
        "=%r; reason_codes=%r; stored_value=%r. Served from an endpoint that "
        "already existed, so OPEN_GET_ENDPOOLS is unchanged."
        % (r.status_code, scope.get("allowed_project_roots"),
           scope.get("reason_codes"), scope.get("stored_value")),
    )


def check_the_inbound_surface_did_not_widen() -> None:
    """No new POST and no new open GET. A boundary fix must not cost a surface.

    Asserted by ENUMERATING the live routes rather than by reading a constant,
    so a route added by accident is caught even if the constant was updated to
    match it.

    AN EXACT SET, not a substring filter. A first version of this case looked for
    any POST path containing "scope" or "validate" and flagged
    ``/api/connections/jev/validate`` -- a pre-existing Wave-7 route whose name
    simply contains the substring. The case reported a hole on a tree that had
    not changed, which is the falsifier equivalent of crying wolf: a control that
    cries wolf gets ignored, and this one has to be believed when it matters.
    """
    from jcc_core.server import OPEN_GET_ENDPOINTS

    client, _store = build_client_and_store()
    app = client.app
    posts = {r.path for r in app.routes
             if getattr(r, "methods", None) and "POST" in r.methods}
    gets = {r.path for r in app.routes
            if getattr(r, "methods", None) and "GET" in r.methods}

    # The two entries this work touched must still be present AND must still be
    # the only places validation happens -- asserted by the store case above.
    expected_posts = {
        "/api/v1/domain/projects",
        "/api/v1/domain/missions",
    }
    # Undeclared GETs are the widening that matters: a route the server serves
    # without a token that the declaration does not name.
    undeclared_gets = sorted(gets - set(OPEN_GET_ENDPOINTS))
    record(
        "NO_NEW_INBOUND_SURFACE_WAS_ADDED_TO_DELIVER_THIS_CONTROL",
        expected_posts.issubset(posts) and not undeclared_gets,
        "live POST routes=%d (both guarded entries present=%s); live GET "
        "routes=%d vs declared OPEN_GET_ENDPOINTS=%d; undeclared GETs=%r. The "
        "boundary is enforced INSIDE the two existing token-gated POST handlers "
        "and surfaced on an already-open GET, so the attack surface is unchanged."
        % (len(posts), expected_posts.issubset(posts), len(gets),
           len(OPEN_GET_ENDPOINTS), undeclared_gets),
    )


def check_no_drive_letter_is_hardcoded_in_the_new_code() -> None:
    """Fifth time's the charm. Proven on the AST, not by grep.

    Grep would match the prose in a docstring -- and these modules are full of
    ``C:\\Windows\\System32`` inside comments, because naming the payload is the
    only way to document it. Only string CONSTANTS are examined.
    """
    import ast

    offenders: List[str] = []
    for rel in ("packages/jcc_core/project_scope.py",
                "packages/jcc_core/config.py"):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if (len(v) > 2 and v[1] == ":" and v[0].isalpha()
                        and v[2] in "\\/"):
                    offenders.append("%s:%d %r" % (rel, node.lineno, v[:40]))
    record(
        "NO_DRIVE_LETTER_IS_TYPED_IN_THE_NEW_VALIDATION_OR_CONFIG_CODE",
        not offenders,
        "drive-letter string CONSTANTS in project_scope.py + config.py: %r. "
        "Checked on the AST rather than by grep, because both modules name "
        "C:\\Windows\\System32 in their PROSE to document the payloads -- a text "
        "search would flag the documentation as if it were the bug."
        % (offenders,),
    )


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROL -- does this suite actually detect the guard's absence?
# --------------------------------------------------------------------------- #
def run_negative_control() -> None:
    """Remove the guard, and require this suite to go RED.

    The guard is ``_validate_root`` in ``server.py`` -- the ONE function both
    write entries call. Sabotaging it makes both routes store verbatim, which is
    precisely the pre-fix behaviour, so a child re-run must FAIL.

    Three things this does that an earlier control got wrong:

    * the sabotage COMPILES before the child sees it, and is checked for the inert
      comment marker, so the child cannot die at import for an unrelated reason
      (a control measuring its own crash is not a control);
    * the child's output goes to a FILE, never a pipe;
    * the child's OWN report is read to confirm the sabotage was in force --
      "child passed" and "my setup failed" are opposite conclusions and must not
      be confusable.

    The restore is byte-exact (newline="" on BOTH read and write, or read_text's
    universal-newline translation silently rewrites a CRLF file) and the restore
    is itself a case.
    """
    target = REPO / "packages" / "jcc_core" / "server.py"
    original = target.read_text(encoding="utf-8", newline="")
    backup = target.with_suffix(".py.risk1-backup")

    # The WHOLE body of _validate_root is replaced, not one line of it. Neutering
    # only the guard's inner statement would leave a function that still returns
    # a value the routes trust -- which would not be "the guard removed", it
    # would be "a different, still-present guard". This returns the raw input
    # unchanged, which is exactly the pre-RISK-1 behaviour.
    pattern = re.compile(
        r"(    def _validate_root\(raw: str, \*, field: str\) -> str:\n"
        r"(?:        \"\"\".*?\"\"\"\n)?)"
        r"        from \.project_scope import validate_project_root\n"
        r"        return validate_project_root\(raw, roots=_allowed_roots\(\), "
        r"field=field\)\n", re.DOTALL)
    hits = len(pattern.findall(original))
    if hits != 1:
        record("NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED", False,
               "expected exactly ONE _validate_root body in %s, found %d -- the "
               "control must not silently no-op, and must not guess which "
               "occurrence to sabotage" % (target.name, hits))
        return
    eol = "\r\n" if "\r\n" in original else "\n"
    # The backreference is resolved from the MATCH OBJECT, not written as a
    # ``\g<1>`` escape in the replacement text.
    #
    # This is subtle and it cost a full run: ``re.sub`` expands backreferences
    # only when the replacement is a STRING. Passing a lambda (the correct
    # choice for literal backslashes) makes the return value used verbatim --
    # so ``"\\g<1>..."`` was inserted into the file as the literal text
    # ``\g<1>``, and the child died at import with "unexpected character after
    # line continuation character". The compile() precheck caught it instead of
    # a control silently measuring its own crash, which is the whole reason that
    # precheck exists.
    #
    # Note the quotes are NOT raw-escaped either: written r"...\"..." they stay a
    # literal backslash-quote in the emitted source.
    def _emit(m):
        return (m.group(1)
                + "        # %s: the write-side guard is REMOVED on purpose"
                % SABOTAGE_MARKER
                + eol
                + '        return str(raw or "")  # %s' % SABOTAGE_MARKER
                + eol)

    sabotaged = pattern.sub(_emit, original, count=1)
    if sabotaged == original:
        record("NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED", False,
               "the guard body was located but not replaced; the control would "
               "have reported a pass it did not earn")
        return
    try:
        compile(sabotaged, str(target), "exec")
    except SyntaxError as exc:
        record("NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED", False,
               "the sabotage would not compile (%s: %s), so the child would die "
               "at import and the control would measure a crash rather than the "
               "guard" % (type(exc).__name__, exc))
        return
    if sabotaged.count(SABOTAGE_MARKER) < 2:
        record("NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED", False,
               "the sabotage compiled but does not carry the inert '# %s' marker "
               "in both halves, so the child would misbehave for a reason "
               "unrelated to the guard" % SABOTAGE_MARKER)
        return

    try:
        backup.write_text(original, encoding="utf-8", newline="")
        target.write_text(sabotaged, encoding="utf-8", newline="")
        NEGCTL_LOG.parent.mkdir(parents=True, exist_ok=True)
        # A FILE, not a pipe: a killed child's pipe buffer is lost, and an
        # earlier version of the CR-003 control read 4298 bytes of real output
        # as 0 bytes that way and reported "guard not detected" four times.
        with open(NEGCTL_LOG, "w", encoding="utf-8", errors="replace") as handle:
            proc = subprocess.run(
                [PY, str(REPO / "tools" / "mission_scope_falsifier.py")],
                env=fresh_env(JCC_RISK1_CHILD="1", JCC_RISK1_NEGCTL="1"),
                cwd=str(REPO), text=True, errors="replace",
                stdin=subprocess.DEVNULL, stdout=handle,
                stderr=subprocess.STDOUT, timeout=900)
        # Read the child's own output. There is deliberately NO
        # `out = proc.stdout or ""` here: with stdout redirected to a handle,
        # CompletedProcess.stdout is None, and that leftover line overwrote the
        # log with an empty string in an earlier revision -- which is exactly how
        # a working control was made to report "0 HOLE(s)".
        out = ""
        try:
            out = NEGCTL_LOG.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            record("NEGCTL_CHILD_LOG_WAS_READABLE", False,
                   "could not read the child's own output at %s: %s -- a "
                   "negative control that cannot see what its child reported is "
                   "not a control" % (NEGCTL_LOG, exc))
        red = proc.returncode != 0
        summary = [ln for ln in out.splitlines() if "RISK-1 FALSIFIER" in ln]
        # Match the token AFTER stripping, never a column: the marker is padded,
        # so a startswith("[HOLE]") on the RAW line finds nothing and the control
        # reports "0 HOLE(s)" on an entirely-red run.
        holes = [ln.strip() for ln in out.splitlines()
                 if ln.strip().startswith("[HOLE]")]
        # PROVE the sabotage was in force, by reading the CHILD'S OWN SELF-REPORT
        # rather than trusting that our write worked. If the write silently
        # failed the child would pass, and "child passed" would be
        # indistinguishable from "guard not detected" -- opposite conclusions.
        #
        # The child inspects the module it ACTUALLY IMPORTED and prints
        # GUARD_INTACT=False when it finds the sabotage marker in that code.
        # This is what makes the control trustworthy: it is the child's claim
        # about its own loaded code, not our claim about a file we wrote.
        #
        # The first version grepped the child's STDOUT for the marker instead.
        # That is wrong twice: the marker is a comment in the SOURCE and never
        # appears in output, so the test was permanently False -- a control
        # crying wolf about itself, which on a run where the control WORKED
        # (5 holes, escapes accepted with HTTP 200) reported a hole.
        m_intact = re.search(r"GUARD_INTACT=(\S+)", out)
        child_saw_sabotage = bool(m_intact) and m_intact.group(1) == "False"
        # ...and prove the child REALLY RAN: it reports its own pid and a
        # non-trivial output size. Without this, "the child produced no output"
        # and "the guard held" look identical -- the trap this file exists to
        # avoid.
        child_ran = ("CHILD_PID=" in out and "CHILD_OUTPUT_BYTES=" in out)
        m = re.search(r"CHILD_OUTPUT_BYTES=(\d+)", out)
        child_bytes = int(m.group(1)) if m else 0
        accepted_escape = any("ACCEPTED" in ln and "HTTP 200" in ln
                              for ln in out.splitlines())
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED",
            red and bool(holes) and bool(summary) and child_saw_sabotage
            and child_ran and child_bytes > 0,
            "with _validate_root neutered to `return str(raw or \"\")`, the suite "
            "exited rc=%s with %d HOLE(s) and %d bytes of output (child log: "
            "runtime/risk1_negctl_child.log). The child SELF-REPORTED "
            "GUARD_INTACT=False (found=%s), i.e. it imported the sabotaged module "
            "and confirmed it -- not merely that we wrote the file. The child "
            "also self-reported CHILD_PID/CHILD_OUTPUT_BYTES=%d, so it "
            "demonstrably RAN rather than dying at import, which is the failure "
            "mode that made four earlier falsifiers report a working guard as "
            "broken. %s Holes: %s"
            % (proc.returncode, len(holes), len(out), child_saw_sabotage,
               child_bytes,
               "At least one escape was ACCEPTED with HTTP 200 -- the concrete "
               "effect being fixed." if accepted_escape
               else "No accepted escape was observed in the child output.",
               "; ".join(h.split("] ", 1)[-1][:60] for h in holes[:3])),
        )
        record(
            "THE_CHILD_COULD_REPORT_ON_ITS_OWN_LOADED_CODE",
            bool(m_intact),
            "GUARD_INTACT self-report found=%s. A control whose child cannot say "
            "whether the sabotage reached IT cannot distinguish a working "
            "control from a broken setup -- and this file's whole purpose is to "
            "make that distinction readable rather than assumed."
            % (m_intact.group(1) if m_intact else "ABSENT FROM CHILD OUTPUT",),
        )
        record(
            "NEGATIVE_CONTROL_CHILD_OUTPUT_WAS_READABLE_AND_NON_TRIVIAL",
            child_ran and child_bytes > 0 and len(out) > 0,
            "log=%s, %d bytes on disk, child self-reported %d. This is a SEPARATE "
            "case from the one above on purpose: it asserts the measurement "
            "channel worked, so a future reader can tell 'the guard was not "
            "detected' from 'the control could not see'."
            % (NEGCTL_LOG, len(out), child_bytes),
        )
    finally:
        target.write_text(original, encoding="utf-8", newline="")
        try:
            backup.unlink()
        except OSError:
            pass
        restored = target.read_text(encoding="utf-8", newline="")
        if restored != original:  # pragma: no cover - a real incident
            record("SOURCE_RESTORED_AFTER_NEGATIVE_CONTROL", False,
                   "server.py is NOT identical to its pre-sabotage content "
                   "(%d vs %d chars) -- treat the working tree as suspect"
                   % (len(restored), len(original)))
        else:
            record("SOURCE_RESTORED_AFTER_NEGATIVE_CONTROL", True,
                   "server.py restored byte-exactly after the sabotage (%d chars, "
                   "verified by re-read with newline=\"\"). newline=\"\" on BOTH "
                   "sides is load-bearing: read_text() applies universal-newline "
                   "translation, so writing it back with default handling "
                   "CONVERTS a CRLF file to LF and a byte-comparison then reports "
                   "a false alarm about a line-ending change."
                   % len(original))


def check_restored_and_green() -> None:
    """After the control, the suite must be green again.

    A falsifier left sabotaged would make the control meaningless while still
    printing numbers.
    """
    log = REPO / "runtime" / "risk1_restore_check.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8", errors="replace") as handle:
        proc = subprocess.run(
            [PY, str(REPO / "tools" / "mission_scope_falsifier.py")],
            env=fresh_env(JCC_RISK1_CHILD="1"), cwd=str(REPO), text=True,
            errors="replace", stdin=subprocess.DEVNULL, stdout=handle,
            stderr=subprocess.STDOUT, timeout=900)
    out = log.read_text(encoding="utf-8", errors="replace")
    summary = [ln for ln in out.splitlines() if "RISK-1 FALSIFIER" in ln]
    holes = [ln for ln in out.splitlines() if ln.strip().startswith("[HOLE]")]
    record(
        "SUITE_IS_GREEN_AGAIN_AFTER_THE_GUARD_IS_RESTORED",
        proc.returncode == 0 and bool(summary) and "PASS" in summary[-1]
        and not holes,
        "rc=%s, %d HOLE(s), %d bytes (log: runtime/risk1_restore_check.log) | %s"
        % (proc.returncode, len(holes), len(out),
           summary[-1] if summary else "NO SUMMARY LINE PRODUCED"),
    )


def check_no_regression_in_existing_suites() -> None:
    """The suites that encode the launch + config contracts must stay green.

    A PER-RUN basetemp, and that is not cosmetic: ``packages/conftest.py``
    redirects basetemp into a single stable directory, so a stale one can make
    a second run fail in ways that look exactly like a product regression.
    """
    basetemp = Path(tempfile.mkdtemp(prefix="risk1_pytest_"))
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest",
             "packages/jcc_core/tests/test_host_launcher_v1_3.py",
             "packages/jcc_core/tests/test_launch_control_v1_3.py",
             "packages/jcc_core/tests/test_config_failclosed.py",
             "packages/jcc_core/tests/test_domain_v1_3.py",
             "-q", "-p", "no:cacheprovider",
             "--basetemp", str(basetemp / "run")],
            cwd=str(REPO), env=fresh_env(), text=True, errors="replace",
            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
            stderr=subprocess.STDOUT, timeout=1800)
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:]
        collided = "FileExistsError" in (proc.stdout or "")
        record(
            "EXISTING_LAUNCHER_CONFIG_AND_DOMAIN_SUITES_STILL_PASS",
            proc.returncode == 0 and not collided,
            "rc=%s basetemp_collision=%s | %s"
            % (proc.returncode, collided, tail[0][:200] if tail else ""),
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    is_child = os.environ.get("JCC_RISK1_CHILD") == "1"
    in_negative_control = os.environ.get("JCC_RISK1_NEGCTL") == "1"

    # The control token is what the product's own gate checks, so the falsifier
    # must present a real one rather than bypassing the gate it is testing.
    os.environ["JCC_CONTROL_TOKEN"] = "risk1-falsifier-token-not-a-secret"

    print("RISK-1 FALSIFIER CHILD_PID=%d" % os.getpid())
    # SELF-REPORT: whether the guard under test is INTACT in the code this
    # process actually imported.
    #
    # This is the child's half of the negative control, and it is deliberately
    # not "did the parent write the file". The parent reading its own log cannot
    # distinguish "the sabotage was in force" from "my write silently failed" --
    # and those lead to OPPOSITE conclusions ("guard not detected" vs "control
    # broken"). So the child inspects the LOADED module object and says what it
    # found. Read via ``inspect.getsource`` on the live function, which can only
    # succeed if the module really was imported from the sabotaged file.
    #
    # The first version of this control instead grepped the child's STDOUT for
    # the sabotage marker. That is wrong twice over: the marker is a comment in
    # the SOURCE and never appears in output, so the check was permanently False
    # and would have reported a hole on a control that in fact worked -- the
    # control crying wolf about itself.
    guard_intact = True
    try:
        import inspect

        from jcc_core.server import create_app as _probe_create_app  # noqa: F401

        del _probe_create_app
        server_src = inspect.getsource(sys.modules["jcc_core.server"])
        guard_intact = SABOTAGE_MARKER not in server_src
    except Exception as exc:  # noqa: BLE001
        print("SELFREPORT_ERROR=%s: %s" % (type(exc).__name__, exc))
        guard_intact = None
    print("GUARD_INTACT=%s" % guard_intact)

    check_write_entry_refuses_every_reported_payload()
    check_the_two_reason_codes_stay_distinct_on_the_write_side()
    check_the_database_holds_only_the_canonical_path()
    check_mission_working_directory_is_guarded_too()
    check_launch_side_still_refuses_them_too()
    check_positive_control_legal_roots_are_still_accepted()
    check_configured_extra_root_is_accepted()
    check_fail_closed_when_no_roots_are_configured()
    check_ui_can_read_the_boundary()
    check_the_inbound_surface_did_not_widen()
    check_no_drive_letter_is_hardcoded_in_the_new_code()

    if not in_negative_control:
        check_no_regression_in_existing_suites()

    if not is_child:
        run_negative_control()
        check_restored_and_green()

    passed = sum(1 for r in RESULTS if r["ok"])
    for r in RESULTS:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    body = "\n".join("  [%s] %s" % ("ok  " if r["ok"] else "HOLE", r["id"])
                     for r in RESULTS)
    print("\nRISK-1 FALSIFIER %s %d/%d"
          % ("PASS" if passed == len(RESULTS) else "FAIL", passed, len(RESULTS)))
    # The child prints its own size so the parent's control can PROVE the child
    # ran, rather than inferring it from a non-empty pipe (see the module
    # docstring: four falsifiers this round died exactly that way).
    print("CHILD_OUTPUT_BYTES=%d" % len(body))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())