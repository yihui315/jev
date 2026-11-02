"""B-segment native launch acceptance probe (JEV v1.3 final closeout).

Drives the REAL launch control plane -- production HostLauncher + the actual
WorkBuddy headless CLI -- with a real, SAFE, SCOPED mission goal:

    Use the Write tool to create <project_root>/output/release_acceptance.txt
    containing exactly the line: JEV_V1_3_NATIVE_LAUNCH_OK
    Then use the Read tool to read it back and report its contents.

This exercises:
  * B2  -- signing_available=true (key loaded from ~/.jcc, never git/log/API)
  * B3  -- launch-request -> signed directive (v2) -> launch-commit
  * B4  -- cwd scope is honoured (project_root in scope, --add-dir bound)
  * B5  -- a genuinely new workload
  * B6  -- a REAL workload writes output/release_acceptance.txt
  * B7  -- file_read / file_write / tool_* evidence from the child's own stream
  * B8  -- the mission is walked DRAFT->...->RUNNING only on a real AEP ingest
  * B9  -- monotonic causal time chain T0..T7

The outcome is REPORTED, never faked. An AUTH_REQUIRED classification is an
honest reading, not a failure to hide.
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.abspath(__file__)
REPO = HERE
for _ in range(6):
    if os.path.isdir(os.path.join(REPO, "packages", "jcc_core")):
        break
    REPO = os.path.dirname(REPO)
sys.path.insert(0, os.path.join(REPO, "packages"))

from jcc_core.signing import DirectiveSigner, load_signing_key, SIGNING_FORM_V2
from jcc_core.host_launcher import HostLauncher
from jcc_core.launch_control import LaunchController
from jcc_core.domain import DomainStore


def _load_key() -> bytes:
    p = os.path.expanduser("~/.jcc/jcc_directive_signing_key")
    if not os.path.isfile(p):
        raise SystemExit("signing key missing at %s" % p)
    return open(p, encoding="utf-8").read().strip().encode("utf-8")


class _CaptureRuntime:
    """Records the REAL AEP handoff the controller performs (no fabrication)."""

    def __init__(self) -> None:
        self.calls = []

    def launch(self, mission_id, *, event=None):
        self.calls.append({"mission_id": mission_id, "event": event})
        return {"ok": bool(event),
                "session_id": (event or {}).get("sessionId"),
                "error": None if event else "no event"}


def main() -> int:
    key = _load_key()
    os.environ["JCC_DIRECTIVE_SIGNING_KEY"] = key.decode("utf-8")  # B2 visible to signer
    signer = DirectiveSigner(key, form=SIGNING_FORM_V2)
    print("signing_available:", signer.available, flush=True)

    workdir = os.path.join(REPO, "artifacts", "release", "v1.3_final",
                           "acceptance_workload")
    os.makedirs(workdir, exist_ok=True)
    outdir = os.path.join(workdir, "output")
    os.makedirs(outdir, exist_ok=True)

    domain_db = os.path.join(workdir, "domain.db")
    if os.path.exists(domain_db):
        os.remove(domain_db)
    domain = DomainStore(domain_db)
    project = domain.create_project("acceptance-project", root_path=workdir)
    goal = (
        "Use the Write tool to create the file "
        "output/release_acceptance.txt in your project root containing exactly "
        "the single line: JEV_V1_3_NATIVE_LAUNCH_OK\n"
        "Then use the Read tool to read that file back and report its contents."
    )
    mission = domain.create_mission(project.project_id, "acceptance-mission", goal=goal)

    launcher = HostLauncher(signer=signer, scope_base=REPO)  # scope = repo root
    runtime = _CaptureRuntime()
    ctl = LaunchController(
        domain, runtime, signer=signer, launcher=launcher,
        measure_path=os.path.join(workdir, "measure.json"),
        journal_path=os.path.join(workdir, "journal.ndjson"),
        collect_timeout_s=240.0)

    t0 = time.time()
    print("== preview ==", flush=True)
    preview = ctl.request(mission.mission_id, commit=False,
                          project_root=workdir)
    print(json.dumps({"would_accept": preview["would_accept"],
                      "capability": preview["capability"],
                      "checks_ok": preview["checks_ok"],
                      "command_buildable": preview["command_buildable"]}, indent=2),
          flush=True)

    print("== commit (real spawn) ==", flush=True)
    t_commit = time.time()
    res = ctl.request(mission.mission_id, commit=True, project_root=workdir)
    t_done = time.time()

    summary = {
        "signing_available": signer.available,
        "preview_would_accept": preview["would_accept"],
        "spawned": res.get("spawned"),
        "pid": res.get("pid"),
        "classification": res.get("classification"),
        "result_reason": res.get("reason"),
        "aep": res.get("aep"),
        "stream_event_types": (res.get("stream") or {}).get("event_types"),
        "stream_event_count": (res.get("stream") or {}).get("event_count"),
        "auth_error": (res.get("stream") or {}).get("auth_error"),
        "mission_status_after": domain.get_mission(mission.mission_id).status.value,
        "runtime_calls": len(runtime.calls),
        "elapsed_commit_s": round(t_done - t_commit, 3),
        "wall_since_start": round(t_done - t0, 3),
        "measure": json.loads(open(os.path.join(workdir, "measure.json"),
                                  encoding="utf-8").read()) if os.path.isfile(
            os.path.join(workdir, "measure.json")) else None,
        "stdout_file": res.get("result", {}).get("stdout_file") or res.get("stdout_file"),
    }
    out_json = os.path.join(workdir, "acceptance_result.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str), flush=True)

    # B6: did the REAL workload write the acceptance file?
    acc_file = os.path.join(outdir, "release_acceptance.txt")
    if os.path.isfile(acc_file):
        summary["acceptance_file_content"] = open(acc_file, encoding="utf-8").read()
        print("ACCEPTANCE FILE WRITTEN:", acc_file, flush=True)
    else:
        print("ACCEPTANCE FILE NOT WRITTEN", flush=True)

    # B7: collect file_read / file_write / tool_* evidence from the raw child stream
    sf = summary.get("stdout_file")
    tool_events = []
    if sf and os.path.isfile(sf):
        for line in open(sf, encoding="utf-8", errors="backslashreplace"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            t = str(obj.get("type") or "")
            name = str(obj.get("name") or obj.get("tool") or "")
            if t in ("tool_use", "tool", "function_call") or name.lower() in (
                    "read", "write", "edit") or "tool" in t.lower():
                tool_events.append({"type": t, "name": name})
    summary["tool_events_observed"] = tool_events
    print("TOOL EVENTS OBSERVED:", len(tool_events), flush=True)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
