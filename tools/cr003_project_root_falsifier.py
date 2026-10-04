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


def record(case_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": case_id, "ok": bool(ok), "detail": detail})


def fresh_env(**overrides: str) -> dict:
    """A child env with the signing key and PYTHONPATH pinned.

    Isolation matters: the ambient environment of whoever runs this suite must
    not decide whether a case passes.
    """
    env = dict(os.environ)
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
    """A real HostLauncher with a real signer, and NO real template resolution.

    ``templates={}`` means ``build_head`` is never reached for the scope cases;
    the scope guard sits AFTER template resolution in ``launch()`` though, so the
    scope cases below use the real registry and accept whatever
    ``resolve`` reports. The reason code asserted is the one the SCOPE step
    produced, and ``steps`` proves which step refused.
    """
    from jcc_core import host_launcher as HL
    from jcc_core.signing import SIGNING_FORM_V2, DirectiveSigner

    signer = DirectiveSigner(b"cr003-falsifier-key-not-a-secret",
                             form=SIGNING_FORM_V2)
    return HL.HostLauncher(signer=signer, scope_base=scope_base)


def make_intent(project_root: str, mission_id: str = "m-cr003"):
    from jcc_core import host_launcher as HL

    return HL.LaunchIntent(
        mission_id=mission_id, project_id="p-cr003", agent_id="workbuddy",
        adapter_id="workbuddy", project_root=project_root,
        template_id="workbuddy-cli-headless-v1",
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
    suite's own script as the child, inside the base, and requires a real pid.
    A guard stuck closed would leave ``pid`` at None and fail here even though it
    passed the softer case.
    """
    from jcc_core import host_launcher as HL
    from jcc_core.signing import SIGNING_FORM_V2, DirectiveSigner

    base = str(REPO)
    signer = DirectiveSigner(b"cr003-falsifier-key-not-a-secret",
                             form=SIGNING_FORM_V2)
    work = Path(base)                      # must be INSIDE the base
    script = work / "_cr003_child.py"
    script.write_text("print('CR003_CHILD_RAN')\n", encoding="utf-8")
    tpl = HL.LaunchTemplate(
        template_id="cr003-self-v1", agent="workbuddy", launch_kind="direct",
        executable=PY, argv_prefix=("-I", str(script)),
        param_flags=(("--add-dir", "project_root"),), terminates=True)
    launcher = HL.HostLauncher(signer=signer, templates={"cr003-self-v1": tpl},
                               scope_base=base)
    it = make_intent(str(work), mission_id="m-real")
    it = HL.LaunchIntent(
        mission_id=it.mission_id, project_id=it.project_id, agent_id="workbuddy",
        adapter_id="workbuddy", project_root=str(work),
        template_id="cr003-self-v1", goal="unused",
        nonce=it.nonce, user_approved=True, policy_version="pv-1")
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
        try:
            script.unlink()
        except OSError:
            pass


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
    launcher = HL.HostLauncher(
        signer=DirectiveSigner(b"cr003-falsifier-key-not-a-secret",
                               form=SIGNING_FORM_V2),
        scope_base=base)
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
    copying ``jcc_core`` to a temp dir and importing it there. If the default were
    typed, the imported module would still report the original drive path while
    running from somewhere else entirely.
    """
    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr003_reloc_"))
    try:
        from jcc_core.config import REPO_ROOT
        dst = tmp / "jcc_core"
        shutil.copytree(str(REPO / "packages" / "jcc_core"), str(dst),
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        probe = tmp / "probe.py"
        probe.write_text(
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from jcc_core.config import DEFAULT_PROJECT_SCOPE_BASE, REPO_ROOT\n"
            "print(json.dumps({'base': DEFAULT_PROJECT_SCOPE_BASE,\n"
            "                  'repo': str(REPO_ROOT)}))\n",
            encoding="utf-8")
        proc = subprocess.run([PY, str(probe), str(tmp)], env=fresh_env(),
                              text=True, errors="replace",
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=180)
        line = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("{")]
        import json as _json
        data = _json.loads(line[-1]) if line else {}
        base = data.get("base", "")
        record(
            "SCOPE_BASE_FOLLOWS_A_MOVED_CHECKOUT",
            proc.returncode == 0 and base and Path(base) == Path(str(tmp)).resolve()
            and base != str(REPO_ROOT),
            "imported jcc_core from %s -> DEFAULT_PROJECT_SCOPE_BASE=%r "
            "(original checkout is %r; equal would mean the path is pinned to "
            "the old location)" % (tmp, base, str(REPO_ROOT)),
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
            "launcher:\n  project_scope_base: %s\n" % (REPO / "packages").as_posix(),
            encoding="utf-8")
        cfg = load_config(str(narrow))
        declared = cfg.launcher.project_scope_base

        from jcc_core import host_launcher as HL
        from jcc_core.signing import SIGNING_FORM_V2, DirectiveSigner
        launcher = HL.HostLauncher(
            signer=DirectiveSigner(b"cr003-falsifier-key-not-a-secret",
                                   form=SIGNING_FORM_V2),
            scope_base=declared)
        # packages/ is now the boundary, so packages/ is IN and its parent is OUT.
        it_in = make_intent(str(REPO / "packages"), mission_id="m-cfg-in")
        out_in = launcher.launch(it_in, make_directive(it_in, launcher))
        it_out = make_intent(str(REPO / "config"), mission_id="m-cfg-out")
        out_out = launcher.launch(it_out, make_directive(it_out, launcher))

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
    from jcc_core import host_launcher as HL
    from jcc_core.signing import SIGNING_FORM_V2, DirectiveSigner

    launcher = HL.HostLauncher(
        signer=DirectiveSigner(b"cr003-falsifier-key-not-a-secret",
                               form=SIGNING_FORM_V2),
        scope_base=str(REPO / "cr003-no-such-base"))
    it = make_intent(str(REPO / "packages"), mission_id="m-badbase")
    out = launcher.launch(it, make_directive(it, launcher))
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
    original = target.read_text(encoding="utf-8")
    backup = target.with_suffix(".py.cr003-backup")
    sabotaged = original.replace(
        "    if _is_inside(str(project_root), base):\n        return \"\"\n",
        "    if True:  %s\n        return \"\"\n" % NEGATIVE_CONTROL_MARKER,
        1)
    if sabotaged == original:
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED",
            False,
            "could not locate the containment branch in %s -- the falsifier must "
            "not report a negative control it did not perform" % target.name,
        )
        return
    try:
        backup.write_text(original, encoding="utf-8")
        target.write_text(sabotaged, encoding="utf-8")
        proc = subprocess.run([PY, str(REPO / "tools" /
                                       "cr003_project_root_falsifier.py")],
                              env=fresh_env(CODEBUDDY_SAFE_DELETE_ENABLED="0",
                                            JCC_CR003_CHILD="1"),
                              cwd=str(REPO), text=True, errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=900)
        out = proc.stdout or ""
        red = proc.returncode != 0
        summary = [ln for ln in out.splitlines() if "CR-003 FALSIFIER" in ln]
        holes = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[HOLE]")]
        record(
            "NEGATIVE_CONTROL_WOULD_GO_RED_WHEN_THE_GUARD_IS_REMOVED",
            red and bool(holes),
            "with the containment branch short-circuited to 'if True', the suite "
            "exited rc=%s and reported %d HOLE(s): %s | %s"
            % (proc.returncode, len(holes),
               "; ".join(h.split("] ", 1)[-1][:60] for h in holes[:4]),
               summary[-1] if summary else "no summary line"),
        )
    finally:
        # Restore from the in-memory original, then verify the tree is byte-exact.
        target.write_text(original, encoding="utf-8")
        try:
            backup.unlink()
        except OSError:
            pass
        restored = target.read_bytes()
        expect = original.encode("utf-8")
        if restored != expect:  # pragma: no cover - would be a real incident
            record("SOURCE_RESTORED_AFTER_NEGATIVE_CONTROL", False,
                   "host_launcher.py is NOT byte-identical to its pre-sabotage "
                   "content (%d vs %d bytes) -- treat the working tree as suspect"
                   % (len(restored), len(expect)))


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
    check_no_regression_in_existing_suites()

    if not is_child:
        run_negative_control()
        check_restored_and_green()

    passed = sum(1 for r in RESULTS if r["ok"])
    for r in RESULTS:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    print("\nCR-003 FALSIFIER %s %d/%d"
          % ("PASS" if passed == len(RESULTS) else "FAIL", passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
