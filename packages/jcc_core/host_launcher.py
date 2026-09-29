"""Host launcher — the only place in JCC that starts a native agent workload.

THE HOLE THIS CLOSES
--------------------
Wave 3 gave the control plane a *bridge*, not a launcher: ``workbuddy_launch``
discovers a WorkBuddy session that the OPERATOR already started
(``~/.workbuddy/sessions/<pid>.json``) and hands that record to the adapter. That
is an honest and useful capability -- and it is ``OBSERVED_EXISTING_LIVE_SESSION``,
which is a fundamentally weaker claim than ``JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION``.
The difference is not vocabulary: in the first case nothing this product did
caused the work to exist.

This module is the piece that actually starts work. It exists because, measured on
this host, WorkBuddy ships a first-class headless CLI
(``<app>/resources/app.asar.unpacked/cli/bin/codebuddy``, "CodeBuddy Code") that
supports ``-p/--print`` with ``--output-format stream-json``, i.e. a real process
that takes a goal and emits a real NDJSON event stream. Before that was measured
the product was right to say ``manual_launch_required``; that claim is now
falsified, so it is replaced by this.

WHAT THIS MODULE WILL NOT DO
----------------------------
1. **It will not build an argv out of the user's goal.** The goal is user text. It
   is passed as EXACTLY ONE argv element to a template whose every other element
   is a frozen constant; there is no shell anywhere (``shell=False``), so there is
   no metacharacter surface to escape. "Mission Goal must never become a shell
   command" is enforced by construction, not by sanitising.

2. **It will not accept an executable or cwd from a request.** Both come from the
   allowlisted :data:`LAUNCH_TEMPLATES` registry plus the mission's own project
   root, which is validated against the intent before anything is spawned.

3. **It will not actuate.** ``user_initiated_mission_launch`` is a capability that
   means "a human pressed Start Mission and a launcher may start a workload". It
   is NOT ``autonomous_actuation``, and this module cannot enable the latter: it
   holds no actuator reference and never writes ``actuation_enabled``. The
   directive it consumes is checked for ``human_approval`` first.

4. **It will not claim a launch it cannot prove.** A process that starts and then
   reports ``Authentication required`` is recorded as ``AUTH_REQUIRED`` -- not as
   a launched mission. The classification is derived from what the child actually
   wrote to its stream.

FAIL-CLOSED ORDER
-----------------
The nine checks are evaluated in the order frozen in :mod:`jcc_core.signing`
(cheap integrity before context), first failure wins, and every failure returns a
reason from that module's closed vocabulary. A refused launch spawns *nothing*.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .nonce_cache import ReplayCache
from .signing import (
    REASON_APPROVAL_MISSING,
    REASON_BUDGET_EXHAUSTED,
    REASON_CAPABILITY_DENIED,
    REASON_OK,
    REASON_POLICY_MISMATCH,
    REASON_SESSION_MISMATCH,
    SIGNING_FORM_V2,
    DirectiveSigner,
    load_signing_key,
    verify_directive,
)

#: The capability a human's "Start Mission" click confers. Deliberately a
#: different name from anything the autonomous controller uses: conflating them
#: would make a user click a way to open the actuation gate.
CAPABILITY_USER_LAUNCH = "USER_INITIATED_MISSION_LAUNCH"

#: Directive lifetime. Short by design: a launch directive is consumed within
#: seconds of being issued, so a long TTL buys nothing and widens the replay
#: window. The nonce cache TTL is deliberately longer than this.
LAUNCH_TTL_S = 120.0

#: Default model for a launched mission. ``auto`` lets the CLI pick; naming one
#: explicitly is a template decision, not a user decision, so it stays here.
DEFAULT_MODEL = "auto"

#: A mission launch is expected to finish within this window in tests. Not a
#: product timeout: the product does not kill long missions, it stops reading.
DEFAULT_COLLECT_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class LaunchTemplate:
    """An allowlisted way to start a workload. Frozen data, never user input.

    ``argv_prefix`` is the argv MINUS the launch head and MINUS the goal. Every
    element is a constant of this build, so the only thing a caller can vary is
    the single slot the goal occupies and the values of explicitly parameterised
    flags.

    The launch HEAD -- what actually becomes ``argv[0]`` (and ``argv[1]`` for a
    script) -- is a property of the template rather than something the launcher
    hard-codes, because on this host the real invocation is
    ``WorkBuddy.exe <cli-script>`` (Electron used as a node runtime), while plain
    ``node <cli-script>`` also works. Baking either one into ``launch()`` would
    make the registry lie about what runs.
    """

    template_id: str
    agent: str
    #: ``node_script`` -> head is ``[node, <script>]``;
    #: ``direct``      -> head is ``[executable]``.
    launch_kind: str = "node_script"
    #: For ``direct``: the absolute executable path.
    executable: str = ""
    #: For ``node_script``: a key into :data:`SCRIPT_RESOLVERS`.
    script_resolver: str = ""
    argv_prefix: Tuple[str, ...] = ()
    #: Flags whose values are supplied per launch; each maps to an intent field.
    #: Ordered, because the argv is built in this order.
    param_flags: Tuple[Tuple[str, str], ...] = ()
    #: True when this template runs the agent to completion and exits.
    terminates: bool = True
    notes: str = ""

    def build_argv(self, *, goal: str, params: Dict[str, str]) -> Tuple[str, ...]:
        """Return ``prefix + flag pairs + [goal]`` -- everything AFTER the head.

        The goal is the LAST element and occupies exactly one slot. Building it
        this way (rather than interpolating a string) is what makes the "goal is
        never a command" property structural: there is no quoting rule to get
        right, because there is no shell and no joined string anywhere.
        """
        argv: List[str] = list(self.argv_prefix)
        for flag, key in self.param_flags:
            value = params.get(key)
            if value is None:
                raise ValueError(
                    "template %s requires param %r" % (self.template_id, key))
            argv.extend([flag, str(value)])
        argv.append(goal)
        return tuple(argv)


def _cli_script() -> Optional[str]:
    """The headless CLI script, resolved from the app tree on this host.

    ``WORKBUDDY_RESOURCES_PATH`` is exported by the WorkBuddy runtime
    (``F:\\WorkBuddy\\resources``), so the path is derived from the environment
    that actually exists rather than hard-coded to one machine's install.
    """
    cands: List[Path] = []
    res = os.environ.get("WORKBUDDY_RESOURCES_PATH")
    if res:
        cands.append(Path(res) / "app.asar.unpacked" / "cli" / "bin" / "codebuddy")
    app = os.environ.get("WORKBUDDY_APP_PATH")
    if app:
        base = Path(app)
        cands.append(base.parent / "app.asar.unpacked" / "cli" / "bin" / "codebuddy")
    for p in cands:
        if p.is_file():
            return str(p)
    return None


def _node_runtime() -> Optional[str]:
    """A node runtime able to run the CLI (>= 18.20.8, per the CLI's own check)."""
    cands = [
        Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"),
        Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\24.14.0\node.exe"),
    ]
    for p in cands:
        if p.is_file():
            return str(p)
    return shutil.which("node")


#: Named script resolvers. A template names one; the launcher never resolves a
#: path from caller input. Values are pure functions of the host environment.
SCRIPT_RESOLVERS = {
    "workbuddy_cli": _cli_script,
}


#: The registry. One entry today, because one agent can actually be launched on
#: this host, and inventing entries for agents we cannot start is exactly the
#: 4/4-green fiction the connection centre is forbidden to show.
LAUNCH_TEMPLATES: Dict[str, LaunchTemplate] = {
    "workbuddy-cli-headless-v1": LaunchTemplate(
        template_id="workbuddy-cli-headless-v1",
        agent="workbuddy",
        launch_kind="node_script",
        script_resolver="workbuddy_cli",
        argv_prefix=(
            "-p",
            "--output-format", "stream-json",
            "--permission-mode", "default",
            "--tools", "Read,Edit,Write,Glob,Grep,Bash",
        ),
        param_flags=(
            ("--session-id", "session_id"),
            ("--model", "model"),
            ("--add-dir", "project_root"),
        ),
        terminates=True,
        notes=("Headless one-shot: the child takes the goal, runs to completion "
               "and exits, streaming NDJSON on stdout. --add-dir binds tool access "
               "to the mission's project root; the process cwd is set to the same "
               "root so relative paths agree."),
    ),
}


def build_head(tpl: LaunchTemplate) -> Tuple[Optional[List[str]], Optional[str]]:
    """Return ``(head_argv, problem)``. Exactly one of the two is None.

    ``problem`` is a machine-readable code, never a silent None: a launcher that
    cannot resolve its own executable must be able to say WHICH half failed.
    """
    if tpl.launch_kind == "direct":
        if tpl.executable and Path(tpl.executable).is_file():
            return [tpl.executable], None
        return None, "EXECUTABLE_NOT_FOUND"
    resolver = SCRIPT_RESOLVERS.get(tpl.script_resolver)
    if resolver is None:
        return None, "UNKNOWN_SCRIPT_RESOLVER"
    script = resolver()
    if not script:
        return None, "SCRIPT_NOT_FOUND"
    node = _node_runtime()
    if not node:
        return None, "NO_NODE_RUNTIME"
    return [node, script], None

#: Params whose values must already be validated before they reach a template.
_PARAM_ORDER = ("session_id", "model", "project_root")


def template_registry_digest(
        registry: Optional[Dict[str, LaunchTemplate]] = None) -> str:
    """sha256 over the canonical registry — the ``capability_version`` anchor.

    A directive binds to this digest, so adding, removing or editing a template
    invalidates every outstanding directive: you cannot get a launch under one
    registry and execute it under another.

    ``registry`` defaults to the global one but a caller may pass its own. It
    takes the parameter at all because a launcher instance can be constructed with
    a different registry (tests do), and a digest computed from the GLOBAL table
    while the instance ran a different one would be exactly the "two sources of
    truth for one fact" this project bans -- the anchor would name a registry that
    did not execute.
    """
    reg = LAUNCH_TEMPLATES if registry is None else registry
    canon = []
    for tid in sorted(reg):
        t = reg[tid]
        canon.append({
            "template_id": t.template_id,
            "agent": t.agent,
            "launch_kind": t.launch_kind,
            "executable": t.executable,
            "script_resolver": t.script_resolver,
            "argv_prefix": list(t.argv_prefix),
            "param_flags": [list(x) for x in t.param_flags],
            "terminates": t.terminates,
        })
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def args_hash(template: LaunchTemplate, head: List[str], *, goal: str,
              params: Dict[str, str]) -> str:
    """sha256 of the concrete argv with the goal REPLACED by a placeholder.

    The goal is excluded so that the hash identifies the *shape* of the command
    (which binary, which template, which flags, which binding) rather than its
    content; two missions with different goals but the same binding must hash the
    same. The goal's own integrity is carried by the signed directive's
    ``evidence_hash`` instead.

    The head IS included, which makes the hash host-specific by construction --
    the executable really is resolved on this host, and a hash that omitted it
    would name a command that could run anywhere, i.e. a different command.
    """
    argv = list(head) + list(template.build_argv(goal="\x00GOAL\x00", params=params))
    blob = json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class LaunchIntent:
    """Everything a launch needs to be *bound* and *auditable*.

    ``user_approved`` is a statement of fact about the request path: it is set by
    the server only after an authenticated, human-initiated POST. It is never
    read from the request body, so a caller cannot approve itself.
    """

    mission_id: str
    project_id: str
    agent_id: str
    adapter_id: str
    project_root: str
    template_id: str
    goal: str
    issued_at: float = field(default_factory=lambda: time.time())
    expires_at: float = 0.0
    nonce: str = ""
    user_approved: bool = False
    policy_version: str = ""
    capability_version: str = ""
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        if self.expires_at == 0.0:
            object.__setattr__(self, "expires_at", self.issued_at + LAUNCH_TTL_S)

    def params(self) -> Dict[str, str]:
        """The template parameter map. Single definition, used by argv AND the hash."""
        return {
            "session_id": self.mission_id,
            "model": self.model or DEFAULT_MODEL,
            "project_root": self.project_root,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def evidence_hash(self) -> str:
        """The value the signed directive commits to (stable, sorted keys)."""
        d = asdict(self)
        d.pop("goal", None)   # the goal is not reproduced in the audit tape verbatim
        return "sha256:" + hashlib.sha256(
            json.dumps(d, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")).hexdigest()


class LaunchRefused(RuntimeError):
    """A launch was refused before any process was spawned. ``reason`` is frozen."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__("%s%s" % (reason, (": " + detail) if detail else ""))
        self.reason = reason
        self.detail = detail


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """Window's ``JOBOBJECT_EXTENDED_LIMIT_INFORMATION``.

    Declared at module level, not inside :class:`JobGuard`: a class body does not
    see the enclosing class's namespace, so a nested reference to a sibling nested
    class raises NameError at import time. That is a Python rule worth not
    rediscovering inside a class that is supposed to be about Windows jobs.
    """

    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JobGuard:
    """A Windows Job Object that kills its members when the LAST handle closes.

    WHY THIS EXISTS
    ---------------
    A launcher that starts a workload and walks away owns nothing. On this host
    that is not hypothetical: the CLI starts helper children, and an abandoned
    agent process is exactly the kind of invisible state the durable-runtime work
    already had to clean up once. ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` makes
    "the launcher goes away" and "the workload goes away" the same event, which is
    the only ownership rule that does not depend on the launcher being well
    behaved.

    It is also why the CLI ships ``bin/windows-job.cjs`` -- this is the platform's
    supported mechanism, not an invention.

    NOT AVAILABLE IS A DISTINCT OUTCOME
    -----------------------------------
    On a non-Windows host, or when the sandbox forbids job assignment,
    :meth:`assign` returns ``False`` with a reason. The launcher records that as
    ``job_object: unavailable:<reason>`` and proceeds -- it does not pretend to
    own the child, and it does not silently drop the fact either.
    """

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    def __init__(self) -> None:
        self.handle = None
        self.available = False
        self.reason = "not_attempted"

    def create(self) -> bool:
        if os.name != "nt":
            self.reason = "not_windows"
            return False
        try:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = ctypes.c_void_p
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            k.SetInformationJobObject.restype = ctypes.c_int
            k.SetInformationJobObject.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            k.AssignProcessToJobObject.restype = ctypes.c_int
            k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            self.handle = k.CreateJobObjectW(None, None)
            if not self.handle:
                self.reason = "CreateJobObject err=%d" % ctypes.get_last_error()
                return False
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = k.SetInformationJobObject(
                ctypes.c_void_p(self.handle), self._JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                self.reason = "SetInformationJobObject err=%d" % ctypes.get_last_error()
                k.CloseHandle(ctypes.c_void_p(self.handle))
                self.handle = None
                return False
            self.available = True
            self.reason = "ok"
            self._k = k
            return True
        except Exception as exc:  # noqa: BLE001
            self.reason = "%s: %s" % (type(exc).__name__, exc)
            return False

    def assign(self, proc: Any) -> bool:
        """Put ``proc`` in the job. Returns False with a reason, never raises."""
        if not self.available:
            return False
        try:
            h = getattr(proc, "_handle", None)
            if h is None:
                self.reason = "no_process_handle"
                return False
            ok = self._k.AssignProcessToJobObject(
                ctypes.c_void_p(self.handle), ctypes.c_void_p(int(h)))
            if not ok:
                self.reason = "AssignProcessToJobObject err=%d" % ctypes.get_last_error()
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            self.reason = "%s: %s" % (type(exc).__name__, exc)
            return False

    def close(self) -> None:
        if self.handle:
            try:
                self._k.CloseHandle(ctypes.c_void_p(self.handle))
            except Exception:  # noqa: BLE001
                pass
            self.handle = None
        self.available = False


def _is_inside(child: str, parent: str) -> bool:
    """True when ``child`` is ``parent`` or below it, by resolved path.

    Used for the project-root binding. ``os.path.commonpath`` on resolved paths
    is the check that does not fall for ``C:\\proj\\..\\elsewhere`` or a case
    variation on Windows.
    """
    try:
        c = os.path.normcase(os.path.realpath(child))
        p = os.path.normcase(os.path.realpath(parent))
    except Exception:  # noqa: BLE001
        return False
    if c == p:
        return True
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False


class HostLauncher:
    """Verify a signed launch directive, then start an allowlisted workload.

    The launcher owns a :class:`ReplayCache` so that a nonce is consumed exactly
    once across every launch attempt in this process. It never owns an actuator.
    """

    def __init__(self, *, signer: Optional[DirectiveSigner] = None,
                 replay: Optional[ReplayCache] = None,
                 templates: Optional[Dict[str, LaunchTemplate]] = None,
                 audit: Any = None) -> None:
        self.signer = signer or DirectiveSigner(load_signing_key(), form=SIGNING_FORM_V2)
        self.replay = replay or ReplayCache()
        self.templates = templates if templates is not None else LAUNCH_TEMPLATES
        self.audit = audit
        # The digest is computed from the registry this instance will actually
        # execute; see template_registry_digest.
        self.registry_digest = template_registry_digest(self.templates)
        #: Live job guards, one per spawned child. Held so the job handle stays
        #: open for as long as this launcher lives -- that is what makes the
        #: kill-on-close guarantee mean "when the launcher exits", not "now".
        self._job_guards: List[Any] = []

    def stop_all(self) -> int:
        """Close every job guard, terminating the workloads they own.

        Returns the number of jobs closed. This is the only way a workload ends
        other than finishing: there is no signal-sending path in this module.
        """
        n = 0
        for g in self._job_guards:
            try:
                g.close()
                n += 1
            except Exception:  # noqa: BLE001
                pass
        self._job_guards = []
        return n

    # -- verification -------------------------------------------------------- #

    def verify(self, intent: LaunchIntent, directive: Any) -> Tuple[bool, str]:
        """Run the nine mandated checks in order. Returns ``(ok, reason)``.

        Reuses :func:`jcc_core.signing.verify_directive` for checks 1-4 so the
        integrity semantics are literally the same code as the effect boundary,
        then applies the five context checks this launcher owns (5-9).

        The signature is read OFF THE DIRECTIVE, never passed alongside it: a
        verifier that accepts a signature as a separate argument has two sources
        of truth for one fact, and the one it reads is whichever the caller
        chose. ``now`` is supplied explicitly rather than defaulted inside the
        kernel so a test can pin time without patching the clock.
        """
        # 1-4: signed present / signature / ttl / replay. verify_directive owns
        # the replay cache too, so the nonce is consumed there -- one owner.
        ok, reason = verify_directive(
            directive, self.signer.key, now=time.time(),
            replay=self.replay, form=SIGNING_FORM_V2)
        if not ok:
            return False, reason

        # 5: mission binding. The directive must name THIS mission.
        bound = str(getattr(directive, "session_id", "") or "")
        if bound != intent.mission_id:
            return False, REASON_SESSION_MISMATCH

        # 6: capability. A user-initiated launch is the only capability this
        # launcher honours; anything else is denied rather than reinterpreted.
        cap = str((getattr(directive, "params", None) or {}).get("capability", ""))
        if cap != CAPABILITY_USER_LAUNCH:
            return False, REASON_CAPABILITY_DENIED

        # 7: policy version must match what the intent was previewed under.
        pol = str(getattr(directive, "policy_version", "") or "")
        if intent.policy_version and pol != intent.policy_version:
            return False, REASON_POLICY_MISMATCH

        # 8: budget. A launch may not start when the caller says budget is spent.
        if bool((getattr(directive, "params", None) or {}).get("budget_exhausted")):
            return False, REASON_BUDGET_EXHAUSTED

        # 9: approval. The intent must be human-approved AND the signed directive
        # must say so; either alone is not enough.
        if not intent.user_approved:
            return False, REASON_APPROVAL_MISSING
        if not bool(getattr(directive, "human_approval", False)):
            return False, REASON_APPROVAL_MISSING

        return True, REASON_OK

    # -- preview ------------------------------------------------------------- #

    def preview(self, intent: LaunchIntent) -> Dict[str, Any]:
        """What WOULD run. No side effects: nothing is spawned, no nonce taken.

        This is the object the UI shows before the human clicks Start Mission, so
        it must be honest about *why* a launch might be refused -- including the
        case where the template cannot be resolved on this host.
        """
        tpl = self.templates.get(intent.template_id)
        problems: List[Dict[str, str]] = []
        head: Optional[List[str]] = None
        if tpl is None:
            problems.append({"code": "UNKNOWN_TEMPLATE", "detail": intent.template_id})
        else:
            if tpl.agent != intent.agent_id:
                problems.append({"code": "TEMPLATE_AGENT_MISMATCH",
                                 "detail": "%s != %s" % (tpl.agent, intent.agent_id)})
            head, head_problem = build_head(tpl)
            if head_problem:
                problems.append({"code": head_problem, "detail": tpl.template_id})
        root = Path(intent.project_root) if intent.project_root else None
        if not root or not root.is_dir():
            problems.append({"code": "PROJECT_ROOT_MISSING",
                             "detail": intent.project_root or ""})

        argv: List[str] = []
        if tpl and head:
            argv = head + list(tpl.build_argv(goal=intent.goal, params=intent.params()))

        return {
            "schema": "jcc/v1.3-launch-preview/1",
            "launchable": not problems,
            "problems": problems,
            "intent": intent.to_dict(),
            "template": (asdict(tpl) if tpl else None),
            "argv": argv,
            "argv_goal_slot": len(argv) - 1 if argv else None,
            "project_root_bound": bool(root and root.is_dir()),
            "capability": CAPABILITY_USER_LAUNCH,
            "capability_version": self.registry_digest,
            "args_hash": (args_hash(tpl, head, goal=intent.goal, params=intent.params())
                          if (tpl and head) else None),
            "shell": False,
            "actuation": False,
            "note": ("preview only: nothing was spawned and no nonce was consumed"),
        }

    # -- launch -------------------------------------------------------------- #

    def launch(self, intent: LaunchIntent, directive: Any) -> Dict[str, Any]:
        """Verify, then spawn. Returns a structured, honest outcome.

        A refusal returns ``spawned=False`` and starts nothing. A spawned child
        returns its real pid, and the classification is left to
        :meth:`classify_stream` because only the child's own output can say
        whether the workload actually ran.
        """
        t0 = time.time()
        out: Dict[str, Any] = {
            "schema": "jcc/v1.3-launch-result/1",
            "mission_id": intent.mission_id,
            "template_id": intent.template_id,
            "capability": CAPABILITY_USER_LAUNCH,
            "steps": [],
            "spawned": False,
            "pid": None,
            "classification": None,
            "reason": None,
            "actuation": False,
        }

        def step(name: str, ok: bool, detail: str = "") -> None:
            out["steps"].append({"step": name, "ok": bool(ok), "detail": detail})

        out["steps"].append({"step": "LAUNCH_REQUESTED", "ok": True,
                             "detail": "template=%s" % intent.template_id})

        try:
            ok, reason = self.verify(intent, directive)
        except Exception as exc:  # noqa: BLE001
            ok, reason = False, "VERIFY_ERROR"
            out["verify_exception"] = "%s: %s" % (type(exc).__name__, exc)
        if not ok:
            out["reason"] = reason
            step("verify", False, reason)
            out["classification"] = "REFUSED"
            out["elapsed_s"] = round(time.time() - t0, 3)
            return out
        step("verify", True, REASON_OK)

        tpl = self.templates.get(intent.template_id)
        if tpl is None:
            out["reason"] = "UNKNOWN_TEMPLATE"
            step("template", False, intent.template_id)
            out["classification"] = "REFUSED"
            return out
        head, head_problem = build_head(tpl)
        if head_problem or not head:
            out["reason"] = head_problem or "EXECUTABLE_NOT_FOUND"
            step("resolve", False, head_problem or "")
            out["classification"] = "REFUSED"
            return out
        step("resolve", True, " ".join(head))

        # The project root binding is re-checked HERE, not only in preview: a
        # directive that was previewed against one root must not be replayable
        # against another.
        if not intent.project_root or not Path(intent.project_root).is_dir():
            out["reason"] = "PROJECT_ROOT_MISSING"
            step("project_root", False, intent.project_root or "")
            out["classification"] = "REFUSED"
            return out
        step("project_root", True, intent.project_root)

        argv = head + list(tpl.build_argv(goal=intent.goal, params=intent.params()))
        out["args_hash"] = args_hash(tpl, head, goal=intent.goal, params=intent.params())
        out["argv_len"] = len(argv)
        out["argv_goal_slot"] = len(argv) - 1
        out["steps"].append({"step": "LAUNCH_ACCEPTED", "ok": True,
                             "detail": "args_hash=%s" % out["args_hash"]})

        child = self._spawn(argv, cwd=intent.project_root, out_dir=self._out_dir())
        out.update(child)
        if not child.get("spawned"):
            out["classification"] = "FAILED"
            return out
        out["steps"].append({"step": "PROCESS_STARTED", "ok": True,
                             "detail": "pid=%s" % child.get("pid")})
        out["elapsed_s"] = round(time.time() - t0, 3)
        return out

    # -- stream ingestion ---------------------------------------------------- #

    def classify_stream(self, events: List[Dict[str, Any]],
                        *, exit_code: Optional[int]) -> Dict[str, Any]:
        """Turn a child's own NDJSON stream into an honest classification.

        The whole point of this function is that it refuses to upgrade on
        structural evidence alone. A process that started and wrote a
        well-formed stream but whose model turn was refused has NOT run a
        mission, and saying otherwise would be the exact lie
        ``FINAL != COMPLETE`` exists to catch.
        """
        types = [str(e.get("type") or "") for e in events]
        auth_error = None
        for e in events:
            if e.get("type") == "result" and e.get("is_error"):
                errs = e.get("errors") or []
                for msg in errs:
                    if "Authentication required" in str(msg):
                        auth_error = str(msg)
                if auth_error is None and errs:
                    auth_error = str(errs[0])
        first_aep = next((e for e in events if e.get("type") == "assistant"), None)
        return {
            "event_count": len(events),
            "event_types": types,
            "exit_code": exit_code,
            "first_assistant_event": bool(first_aep),
            "auth_error": auth_error,
            "classification": ("AUTH_REQUIRED" if auth_error else
                               "LAUNCHED" if first_aep else
                               "STARTED_NO_TELEMETRY"),
        }

    # -- internals ----------------------------------------------------------- #

    def _out_dir(self) -> Path:
        d = Path(os.environ.get("JCC_LAUNCH_LOG_DIR")
                 or (Path(os.environ.get("TEMP", ".")) / "jcc-launch"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _spawn(self, argv: List[str], *, cwd: str, out_dir: Path) -> Dict[str, Any]:
        """Start the child with real files for stdout/stderr and no stdin.

        Files, not pipes: the CLI starts helper processes, and a grandchild that
        inherits a pipe write end makes a reader block past its own deadline --
        measured, not theorised (the first probe hung forever and reported
        nothing). ``DEVNULL`` for stdin so an interactive prompt can never wedge a
        launch. ``shell=False`` always -- the argv is a vector, never a command
        line.

        The child is placed in a :class:`JobGuard` so that it cannot outlive the
        launcher. Whether the job was actually obtained is REPORTED
        (``job_object``), never assumed.
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        stem = out_dir / ("launch-%s-%d" % (stamp, os.getpid()))
        out_f, err_f = str(stem) + ".ndjson", str(stem) + ".stderr.txt"
        guard = JobGuard()
        guard.create()
        ofh = open(out_f, "wb")
        efh = open(err_f, "wb")
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=ofh, stderr=efh, shell=False)
        except Exception as exc:  # noqa: BLE001
            ofh.close()
            efh.close()
            guard.close()
            return {"spawned": False, "error": "%s: %s" % (type(exc).__name__, exc),
                    "stdout_file": out_f, "stderr_file": err_f}
        finally:
            ofh.close()
            efh.close()
        owned = guard.assign(proc)
        if owned:
            # Keep the job handle alive on the instance: closing it early would
            # kill the child immediately, which is the opposite of owning it.
            self._job_guards.append(guard)
            job_state = "assigned"
        else:
            job_state = "unavailable:%s" % guard.reason
            guard.close()
        return {"spawned": True, "pid": proc.pid, "stdout_file": out_f,
                "stderr_file": err_f, "shell": False, "job_object": job_state}

    def read_stream(self, path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Parse a child's NDJSON file. Returns ``(events, unparseable)``.

        A line that does not parse is REPORTED, not skipped silently: a stream
        that is 90% parseable is a finding, and swallowing it would hide a
        contract change behind a green count.
        """
        events: List[Dict[str, Any]] = []
        bad: List[Dict[str, Any]] = []
        p = Path(path)
        if not p.is_file():
            return events, [{"line": -1, "err": "no such file", "head": str(p)}]
        for i, line in enumerate(p.read_text(encoding="utf-8",
                                             errors="backslashreplace").splitlines()):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception as exc:  # noqa: BLE001
                bad.append({"line": i, "err": str(exc)[:120], "head": s[:160]})
                continue
            if isinstance(obj, dict):
                events.append(obj)
            else:
                bad.append({"line": i, "err": "not an object", "head": s[:160]})
        return events, bad
