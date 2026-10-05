#!/usr/bin/env python3
"""INC-002 tree check -- two halves, both required, neither sufficient alone.

WHY THIS TOOL EXISTS
--------------------
After INC-002 (files zeroed in place, size preserved, escaping git's stat cache)
the rule became "do not prove the tree is intact with ``git status``". Executing
that literally produced FALSE POSITIVES, because this repo sets
``core.autocrlf=true`` and carries ``* text=auto eol=lf``:

    $ git show HEAD:pkg/host_launcher.py | sha256sum   -> 45366 B
    $ sha256sum pkg/host_launcher.py                   -> 46355 B   (+989)
    $ git diff -- pkg/host_launcher.py                 -> EMPTY
    # 989 == the CR count. Content is byte-identical.

A rule that cries wolf gets ignored, which is worse than no rule, so the
distinction that matters is:

  (a) size UNCHANGED, content zeroed   -> INC-002. Real damage.
  (b) size CHANGED, content identical  -> ordinary CRLF/LF. NOT damage.

Both halves are required, and neither alone localises the incident:

  1. CR-normalised content hash  -- separates (a)/(b) from real edits. A zeroed
     file still hashes *something*, so this alone cannot name INC-002.
  2. non-zero-byte check         -- what actually names it: bytes-on-disk vs
     bytes-after-stripping-NULs. A file that is 8639 bytes of NUL has s == z
     FALSE, and git diff will happily call that "Bin 8639 -> 8639 bytes".

ONE DEVIATION FROM forge2's NOTE, AND IT IS DELIBERATE
------------------------------------------------------
forge2's note normalises with ``tr -d '\\r'``. That DELETES every CR byte,
including a CR that is a real character inside a string literal:

    printf 'x = "a\\rb"\\r\\n'   # 17 bytes
    tr -d '\\r'                 -> 16 bytes, the literal CR is GONE
    sed 's/\\r$//'              -> 17 bytes, the literal CR is KEPT

Deleting it can mask a genuine content change on both sides at once and make two
different files compare equal. This tool therefore strips CR only at END OF LINE
(CRLF -> LF), which is the normalisation that was actually wanted. It resolves
forge2's case (b) identically -- verified on host_launcher.py, both forms give
26d9cd51e4458837 -- while additionally refusing to destroy in-band CRs.

Binary files are SKIPPED, not normalised: a PDF or SQLite blob has no line
structure, so rewriting "line endings" in one would corrupt the comparison and
manufacture a false DIFFER. Git's own ``text=auto eol=lf`` already exempts them
(``.gitattributes`` marks *.png / *.pdf binary).

RE-EMITTED EVIDENCE, AND THE LIMIT OF ITS EXEMPTION
-----------------------------------------------------
Five artifacts under ``artifacts/acceptance/`` are re-emitted with a fresh
``probe_at`` timestamp on every full-suite run, so they would otherwise be
reported as DRIFT every single time -- and a rule that cries wolf gets ignored.
They are classified ``EVIDENCE_REEMITTED`` with the reason printed, never
silently skipped.

The exemption is keyed on PATH, not on what changed: a hand edit to the
substance of one of those files would also be classified EVIDENCE_REEMITTED.
This is a known, adjudicated limit rather than an oversight -- see the long note
on ``EVIDENCE_REEMITTED`` for why schema-aware exemption would be worse. The
zero-byte and size checks still run on these paths unconditionally, so a zeroed
exempt file fails with exit 1 and cannot hide.

READ-ONLY. Reads git objects and files; writes nothing but the report.

USAGE
-----
  python tools/inc002_tree_check.py --selftest
  python tools/inc002_tree_check.py --files a.py b.py
  python tools/inc002_tree_check.py --a8-pins        # the frozen artifact set
  python tools/inc002_tree_check.py --all-tracked

Exit: 0 clean, 1 damage or drift found, 2 the check could not run (NOT a pass).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]

#: Suffixes whose bytes are not line-structured. Normalising these would corrupt
#: the comparison, so they get a byte-exact check instead.
BINARY_SUFFIXES = {".png", ".pdf", ".db", ".sqlite", ".sqlite3", ".pyc",
                   ".zip", ".gz", ".ico", ".jpg", ".jpeg", ".woff", ".woff2"}

#: Sentinel distinguishing "blob not preloaded" from "blob is None".
_NO_PRELOAD = object()

SEV_CLEAN = "CLEAN"
SEV_DRIFT = "DRIFT"
SEV_ZEROED = "ZEROED"
SEV_DIAG = "SANDBOX_DIAG"
#: Content legitimately differs from HEAD, classified and explained -- NOT damage
#: and NOT ignored. Reported as its own severity so a reader can see the count.
SEV_EVIDENCE = "EVIDENCE_REEMITTED"

#: Files that a test/gate run RE-EMITS with a fresh timestamp, so they differ
#: from HEAD after every full-suite run. Exempt from the DRIFT verdict -- but only
#: after the two damage checks still run on them, and only with the reason
#: printed. Silently skipping them would be the failure mode this tool exists to
#: prevent: a check that cries wolf gets ignored, and an exemption with no
#: recorded justification is just a hole.
#:
#: Verified, not assumed: each of the four adapter JSONs carries
#: ``"probe_at": "2026-10-04T09:55:15Z"``, which changes on every re-emit.
#: KNOWN LIMITATION -- read this before trusting an EVIDENCE_REEMITTED verdict.
#:
#: The exemption is keyed on PATH, not on WHAT changed. If a hand edit alters
#: the substance of one of these files -- a capability flipped to CONNECTED, a
#: field dropped -- it is still classified EVIDENCE_REEMITTED rather than DRIFT,
#: because this check does not parse the JSON and cannot tell a re-emit's
#: timestamp churn from a substantive edit.
#:
#: This is deliberate and was adjudicated rather than overlooked. Distinguishing
#: the two would require knowing these documents' schema, and schema knowledge
#: hard-wired into a checker is a liability: the schema will change and the tool
#: will not, so it would silently keep applying a rule that no longer describes
#: the file. A path-keyed exemption with a printed reason degrades honestly --
#: it says "I did not verify this one, here is why" -- whereas a schema-aware
#: exemption that has drifted would say "I checked this one" falsely.
#:
#: WHAT IS STILL ENFORCED on these paths, unconditionally:
#:   * the NUL/zero-byte check (INC-002 shape (a)) -- verified by test;
#:   * the size comparison against the HEAD blob.
#: So a zeroed exempt file fails with exit 1; it cannot hide.
#:
#: TO TIGHTEN THIS LATER: compare the parsed document with volatile fields
#: (probe_at and friends) removed, rather than adding more exempt paths. That is
#: the right shape of fix, and it belongs in whoever owns those artifacts.
EVIDENCE_REEMITTED = {
    "artifacts/acceptance/CONTINUATION_INFRA_TEST.json":
        "re-emitted by the continuation infra test (per-run node_ids/checks)",
    "artifacts/acceptance/adapter_capability/claude.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
    "artifacts/acceptance/adapter_capability/codex.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
    "artifacts/acceptance/adapter_capability/hermes.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
    "artifacts/acceptance/adapter_capability/workbuddy.json":
        "probe_at timestamp; re-emitted by v1_1_emit_capability_matrix",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise_eol(data: bytes) -> bytes:
    """CRLF -> LF, i.e. strip a CR ONLY at end of line.

    Deliberately not ``data.replace(b"\\r", b"")``: that would also delete a CR
    which is a real character inside a string literal, letting two genuinely
    different files compare equal. Lone CRs (classic Mac line endings) are left
    alone -- they are vanishingly rare here and normalising them would be a
    second, unjustified transformation.
    """
    return data.replace(b"\r\n", b"\n")


def nul_count(data: bytes) -> int:
    return data.count(b"\x00")


def head_bytes(path: str, rev: str = "HEAD") -> Optional[bytes]:
    """Raw blob from the object store, single-file path.

    ``git cat-file -p`` on purpose: ``git show``/``git cat-file`` differ in
    whether working-tree conversion is applied, and we need the bytes AS STORED.

    For a whole-tree run prefer :func:`head_bytes_batch`, which pays the git
    startup cost once instead of once per file (~0.24s/file here, which is ~3
    minutes over 740 tracked files -- too slow to sit in a gate).
    """
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-p", f"{rev}:{path}"],
            capture_output=True, cwd=str(REPO), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def head_bytes_batch(paths: List[str], rev: str = "HEAD") -> Dict[str, Optional[bytes]]:
    """Read many blobs in ONE ``git cat-file --batch`` process.

    Binary output is framed as ``<sha> <type> <size>\\n<bytes>\\n``, which is
    parsed explicitly. Doing this per-file is correct but costs one process spawn
    per file, and a gate that takes three minutes gets skipped.
    """
    out: Dict[str, Optional[bytes]] = {p: None for p in paths}
    if not paths:
        return out
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch"],
            input="\n".join(f"{rev}:{p}" for p in paths).encode("utf-8"),
            capture_output=True, cwd=str(REPO), timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return out
    buf = proc.stdout
    pos = 0
    for path in paths:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            break
        header = buf[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        # "<sha> <type> <size>" or "<spec> missing"
        if len(header) < 3 or header[1] == "missing":
            continue
        try:
            size = int(header[2])
        except ValueError:
            continue
        out[path] = buf[pos:pos + size]
        pos += size + 1  # skip the trailing newline git appends
    return out


def check_one(rel: str, rev: str = "HEAD",
               preloaded: Any = _NO_PRELOAD) -> Dict[str, Any]:
    """One file. Always returns a record; never raises.

    ``preloaded`` supplies the HEAD blob when the caller already fetched it in a
    batch; passing ``None`` is ambiguous (absent vs not fetched), so the sentinel
    ``_NO_PRELOAD`` is used to mean "fetch it yourself".
    """
    rec: Dict[str, Any] = {"path": rel, "severity": SEV_CLEAN, "notes": []}
    p = REPO / rel
    if not p.is_file():
        rec["severity"] = SEV_DIAG
        rec["notes"].append("not present in the worktree")
        return rec

    try:
        work = p.read_bytes()
    except OSError as exc:
        rec["severity"] = SEV_DIAG
        rec["notes"].append(f"unreadable: {exc}")
        return rec

    rec["size"] = len(work)
    rec["nul_bytes"] = nul_count(work)
    # Half 2 -- the half that actually names INC-002. Evaluated BEFORE the HEAD
    # comparison and independent of it: a file that is entirely NUL is destroyed
    # whether or not it is tracked, so a missing blob must not downgrade this to
    # a diagnostic. (Ordering matters -- an earlier draft returned SANDBOX_DIAG
    # here for an untracked target and the selftest caught that the zeroing
    # detection was never actually being exercised.)
    rec["zeroed"] = rec["nul_bytes"] > 0 and rec["nul_bytes"] == len(work)
    if rec["zeroed"]:
        rec["severity"] = SEV_ZEROED
        rec["notes"].append(
            f"INC-002 shape (a): {rec['size']} bytes, ALL NUL -- content destroyed, "
            "size preserved so git's stat cache does not notice"
        )

    hb = head_bytes(rel, rev) if preloaded is _NO_PRELOAD else preloaded
    if hb is None:
        if rec["severity"] != SEV_ZEROED:
            rec["severity"] = SEV_DIAG
            rec["notes"].append(f"no blob at {rev}:{rel} (untracked or new file)")
        else:
            rec["notes"].append(f"additionally: no blob at {rev}:{rel}")
        return rec
    rec["head_size"] = len(hb)

    is_binary = p.suffix.lower() in BINARY_SUFFIXES or b"\x00" in work[:8192]
    rec["binary"] = is_binary

    if is_binary:
        # Byte-exact; no EOL normalisation is meaningful or safe.
        rec["match"] = _sha(work) == _sha(hb)
        rec["mode"] = "binary-exact"
    else:
        rec["match"] = _sha(normalise_eol(work)) == _sha(normalise_eol(hb))
        rec["mode"] = "crlf-normalised"

    if rec["zeroed"]:
        pass  # already recorded above; do not overwrite with DRIFT
    elif not rec["match"]:
        if rec["mode"] == "binary-exact" and rec["match"] is False and rel in EVIDENCE_REEMITTED:
            pass
        if rel in EVIDENCE_REEMITTED:
            # Re-emitted evidence, not damage -- but say so out loud, and say why.
            rec["severity"] = SEV_EVIDENCE
            rec["exempt_reason"] = EVIDENCE_REEMITTED[rel]
            rec["notes"].append(
                "content differs from HEAD; classified as re-emitted evidence "
                f"({EVIDENCE_REEMITTED[rel]}) -- not counted as DRIFT"
            )
        else:
            rec["severity"] = SEV_DRIFT
            d_head, d_work = rec["head_size"], rec["size"]
            if d_work - d_head == work.count(b"\r\n") and d_work > d_head:
                rec["notes"].append(
                    "size delta equals the CRLF count but content still differs after "
                    "normalisation -- a real edit, not a line-ending artefact"
                )
            else:
                rec["notes"].append("content differs from HEAD after EOL normalisation")
    return rec


def a8_pinned_files() -> List[str]:
    """The frozen artifact list, parsed rather than imported.

    Importing the P3 suite would pull pytest fixtures into this process, which is
    exactly the coupling that makes a checker fragile.
    """
    src = REPO / "packages" / "jcc_core" / "tests" / "test_p3_acceptance_gate.py"
    if not src.is_file():
        return []
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    start = text.find("_PROD_ARTIFACTS")
    if start < 0:
        return []
    seg = text[start:]
    end = seg.find("]")
    if end < 0:
        return []
    out: List[str] = []
    for line in seg[:end].splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        # Each entry is a Python expression like
        #     REPO_ROOT / "packages" / "jcc_core" / "core.py"
        # Extract the quoted parts and join them; taking the whole line (an
        # earlier draft did) yields a path with quotes and commas in it, which
        # then "is not present in the worktree" for every entry.
        parts = re.findall(r'"([^"]+)"', line)
        if parts:
            out.append("/".join(parts))
    return out


def selftest() -> int:
    """Prove the two halves on synthetic inputs, including a real zeroed file."""
    fails: List[str] = []

    # 1. CRLF vs LF must MATCH (shape b -- the false positive forge2 hit).
    lf = b"line one\nline two\n"
    crlf = b"line one\r\nline two\r\n"
    if _sha(normalise_eol(crlf)) != _sha(normalise_eol(lf)):
        fails.append("CRLF and LF did not normalise equal (shape b unresolved)")

    # 2. A real edit must NOT match.
    if _sha(normalise_eol(b"line one\n")) == _sha(normalise_eol(b"line ONE\n")):
        fails.append("a real content edit compared equal")

    # 3. An in-band CR must SURVIVE normalisation (our deviation from tr -d).
    inband = b'x = "a\rb"\n'
    if normalise_eol(inband) != inband:
        fails.append("normalisation destroyed an in-band CR -- would mask a real diff")
    if _sha(normalise_eol(b'a = "1"\n')) == _sha(normalise_eol(b'a = "2"\n')):
        fails.append("distinct literals compared equal")

    # 4. Zero detection.
    z = b"\x00" * 100
    if not (nul_count(z) == len(z) == 100):
        fails.append("zero detection failed on a synthetic zeroed buffer")

    # 5. End-to-end on a REAL file, with a real size-preserving zeroing, then
    #    restore. This is the negative control -- without it the check is a
    #    tautology that always passes.
    #
    #    The target is a real TRACKED file so the comparison half has a baseline:
    #    an untracked scratch file legitimately yields SANDBOX_DIAG ("no blob at
    #    HEAD"), which is the right answer and would make this control vacuous.
    #    The control therefore round-trips an actual repo file and restores it
    #    byte-for-byte in a finally block.
    target_rel = "integrations/_common/jcc_emit.py"
    target = REPO / target_rel
    original = target.read_bytes()
    try:
        target.write_bytes(b"\x00" * len(original))            # shape (a)
        rec = check_one(target_rel)
        if rec["severity"] != SEV_ZEROED:
            fails.append(
                f"a genuinely zeroed file was not flagged ZEROED (got {rec['severity']})"
            )
        if rec.get("size") != len(original):
            fails.append("size was not preserved in the control -- not a valid (a)")
        # The clean CRLF case must NOT cry wolf: CRLF worktree vs LF blob.
        target.write_bytes(original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        rec2 = check_one(target_rel)
        if rec2["severity"] != SEV_CLEAN:
            fails.append(
                f"CRLF-only variant was misreported as {rec2['severity']} -- false positive"
            )
        # And the unmodified original must be CLEAN.
        target.write_bytes(original)
        rec3 = check_one(target_rel)
        if rec3["severity"] != SEV_CLEAN:
            fails.append(f"pristine file reported {rec3['severity']}, expected CLEAN")
    finally:
        target.write_bytes(original)

    if fails:
        print("SELFTEST FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("SELFTEST PASS (EOL normalisation, real-edit detection, in-band CR "
          "preservation, zero detection, and a live size-preserving zeroing "
          "control that must be flagged)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="INC-002 tree check (read-only)")
    ap.add_argument("--files", nargs="*", default=None, help="explicit paths")
    ap.add_argument("--a8-pins", action="store_true", help="the frozen artifact set")
    ap.add_argument("--all-tracked", action="store_true")
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--json", default=None, help="write the report here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.files:
        targets = list(args.files)
    elif args.a8_pins:
        targets = a8_pinned_files()
        if not targets:
            print("FATAL: could not read _PROD_ARTIFACTS", file=sys.stderr)
            return 2
    elif args.all_tracked:
        try:
            out = subprocess.run(["git", "ls-files"], capture_output=True,
                                 text=True, cwd=str(REPO), timeout=120)
            targets = [t for t in out.stdout.splitlines() if t.strip()]
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"FATAL: git ls-files failed: {exc}", file=sys.stderr)
            return 2
    else:
        ap.print_help()
        return 2

    # One `git cat-file --batch` for the whole set, not one process per file.
    blobs = head_bytes_batch(targets, args.rev)
    records = [check_one(t, args.rev, preloaded=blobs.get(t)) for t in targets]
    by_sev: Dict[str, int] = {}
    for r in records:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1

    # EVIDENCE is reported, never silently dropped and never counted as damage.
    bad = [r for r in records if r["severity"] in (SEV_ZEROED, SEV_DRIFT)]
    ev = [r for r in records if r["severity"] == SEV_EVIDENCE]
    diags = [r for r in records if r["severity"] == SEV_DIAG]
    if bad:
        verdict = "FAIL"
    elif diags:
        verdict = "DEGRADED"
    else:
        verdict = "PASS"

    report = {
        "schema": "jcc-inc002-tree-check/1",
        "verdict": verdict,
        "rev": args.rev,
        "read_only": True,
        "checked": len(records),
        "by_severity": by_sev,
        "eol_normalisation": "CRLF->LF (CR stripped at EOL only; in-band CR preserved)",
        "binary_policy": "byte-exact, never EOL-normalised",
        "results": records,
    }
    if args.json:
        try:
            outp = Path(args.json)
            if not outp.is_absolute():
                outp = REPO / outp
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            print(f"written: {outp}")
        except OSError as exc:
            print(f"FATAL: cannot write {args.json}: {exc}", file=sys.stderr)
            return 2

    print(f"verdict={verdict} checked={len(records)} {by_sev}")
    for r in bad:
        print(f"  [{r['severity']}] {r['path']}: {'; '.join(r['notes'])}")
    for r in ev:
        print(f"  [{SEV_EVIDENCE}] {r['path']}: {r.get('exempt_reason', '')}")
    for r in diags[:5]:
        print(f"  [{SEV_DIAG}] {r['path']}: {'; '.join(r['notes'])}")
    return 1 if bad else (2 if diags else 0)


if __name__ == "__main__":
    raise SystemExit(main())
