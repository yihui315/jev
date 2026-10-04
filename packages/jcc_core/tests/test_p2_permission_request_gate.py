"""P2's PermissionRequest guard must survive `python -O` (assert stripping).

THE DEFECT
----------
``integrations/_common/jcc_settings.install`` gated P2 with an ``assert``::

    assert not (FORBIDDEN_EVENTS & set(events)), "P2 forbids PermissionRequest hooking"

``python -O`` strips every ``assert``. This module is reachable from installer
and integrator entrypoints, so a release gate implemented that way is not a gate.
Measured on this host before the fix, with a real agent whose event list had been
made to include ``PermissionRequest``::

    python     -> AssertionError: P2 forbids PermissionRequest hooking
    python -O  -> NO EXCEPTION (gate did not hold)

WHY A SUBPROCESS IS REQUIRED, AND WHY THIS FILE EXISTS SEPARATELY
----------------------------------------------------------------
``-O`` is a flag of the INTERPRETER, not of a function. It cannot be turned on
for one call inside a running process, and it cannot be turned off for a
``pytest`` run that did not start with it. So a test that claims to check the
optimised behaviour without a child process is checking nothing: importing the
module normally and calling ``install`` would pass no matter what the source
says. Hence one child per mode, and the assertion is made on the child's
OBSERVED result rather than on the source text.

The existing ``test_permission_request_never_hooked`` in
``test_installer_roundtrip.py`` does NOT cover this: it asserts the forbidden
event is absent from the OUTPUT, which passed under ``-O`` too -- because the
install loop also skips forbidden events, the bypass was silent rather than
loud. This file pins the guard itself, in both interpreter modes.

FALSIFIER. Revert the ``raise ValueError`` in ``jcc_settings.install`` back to an
``assert`` and the ``-O`` child reports ``GATE_HELD=False`` and this file goes
red. A gate test that cannot fail is the thing it is meant to prevent.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PY = ROOT / "integrations" / "_common" / "jcc_settings.py"

#: The message is part of the contract: the fix preserved it verbatim, and
#: anything matching on it (integrator error handling, logs, other tests)
#: depends on it. So it is asserted literally rather than by substring.
EXPECTED_MESSAGE = "P2 forbids PermissionRequest hooking"

#: Run as a child: see the module docstring. Kept as a function so the probe is
#: a plain script with no pytest dependency, so the child measures the MODULE's
#: behaviour and not the test harness'.
_CHILD_PROBE = r'''
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import jcc_settings as js

# A REAL agent (so the command-template guard is passed) whose event list has
# been made to include a forbidden event. The shipped TELEMETRY_EVENTS
# correctly excludes PermissionRequest; this stands in for a future edit, a
# config merge, or a bad patch that does not.
js.TELEMETRY_EVENTS = {"workbuddy": ["SessionStart", "SessionEnd",
                                     "PermissionRequest"]}

target = Path(tempfile.mkdtemp(prefix="p2_gate_")) / "settings.json"
target.write_text("{}", encoding="utf-8")

raised, message = False, ""
try:
    js.install("workbuddy", target, jcc_root=sys.argv[2], command="echo hi")
except ValueError as exc:
    raised, message = True, str(exc)
except AssertionError as exc:
    # A plain-mode assert still refuses; the MESSAGE is what callers may match.
    raised, message = True, str(exc)
except Exception as exc:
    message = "UNEXPECTED:%s:%s" % (type(exc).__name__, exc)

written = None
try:
    data = json.loads(target.read_text(encoding="utf-8"))
    for ev, entries in (data.get("hooks") or {}).items():
        for entry in entries or []:
            if "jcc" in json.dumps(entry).lower():
                written = ev
except Exception as exc:
    written = "read-failed:%s" % exc

print(json.dumps({"optimized": not __debug__, "raised": raised,
                  "message": message, "forbidden_hook_written": written}))
'''


def _run_child(mode: str) -> dict:
    """Run the probe under ``mode`` (``""`` or ``"-O"``) and return its report."""
    env = {"PYTHONPATH": str(ROOT / "packages"), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, mode, "-c", _CHILD_PROBE,
         str(SETTINGS_PY.parent), str(ROOT)] if mode else
        [sys.executable, "-c", _CHILD_PROBE,
         str(SETTINGS_PY.parent), str(ROOT)],
        cwd=str(ROOT), env=env, text=True, errors="replace", timeout=180,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    payload = [ln for ln in (proc.stdout or "").splitlines()
               if ln.strip().startswith("{")]
    assert payload, (
        "probe produced no report (rc=%s)\nstdout=%s\nstderr=%s"
        % (proc.returncode, (proc.stdout or "")[:800],
           (proc.stderr or "")[:800]))
    return json.loads(payload[-1])


# --------------------------------------------------------------------------- #
# 1. THE CRITERION: the gate holds with asserts stripped
# --------------------------------------------------------------------------- #
def test_permission_request_gate_holds_under_python_O():
    """``-O`` must still refuse. This is the whole point of the P2 fix.

    Falsifier: restore the ``assert`` in ``jcc_settings.install`` and
    ``GATE_HELD`` becomes False here (the child reports
    ``UNEXPECTED:...``/no-raise), so this test goes red.
    """
    r = _run_child("-O")
    assert r["optimized"] is True, "the -O child was not actually optimised"
    gate_held = r["raised"] and EXPECTED_MESSAGE in r["message"]
    assert gate_held, (
        "GATE DID NOT HOLD under -O: the child did not refuse the forbidden "
        "event (raised=%r message=%r). An assert-based gate is stripped by -O, "
        "so a P2 release gate written that way is not a gate."
        % (r["raised"], r["message"]))
    assert r["forbidden_hook_written"] is None, (
        "a PermissionRequest hook reached the settings file under -O: %r"
        % r["forbidden_hook_written"])


def test_plain_mode_still_refuses_with_the_same_message():
    """The unoptimised path must be unchanged in behaviour AND in message.

    Pinned because the fix replaced an ``AssertionError`` with a
    ``ValueError``. A caller catching ``AssertionError`` specifically would now
    miss it -- which is a real, intended consequence of making the gate real, and
    it is why the MESSAGE is asserted rather than the type.
    """
    r = _run_child("")
    assert r["optimized"] is False, "the plain child was unexpectedly optimised"
    assert r["raised"] and EXPECTED_MESSAGE in r["message"], (
        "plain mode must refuse with the original message; got raised=%r "
        "message=%r" % (r["raised"], r["message"]))
    assert r["forbidden_hook_written"] is None


@pytest.mark.parametrize("mode", ["-O", "-OO"])
def test_both_optimised_modes_refuse(mode):
    """``-OO`` strips docstrings as well; the gate must not depend on either.

    Cheap to add and it closes the obvious "someone will try the other flag"
    path. ``-O`` and ``-OO`` differ only in docstrings, so a gate implemented
    with an assert passes neither once compiled with either.
    """
    r = _run_child(mode)
    assert r["optimized"] is True
    assert r["raised"] and EXPECTED_MESSAGE in r["message"], (
        "%s: gate did not hold (raised=%r message=%r)"
        % (mode, r["raised"], r["message"]))


# --------------------------------------------------------------------------- #
# 2. the source itself carries no assert, so the gate cannot be re-stripped
# --------------------------------------------------------------------------- #
def test_the_guard_is_a_raise_not_an_assert():
    """Static pin: ``install`` must contain no bare ``assert``.

    The behavioural cases above are the real evidence, but they only cover the
    two flags someone remembered to run. This pins the SOURCE so the obvious
    regression -- someone tidying the line back into an ``assert`` because it
    reads better -- is caught without waiting for an optimised run to fail.
    Checked on the AST so a mention in a comment or docstring cannot satisfy it.
    """
    import ast

    tree = ast.parse(SETTINGS_PY.read_text(encoding="utf-8"))
    asserts = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    assert not asserts, (
        "%s carries %d bare assert(s) at line(s) %s. Any of them is a control "
        "that `python -O` deletes. This file's other cases prove the P2 gate "
        "behaves correctly under -O, but they cannot stop the next assert from "
        "being introduced." % (SETTINGS_PY.name, len(asserts), asserts))


def test_the_uninstall_skip_branch_is_still_a_continue():
    """The OTHER forbidden-event branch must stay a ``continue``, not a raise.

    The install loop skips forbidden events rather than raising, and that is
    correct and load-bearing: the loop must stay able to install the PERMITTED
    events even when a forbidden one is present in the table, so a future table
    edit degrades to "the forbidden event is not hooked" instead of "the whole
    install fails". It is one line away from the guard that was just hardened,
    and the two have OPPOSITE intents, so the distinction is pinned here.

    Matched on the AST, not on the text, and the AST shape is asserted rather
    than assumed. Two earlier forms of this test were wrong: a literal
    ``"if ev in FORBIDDEN_EVENTS:\\n        continue"`` encoded an indentation
    the loop does not have, and the first AST attempt read the comparator as a
    subscript. The real node is ``ev in FORBIDDEN_EVENTS`` -- a ``Name`` compared
    against a ``Name``, i.e. ``comparators[0]`` is the SET, not a string. A test
    that fails because it guessed an AST shape is a test that gets "fixed" by
    loosening the assertion, so the shape is stated explicitly here.
    """
    import ast

    tree = ast.parse(SETTINGS_PY.read_text(encoding="utf-8"))
    skips = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare)
        and isinstance(n.test.left, ast.Name)
        and n.test.left.id == "ev"
        # `ev in FORBIDDEN_EVENTS` -- the set is the comparator itself.
        and len(n.test.comparators) == 1
        and isinstance(n.test.comparators[0], ast.Name)
        and n.test.comparators[0].id == "FORBIDDEN_EVENTS"
        and len(n.test.ops) == 1
        and isinstance(n.test.ops[0], ast.In)
        and len(n.body) == 1
        and isinstance(n.body[0], ast.Continue)
    ]
    assert skips, (
        "the install loop's `if ev in FORBIDDEN_EVENTS: continue` branch is gone "
        "or was changed. Do NOT turn it into a raise: skipping is the correct "
        "semantic there (install the permitted events, skip the forbidden one), "
        "and converting it would make any table edit fail the whole install "
        "instead of merely not hooking the event.")


def test_no_forbidden_event_in_the_shipped_tables():
    """The shipped event tables must not contain a forbidden event at all.

    The raise is the backstop, not the mechanism: the primary control is that
    ``TELEMETRY_EVENTS`` simply never lists ``PermissionRequest``. This asserts
    that directly, so a table edit that added the event is caught as a table
    problem rather than only as a gate firing.
    """
    sys.path.insert(0, str(SETTINGS_PY.parent))
    spec_path = SETTINGS_PY
    import importlib.util

    spec = importlib.util.spec_from_file_location("jcc_settings_tbl", spec_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jcc_settings_tbl"] = mod
    try:
        spec.loader.exec_module(mod)
        for agent, events in mod.TELEMETRY_EVENTS.items():
            overlap = mod.FORBIDDEN_EVENTS & set(events)
            assert not overlap, (
                "TELEMETRY_EVENTS[%r] lists forbidden event(s) %s; P2 forbids "
                "PermissionRequest hooking" % (agent, sorted(overlap)))
    finally:
        sys.modules.pop("jcc_settings_tbl", None)
