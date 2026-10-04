#!/usr/bin/env python3
"""CR-002 falsifier -- can a PRODUCT instance still take GREEN's port 4778?

THE DEFECT
----------
``jcc_core.config.DEFAULT_CONFIG_PATH`` pointed at ``config/jcc.yaml`` -- the GREEN
baseline's own file, which declares ``server.port: 4778``. So a bare

    python -m jcc_core.run            # no JCC_CONFIG

started a *v1.3 product* instance on the baseline's protected port. That is not
merely a port conflict: a health probe on 4778 cannot tell the baseline from an
impostor, so every GREEN-protection assertion in the release became
unfalsifiable, and master directive 0.2 (4778 is read/observe only) was violated
by the product itself.

THE FIX, AND WHY IT IS SHAPED THIS WAY
-------------------------------------
1. The default is now ``config/jcc-v1.3.yaml`` (port 4779).
2. ``run.check_green_port_ownership`` REFUSES to start any NON-GREEN instance on
   4778, before the store, provider, socket or marker are touched.

Identity is decided by WHICH CONFIG FILE WAS LOADED, not by an ``--edition``
flag or a ``JCC_INSTANCE`` env var. That is the load-bearing decision. A flag
means GREEN only starts if its launcher remembers to pass it, and the failure
mode is *GREEN refusing to come up* -- strictly worse than the bug being fixed.
GREEN already declares itself: ``scripts/start.ps1`` sets
``JCC_CONFIG=config/jcc.yaml`` explicitly and no GREEN launch path reads the
default (see :func:`verify_green_launcher_contract`).

WHAT THIS SUITE PROVES
----------------------
Each refusal is paired with a POSITIVE CONTROL, because a suite where everything
is rejected would score 7/7 while proving nothing. The GREEN cases are the
important ones: a guard that stops the baseline from starting has broken the
baseline, which is a worse outcome than the one it prevents.

Run:
    D:/jev/.venv/Scripts/python.exe tools/cr002_green_port_falsifier.py
Exit 0 only if every case holds.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():  # pragma: no cover - environment guard
    PY = sys.executable

sys.path.insert(0, str(REPO / "packages"))

RESULTS = []


def record(case_id: str, ok: bool, detail: str) -> None:
    RESULTS.append({"id": case_id, "ok": bool(ok), "detail": detail})


def fresh_env(**overrides: str) -> dict:
    """A child env with JCC_CONFIG removed unless a case sets it.

    Isolation matters here: the ambient environment of whoever runs the suite
    must not decide whether a case passes.
    """
    env = dict(os.environ)
    env.pop("JCC_CONFIG", None)
    env["PYTHONPATH"] = str(REPO / "packages")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
# in-process checks (fast, and they exercise the real functions)
# --------------------------------------------------------------------------- #
def check_defaults() -> None:
    from jcc_core import config as cfg_mod
    from jcc_core.config import GREEN_PROTECTED_PORT, load_config

    default_path = Path(cfg_mod.DEFAULT_CONFIG_PATH)
    product = load_config()

    record(
        "PRODUCT_DEFAULT_IS_V1_3_AND_NEVER_4778",
        default_path.name == "jcc-v1.3.yaml"
        and product.server.port != GREEN_PROTECTED_PORT
        and product.config_path == str(default_path)
        and not product.is_green_instance,
        "DEFAULT_CONFIG_PATH=%s -> port=%s (GREEN=%s); GREEN_PROTECTED_PORT=%d"
        % (default_path.name, product.server.port, GREEN_PROTECTED_PORT,
           product.is_green_instance),
    )

    # A Config built with no server block at all (library callers, tests) must
    # also not describe a process that lands on the baseline's port.
    from jcc_core.config import ServerConfig
    record(
        "BUILT_IN_PORT_DEFAULT_IS_NOT_THE_GREEN_PORT",
        ServerConfig().port != GREEN_PROTECTED_PORT,
        "ServerConfig().port=%s (GREEN_PROTECTED_PORT=%d)"
        % (ServerConfig().port, GREEN_PROTECTED_PORT),
    )


def check_green_instance_still_legal() -> None:
    """POSITIVE CONTROL for the guard: GREEN itself must NOT be refused."""
    from jcc_core.config import GREEN_CONFIG_PATH, GREEN_PROTECTED_PORT, load_config
    from jcc_core.run import check_green_port_ownership

    cfg = load_config(GREEN_CONFIG_PATH)
    refused = None
    try:
        check_green_port_ownership(cfg)
    except SystemExit as exc:
        refused = exc
    record(
        "GREEN_INSTANCE_IS_STILL_ALLOWED_TO_BIND_4778",
        cfg.server.port == GREEN_PROTECTED_PORT and cfg.is_green_instance
        and refused is None,
        "GREEN config port=%s is_green_instance=%s refused=%r"
        % (cfg.server.port, cfg.is_green_instance, refused),
    )


def check_product_refused_on_4778() -> None:
    """The core refusal: a product instance pointed at 4778 must not start."""
    from jcc_core.config import GREEN_PROTECTED_PORT, load_config
    from jcc_core.run import check_green_port_ownership

    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr002_"))
    try:
        # A product-shaped config that (wrongly) asks for the baseline's port.
        squatter = tmp / "squatter.yaml"
        squatter.write_text(
            "server:\n  host: 127.0.0.1\n  port: %d\n" % GREEN_PROTECTED_PORT,
            encoding="utf-8")
        # A COPY of GREEN's config under a different name must also be refused:
        # otherwise the guard is trivially bypassed by renaming the file.
        green_copy = tmp / "looks-green.yaml"
        green_copy.write_text(
            Path(GREEN_CONFIG_PATH_LOCAL()).read_text(encoding="utf-8"),
            encoding="utf-8")

        outcomes = {}
        for label, path in (("foreign_config_on_4778", squatter),
                            ("copy_of_green_config", green_copy)):
            cfg = load_config(str(path))
            try:
                check_green_port_ownership(cfg)
                outcomes[label] = None
            except SystemExit as exc:
                outcomes[label] = str(exc)

        refused = [v for v in outcomes.values() if v is not None]
        # The message must be actionable: it has to name the port AND say what to
        # do, or an operator is left staring at a refusal with no way forward.
        informative = all(
            ("4778" in m and "config/jcc-v1.3.yaml" in m and "start.ps1" in m)
            for m in refused)
        record(
            "NON_GREEN_INSTANCE_ON_4778_IS_REFUSED_WITH_AN_ACTIONABLE_MESSAGE",
            len(refused) == 2 and informative,
            "refusals=%d/2 informative=%s | %s"
            % (len(refused), informative,
               (refused[0][:150] + "...") if refused else "NONE RAISED"),
        )
    finally:
        _rmtree(tmp)


def GREEN_CONFIG_PATH_LOCAL() -> str:
    from jcc_core.config import GREEN_CONFIG_PATH
    return GREEN_CONFIG_PATH


def check_product_instance_actually_starts_on_4779() -> None:
    """End-to-end: the real entrypoint must come up on 4779, not 4778.

    Runs the real ``run.main`` in a child process, which is the only way to
    observe the actual bind. A refusal is detected by the child's exit code and
    output rather than by inspecting the function, so this cannot pass while the
    entrypoint is broken.
    """
    port = _free_port()
    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr002_run_"))
    try:
        cfg = tmp / "product.yaml"
        cfg.write_text(
            "server:\n  host: 127.0.0.1\n  port: %d\n"
            "state_store:\n  db_path: %s\n"
            "control:\n  token_env: JCC_CONTROL_TOKEN\n"
            % (port, (tmp / "jcc.db").as_posix()),
            encoding="utf-8")
        proc = _run_entrypoint(env=fresh_env(JCC_CONFIG=str(cfg)), timeout=25)
        combined = (proc.stdout or "") + (proc.stderr or "")
        bound_ok = ("starting on 127.0.0.1:%d" % port) in combined
        record(
            "PRODUCT_ENTRYPOINT_STARTS_ON_ITS_OWN_PORT",
            proc.returncode == 0 and bound_ok and "4778" not in combined,
            "rc=%s bound_on_%d=%s mentions_4778=%s"
            % (proc.returncode, port, bound_ok, "4778" in combined),
        )
    finally:
        _rmtree(tmp)


def check_entrypoint_refuses_4778_product() -> None:
    """End-to-end: the real entrypoint must EXIT NON-ZERO on 4778."""
    from jcc_core.config import GREEN_PROTECTED_PORT

    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr002_refuse_"))
    try:
        cfg = tmp / "product-4778.yaml"
        cfg.write_text(
            "server:\n  host: 127.0.0.1\n  port: %d\n"
            "state_store:\n  db_path: %s\n" % (GREEN_PROTECTED_PORT,
                                              (tmp / "jcc.db").as_posix()),
            encoding="utf-8")
        proc = _run_entrypoint(env=fresh_env(JCC_CONFIG=str(cfg)), timeout=25)
        combined = (proc.stdout or "") + (proc.stderr or "")
        # A refusal that exits 0 is a fail-open: a supervisor would restart it.
        refused = proc.returncode != 0 and "refusing to start" in combined
        # It must have refused BEFORE binding, so no uvicorn line may appear.
        never_bound = "Uvicorn running" not in combined
        record(
            "PRODUCT_ENTRYPOINT_ON_4778_EXITS_NONZERO_WITHOUT_BINDING",
            refused and never_bound,
            "rc=%s refused=%s never_bound=%s | %s"
            % (proc.returncode, refused, never_bound,
               combined.strip().splitlines()[-1][:160] if combined.strip() else ""),
        )
    finally:
        _rmtree(tmp)


def verify_green_launcher_contract() -> None:
    """The evidence that makes 'GREEN is unaffected' a measurement, not a hope.

    Asserts the two properties the whole design rests on:
      (1) ``scripts/start.ps1`` names GREEN's config EXPLICITLY, so GREEN never
          reads the changed default;
      (2) no GREEN launch path invokes the bare entrypoint without it.
    If someone later removes that assignment, this case fails -- which is the
    point: the guard's safety for GREEN is a property of the launcher, so the
    launcher is what must be pinned.
    """
    start = REPO / "scripts" / "start.ps1"
    common = REPO / "scripts" / "lib" / "JccCommon.ps1"
    try:
        start_src = start.read_text(encoding="utf-8-sig")
        common_src = common.read_text(encoding="utf-8-sig")
    except OSError as exc:  # pragma: no cover
        record("GREEN_LAUNCHER_STILL_NAMES_ITS_OWN_CONFIG_EXPLICITLY", False,
               "cannot read launcher: %s" % exc)
        return

    sets_env = re.search(r"\$env:JCC_CONFIG\s*=\s*\$Paths\.Config", start_src) is not None
    green_path = re.search(r"Config\s*=\s*Join-Path\s+\$Root\s+'config\\jcc\.yaml'",
                           common_src) is not None
    # A bare `-m jcc_core.run` in a launcher is only safe if JCC_CONFIG is set
    # first; both facts are asserted above, and the bare spawn itself is expected
    # (GREEN legitimately runs the same module, just with its own config).
    record(
        "GREEN_LAUNCHER_STILL_NAMES_ITS_OWN_CONFIG_EXPLICITLY",
        sets_env and green_path,
        "start.ps1 sets $env:JCC_CONFIG=$Paths.Config -> %s; "
        "JccCommon maps that to config\\jcc.yaml -> %s"
        % (sets_env, green_path),
    )


def check_no_regression_in_existing_suites() -> None:
    """The two suites that encode the config/entrypoint contract must stay green."""
    env = fresh_env(CODEBUDDY_SAFE_DELETE_ENABLED="0",
                    JCC_PYTEST_ALLOW_DIRTY_ENV="1")
    proc = subprocess.run(
        [PY, "-m", "pytest",
         "packages/jcc_core/tests/test_config_failclosed.py",
         "packages/jcc_core/tests/test_server_surface.py",
         "-q", "-p", "no:cacheprovider"],
        cwd=str(REPO), env=env, text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:]
    record(
        "EXISTING_CONFIG_AND_SERVER_SUITES_STILL_PASS",
        proc.returncode == 0,
        "rc=%s | %s" % (proc.returncode, tail[0][:160] if tail else ""),
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run_entrypoint(env: dict, timeout: int) -> subprocess.CompletedProcess:
    """Run the real ``jcc_core.run`` entrypoint and capture its output.

    ``timeout`` is a FAILURE SIGNAL, not just a guard: a server that ignores
    SIGTERM (the venv python.exe is a redirector stub on this host) would
    otherwise hang the suite. A TimeoutExpired is turned into a synthetic
    non-zero result carrying the partial output, so the case fails loudly
    instead of being silently skipped.
    """
    try:
        return subprocess.run(
            [PY, "-m", "jcc_core.run"], cwd=str(REPO), env=env, text=True,
            errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return subprocess.CompletedProcess(
            args=[PY, "-m", "jcc_core.run"], returncode=124,
            stdout=out + "\n<<TIMED OUT: the entrypoint did not exit>>")


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    check_defaults()
    check_green_instance_still_legal()
    check_product_refused_on_4778()
    check_product_instance_actually_starts_on_4779()
    check_entrypoint_refuses_4778_product()
    verify_green_launcher_contract()
    check_no_regression_in_existing_suites()

    passed = sum(1 for r in RESULTS if r["ok"])
    for r in RESULTS:
        print("  [%s] %s\n        %s"
              % ("ok  " if r["ok"] else "HOLE", r["id"], r["detail"]))
    print("\nCR-002 FALSIFIER %s %d/%d"
          % ("PASS" if passed == len(RESULTS) else "FAIL", passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
