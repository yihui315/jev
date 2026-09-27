"""§7G — cross-test environment isolation, and the falsifiers that keep it.

THE DEFECT THIS EXISTS TO PREVENT. §7's
``tools/v1_1_emit_real_jev_verification.py --check`` records credential PRESENCE
(``credential_evidence.env_presence``) and compares a fresh generation against the
committed artifact. Measured on this tree: the check is rc 0 against a clean
environment and rc 1 (STALE) the moment ANY of ``JCC_CONTROL_TOKEN``,
``JCC_DIRECTIVE_SIGNING_KEY`` or ``JEV_API_KEY`` is set -- because the artifact
records ABSENT and a fresh generation would record PRESENT (and a different
``length_bucket``). ``tests/e2e/conftest.py`` did exactly that: the
``InProcessServer`` constructor wrote two of those names into the process-global
``os.environ`` and nothing ever restored them, so in a single pytest process
every later test -- §7 included -- ran with a control token and a signing key
set, and §7 failed for a reason that had nothing to do with §7.

So §7 is a CANARY for process-global environment pollution, and these tests are
what keep the canary honest in both directions.

WHY EVERY PROPERTY HERE IS PAIRED. A test that only asserts "nothing changed"
passes on a broken instrument -- a check that never looks anywhere also reports
no change. So each property below has a POSITIVE CONTROL that must FAIL when the
defect is re-introduced, and both directions run through the SAME assertion
helper. That is what makes the control evidence about the CHECK rather than about
a separate piece of code that merely resembles it.

AND WHY §7 IS NOT ALLOWED TO BE WEAKENED INSTEAD. The obvious way to make a
polluted §7 pass is to teach the generator to ignore the environment. That is
forbidden: when a real Jev credential IS later provided, §7 must be able to
observe PRESENT. ``test_the_jev_check_still_observes_the_environment`` pins that
sensitivity, so "fix the pollution" can never quietly become "blind the canary".
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "tools", REPO / "packages"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

E2E_CONFTEST = REPO / "tests" / "e2e" / "conftest.py"
GEN_REL = "tools/v1_1_emit_real_jev_verification.py"
ARTIFACT_REL = "artifacts/release/V1_1_REAL_JEV_VERIFICATION.json"

#: The credential names §7 records presence for, as a LITERAL copy rather than an
#: import of the producer. Importing the list would let a shrunken list certify
#: its own shrinkage; ``test_the_watched_names_match_the_discovery_module``
#: reconciles the copy against the module AND against the committed artifact, so
#: drift is caught instead of assumed.
WATCHED_ENV = (
    "JEV_API_KEY",
    "TYPESAFE_API_KEY",
    "TYPESAFE_TOKEN",
    "TYPESAFE_API_TOKEN",
    "JEV_TOKEN",
    "JCC_CONTROL_TOKEN",
    "JCC_DIRECTIVE_SIGNING_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
)

#: The two names the e2e fixture's in-process server runs with.
E2E_SET_NAMES = ("JCC_CONTROL_TOKEN", "JCC_DIRECTIVE_SIGNING_KEY")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_by_path(path: Path, name: str):
    """Import a file under a PRIVATE module name.

    Deliberately not ``import conftest``: with no ``__init__.py`` under ``tests/``
    or ``tools/``, pytest imports every conftest as the bare name ``conftest`` and
    only the last one loaded owns it, so that name is not a stable handle. A
    private name also keeps this probe out of pytest's plugin registry, so the
    module's ``pytest_*`` hooks cannot be registered a second time.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: string annotations are resolved through sys.modules.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def _env_delta(before: dict, after: dict) -> str:
    added = sorted(k for k in after if k not in before)
    removed = sorted(k for k in before if k not in after)
    changed = sorted(k for k in after if k in before and after[k] != before[k])
    return "added=%r removed=%r changed=%r" % (added, removed, changed)


def _restore_env(before: dict) -> None:
    for key in [k for k in os.environ if k not in before]:
        os.environ.pop(key, None)
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


def _display_path(path: Path) -> str:
    """Repo-relative when inside the repo, absolute otherwise.

    ``relative_to`` RAISES for a path outside REPO -- and the positive control
    deliberately uses a tmp copy -- so an error-path formatter that itself throws
    would turn a *detected* defect into an unexplained crash.
    """
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _assert_import_leaves_the_environment_alone(path: Path, name: str) -> None:
    """THE assertion both the real case and the positive control go through."""
    before = dict(os.environ)
    _load_by_path(path, name)
    after = dict(os.environ)
    if before != after:
        raise AssertionError(
            "importing %s mutated the process environment (%s). A module loaded "
            "during collection must not change global state: in a single pytest "
            "process the change survives into every later test, and measured on "
            "this tree it makes §7's --check report STALE for a reason that has "
            "nothing to do with §7."
            % (_display_path(path), _env_delta(before, after)))


def _raw_fixture(fixture_obj):
    """The plain generator function behind ``@pytest.fixture``.

    pytest 9 wraps fixtures in ``FixtureFunctionDefinition``; the underlying
    function is ``__wrapped__`` (and ``_fixture_function``). Both are tried, and
    if neither is callable this RAISES -- a test that silently stopped driving
    the real fixture would be a vacuous test, which is the failure mode this whole
    file exists to prevent.
    """
    for attr in ("__wrapped__", "_fixture_function", "function"):
        fn = getattr(fixture_obj, attr, None)
        if callable(fn):
            return fn
    raise AssertionError(
        "cannot reach the function behind the fixture %r (type %s); this probe "
        "would be vacuous" % (fixture_obj, type(fixture_obj)))


def _run_jev_check(extra_env: "dict | None" = None) -> subprocess.CompletedProcess:
    """Run §7's own staleness check as a subprocess, inheriting this process env.

    Inheriting is the point: §7 is being used as a canary for process-global
    pollution, so the check has to see exactly what the pytest process sees.
    """
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(REPO / GEN_REL), "--check"],
        capture_output=True, text=True, cwd=str(REPO), env=env)


def _artifact() -> dict:
    return json.loads((REPO / ARTIFACT_REL).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# The watch-list cannot silently shrink
# --------------------------------------------------------------------------- #
def test_the_watched_names_match_the_discovery_module():
    """The literal copy above, the producer's list, and the artifact must agree.

    Three sources, all three compared: the copy is what the assertions below
    trust, the module is what actually runs, and the artifact is what a reader
    sees. Any one of them drifting means the canary is watching a different set of
    names than the record claims.
    """
    from v1_1_local_credential_discovery import _CRED_ENV_NAMES

    assert set(WATCHED_ENV) == set(_CRED_ENV_NAMES), (
        "this file's watch-list drifted from the discovery module: only here=%r "
        "only there=%r"
        % (sorted(set(WATCHED_ENV) - set(_CRED_ENV_NAMES)),
           sorted(set(_CRED_ENV_NAMES) - set(WATCHED_ENV))))

    published = set(_artifact()["credential_evidence"]["env_presence"])
    assert published == set(WATCHED_ENV), (
        "the committed §7 artifact records presence for %r, not %r -- the canary "
        "is not watching what this file asserts"
        % (sorted(published), sorted(WATCHED_ENV)))


# --------------------------------------------------------------------------- #
# FINAL-C: importing the e2e conftest must not touch the environment
# --------------------------------------------------------------------------- #
def test_e2e_conftest_has_no_import_time_environment_side_effects():
    """Behavioural, not a grep: the module is REALLY imported and the WHOLE
    environment is compared before/after -- every name, not a hand-picked list,
    because the defect being guarded against is precisely an unlisted write."""
    assert E2E_CONFTEST.is_file(), E2E_CONFTEST
    _assert_import_leaves_the_environment_alone(
        E2E_CONFTEST, "_jcc_e2e_conftest_import_probe")


def test_the_import_side_effect_falsifier_can_actually_fail(tmp_path):
    """POSITIVE CONTROL. Re-introduce the defect in a throwaway copy and require
    the SAME assertion to reject it -- so a green run above is evidence about the
    check, not merely an absence of evidence."""
    poisoned = tmp_path / "poisoned_conftest.py"
    poisoned.write_text(
        "import os\n"
        "os.environ['JCC_CONTROL_TOKEN'] = 'poison-from-import'\n"
        "os.environ.setdefault('JEV_API_KEY', 'poison-from-default')\n",
        encoding="utf-8")

    before = dict(os.environ)
    try:
        with pytest.raises(AssertionError) as excinfo:
            _assert_import_leaves_the_environment_alone(
                poisoned, "_jcc_poisoned_conftest")
        message = str(excinfo.value)
        # The message must NAME the offending keys: an error that says only
        # "something changed" cannot be acted on, and a control that accepts an
        # unnamed delta would pass on a check that detects the wrong thing.
        assert "JCC_CONTROL_TOKEN" in message and "JEV_API_KEY" in message, message
    finally:
        _restore_env(before)


# --------------------------------------------------------------------------- #
# FINAL-D: the e2e fixture lifecycle must leave the credential state untouched
# --------------------------------------------------------------------------- #
def test_the_jev_credential_state_is_unchanged_by_the_e2e_fixture_lifecycle(tmp_path):
    """§7 before -> the REAL e2e fixture lifecycle -> teardown -> §7 after.

    Read at both ends in ONE process, which is the only place the leak was ever
    observable. Two independent instruments are used, so neither can carry the
    verdict alone:
      * the environment itself (``before == after``), and
      * §7's own ``--check``, run as a subprocess at both ends.
    """
    conf = _load_by_path(E2E_CONFTEST, "_jcc_e2e_conftest_lifecycle")

    before_env = dict(os.environ)
    before_check = _run_jev_check()
    assert before_check.returncode == 0, (
        "§7 already disagrees with a fresh generation BEFORE the e2e lifecycle, "
        "so this test could not attribute anything to the lifecycle. Re-run the "
        "generator without --check and commit the result.\n"
        "stdout: %s\nstderr: %s" % (before_check.stdout, before_check.stderr))

    monkeypatch = pytest.MonkeyPatch()
    fixture_gen = _raw_fixture(conf.api_server)(tmp_path, monkeypatch)
    srv = next(fixture_gen)
    try:
        assert srv.request("GET", "/api/health").status == 200
        # NON-VACUITY: DURING the fixture the two names MUST be set, otherwise
        # this test would pass on a fixture that simply never configured its
        # server -- proving nothing about restoration.
        for name, expected in ((E2E_SET_NAMES[0], conf.E2E_CONTROL_TOKEN),
                               (E2E_SET_NAMES[1], conf.E2E_SIGNING_KEY)):
            assert os.environ.get(name) == expected, (
                "the e2e fixture did not scope %s into the environment while its "
                "server was running, so this run measured nothing" % name)
    finally:
        try:
            next(fixture_gen)          # runs the fixture's own teardown
        except StopIteration:
            pass
        monkeypatch.undo()
        srv.stop()

    after_env = dict(os.environ)
    assert after_env == before_env, (
        "the e2e fixture lifecycle changed the process environment (%s). A "
        "function-scoped fixture must restore what it set -- an unrestored write "
        "here is what leaked a control token and a signing key into every later "
        "test in the same pytest process."
        % _env_delta(before_env, after_env))

    after_check = _run_jev_check()
    assert after_check.returncode == 0, (
        "§7 disagrees with a fresh generation AFTER the e2e lifecycle but not "
        "before it, so the lifecycle polluted the credential state.\n"
        "stdout: %s\nstderr: %s" % (after_check.stdout, after_check.stderr))


def test_the_credential_presence_in_the_artifact_matches_this_host():
    """The committed record's presence claims == the host, field for field.

    This is the honest end state the release is allowed to ship: if the host has
    no Jev credential, the artifact says ABSENT; if one is later provided, the
    artifact must say PRESENT (which means it was regenerated). Recomputed here
    rather than compared against a hard-coded expectation, so the test states the
    rule instead of one particular host's answer.
    """
    from v1_1_local_credential_discovery import env_presence

    published = _artifact()["credential_evidence"]["env_presence"]
    live = env_presence()
    assert published == live, (
        "the committed §7 credential record disagrees with this host:\n"
        "  recorded: %r\n  live:     %r\n"
        "Regenerate the artifact (tools/v1_1_emit_real_jev_verification.py) and "
        "commit it." % (published, live))

    # The credential the REAL provider would use, stated explicitly so the release
    # report can quote it.
    real_env = _artifact()["credential_evidence"]["real_provider_api_key_env"]
    assert published[real_env]["state"] == live[real_env]["state"]


# --------------------------------------------------------------------------- #
# The canary must stay sensitive (this is the "do not weaken §7" lock)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["JEV_API_KEY", "JCC_CONTROL_TOKEN",
                                  "JCC_DIRECTIVE_SIGNING_KEY"])
def test_the_jev_check_still_observes_the_environment(name):
    """§7's check MUST go STALE when a credential appears. The cheap way to make
    a polluted §7 pass is to stop it reading the environment -- which would also
    make it blind the day a real credential arrives. This pins the sensitivity so
    that "fix the pollution" cannot quietly become "blind the canary"."""
    clean = _run_jev_check()
    assert clean.returncode == 0, (
        "§7 does not match this host before any injection, so the injection below "
        "would not be attributable.\nstdout: %s" % clean.stdout)

    polluted = _run_jev_check({name: "jcc-isolation-falsifier"})
    assert polluted.returncode != 0, (
        "§7's --check returned SUCCESS with %s=%r set. It is no longer observing "
        "the environment, so a real Jev credential could never be noticed.\n"
        "stdout: %s" % (name, "jcc-isolation-falsifier", polluted.stdout))
    assert "STALE" in (polluted.stdout + polluted.stderr), (
        "the check failed for a reason other than staleness, so this probe cannot "
        "show that the environment was observed: %r"
        % (polluted.stdout + polluted.stderr))
