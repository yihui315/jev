#!/usr/bin/env python
"""Verify the Wave-4 inbox contract against the LIVE product, over HTTP.

WHY THIS EXISTS
===============
Two defects were fixed in ``DomainStore`` and ``attention_control``:

1. **An approval-only blocker was invisible.** A mission that is still ``DRAFT``
   with a ``PENDING`` approval (exactly what the launch path produces when the
   spawned workload cannot be read back) did not appear in
   ``GET /api/v1/domain/needs-attention``, because that endpoint only queried
   ``missions.status = 'NEEDS_ATTENTION'`` -- a status the launch path
   deliberately never sets. The operator saw an empty inbox while the product
   was waiting on them.
2. **A mission could appear twice.** The obvious fix (append the approval as a
   second row) breaks the contract the whole surface is built on: ``HomeView``
   keys on ``mission_id``, ``NeedsAttentionView`` indexes resolutions in a ``Map``
   keyed by ``mission_id``, and ``Server._apply_attention`` decides by *mission*
   (``list_approvals(mission_id=mid, state="PENDING")[0]``). Two rows means a
   duplicated React key and a silently dropped decision. The rule is therefore
   **one row per mission**, with the approval merged into it.

The unit tests in ``packages/jcc_core/tests`` pin both rules against a store.
This tool pins them against the **shipped HTTP surface and the real database**,
because a unit test can pass while the projection layer drops the field -- the
exact class of defect this release has already been bitten by ("projection layer
carries every field by hand; a new key that is not added never reaches the
screen").

WHY TWO APPROVALS
=================
One approval only proves the row exists. Two prove the *merge*: the row must
still be single, and ``approval_id`` must be the one a decision would act on
(the newest pending), not the older one that happens to be scanned last.

WHAT IT REFUSES TO DO
=====================
* It refuses to run without a control token (a 401 is a correct answer, not a
  failure to report as a pass).
* It never claims a leg it did not observe: every check records the HTTP status
  and body it actually read, and a leg whose request failed is FAIL, not skipped.
* It creates only labelled test data (``JCC INBOX LIVE VERIFICATION (test)``),
  and it says so in the evidence rather than pretending the writes were free.

Usage::

    set JCC_CONTROL_TOKEN=<the token the API reads>
    F:\\jev\\.venv\\Scripts\\python.exe tools/v1_3_inbox_live_verification.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(errors="backslashreplace")
except Exception:  # pragma: no cover
    pass

BASE = os.environ.get("JCC_BASE", "http://127.0.0.1:4779")
GREEN_PORT = 4778
SCRATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       ".workbuddy", "scratch")
PROJECT_NAME = "JCC INBOX LIVE VERIFICATION (test)"
MISSION_TITLE = "inbox verification mission"
KIND = "LAUNCH_HOST_ACTIVATION"


# The proxy env injected into agent sessions has no NO_PROXY, so urllib would
# route loopback through it. Measured; the gate solves it the same way.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body=None, token: str | None = None):
    url = BASE + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-JCC-Token", token)
    try:
        with opener.open(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # connection refused, timeout, ...
        return None, "%s: %s" % (type(e).__name__, e)


def jload(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return None


def _as_items(raw: str):
    """The inbox is a bare JSON list; the home summary nests it. Accept both.

    Measured: ``GET /api/v1/domain/needs-attention`` returns a list, and reading
    it as ``{}.get("items")`` raises -- which is why this helper exists and why
    every caller routes through it rather than assuming a shape.
    """
    d = jload(raw)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("items", "needs_attention", "attention_items", "projects"):
            v = d.get(k)
            if isinstance(v, list):
                return v
    return []


def green_port_pid():
    """Who listens on the protected GREEN port? Read from netstat, not assumed."""
    try:
        p = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=30)
        out = p.stdout.decode("utf-8", "replace")
    except Exception as e:
        return None, "netstat failed: %s" % e
    for line in out.splitlines():
        if re.search(r":%d\s" % GREEN_PORT, line) and "LISTENING" in line:
            m = re.search(r"(\d+)\s*$", line.strip())
            if m:
                return int(m.group(1)), line.strip()
    return None, "no LISTENING line for %d" % GREEN_PORT


def main() -> int:
    legs = []
    evidence = {"base": BASE, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def leg(lid, ok, detail):
        legs.append({"id": lid, "ok": bool(ok), "detail": detail})

    token = os.environ.get("JCC_CONTROL_TOKEN")
    leg("A_CONTROL_TOKEN_IS_CONFIGURED", bool(token),
        "the token is read from the environment; it is never printed or stored")

    green_before, green_line = green_port_pid()
    evidence["green"] = {"pid_before": green_before, "netstat_line": green_line}

    # -- a 401 is the correct fail-closed answer, and it must be visible -------
    s, b = call("GET", "/api/v1/domain/needs-attention")
    leg("THE_INBOX_IS_READABLE_WITHOUT_A_TOKEN", s == 200,
        "GET /api/v1/domain/needs-attention -> %s" % s)

    if not token:
        return _finish(legs, evidence, note="no JCC_CONTROL_TOKEN in the environment")

    # -- setup ----------------------------------------------------------------
    # Reuse the labelled project from an earlier run when it is already there, so
    # repeated runs do not pile up test projects in the operator's database.
    s, b = call("GET", "/api/v1/domain/projects")
    proj = None
    for p in _as_items(b) if s == 200 else []:
        if p.get("name") == PROJECT_NAME:
            proj = p
            break
    reused = proj is not None
    if proj is None:
        s, b = call("POST", "/api/v1/domain/projects",
                    {"name": PROJECT_NAME, "root_path": os.path.dirname(SCRATCH)}, token)
        proj = jload(b) if s == 200 else None
    leg("SETUP_PROJECT_IS_AVAILABLE", bool(proj and proj.get("project_id")),
        "reused=%s POST /api/v1/domain/projects -> %s :: %s" % (reused, s, b[:200]))
    if not proj:
        return _finish(legs, evidence)
    pid = proj["project_id"]
    evidence["project_id"] = pid

    s, b = call("POST", "/api/v1/domain/missions",
                {"project_id": pid, "title": MISSION_TITLE,
                 "goal": "verify the Wave 4 inbox contract over HTTP",
                 "agent": "workbuddy", "working_directory": pid}, token)
    mission = jload(b) if s == 200 else None
    leg("SETUP_MISSION_IS_CREATED", bool(mission and mission.get("mission_id")),
        "POST /api/v1/domain/missions -> %s :: %s" % (s, b[:200]))
    if not mission:
        return _finish(legs, evidence)
    mid = mission["mission_id"]
    evidence["mission_id"] = mid

    approvals = []
    for i in (1, 2):
        s, b = call("POST", "/api/v1/domain/missions/%s/approvals" % mid,
                    {"kind": KIND, "reason": "inbox verification #%d" % i,
                     "requested_by": "v1_3_inbox_live_verification"}, token)
        a = jload(b) if s == 200 else None
        if a and a.get("approval_id"):
            approvals.append(a["approval_id"])
        time.sleep(1.1)  # distinct created_at, so "newest" is unambiguous
    evidence["approval_ids"] = approvals
    leg("SETUP_TWO_PENDING_APPROVALS_EXIST", len(approvals) == 2,
        "created %d of 2 :: kinds=%s" % (len(approvals), KIND))

    # -- the contract ---------------------------------------------------------
    s, b = call("GET", "/api/v1/domain/needs-attention")
    inbox = _as_items(b)
    evidence["inbox"] = inbox
    rows = [r for r in inbox if str(r.get("mission_id")) == mid]
    leg("THE_MISSION_IS_IN_THE_INBOX", len(rows) >= 1,
        "the approval-only blocker must not be invisible; rows=%d" % len(rows))
    leg("THE_MISSION_APPEARS_AT_MOST_ONCE", len(rows) == 1,
        "one row per mission is the contract the UI keys on; rows=%d" % len(rows))

    row = rows[0] if rows else {}
    leg("THE_ROW_CARRIES_AN_APPROVAL_ID", bool(row.get("approval_id")),
        "the inbox must name the approval a decision would act on; got %r"
        % row.get("approval_id"))
    leg("THE_ROW_NAMES_ITS_SOURCE",
        str(row.get("source") or "") in ("PENDING_APPROVAL",
                                         "MISSION_STATUS+PENDING_APPROVAL"),
        "source=%r" % row.get("source"))
    leg("THE_APPROVAL_ID_IS_THE_NEWEST_PENDING",
        bool(row.get("approval_id")) and row.get("approval_id") == approvals[-1],
        "must be the one list_approvals(...)[0] would return; row=%r newest=%r"
        % (row.get("approval_id"), approvals[-1] if approvals else None))

    s, b = call("GET", "/api/v1/domain/home")
    home = jload(b) or {}
    evidence["home"] = {k: v for k, v in home.items() if k != "attention_items"}
    home_items = home.get("attention_items") or []
    leg("HOME_AND_THE_INBOX_AGREE",
        int(home.get("needs_attention") or 0) == len(inbox)
        and [i.get("mission_id") for i in home_items] == [i.get("mission_id") for i in inbox],
        "home.needs_attention=%r inbox=%d; the Home count is the first number an "
        "operator reads and must not disagree with the list"
        % (home.get("needs_attention"), len(inbox)))

    # -- deciding it must clear the inbox, and must not pretend otherwise -----
    if row.get("approval_id"):
        s, b = call("POST", "/api/v1/domain/approvals/%s/decide" % row["approval_id"],
                    {"state": "APPROVED", "decided_by": "v1_3_inbox_live_verification"},
                    token)
        leg("THE_ADVERSARIAL_DECISION_IS_ACCEPTED", s == 200,
            "POST decide -> %s :: %s" % (s, b[:200]))
        time.sleep(0.3)
        s, b = call("GET", "/api/v1/domain/needs-attention")
        inbox2 = _as_items(b)
        left = [r for r in inbox2 if str(r.get("mission_id")) == mid]
        evidence["inbox_after_decision"] = left
        leg("AN_APPROVAL_THAT_OUTLIVES_IT_DOES_NOT_SURVIVE_A_DECISION",
            len(left) <= 1,
            "the surviving row is the older PENDING approval, still once; rows=%d" % len(left))

    green_after, green_line2 = green_port_pid()
    evidence["green"]["pid_after"] = green_after
    evidence["green"]["netstat_line_after"] = green_line2
    leg("GREEN_4778_WAS_NOT_TOUCHED", green_before == green_after and green_before,
        "pid %r -> %r" % (green_before, green_after))

    evidence["test_data_created"] = {
        "project": PROJECT_NAME, "mission": MISSION_TITLE,
        "approvals": approvals, "note": "labelled test data; retained by design",
    }
    return _finish(legs, evidence)


def _finish(legs, evidence, note=None):
    evidence["legs"] = legs
    passed = sum(1 for l in legs if l["ok"])
    evidence["verdict"] = "%s %d/%d" % ("PASS" if passed == len(legs) else "FAIL",
                                       passed, len(legs))
    if note:
        evidence["note"] = note
    evidence["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(SCRATCH, exist_ok=True)
    p = os.path.join(SCRATCH, "v1_3_inbox_live_verification_%s.json"
                     % time.strftime("%Y%m%d-%H%M%S"))
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(evidence, fh, indent=2)
    print(json.dumps(evidence, indent=2))
    print("\nEVIDENCE %s" % p)
    return 0 if passed == len(legs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
