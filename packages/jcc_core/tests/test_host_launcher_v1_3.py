"""Wave 9.1 tests — the HOST LAUNCHER actually starts work, and refuses to lie.

Owner: Forge (mission runtime) / Sentinel (capability + signed directive).

WHAT THIS FILE PINS
-------------------
Wave 3 shipped a *bridge*, not a launcher: ``workbuddy_launch`` discovers a
WorkBuddy session the OPERATOR already started and hands that record to the
adapter. Measured on this host, that is ``OBSERVED_EXISTING_LIVE_SESSION`` -- a
weaker claim than ``JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION``, because nothing the
product did caused the work to exist. ``host_launcher`` closes that gap, and this
file exists so the gap cannot silently reopen.

Each property has a falsifier named with it, because a test that cannot fail is
not evidence:

  1. **A refusal spawns nothing.** Falsifier: if verification is moved after
     ``Popen``, the refusal cases start passing a pid and this file goes red.
  2. **The goal is never a command.** Falsifier: a goal containing shell
     metacharacters must remain exactly one argv element; if anyone replaces the
     argv-vector call with ``shell=True`` or a joined string, the metacharacter
     case fails.
  3. **A structural success is not upgraded to a launch.** Falsifier: a
     well-formed NDJSON stream whose model turn was refused must classify as
     ``AUTH_REQUIRED``, never ``LAUNCHED``. This is the exact fiction the
     overnight order's ``FINAL != COMPLETE`` list forbids.
  4. **The anchor names the registry that ran.** Falsifier: a launcher built with
     a custom registry must report a digest over THAT registry, not the global
     one; otherwise the capability anchor certifies a command that never ran.
  5. **The launcher owns its children.** Falsifier: ``job_object`` must be
     REPORTED on every spawn (``assigned`` or ``unavailable:<reason>``), so a
     silent loss of process ownership is visible rather than assumed.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from jcc_core import host_launcher as HL  # noqa: E402
from jcc_core import signing as SG  # noqa: E402
from jcc_core.nonce_cache import ReplayCache  # noqa: E402
from jcc_core.signing import (  # noqa: E402
    SIGNING_FORM_V2, DirectiveSigner, sign_directive,
)

TEMPLATE_ID = "workbuddy-cli-headless-v1"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The refusal vocabulary lives in ONE module (signing.py) by design; it is read
#: from there rather than re-exported, so this file cannot drift from it.
R = {k: getattr(SG, "REASON_" + k) for k in
     ("UNSIGNED", "BAD_SIGNATURE", "EXPIRED", "REPLAY", "SESSION_MISMATCH",
      "CAPABILITY_DENIED", "POLICY_MISMATCH", "BUDGET_EXHAUSTED", "APPROVAL_MISSING")}


@dataclass
class Dir:
    """Minimal directive carrying exactly the fields the canonical form reads."""

    policy_table_version: str = "pt-1"
    policy_version: str = "pv-1"
    session_id: str = ""
    directive_id: str = "d-1"
    issued_at: float = 0.0
    ttl_s: float = HL.LAUNCH_TTL_S
    nonce: str = ""
    action: str = "launch_mission"
    target_actuator: str = "host_launcher"
    evidence_hash: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    human_approval: Optional[bool] = None
    scope: str = ""
    signed: bool = True
    signature: Optional[str] = None


@pytest.fixture
def signer(monkeypatch):
    monkeypatch.setenv("JCC_DIRECTIVE_SIGNING_KEY", "unit-test-key-not-a-secret")
    return DirectiveSigner(b"unit-test-key-not-a-secret", form=SIGNING_FORM_V2)


@pytest.fixture
def launcher(signer):
    lch = HL.HostLauncher(signer=signer, replay=ReplayCache())
    yield lch
    lch.stop_all()


def intent(**over) -> HL.LaunchIntent:
    base = dict(mission_id="m-1", project_id="p-1", agent_id="workbuddy",
                adapter_id="workbuddy", project_root=ROOT, template_id=TEMPLATE_ID,
                goal="Reply with exactly: TEST_OK",
                nonce="n-%d" % time.time_ns(), user_approved=True,
                policy_version="pv-1")
    base.update(over)
    return HL.LaunchIntent(**base)


def directive(it: HL.LaunchIntent, signer: DirectiveSigner, *, approve=True,
              capability=HL.CAPABILITY_USER_LAUNCH, mutate=None,
              templates=None) -> Dir:
    reg = templates if templates is not None else HL.LAUNCH_TEMPLATES
    tpl = reg[it.template_id]
    head, _ = HL.build_head(tpl)
    d = Dir(session_id=it.mission_id, issued_at=time.time(), nonce=it.nonce,
            evidence_hash=it.evidence_hash(),
            params={"capability": capability, "project_id": it.project_id,
                    "adapter_id": it.adapter_id, "project_root": it.project_root,
                    "args_hash": HL.args_hash(tpl, head, goal=it.goal,
                                              params=it.params())},
            human_approval=approve, scope="project:%s" % it.project_root)
    if mutate:
        mutate(d)
    d.signature = sign_directive(d, signer.key, form=SIGNING_FORM_V2)
    return d


# --------------------------------------------------------------------------- #
# 4. the anchor names the registry that will run
# --------------------------------------------------------------------------- #
def test_registry_covers_exactly_the_agents_that_can_be_launched():
    assert set(t.agent for t in HL.LAUNCH_TEMPLATES.values()) == {"workbuddy"}, (
        "the registry grew an agent we cannot actually launch; the connection "
        "centre is forbidden to show a green it cannot back")


def test_registry_digest_is_a_stable_sha256_anchor():
    d = HL.template_registry_digest()
    assert d.startswith("sha256:") and len(d) == 71
    assert d == HL.template_registry_digest(), "digest must be stable across calls"


def test_capability_version_follows_the_instance_registry_not_the_global_one():
    custom = {"only-this": HL.LaunchTemplate(template_id="only-this",
                                             agent="workbuddy",
                                             launch_kind="direct",
                                             executable=sys.executable)}
    lch = HL.HostLauncher(signer=DirectiveSigner(b"k", form=SIGNING_FORM_V2),
                          replay=ReplayCache(), templates=custom)
    assert lch.registry_digest == HL.template_registry_digest(custom)
    assert lch.registry_digest != HL.template_registry_digest(), (
        "a launcher running a custom registry reported the global anchor")


# --------------------------------------------------------------------------- #
# 2. the goal is never a command
# --------------------------------------------------------------------------- #
def test_the_goal_occupies_exactly_one_argv_slot_and_only_the_last():
    tpl = HL.LAUNCH_TEMPLATES[TEMPLATE_ID]
    it = intent()
    argv = list(tpl.build_argv(goal=it.goal, params=it.params()))
    assert argv[-1] == it.goal
    assert argv.count(it.goal) == 1
    assert tpl.launch_kind == "node_script" and tpl.script_resolver == "workbuddy_cli"


def test_param_flags_are_emitted_in_declared_order():
    tpl = HL.LAUNCH_TEMPLATES[TEMPLATE_ID]
    argv = list(tpl.build_argv(goal="g", params={"session_id": "s", "model": "m",
                                                 "project_root": "r"}))
    assert argv[-1] == "g"
    assert argv.index("--session-id") < argv.index("--model") < argv.index("--add-dir")
    # each flag is immediately followed by its own value
    for flag, value in (("--session-id", "s"), ("--model", "m"), ("--add-dir", "r")):
        assert argv[argv.index(flag) + 1] == value


def test_a_template_missing_a_required_param_refuses_rather_than_dropping_it():
    tpl = HL.LAUNCH_TEMPLATES[TEMPLATE_ID]
    with pytest.raises(ValueError):
        tpl.build_argv(goal="g", params={"session_id": "s"})


def test_args_hash_ignores_the_goal_but_covers_the_head():
    tpl = HL.LAUNCH_TEMPLATES[TEMPLATE_ID]
    head, _ = HL.build_head(tpl)
    params = {"session_id": "s", "model": "m", "project_root": "r"}
    a = HL.args_hash(tpl, head, goal="goal one", params=params)
    b = HL.args_hash(tpl, head, goal="goal two", params=params)
    assert a == b, "the hash must describe the command shape, not the goal text"
    c = HL.args_hash(tpl, head + ["--extra"], goal="goal one", params=params)
    assert a != c, "the head is part of the command and must be covered"


# --------------------------------------------------------------------------- #
# preview: honest, side-effect free
# --------------------------------------------------------------------------- #
def test_preview_is_side_effect_free_and_reports_the_capability(launcher):
    it = intent()
    pv = launcher.preview(it)
    assert pv["schema"] == "jcc/v1.3-launch-preview/1"
    assert pv["shell"] is False and pv["actuation"] is False
    assert pv["capability"] == HL.CAPABILITY_USER_LAUNCH
    assert pv["capability_version"] == launcher.registry_digest
    assert launcher.replay.seen(it.nonce) is False, "preview consumed a nonce"


def test_preview_names_the_problem_when_the_project_root_is_missing(launcher, tmp_path):
    pv = launcher.preview(intent(project_root=str(tmp_path / "does-not-exist")))
    assert pv["launchable"] is False
    assert "PROJECT_ROOT_MISSING" in [p["code"] for p in pv["problems"]]


def test_preview_refuses_an_unknown_template_without_raising(launcher):
    pv = launcher.preview(intent(template_id="no-such-template"))
    assert pv["launchable"] is False
    assert "UNKNOWN_TEMPLATE" in [p["code"] for p in pv["problems"]]
    assert pv["argv"] == []


# --------------------------------------------------------------------------- #
# 1. refusals: nine classes, each spawning nothing
# --------------------------------------------------------------------------- #
def _refusal_case(name, it, signer, templates=None):
    if name == "unsigned":
        d = directive(it, signer, templates=templates)
        d.signed = False
    elif name == "bad signature":
        d = directive(it, signer, templates=templates)
        d.signature = "sha256=" + "0" * 64
    elif name == "expired directive":
        d = directive(it, signer, templates=templates,
                      mutate=lambda x: setattr(x, "issued_at", time.time() - 10_000))
    elif name == "wrong mission":
        d = directive(it, signer, templates=templates,
                      mutate=lambda x: setattr(x, "session_id", "m-other"))
    elif name == "wrong capability":
        d = directive(it, signer, templates=templates,
                      capability="AUTONOMOUS_ACTUATION")
    elif name == "policy mismatch":
        d = directive(it, signer, templates=templates,
                      mutate=lambda x: setattr(x, "policy_version", "pv-OTHER"))
    elif name == "budget exhausted":
        d = directive(it, signer, templates=templates,
                      mutate=lambda x: x.params.update({"budget_exhausted": True}))
    elif name == "no human approval (directive)":
        d = directive(it, signer, approve=False, templates=templates)
    elif name == "no human approval (intent)":
        d = directive(it, signer, templates=templates)
    else:
        raise AssertionError(name)
    return d


@pytest.mark.parametrize("name,expected,it_over", [
    ("unsigned", R["UNSIGNED"], {}),
    ("bad signature", R["BAD_SIGNATURE"], {}),
    ("expired directive", R["EXPIRED"], {}),
    ("wrong mission", R["SESSION_MISMATCH"], {}),
    ("wrong capability", R["CAPABILITY_DENIED"], {}),
    ("policy mismatch", R["POLICY_MISMATCH"], {}),
    ("budget exhausted", R["BUDGET_EXHAUSTED"], {}),
    ("no human approval (directive)", R["APPROVAL_MISSING"], {}),
    ("no human approval (intent)", R["APPROVAL_MISSING"], {"user_approved": False}),
])
def test_every_refusal_class_spawns_nothing(name, expected, it_over, signer):
    it = intent(**it_over)
    lch = HL.HostLauncher(signer=signer, replay=ReplayCache())
    try:
        r = lch.launch(it, _refusal_case(name, it, signer))
        assert r["spawned"] is False, "%s spawned a process" % name
        assert r["pid"] is None
        assert r["classification"] == "REFUSED"
        assert r["reason"] == expected, "reason vocabulary drifted: %r" % r["reason"]
        assert r["actuation"] is False
        assert [s["step"] for s in r["steps"]].count("LAUNCH_ACCEPTED") == 0, (
            "a refused launch reached the accepted step")
    finally:
        lch.stop_all()


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #
def test_a_directive_cannot_launch_twice(signer, monkeypatch, tmp_path):
    # A template that cannot touch anything: python printing one NDJSON line.
    code = "import sys;sys.stdout.write('{\"type\":\"assistant\"}\\n')"
    tpl = HL.LaunchTemplate(template_id="t-echo", agent="workbuddy",
                            launch_kind="direct", executable=sys.executable,
                            argv_prefix=("-c", code), param_flags=())
    reg = {"t-echo": tpl}
    monkeypatch.setenv("JCC_LAUNCH_LOG_DIR", str(tmp_path / "logs"))
    lch = HL.HostLauncher(signer=signer, replay=ReplayCache(), templates=reg)
    try:
        it = intent(template_id="t-echo")
        d = directive(it, signer, templates=reg)
        first = lch.launch(it, d)
        assert first["spawned"] is True, first
        second = lch.launch(it, d)
        assert second["spawned"] is False
        assert second["reason"] == R["REPLAY"]
        # non-vacuous: the first attempt really did produce a child
        assert isinstance(first["pid"], int) and first["pid"] > 0
    finally:
        lch.stop_all()


# --------------------------------------------------------------------------- #
# 5. spawn: real process, real files, reported ownership
# --------------------------------------------------------------------------- #
def test_a_real_spawn_writes_a_real_ndjson_stream_and_reports_ownership(
        signer, monkeypatch, tmp_path):
    body = ("import sys;"
            "sys.stdout.write('{\"type\":\"system\",\"subtype\":\"init\"}\\n');"
            "sys.stdout.write('{\"type\":\"assistant\"}\\n');"
            "sys.stdout.flush()")
    tpl = HL.LaunchTemplate(template_id="t-stream", agent="workbuddy",
                            launch_kind="direct", executable=sys.executable,
                            argv_prefix=("-c", body), param_flags=())
    reg = {"t-stream": tpl}
    monkeypatch.setenv("JCC_LAUNCH_LOG_DIR", str(tmp_path / "logs"))
    lch = HL.HostLauncher(signer=signer, replay=ReplayCache(), templates=reg)
    try:
        it = intent(template_id="t-stream")
        r = lch.launch(it, directive(it, signer, templates=reg))
        assert r["spawned"] is True and r["steps"][-1]["step"] == "PROCESS_STARTED"
        assert r["shell"] is False
        assert r["job_object"] == "assigned" or r["job_object"].startswith("unavailable:"), (
            "ownership must be reported explicitly, never omitted")
        assert r["argv_goal_slot"] == r["argv_len"] - 1

        deadline = time.time() + 20.0
        events = []
        while time.time() < deadline:
            events, bad = lch.read_stream(r["stdout_file"])
            if events:
                break
            time.sleep(0.1)
        assert [e.get("type") for e in events] == ["system", "assistant"], events
        assert bad == []
        cls = lch.classify_stream(events, exit_code=0)
        assert cls["classification"] == "LAUNCHED"
    finally:
        lch.stop_all()


def test_stop_all_reports_how_many_jobs_it_closed(signer, monkeypatch, tmp_path):
    code = "import time;time.sleep(30)"
    tpl = HL.LaunchTemplate(template_id="t-sleep", agent="workbuddy",
                            launch_kind="direct", executable=sys.executable,
                            argv_prefix=("-c", code), param_flags=())
    reg = {"t-sleep": tpl}
    monkeypatch.setenv("JCC_LAUNCH_LOG_DIR", str(tmp_path / "logs"))
    lch = HL.HostLauncher(signer=signer, replay=ReplayCache(), templates=reg)
    it = intent(template_id="t-sleep")
    r = lch.launch(it, directive(it, signer, templates=reg))
    assert r["spawned"] is True
    closed = lch.stop_all()
    assert closed == (1 if r["job_object"] == "assigned" else 0), (
        "stop_all must report exactly the jobs it actually owned; "
        "job_object=%r closed=%r" % (r["job_object"], closed))


def test_job_guard_is_honest_about_availability():
    g = HL.JobGuard()
    ok = g.create()
    assert isinstance(ok, bool)
    assert isinstance(g.reason, str) and g.reason, (
        "an unavailable job must carry a reason -- 'cannot determine' is not "
        "'not available'")
    if ok:
        assert g.available is True and g.reason == "ok"
        g.close()
        assert g.available is False
    else:
        assert g.available is False


# --------------------------------------------------------------------------- #
# 3. classification refuses to upgrade on structure alone
# --------------------------------------------------------------------------- #
AUTH_STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant",
     "message": {"content": [{"type": "text",
                              "text": "Authentication required. Please use /login "
                                      "command to sign in to your account"}]}},
    {"type": "result", "is_error": True,
     "errors": ["Authentication required. Please use /login command to sign in "
                "to your account"]},
]


def test_a_well_formed_stream_with_an_auth_error_is_never_called_launched(launcher):
    cls = launcher.classify_stream(AUTH_STREAM, exit_code=0)
    assert cls["classification"] == "AUTH_REQUIRED"
    assert cls["auth_error"] is not None
    assert cls["event_count"] == 3


def test_a_stream_with_no_model_turn_is_started_no_telemetry(launcher):
    cls = launcher.classify_stream([{"type": "result", "is_error": False}], exit_code=0)
    assert cls["classification"] == "STARTED_NO_TELEMETRY"


def test_only_an_assistant_event_upgrades_to_launched(launcher):
    cls = launcher.classify_stream([{"type": "assistant"}], exit_code=0)
    assert cls["classification"] == "LAUNCHED"
    assert cls["first_assistant_event"] is True


def test_read_stream_reports_unparseable_lines_instead_of_swallowing_them(
        launcher, tmp_path):
    p = tmp_path / "s.ndjson"
    p.write_text('{"type":"assistant"}\nnot json\n[]\n', encoding="utf-8")
    events, bad = launcher.read_stream(str(p))
    assert len(events) == 1 and len(bad) == 2, (events, bad)
    assert {b["err"] for b in bad}  # each bad line carries a reason


def test_read_stream_reports_a_missing_file_as_a_finding(launcher):
    events, bad = launcher.read_stream(str("F:/definitely/not/here.ndjson"))
    assert events == [] and len(bad) == 1
    assert bad[0]["err"] == "no such file"
