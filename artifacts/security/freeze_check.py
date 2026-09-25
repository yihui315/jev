"""E-06 freeze-compliance gate (read-only).

team-lead's rule, made mechanical:
  * There is ONE product-freeze commit. It holds all product/ops code and no
    release evidence.
  * A leg may be signed at that freeze commit.
  * The gate diffs HEAD against the freeze and requires EVERY changed path to
    be release evidence: artifacts/, docs/, tools/, scripts/, .gitignore,
    root checklist.
  * A signature whose SHA was not verified by the signer, or an unrelated SHA,
    is a FAIL.

Standing ruling from team-lead (do not re-litigate, do not add an override):
a change to a module under `packages/` is NEVER release evidence, no matter
which commit carries it. The path whitelist only covers things that do not
carry product logic - `scripts/*.ps1` are operator entry points, `docs/` and
`tools/` are prose and tooling. `packages/` is the product. If a product module
is touched after the freeze - even one line, even in a commit whose other paths
are all evidence - the signature no longer describes what ships and re-probing
is mandatory. Test 2 below is the one that sees content; the whitelist only sees
names. Content wins.

Path-classification alone is necessary but not sufficient for the security leg,
because it keys on NAMES not CONTENT.  So this script adds the sharper test the
security leg actually needs: the 15 tracked product modules that the 83 probes
execute must be byte-identical between the freeze and HEAD.  If they are,
re-signing the SHA without re-probing is provably sound.  If any differ, the
probes MUST be re-run before re-signing.

usage: python freeze_check.py <freeze_sha> [current_sha]
exit 0 = compliant, 1 = not
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"F:/jev")

# One truth for "what counts as release evidence".
#
# This used to carry its own copy of the whitelist, and the two copies had
# drifted: final_gate_run.py accepts FINAL_ACTIVATION_CHECKLIST.md as evidence
# while this file did not, so the very same commit was evidence to one gate and
# a violation to the other. A rule that resolves differently depending on which
# file states it is worse than no rule - the FAIL was real, but it proved
# nothing about the release. Import the constant instead of restating it; the
# gate has an `__main__` guard, so importing it runs no checks.
_GATE_DIR = Path(r"F:/jev/artifacts/acceptance")
if str(_GATE_DIR) not in sys.path:
    sys.path.insert(0, str(_GATE_DIR))

import final_gate_run as _gate  # noqa: E402  (path tweak above must precede this)

RELEASE_EVIDENCE_PREFIXES = _gate.EVIDENCE_PREFIXES
RELEASE_EVIDENCE_FILES = {".gitignore"}

# The 15 tracked product modules the E-06 probes execute (from import_closure.py).
PROBE_EXECUTED_MODULES = (
    "packages/jcc_core/actuator.py",
    "packages/jcc_core/audit.py",
    "packages/jcc_core/config.py",
    "packages/jcc_core/decision.py",
    "packages/jcc_core/nonce_cache.py",
    "packages/jcc_core/policy.py",
    "packages/jcc_core/redaction.py",
    "packages/jcc_core/schemas.py",
    "packages/jcc_core/server.py",
    "packages/jcc_core/signing.py",
    "packages/jcc_core/state_store.py",
    "packages/operator_skill/gates.py",
    "packages/operator_skill/trace.py",
    "packages/supervisor/budget.py",
    "packages/supervisor/shadow_store.py",
)


def git(*args: str) -> str:
    proc = subprocess.run(
        ("git", "-C", str(ROOT)) + args, capture_output=True
    )
    return proc.stdout.decode("utf-8", "replace")


def blob_sha(rev: str, rel: str) -> str | None:
    """Content hash of a path at a revision; None if absent there."""
    proc = subprocess.run(
        ("git", "-C", str(ROOT), "rev-parse", f"{rev}:{rel}"),
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return hashlib.sha256(proc.stdout).hexdigest()


def main() -> int:
    freeze = sys.argv[1] if len(sys.argv) > 1 else None
    head = sys.argv[2] if len(sys.argv) > 2 else git("rev-parse", "HEAD").strip()
    if not freeze:
        print("FAIL  usage: freeze_check.py <freeze_sha> [current_sha]")
        return 1
    if len(freeze) == 40 and all(c in "0123456789abcdef" for c in freeze.lower()):
        pass
    else:
        print(f"FAIL  freeze sha is not 40-hex: {freeze!r}")
        return 1

    print(f"freeze={freeze}")
    print(f"head  ={head}")
    if freeze == head:
        print("\n[1] head IS the freeze — nothing to diff.")

    failures: list[str] = []

    # --- test 1: every changed path is release evidence -------------------- #
    print("\n[1] changed paths are release evidence")
    diff = git("diff", "--name-only", freeze, head).splitlines()
    diff = [d for d in diff if d]
    if not diff:
        print("    no changed paths")
    for path in sorted(set(diff)):
        norm = path.replace("\\", "/")
        allowed = norm.startswith(RELEASE_EVIDENCE_PREFIXES) or norm in RELEASE_EVIDENCE_FILES
        print(f"    {'ok  ' if allowed else 'FAIL'} {norm}")
        if not allowed:
            failures.append(f"non-evidence path changed: {norm}")

    # --- test 2: no probe-executed product module changed ------------------ #
    print("\n[2] probe-executed product modules are byte-identical")
    changed_mods: list[str] = []
    for rel in PROBE_EXECUTED_MODULES:
        a = blob_sha(freeze, rel)
        b = blob_sha(head, rel)
        same = (a is not None and b is not None and a == b) or (a is None and b is None)
        print(f"    {'same' if same else 'DIFF'}  {rel}")
        if not same:
            changed_mods.append(rel)
    if changed_mods:
        failures.append(
            "PRODUCT MODULES CHANGED: " + ", ".join(changed_mods)
            + " - packages/ is product, never release evidence. "
            + "The 83 probes MUST be re-run before re-signing; this is a hard "
            + "FAIL, not a warning."
        )

    # --- verdict ----------------------------------------------------------- #
    print("\n" + "=" * 70)
    if failures:
        print("FREEZE CHECK: NOT COMPLIANT")
        for f in failures:
            print("  - " + f)
        return 1
    print("FREEZE CHECK: COMPLIANT")
    if changed_mods:
        print("  (but product modules changed, so re-probe before re-signing)")
    else:
        print("  re-signing the SHA without re-probing is sound")
    return 0


if __name__ == "__main__":
    sys.exit(main())
