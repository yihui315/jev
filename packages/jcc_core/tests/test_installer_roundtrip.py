"""Installer round-trip tests for the P2 settings/notify merge engine.

These exercise ``integrations/_common/jcc_settings.py`` and
``integrations/codex/jcc_codex_config.py`` on TEMP files only (never a real user
config). They exist because the SRE gate (Harbor) found that the original
``jcc_emit.py``-only ownership token made:

  * the Claude install NON-IDEMPOTENT (duplicated on every re-install), and
  * the Claude uninstall NON-REVERSIBLE (removed nothing),

and that the Codex opt-in notify path crashed with
``re.error: bad escape \\j`` because a Windows path was used as a ``re.sub``
replacement string. The 81-test suite did not cover these paths -> regression
gap now closed here.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(r"F:\jev")
INTEGRATIONS = ROOT / "integrations"


def _load(module_path: Path, name: str):
    if str(module_path.parent) not in sys.path:
        sys.path.insert(0, str(module_path.parent))
    if str(INTEGRATIONS) not in sys.path:
        sys.path.insert(0, str(INTEGRATIONS))
    spec = importlib.util.spec_from_file_location(name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


jcc_settings = _load(INTEGRATIONS / "_common" / "jcc_settings.py", "jcc_settings_ut")
codex_cfg = _load(INTEGRATIONS / "codex" / "jcc_codex_config.py", "jcc_codex_config_ut")

USER_HOOK = {"hooks": [{"type": "command", "command": "echo user-hook"}]}


def _write_user_settings(path: Path, events=("SessionStart",)) -> None:
    path.write_text(
        json.dumps({"hooks": {ev: [dict(USER_HOOK)] for ev in events}}),
        encoding="utf-8",
    )


def _entries(path: Path, ev: str):
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("hooks") or {}).get(ev) or []


def _count_user(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    return sum(1 for entries in (data.get("hooks") or {}).values()
               for e in (entries or [])
               if isinstance(e, dict) and not jcc_settings._entry_is_jcc(e))


# --------------------------------------------------------------------------- #
# 1) The core invariant that regressed: every JCC hook command is recognised.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("agent", ["workbuddy", "claude"])
def test_default_hook_command_is_recognised_as_jcc(agent):
    cmd = jcc_settings.default_hook_command(agent, str(ROOT))
    entry = {"hooks": [{"type": "command", "command": cmd}]}
    assert jcc_settings._entry_is_jcc(entry), (
        f"{agent}: JCC hook command {cmd!r} is NOT recognised as JCC-owned -> "
        "install would duplicate and uninstall would remove nothing"
    )


def test_user_hook_is_not_mistaken_for_jcc():
    assert not jcc_settings._entry_is_jcc(USER_HOOK)


# --------------------------------------------------------------------------- #
# 2) install/uninstall round-trip: idempotent + reversible + user hooks kept.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("agent", ["workbuddy", "claude"])
def test_settings_install_is_idempotent_and_reversible(tmp_path, agent):
    settings = tmp_path / "settings.json"
    _write_user_settings(settings, ("SessionStart", "Stop"))
    original = settings.read_text(encoding="utf-8")

    r1 = jcc_settings.install(agent, settings, str(ROOT))
    after1 = settings.read_text(encoding="utf-8")
    r2 = jcc_settings.install(agent, settings, str(ROOT))
    after2 = settings.read_text(encoding="utf-8")

    assert r1["user_hooks_preserved"] is True
    assert r2["user_hooks_preserved"] is True
    assert after1 == after2, f"{agent}: re-install is NOT idempotent"

    # user hooks survive install
    assert _count_user(settings) == 2
    # JCC installed on all telemetry events, exactly once each
    for ev in jcc_settings.TELEMETRY_EVENTS[agent]:
        jcc_entries = [e for e in _entries(settings, ev) if jcc_settings._entry_is_jcc(e)]
        assert len(jcc_entries) == 1, f"{agent}/{ev}: expected exactly 1 JCC entry"

    res = jcc_settings.uninstall(agent, settings)
    assert res["removed_entries"] > 0
    assert _count_user(settings) == 2, f"{agent}: uninstall removed a USER hook"
    assert not any(jcc_settings._entry_is_jcc(e)
                   for entries in (json.loads(settings.read_text(encoding='utf-8'))
                                   .get("hooks") or {}).values()
                   for e in (entries or [])), f"{agent}: JCC entries remain after uninstall"
    # reversible: user-hook payload identical to before install
    assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]["SessionStart"] == [USER_HOOK]
    assert original  # sanity


@pytest.mark.parametrize("agent", ["workbuddy", "claude"])
def test_settings_backup_written_before_change(tmp_path, agent):
    settings = tmp_path / "settings.json"
    _write_user_settings(settings)
    res = jcc_settings.install(agent, settings, str(ROOT))
    assert res["backup"] and Path(res["backup"]).exists()


@pytest.mark.parametrize("agent", ["workbuddy", "claude"])
def test_permission_request_never_hooked(tmp_path, agent):
    settings = tmp_path / "settings.json"
    _write_user_settings(settings)
    jcc_settings.install(agent, settings, str(ROOT))
    hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks") or {}
    assert "PermissionRequest" not in hooks, f"{agent}: P2 forbids PermissionRequest hooking"


def test_uninstall_on_absent_file_is_noop(tmp_path):
    res = jcc_settings.uninstall("claude", tmp_path / "missing.json")
    assert res["removed_entries"] == 0 if "removed_entries" in res else True


# --------------------------------------------------------------------------- #
# 3) Codex notify opt-in: Windows-path replacement must work + be reversible.
# --------------------------------------------------------------------------- #
def _codex_cfg(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def test_codex_notify_force_with_windows_path_does_not_raise(tmp_path):
    cfg = _codex_cfg(tmp_path, 'model = "gpt-5-codex"\n'
                               'notify = [ "codex-computer-use.exe", "turn-ended" ]\n'
                               '[projects]\n')
    wrapper = r"F:\jev\integrations\codex\jcc-codex-notify.cmd"  # backslashes!
    res = codex_cfg.install(cfg, wrapper, force=True)  # must not raise re.error
    assert res["ok"] is True and res["chained"] is True
    text = cfg.read_text(encoding="utf-8")
    assert "jcc-codex-notify.cmd" in text
    assert "model = " in text and "[projects]" in text  # unrelated keys intact
    # original preserved in sidecar
    sidecar = cfg.with_name(cfg.name + codex_cfg.SIDECAR_SUFFIX)
    assert json.loads(sidecar.read_text(encoding="utf-8"))["notify"] == \
        ["codex-computer-use.exe", "turn-ended"]


def test_codex_notify_uninstall_restores_original_exactly(tmp_path):
    cfg = _codex_cfg(tmp_path, 'model = "gpt-5-codex"\n'
                               'notify = [ "codex-computer-use.exe", "turn-ended" ]\n')
    before = cfg.read_text(encoding="utf-8")
    codex_cfg.install(cfg, r"F:\jev\integrations\codex\jcc-codex-notify.cmd", force=True)
    res = codex_cfg.uninstall(cfg)
    assert res["restored"] == ["codex-computer-use.exe", "turn-ended"]
    assert cfg.read_text(encoding="utf-8") == before, "uninstall is not exactly reversible"
    assert res["sidecar_removed"] is True


def test_codex_notify_refuses_without_force(tmp_path):
    cfg = _codex_cfg(tmp_path, 'notify = [ "codex-computer-use.exe", "turn-ended" ]\n')
    before = cfg.read_text(encoding="utf-8")
    res = codex_cfg.install(cfg, r"F:\jev\integrations\codex\jcc-codex-notify.cmd", force=False)
    assert res["ok"] is False and res["reason"] == "user_notify_present"
    assert cfg.read_text(encoding="utf-8") == before  # fail-safe: unchanged


def test_codex_failed_install_leaves_no_orphan_sidecar(tmp_path):
    """A refused install must not write a sidecar (ordering regression guard)."""
    cfg = _codex_cfg(tmp_path, 'notify = [ "a.exe" ]\n')
    codex_cfg.install(cfg, r"F:\jev\integrations\codex\jcc-codex-notify.cmd", force=False)
    assert not cfg.with_name(cfg.name + codex_cfg.SIDECAR_SUFFIX).exists()


def test_codex_referenced_wrapper_artifacts_exist():
    """Harbor: the notify wrapper was referenced but absent from the tree."""
    d = INTEGRATIONS / "codex"
    assert (d / "jcc-codex-notify.cmd").exists()
    assert (d / "jcc-codex-notify.py").exists()


@pytest.mark.parametrize("body", [
    'model = "x"\nnotify = [ "a.exe", "turn-ended" ]\n[t]\n',
    'notify   =   [  "a.exe" ,  "turn-ended"  ]\n',         # odd spacing
    'model = "x"\r\nnotify = [ "a.exe" ]\r\n[t]\r\n',        # CRLF
    'model = "x"\nnotify = [ \'a.exe\', \'turn-ended\' ]\n',  # single quotes
])
def test_codex_notify_restore_is_byte_exact(tmp_path, body):
    """Uninstall must reproduce the original file byte-for-byte (rollback)."""
    cfg = _codex_cfg(tmp_path, body)
    before = cfg.read_bytes()
    codex_cfg.install(cfg, r"F:\jev\integrations\codex\jcc-codex-notify.cmd", force=True)
    assert cfg.read_bytes() != before
    codex_cfg.uninstall(cfg)
    assert cfg.read_bytes() == before, "uninstall is not byte-exact reversible"
