"""Wave 9.2 — the mission launch control plane (jcc_core.launch_control).

What these tests are FOR
------------------------
Wave 9.1 shipped a HostLauncher that can verify a signed directive and spawn a
real workload. The risk this file exists to contain is the gap between that and
a product claim: it is very easy to build something that SPAWNS and then report
it as a mission that RAN. So the tests below are written in two halves.

  * The first half proves the plane REFUSES. A wrong capability, a tampered
    directive, a spent nonce, an unknown mission, a bad model token, a missing
    signing key -- each must produce ``spawned is False`` and a reason from the
    frozen vocabulary, with nothing started and nothing recorded as started.
  * The second half proves it can actually START something, using a REAL child
    process (a python one-liner emitting CLI-shaped NDJSON) so the assertions do
    not depend on the WorkBuddy CLI's credential state.

The single most important test is
``test_auth_failure_never_walks_the_mission_to_running``: a run that started,
was refused at authentication, and reported a session id must still NOT be
handed to ``MissionRuntime.launch``. That path walks DRAFT->READY->STARTING->
RUNNING, so feeding it an unproductive stream is precisely how a product comes
to show a mission running that did no work.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jcc_core import launch_control as LC
from jcc_core.domain import DomainStore
from jcc_core.host_launcher import HostLauncher, LaunchTemplate
from jcc_core.schemas import Directive
from jcc_core.signing import (
    SIGNING_FORM_V2,
    DirectiveSigner,
    sign_directive,
)

KEY = b"unit-launch-control-key-not-a-secret"

_OK_SCRIPT = "\n".join([
    "import json",
    'print(json.dumps({"type":"system","subtype":"init","session_id":"sess-unit-1",'
    '"cwd":".","version":"unit-cli"}))',
    'print(json.dumps({"type":"assistant","text":"ok"}))',
    'print(json.dumps({"type":"result","is_error":False}))',
])

_AUTH_SCRIPT = "\n".join([
    "import json",
    'print(json.dumps({"type":"system","subtype":"init","session_id":"sess-auth-1"}))',
    'print(json.dumps({"type":"result","is_error":True,'
    '"errors":["Authentication required"]}))',
])

_NO_SESSION_SCRIPT = "\n".join([
    "import json",
    'print(json.dumps({"type":"assistant","text":"hello"}))',
    'print(json.dumps({"type":"result","is_error":False}))',
])

_SILENT_SCRIPT = "pass"

#: Template IDs are per-test so a launcher's registry digest is stable within a
#: test and cannot be confused with another test's registry.
_OK_TID = "unit-ok-v1"
_AUTH_TID = "unit-auth-v1"
_NOSID_TID = "unit-nosid-v1"
_SILENT_TID = "unit-silent-v1"


def _template(tid: str, script: str) -> LaunchTemplate:
    """A REAL template whose head is this interpreter.

    ``-c <script>`` is the head's prefix and the goal is appended as one more
    argv element, exactly as the production template appends it. Using the same
    shape means these tests exercise the real argv-building path.
    """
    return LaunchTemplate(template_id=tid, agent="workbuddy", launch_kind="direct",
                          executable=sys.executable, argv_prefix=("-c", script))


class _StubRuntime:
    """Records what WOULD have been ingested. Never touches the engine."""

    def __init__(self) -> None:
        self.calls: list = []

    def launch(self, mission_id, *, event=None):  # noqa: D401
        self.calls.append((mission_id, event))
        return {"ok": bool(event),
                "session_id": (event or {}).get("sessionId"),
                "error": None if event else "no event"}


def _field(obj, name):
    """Read a field from a domain row that may be a pydantic model OR a dict.

    ``list_approvals`` returns models while ``list_runs`` returns dicts, and a
    test that assumed either one would break on the other rather than on the
    behaviour under test.
    """
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _make(tmp_path: Path, *, tid: str = _OK_TID, script: str = _OK_SCRIPT,
          signer_key=KEY, collect_timeout_s: float = 60.0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    domain = DomainStore(str(tmp_path / "domain.db"))
    project = domain.create_project("Unit project", root_path=str(tmp_path))
    mission = domain.create_mission(project.project_id, "Unit mission", goal="say hi")
    tpl = _template(tid, script)
    signer = DirectiveSigner(signer_key, form=SIGNING_FORM_V2)
    # CR-003: this fixture's project root IS tmp_path, so tmp_path is the
    # workspace boundary it means. The boundary goes on the LAUNCHER, not on the
    # controller: this fixture injects its own launcher, and an injected launcher
    # is used as-is (so that a test can supply a custom template registry), which
    # means a `scope_base=` passed to the controller would be silently ignored
    # here. Without a declared boundary every launch in this file is refused for
    # being outside the repository root -- correctly, but not what these tests are
    # about. Declaring it keeps each test's original subject intact: a root that
    # VANISHES still yields PROJECT_ROOT_MISSING, because a vanished root INSIDE
    # the boundary passes containment and then fails existence. Scope refusal
    # itself is pinned in tools/cr003_project_root_falsifier.py.
    launcher = HostLauncher(signer=signer, templates={tid: tpl},
                            scope_base=str(tmp_path))
    runtime = _StubRuntime()
    ctl = LC.LaunchController(
        domain, runtime, signer=signer, launcher=launcher,
        measure_path=tmp_path / "measure.json",
        journal_path=tmp_path / "journal.ndjson",
        collect_timeout_s=collect_timeout_s)
    return SimpleNamespace(domain=domain, project=project, mission=mission,
                           tpl=tpl, signer=signer, launcher=launcher,
                           runtime=runtime, ctl=ctl, tmp=tmp_path)


# =========================================================================== #
# 1. the template registry and the intent binding
# =========================================================================== #
def test_template_for_agent_is_deterministic_and_never_invents_an_agent():
    reg = {"b-v1": _template("b-v1", "pass"), "a-v1": _template("a-v1", "pass")}
    # dict order, not sorted order: adding a template must not silently move the
    # template a mission launches with.
    assert LC.template_for_agent("workbuddy", reg).template_id == "b-v1"
    assert LC.template_for_agent("claude", reg) is None


def test_capability_version_names_the_instance_registry(tmp_path):
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    assert req["capability_version"] == env.launcher.registry_digest
    assert req["preview"]["capability_version"] == env.launcher.registry_digest
    # The digest is a real sha256 anchor, not a placeholder.
    assert env.launcher.registry_digest.startswith("sha256:")


def test_intent_binds_mission_project_root_and_adapter(tmp_path):
    env = _make(tmp_path)
    intent = LC.build_intent(env.mission, env.project, template=env.tpl,
                             policy_version=LC.DEFAULT_POLICY_VERSION,
                             capability_version="sha256:anchor")
    assert intent.mission_id == env.mission.mission_id
    assert intent.project_id == env.project.project_id
    assert intent.project_root == str(tmp_path)
    assert intent.adapter_id == "workbuddy"
    assert intent.user_approved is True
    # The goal is NOT in the audit-facing evidence hash: the tape names the
    # mission and the binding, not the operator's content.
    assert intent.evidence_hash().startswith("sha256:")
    assert "say hi" not in json.dumps(intent.evidence_hash())


def test_build_intent_refuses_a_mission_with_no_root(tmp_path):
    domain = DomainStore(str(tmp_path / "d.db"))
    project = domain.create_project("P", root_path="")
    mission = domain.create_mission(project.project_id, "M", goal="g")
    with pytest.raises(LC.LaunchControlError) as ei:
        LC.build_intent(mission, project, template=_template(_OK_TID, "pass"))
    assert ei.value.code == "PROJECT_ROOT_MISSING"


def test_build_intent_refuses_a_missing_project():
    with pytest.raises(LC.LaunchControlError) as ei:
        LC.build_intent(SimpleNamespace(mission_id="m", project_id="p"),
                        None, template=_template(_OK_TID, "pass"))
    assert ei.value.code == "PROJECT_NOT_FOUND"


# =========================================================================== #
# 2. the directive
# =========================================================================== #
def test_directive_is_short_lived_single_use_and_four_way_bound(tmp_path):
    env = _make(tmp_path)
    intent = LC.build_intent(env.mission, env.project, template=env.tpl,
                             policy_version="p3.0.0",
                             capability_version=env.launcher.registry_digest)
    d = LC.sign_launch_directive(intent, signer=env.signer)
    assert d.signed is True and d.signature and d.signature.startswith("sha256=")
    assert d.signature_verified is True
    assert d.nonce == intent.nonce
    assert d.session_id == intent.mission_id                       # mission-bound
    assert d.policy_version == "p3.0.0"                            # policy-bound
    assert d.params["capability"] == LC.CAPABILITY_USER_LAUNCH      # authority-bound
    assert d.evidence_hash == intent.evidence_hash()               # registry-bound
    assert d.human_approval
    assert d.scope and intent.mission_id in d.scope and str(tmp_path) in d.scope
    assert 0 < d.ttl_s <= 120.0
    assert d.issued_at is not None


def test_signing_without_a_key_fails_closed(tmp_path):
    env = _make(tmp_path, signer_key=None)
    with pytest.raises(LC.LaunchControlError) as ei:
        env.ctl.request(env.mission.mission_id, commit=False)
    assert ei.value.code == "NO_SIGNING_KEY"


# =========================================================================== #
# 3. preview is side-effect free
# =========================================================================== #
def test_preview_spawns_nothing_and_consumes_no_nonce(tmp_path):
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    assert req["committed"] is False
    assert req["would_accept"] is True
    assert "spawned" not in req                       # never even attempted
    assert env.runtime.calls == []
    assert not (tmp_path / "measure.json").exists()   # nothing was measured
    assert req["actuation"] is False
    assert req["preview"]["shell"] is False
    assert req["preview"]["launchable"] is True
    # ...and the SAME directive still commits, which is the proof the preview
    # did not spend the one-shot nonce.
    applied = env.ctl.request(env.mission.mission_id, commit=True)
    assert applied["spawned"] is True


def test_goal_occupies_exactly_one_argv_slot_and_is_the_last(tmp_path):
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    argv = req["preview"]["argv"]
    assert argv[-1] == "say hi"
    assert req["preview"]["argv_goal_slot"] == len(argv) - 1
    assert [a for a in argv if a == "say hi"] == ["say hi"]
    assert argv[0] == sys.executable
    assert "-c" in argv


def test_a_model_token_that_is_not_a_token_is_refused(tmp_path):
    env = _make(tmp_path)
    # NB: the empty string is NOT in this list on purpose -- an empty model
    # means "use the default" and is normalised, not refused. Only a value that
    # would reach the child as its own token is rejected.
    for bad in ("--permission-mode", "a b", "x;rm -rf /", "a/../../b", "-dash",
                "a\tb", "with\"quote"):
        with pytest.raises(LC.LaunchControlError) as ei:
            env.ctl.request(env.mission.mission_id, commit=False, model=bad)
        assert ei.value.code == "INVALID_MODEL", bad
    assert not (tmp_path / "measure.json").exists()
    # The normalisation is real: an empty model previews as the default.
    ok = env.ctl.request(env.mission.mission_id, commit=False, model="")
    assert ok["would_accept"] is True
    assert ok["preview"]["intent"]["model"] == "auto"


def test_unknown_mission_and_unknown_agent_are_refused(tmp_path):
    env = _make(tmp_path)
    with pytest.raises(LC.LaunchControlError) as ei:
        env.ctl.request("mis-does-not-exist", commit=False)
    assert ei.value.code == "MISSION_NOT_FOUND"

    other = env.domain.create_mission(env.project.project_id, "Other", agent="hermes")
    with pytest.raises(LC.LaunchControlError) as ei2:
        env.ctl.request(other.mission_id, commit=False)
    assert ei2.value.code == "NO_TEMPLATE_FOR_AGENT"


# =========================================================================== #
# 4. refusals: a refusal starts nothing
# =========================================================================== #
def _refused(result):
    return result.get("spawned") is False and result.get("classification") == "REFUSED"


def test_tampering_without_resigning_dies_at_the_signature(tmp_path):
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    d = req["directive"]
    d["params"] = dict(d["params"])
    d["params"]["budget_exhausted"] = False
    d["params"]["capability"] = "AUTONOMOUS_AGENT_CONTROL"
    tampered = Directive(**{k: v for k, v in d.items()
                            if k in set(Directive.model_fields)})
    intent = LC.build_intent(env.mission, env.project, template=env.tpl,
                             policy_version=env.ctl.policy_version,
                             capability_version=env.launcher.registry_digest)
    ok, reason = env.launcher.verify(intent, tampered)
    assert ok is False
    assert reason == "BAD_SIGNATURE"     # check 2 fires BEFORE the capability check


def test_a_validly_signed_wrong_capability_is_denied(tmp_path):
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    d = req["directive"]
    d["params"] = dict(d["params"])
    d["params"]["capability"] = "AUTONOMOUS_AGENT_CONTROL"
    tampered = Directive(**{k: v for k, v in d.items()
                            if k in set(Directive.model_fields)})
    tampered.signature = sign_directive(tampered, env.signer.key, form=SIGNING_FORM_V2)
    intent = LC.build_intent(env.mission, env.project, template=env.tpl,
                             policy_version=env.ctl.policy_version,
                             capability_version=env.launcher.registry_digest)
    ok, reason = env.launcher.verify(intent, tampered)
    assert ok is False
    assert reason == "CAPABILITY_DENIED"


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d.update(signed=False), "UNSIGNED"),
    (lambda d: d.update(ttl_s=-10.0), "BAD_SIGNATURE"),
    (lambda d: d.update(session_id="some-other-mission"), "BAD_SIGNATURE"),
])
def test_mutated_directives_never_spawn(tmp_path, mutate, expected):
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    raw = dict(req["directive"])
    mutate(raw)
    tampered = Directive(**{k: v for k, v in raw.items()
                            if k in set(Directive.model_fields)})
    intent = LC.build_intent(env.mission, env.project, template=env.tpl,
                             policy_version=env.ctl.policy_version,
                             capability_version=env.launcher.registry_digest)
    ok, reason = env.launcher.verify(intent, tampered)
    assert ok is False
    assert reason == expected
    assert env.runtime.calls == []


def test_a_directive_is_single_use(tmp_path):
    """The second commit with the same directive is refused as a REPLAY."""
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    directive = Directive(**{k: v for k, v in req["directive"].items()
                             if k in set(Directive.model_fields)})
    intent = LC.build_intent(env.mission, env.project, template=env.tpl,
                             policy_version=env.ctl.policy_version,
                             capability_version=env.launcher.registry_digest)
    ok1, _ = env.launcher.verify(intent, directive)
    ok2, reason2 = env.launcher.verify(intent, directive)
    assert ok1 is True
    assert ok2 is False
    assert reason2 == "REPLAY"


def test_dry_verify_does_not_consume_the_nonce_but_real_verify_does(tmp_path):
    env = _make(tmp_path)
    req = env.ctl.request(env.mission.mission_id, commit=False)
    directive = Directive(**{k: v for k, v in req["directive"].items()
                             if k in set(Directive.model_fields)})
    intent = LC.build_intent(env.mission, env.project, template=env.tpl,
                             policy_version=env.ctl.policy_version,
                             capability_version=env.launcher.registry_digest)
    for _ in range(3):
        ok, reason = env.launcher.dry_verify(intent, directive)
        assert (ok, reason) == (True, "ok")
    ok, _ = env.launcher.verify(intent, directive)
    assert ok is True
    ok2, reason2 = env.launcher.dry_verify(intent, directive)
    assert ok2 is False and reason2 == "REPLAY"


def test_commit_refuses_a_mission_whose_root_vanished(tmp_path):
    """A stale project root must not be launched against silently.

    The project row names a directory that is gone. The preview must say so and
    the commit must REFUSE -- without spawning -- rather than starting a child
    with a cwd that does not exist (which Windows would silently upgrade to the
    parent, running the workload in the wrong tree).
    """
    import sqlite3
    env = _make(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "domain.db"))
    conn.execute("UPDATE projects SET root_path=? WHERE project_id=?",
                 (str(tmp_path / "gone"), env.project.project_id))
    conn.commit()
    conn.close()

    preview = env.ctl.request(env.mission.mission_id, commit=False)
    codes = [p["code"] for p in preview["preview"]["problems"]]
    assert "PROJECT_ROOT_MISSING" in codes
    assert preview["would_accept"] is False

    applied = env.ctl.request(env.mission.mission_id, commit=True)
    assert applied["spawned"] is False
    assert applied["classification"] == "REFUSED"
    assert applied["reason"] == "PROJECT_ROOT_MISSING"
    assert env.runtime.calls == []


# =========================================================================== #
# 5. a real child: it starts, and its OWN stream decides the outcome
# =========================================================================== #
def test_commit_spawns_classifies_and_ingests_a_real_session(tmp_path):
    env = _make(tmp_path)
    applied = env.ctl.request(env.mission.mission_id, commit=True)
    assert applied["spawned"] is True
    assert isinstance(applied["pid"], int) and applied["pid"] > 0
    assert applied["classification"] == "LAUNCHED"
    assert applied["stream"]["event_count"] == 3
    assert applied["stream"]["event_types"] == ["system", "assistant", "result"]
    assert applied["stream"]["exited"] is True
    assert applied["stream"]["exit_code"] == 0
    assert applied["stream"]["unparseable_lines"] == 0
    # The session id came from the CHILD, not from the mission we passed.
    assert applied["aep"]["session_discovered"] is True
    assert applied["aep"]["session_id"] == "sess-unit-1"
    assert len(env.runtime.calls) == 1
    assert env.runtime.calls[0][1]["sessionId"] == "sess-unit-1"
    assert applied["aep"]["ingested"] is True
    # Process ownership was reported, not assumed.
    assert applied["stream"]["job_object"].startswith(("assigned", "unavailable:"))


def test_the_mandated_step_order_appears_exactly_once(tmp_path):
    env = _make(tmp_path)
    applied = env.ctl.request(env.mission.mission_id, commit=True)
    names = [s["step"] for s in applied["steps"]]
    assert names.count(LC.STEP_REQUESTED) == 1
    idx = {n: names.index(n) for n in
           (LC.STEP_REQUESTED, LC.STEP_ACCEPTED, LC.STEP_PROCESS_STARTED,
            LC.STEP_NATIVE_SESSION, LC.STEP_AEP_FIRST) if n in names}
    assert idx[LC.STEP_REQUESTED] < idx[LC.STEP_ACCEPTED] < idx[LC.STEP_PROCESS_STARTED]
    assert idx[LC.STEP_PROCESS_STARTED] < idx[LC.STEP_NATIVE_SESSION]
    assert idx[LC.STEP_NATIVE_SESSION] < idx[LC.STEP_AEP_FIRST]
    assert all(s["ok"] for s in applied["steps"])


def test_auth_failure_never_walks_the_mission_to_running(tmp_path):
    """The load-bearing honesty test.

    The child DID run and DID report a session id -- so ``runtime.launch`` would
    happily walk the mission to RUNNING. It must not be called: authentication
    was refused, no work happened, and a mission shown RUNNING here is the exact
    lie FINAL != COMPLETE exists to catch.
    """
    env = _make(tmp_path, tid=_AUTH_TID, script=_AUTH_SCRIPT)
    applied = env.ctl.request(env.mission.mission_id, commit=True)
    assert applied["spawned"] is True
    assert applied["classification"] == "AUTH_REQUIRED"
    assert applied["stream"]["auth_error"] == "Authentication required"
    # The session genuinely existed...
    assert applied["aep"]["session_discovered"] is True
    assert applied["aep"]["session_id"] == "sess-auth-1"
    # ...and was still NOT ingested, because no work happened.
    assert applied["aep"]["ingested"] is False
    assert env.runtime.calls == []
    assert env.domain.get_mission(env.mission.mission_id).status.value == "DRAFT"
    aep_step = next(s for s in applied["steps"] if s["step"] == LC.STEP_AEP_FIRST)
    assert aep_step["ok"] is False and "AUTH_REQUIRED" in aep_step["detail"]
    # The blocker is a HUMAN task, recorded on the mission.
    approvals = env.domain.list_approvals(mission_id=env.mission.mission_id)
    assert len(approvals) == 1
    assert _field(approvals[0], "kind") == LC.APPROVAL_HOST_ACTIVATION
    assert "Authentication required" in str(_field(approvals[0], "reason"))


def test_a_run_with_no_session_id_fabricates_no_event(tmp_path):
    env = _make(tmp_path, tid=_NOSID_TID, script=_NO_SESSION_SCRIPT)
    applied = env.ctl.request(env.mission.mission_id, commit=True)
    assert applied["classification"] == "LAUNCHED"        # a model turn happened
    assert applied["aep"]["session_discovered"] is False  # but no identity exists
    assert applied["aep"]["session_id"] is None
    assert applied["aep"]["ingested"] is False
    assert env.runtime.calls == []
    step = next(s for s in applied["steps"] if s["step"] == LC.STEP_NATIVE_SESSION)
    assert step["ok"] is False and "no AEP event is fabricated" in step["detail"]


def test_a_silent_child_is_started_not_launched(tmp_path):
    env = _make(tmp_path, tid=_SILENT_TID, script=_SILENT_SCRIPT)
    applied = env.ctl.request(env.mission.mission_id, commit=True)
    assert applied["spawned"] is True
    assert applied["classification"] == "STARTED_NO_TELEMETRY"
    assert applied["stream"]["event_count"] == 0
    assert env.runtime.calls == []


# =========================================================================== #
# 6. capability is a MEASUREMENT
# =========================================================================== #
def test_capability_is_unknown_until_it_is_measured(tmp_path):
    env = _make(tmp_path)
    cap = env.ctl.capability("workbuddy")
    assert cap["executability"] == LC.EXEC_LOCAL_SPAWN_READY
    assert cap["spawn_ready"] is True
    assert cap["spawn_problem"] is None
    assert cap["end_to_end_verified"] is False     # nothing has run yet
    assert cap["last_measured"] is None
    assert cap["autonomous_actuation"] is False
    assert cap["capability"] == LC.CAPABILITY_USER_LAUNCH


def test_capability_records_a_real_run_and_flags_host_activation(tmp_path):
    ok_env = _make(tmp_path)
    ok_env.ctl.request(ok_env.mission.mission_id, commit=True)
    cap = ok_env.ctl.capability("workbuddy")
    assert cap["end_to_end_verified"] is True
    assert cap["last_measured"]["classification"] == "LAUNCHED"
    assert cap["last_measured"]["session_discovered"] is True
    assert cap["executability"] == LC.EXEC_LOCAL_SPAWN_READY

    auth_env = _make(tmp_path / "auth", tid=_AUTH_TID, script=_AUTH_SCRIPT)
    auth_env.ctl.request(auth_env.mission.mission_id, commit=True)
    cap2 = auth_env.ctl.capability("workbuddy")
    assert cap2["executability"] == LC.EXEC_HOST_ACTIVATION_PENDING
    assert cap2["end_to_end_verified"] is False
    assert cap2["last_measured"]["auth_error"] == "Authentication required"


def test_capability_is_unknown_when_no_template_can_be_resolved(tmp_path):
    env = _make(tmp_path)
    tpl = LaunchTemplate(template_id="ghost-v1", agent="workbuddy",
                         launch_kind="direct", executable=str(tmp_path / "nope.exe"))
    signer = DirectiveSigner(KEY, form=SIGNING_FORM_V2)
    launcher = HostLauncher(signer=signer, templates={"ghost-v1": tpl})
    ctl = LC.LaunchController(env.domain, _StubRuntime(), signer=signer, launcher=launcher)
    cap = ctl.capability("workbuddy")
    assert cap["spawn_ready"] is False
    assert cap["spawn_problem"] == "EXECUTABLE_NOT_FOUND"
    assert cap["executability"] == LC.EXEC_UNKNOWN      # never green
    assert cap["end_to_end_verified"] is False


# =========================================================================== #
# 7. the journal is a real, durable, secret-free record
# =========================================================================== #
def test_journal_records_the_attempt_without_the_goal(tmp_path):
    env = _make(tmp_path)
    env.ctl.request(env.mission.mission_id, commit=True)
    lines = [json.loads(x) for x in
             (tmp_path / "journal.ndjson").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines, "journal is empty"
    events = [x["event"] for x in lines]
    assert LC.STEP_REQUESTED in events
    assert any(e.startswith("STREAM_CLASSIFIED") or e == "STREAM_CLASSIFIED" for e in events)
    blob = json.dumps(lines)
    assert "say hi" not in blob          # the operator's content stays out
    assert "signature" not in blob        # no token material either
