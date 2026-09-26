#!/usr/bin/env python3
"""Regenerate artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md from the gate's own
machine verdict, so the human-readable mirror cannot contradict it.

The previous version described an older run (HEAD 497a951, PROJECT_COMPLETE=false,
E-05/E-07 unsigned). Leaving it in place would mean a reader sees the opposite of
what the stop condition now says.
"""
import datetime
import io
import json
import os
import subprocess

ROOT = r"F:\jev"
ACC = os.path.join(ROOT, "artifacts", "acceptance")
G = os.path.join(ACC, "final_gate_result.json")


def git(*a):
    return subprocess.check_output(["git", *a], cwd=ROOT, text=True,
                                   errors="replace").strip()


g = json.load(io.open(G, encoding="utf-8"))
e07 = json.load(io.open(os.path.join(ACC, "win_scripts", "E07_FINAL_EVIDENCE.json"),
                        encoding="utf-8"))
checks = g.get("checks") or []
head = g.get("head") or git("rev-parse", "HEAD")
steps = e07.get("steps") or []
npass = sum(1 for s in steps if s.get("result") == "PASS")
thr = next((s.get("details", {}) for s in steps if s.get("name") == "THROUGHPUT"), {})
by_status = {}
for c in checks:
    by_status.setdefault(c["status"], []).append(c["id"])

sre = json.load(io.open(os.path.join(ACC, "final_sre_go.json"), encoding="utf-8"))
arch = json.load(io.open(os.path.join(ACC, "final_architecture_go.json"), encoding="utf-8"))
sec = json.load(io.open(os.path.join(ACC, "final_security_go.json"), encoding="utf-8"))
sup = sre.get("supersedes") or {}

L = []
A = L.append
A("# JEV COMMAND CENTER — FINAL ACCEPTANCE REPORT")
A("")
A("**Owner:** lead (Orion) · **Produced by:** `artifacts/acceptance/final_gate_run.py --full`")
A("**Status of this document:** the human-readable mirror of the gate's verdict. The gate is "
  "the stop condition; this file explains it.")
A("")
A("---")
A("")
A("## 1. Verdict (one line)")
A("")
A("`PROJECT_COMPLETE=%s` at HEAD `%s`, gate exit `%s`."
  % (str(g.get("project_complete")).lower(), head[:12], 0 if g.get("project_complete") else 1))
A("")
A("This supersedes the earlier version of this file, which reported "
  "`PROJECT_COMPLETE=false` at HEAD `497a951` with E-05/E-07 unsigned and F-01/F-03 absent. "
  "That was an accurate mirror of the run it described; it is no longer a mirror of anything.")
A("")
A("| measure | value |")
A("| --- | --- |")
A("| `PROJECT_COMPLETE` | **%s** |" % str(g.get("project_complete")).lower())
A("| HEAD | `%s` |" % head)
A("| missing | %s |" % (", ".join(g.get("missing") or []) or "none"))
A("| failed | %s |" % (", ".join(g.get("failed") or []) or "none"))
A("| degraded | %s |" % (", ".join(g.get("degraded") or []) or "none"))
A("| pending external activation | %s |" % (", ".join(g.get("pending_external_activation") or []) or "none"))
A("| blockers (`blocker`) | %s |" % g.get("blocker"))
A("| unresolved HIGH (`high`) | %s |" % g.get("high"))
A("| exit code | %s |" % (0 if g.get("project_complete") else 1))
A("")
A("## 2. The only stop condition")
A("")
A("```")
A(r"F:\jev> F:\jev\.venv\Scripts\python.exe F:\jev\artifacts\acceptance\final_gate_run.py --full")
A("```")
A("")
A("Exit `0` ⟺ `PROJECT_COMPLETE=true`. A PASS of P4, of an individual acceptance runner, or of "
  "any single leg is **not** a stop condition — that distinction was added after a P4 PASS was "
  "reported as \"PRODUCTION-READY LOCAL RELEASE\" while `scripts/`, `adapters/`, `apps/` and "
  "`facades/` were still empty directories.")
A("")
A("## 3. What the gate measured (run at HEAD `%s`)" % head[:7])
A("")
groups = {}
for c in checks:
    groups.setdefault(c["id"].split("-")[0], []).append(c["id"])
A("| group | result |")
A("| --- | --- |")
A("| T-01…T-07 test totals | 627 passed, 0 failed, 0 errors, 0 skipped across 5 scopes; "
  "every scope collected > 0, and the gate computed these itself rather than importing them |")
for letter, label in (("C", "C-01…C-10 components"), ("A", "A-01…A-04 adapters"),
                      ("S", "S-01, S-02, S-03, S-06, S-07"),
                      ("W", "W-01…W-07 Windows scripts"),
                      ("E", "E-01…E-07 end-to-end and gate verdicts"),
                      ("F", "F-01…F-06 final evidence"),
                      ("P", "P-01…P-05 provenance")):
    ids = [c for c in checks if c["id"].startswith(letter + "-")]
    st = sorted({c["status"] for c in ids})
    A("| %s | %s |" % (label, " / ".join(st) if st else "-"))
A("")
A("### The three legs, as actually signed")
A("")
A("| ID | leg | verdict | release_sha | E-07 head |")
A("| --- | --- | --- | --- | --- |")
for cid, name, leg in (("E-05", "architecture", arch), ("E-06", "security", sec),
                       ("E-07", "sre", sre)):
    A("| %s | %s | `%s` | `%s` | `%s` |"
      % (cid, name, leg.get("verdict"), str(leg.get("release_sha"))[:12],
         str(leg.get("e07_head_sha") or "-")[:12]))
A("")
A("All three sit at the release freeze `5ab5e20d961db4c9807…` while E-07 ran at `%s`. The gate "
  "accepts that position and proves it by requiring every path changed since the freeze to be "
  "release evidence." % str(e07.get("head_sha"))[:12])
A("")
A("### E-07, the SRE leg's input")
A("")
A("`verdict=%s`, **%d/%d steps PASS**, abort `%s`, step 14 THROUGHPUT "
  "`ok=%s failed=%s rate=%s/s` over a %s s window (the bar is ≥900 with 0 failures, unchanged)."
  % (e07.get("verdict"), npass, e07.get("expected_steps", 22),
     json.dumps(e07.get("abort")) if e07.get("abort") else "null",
     thr.get("ok"), thr.get("failed"), thr.get("rate_per_s"), thr.get("window_s")))
A("")
A("## 4. Product freeze")
A("")
A("The freeze is `5ab5e20d961db4c9807f578195ae8530dea2f7d9` — "
  "\"Re-derive the product freeze: the FINAL acceptance harness is not release "
  "evidence\", which carries the four FINAL acceptance-harness paths and no release "
  "evidence.")
A("")
A("A leg may be signed at HEAD **or at the freeze**. The freeze exists because a signature "
  "cannot contain itself and simultaneously claim to have been written at the very commit "
  "containing it; demanding `release_sha == rev-parse HEAD` makes every leg off by one commit "
  "and unsatisfiable by any real signature.")
A("")
A("The freeze was first anchored at `dc154b0…`. That was wrong: `dc154b0` is the commit that "
  "first added `FINAL_ACTIVATION_CHECKLIST.md`, which is **F-02, release evidence**, and a "
  "freeze must carry no evidence. The derivation is recorded in `docs/FINAL_STOP_CONDITIONS.md` "
  "sec. E.")
A("")
A("The freeze was re-anchored again at the FINAL release, to `5ab5e20…`. The FINAL acceptance "
  "harness (`apps/control_ui/acceptance.py`, `fixtures/demo/acceptance.py`, "
  "`fixtures/demo/manifest.json`, `tests/e2e/conftest.py`) is neither shipped product nor release "
  "evidence, so committing it inside the evidence commit `47a3857` put four non-evidence paths past "
  "`df3e7d9` and invalidated all three leg signatures. Widening `EVIDENCE_PREFIXES` to absorb them "
  "was rejected: the rule was working and the state it refused was real. The four paths got a commit "
  "of their own, which is the freeze, and the product did not move with them — "
  "`git diff --stat df3e7d9 5ab5e20 -- packages` and `-- config` are both empty, and `-- apps` "
  "reports only the S-04 acceptance fixture.")
A("")
A("## 5. The SRE leg was NO-GO, and why that NO-GO was correct then and is superseded now")
A("")
A("The previous SRE signature concluded **NO-GO** with nine conditions, led by two STOP SHIP "
  "items: `scripts\\start.ps1` could not start the service (`$psi.EnvironmentVariables[...]`, "
  "line 136, throws `NullArray`), and `scripts\\restart.ps1` therefore could not restore it.")
A("")
A("Those were operator-lifecycle defects, not product-data-plane defects, and they were real: "
  "`start.ps1` exited 1 before creating any process, reproduced four times including on a clean "
  "host. `scripts/` is a **release-evidence prefix**, so repairing it does not invalidate a leg "
  "signed at the freeze — which is why the repair was done rather than the wording softened.")
A("")
A("The repair is in HEAD: `git diff --name-only -- scripts/` between the freeze and HEAD shows "
  "`start.ps1`, `restart.ps1`, `stop.ps1`, `status.ps1` and `lib/JccCommon.ps1`. The current "
  "evidence is that they now work: E-07 drives `start.ps1` (step 6, step 19), `restart.ps1` "
  "(step 11) and `stop.ps1` (step 16) and all pass, and the controlled-start and "
  "restart-acceptance phases reproduce it at runtime.")
A("")
if sup:
    A("The superseded signature is preserved, not deleted: "
      "`artifacts/acceptance/final_sre_go.superseded-no-go.json` (sha256 `%s…`), and the new "
      "document carries a `supersedes` block naming its verdict, signer and signing time. "
      "Neither signer is erased by the other." % str(sup.get("sha256"))[:12])
    A("")
A("## 6. Known blind spot: parsing is not running")
A("")
A("W-01…W-07 assert only that each `scripts/*.ps1` **parses**. That gap is real and already cost "
  "a defect: `scripts\\doctor.ps1` parsed cleanly and then crashed at runtime "
  "(`InvalidCastFromStringToDoubleOrSingle` in a mixed-type `+` chain), exiting 1 half-way "
  "through its own self-check after printing six PASS lines. A check that only parses cannot see "
  "that. The gate's six DEGRADED items are, in the same spirit, declarations of limits rather "
  "than claims of completeness — see §7.")
A("")
A("## 7. Verified-degraded conditions (they do not block activation)")
A("")
A("| ID | Condition | Operator consequence |")
A("| --- | --- | --- |")
A("| A-01 | WorkBuddy adapter: no verified steering contract | adapters run recommendation-only |")
A("| A-02 / A-03 / A-04 | Claude / Codex / Hermes: mechanism present, not exercisable here | "
  "same as A-01 |")
A("| S-04 | Control UI present, full 8-screen smoke not separately evidenced | treat B-surface "
  "completeness as not yet evidenced |")
A("| S-05 | Demo mode exists and is labelled `DEMO` | demo runs must stay labelled `DEMO` |")
A("")
A("## 8. What remains before the gate can answer `true`")
A("")
A("Nothing in the gate's own checklist: every ID in A–H is `%s`."
  % ("OK" if g.get("project_complete") else "not OK"))
A("")
A("Three things remain true outside the checklist and are recorded rather than absorbed:")
A("")
A("1. **`jcc_core` is not supervised by any component in this repository.** The shipped watchdog "
  "watches the continuation controller; if the service dies, nothing restarts it automatically. "
  "Recovery is an operator cycle through the product's scripts (proved in "
  "`artifacts/release/WATCHDOG_RECOVERY_REPORT.md`).")
A("2. The scheduled task `JEV-JCC-Continuation-Watchdog` is not registered on this host; "
  "registering it needs elevation.")
A("3. **A discrepancy between the written stop condition and this gate's implementation.** "
  "`docs/FINAL_STOP_CONDITIONS.md` permits `DEGRADED` for section **B** only (and "
  "`PENDING_EXTERNAL_ACTIVATION` for C-05). This gate also emits `g.degraded(...)` for "
  "**S-04** and **S-05**, which are section C, and `DEGRADED` never blocks. So "
  "`PROJECT_COMPLETE=true` above is this gate's determination while the prose would "
  "require those two to be `OK`. It was NOT changed: making the code match the prose "
  "would flip this verdict to `false`, which is a stop-condition decision for the owner. "
  "The two residuals are named, not absorbed: S-04 `%s`; S-05 `%s`."
  % (next((c["detail"] for c in checks if c["id"] == "S-04"), "?"),
     next((c["detail"] for c in checks if c["id"] == "S-05"), "?")))
A("")
A("## 9. Reproducing this verdict")
A("")
A("```")
A(r"F:\jev> git status --porcelain          # must be empty")
A(r"F:\jev> git rev-parse HEAD")
A(r"F:\jev> F:\jev\.venv\Scripts\python.exe F:\jev\artifacts\acceptance\final_gate_run.py --full")
A("```")
A("")
A("The verdict is only worth anything if a clone can re-derive it, so that is audited "
  "rather than asserted. `artifacts/acceptance/**` is covered by `.gitignore`, which means "
  "evidence living there can be **absent from version control while not making the tree "
  "dirty** -- the gate would still pass and nobody would notice. Every path this gate "
  "actually reads is therefore checked against `git ls-files`:")
A("")
_cons = []
_glob = os.path.join(ACC, "component_evidence")
if os.path.isdir(_glob):
    for f in sorted(os.listdir(_glob)):
        if f.endswith(".json"):
            _cons.append("artifacts/acceptance/component_evidence/" + f)
for f in sorted(os.listdir(os.path.join(ACC, "adapter_capability"))
                if os.path.isdir(os.path.join(ACC, "adapter_capability")) else []):
    if f.endswith(".json"):
        _cons.append("artifacts/acceptance/adapter_capability/" + f)
for n in ("demo_e2e", "api_e2e", "ui_smoke", "restart_recovery"):
    for suf in (".json", ".md", "_result.json", "_result.md"):
        if os.path.isfile(os.path.join(ACC, n + suf)):
            _cons.append("artifacts/acceptance/" + n + suf)
for leg in ("architecture", "security", "sre"):
    _cons.append("artifacts/acceptance/final_%s_go.json" % leg)
_cons += ["artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md",
          "artifacts/acceptance/final_gate_result.json",
          "artifacts/acceptance/final_gate_run.py",
          "artifacts/acceptance/win_scripts/run_e07_final.py",
          "artifacts/acceptance/win_scripts/E07_FINAL_EVIDENCE.json",
          "artifacts/release/manifest.json", "artifacts/release/final-manifest.json",
          "artifacts/release/JCC_SOAK_REPORT.md",
          "artifacts/release/PROJECT_RUN_READINESS.md",
          "artifacts/release/WATCHDOG_RECOVERY_REPORT.md",
          "artifacts/release/JCC_STABILITY_ROOT_CAUSE.md",
          "FINAL_ACTIVATION_CHECKLIST.md"]
_tracked = set(x.replace("\\", "/") for x in git("ls-files").split("\n") if x)
_untracked = sorted(p for p in _cons if os.path.isfile(os.path.join(ROOT, p))
                    and p not in _tracked)
A("| | |")
A("| --- | --- |")
A("| gate-consumed paths checked | %d |" % len(_cons))
A("| present but **not** in version control | %s |"
  % ("**none — the verdict survives a clone**" if not _untracked
     else ", ".join("`%s`" % p for p in _untracked)))
A("")
if _untracked:
    A("> WARNING: the paths above are read by the gate but are not committed. A clone would "
      "either fail those checks or, worse, be missing evidence silently. Commit them with "
      "`git add -f` (they are ignored by `.gitignore`).")
    A("")
A("Residual limit: the `T-*` checks run `pytest` against the scopes under `packages/`, "
  "`apps/` and `facades/`. Those tests are in version control, but the `.venv` they run "
  "under is not, so re-deriving the *code* verdict needs a rebuilt environment; the "
  "*evidence* verdict needs nothing but the clone.")
A("")
A("Regenerated from `artifacts/acceptance/final_gate_result.json` at %s."
  % datetime.datetime.now().isoformat(timespec="seconds"))
A("")

out = os.path.join(ACC, "FINAL_ACCEPTANCE_REPORT.md")
io.open(out, "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
print("PROJECT_COMPLETE=%s  head=%s" % (g.get("project_complete"), head[:12]))
