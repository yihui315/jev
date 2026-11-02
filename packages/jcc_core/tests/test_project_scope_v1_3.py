"""RISK-1 -- project_root validation on the WRITE side.

THE DEFECT THESE TESTS CLOSE
----------------------------
``POST /api/v1/domain/projects`` stored whatever ``root_path`` arrived. Measured
against the product before this change, all four of these came back **200** and
were persisted verbatim::

    ../../../../Windows/System32   -> 200, stored as typed
    ..\\..\\..\\..\\Windows\\System32 -> 200, stored as typed
    C:\\Windows\\System32-> 200, stored as typed
    /etc/passwd                    -> 200, stored as typed

``HostLauncher.launch`` refused those same paths since CR-003, so the boundary
existed on the way OUT and not on the way IN. That is not a boundary: the row is
still on disk, still rendered, still exported.

HOW THESE TESTS ARE ORDERED, AND WHY
------------------------------------
Every refusal asserts the SPECIFIC reason code, never merely "it raised". A
guard that reported ``PROJECT_ROOT_MISSING`` for a traversal probe would pass a
naive "did it reject?" assertion while having relabelled an attack as a typo --
the exact conflation the master forbade. So:

* cases 1-4, 7, 8 assert ``PROJECT_ROOT_OUT_OF_SCOPE`` (never MISSING);
* case 5 asserts ``PROJECT_ROOT_MISSING`` and cases 1-4 assert it is NOT that;
* case 6 asserts ``PROJECT_ROOT_NOT_A_DIRECTORY``, distinct from MISSING;
* cases 9-12 are POSITIVE controls, without which a guard that refused
  everything would score full marks here while being completely broken.

THE ORDERING IS PINNED BY CASE 5
--------------------------------
Cases 1, 2 and 4 all resolve to paths that DO NOT EXIST on this host
(``D:\\Windows\\System32``, ``D:\\etc\\passwd``). An existence-first guard calls
them MISSING; a scope-first guard calls them OUT_OF_SCOPE. That is why the
expected code for a *nonexistent escaping* path is asserted to be
OUT_OF_SCOPE -- it is the observable proof of which check runs first.

THE JUNCTION CASE IS A REAL TEST ON THIS HOST
---------------------------------------------
Case 8 constructs a genuine Windows junction (``New-Item -ItemType Junction``)
whose own NAME sits inside the workspace while its TARGET sits outside, then
asserts the resolved target is refused. It is NOT skipped here because it was
measured to work on this host. It still carries a ``pytest.skip`` guard for the
host that cannot create one, because a skip is an honest statement about a
missing host prerequisite while an ``if`` that quietly passes is a lie.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from jcc_core.config import REPO_ROOT, LaunchScopeConfig  # noqa: E402
from jcc_core.project_scope import (  # noqa: E402
    PROJECT_ROOT_REASON_CODES,
    REASON_PROJECT_ROOT_MISSING,
    REASON_PROJECT_ROOT_NOT_A_DIRECTORY,
    REASON_PROJECT_ROOT_NOT_ABSOLUTE,
    REASON_PROJECT_ROOT_OUT_OF_SCOPE,
    ProjectRootRejected,
    allowed_project_roots,
    scope_report,
    validate_project_root,
)

#: The boundary every default case is measured against. DERIVED, never typed:
#: this repository has been broken by a hardcoded drive letter four times.
ROOTS = [str(REPO_ROOT)]


def _reject(raw, roots=None):
    """Validate and return the reason code, or ``None`` if it was accepted."""
    try:
        validate_project_root(raw, roots=ROOTS if roots is None else roots)
        return None
    except ProjectRootRejected as exc:
        return exc.code


# --------------------------------------------------------------------------- #
# 1-4, 7. traversal / absolute / POSIX / UNC  -> OUT OF SCOPE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    "../../../../Windows/System32",
    "..\\..\\..\\..\\Windows\\System32",
    "C:\\Windows\\System32",
    "/etc/passwd",
    "\\\\server\\share",
    "\\\\?\\UNC\\server\\share\\workspace",
])
def test_escaping_roots_are_refused_as_OUT_OF_SCOPE(raw):
    """Cases 1,2,3,4,7 -- every escaping spelling, and never as MISSING.

    The reason code is the assertion, not a side effect. ``OUT_OF_SCOPE`` says
    "this resolves outside the boundary, which may be an attack"; ``MISSING``
    says "this is not there, go create it", and an operator who received that
    about a traversal would go and create the very directory the guard just
    declined to expose.
    """
    assert _reject(raw) == REASON_PROJECT_ROOT_OUT_OF_SCOPE, (
        "%r must be refused as OUT_OF_SCOPE; MISSING here would report an "
        "attack as a typo" % raw)


def test_case1_posix_traversal_specifically_is_NOT_reported_as_missing():
    """Case 1 pinned individually: the measured nonexistent-escape ordering.

    On this host ``../../../../Windows/System32`` resolves to
    ``D:\\Windows\\System32``, which does not exist. So the code it receives is
    the ONLY way to tell which check ran first, and this is the assertion that
    tells.
    """
    assert not os.path.exists(os.path.realpath("../../../../Windows/System32")), (
        "precondition: this case is only meaningful while the escape resolves to "
        "a nonexistent path -- if it now exists, the ordering is no longer "
        "observable here and the case needs rethinking")
    assert _reject("../../../../Windows/System32") == REASON_PROJECT_ROOT_OUT_OF_SCOPE
    assert _reject("../../../../Windows/System32") != REASON_PROJECT_ROOT_MISSING


# --------------------------------------------------------------------------- #
# 5. a nonexistent directory INSIDE the boundary -> MISSING
# --------------------------------------------------------------------------- #
def test_nonexistent_directory_inside_the_boundary_is_MISSING():
    """Case 5 -- and the counterpart that keeps cases 1-4 honest.

    ``MISSING`` must still be reachable, or it has been merged into
    ``OUT_OF_SCOPE`` and the operator has lost the one answer that tells them
    what to do next.
    """
    gone = str(REPO_ROOT / "packages" / "risk1-definitely-not-here")
    assert _reject(gone) == REASON_PROJECT_ROOT_MISSING
    # ...and the code is genuinely different, not the same refusal relabelled.
    assert REASON_PROJECT_ROOT_MISSING != REASON_PROJECT_ROOT_OUT_OF_SCOPE


# --------------------------------------------------------------------------- #
# 6. a FILE where a directory is required -> NOT_A_DIRECTORY
# --------------------------------------------------------------------------- #
def test_a_file_is_not_a_directory_and_says_so():
    """Case 6 -- a third code, because the three answers mean three things.

    MISSING: "create it". OUT_OF_SCOPE: "you may not have it".
    NOT_A_DIRECTORY: "it is there, under that exact name, and it is a file."
    Merging the third into the first sends the operator hunting for a directory
    that is sitting right there.
    """
    a_file = str(REPO_ROOT / "AGENTS.md")
    assert os.path.isfile(a_file), "precondition: AGENTS.md must be a real file"
    assert _reject(a_file) == REASON_PROJECT_ROOT_NOT_A_DIRECTORY
    assert _reject(a_file) != REASON_PROJECT_ROOT_MISSING


# --------------------------------------------------------------------------- #
# 8. junction / reparse escape
# --------------------------------------------------------------------------- #
def _is_inside_workspace(path) -> bool:
    """True when ``path`` resolves inside the workspace boundary."""
    resolved = os.path.normcase(os.path.realpath(str(path)))
    return resolved == os.path.normcase(str(REPO_ROOT)) or resolved.startswith(
        os.path.normcase(str(REPO_ROOT)) + os.sep)


def _make_outside_dir() -> Path:
    """A real directory that is genuinely OUTSIDE the workspace boundary.

    Searches candidate roots and returns the first that is verifiably outside,
    because on this host both obvious answers are inside the boundary:
    ``tempfile.gettempdir()`` and ``tmp_path`` both resolve under
    ``<repo>/.workbuddy/scratch`` (conftest redirects both). The env vars
    ``TEMP``/``TMP`` still name the real system temp, so they come next.

    Every candidate is CHECKED with the same containment rule the guard uses,
    rather than trusted by name. A test that assumed its "outside" directory was
    outside would fail on a false precondition, and the failure would read as a
    guard defect -- the most expensive way to waste a reviewer's time.
    """
    candidates = []
    for var in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(var)
        if value:
            candidates.append(Path(value))
    candidates.append(Path(tempfile.gettempdir()))
    candidates.append(Path(os.environ.get("SystemRoot") or "C:\\Windows"))
    for root in candidates:
        try:
            if not root.is_dir() or _is_inside_workspace(root):
                continue
            target = root / ("risk1_junction_target_%d" % os.getpid())
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if target.is_dir() and not _is_inside_workspace(target):
            return target
    pytest.skip(
        "no writable directory outside the workspace could be found on this "
        "host, so the reparse-point ESCAPE cannot be constructed. Candidates "
        "tried: %r. This is a HOST prerequisite, not a pass -- the case is "
        "unmeasured on this machine." % [str(c) for c in candidates])


def _junction_present(link: Path) -> bool:
    """True when ``link`` exists AS A JUNCTION -- including a broken one.

    This is the check that matters and it is not ``Path.exists()``. Measured on
    this host: a junction whose target has been deleted is reported by
    ``Path.exists()`` as **False** and by ``os.path.islink()`` as **False**,
    while PowerShell still lists it with ``Attributes = Directory,
    ReparsePoint``. A stale stub left by an earlier run therefore looks absent
    to the obvious check, survives cleanup, and then makes ``New-Item -ItemType
    Junction`` fail on the next run with a *misleading* ``DirectoryNotFound``
    naming a path whose parent demonstrably exists.

    That is precisely how case 8 came to SKIP on a host that creates junctions
    perfectly well -- and a skip is the one outcome that would have hidden it.
    The name is therefore probed with the shell, which is the only thing on this
    host that sees a broken reparse point, and the test asserts the junction it
    built rather than the one it found.
    """
    if link.exists():
        return True
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         'if (Test-Path -LiteralPath "%s") { "present" } else { "absent" }'
         % link],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60)
    return "present" in (proc.stdout or "")


def _force_remove_junction(link: Path):
    """Remove a junction without following it, INCLUDING a broken one.

    ``shutil.rmtree`` on a junction can delete *through* it, and ``Path.unlink``
    raises ``PermissionError`` on Windows for a directory-shaped link. So the
    removal is delegated to ``cmd /c rmdir``, which unlinks the junction ITSELF
    and leaves the target untouched.

    The existence gate uses :func:`_junction_present`, not ``Path.exists()``, for
    the reason documented there: a broken junction is invisible to
    ``Path.exists()`` and would be skipped here, then poison the next run. Best
    effort by design -- a link that was never created needs no removal, and a
    failure here must not mask the assertion the caller is about to make.
    """
    if not _junction_present(link):
        return
    subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True,
                   text=True, encoding="utf-8", errors="replace", timeout=60)


def _make_junction(link: Path, target: Path):
    """Create a Windows junction, or return None if this host cannot.

    ``os.symlink`` needs a privilege this host may not grant; a junction needs
    none, which is why it is tried first. Returns None rather than raising so
    the CALLER decides between skip and fail -- the decision belongs to the test,
    not to the helper.

    ``errors="replace"`` is not optional: PowerShell emits the console's ANSI
    codepage (cp936/GBK on this host), and letting ``text=True`` decode it as
    UTF-8 raises ``UnicodeDecodeError`` inside subprocess's reader THREAD. That
    exception does not fail the call -- it just leaves the output unreadable --
    so without this the helper silently reports "cannot create a junction" on a
    host that created one perfectly well, and the test skips for a false reason.

    Success is confirmed with :func:`_junction_present`, not ``link.exists()``:
    a junction is a reparse point, and the naive check reports a freshly created
    one as absent in some conditions -- which would turn a real escape into a
    skip.
    """
    for args in (["New-Item", "-ItemType", "Junction", "-Path", str(link),
                  "-Target", str(target)],
                 ["cmd", "/c", "mklink", "/J", str(link), str(target)]):
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 " ".join(args)], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and _junction_present(link):
            return True
    return False


def test_a_junction_that_points_outside_is_refused_after_resolution():
    """Case 8 -- the case that separates realpath() from a string prefix test.

    The junction's OWN NAME is inside the workspace. Only resolving it reveals
    that its target is not. A guard comparing strings passes this happily; a
    guard that resolves first refuses it -- and ``os.path.realpath`` is what
    does the resolving, so this test is also the evidence that spec 2 is real.

    THE TARGET MUST REALLY BE OUTSIDE, AND IS THEREFORE CHOSEN BY SEARCHING FOR
    IT. Two obvious candidates are both wrong on this host, which is why the
    test derives one instead of assuming it:

    * ``tmp_path`` lives under ``<repo>/.workbuddy/scratch`` because
      ``conftest.py`` redirects basetemp into the project tree so the host's
      safe-delete guard cannot distort the run -- so it is INSIDE the boundary
      and a junction pointing there escapes nothing;
    * ``tempfile.gettempdir()`` returns that same in-repo path, because conftest
      also sets ``TMPDIR``. The env vars ``TEMP``/``TMP`` still name the real
      system temp, so the search falls through to them.

    So the candidate list is walked and the first directory that is genuinely
    outside the boundary wins. A junction pointing inside would assert a false
    precondition and fail for a reason unrelated to the guard under test.
    """
    outside = _make_outside_dir()
    probe_dir = REPO_ROOT / "runtime" / "risk1_junction_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    link = probe_dir / "escape"
    # A leftover junction from an earlier run would be measured instead of the
    # one this test builds. Removing it is not optional: the alternative is an
    # `if link.exists(): return`, which is a silent pass -- the exact failure
    # mode a falsifier is supposed to be immune to.
    _force_remove_junction(link)
    made = _make_junction(link, outside)
    if not made:
        pytest.skip(
            "this host cannot create a Windows junction (New-Item -ItemType "
            "Junction and mklink /J both failed), so the reparse-point escape "
            "cannot be constructed here. This is a HOST prerequisite, not a "
            "pass: the case is unmeasured on this machine.")
    try:
        # The junction must be the one THIS run built. Asserted rather than
        # assumed, because a stale stub from a previous run is exactly how this
        # case came to skip on a host that supports junctions perfectly well.
        assert _junction_present(link), (
            "the helper reported success but no junction is present at %s, so "
            "the escape below would be measured against stale state" % link)
        # Precondition that makes this the case it claims to be: the junction's
        # name is lexically inside the boundary while its resolved target is
        # not. Without this the test would pass for the wrong reason (plain
        # out-of-scope, no junction involved).
        lexically_inside = os.path.normcase(os.path.abspath(str(link))).startswith(
            os.path.normcase(str(REPO_ROOT)))
        resolved = os.path.realpath(str(link))
        resolved_outside = not os.path.normcase(resolved).startswith(
            os.path.normcase(str(REPO_ROOT)))
        assert lexically_inside and resolved_outside, (
            "precondition failed: junction name inside=%s resolved=%r outside=%s "
            "-- if the junction did not actually escape, this case is not "
            "measuring an escape" % (lexically_inside, resolved, resolved_outside))

        assert _reject(str(link)) == REASON_PROJECT_ROOT_OUT_OF_SCOPE, (
            "a junction whose name is inside the workspace but whose TARGET is "
            "outside must be refused once resolved; it was accepted as %r"
            % resolved)
    finally:
        _force_remove_junction(link)


def test_an_in_scope_junction_that_points_inside_is_still_allowed():
    """The positive half of case 8 -- resolution must not refuse everything.

    Without this, a validator that simply rejected every junction would satisfy
    case 8 perfectly while breaking a legitimate layout.
    """
    inside_target = REPO_ROOT / "packages"
    probe_dir = REPO_ROOT / "runtime" / "risk1_junction_probe_ok"
    probe_dir.mkdir(parents=True, exist_ok=True)
    link = probe_dir / "inside"
    _force_remove_junction(link)
    made = _make_junction(link, inside_target)
    if not made:
        pytest.skip("this host cannot create a Windows junction (host prerequisite)")
    try:
        accepted = validate_project_root(str(link), roots=ROOTS)
        assert os.path.normcase(accepted) == os.path.normcase(os.path.realpath(
            str(inside_target))), (
            "an in-scope junction must resolve to its in-scope target and be "
            "accepted; got %r" % accepted)
    finally:
        _force_remove_junction(link)


# --------------------------------------------------------------------------- #
# 9, 10, 11. POSITIVE CONTROLS
# --------------------------------------------------------------------------- #
def test_case9_windows_case_normalisation_is_allowed():
    """Case 9 -- Windows does not distinguish case, so ``D:\\JEV`` IS ``D:\\jev``.

    Asserted per measured Windows behaviour. A guard that refused this would be
    refusing the workspace to an operator who merely typed it in capitals.
    """
    shouted = str(REPO_ROOT).swapcase() + "\\packages"
    accepted = validate_project_root(shouted, roots=ROOTS)
    assert os.path.normcase(accepted) == os.path.normcase(str(REPO_ROOT / "packages"))


@pytest.mark.parametrize("suffix", ["\\", "/", "\\packages\\", "/packages/"])
def test_case10_trailing_separators_are_allowed_and_normalised(suffix):
    """Case 10 -- a trailing separator is a spelling, not a different directory."""
    accepted = validate_project_root(str(REPO_ROOT) + suffix, roots=ROOTS)
    assert accepted == str(REPO_ROOT) or accepted.startswith(str(REPO_ROOT))
    assert not accepted.endswith("\\") and not accepted.endswith("/")


def test_case11_a_legal_in_scope_fixture_is_accepted_and_canonicalised():
    """Case 11 -- and the stored value is the RESOLVED one (spec 7).

    The return value is what the caller persists, so asserting it is how this
    suite proves "DB only ever holds a canonical path" rather than merely
    claiming it. A mixed-separator input that resolves to the canonical spelling
    is the case that would catch a store keeping the raw text.
    """
    raw = str(REPO_ROOT) + "/packages/../packages"
    accepted = validate_project_root(raw, roots=ROOTS)
    assert accepted == str(REPO_ROOT / "packages")
    assert os.path.isabs(accepted)
    assert ".." not in accepted


# --------------------------------------------------------------------------- #
# 12. an EXPLICITLY configured extra root is allowed
# --------------------------------------------------------------------------- #
def test_case12_a_configured_extra_root_is_allowed(tmp_path):
    """Case 12 -- the boundary is widenable on purpose, and only on purpose.

    An allow-list of one root is indistinguishable from a guard that refuses
    everything outside a fixed tree. Widening must be possible, or the only way
    to launch against another directory is to edit code.

    THE ALLOW-LIST HERE DOES NOT INCLUDE ``REPO_ROOT``, deliberately. ``tmp_path``
    lives INSIDE this repository (conftest redirects basetemp into the project
    tree so the safe-delete guard cannot distort the run), so an allow-list
    containing the repo root would make every sibling in this test in scope and
    the second half -- the part that proves widening did NOT widen -- would pass
    for the wrong reason. An earlier version of this test made exactly that
    mistake and its negative assertion was vacuous.
    """
    allowed = tmp_path / "allowed_project"
    allowed.mkdir(parents=True, exist_ok=True)
    refused = tmp_path / "not_allowed"
    refused.mkdir(parents=True, exist_ok=True)
    # One root: the allowed project. Nothing else is in scope, by construction.
    roots = [str(allowed)]

    assert validate_project_root(str(allowed), roots=roots) == str(allowed)
    assert _reject(str(refused), roots=roots) == REASON_PROJECT_ROOT_OUT_OF_SCOPE

    # Widening is additive and explicit: adding a second root admits that root
    # and nothing else beyond it.
    roots2 = [str(allowed), str(refused)]
    assert validate_project_root(str(refused), roots=roots2) == str(refused)
    still_outside = _reject(str(REPO_ROOT), roots=roots2)
    assert still_outside == REASON_PROJECT_ROOT_OUT_OF_SCOPE, (
        "adding an extra root must not transitively admit the whole disk; "
        "REPO_ROOT is still refused and got %r" % still_outside)


# --------------------------------------------------------------------------- #
# reason-code discipline
# --------------------------------------------------------------------------- #
def test_the_four_reason_codes_stay_distinct_and_are_all_reachable():
    """No two codes may share a meaning, and none may be decorative.

    Reachability is asserted from the four situations above rather than from the
    constant tuple, so a code that is declared but never returned fails here.
    """
    assert len(set(PROJECT_ROOT_REASON_CODES)) == 4
    reached = {
        _reject("C:\\Windows\\System32"),
        _reject(str(REPO_ROOT / "packages" / "risk1-not-here")),
        _reject(str(REPO_ROOT / "AGENTS.md")),
        _reject("packages"),
    }
    assert reached == set(PROJECT_ROOT_REASON_CODES), (
        "every declared code must be reachable from a real situation; got %r"
        % reached)


def test_a_relative_fragment_resolving_inside_is_NOT_ABSOLUTE():
    """Spec 1 is enforced -- but only where it is the honest answer.

    ``packages`` resolves INSIDE the workspace, so the operator meant something
    in-bounds and under-specified it. Reporting OUT_OF_SCOPE there would be the
    same relabelling error in the other direction.
    """
    assert _reject("packages") == REASON_PROJECT_ROOT_NOT_ABSOLUTE


def test_an_empty_root_is_allowed_through_as_no_root_yet():
    """A project may legitimately have no root until its directory is chosen.

    Refusing it here would make this module -- which is about CONTAINMENT -- the
    owner of a COMPLETENESS question, and would break every project created
    before its root is decided. The launch path still answers MISSING for it.
    """
    assert validate_project_root("", roots=ROOTS) == ""
    assert validate_project_root(None, roots=ROOTS) == ""
    assert validate_project_root("   ", roots=ROOTS) == ""


# --------------------------------------------------------------------------- #
# fail-closed
# --------------------------------------------------------------------------- #
def test_an_empty_allow_list_refuses_everything():
    """Fail-closed. An empty roots list must NOT mean "admit everything".

    This is the single most important line in the module: if an operator's
    config lost its boundary, the safe outcome is a stopped product, not an
    unrestricted one.
    """
    assert _reject(str(REPO_ROOT), roots=[]) == REASON_PROJECT_ROOT_OUT_OF_SCOPE


def test_a_root_that_is_itself_the_boundary_is_allowed():
    """The boundary directory itself is inside the boundary.

    ``_is_inside`` treats parent==child as inside. If it did not, an operator
    whose project IS the repo root would be locked out of their own workspace.
    """
    assert validate_project_root(str(REPO_ROOT), roots=ROOTS) == str(REPO_ROOT)


# --------------------------------------------------------------------------- #
# configuration surface
# --------------------------------------------------------------------------- #
def test_allowed_roots_default_to_the_derived_scope_base_not_a_typed_drive():
    """The default must DERIVE. A drive literal here is the fifth such incident."""
    import ast

    src = (REPO_ROOT / "packages" / "jcc_core" / "config.py").read_text(
        encoding="utf-8")
    drive_literals = [n.value[:40] for n in ast.walk(ast.parse(src))
                      if isinstance(n, ast.Constant) and isinstance(n.value, str)
                      and len(n.value) > 2 and n.value[1] == ":"
                      and n.value[0].isalpha() and n.value[2] in "\\/"]
    assert not drive_literals, (
        "config.py carries drive-letter literals: %r" % drive_literals)
    assert allowed_project_roots() == [str(REPO_ROOT)]


def test_the_env_var_overrides_and_the_config_value_is_secondary():
    """Env beats file beats derived default -- and all three are testable."""
    env = {"JCC_ALLOWED_PROJECT_ROOTS": os.pathsep.join(["/a", "/b"])}
    assert allowed_project_roots(env=env) == ["/a", "/b"]
    assert allowed_project_roots(env={}, config_value=["/c"]) == ["/c"]
    assert allowed_project_roots(env={}) == [str(REPO_ROOT)]


def test_effective_allowed_roots_never_returns_an_empty_list():
    """A Config built with no launcher block still has a boundary.

    Library callers and tests construct ``Config()`` directly; without this they
    would silently get "no boundary", which is the fail-open direction.
    """
    cfg = LaunchScopeConfig()
    roots = cfg.effective_allowed_roots()
    assert roots and all(str(r).strip() for r in roots)
    narrow = LaunchScopeConfig(project_scope_base=str(REPO_ROOT / "packages"))
    assert narrow.effective_allowed_roots() == [str(REPO_ROOT / "packages")]


def test_an_explicitly_blank_base_falls_back_to_the_derived_root():
    """REGRESSION: a blank base used to yield ``[""]`` and refuse EVERYTHING.

    Found by the falsifier's fail-closed case, not by this suite -- which is
    what a falsifier is for. The shipped YAML declares ``project_scope_base:
    ""`` meaning "take the default", and constructing ``LaunchScopeConfig`` with
    that empty string produced a list holding the EMPTY STRING. That is neither
    the derived root nor an empty list, and it is the worst of the three:
    ``_is_inside(anything, "")`` resolves the empty string against the process
    working directory, so on this host every project root -- including the
    repository's own -- was refused with OUT_OF_SCOPE.

    The failure direction was safe and the behaviour was still wrong. A boundary
    that refuses everything is indistinguishable from a misconfigured one, and
    the operator's actual fix (configure a base) is not what the message would
    have told them to do.
    """
    blank = LaunchScopeConfig(project_scope_base="", allowed_project_roots=[])
    roots = blank.effective_allowed_roots()
    assert roots == [str(REPO_ROOT)], (
        "a blank base must fall back to the DERIVED repo root, got %r" % roots)
    assert "" not in roots, "the empty string is not a root"
    # And the practical consequence: the repository root must be usable with a
    # blank base, which is exactly the configuration the shipped YAML declares.
    assert validate_project_root(str(REPO_ROOT / "packages"), roots=roots) == str(
        REPO_ROOT / "packages")


def test_scope_report_exposes_the_boundary_for_the_ui():
    """Spec 10 -- the UI cannot render a boundary it is not told.

    Asserted as data: the allow-list, the codes and the fail-closed posture all
    have to be reachable by a client that only reads the response.
    """
    report = scope_report(roots=ROOTS)
    assert report["allowed_project_roots"] == ROOTS
    assert set(report["reason_codes"]) == set(PROJECT_ROOT_REASON_CODES)
    assert report["fail_closed"] is True
    assert report["stored_value"] == "canonical"


# --------------------------------------------------------------------------- #
# the reused predicate was not quietly rewritten
# --------------------------------------------------------------------------- #
def test_the_launch_side_predicate_is_reused_not_reimplemented():
    """This module CALLS ``_is_inside``; it must not own a second copy.

    A guard that also edits its own predicate cannot be reviewed independently:
    a weakened copy would make every case above pass for the wrong reason.
    Counted on the AST, so a docstring mentioning the name cannot satisfy it.
    """
    import ast

    src = (REPO_ROOT / "packages" / "jcc_core" / "project_scope.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_is_inside"]
    helper_defs = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_is_inside"]
    assert len(calls) >= 2, (
        "expected real calls into the launch side's _is_inside, found %d"
        % len(calls))
    assert not helper_defs, (
        "project_scope.py must not DEFINE _is_inside; the launch side owns it")


def test_the_launch_side_predicate_still_has_its_measured_properties():
    """The reused predicate must still behave as it was independently verified.

    If a rewrite broke one of these, the cases above would be measuring the
    rewrite rather than the wiring.
    """
    from jcc_core.host_launcher import _is_inside

    assert _is_inside("D:/jev/packages/../packages", "D:/jev") is True
    assert _is_inside(str(REPO_ROOT).upper(), str(REPO_ROOT)) is True
    assert _is_inside("Z:/somewhere/else", str(REPO_ROOT)) is False
    assert _is_inside(str(REPO_ROOT), str(REPO_ROOT)) is True
    assert _is_inside("D:/jev/../etc", str(REPO_ROOT)) is False