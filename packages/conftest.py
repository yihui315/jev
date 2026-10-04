"""Test-infra: keep pytest scratch inside the project and off the user profile.

Two sandbox / user-profile hazards are neutralised here:

1. Base temp dir. The default pytest basetemp lives under the system TEMP dir
   (pytest-of-<user>); pytest prunes the previous run's numbered dirs there by
   bulk-deleting them. In this sandbox a >50-file bulk delete trips the
   SAFE_DELETE guard, which raises after the run, making pytest exit 1 even when
   every test passed. ``pytest_configure`` redirects basetemp into a
   single, stable directory *inside the OS temp dir*.

   The location is NOT optional and NOT cosmetic -- it is load-bearing:
   the safe-delete shim (``sitecustomize.py``) short-circuits every delete that
   resolves under ``tempfile.gettempdir()`` (see ``_is_under_os_tmp_dir`` /
   ``_should_bypass_safe_delete``) and only routes *other* paths through the
   bulk-delete guard helper. A basetemp anywhere else -- including the
   project tree and including ``C:\\Windows\\TEMP`` on hosts where
   ``tempfile.gettempdir()`` is the per-user ``AppData\\Local\\Temp`` -- is
   guarded, so pytest's scratch cleanup aborts the run with
   ``SAFE_DELETE_BULK_CONFIRM_REQUIRED`` (observed: ``count:171,
   threshold:50``) and every ``tmp_path`` fixture errors out.

   The target is therefore derived from ``tempfile.gettempdir()`` at runtime
   rather than hard-coded, so it stays correct when TEMP/TMP differ per host or
   per shell.

   It is also PER-PROCESS (CR-H1). It used to be one fixed ``jev-pytest``
   directory for the whole machine, but pytest ``rm_rf``s a basetemp on entry,
   so two concurrent runs deleted each other's scratch: measured 3 of 5
   concurrent pairs on this host had a non-zero leg, the loser erroring during
   SETUP. Each process now gets ``jev-pytest<pid>-<uuid8>`` under the same
   parent, and removes only its own on exit. The rule itself lives in
   ``jcc_core.pytest_basetemp`` so this file and ``tools/conftest.py`` cannot
   drift apart again.

2. TEST-NUMBER PRECONDITION (lead ruling 2026-09-25). The suite's pass/fail
   numbers are NOT a property of the code under test: with the machine's
   safe-delete guard ENABLED, the same tree measures ``2 failed, 339 passed,
   11 errors`` and with it disabled ``2 failed, 350 passed``. The erroring
   tests are ones whose ``tmp_path`` factory is interrupted by the shim's
   bulk-delete guard (``SAFE_DELETE_BULK_CONFIRM_REQUIRED``) -- the test is
   never entered, so "error" here means "the environment could not provide
   scratch", not "the product is broken".

   So the precondition is SELF-PROVING, at COLLECTION time: the tests that the
   guard measurably breaks are SKIPPED with a loud warning rather than being
   reported as errors, and the list below is the measured set -- extend it when
   a new one appears. It is deliberately NOT "skip the whole suite": a wall of
   skips reads like a pass and would take the A8 artifact pins down with it.

   The direction is now FAIL-CLOSED ON THE RUN AS A WHOLE (lead ruling
   2026-09-25, replacing the earlier fail-informational): a guarded environment
   no longer produces a "26 skipped, exit 0" that reads like a pass. At
   COLLECTION the guarded configuration raises ``pytest.UsageError``, which
   exits 4 -- a usage error is a statement about the invocation, not about the
   product, and it cannot be mistaken for a test result. ``26 skipped / exit 0``
   was exactly the false PASS the project already has too many of.

   ``JCC_PYTEST_ALLOW_DIRTY_ENV=1`` remains the deliberate opt-in for someone
   who wants the raw errors; it keeps the skips off and prints the flagged set
   to the terminal in its own block, so the numbers are not hidden inside a
   warning summary.

3. Spool dir. ``jcc_emit.spool()`` defaults to ``~/.jcc/spool``
   (``JCC_SPOOL_DIR`` unset) and writes there whenever delivery fails -- which
   the P2 failure-path tests deliberately trigger. An autouse, session-scoped
   fixture points ``JCC_SPOOL_DIR`` at a temp dir for the whole session so tests
   never touch the user's real profile; subprocesses inherit ``os.environ``, so
   the override propagates to spawned jcc_emit.py / jcc-codex-notify.py too.
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

#: Reporting precondition, see item 2 of the module docstring.
SAFE_DELETE_PRECONDITION = (
    "CODEBUDDY_SAFE_DELETE_ENABLED=0 is a REPORTING precondition, not a product "
    "one: with the machine's safe-delete guard on, pytest's own tmp_path churn "
    "trips the bulk-delete guard and the tests in the measured set ERROR during "
    "SETUP -- the test is never entered, so 'error' here means the environment "
    "could not provide scratch, NOT that the product is broken. Measured on this "
    "tree: guard on = '11 errors', guard off = clean. This run aborts with a "
    "usage error (exit 4) because it collects tests from that measured set. "
    "Re-run with CODEBUDDY_SAFE_DELETE_ENABLED=0; JCC_PYTEST_ALLOW_DIRTY_ENV=1 "
    "deliberately overrides this and reports the raw errors instead."
)

#: Shown when someone opts out of the precondition on purpose; the count and the
#: flagged set are reported separately in the terminal, not folded into the
#: warning summary.
SAFE_DELETE_PRECONDITION_DIRTY = (
    "Opting OUT of the CODEBUDDY_SAFE_DELETE_ENABLED=0 reporting precondition "
    "(JCC_PYTEST_ALLOW_DIRTY_ENV=1). The tests in the measured set below will "
    "ERROR because the safe-delete guard interrupts their tmp_path factory -- "
    "that is an environment failure, not a product verdict. Treat the resulting "
    "numbers as un-comparable with the recorded ones."
)

#: Shown when the guard is on but this run collects none of the measured tests.
SAFE_DELETE_PRECONDITION_INAPPLICABLE = (
    "CODEBUDDY_SAFE_DELETE_ENABLED is not '0' and this run collects none of the "
    "tests the safe-delete guard is measured to break, so the guard cannot have "
    "distorted these numbers: running it anyway. If tests ERROR, that is an "
    "environment failure to investigate, not a product verdict."
)

#: The measured set. Derived from guard-on runs of this tree (2026-09-25): these
#: are the tests that error at SETUP under the guard, i.e. that the environment
#: refuses to run -- not tests that assert anything about the product. (Named
#: for what they MEASURE, not for what the hook does to them: the guarded run
#: now aborts instead of skipping.)
_MEASURED_UNDER_SAFE_DELETE_GUARD = {
    ("test_installer_roundtrip.py", "test_settings_install_is_idempotent_and_reversible"),
    ("test_installer_roundtrip.py", "test_settings_backup_written_before_change"),
    ("test_installer_roundtrip.py", "test_permission_request_never_hooked"),
    ("test_installer_roundtrip.py", "test_codex_notify_restore_is_byte_exact"),
    ("test_installer_roundtrip.py", "test_codex_notify_uninstall_restores_original_exactly"),
    ("test_manual_actuation.py", "test_manual_path_dispatches_and_never_executes"),
    ("test_p3_core_integration.py", "test_assess_boundary_matrix"),
    ("test_p3_core_integration.py", "test_manual_endpoints_never_reach_actuator_execute"),
    ("test_server_surface.py", "test_the_manual_path_produces_no_execution_result"),
}


def _flagged(items):
    """The items the guard measurably breaks: i.e. the reported numbers."""
    out = []
    for item in items:
        module = Path(str(item.fspath)).name
        name = getattr(item, "originalname", item.name).split("[")[0]
        if (module, name) in _MEASURED_UNDER_SAFE_DELETE_GUARD:
            out.append(item)
    return out


def pytest_collection_modifyitems(config, items) -> None:
    """Self-proving precondition check, run at COLLECTION -- see docstring 2.

    Guarded environment and a run that collects at least one of the measured
    tests => ``pytest.UsageError`` (exit 4). Not a skip: the skip made the same
    tree report "green" while 26 tests never ran. A guarded run that collects
    NONE of the measured tests is left alone -- otherwise the guard would abort
    any single-file run on a guarded box, which is a false alarm.
    """
    if os.environ.get("CODEBUDDY_SAFE_DELETE_ENABLED", "1") == "0":
        return  # the reporting configuration: nothing to correct
    flagged = _flagged(items)
    if os.environ.get("JCC_PYTEST_ALLOW_DIRTY_ENV") == "1":
        # explicit opt-in: keep the raw errors, but own them out loud
        warnings.warn(SAFE_DELETE_PRECONDITION_DIRTY, stacklevel=2)
        # name changed in pytest 8; support either without guessing a minimum
        make = getattr(config, "get_terminal_writer", None) or config.getterminalwriter
        tw = make()
        tw.line()
        tw.sep("!", "JCC_PYTEST_ALLOW_DIRTY_ENV=1 -- RUNNING WITHOUT THE REPORTING "
                    "PRECONDITION")
        tw.line(f"CODEBUDDY_SAFE_DELETE_ENABLED is NOT '0'; {len(flagged)} test(s) "
                "of the collected ones are in the measured set that the "
                "safe-delete guard breaks"
                + (":" if flagged else
                   " -- none here, so the guard is not expected to distort these "
                   "numbers, but they still are not the recorded configuration."))
        for item in flagged:
            tw.line(f"  - {Path(str(item.fspath)).name}::{item.name}")
        tw.line("Numbers from THIS run are not comparable with the recorded ones.")
        tw.line()
        return
    if not flagged:
        # Nothing collected belongs to the measured set, so -- by measurement --
        # the guard cannot have broken this particular run. Aborting here would
        # be a false alarm that blocks e.g. running one file on a guarded box.
        warnings.warn(SAFE_DELETE_PRECONDITION_INAPPLICABLE, stacklevel=2)
        return
    raise pytest.UsageError(
        SAFE_DELETE_PRECONDITION + f"\n\n{len(flagged)} of the collected tests are "
        "in the measured set that this environment cannot run:\n"
        + "\n".join(f"  - {Path(str(item.fspath)).name}::{item.name}"
                    for item in flagged)
        + "\n\nRe-run with CODEBUDDY_SAFE_DELETE_ENABLED=0. "
        "JCC_PYTEST_ALLOW_DIRTY_ENV=1 deliberately overrides this and reports the "
        "raw errors instead."
    )


def pytest_configure(config) -> None:
    # CR-H1: the basetemp is PER-PROCESS. This used to be a single fixed path
    # (``gettempdir()/jev-pytest``), which pytest rm_rf's on entry -- so two
    # concurrent runs deleted each other's scratch. Measured on this host before
    # the fix: 3 of 5 concurrent pairs had a non-zero leg, the loser erroring
    # during SETUP. The derivation, the shim requirement and the --basetemp
    # precedence all now live in one place (jcc_core.pytest_basetemp) because
    # this file and tools/conftest.py previously carried duplicate copies.
    from jcc_core.pytest_basetemp import configure_basetemp

    configure_basetemp(config)


@pytest.fixture(autouse=True, scope="session")
def _isolate_jcc_spool(tmp_path_factory):
    """Point JCC_SPOOL_DIR at a per-session temp dir, restoring it on teardown.

    JCC_ROOT is intentionally NOT redirected: jcc_emit uses JCC_ROOT only to
    locate the ``packages`` dir for sys.path (its default, F:/jev, is already
    correct) and never writes to it -- redirecting it would break imports. Only
    the spool dir is a write target and needs isolation.
    """
    previous = os.environ.get("JCC_SPOOL_DIR")
    spool_dir = tmp_path_factory.mktemp("jcc-spool")
    os.environ["JCC_SPOOL_DIR"] = str(spool_dir)
    try:
        yield spool_dir
    finally:
        if previous is None:
            os.environ.pop("JCC_SPOOL_DIR", None)
        else:
            os.environ["JCC_SPOOL_DIR"] = previous
