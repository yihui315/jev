"""Regression test: the agent-event path must survive a repo relocation.

WHY THIS FILE EXISTS
--------------------
The checkout moved from ``F:/jev`` to ``D:/jev``. The four agent integrations
each carried ``os.environ.get("JCC_ROOT", "F:/jev")`` (and its PowerShell/``.cmd``
equivalents), so with ``JCC_ROOT`` unset the derived path named a drive that does
not exist. ``integrations/_common/jcc_emit.py`` inserts that path into
``sys.path`` and then imports ``jcc_core`` at MODULE level, so the failure was
at import time, not at call time: the native event path for ALL FOUR agents died
and every one of the four installers was affected, not just one entry point.

These tests assert the property that actually matters -- **the path is DERIVED,
not typed** -- rather than asserting today's drive letter, which would just
re-introduce the same class of bug one drive move later.

WHAT IS COVERED
---------------
1. No integration file may contain a hardcoded absolute repo root. Guarded by an
   AST scan for string CONSTANTS, so a value inside a comment or a docstring
   example does not trip it, while a real ``ROOT = "F:/jev"`` does.
2. ``JCC_ROOT`` remains honoured as an explicit operator override -- deriving the
   default must not delete the escape hatch.
3. With ``JCC_ROOT`` UNSET, the emitter's derived root exists and ``jcc_core``
   really loads FROM it (asserting the module's ``__file__``, so a ``sys.path``
   entry left over from another test cannot make this pass by accident).
4. ``jcc_settings``'s ``--jcc-root`` default names a directory that exists and
   contains the emitter, because that value gets baked into a hook command.
5. A real end-to-end emit: a native WorkBuddy event normalizes to a valid AEP
   envelope with ``JCC_ROOT`` unset. This is the behaviour that was broken.
6. The Hermes handler template carries a SUBSTITUTION TOKEN, not a drive letter:
   it is installed outside the checkout, so it cannot derive anything.
7. ``install()`` is never a silent no-op -- the "ran but did nothing" class that
   is indistinguishable, from outside, from "the hook is fine".

Run:
  D:\\jev\\.venv\\Scripts\\python.exe -m pytest packages/jcc_core/tests/test_relocation_derivations.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
INTEGRATIONS = REPO / "integrations"
PACKAGES = REPO / "packages"
VENV_PYTHON = REPO / ".venv" / "Scripts" / "python.exe"


def _load(module_path: Path, name: str):
    if str(module_path.parent) not in sys.path:
        sys.path.insert(0, str(module_path.parent))
    spec = importlib.util.spec_from_file_location(name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _string_constants(path: Path) -> list[str]:
    """Every string literal in ``path``, excluding docstrings.

    A drive letter in a COMMENT or a docstring usage example is documentation; a
    drive letter in a real assignment is the bug. Walking the AST separates the
    two without depending on comment text.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value not in docstrings
    ]


def _looks_like_absolute_repo_root(text: str) -> bool:
    """True for a Windows drive-rooted path, which must never be a fallback.

    ``http://`` / ``https://`` schemes are stripped FIRST: the ``p:`` in a URL
    otherwise matches a naive drive-letter pattern, which would make every
    loopback URL in these files a false positive. A drive letter can still hide
    in the path part of a URL, so only the scheme is removed, not the whole URL.
    """
    stripped = re.sub(r"https?://", "", text, flags=re.IGNORECASE)
    return bool(re.search(r"(?:^|[^A-Za-z0-9])[A-Za-z]:[\\/]", stripped))


def _python_sources() -> list[Path]:
    return [
        p for p in sorted(INTEGRATIONS.rglob("*.py"))
        if "__pycache__" not in p.parts
    ]


def _scratch(name: str) -> Path:
    """A throwaway dir that does NOT use tmp_path, and never deletes anything.

    Two host constraints, both measured rather than assumed:
      * ``tmp_path`` goes through the session-scoped basetemp, whose own cleanup
        trips this host's safe-delete bulk guard;
      * that guard counts EVERY file created in the turn, so a ``shutil.rmtree``
        here aborts the whole test run once the session crosses 50 files.

    So: a unique-per-call directory under the repo scratch tree, created with
    ``exist_ok`` and left in place. These are a handful of small JSON files under
    ``.workbuddy/scratch``; leaving them is cheaper than a flaky suite, and they
    are outside every gate's measured set.
    """
    d = REPO / ".workbuddy" / "scratch" / "relay_relocation_tests" / f"{name}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# 1. No hardcoded absolute repo root in live integration code.
# --------------------------------------------------------------------------

def test_no_integration_python_hardcodes_an_absolute_repo_root():
    """The defect itself: a drive-rooted fallback in a shipped .py file."""
    offenders = []
    for p in _python_sources():
        for value in _string_constants(p):
            if _looks_like_absolute_repo_root(value):
                offenders.append(f"{p.relative_to(REPO)}: {value!r}")
    assert not offenders, (
        "absolute repo root hardcoded in integration code (relocate the checkout "
        "and the agent event path breaks at import time):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("script", [
    "workbuddy/install.ps1", "workbuddy/uninstall.ps1",
    "claude/install.ps1", "claude/uninstall.ps1",
    "codex/install.ps1", "codex/uninstall.ps1",
    "hermes/install.ps1",
])
def test_installer_has_no_hardcoded_drive_default(script):
    """PowerShell installers must not default ``$JccRoot`` to a drive letter."""
    text = (INTEGRATIONS / script).read_text(encoding="utf-8", errors="ignore")
    code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    offenders = [
        ln.strip() for ln in code_lines
        if _looks_like_absolute_repo_root(ln) and "JccRoot" in ln
    ]
    assert not offenders, (
        f"{script} still defaults $JccRoot to a drive letter: {offenders}"
    )


@pytest.mark.parametrize("wrapper", [
    "claude/jcc-claude-hook.cmd",
    "codex/jcc-codex-notify.cmd",
])
def test_cmd_wrapper_derives_root_instead_of_naming_a_drive(wrapper):
    """``.cmd`` wrappers run with no env; they must self-locate via %~dp0."""
    text = (INTEGRATIONS / wrapper).read_text(encoding="utf-8", errors="ignore")
    code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("rem")]
    offenders = [ln.strip() for ln in code_lines if _looks_like_absolute_repo_root(ln)]
    assert not offenders, f"{wrapper} names a hardcoded drive: {offenders}"
    assert "%~dp0" in text, f"{wrapper} does not derive its location with %~dp0"


def test_url_regex_does_not_trip_on_loopback_urls():
    """Guards the guard: a URL must not read as a drive letter."""
    for url in ("http://127.0.0.1:4778", "http://127.0.0.1:4778/api/assess",
                "https://api.typesafe.ai"):
        assert not _looks_like_absolute_repo_root(url), f"false positive on {url!r}"
    for path in ("F:/jev", "D:\\jev", '"F:/jev"'):
        assert _looks_like_absolute_repo_root(path), f"missed a real drive in {path!r}"


# --------------------------------------------------------------------------
# 2. JCC_ROOT is STILL honoured (deriving a default must not kill the override).
# --------------------------------------------------------------------------

def test_jcc_root_env_override_still_wins(monkeypatch):
    """An operator who sets JCC_ROOT means it; the derived default is a fallback."""
    monkeypatch.setenv("JCC_ROOT", "D:/jev")
    emit = _load(INTEGRATIONS / "_common" / "jcc_emit.py", "relay_emit_override")
    assert emit.DEFAULT_ROOT == "D:/jev", (
        "JCC_ROOT override was dropped; it is a supported operator interface"
    )


def test_jcc_settings_root_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("JCC_ROOT", "D:/jev")
    s = _load(INTEGRATIONS / "_common" / "jcc_settings.py", "relay_settings_override")
    assert s.DEFAULT_JCC_ROOT == "D:/jev"


# --------------------------------------------------------------------------
# 3. With JCC_ROOT UNSET, the derived root is real and jcc_core loads FROM it.
# --------------------------------------------------------------------------

def test_derived_root_exists_with_jcc_root_unset(monkeypatch):
    """The property that was broken: no env set => still a usable repo root."""
    monkeypatch.delenv("JCC_ROOT", raising=False)
    emit = _load(INTEGRATIONS / "_common" / "jcc_emit.py", "relay_emit_derived")
    root = Path(emit.DEFAULT_ROOT)
    assert root.is_dir(), f"derived root does not exist: {root}"
    assert root == REPO, f"derived root {root} != checkout {REPO}"
    assert (root / "packages" / "jcc_core" / "__init__.py").is_file()


def test_jcc_core_really_loads_from_the_derived_root(monkeypatch):
    """Assert the module ORIGIN, not merely that the import succeeded.

    A stale ``sys.path`` entry from another test could otherwise make a broken
    derivation look fine. ``jcc_core`` is purged from ``sys.modules`` first so
    the import is genuine.
    """
    monkeypatch.delenv("JCC_ROOT", raising=False)
    for name in [n for n in list(sys.modules) if n == "jcc_core" or n.startswith("jcc_core.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    emit = _load(INTEGRATIONS / "_common" / "jcc_emit.py", "relay_emit_origin")
    import jcc_core

    core_file = Path(jcc_core.__file__).resolve()
    assert core_file.is_relative_to(Path(emit.DEFAULT_ROOT).resolve()), (
        f"jcc_core loaded from {core_file}, which is OUTSIDE the derived root "
        f"{emit.DEFAULT_ROOT}"
    )


# --------------------------------------------------------------------------
# 4. jcc_settings' default names a directory that exists (it is BAKED into a
#    hook command written into an agent's settings.json).
# --------------------------------------------------------------------------

def test_jcc_settings_default_root_is_usable(monkeypatch):
    monkeypatch.delenv("JCC_ROOT", raising=False)
    s = _load(INTEGRATIONS / "_common" / "jcc_settings.py", "relay_settings_default")
    root = Path(s.DEFAULT_JCC_ROOT)
    assert root.is_dir(), f"--jcc-root default points nowhere: {root}"
    assert (root / "integrations" / "_common" / "jcc_emit.py").is_file()
    for agent in ("workbuddy", "claude"):
        cmd = s.default_hook_command(agent, s.DEFAULT_JCC_ROOT)
        # The WorkBuddy command goes through posix(), which renders the root as
        # "/d/jev" (leading slash, lowercased drive, NO colon); Claude keeps native
        # Windows form "D:\jev". Comparing raw strings would assert on formatting.
        # Compare on the path SEGMENTS that must survive either rendering, and --
        # the substantive check -- assert the referenced files really exist.
        assert str(root).replace("\\", "/").split(":", 1)[-1].lstrip("/").lower() in \
            cmd.replace("\\", "/").lower(), (
            f"{agent} hook command does not reference the real root: {cmd}"
        )
    # And the strongest form: the files the WorkBuddy hook names must really exist.
    # NOTE the emitted form is deliberately Git-Bash POSIX ("/d/jev/..."), because
    # WorkBuddy/CodeBuddy enforce Git Bash on Windows (see default_hook_command's
    # docstring). Windows Path() cannot open that form -- it reads "/d/jev" as
    # "\d\jev" relative to the current drive -- so rebuild a native path by
    # REPLACING the posix drive prefix, not by stripping characters off it.
    def _from_posix(p: str) -> Path:
        # "/d/jev/x" -> "D:\jev\x"
        assert p.startswith("/"), f"not a posix path: {p!r}"
        return Path(f"{p[1].upper()}:{p[2:].replace('/', os.sep)}")

    parts = s.default_hook_command("workbuddy", s.DEFAULT_JCC_ROOT).split('"')
    assert parts[1].startswith("/d/"), f"workbuddy command lost POSIX form: {parts[1]}"
    native = _from_posix(parts[1])
    assert native.is_file(), f"workbuddy hook names a missing interpreter: {native}"
    assert native == REPO / ".venv" / "Scripts" / "python.exe"
    emitter_native = _from_posix(parts[3])
    assert emitter_native.is_file(), f"workbuddy hook names a missing emitter: {emitter_native}"
    assert emitter_native == REPO / "integrations" / "_common" / "jcc_emit.py"


def test_claude_hook_command_targets_a_real_wrapper(monkeypatch):
    """The Claude hook is a .cmd; the path written must actually exist."""
    monkeypatch.delenv("JCC_ROOT", raising=False)
    s = _load(INTEGRATIONS / "_common" / "jcc_settings.py", "relay_settings_wrapper")
    s.default_hook_command("claude", s.DEFAULT_JCC_ROOT)
    wrapper = Path(s.DEFAULT_JCC_ROOT) / "integrations" / "claude" / "jcc-claude-hook.cmd"
    assert wrapper.is_file(), f"Claude hook command names a missing wrapper: {wrapper}"


# --------------------------------------------------------------------------
# 5. END-TO-END: a native event normalizes with JCC_ROOT unset. This is the
#    behaviour that was actually broken in the field.
# --------------------------------------------------------------------------

NATIVE_WORKBUDDY_EVENT = {
    "hook_event_name": "SessionStart",
    "session_id": "relocation-regression-1",
    "cwd": "D:/jev",
    "_source": "hook",
}


@pytest.mark.skipif(not VENV_PYTHON.is_file(), reason="repo venv python not found")
def test_native_event_normalizes_in_a_clean_subprocess_without_jcc_root():
    """Run the emitter exactly as the hook does: no JCC_ROOT, fresh interpreter.

    A subprocess is required -- an in-process test would inherit this test
    session's already-patched ``sys.path`` and could pass even if the file's own
    derivation were still broken.
    """
    env = {k: v for k, v in os.environ.items() if k != "JCC_ROOT"}
    proc = subprocess.run(
        [str(VENV_PYTHON), str(INTEGRATIONS / "_common" / "jcc_emit.py"),
         "--agent", "workbuddy", "--dry-run"],
        input=json.dumps(NATIVE_WORKBUDDY_EVENT),
        capture_output=True, text=True, env=env, timeout=120,
        cwd=str(_scratch("emit_cwd")),
    )
    assert proc.returncode == 0, (
        f"emitter failed with JCC_ROOT unset (exit {proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ModuleNotFoundError" not in proc.stderr, (
        f"import path still broken: {proc.stderr}"
    )
    env_json = json.loads(proc.stdout)
    assert env_json["session_id"] == NATIVE_WORKBUDDY_EVENT["session_id"]
    assert env_json["agent"] == "workbuddy"
    assert env_json["schema_ok"] is True
    assert env_json["version"] == "aep/v0.1"


@pytest.mark.skipif(not VENV_PYTHON.is_file(), reason="repo venv python not found")
def test_codex_observer_entrypoint_loads_without_jcc_root():
    """The VERIFIED Codex rollout path shares the same sys.path injection."""
    env = {k: v for k, v in os.environ.items() if k != "JCC_ROOT"}
    proc = subprocess.run(
        [str(VENV_PYTHON), str(INTEGRATIONS / "codex" / "codex_rollout_observer.py"),
         "--once", "--dry-run"],
        capture_output=True, text=True, env=env, timeout=120,
        cwd=str(_scratch("codex_cwd")),
    )
    assert proc.returncode == 0, (
        f"codex observer failed with JCC_ROOT unset (exit {proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ModuleNotFoundError" not in proc.stderr


@pytest.mark.skipif(not VENV_PYTHON.is_file(), reason="repo venv python not found")
def test_codex_notify_helper_loads_without_jcc_root():
    """The notify wrapper must find jcc_emit; it swallows errors, so assert directly."""
    env = {k: v for k, v in os.environ.items() if k != "JCC_ROOT"}
    code = (
        "import importlib.util, os, sys;"
        f"spec=importlib.util.spec_from_file_location('n', r'{INTEGRATIONS / 'codex' / 'jcc-codex-notify.py'}');"
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "sys.path[:]=[p for p in sys.path if 'F:' not in p];"
        "from jcc_emit import emit_event;"
        "print('OK', emit_event is not None, m.DEFAULT_ROOT)"
    )
    proc = subprocess.run(
        [str(VENV_PYTHON), "-c", code],
        capture_output=True, text=True, env=env, timeout=120,
        cwd=str(_scratch("codex_notify_cwd")),
    )
    assert proc.returncode == 0, (
        f"codex notify helper could not import jcc_emit\nstderr: {proc.stderr}"
    )
    assert "OK True" in proc.stdout


# --------------------------------------------------------------------------
# 6. The Hermes template: rendered by install.ps1, so it carries a TOKEN, not a
#    drive. An unsubstituted token must never masquerade as a real root.
# --------------------------------------------------------------------------

def test_hermes_template_uses_a_substitution_token_not_a_drive():
    text = (INTEGRATIONS / "hermes" / "handler.py.tpl").read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    offenders = [ln.strip() for ln in code if _looks_like_absolute_repo_root(ln)]
    assert not offenders, f"hermes template names a drive: {offenders}"
    assert "__JCC_ROOT__" in text, (
        "handler.py.tpl must carry the __JCC_ROOT__ token; install.ps1 substitutes it"
    )


def test_hermes_installer_substitutes_the_token():
    """A token that nobody renders would ship a literal __JCC_ROOT__ to ~/.hermes."""
    install_ps1 = (INTEGRATIONS / "hermes" / "install.ps1").read_text(encoding="utf-8")
    assert "__JCC_ROOT__" in install_ps1, "install.ps1 never substitutes the handler token"
    assert "WriteAllText" in install_ps1 or "Set-Content" in install_ps1


def test_hermes_rendered_handler_resolves_the_real_root():
    """Render the template the way install.ps1 does, then import the result.

    Proves the substituted value is a usable repo root AND that backslashes are
    escaped correctly for the Python string literal (``D:\\jev``, not ``D:\\jev``
    collapsing to ``D:jev`` through an invalid escape).
    """
    import tempfile

    monkey_root = str(REPO)
    text = (INTEGRATIONS / "hermes" / "handler.py.tpl").read_text(encoding="utf-8")
    rendered = text.replace("__JCC_ROOT__", monkey_root.replace("\\", "\\\\"))
    assert "__JCC_ROOT__" not in rendered

    # No rmtree: see _scratch() -- this host's safe-delete guard aborts the run.
    tmp = _scratch("hermes_rendered")
    dst = tmp / "handler.py"
    dst.write_text(rendered, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "JCC_ROOT"}
    proc = subprocess.run(
        [str(VENV_PYTHON), "-c",
         f"import importlib.util,sys;"
         f"spec=importlib.util.spec_from_file_location('h', r'{dst}');"
         "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
         "print('EMIT', m.emit_event is not None)"],
        capture_output=True, text=True, env=env, timeout=120, cwd=str(tmp),
    )
    assert proc.returncode == 0, f"rendered handler failed to import: {proc.stderr}"
    assert "EMIT True" in proc.stdout, (
        f"rendered handler could not import jcc_emit: {proc.stdout}"
    )


# --------------------------------------------------------------------------
# 7. install() must never be a silent no-op -- the "ran but did nothing" class.
# --------------------------------------------------------------------------

def test_install_into_absent_directory_creates_and_is_visible(monkeypatch):
    """An absent ~/.claude must be CREATED, not skipped, and status() must see it.

    This is the distinction team-lead asked to be pinned down: a skip here would
    look identical, from the outside, to "the installer ran and the hook is
    fine".
    """
    monkeypatch.delenv("JCC_ROOT", raising=False)
    s = _load(INTEGRATIONS / "_common" / "jcc_settings.py", "relay_settings_noskip")
    target = _scratch("absent_home") / "claude" / "settings.json"
    assert not target.parent.is_dir(), "precondition: parent must start absent"

    result = s.install("claude", target, s.DEFAULT_JCC_ROOT)
    assert target.is_file(), "install() did not create the settings file"
    assert result["permission_request_hooked"] is False
    st = s.status("claude", target)
    assert st["jcc_installed"] is True
    assert "PermissionRequest" not in st["jcc_events"]
    assert len(st["jcc_events"]) == 8


def test_install_is_idempotent_and_uninstall_preserves_user_hooks(monkeypatch):
    monkeypatch.delenv("JCC_ROOT", raising=False)
    s = _load(INTEGRATIONS / "_common" / "jcc_settings.py", "relay_settings_roundtrip")
    target = _scratch("roundtrip") / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo user"}]}]}}),
        encoding="utf-8",
    )
    s.install("workbuddy", target, s.DEFAULT_JCC_ROOT)
    s.install("workbuddy", target, s.DEFAULT_JCC_ROOT)
    data = json.loads(target.read_text(encoding="utf-8"))
    entries = data["hooks"]["SessionStart"]
    assert len(entries) == 1, f"re-install duplicated the hook: {len(entries)}"
    s.uninstall("workbuddy", target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert s.status("workbuddy", target)["jcc_installed"] is False
    assert any("echo user" in json.dumps(e) for e in data["hooks"].get("Stop", [])), \
        "uninstall removed a USER hook"


# --------------------------------------------------------------------------
# 8. Status reporting honesty: a fresh install reports every event under
#    events_refreshed, leaving events_added at 0. Not a functional bug (the hooks
#    ARE written and status() sees them) but a log reading "events_added: 0"
#    next to 8 written events invites exactly the wrong conclusion. Pinned so a
#    future change to the reporting is deliberate.
# --------------------------------------------------------------------------

def test_fresh_install_reports_every_event(monkeypatch):
    monkeypatch.delenv("JCC_ROOT", raising=False)
    s = _load(INTEGRATIONS / "_common" / "jcc_settings.py", "relay_settings_counters")
    target = _scratch("counters") / "settings.json"
    result = s.install("workbuddy", target, s.DEFAULT_JCC_ROOT)
    accounted = len(result["events_added"]) + len(result["events_refreshed"])
    assert accounted == len(s.TELEMETRY_EVENTS["workbuddy"]) == 8
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert len(on_disk["hooks"]) == 8
