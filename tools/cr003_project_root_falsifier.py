#!/usr/bin/env python3
"""CR-003 falsifier -- can a launch name a project root OUTSIDE the workspace?

THE DEFECT
----------
``host_launcher.launch`` checked only that ``intent.project_root`` EXISTED::

    if not intent.project_root or not Path(intent.project_root).is_dir():
        out["reason"] = "PROJECT_ROOT_MISSING"

Existence is not a boundary. ``project_root`` is not inert: it becomes the
child's ``cwd`` (``self._spawn(argv, cwd=intent.project_root, ...)``) AND it is
handed to the agent as ``--add-dir`` via the template's ``param_flags``
(``("--add-dir", "project_root")``), which is the directory the agent's
Read/Edit/Write/Glob/Grep/Bash tools are granted. A project row naming
``../../../../Windows/System32`` is therefore not a mistyped path, it is a
privilege grant.

Meanwhile ``_is_inside()`` -- a correct, already-written containment helper whose
docstring says "Used for the project-root binding" -- had ZERO call sites in
packages/ apps/ tools/ tests/ integrations/. It was dead code, and the binding it
was written for was unguarded.

THE FIX
-------
1. ``_is_inside`` is now CALLED, before the child is spawned. Its implementation
   is untouched (it was independently measured as correct on 6 cases including
   case-folding and cross-drive).
2. The boundary is the config item ``launcher.project_scope_base``. Its default is
   ``jcc_core.config.DEFAULT_PROJECT_SCOPE_BASE`` = the repository root, derived
   from ``Path(__file__).resolve().parents[2]``. No drive letter is hardcoded --
   this project has been broken by that three times (the F:/jev migration, the
   CR-002 GREEN port, the Get-JccPort regression).
3. Out of scope is REFUSED with a NEW reason code, ``PROJECT_ROOT_OUT_OF_SCOPE``,
   never ``PROJECT_ROOT_MISSING``. Those are opposite facts: "not there" versus
   "right there, and you may not have it". An operator told "no such directory"
   about a traversal probe would go and create the very directory the guard just
   declined to expose.

THE ORDERING IS PART OF THE FIX
-------------------------------
Containment is evaluated BEFORE existence, and that was measured, not chosen. On
this host ``../../../../Windows/System32`` and ``/etc/passwd`` both resolve to
paths that DO NOT EXIST (``D:\\Windows\\System32``, ``D:\\etc\\passwd``). An
existence-first guard answers ``PROJECT_ROOT_MISSING`` for both -- reporting an
attack as a typo. Scope-first also means an out-of-scope path cannot be probed
for existence at all: the verdict is identical whether or not it is there.

WHAT THIS SUITE PROVES
----------------------
Every refusal is paired with a POSITIVE CONTROL, because a guard that refuses
everything would score full marks here while proving nothing -- a real hazard,
since this round already had one falsifier rejected by an independent verifier
for asserting something that was trivially true. The positive cases must
genuinely launch, and ``NEGATIVE_CONTROL_WOULD_GO_RED`` records whether the
suite actually detects the guard's absence.

Run:
    D:/jev/.venv/Scripts/python.exe tools/cr003_project_root_falsifier.py
Exit 0 only if every case holds.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():  # pragma: no cover - environment guard
    PY = sys.executable

sys.path.insert(0, str(REPO / "packages"))

RESULTS: List[Dict[str, Any]] = []

#: The harmless stand-in template id, and the trivial script it execs. Created
#: once, in the repository, and removed in :func:`main` -- it must live INSIDE
#: the base for the in-scope positive control to be in scope at all.
CR003_TID = "cr003-self-v1"
CHILD_SCRIPT = REPO / "_cr003_child.py"
CHILD_BODY = "print('CR003_CHILD_RAN')\n"



def record(case_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": case_id, "ok": bool(ok), "detail": detail})


def fresh_env(**overrides: str) -> dict:
    """A child env with the signing key and PYTHONPATH pinned.

    Isolation matters here: the ambient environment of whoever runs this suite
    must not decide whether a case passes.

    The suite's OWN control variables are cleared unless a case sets them
    deliberately. Without this, ``JCC_CR003_NEGCTL`` set for the negative-control
    child is inherited by the LATER "is it green again?" child -- which then
    silently skips the two heaviest cases and reports a pass that did not
    examine them. A control flag that leaks past the control is a control that
    reports the wrong thing.
    """
    env = dict(os.environ)
    env.pop("JCC_CR003_CHILD", None)
    env.pop("JCC_CR003_NEGCTL", None)
    env["PYTHONPATH"] = str(REPO / "packages")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["JCC_DIRECTIVE_SIGNING_KEY"] = "cr003-falsifier-key-not-a-secret"
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# the real objects under test
# --------------------------------------------------------------------------- #
@dataclass
class Dir:
    """Minimal directive carrying exactly the fields the canonical form reads.

    Mirrors the shape used by the launcher's own suite; a stub rather than the
    real Directive so this file does not depend on signing internals it is not
    testing.
    """

    policy_table_version: str = "pt-1"
    policy_version: str = "pv-1"
    session_id: str = ""
    directive_id: str = "d-1"
    issued_at: float = 0.0
    ttl_s: float = 120.0
    nonce: str = ""
    action: str = "launch_mission"
    target_actuator: str = "host_launcher"
    evidence_hash: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    human_approval: Optional[bool] = None
    scope: str = ""
    signed: bool = True
    signature: Optional[str] = None


def make_launcher(scope_base: str):
    """A launcher whose registry CANNOT start a real agent.

    This is the most important implementation decision in the file, and it was
    learned the expensive way. A first version used the real
    ``LAUNCH_TEMPLATES`` here, reasoning that the scope cases are all refused
    before ``Popen``. That is true for the NEGATIVE cases and FALSE for the
    POSITIVE ones: an in-scope root passes the guard and spawns a real headless
    WorkBuddy CLI, which does not exit on its own. The suite then hung for 31
    minutes at 0.7s CPU with six live agents under the falsifier's PID, and had
    to be killed by exact PID.

    So every scope case runs against a DIRECT template that execs a trivial
    script instead. The guard under test is identical either way -- it sits
    upstream of ``_spawn`` and does not care what the template is -- and the
    suite now finishes in seconds and provably starts no agent. The real
    registry is exercised once, explicitly, by
    :func:`check_real_registry_is_refused_too`, where the root is out of scope
    and therefore cannot spawn.
    """
    from jcc_core import host_launcher as HL
    from jcc_core.signing import SIGNING_FORM_V2, DirectiveSigner

    signer = DirectiveSigner(b"cr003-falsifier-key-not-a-secret",
                             form=SIGNING_FORM_V2)
    tpl = HL.LaunchTemplate(
        template_id=CR003_TID, agent="workbuddy", launch_kind="direct",
        executable=PY, argv_prefix=("-I", str(CHILD_SCRIPT)),
        param_flags=(("--add-dir", "project_root"),), terminates=True)
    return HL.HostLauncher(signer=signer, templates={CR003_TID: tpl},
                           scope_base=scope_base)


def check_real_registry_is_refused_too() -> None:
    """The REAL template registry must refuse an out-of-scope root as well.

    Every other scope case runs against a harmless direct template, so this one
    exists to prove the guard is not an artifact of that substitution: with the
    genuine ``workbuddy-cli-headless-v1`` -- the template that really does carry
    ``--add-dir`` -- an out-of-scope root is still REFUSED, and nothing spawns.
    The root is out of scope precisely so this cannot start a real agent.
    """
    from jcc_core import host_launcher as HL
    from jcc_core.signing import SIGNING_FORM_V2, DirectiveSigner

    launcher = HL.HostLauncher(
        signer=DirectiveSigner(b"cr003-falsifier-key-not-a-secret",
                               form=SIGNING_FORM_V2),
        scope_base=str(REPO))
    base_it = make_intent("C:\\Windows\\System32", mission_id="m-realreg")
    it = HL.LaunchIntent(
        mission_id=base_it.mission_id, project_id=base_it.project_id,
        agent_id="workbuddy", adapter_id="workbuddy",
        project_root=base_it.project_root,
        template_id="workbuddy-cli-headless-v1", goal="unused",
        nonce=base_it.nonce, user_approved=True, policy_version="pv-1")
    try:
        out = launcher.launch(it, make_directive(it, launcher))
        record(
            "THE_REAL_TEMPLATE_REGISTRY_REFUSED_AN_OUT_OF_SCOPE_ROOT",
            out.get("reason") == "PROJECT_ROOT_OUT_OF_SCOPE"
            and out.get("spawned") is False
            and (scope_step(out) or {}).get("ok") is False,
            "real workbuddy-cli-headless-v1 + root=%r -> reason=%r spawned=%s; "
            "this is the template that actually carries --add-dir, so the guard "
            "is proven against the registry that matters and not only against "
            "the harmless stand-in the other cases use"
            % (it.project_root, out.get("reason"), out.get("spawned")),
        )
    finally:
        launcher.stop_all()



def make_intent(project_root: str, mission_id: str = "m-cr003"):
    from jcc_core import host_launcher as HL

    return HL.LaunchIntent(
        mission_id=mission_id, project_id="p-cr003", agent_id="workbuddy",
        adapter_id="workbuddy", project_root=project_root,
        template_id=CR003_TID,
        goal="Reply with exactly: CR003_OK",
        nonce="n-%d" % time.time_ns(), user_approved=True,
        policy_version="pv-1")


def make_directive(intent, launcher):
    """A directive that satisfies every VERIFY check, so the scope step is reached.

    This matters: a refusal for the wrong reason (bad signature, replay) would
    make a negative case pass for free. Each case therefore also asserts that
    the refusal came from the ``project_root`` STEP, not merely that the
    classification is REFUSED.
    """
    from jcc_core import host_launcher as HL
    from jcc_core.signing import SIGNING_FORM_V2, sign_directive

    tpl = launcher.templates.get(intent.template_id)
    head, _ = HL.build_head(tpl) if tpl else (None, "NO_TEMPLATE")
    d = Dir(session_id=intent.mission_id, issued_at=time.time(),
            nonce=intent.nonce, evidence_hash=intent.evidence_hash(),
            params={"capability": HL.CAPABILITY_USER_LAUNCH,
                    "project_id": intent.project_id,
                    "adapter_id": intent.adapter_id,
                    "project_root": intent.project_root,
                    "args_hash": HL.args_hash(tpl, head, goal=intent.goal,
                                              params=intent.params())
                    if tpl and head else ""},
            human_approval=True, scope="project:%s" % intent.project_root)
    d.signature = sign_directive(d, b"cr003-falsifier-key-not-a-secret",
                                 form=SIGNING_FORM_V2)
    return d


def scope_step(out: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ``project_root`` step from a launch outcome, or None if absent."""
    for s in out.get("steps") or []:
        if s.get("step") == "project_root":
            return s
    return None


# --------------------------------------------------------------------------- #
# 1. the guard is wired in at all (the defect: _is_inside had no call sites)
# --------------------------------------------------------------------------- #
def check_is_inside_is_actually_called() -> None:
    """Static proof of wiring, checked on the AST so a comment cannot satisfy it.

    Before the fix ``_is_inside`` was defined and never called. Asserting on the
    SOURCE TEXT would be satisfied by the word appearing in a docstring, so this
    counts real call nodes.
    """
    import ast

    src = (REPO / "packages" / "jcc_core" / "host_launcher.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "_is_inside"]
    # And the containment entry point must be called on the launch path.
    guard_calls = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name)
                   and n.func.id == "project_scope_violation"]
    record(
        "IS_INSIDE_HAS_REAL_CALL_SITES_NOT_JUST_A_DEFINITION",
        len(calls) >= 1 and len(guard_calls) >= 2,
        "ast Call nodes: _is_inside=%d (was 0 before CR-003), "
        "project_scope_violation=%d (preview + launch) -- counted as calls, not "
        "as text, so a docstring mentioning the name cannot pass this"
        % (len(calls), len(guard_calls)),
    )


def check_helper_implementation_was_not_rewritten() -> None:
    """The fix must WIRE ``_is_inside`` on, not quietly rewrite it.

    A guard that is also edited cannot be reviewed independently: a weakened
    ``_is_inside`` would make every case below pass for the wrong reason. These
    are the shape facts the earlier independent review established.
    """
    from jcc_core.host_launcher import _is_inside

    checks = {
        "resolves_before_comparing": _is_inside("D:/jev/packages/../packages",
                                                "D:/jev") is True,
        "case_folded_on_windows": _is_inside(str(REPO).upper(), str(REPO)) is True,
        "cross_drive_refused": _is_inside("Z:/somewhere/else", str(REPO)) is False,
        "parent_itself_allowed": _is_inside(str(REPO), str(REPO)) is True,
        "traversal_refused": _is_inside("D:/jev/../etc", str(REPO)) is False,
    }
    record(
        "IS_INSIDE_STILL_HAS_THE_MEASURED_PROPERTIES_IT_WAS_VERIFIED_FOR",
        all(checks.values()),
        "%r -- these are the properties the earlier independent review "
        "established; if a rewrite broke one, the cases below would be measuring "
        "the rewrite rather than the wiring" % checks,
    )


# --------------------------------------------------------------------------- #
# 2. NEGATIVE cases -- the attack surface, each must be OUT_OF_SCOPE
# --------------------------------------------------------------------------- #
def check_traversal_and_crossdrive_roots_are_refused() -> None:
    """The four reported attack strings, plus the Windows spelling variants.

    Each case asserts THREE things, because any one alone is gameable:
      * ``classification == "REFUSED"``
      * ``reason == "PROJECT_ROOT_OUT_OF_SCOPE"`` (NOT PROJECT_ROOT_MISSING --
        a traversal probe reported as a typo is the defect coming back)
      * the refusal came from the ``project_root`` STEP (so it was not refused
        for an unrelated reason while still happening to be REFUSED)
      * nothing was spawned
    """
    base = str(REPO)
    cases = [
        ("posix_traversal", "../../../../Windows/System32"),
        ("windows_traversal", "..\\..\\..\\..\\Windows\\System32"),
        ("cross_drive", "C:\\Windows\\System32"),
        ("posix_absolute", "/etc/passwd"),
        # Measured on this host: these resolve to D:\Windows\System32 and
        # D:\etc\passwd, which DO NOT EXIST. They are here to pin the ORDERING:
        # an existence-first guard calls these PROJECT_ROOT_MISSING.
        ("traversal_to_a_path_that_does_not_exist", "../../../../Windows/System32"),
        ("posix_path_that_does_not_exist", "/etc/passwd"),
        # The dot-segment spelling of the same escape.
        ("dot_segment_escape", str(REPO) + "/../Windows/System32"),
    ]
    launcher = make_launcher(base)
    detail_parts = []
    ok_all = True
    for label, root in cases:
        it = make_intent(root, mission_id="m-%s" % label)
        out = launcher.launch(it, make_directive(it, launcher))
        step = scope_step(out)
        good = (out.get("classification") == "REFUSED"
                and out.get("reason") == "PROJECT_ROOT_OUT_OF_SCOPE"
                and out.get("spawned") is False
                and step is not None and step.get("ok") is False)
        ok_all = ok_all and good
        detail_parts.append("%s=%s(reason=%s,spawned=%s)"
                            % (label, "REFUSED" if good else "NOT_REFUSED",
                               out.get("reason"), out.get("spawned")))
    record(
        "EVERY_TRAVERSAL_SPELLING_IS_REFUSED_AS_OUT_OF_SCOPE_NOT_MISSING",
        ok_all,
        "%d cases: %s | base=%s" % (len(cases), "; ".join(detail_parts), base),
    )


def check_out_of_scope_message_is_actionable() -> None:
    """A refusal an operator cannot act on is a half-delivered control.

    The message must name the offending value, the config item that defines the
    boundary, the boundary's current value, and the fact that the default is
    derived rather than typed -- the four things needed to fix it.
    """
    base = str(REPO)
    launcher = make_launcher(base)
    root = "C:\\Windows\\System32"
    it = make_intent(root, mission_id="m-msg")
    out = launcher.launch(it, make_directive(it, launcher))
    msg = (scope_step(out) or {}).get("detail", "")
    required = {
        "names_the_root": root in msg,
        "names_the_config_item": "launcher.project_scope_base" in msg,
        "names_the_current_base": base in msg,
        "explains_the_grant": "--add-dir" in msg,
        "says_how_to_widen": "set launcher.project_scope_base" in msg,
        "says_the_default_is_derived": "derived from" in msg,
    }
    record(
        "OUT_OF_SCOPE_REFUSAL_NAMESTHE_CONFIG_ITEM_VALUE_AND_EXPECTED_RANGE",
        out.get("reason") == "PROJECT_ROOT_OUT_OF_SCOPE" and all(required.values()),
        "checks=%r | message[%d chars]: %s..."
        % (required, len(msg), msg[:150]),
    )


def check_scope_is_checked_before_existence_not_after() -> None:
    """Pins the ORDERING, which is where a future edit would quietly undo this.

    Two roots that DO NOT EXIST on this host are used. A scope-first guard calls
    them OUT_OF_SCOPE; an existence-first guard calls them MISSING. This case
    fails if anyone moves the containment check below the ``is_dir()`` check --
    and it is the check most likely to be moved, because the existence check was
    here first and looks like the load-bearing one.
    """
    from jcc_core.host_launcher import project_scope_violation

    base = str(REPO)
    nonexistent_escapes = ["../../../../Windows/System32", "/etc/passwd"]
    launcher = make_launcher(base)
    reasons = []
    for i, root in enumerate(nonexistent_escapes):
        it = make_intent(root, mission_id="m-ord%d" % i)
        out = launcher.launch(it, make_directive(it, launcher))
        reasons.append(out.get("reason"))
    # And the helper itself must refuse them regardless of existence.
    helper_refuses = all(project_scope_violation(r, base) for r in nonexistent_escapes)
    record(
        "SCOPE_IS_EVALUATED_BEFORE_EXISTENCE_SO_PROBES_ARE_NOT_REPORTED_AS_TYPOS",
        reasons == ["PROJECT_ROOT_OUT_OF_SCOPE"] * len(nonexistent_escapes)
        and helper_refuses,
        "nonexistent escapes %r -> reasons=%r (PROJECT_ROOT_MISSING here would "
        "mean existence is being tested first); helper refuses all=%s. On this "
        "host these resolve to D:\\Windows\\System32 and D:\\etc\\passwd, neither "
        "of which exists -- which is exactly why the order is observable."
        % (nonexistent_escapes, reasons, helper_refuses),
    )


# --------------------------------------------------------------------------- #
# 3. POSITIVE CONTROLS -- a guard that refuses everything must not pass
# --------------------------------------------------------------------------- #
def check_paths_inside_the_base_are_still_launched() -> None:
    """POSITIVE CONTROL: in-scope roots must survive the guard.

    Without this, a launcher that refused 100%% of requests would score 7/7 on
    the cases above while being completely broken. The verdict asserted is
    "NOT REFUSED for scope", which is deliberately weaker than "spawned" so the
    case stays honest on a host where no node/CLI runtime is present.
    """
    base = str(REPO)
    launcher = make_launcher(base)
    in_scope = [
        ("repo_root", str(REPO)),
        ("packages_dir", str(REPO / "packages")),
        # Windows does not distinguish case, so D:\\JEV\\packages IS the same
        # directory. A guard that refused this would be refusing the workspace.
        ("case_folded_variant", str(REPO).swapcase() + "\\packages"),
        # Windows strips a trailing space or a trailing dot, so these resolve to
        # packages/ as well. Asserted per MEASURED Windows behaviour, not per
        # what would be tidier: realpath() reports packages/ for both.
        ("trailing_space", str(REPO / "packages") + " "),
        ("trailing_dot", str(REPO / "packages") + "\\."),
    ]
    detail = []
    ok_all = True
    for label, root in in_scope:
        it = make_intent(root, mission_id="m-pos-%s" % label)
        out = launcher.launch(it, make_directive(it, launcher))
        refused_for_scope = out.get("reason") == "PROJECT_ROOT_OUT_OF_SCOPE"
        step = scope_step(out)
        # Either the scope step passed, or the launch never got that far because
        # something earlier refused -- both are non-scope outcomes, which is what
        # "the guard did not refuse this" means.
        ok = not refused_for_scope
        ok_all = ok_all and ok
        detail.append("%s=%s" % (label, "ALLOWED" if ok else "REFUSED_FOR_SCOPE"))
    record(
        "POSITIVE_CONTROL_IN_SCOPE_ROOTS_ARE_NOT_REFUSED_BY_THE_GUARD",
        ok_all,
        "%d in-scope roots: %s | base=%s (a guard that refused everything would "
        "fail exactly here)" % (len(in_scope), "; ".join(detail), base),
    )


def check_real_child_still_starts_in_scope() -> None:
    """POSITIVE CONTROL, strongest form: a REAL process is actually started.

    The case above only proves the guard did not refuse. This one launches the
    suite's own trivial script as the child, inside the base, and requires a real
    pid. A guard stuck closed would leave ``pid`` at None and fail here even
    though it passed the softer case. The child is a two-line print, so it exits
    on its own -- this suite must not leave processes running.
    """
    launcher = make_launcher(str(REPO))
    work = REPO                            # must be INSIDE the base
    it = make_intent(str(work), mission_id="m-real")
    try:
        out = launcher.launch(it, make_directive(it, launcher))
        pid = out.get("pid")
        record(
            "A_REAL_CHILD_STILL_SPAWNS_INSIDE_THE_BOUNDARY",
            out.get("spawned") is True and isinstance(pid, int) and pid > 0
            and out.get("reason") is None,
            "spawned=%s pid=%r reason=%r classification=%r -- an in-scope launch "
            "must still reach Popen, or the guard is fail-closed on everything"
            % (out.get("spawned"), pid, out.get("reason"),
               out.get("classification")),
        )
    finally:
        launcher.stop_all()


def check_vanished_root_in_scope_is_still_PROJECT_ROOT_MISSING() -> None:
    """The two reason codes must stay DISTINCT, in the direction that matters.

    A root INSIDE the base that does not exist is a MISSING root, not an
    out-of-scope one. If both collapsed to one code, an operator could not tell
    a typo from an attack -- and the fix for each is opposite.
    """
    base = str(REPO)
    launcher = make_launcher(base)
    gone = str(REPO / "packages" / "cr003-definitely-not-here")
    it = make_intent(gone, mission_id="m-gone")
    out = launcher.launch(it, make_directive(it, launcher))
    record(
        "A_VANISHED_ROOT_INSIDE_THE_BOUNDARY_IS_PROJECT_ROOT_MISSING",
        out.get("reason") == "PROJECT_ROOT_MISSING"
        and out.get("classification") == "REFUSED",
        "root=%r -> reason=%r classification=%r (OUT_OF_SCOPE here would mean "
        "the two codes had merged)"
        % (gone, out.get("reason"), out.get("classification")),
    )


def check_preview_does_not_lie_about_scope() -> None:
    """A preview that says "launchable" for a root the commit refuses is a lie.

    ``preview`` is what a human approves against, so if it omits the scope
    problem the operator is shown a green for a mission that cannot start. This
    is the same "a preview that lies is worse than no preview" rule the rest of
    this codebase applies to replay detection.
    """
    from jcc_core import host_launcher as HL
    from jcc_core.signing import SIGNING_FORM_V2, DirectiveSigner

    base = str(REPO)
    # The launcher must carry CR003_TID, because make_intent() names it. An
    # earlier version built a launcher over the REAL registry while the intent
    # asked for the stand-in, so every preview came back UNKNOWN_TEMPLATE -- the
    # guard was never even reached and the case failed for the wrong reason.
    launcher = make_launcher(base)
    assert CR003_TID in launcher.templates, (
        "the preview case's launcher must carry the template its intents name")
    bad = launcher.preview(make_intent("C:\\Windows\\System32",
                                       mission_id="m-pv-bad"))
    good = launcher.preview(make_intent(str(REPO), mission_id="m-pv-good"))
    bad_codes = [p["code"] for p in bad.get("problems") or []]
    good_codes = [p["code"] for p in good.get("problems") or []]
    record(
        "PREVIEW_REPORTS_THE_SCOPE_PROBLEM_AND_STAYS_LAUNCHABLE_IN_SCOPE",
        bad.get("launchable") is False
        and "PROJECT_ROOT_OUT_OF_SCOPE" in bad_codes
        and good.get("launchable") is True
        and "PROJECT_ROOT_OUT_OF_SCOPE" not in good_codes,
        "out-of-scope: launchable=%s problems=%r | in-scope: launchable=%s "
        "problems=%r" % (bad.get("launchable"), bad_codes,
                         good.get("launchable"), good_codes),
    )


# --------------------------------------------------------------------------- #
# 4. the configuration surface
# --------------------------------------------------------------------------- #
def check_scope_base_default_is_derived_not_typed() -> None:
    """No drive letter in the default, and it moves with the checkout.

    Hardcoding ``D:\\jev`` would be the third such incident in this project's
    history. Proven by AST (not grep, which would match the prose in a
    docstring) and by relocation: a copy of the package tree in a temp directory
    derives a base under THAT directory.
    """
    import ast

    from jcc_core.config import DEFAULT_PROJECT_SCOPE_BASE, REPO_ROOT

    src = (REPO / "packages" / "jcc_core" / "config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    literal_drive = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if len(v) > 2 and v[1] == ":" and v[0].isalpha() and v[2] in "\\/":
                literal_drive.append((node.lineno, v[:40]))
    # The assignment must be an expression over REPO_ROOT, not a constant.
    derived = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Name)
                        and t.id == "DEFAULT_PROJECT_SCOPE_BASE"):
                    derived = "REPO_ROOT" in ast.unparse(node.value)
    record(
        "SCOPE_BASE_DEFAULT_IS_DERIVED_FROM_THE_CHECKOUT_WITH_NO_DRIVE_LETTER",
        derived and not literal_drive
        and DEFAULT_PROJECT_SCOPE_BASE == str(REPO_ROOT),
        "DEFAULT_PROJECT_SCOPE_BASE=%r == str(REPO_ROOT)=%r -> %s; assigned from "
        "%s; drive-letter string literals in config.py code: %r"
        % (DEFAULT_PROJECT_SCOPE_BASE, str(REPO_ROOT),
           DEFAULT_PROJECT_SCOPE_BASE == str(REPO_ROOT),
           "an expression containing REPO_ROOT" if derived else "SOMETHING ELSE",
           literal_drive),
    )


def check_scope_base_relocates_with_the_checkout() -> None:
    """The base must follow the code, measured by MOVING the package.

    This is the property a hardcoded path cannot have, and it is checked by
    copying the package into a temp tree and importing it THERE. If the default
    were typed, the relocated module would still report the original drive path
    while running from somewhere else entirely.

    THE LAYOUT MUST MIRROR THE REAL ONE, and getting this wrong is a trap worth
    naming. ``REPO_ROOT`` is ``parents[2]`` -- ``<root>/packages/jcc_core/
    config.py``. Copying just ``jcc_core`` to ``<tmp>/jcc_core`` therefore
    resolves ``parents[2]`` to the GRANDPARENT of wherever the copy landed, and
    the first version of this case asserted the base was the temp dir itself. It
    failed, and it was the TEST that was wrong, not the derivation: the copy has
    to sit at ``<tmp>/packages/jcc_core`` for ``parents[2]`` to mean what it
    means in the repository. Asserting the wrong layout would have "proved" the
    guard relocates while actually proving nothing.
    """
    import json as _json

    from jcc_core.config import REPO_ROOT

    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr003_reloc_"))
    try:
        pkg_parent = tmp / "packages"          # <root>/packages/jcc_core/...
        pkg_parent.mkdir(parents=True, exist_ok=True)
        dst = pkg_parent / "jcc_core"
        shutil.copytree(str(REPO / "packages" / "jcc_core"), str(dst),
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        probe = tmp / "probe.py"
        # Written with write_text (not a shell heredoc): a heredoc that fails to
        # create the file leaves python reading STDIN, which blocks forever
        # instead of erroring -- and that is exactly how this case hung for
        # minutes on the first run.
        probe.write_text(
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from jcc_core.config import DEFAULT_PROJECT_SCOPE_BASE, REPO_ROOT\n"
            "print(json.dumps({'base': DEFAULT_PROJECT_SCOPE_BASE,\n"
            "                  'repo': str(REPO_ROOT)}))\n",
            encoding="utf-8")
        # PYTHONPATH is deliberately DROPPED for the child: leaving it set would
        # put the real repo ahead of the relocated copy on sys.path and the
        # probe would measure the original checkout while claiming to measure
        # the copy.
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # sys.path must point at the directory CONTAINING the package, which is
        # ``<tmp>/packages`` -- not at ``<tmp>``. Pointing it at the temp root is
        # the natural-looking mistake and it imports the REAL jcc_core instead
        # (or fails), which would measure the original checkout while claiming to
        # measure the copy.
        proc = subprocess.run([PY, str(probe), str(pkg_parent)], env=env,
                              cwd=str(tmp),
                              text=True, errors="replace",
                              stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=180)
        line = [ln for ln in (proc.stdout or "").splitlines()
                if ln.startswith("{")]
        data = _json.loads(line[-1]) if line else {}
        base = data.get("base", "")
        if not line:
            # Say WHY rather than reporting a bare False: an import error here
            # means the probe measured nothing at all, and a silent False would
            # read like "the guard does not relocate", which is the opposite
            # conclusion.
            record(
                "SCOPE_BASE_FOLLOWS_A_MOVED_CHECKOUT", False,
                "the relocated probe produced no result (rc=%s). It must import "
                "the COPY, so sys.path must be <tmp>/packages. Probe output: %s"
                % (proc.returncode, (proc.stdout or "<none>").strip()[:300]),
            )
            return
        record(
            "SCOPE_BASE_FOLLOWS_A_MOVED_CHECKOUT",
            proc.returncode == 0 and base
            and Path(base) == Path(str(tmp)).resolve()
            and base != str(REPO_ROOT),
            "imported a COPY of jcc_core from %s (mirroring <root>/packages/"
            "jcc_core, so parents[2] means what it means in the repo) -> "
            "DEFAULT_PROJECT_SCOPE_BASE=%r, equal to the relocated root=%s, and "
            "NOT the original checkout %r. A typed path would have reported the "
            "original location while executing from the copy."
            % (tmp, base, Path(base) == Path(str(tmp)).resolve(),
               str(REPO_ROOT)),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_config_item_is_read_and_enforced() -> None:
    """The config item must be load-bearing, not decorative.

    A key that parses but never reaches the launcher is worse than no key: it
    reads like a control. So this writes a config that names a NARROW base and
    asserts the launcher's boundary actually moved -- and that the shipped
    product config parses with the item present.
    """
    from jcc_core.config import REPO_ROOT, load_config

    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr003_cfg_"))
    try:
        narrow = tmp / "narrow.yaml"
        narrow.write_text(
            "server:\n  host: 127.0.0.1\n  port: 4779\n"
            "launcher:\n  project_scope_base: %s\n"
            % (REPO / "packages").as_posix(), encoding="utf-8")
        declared = load_config(str(narrow)).launcher.project_scope_base

        # The launcher must carry CR003_TID (see make_intent), otherwise the
        # template lookup refuses first and this case never measures the BOUNDARY.
        launcher = make_launcher(declared)
        assert CR003_TID in launcher.templates
        # packages/ is now the boundary, so packages/ is IN and its parent is OUT.
        it_in = make_intent(str(REPO / "packages"), mission_id="m-cfg-in")
        out_in = launcher.launch(it_in, make_directive(it_in, launcher))
        it_out = make_intent(str(REPO / "config"), mission_id="m-cfg-out")
        out_out = launcher.launch(it_out, make_directive(it_out, launcher))
        launcher.stop_all()

        # And the SHIPPED product config must load with the item present.
        shipped = load_config(str(REPO / "config" / "jcc-v1.3.yaml"))
        shipped_base = shipped.launcher.project_scope_base
        raw_has_key = "project_scope_base" in (
            REPO / "config" / "jcc-v1.3.yaml").read_text(encoding="utf-8")
        record(
            "CONFIG_ITEM_IS_READ_FROM_YAML_AND_CHANGES_THE_ENFORCED_BOUNDARY",
            declared == str(REPO / "packages")
            and out_in.get("reason") != "PROJECT_ROOT_OUT_OF_SCOPE"
            and out_out.get("reason") == "PROJECT_ROOT_OUT_OF_SCOPE"
            and raw_has_key and shipped_base == str(REPO_ROOT),
            "narrow config declared base=%r; packages/ -> reason=%r (in scope), "
            "config/ -> reason=%r (out of scope under the narrowed base); "
            "shipped config/jcc-v1.3.yaml declares the key=%s and loads base=%r"
            % (declared, out_in.get("reason"), out_out.get("reason"),
               raw_has_key, shipped_base),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_misconfigured_base_fails_closed() -> None:
    """A base that does not exist must refuse everything, not widen to the disk.

    The failure mode of a typo in a security control is normally the dangerous
    one. ``_is_inside`` returns False for a nonexistent parent, so this holds by
    construction -- but "by construction" is a claim, so it is measured.
    """
    launcher = make_launcher(str(REPO / "cr003-no-such-base"))
    assert CR003_TID in launcher.templates
    it = make_intent(str(REPO / "packages"), mission_id="m-badbase")
    out = launcher.launch(it, make_directive(it, launcher))
    launcher.stop_all()
    record(
        "A_MISCONFIGURED_BASE_REFUSES_EVERYTHING_RATHER_THAN_WIDENING",
        out.get("reason") == "PROJECT_ROOT_OUT_OF_SCOPE"
        and out.get("spawned") is False,
        "base=%r (nonexistent) + root=%r -> reason=%r spawned=%s. Fail-CLOSED: a "
        "typo in the boundary must not remove the boundary."
        % (launcher.scope_base, it.project_root, out.get("reason"),
           out.get("spawned")),
    )


# --------------------------------------------------------------------------- #
# 5. NEGATIVE CONTROL -- does this suite actually detect the guard's absence?
# --------------------------------------------------------------------------- #
NEGATIVE_CONTROL_MARKER = "CR003_NEGATIVE_CONTROL"


def run_negative_control() -> None:
    """Temporarily remove the guard call and confirm this suite goes RED.

    A falsifier that cannot fail is not evidence, and this round already lost one
    to an independent verifier for asserting something trivially true. So: make
    the source call ``project_scope_violation`` a no-op, re-run the whole suite in
    a child process, and require that it FAILS. Then restore, and require green.

    The edit is done with a backup copy and a verified restore, because this
    repository has already had three incidents of one member's uncommitted work
    being overwritten by another's. A falsifier that damages the tree it is
    proving things about would be its own defect.
    """
    target = REPO / "packages" / "jcc_core" / "host_launcher.py"
    # newline="" on BOTH read and write. This is not a detail: read_text()
    # applies universal-newline translation, so a CRLF file comes back as LF,
    # and writing that back with default newline handling CONVERTS the file to
    # CRLF. The first version compared read_bytes() against the translated text
    # and reported the tree as "NOT byte-identical, treat the working tree as
    # suspect", which was a false alarm about a line-ending conversion. Pinning
    # newline="" on both sides makes the restore byte-exact and the check real.
    original = target.read_text(encoding="utf-8", newline="")
    backup = target.with_suffix(".py.cr003-backup")

    # THE SABOTAGE MUST STAY SYNTACTICALLY VALID, and getting this wrong made
    # the control meaningless for three runs. Rewriting only the `if` line to
    # `if True:` orphans the `return ""` beneath it, so the child died with
    # IndentationError at IMPORT time: it never reached a single case, reported
    # "0 HOLE(s) / NO SUMMARY LINE", and the control could not tell "the guard
    # was not detected" from "the child did not run". A negative control whose
    # child crashes proves nothing.
    #
    # So the WHOLE two-line block is replaced, and both the if and its return are
    # neutralised together -- the function still parses, still returns, and now
    # reports "inside scope" for everything. Line endings are handled by
    # matching the block with a regex rather than a literal "\n" join.
    pattern = re.compile(
        r"[ \t]*if _is_inside\(str\(project_root\), base\):[ \t]*\r?\n"
        r"[ \t]*return \"\"[ \t]*\r?\n")
    hits = len(pattern.findall(original))
    if hits != 1:
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED",
            False,
            "expected exactly ONE containment block in %s, found %d -- the "
            "control must not silently no-op, and must not guess which "
            "occurrence to sabotage" % (target.name, hits),
        )
        return
    eol = "\r\n" if "\r\n" in original else "\n"
    # `if True:` needs an INDENTED body. The original `return ""` is already the
    # body but sits at the same level as the `if` only because the `if` line was
    # replaced -- so the replacement must indent it by one level (8 spaces) or
    # the module raises IndentationError at import. Getting this wrong was the
    # third distinct way this control failed to actually test the guard:
    # orphaned return (crash), bare name (NameError), unindented return (crash).
    replacement = ('    if True:  # %s%s' % (NEGATIVE_CONTROL_MARKER, eol)
                   + '        return ""%s' % eol)
    # Use a plain str replacement via a lambda: in re.sub the replacement string
    # treats backslashes as escapes, which silently ate the "#" and turned the
    # marker into a bare name (NameError at import of the sabotaged module --
    # the child died again, for a third different reason). A lambda's return
    # value is used literally.
    sabotaged = pattern.sub(lambda _m: replacement, original, count=1)
    if sabotaged == original:
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED",
            False,
            "the containment block was located but not replaced; the control "
            "would have reported a pass it did not earn",
        )
        return
    # Prove the sabotaged source still COMPILES before running anything against
    # it. A control must not hand a broken file to its own child. Compiling is
    # not enough on its own -- a replacement string mangled by re.sub once
    # produced code that compiled fine and raised NameError at import, killing
    # the child for a reason that had nothing to do with the guard -- so the
    # substituted text is also checked for the two things that must survive.
    try:
        compile(sabotaged, str(target), "exec")
    except SyntaxError as exc:
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED", False,
            "the sabotage would not compile (%s: %s), so the child would die at "
            "import and the control would measure a crash rather than the guard"
            % (type(exc).__name__, exc),
        )
        return
    if NEGATIVE_CONTROL_MARKER not in sabotaged or "#" not in sabotaged.split(
            NEGATIVE_CONTROL_MARKER)[0][-40:]:
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED", False,
            "the sabotage compiled but does not carry an inert '# %s' comment, "
            "so the child would raise NameError at import instead of exercising "
            "the guard" % NEGATIVE_CONTROL_MARKER,
        )
        return
    try:
        backup.write_text(original, encoding="utf-8", newline="")
        target.write_text(sabotaged, encoding="utf-8", newline="")
        # The child's stdout goes to a FILE. A pipe is the wrong tool for a child
        # that may be killed: a killed child's pipe buffer is lost, so an earlier
        # version of this control read an EMPTY string and reported "0 HOLE(s) /
        # NO SUMMARY LINE" for a run that had in fact produced 4290 bytes and
        # seven holes. That is the most dangerous possible misreading -- it looks
        # exactly like "the guard is not detected". A file outlives the child.
        child_log = REPO / "runtime" / "cr003_negctl_child.log"
        child_log.parent.mkdir(parents=True, exist_ok=True)
        with open(child_log, "w", encoding="utf-8", errors="replace") as handle:
            proc = subprocess.run(
                [PY, str(REPO / "tools" / "cr003_project_root_falsifier.py")],
                env=fresh_env(CODEBUDDY_SAFE_DELETE_ENABLED="0",
                              JCC_CR003_CHILD="1",
                              JCC_CR003_NEGCTL="1"),
                cwd=str(REPO), text=True, errors="replace",
                stdin=subprocess.DEVNULL,
                stdout=handle, stderr=subprocess.STDOUT,
                timeout=900)
        try:
            out = child_log.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            out = ""
            record("NEGATIVE_CONTROL_CHILD_LOG_WAS_READABLE", False,
                   "could not read the child's own output at %s: %s -- a "
                   "negative control that cannot see what its child reported is "
                   "not a control" % (child_log, exc))
        # NOTE: there is deliberately NO `out = proc.stdout or ""` here. When
        # stdout is redirected to a file handle, CompletedProcess.stdout is None,
        # so that line overwrote the log with the empty string -- and the control
        # reported "0 HOLE(s) / NO SUMMARY LINE" for a child whose log held 4318
        # bytes and seven holes. The line was left over from the pipe-based
        # version and was the single reason this control read as "guard not
        # detected" when the guard was in fact detected perfectly.
        red = proc.returncode != 0
        summary = [ln for ln in out.splitlines() if "CR-003 FALSIFIER" in ln]
        # The marker is padded -- "  [HOLE] name" and "  [ok  ] name" are both
        # five characters wide -- so a startswith("[HOLE]") test on the RAW line
        # finds NOTHING and the control reports "0 HOLE(s)" on a run that is
        # entirely holes. That is the worst possible failure for a negative
        # control: it looks like the guard was not detected. Match the token
        # after stripping, never a column.
        holes = [ln.strip() for ln in out.splitlines()
                 if ln.strip().startswith("[HOLE]")]
        # PROVE the sabotage was actually in force for that child, by reading
        # the child's own report rather than trusting that we wrote the file.
        # A control that assumes its own setup worked is not a control: if the
        # write silently failed, the child would pass, and "child passed" would
        # be indistinguishable from "guard not detected" -- the two conclusions
        # that matter most here are opposite, so the distinction cannot be left
        # to assumption.
        guard_sabotaged = "ast Call nodes: _is_inside=0" in out
        spawned_out_of_scope = any(
            "spawned=True" in ln and "NOT_REFUSED" in ln for ln in out.splitlines())
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED",
            red and bool(holes) and bool(summary) and guard_sabotaged,
            "with the containment branch short-circuited to 'if True', the suite "
            "exited rc=%s with %d HOLE(s) and %d bytes of output (child log: "
            "runtime/cr003_negctl_child.log); the child's own report confirms "
            "_is_inside call sites fell to 0, so the sabotage WAS in force. "
            "Holes: %s | %s. Note %s"
            % (proc.returncode, len(holes), len(out),
               "; ".join(h.split("] ", 1)[-1][:52] for h in holes[:3]),
               summary[-1] if summary else "NO SUMMARY LINE PRODUCED",
               "at least one out-of-scope root was actually SPAWNED without the "
               "guard -- the concrete effect being fixed"
               if spawned_out_of_scope else "no spawn observed"),
        )
    finally:
        # Restore from the in-memory original, then verify the tree is byte-exact.
        target.write_text(original, encoding="utf-8", newline="")
        try:
            backup.unlink()
        except OSError:
            pass
        restored = target.read_text(encoding="utf-8", newline="")
        if restored != original:  # pragma: no cover - would be a real incident
            record("SOURCE_RESTORED_AFTER_NEGATIVE_CONTROL", False,
                   "host_launcher.py is NOT identical to its pre-sabotage "
                   "content (%d vs %d chars) -- treat the working tree as suspect"
                   % (len(restored), len(original)))
        else:
            record("SOURCE_RESTORED_AFTER_NEGATIVE_CONTROL", True,
                   "host_launcher.py restored byte-exactly after the sabotage "
                   "(%d chars, verified by re-read); the negative control left "
                   "no residue in the tree it was auditing" % len(original))


def check_restored_and_green() -> None:
    """After the negative control, the suite must be green again.

    A falsifier left in the sabotaged state would make the negative control
    meaningless while still reporting numbers, so the restore is itself a case.
    """
    proc = subprocess.run([PY, str(REPO / "tools" /
                                   "cr003_project_root_falsifier.py")],
                          env=fresh_env(CODEBUDDY_SAFE_DELETE_ENABLED="0",
                                        JCC_CR003_CHILD="1"),
                          cwd=str(REPO), text=True, errors="replace",
                          stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=900)
    out = proc.stdout or ""
    summary = [ln for ln in out.splitlines() if "CR-003 FALSIFIER" in ln]
    record(
        "SUITE_IS_GREEN_AGAIN_AFTER_THE_GUARD_IS_RESTORED",
        proc.returncode == 0 and bool(summary) and "PASS" in summary[-1],
        "rc=%s | %s" % (proc.returncode, summary[-1] if summary else "no summary"),
    )


# --------------------------------------------------------------------------- #
# 6. no regression in the suites that encode the launch contract
# --------------------------------------------------------------------------- #
def check_no_regression_in_existing_suites() -> None:
    """The launcher + launch-control suites must stay green.

    A PER-RUN basetemp, and this is not cosmetic: ``tools/conftest.py`` computes a
    FIXED basetemp path and creates it with ``mkdir(parents=True,
    exist_ok=False)``, so a second run against a leftover directory dies with
    FileExistsError on every test -- failures that look exactly like a product
    regression and are nothing of the kind.
    """
    basetemp = Path(tempfile.mkdtemp(prefix="jcc_cr003_pytest_"))
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest",
             "packages/jcc_core/tests/test_host_launcher_v1_3.py",
             "packages/jcc_core/tests/test_launch_control_v1_3.py",
             "-q", "-p", "no:cacheprovider",
             "--basetemp", str(basetemp / "run")],
            cwd=str(REPO),
            env=fresh_env(CODEBUDDY_SAFE_DELETE_ENABLED="0",
                          JCC_PYTEST_ALLOW_DIRTY_ENV="1"),
            text=True, errors="replace", stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.STDOUT, timeout=900)
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:]
        collided = "FileExistsError" in (proc.stdout or "")
        record(
            "EXISTING_LAUNCHER_SUITES_STILL_PASS",
            proc.returncode == 0 and not collided,
            "rc=%s basetemp_collision=%s | %s"
            % (proc.returncode, collided, tail[0][:160] if tail else ""),
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    # A child re-invocation (from the negative control) runs everything EXCEPT
    # the control itself, or the control would recurse without end.
    is_child = os.environ.get("JCC_CR003_CHILD") == "1"
    # The negative control REMOVES the guard, and one case's safety depends on
    # that guard: check_real_registry_is_refused_too uses the real
    # workbuddy-cli-headless-v1 template with an out-of-scope root, precisely
    # because the guard refuses it. Run that case with the guard gone and it
    # spawns live headless agents -- which is exactly how this suite hung for 31
    # minutes on its first run. A negative control must therefore never execute a
    # case whose safety depends on the thing being removed, so it is skipped here
    # and only here. It still runs in every normal (non-control) invocation.
    in_negative_control = os.environ.get("JCC_CR003_NEGCTL") == "1"

    # The stand-in child script must exist before any launcher is built, and MUST
    # be removed afterwards however this run ends -- a falsifier that leaves a
    # stray file in the repository it is auditing is its own defect.
    CHILD_SCRIPT.write_text(CHILD_BODY, encoding="utf-8")
    try:
        check_is_inside_is_actually_called()
        check_helper_implementation_was_not_rewritten()
        check_traversal_and_crossdrive_roots_are_refused()
        check_out_of_scope_message_is_actionable()
        check_scope_is_checked_before_existence_not_after()
        check_paths_inside_the_base_are_still_launched()
        check_real_child_still_starts_in_scope()
        check_vanished_root_in_scope_is_still_PROJECT_ROOT_MISSING()
        check_preview_does_not_lie_about_scope()
        check_scope_base_default_is_derived_not_typed()
        check_scope_base_relocates_with_the_checkout()
        check_config_item_is_read_and_enforced()
        check_misconfigured_base_fails_closed()
        if not in_negative_control:
            check_real_registry_is_refused_too()
            check_no_regression_in_existing_suites()

        if not is_child:
            run_negative_control()
            check_restored_and_green()
    finally:
        try:
            CHILD_SCRIPT.unlink()
        except OSError:
            pass

    passed = sum(1 for r in RESULTS if r["ok"])
    for r in RESULTS:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    print("\nCR-003 FALSIFIER %s %d/%d"
          % ("PASS" if passed == len(RESULTS) else "FAIL", passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
