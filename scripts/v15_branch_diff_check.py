#!/usr/bin/env python3
"""V1.5 P1 — branch integration regression.

Runs from a git worktree of feature/v15-staging-hardening and proves, with
real git object hashes (not eyeballing), that the integration branch:
  * keeps the VERIFIED CI workflow fixes from origin/feat/jev-cloudflare-staging
  * keeps the audit evidence reports from origin/feature/v14-ci-audit-20261109
  * does NOT reintroduce the audit branch's duplicate acceptance workflow
  * does NOT change a single byte of the frozen trading engine tree
  * keeps the strengthened idempotency / readiness assertions

Exit code 0 == all PASS; non-zero == at least one FAIL. No network needed.
"""
import hashlib
import json
import subprocess
import sys

FEAT = "origin/feat/jev-cloudflare-staging"
AUDIT = "origin/feature/v14-ci-audit-20261109"
HEAD = "HEAD"

WORKFLOWS = [
    ".github/workflows/jev-cloudflare-staging.yml",
    ".github/workflows/v14-acceptance.yml",
]
REPORTS = [
    "reports/V14_AUDIT_BRANCH.md",
    "reports/V14_GITHUB_CI_SIGNOFF.md",
]
DUPLICATE = ".github/workflows/v14-audit-acceptance.yml"

ASSERTIONS = {
    ".github/workflows/jev-cloudflare-staging.yml": [
        "session')=='created'",           # strengthened session smoke
        "preview auth not ready",          # bounded readiness retry
        "test \"$status\" = 200",          # assertion retained, not deleted
    ],
    ".github/workflows/v14-acceptance.yml": [
        "duplicate must point at the original order",  # stronger idempotency check
        "assert b['duplicate'] is True",
    ],
}


def git(*args):
    return subprocess.run(["git", *args], capture_output=True)


def blob_hash(ref, path):
    p = git("show", f"{ref}:{path}")
    if p.returncode != 0:
        return None
    return hashlib.sha256(p.stdout).hexdigest()


def exists(ref, path):
    return git("cat-file", "-e", f"{ref}:{path}").returncode == 0


def engine_tree_hash(ref):
    p = git("ls-tree", "-r", ref)
    lines = [l for l in p.stdout.decode("utf-8", "replace").splitlines()
             if "server/engine/" in l]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), len(lines)


def main():
    results = []

    def check(name, ok, detail):
        results.append({"check": name, "pass": bool(ok), "detail": detail})

    for path in WORKFLOWS:
        h_head, h_feat = blob_hash(HEAD, path), blob_hash(FEAT, path)
        check(f"workflow_from_feat:{path}", h_head and h_head == h_feat,
              {"head_sha256": h_head, "feat_sha256": h_feat})

    for path in REPORTS:
        h_head, h_audit = blob_hash(HEAD, path), blob_hash(AUDIT, path)
        check(f"report_from_audit:{path}", h_head and h_head == h_audit,
              {"head_sha256": h_head, "audit_sha256": h_audit})

    check("duplicate_audit_workflow_absent", not exists(HEAD, DUPLICATE),
          {"path": DUPLICATE, "present_on_audit_branch": exists(AUDIT, DUPLICATE)})

    hashes = {r: engine_tree_hash(r) for r in (HEAD, FEAT, AUDIT, "ce8d570")}
    check("frozen_engine_byte_identical",
          len({v[0] for v in hashes.values()}) == 1 and all(v[1] > 0 for v in hashes.values()),
          {k: {"sha256": v[0], "files": v[1]} for k, v in hashes.items()})

    for path, needles in ASSERTIONS.items():
        p = git("show", f"{HEAD}:{path}")
        text = p.stdout.decode("utf-8", "replace")
        for needle in needles:
            check(f"assertion_present:{needle[:38]}", needle in text,
                  {"path": path, "needle": needle})

    # merge commit has two parents -> both histories preserved
    p = git("rev-list", "--parents", "-n", "1", HEAD)
    parents = p.stdout.decode().split()[1:]
    check("both_branch_histories_preserved", len(parents) == 2,
          {"parents": parents, "note": "no force push, no squash, no rebase"})

    passed = sum(1 for r in results if r["pass"])
    summary = {"total": len(results), "pass": passed,
               "fail": len(results) - passed, "results": results,
               "verdict": "PASS" if passed == len(results) else "FAIL"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
