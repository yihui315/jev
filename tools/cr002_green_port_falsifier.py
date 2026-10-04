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
import time
from pathlib import Path
from typing import Dict

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
        "DEFAULT_CONFIG_PATH=%s -> port=%s (GREEN_PROTECTED_PORT=%d, "
        "is_green_instance=%s)"
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
        proc_out, still_running = _run_until_bound(
            fresh_env(JCC_CONFIG=str(cfg)), tmp)
        bound_ok = ("starting on 127.0.0.1:%d" % port) in proc_out
        record(
            "PRODUCT_ENTRYPOINT_STARTS_ON_ITS_OWN_PORT",
            bound_ok and "4778" not in proc_out and still_running,
            "bound_on_%d=%s mentions_4778=%s still_running=%s (a server that "
            "stays up is the success signal here) | %s"
            % (port, bound_ok, "4778" in proc_out, still_running,
               (proc_out.strip().splitlines() or ["<no output>"])[-1][:120]),
        )
    finally:
        _rmtree(tmp)


def check_product_instance_really_serves_health() -> None:
    """The socket is the fact, not the log line.

    The case above reads what the server *says* about itself. This one opens a
    TCP connection to the port the product instance was configured with and asks
    ``/api/health`` for a 200. "It logged that it started" and "it is accepting
    connections and serving the product" are different claims, and only the
    second is what a user's browser would experience.
    """
    import urllib.error
    import urllib.request

    port = _free_port()
    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr002_serve_"))
    proc = None
    log_path = tmp / "entrypoint.log"
    try:
        cfg = tmp / "product.yaml"
        cfg.write_text(
            "server:\n  host: 127.0.0.1\n  port: %d\n"
            "state_store:\n  db_path: %s\n" % (port, (tmp / "jcc.db").as_posix()),
            encoding="utf-8")
        proc = _spawn_entrypoint(fresh_env(JCC_CONFIG=str(cfg)), log_path)

        deadline = time.time() + 30
        http = None
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/health" % port, timeout=3) as resp:
                    http = resp.status
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)

        record(
            "PRODUCT_INSTANCE_SERVES_HEALTH_ON_ITS_OWN_PORT",
            http == 200 and port != 4778,
            "GET http://127.0.0.1:%d/api/health -> %s (port != 4778: %s)"
            % (port, http, port != 4778),
        )
    finally:
        if proc is not None:
            _terminate_tree(proc)
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
        proc = _run_until_exit(fresh_env(JCC_CONFIG=str(cfg)), tmp, seconds=25)
        combined = proc.stdout or ""
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


def check_type_confused_ports_are_all_refused() -> None:
    """A QUOTED 4778 must not slip past the guard (independently found 2026-10-04).

    ``ServerConfig`` is a bare dataclass and ``load_config`` splats
    ``**data["server"]`` into it, so the YAML TYPE survives to the bind. The
    original guard compared ``config.server.port`` to ``GREEN_PROTECTED_PORT``
    with ``!=``, so ``port: '4778'`` (a str) passed the ``!=`` test and went on
    to bind GREEN's port. The impostor then answers /api/health with 200, exactly
    like the baseline, so the failure is invisible from outside.

    This is a SEPARATE case from ``check_product_refused_on_4778`` because that
    one only ever built an int. It is the case that would have caught the defect.
    """
    from jcc_core.config import GREEN_PROTECTED_PORT, load_config
    from jcc_core.run import check_green_port_ownership

    sq, dq = chr(39), chr(34)
    tmp = Path(tempfile.mkdtemp(prefix="jcc_cr002_types_"))
    try:
        cases = [("4778", "int"), (sq + "4778" + sq, "str"),
                 (dq + "4778" + dq, "str"), ("4778.0", "float"),
                 (sq + " 4778 " + sq, "padded-str")]
        refused: Dict[str, bool] = {}
        types: Dict[str, str] = {}
        for literal, label in cases:
            cfg_file = tmp / ("c_%s.yaml" % label)
            cfg_file.write_text(
                "server:" + chr(10) + "  host: 127.0.0.1" + chr(10)
                + "  port: " + literal + chr(10), encoding="utf-8")
            cfg = load_config(str(cfg_file))
            types[label] = type(cfg.server.port).__name__
            try:
                check_green_port_ownership(cfg)
                refused[label] = False
            except SystemExit:
                refused[label] = True
        # The str cases are the regression; the int case is the positive control
        # that proves the harness can still recognise a refusal.
        ok = all(refused.values()) and types.get("str") == "str"
        record(
            "EVERY_SPELLING_OF_4778_IS_REFUSED_INCLUDING_QUOTED",
            ok,
            "refused=%r types=%r (a quoted 4778 arriving as %r is the bypass)"
            % (refused, types, types.get("str")),
        )
    finally:
        _rmtree(tmp)


def check_green_port_resolution_is_not_default_dependent() -> None:
    """GREEN's port must be read from GREEN's config, never from the default.

    CR-002 changed ``DEFAULT_CONFIG_PATH`` from ``config/jcc.yaml`` to the v1.3
    product config. ``Get-JccPort`` used to call ``load_config()`` with no
    ``JCC_CONFIG`` set, so it silently began returning 4779 while GREEN still
    bound 4778 -- ``start.ps1`` then polled 4779 for a service on 4778 and timed
    out, and the watchdog would have supervised the wrong port entirely.

    The PowerShell side is verified by running JccCommon.ps1 itself; this case
    asserts the PYTHON precondition that made the bug possible, so a future
    default change is caught even if nobody re-runs the shell probe.
    """
    from jcc_core import config as cfg_mod
    from jcc_core.config import GREEN_PROTECTED_PORT, load_config

    default_name = Path(cfg_mod.DEFAULT_CONFIG_PATH).name
    without_config = load_config()
    record(
        "GREEN_PORT_MUST_NOT_BE_RESOLVABLE_FROM_THE_DEFAULT",
        default_name != "jcc.yaml"
        and without_config.server.port != GREEN_PROTECTED_PORT,
        "DEFAULT_CONFIG_PATH=%s -> %s; GREEN's 4778 is reachable ONLY by naming "
        "config/jcc.yaml (so a GREEN tool must name it, not rely on the default)"
        % (default_name, without_config.server.port),
    )


def check_no_regression_in_existing_suites() -> None:
    """The two suites that encode the config/entrypoint contract must stay green."""
    env = fresh_env(CODEBUDDY_SAFE_DELETE_ENABLED="0",
                    JCC_PYTEST_ALLOW_DIRTY_ENV="1")
    # A PER-RUN basetemp, and this is not cosmetic. `tools/conftest.py:29`
    # computes the basetemp as a FIXED path (`<tmp>/jev-pytest`) and creates it
    # with `mkdir(parents=True, exist_ok=False)`, so the second run against a
    # leftover directory dies with
    #   FileExistsError: [WinError 183] ... '.workbuddy\scratch\host_temp\jev-pytest'
    # on EVERY test -- 32 errors that look exactly like a product regression and
    # are nothing of the kind. Passing a unique --basetemp keeps this suite
    # measuring the product instead of the previous run's leftovers.
    basetemp = Path(tempfile.mkdtemp(prefix="jcc_cr002_pytest_"))
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest",
             "packages/jcc_core/tests/test_config_failclosed.py",
             "packages/jcc_core/tests/test_server_surface.py",
             "-q", "-p", "no:cacheprovider",
             "--basetemp", str(basetemp / "run")],
            cwd=str(REPO), env=env, text=True, errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:]
        # A FileExistsError from the conftest basetemp is an ARTEFACT of the
        # harness, not a product failure. Naming it distinctly keeps a real
        # regression from hiding behind it.
        collided = "jev-pytest" in (proc.stdout or "") and "FileExistsError" in (
            proc.stdout or "")
        record(
            "EXISTING_CONFIG_AND_SERVER_SUITES_STILL_PASS",
            proc.returncode == 0 and not collided,
            "rc=%s basetemp_collision=%s | %s"
            % (proc.returncode, collided, tail[0][:160] if tail else ""),
        )
    finally:
        _rmtree(basetemp)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _spawn_entrypoint(env: dict, log_path: Path) -> subprocess.Popen:
    """Start the entrypoint with its output going to a FILE, not a pipe.

    A pipe is the wrong tool here: the child is killed while running, and a
    killed child's pipe buffer is lost, so an earlier version of this suite read
    "<no output>" for a server that had in fact started and bound correctly. A
    file outlives the kill, so the evidence survives.
    """
    handle = open(log_path, "w", encoding="utf-8", errors="replace")
    return subprocess.Popen(
        [PY, "-m", "jcc_core.run"], cwd=str(REPO), env=env, text=True,
        errors="replace", stdout=handle, stderr=subprocess.STDOUT)


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Stop ONLY the process tree we started, by PID.

    The venv ``python.exe`` is a redirector stub on this host (see the repo's own
    comments in ``continuation-watchdog.ps1``), so the pid Popen reports may not
    be the process that actually holds the socket. Hence ``/T`` to take the child
    with it. This is deliberately PID-scoped: a global ``taskkill /IM python.exe``
    is forbidden by the Windows-safe-ops rule, and would take down the GREEN
    baseline along with the fixture.
    """
    if proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except (OSError, subprocess.SubprocessError):
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def _drain(log_path: Path) -> str:
    """Return whatever the child has written to its log so far."""
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _run_until_bound(env: dict, workdir: Path, seconds: float = 25.0) -> tuple:
    """Start the real entrypoint; return (log_text, still_running_at_deadline).

    A server does not exit, so "the process was still alive when the deadline
    arrived" is the SUCCESS signal, not a timeout error. Treating a timeout as
    failure here (an earlier version of this suite did) makes a correctly
    started server look like a broken one -- so the caller gets the fact, not a
    verdict.
    """
    log_path = workdir / "entrypoint.log"
    proc = _spawn_entrypoint(env, log_path)
    try:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                return _drain(log_path), False
            time.sleep(0.5)
        return _drain(log_path), proc.poll() is None
    finally:
        _terminate_tree(proc)
        time.sleep(0.3)  # let the OS flush the child's last writes


def _run_until_exit(env: dict, workdir: Path, seconds: float = 25.0
                    ) -> subprocess.CompletedProcess:
    """Run the entrypoint expecting it to EXIT (a refusal must not linger).

    A refusal that has to be killed is a failure: it means the guard did not stop
    the process before it started serving.
    """
    log_path = workdir / "entrypoint.log"
    proc = _spawn_entrypoint(env, log_path)
    try:
        proc.wait(timeout=seconds)
        return subprocess.CompletedProcess(args=proc.args, returncode=proc.returncode,
                                           stdout=_drain(log_path))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=proc.args, returncode=124,
            stdout=_drain(log_path)
            + "\n<<DID NOT EXIT: the entrypoint kept running>>")
    finally:
        _terminate_tree(proc)
        time.sleep(0.3)


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
    check_type_confused_ports_are_all_refused()
    check_green_port_resolution_is_not_default_dependent()
    check_product_instance_actually_starts_on_4779()
    check_product_instance_really_serves_health()
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
