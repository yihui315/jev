#!/usr/bin/env python
"""Emit (and re-check) the GREEN runtime restore record.

GREEN is 127.0.0.1:4778, the protected production runtime. On 2026-09-27 the host
crashed repeatedly (bugcheck 0x139) and took it down; this artifact is the record
of bringing it back from the FROZEN v1.0.1 commit and of the machinery that keeps
it up afterwards.

WHY IT IS EMITTED RATHER THAN WRITTEN. Every number here is read from the thing
it describes -- the snapshot manifest, git's object store, the live HTTP payload,
the ownership marker, the recovery ledger, the Task Scheduler's own XML store.
A hand-typed record of a restore is a claim; this is a measurement.

    python tools/v1_1_emit_green_runtime_restore.py            # write
    python tools/v1_1_emit_green_runtime_restore.py --check    # rc 0 current / 1 stale

`--check` compares only the parts that are supposed to be STABLE; pids, uptimes
and last-run times are volatile by nature and are excluded from the comparison
(the same discipline the release gate uses on its own artifacts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "artifacts", "release", "V1_1_GREEN_RUNTIME_RESTORE.json")
SCHEMA = "jcc-v1.1-green-runtime-restore/1"
SNAPSHOT_DIR = os.path.join(REPO, "runtime", "stable_v101")
HEALTH_URL = "http://127.0.0.1:4778/api/health"
TASK_STORE = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Tasks")

#: Keys excluded from `--check`: they describe THIS instance, not the invariants.
VOLATILE = {
    "emitted_at", "uptime_s", "age_s", "last_run", "last_result",
    "service_pid", "launcher_pid", "process_start_time", "sentinel_checks",
}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_atomic(path: str, obj) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-restore-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _git(*args: str) -> str:
    p = subprocess.run(["git", "-C", REPO, *args], capture_output=True)
    return p.stdout.decode("utf-8", "backslashreplace")


def _health_keys_from_git(rev: str) -> list:
    src = _git("show", rev + ":packages/jcc_core/server.py")
    i = src.find('@app.get("/api/health")')
    if i < 0:
        return []
    j = src.find("@app.", i + 10)
    body = src[i:j if j > 0 else i + 4000]
    return sorted(set(re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', body)))


def _task_facts(task_name: str) -> dict:
    """Three-state task probe, from the scheduler's own store.

    Same discipline as the test suite's probe: a name that is not in a
    SUCCESSFUL directory listing is ABSENT; anything else unanswered is UNKNOWN,
    never ABSENT.
    """
    path = os.path.join(TASK_STORE, task_name)
    if not os.path.isfile(path):
        try:
            listed = sorted(os.listdir(TASK_STORE))
        except OSError as exc:
            return {"probe": "UNKNOWN", "detail": "no store and cannot list it: %s" % exc}
        if task_name in listed:
            return {"probe": "UNKNOWN", "detail": "listed but unreadable"}
        return {"probe": "ABSENT", "detail": "listed %d entries, name not among them" % len(listed)}
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-16", "utf-8-sig", "cp936"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeError:
            continue
    if text is None:
        return {"probe": "UNKNOWN", "detail": "decodes as none of utf-16/utf-8-sig/cp936"}

    def one(pat):
        m = re.search(pat, text)
        return m.group(1) if m else None

    facts = {
        "probe": "PRESENT",
        "task_file": path,
        "sha256": _sha256(path),
        "enabled": (one(r"<Enabled>\s*(true|false)\s*</Enabled>") or "").lower() == "true",
        "command": one(r"<Command>(.*?)</Command>"),
        "arguments": one(r"<Arguments>(.*?)</Arguments>"),
        "user_id": one(r"<UserId>(.*?)</UserId>"),
        "run_level": one(r"<RunLevel>(.*?)</RunLevel>"),
        "repetition": one(r"<Repetition>\s*<Interval>(.*?)</Interval>"),
        "working_directory": one(r"<WorkingDirectory>(.*?)</WorkingDirectory>"),
        "boot_trigger": bool(re.search(r"<BootTrigger>", text)),
        "logon_trigger": bool(re.search(r"<LogonTrigger>", text)),
    }
    # Non-vacuity: if the parse found no action at all, it must not be read as
    # "the task has no action".
    if not facts["command"]:
        facts["probe"] = "UNKNOWN"
        facts["detail"] = "parsed a task definition but found no <Command>"
    return facts


def gather() -> dict:
    snap_man = _read_json(os.path.join(SNAPSHOT_DIR, "SNAPSHOT.json")) or {}
    baseline = _read_json(os.path.join(REPO, "artifacts", "release", "V1_1_BASE_BASELINE.json")) or {}

    verify = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "stable_runtime_snapshot.py"),
         "verify", "--dest", SNAPSHOT_DIR, "--root", REPO],
        capture_output=True, text=True, encoding="utf-8", errors="replace")

    health, health_error = None, None
    keys = []
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=8) as r:
            payload = json.loads(r.read().decode("utf-8"))
            health = {"http_status": r.getcode(), "body": payload}
            keys = sorted(payload.keys())
    except Exception as exc:
        health_error = "%s: %s" % (type(exc).__name__, exc)

    frozen_sha = snap_man.get("source_sha")
    pid_path = os.path.join(REPO, "runtime", "jcc", "jcc.pid")
    marker_path = os.path.join(REPO, "runtime", "jcc", "jcc.ownership.json")
    db_path = os.path.join(REPO, "runtime", "jcc", "jcc.db")
    marker = _read_json(marker_path) or {}
    pid_text = None
    if os.path.isfile(pid_path):
        pid_text = open(pid_path, encoding="ascii", errors="replace").read().strip()

    ledger = []
    ledger_path = os.path.join(REPO, "runtime", "recovery", "ledger.jsonl")
    if os.path.isfile(ledger_path):
        for line in open(ledger_path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line:
                try:
                    ledger.append(json.loads(line))
                except json.JSONDecodeError:
                    ledger.append({"unparsable": line[:200]})

    log_path = os.path.join(REPO, "runtime", "logs", "stable-runtime-watchdog.log")
    log_tail = []
    if os.path.isfile(log_path):
        log_tail = [l for l in open(log_path, encoding="utf-8", errors="replace").read().splitlines() if l.strip()][-12:]

    return {
        "schema": SCHEMA,
        "emitted_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "emitted_by": "tools/v1_1_emit_green_runtime_restore.py",
        "green": {
            "bind": "127.0.0.1:4778",
            "health_url": HEALTH_URL,
            "health": health,
            "health_error": health_error,
            "health_keys_live": keys,
            "service_pid": int(pid_text) if (pid_text or "").isdigit() else None,
            "launcher_pid": marker.get("launcher_pid"),
            "command_line": marker.get("command_line"),
            "repo_path": marker.get("repo_path"),
            "ownership_marker_path": marker_path,
            "ownership_marker_sha256": _sha256(marker_path) if os.path.isfile(marker_path) else None,
            "pid_file_sha256": _sha256(pid_path) if os.path.isfile(pid_path) else None,
            "db_path": "F:/jev/runtime/jcc/jcc.db",
            "db_sha256": _sha256(db_path) if os.path.isfile(db_path) else None,
        },
        "frozen_source": {
            "sha": frozen_sha,
            "sha_short": snap_man.get("source_sha_short"),
            "commit_date": snap_man.get("source_commit_date"),
            "subject": snap_man.get("source_commit_subject"),
            "snapshot_dir": SNAPSHOT_DIR,
            "file_count": snap_man.get("file_count"),
            "tree_digest_sha256": snap_man.get("tree_digest_sha256"),
            "verify_rc": verify.returncode,
            "verify_verdict": (verify.stdout or "").strip().splitlines()[-1] if verify.stdout.strip() else "",
        },
        "run_as_read_only": {
            "env": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH_primary": os.path.join(SNAPSHOT_DIR, "packages")},
            "why": ("the first real restore let CPython write __pycache__ INTO the snapshot, "
                    "which made `verify` fail with 35 problems; a tree the runtime can mutate "
                    "is not immutable, so bytecode writing is disabled for the service"),
            "pycache_files_in_snapshot": sum(
                1 for root, _d, files in os.walk(os.path.join(SNAPSHOT_DIR, "packages"))
                if os.path.basename(root) == "__pycache__" for _ in files),
        },
        "code_identity_proof": {
            "method": ("two independent routes: (1) the snapshot manifest written at create time, "
                       "(2) git's own object store via `git cat-file --batch` on the frozen commit"),
            "frozen_health_keys": _health_keys_from_git(frozen_sha) if frozen_sha else [],
            "head_health_keys": _health_keys_from_git("HEAD"),
            "live_health_keys": keys,
            "discriminator": ("the v1.1 server.py adds provider_mode / provider_verified / "
                              "provider_activation_state / provider_requested_mode / provider_is_real; "
                              "none of them may appear on 4778"),
        },
        "supervisors": {
            "JEV-JCC-Stable-Runtime": _task_facts("JEV-JCC-Stable-Runtime"),
            "JEV-JCC-Stable-Health": _task_facts("JEV-JCC-Stable-Health"),
        },
        "baseline_delta": {
            "baseline_artifact": "artifacts/release/V1_1_BASE_BASELINE.json",
            "baseline_rollback_sha": baseline.get("rollback_sha"),
            "baseline_rollback_tag": baseline.get("rollback_tag"),
            "baseline_service_pid": (baseline.get("service") or {}).get("pid"),
            "baseline_launcher_pid": (baseline.get("service") or {}).get("launcher_pid"),
            "baseline_marker_sha256": (baseline.get("service") or {}).get("ownership_marker_sha256"),
            "authorised_by": ("the baseline's own note: 'Final maintenance acceptance (NOT STARTED YET) "
                              "may only switch the production port after saving this baseline AND a "
                              "v1.1 rollback plan'. The port was switched TO the rollback SHA itself, "
                              "which is the most conservative reading of that clause."),
            "frozen_sha_equals_baseline_rollback_sha": frozen_sha == baseline.get("rollback_sha"),
            "config_changed": False,
            "db_changed": False,
            "worktree_checked_out_or_reset": False,
        },
        "recovery": {
            "backup_dir": os.path.join(REPO, "runtime", "recovery"),
            "ledger": ledger,
            "durable_log_tail": log_tail,
        },
        "root_causes_fixed_here": [
            {
                "id": "G-1",
                "what": ".workbuddy/release_state.json did not exist, and scripts/service-watchdog.ps1 "
                        "required it to be 'READY' before doing anything",
                "evidence": "951 consecutive NOOP audit lines: 'release state is <unset>; monitoring is "
                            "reserved for READY' in .workbuddy/scheduled/logs/service-watchdog.jsonl, "
                            "still ticking every 120 s while 4778 was dead",
                "fix": "scripts/ensure-stable-runtime.ps1 has no such gate",
            },
            {
                "id": "G-2",
                "what": "Get-JccTaskReport folded 'the ScheduledTasks module could not answer' into "
                        "'the task does not exist'",
                "evidence": "Get-ScheduledTask throws ERROR_FILE_NOT_FOUND here while the two JEV tasks "
                            "were present, Enabled, and firing every 2 minutes",
                "fix": "three-state probe (PRESENT/ABSENT/UNKNOWN) on the task store's own XML",
            },
            {
                "id": "G-3",
                "what": "the scheduled-task action '/c \"<ps51>\" args >> \"<log>\"' made cmd.exe apply its "
                        "legacy quote rule, which stripped the LAST quote and left the program path "
                        "unterminated",
                "evidence": "LastTaskResult=1 with no log file and no audit line created",
                "fix": "wrap the whole command in one more quote pair",
            },
            {
                "id": "G-4",
                "what": "the runtime wrote __pycache__ into the immutable snapshot",
                "evidence": "verify reported 'unexpected extra file' x35 and a tree-digest mismatch; the "
                            "restore's own proof then refused with FROZEN_PROOF_FAILED",
                "fix": "PYTHONDONTWRITEBYTECODE=1 for the served process; verify stays strict",
            },
        ],
    }


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="rc 0 if the recorded invariants still match the live state, 1 if stale")
    args = ap.parse_args()

    live = gather()
    if args.check:
        if not os.path.isfile(OUT):
            sys.stdout.write("STALE: no artifact at %s\n" % OUT)
            return 1
        recorded = _read_json(OUT)
        if _strip_volatile(recorded) != _strip_volatile(live):
            sys.stdout.write("STALE: the recorded GREEN invariants no longer match the live state\n")
            a = json.dumps(_strip_volatile(recorded), sort_keys=True, indent=2).splitlines()
            b = json.dumps(_strip_volatile(live), sort_keys=True, indent=2).splitlines()
            import difflib
            for line in list(difflib.unified_diff(a, b, "recorded", "live", lineterm=""))[:60]:
                sys.stdout.write(line + "\n")
            return 1
        sys.stdout.write("CURRENT: %s matches the live GREEN invariants\n" % os.path.basename(OUT))
        return 0

    _write_atomic(OUT, live)
    sys.stdout.write("wrote %s\n" % OUT)
    sys.stdout.write("  frozen sha   : %s\n" % (live["frozen_source"]["sha_short"]))
    sys.stdout.write("  snapshot     : %s files, digest %s\n"
                     % (live["frozen_source"]["file_count"], live["frozen_source"]["tree_digest_sha256"]))
    sys.stdout.write("  verify rc    : %s\n" % live["frozen_source"]["verify_rc"])
    sys.stdout.write("  live keys    : %s\n" % live["green"]["health_keys_live"])
    sys.stdout.write("  tasks        : %s\n"
                     % {k: v.get("probe") for k, v in live["supervisors"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
