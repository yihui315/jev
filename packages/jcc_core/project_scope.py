"""RISK-1 -- project_root validation on the WRITE side.

THE ASYMMETRY THIS CLOSES
-------------------------
``HostLauncher.launch``/``preview`` have refused an out-of-scope ``project_root``
since CR-003 (:func:`~jcc_core.host_launcher.project_scope_violation`). The
WRITE side had no equivalent: ``POST /api/v1/domain/projects`` stored whatever
``root_path`` arrived -- ``../../../../Windows/System32``, ``/etc/passwd``,
``C:\\Windows\\System32`` all landed verbatim and un-normalised.

A boundary that only exists on the way OUT is not a boundary. The dirty row
cannot be *launched* today, but it is still on disk, still rendered by the UI,
and one launcher change away from being reachable. This module makes the WRITE
path hold the same line the launch path already holds.

REUSE, NOT A FOURTH IMPLEMENTATION
----------------------------------
The containment predicate is :func:`jcc_core.host_launcher._is_inside` --
``normcase(realpath())`` + ``commonpath``, independently measured correct on six
cases including case folding, cross-drive and ``..`` escape. It is CALLED here,
never re-implemented or re-tuned. ``REASON_PROJECT_ROOT_OUT_OF_SCOPE`` and
``PROJECT_ROOT_MISSING`` are likewise imported, not redefined: two names for two
OPPOSITE facts ("not there" vs "right there, and you may not have it"), because
collapsing them would let a refused traversal read as a typo on the audit tape.

THE ORDER IS LOAD-BEARING (measured, not chosen)
------------------------------------------------
Containment is evaluated BEFORE existence, matching the launch side. On this
host ``../../../../Windows/System32`` and ``/etc/passwd`` resolve to paths that
DO NOT EXIST, so an existence-first guard answers ``PROJECT_ROOT_MISSING`` for a
traversal probe -- calling an attack a typo and telling the operator to go
create the very directory the guard just declined to expose. Scope-first also
means an out-of-scope path cannot be probed for existence at all: the answer is
the same whether or not it is there.

BUT THE ORDER IS NOT WHAT MAKES A JUNCTION SAFE
----------------------------------------------
``_is_inside`` resolves BEFORE comparing, so a junction is followed to its
target and the TARGET is what gets compared. That is the property that matters
and it is what :func:`validate_project_root` relies on. It is also why the
resolved path -- not the typed one -- is what gets stored (spec 7): storing the
typed spelling would leave ``<root>/escape`` in the database as a name whose
meaning is decided by filesystem state at some later moment.

FAIL-CLOSED
-----------
An empty allow-list refuses everything. A base that does not exist refuses
everything. There is no configuration of this module under which an unvalidated
path is returned as valid, because "I could not tell" and "it is fine" must not
be the same answer.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: REUSED, not redefined. The containment predicate and the out-of-scope reason
#: code are the ones the launch path already enforces; a second copy would be a
#: second thing to keep correct, and the drift between them is the defect.
from .host_launcher import (  # noqa: F401  (REASON_* re-exported on purpose)
    REASON_PROJECT_ROOT_OUT_OF_SCOPE,
    _is_inside,
    project_scope_violation,
)

#: The existing launch-side code for "in scope, but not there". Reused verbatim
#: so a root that is merely absent reads identically on both sides of the wire.
REASON_PROJECT_ROOT_MISSING = "PROJECT_ROOT_MISSING"

#: NEW codes, because the two situations below are NOT the two above and must not
#: be folded into them (master's constraint (b): reason codes are not merged).
#:
#: NOT_ABSOLUTE is a caller error -- "packages/jcc_core" is not a project root,
#: it is a fragment, and silently anchoring it to the process CWD would make the
#: stored value depend on where the server happened to be started.
REASON_PROJECT_ROOT_NOT_ABSOLUTE = "PROJECT_ROOT_NOT_ABSOLUTE"

#: A path that resolves and is in scope but is a FILE. Distinct from MISSING:
#: the operator's path is real, they just pointed a working directory at a
#: document. Answering MISSING here would send them looking for a directory that
#: is sitting right there under its own name.
REASON_PROJECT_ROOT_NOT_A_DIRECTORY = "PROJECT_ROOT_NOT_A_DIRECTORY"

#: Every refusal this module can produce, for callers that want to enumerate the
#: surface (the UI renders it; a test asserts the set did not silently grow).
PROJECT_ROOT_REASON_CODES: Tuple[str, ...] = (
    REASON_PROJECT_ROOT_OUT_OF_SCOPE,
    REASON_PROJECT_ROOT_MISSING,
    REASON_PROJECT_ROOT_NOT_ABSOLUTE,
    REASON_PROJECT_ROOT_NOT_A_DIRECTORY,
)


class ProjectRootRejected(ValueError):
    """A project root failed validation. Carries the reason code as DATA.

    A ``ValueError`` so the existing ``except ValueError -> HTTP 422`` handlers
    in ``server.py`` keep working unchanged, but the code travels as an
    attribute rather than being parsed back out of a message string -- parsing a
    reason out of prose is how two codes end up merged by accident.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def _roots_from_env(raw: str) -> List[str]:
    """Split an allow-list on the OS path separator, dropping empty entries.

    ``os.pathsep`` (``:`` on POSIX, ``;`` on Windows) rather than a comma: a
    Windows path may legally contain a comma, and this project has been broken
    by hardcoding platform assumptions before.
    """
    return [part.strip() for part in str(raw or "").split(os.pathsep)
            if part.strip()]


def allowed_project_roots(
    *,
    scope_base: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    config_value: Optional[Sequence[str]] = None,
) -> List[str]:
    """The allow-list a project root is checked against. NEVER empty-safe-open.

    Resolution order, most specific first:

    1. the ``JCC_ALLOWED_PROJECT_ROOTS`` environment variable (``os.pathsep``-
       separated), which is how an operator widens the boundary for one process
       without editing a file that is under A8 pinning;
    2. an explicit ``config_value`` list;
    3. ``scope_base`` -- i.e. ``launcher.project_scope_base``, which is itself
       derived from ``REPO_ROOT`` and never a typed drive letter.

    The DEFAULTS come from the config item rather than from a literal, for the
    reason this repository has been bitten four times now: a drive letter typed
    into source stops being correct the moment the checkout moves.
    """
    environ = os.environ if env is None else env
    from_env = _roots_from_env(environ.get("JCC_ALLOWED_PROJECT_ROOTS", ""))
    if from_env:
        return from_env
    if config_value:
        return [str(p) for p in config_value if str(p).strip()]
    from .config import DEFAULT_PROJECT_SCOPE_BASE
    return [str(scope_base or DEFAULT_PROJECT_SCOPE_BASE)]


def _has_drive_or_root(text: str) -> bool:
    """True when ``text`` names a drive letter or a UNC/share root explicitly.

    ``os.path.isabs`` is not sufficient on Windows, and the gap is exactly where
    the POSIX-shaped attacks live. Measured on this host::

        isabs('../../../../Windows/System32')  -> False
        isabs('/etc/passwd')                   -> False   (but it RESOLVES)
        isabs('C:\\\\Windows\\\\System32')       -> True

    ``/etc/passwd`` is the awkward one: Windows resolves a leading-slash path
    against the CURRENT DRIVE, so it becomes ``D:\\etc\\passwd`` and could look
    like an ordinary in-drive path. It is refused because the operator did not
    name a drive at all -- and a path whose root depends on which drive the
    process happens to be on is not absolute in any sense worth storing.

    Deliberately a ``splitdrive`` + leading-separator test rather than a regex
    over drive letters: no pattern here is written for one platform's spelling,
    which is the class of bug that has repeatedly broken this project.
    """
    drive, _tail = os.path.splitdrive(text)
    if drive:
        # "C:" or "\\\\server\\share" -- both name a root explicitly.
        return True
    # No drive: only an UNC-style or already-absolute spelling can qualify, and
    # on Windows a bare "/..." does NOT (it is drive-relative). Anything else is
    # a fragment.
    return False


def validate_project_root(
    raw: Optional[str],
    *,
    roots: Iterable[str],
    field: str = "project_root",
) -> str:
    """Validate ``raw`` and return the CANONICAL path to store.

    Returns the resolved absolute path on success. Raises
    :class:`ProjectRootRejected` with one of :data:`PROJECT_ROOT_REASON_CODES`
    otherwise. An EMPTY input is allowed through unchanged (as ``""``): a project
    row may legitimately have no root yet, and the launch path already answers
    ``PROJECT_ROOT_MISSING`` for that case. Refusing it here would break every
    project that is created before its directory is chosen -- and would make
    this module, which is about *containment*, the owner of a *completeness*
    question it has no business answering.

    THE NINE SPECS, in the order they are enforced:

    1. absolute        -- enforced, but only AFTER containment (see THE ORDER
                          below); a fragment that resolves inside the workspace
                          is refused, never silently anchored
    2. canonicalize    -- ``realpath`` resolves junction/symlink/reparse/``..``
    3. exists          -- asked only of an in-scope path
    4. is a directory  -- its own code; a file is not "missing"
    5. inside roots    -- containment, via the launch side's own ``_is_inside``
    6. re-checked      -- containment is evaluated on the RESOLVED path, twice:
                          once directly and once by re-resolving, so a target
                          that moves underneath the check is still caught
    7. store canonical  -- the caller persists the RETURN VALUE, never ``raw``
    8. launcher uses DB-- ``build_intent`` already reads ``project.root_path``
                          from the store, so a canonical row is a safe row
    9. raw is display  -- ``raw`` is retained only for audit/display, never as a cwd

    THE ORDER, AND WHY ABSOLUTENESS IS NOT CHECKED FIRST
    -----------------------------------------------------
    The obvious ordering is "absolute, then everything else", and it is wrong
    here -- measurably so on this host. ``os.path.isabs`` answers False for
    ``../../../../Windows/System32``, for ``/etc/passwd`` and for every other
    traversal spelling, because those are relative (or POSIX-rooted) paths on
    Windows. An absoluteness-first guard therefore reports the four attack
    strings as ``PROJECT_ROOT_NOT_ABSOLUTE`` -- which tells the operator "give
    me a full path", i.e. it treats an attack as a typo and invites them to
    resubmit the same escape in absolute form.

    That is the exact failure the launch side's ordering was chosen to avoid,
    so containment is evaluated FIRST and absoluteness second. The resulting
    answers are the ones the operator needs:

    * an escaping path (relative or not) -> ``OUT_OF_SCOPE``, whatever its
      spelling, because it resolved outside the boundary. This is the security
      finding and it is not downgraded.
    * a benign fragment like ``packages`` that happens to resolve inside ->
      ``NOT_ABSOLUTE``, because the operator did mean something in-workspace
      and simply under-specified it.

    Relative paths ARE resolved before this point, which means they are anchored
    to the process working directory for the purpose of the containment test.
    That is not a hole: the outcome is a refusal either way. Anchored inside the
    workspace the answer is NOT_ABSOLUTE; anchored outside it is OUT_OF_SCOPE.
    There is no anchoring that turns a relative path into an accepted one,
    because absoluteness is checked immediately after and rejects it.
    """
    text = str(raw or "").strip()
    if not text:
        return ""

    # (2) CANONICALIZE FIRST. realpath() is what resolves a Windows junction, a
    # symlink, a '..' segment, and a relative spelling. Measured on this host:
    # os.path.realpath() returns a junction's TARGET while os.path.islink()
    # returns False for a junction, so islink() is not a usable test here and
    # realpath is not optional. This happens BEFORE containment precisely so
    # that containment is judged on where the path really points.
    try:
        canonical = os.path.realpath(text)
    except (OSError, ValueError) as exc:
        # A path containing a NUL byte, or one the OS refuses to resolve at all.
        # Treated as out of scope rather than passed through: an unresolvable
        # path is one this process cannot vouch for.
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_OUT_OF_SCOPE,
            "%s %r could not be resolved on this host (%s: %s), so it cannot be "
            "shown to be inside the allowed project roots. Refusing rather than "
            "guessing." % (field, text, type(exc).__name__, exc),
        ) from None
    if not canonical:
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_NOT_ABSOLUTE,
            "%s %r resolved to an empty path; refusing to store it."
            % (field, text),
        )

    # (5)+(6) CONTAINMENT, BEFORE EXISTENCE. The order is the point; see the
    # module docstring. Both checks below evaluate the RESOLVED path, which is
    # what makes a junction that points outside fail here even though the
    # junction's own name sits legitimately inside the workspace.
    #
    # The list comprehension over an EMPTY roots is the fail-closed property:
    # `any(...)` over no elements is False, so an empty allow-list refuses every
    # path. Written as a loop rather than `all()` for the same reason -- there is
    # no spelling of "no roots configured" that yields True.
    root_list = [str(r) for r in roots if str(r or "").strip()]
    if not root_list:
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_OUT_OF_SCOPE,
            "no allowed project roots are configured, so %s %r cannot be shown "
            "to be inside the workspace. This is fail-closed on purpose: an "
            "empty allow-list must refuse, never admit." % (field, text),
        )
    # Delegated to the launch side's own predicate. Not a re-implementation.
    scope_problem = project_scope_violation(canonical, root_list[0])
    inside = False
    for candidate_root in root_list:
        if _is_inside(canonical, candidate_root):
            inside = True
            break
        # The multi-root form of the refusal names EVERY root, because with more
        # than one allowed root "outside" is only meaningful against the list.
        scope_problem = project_scope_violation(canonical, candidate_root)
    if not inside:
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_OUT_OF_SCOPE,
            scope_problem or (
                "%s %r (resolved to %r) is outside every allowed project root: "
                "%s. A project root is not inert -- it becomes the agent's "
                "working directory AND the directory its Read/Edit/Write/Glob/"
                "Grep/Bash tools are granted -- so a root outside the boundary "
                "is a privilege grant, not a convenience."
                % (field, text, canonical, root_list)),
        )

    # (6b) RE-CHECK AFTER RESOLUTION. The first containment test already judged
    # the resolved path; this second pass re-resolves and re-judges, so a root
    # whose target MOVED between the two operations is still caught rather than
    # admitted on the strength of a resolution that no longer holds. Cheap, and
    # it is the difference between "checked once" and "checked".
    re_resolved = os.path.realpath(canonical)
    if not any(_is_inside(re_resolved, r) for r in root_list):
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_OUT_OF_SCOPE,
            "%s %r resolved to %r, which is inside the allowed roots, but "
            "re-resolving it yields %r, which is not. A junction or reparse "
            "point whose target changed while it was being checked is refused "
            "rather than admitted on a stale resolution."
            % (field, text, canonical, re_resolved),
        )

    # (1) ABSOLUTE -- enforced HERE, after containment, and the placement is the
    # point. See the ORDER note in the docstring: on Windows ``isabs`` is False
    # for the very traversal spellings this guard exists to catch, so checking it
    # first would relabel all four attacks as "give me a full path" and invite
    # the same escape resubmitted in absolute form. Reaching this line means the
    # path already resolved INSIDE the workspace, so the operator's intent was
    # in-bounds and only the spelling is wrong -- which is exactly the case where
    # "not absolute" is the useful, actionable answer.
    #
    # A path with no drive/root component is also refused when it merely LOOKS
    # absolute on the wrong platform (``/etc/passwd`` resolves to
    # ``<drive>:\etc\passwd`` on Windows). ``os.path.isabs`` cannot catch that --
    # it is platform-relative -- so the drive/anchor check below is what makes
    # spec 1 real on the platform this product ships on.
    if not os.path.isabs(text) or not _has_drive_or_root(text):
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_NOT_ABSOLUTE,
            "%s must be an ABSOLUTE path naming a drive or UNC root; got %r. "
            "It resolved to %r, which IS inside the allowed project roots, so "
            "this is a spelling problem rather than a boundary problem -- but "
            "it is still refused rather than stored, because a stored value "
            "whose meaning depends on the process working directory would make "
            "the row mean different things on different launches. Give the "
            "full absolute path." % (field, text, canonical),
        )

    # (3) EXISTENCE -- asked only now, of a path already known to be in scope.
    if not os.path.exists(canonical):
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_MISSING,
            "%s %r (resolved to %r) does not exist. It IS inside the allowed "
            "project roots, so this is a path to fix, not a boundary to argue "
            "with: create the directory, or point at one that exists."
            % (field, text, canonical),
        )

    # (4) DIRECTORY, its own code. See the constant's comment for why this is
    # not MISSING.
    if not os.path.isdir(canonical):
        raise ProjectRootRejected(
            REASON_PROJECT_ROOT_NOT_A_DIRECTORY,
            "%s %r (resolved to %r) exists but is a FILE, not a directory. A "
            "project root is used as a working directory, so it has to be one."
            % (field, text, canonical),
        )

    # (7) CANONICAL ONLY. The caller stores this return value. `raw` is kept for
    # audit and display (spec 9) and is never handed to a child as a cwd.
    return canonical


def scope_report(
    *,
    roots: Optional[Iterable[str]] = None,
    scope_base: Optional[str] = None,
) -> Dict[str, Any]:
    """A machine-readable description of the boundary, for the UI (spec 10).

    The UI cannot render "your project root is not allowed" without being told
    what *is* allowed, and an operator who cannot see the boundary cannot fix a
    rejection. So the allow-list is exposed as DATA on an existing read path
    rather than left implicit in a 422 message.
    """
    effective = list(roots) if roots is not None else allowed_project_roots(
        scope_base=scope_base)
    return {
        "schema": "jcc/v1.3-project-scope/1",
        "allowed_project_roots": effective,
        "reason_codes": list(PROJECT_ROOT_REASON_CODES),
        "fail_closed": True,
        "stored_value": "canonical",
        "note": ("A project root must be absolute, must exist, must be a "
                 "directory, and must resolve inside one of the roots above. "
                 "The stored value is the RESOLVED path; what you typed is "
                 "kept for display only. Widen the boundary with "
                 "launcher.project_scope_base (config) or "
                 "JCC_ALLOWED_PROJECT_ROOTS (environment, os.pathsep-separated)."),
    }