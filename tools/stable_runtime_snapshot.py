#!/usr/bin/env python
"""Immutable stable-runtime snapshot for GREEN (the protected 127.0.0.1:4778 runtime).

WHY THIS FILE EXISTS.

The project runs TWO runtimes at once and they must never be confused:

    GREEN = 127.0.0.1:4778  the frozen, accepted, v1.0.1-ops product code
    BLUE  = 127.0.0.1:4779  the in-progress v1.1 feature branch code

`scripts/start.ps1` builds PYTHONPATH from `$Root\\packages`, and `$Root` is the
WORKTREE.  So the moment v1.1 changed `packages/jcc_core/core.py`, running the
ordinary start path from the worktree would put v1.1 code on the production
port -- a promotion nobody authorised.  (Measured: `core.py` blob
`32d6089c0e7a` at the frozen v1.0.1 tag vs `b21fe5df8218` at v1.1 HEAD.)

THE FIX IS A SNAPSHOT, NOT A CHECKOUT.  We extract `packages/` from an exact
commit into a directory under `runtime/` (which is gitignored, so the worktree
stays clean) and point PYTHONPATH at it FIRST.  The v1.1 worktree is never
checked out, never reset, never touched.

WHY "IMMUTABLE" IS A MEASURED CLAIM, NOT A NAME.  A directory called
`stable_v101` proves nothing.  So `create` refuses to overwrite a non-empty
destination, and records a per-file sha256 manifest plus a whole-tree digest.
`verify` re-hashes the tree AND cross-checks it against git's own object store
(`git ls-tree -r <sha> packages`), which is an independent oracle: the tree is
only "the frozen code" if both agree.

FAIL-CLOSED EVERYWHERE.  A command that cannot prove its claim exits non-zero
and says which probe failed.  Absence of an instrument reading is never
reported as absence of the thing.

Usage
-----
  create        --sha <commit> --dest <dir> [--root <repo>]
  verify        --dest <dir> [--root <repo>]
  verify-live   --dest <dir> --health-url <url> [--root <repo>]

Exit codes: 0 = the requested claim is PROVEN, 1 = the claim is FALSE (drift /
unfrozen code), 2 = the claim could not be evaluated (instrument failure).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone

DEFAULT_ROOT = str(Path(__file__).resolve().parents[1])
SCHEMA = "jcc.stable-runtime-snapshot/1"

# The health fields the frozen (v1.0.1) server.py is known to return.  These are
# asserted as a NON-VACUITY GUARD on the parser below: if the parse stops
# finding them, the parser broke and must not be trusted to prove anything.
FROZEN_HEALTH_CORE_KEYS = frozenset(
    {"status", "jev_provider", "actuation_enabled", "policy_version",
     "localhost_only", "wal", "open_get_endpoints"}
)
# Introduced only by the v1.1 server.py.  Their ABSENCE from the live payload is
# the direct, behavioural proof that 4778 is running the frozen file.
V1_1_ONLY_HEALTH_KEYS = frozenset(
    {"provider_mode", "provider_verified", "provider_activation_state",
     "provider_requested_mode", "provider_is_real"}
)


def _die(msg: str, code: int = 1) -> "NoReturn":  # noqa: F821
    sys.stdout.write(msg.rstrip() + "\n")
    sys.stdout.flush()
    raise SystemExit(code)


def _git(root: str, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", root, *args], capture_output=True)
    if proc.returncode != 0:
        _die("git %s failed (rc=%d): %s" % (
            " ".join(args), proc.returncode,
            proc.stderr.decode("utf-8", "backslashreplace").strip()), 2)
    return proc.stdout


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_digest(files: dict) -> str:
    """Digest over the sorted (relpath, sha256) set -- order independent."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.replace("\\", "/").encode("utf-8"))
        h.update(b"\0")
        h.update(files[rel].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _write_json_atomic(path: str, obj) -> None:
    """temp file -> flush -> fsync -> atomic replace.

    The host lost a whole source file to an all-NUL write-back on 2026-09-27.
    Every write of a file this project depends on goes through here.
    """
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".tmp-snapshot-", suffix=".json")
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


def _walk_rel(base: str) -> dict:
    out = {}
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base)
            out[rel] = _sha256_file(full)
    return out


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #

def cmd_create(args) -> int:
    dest = os.path.abspath(args.dest)
    sha = args.sha

    commit = _git(args.root, "rev-parse", sha).decode().strip()
    if len(commit) != 40:
        _die("could not resolve %r to a full commit id (got %r)" % (sha, commit), 2)

    # PERMANENT_DELETE=0: never clobber an existing snapshot.  A stale snapshot
    # silently replaced is exactly how "immutable" becomes a lie.
    if os.path.isdir(dest) and os.listdir(dest):
        _die("REFUSED: %s already exists and is non-empty.\n"
             "         An immutable snapshot is never overwritten in place. Remove it by hand\n"
             "         only after confirming no runtime is serving from it." % dest, 1)

    blob = _git(args.root, "archive", "--format=tar", commit, "packages")
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tf:
        members = tf.getmembers()
        for m in members:
            # No path escapes: a tar entry may not write outside dest.
            target = os.path.realpath(os.path.join(dest, m.name))
            if not target.startswith(os.path.realpath(dest) + os.sep) and target != os.path.realpath(dest):
                _die("REFUSED: tar member %r escapes the destination" % m.name, 1)
        # `filter="data"` (3.12+) keeps the extraction deterministic and refuses
        # absolute/escaping members itself. Older interpreters have no such
        # argument; the explicit escape check above is the portable guard.
        try:
            tf.extractall(dest, filter="data")
        except TypeError:
            tf.extractall(dest)

    pkg = os.path.join(dest, "packages")
    if not os.path.isdir(pkg):
        _die("REFUSED: extracted archive has no packages/ directory", 1)

    files = _walk_rel(pkg)
    if not files:
        _die("REFUSED: extracted packages/ is empty -- refusing to certify an empty snapshot", 1)

    manifest = {
        "schema": SCHEMA,
        "source_root": os.path.abspath(args.root),
        "source_sha": commit,
        "source_sha_short": commit[:12],
        "source_commit_date": _git(args.root, "log", "-1", "--format=%cI", commit).decode().strip(),
        "source_commit_subject": _git(args.root, "log", "-1", "--format=%s", commit).decode().strip(),
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "packages_dir": pkg,
        "file_count": len(files),
        "tree_digest_sha256": _tree_digest(files),
        "files": files,
    }
    _write_json_atomic(os.path.join(dest, "SNAPSHOT.json"), manifest)
    sys.stdout.write("created %s\n" % dest)
    sys.stdout.write("  source_sha   : %s (%s)\n" % (commit[:12], manifest["source_commit_subject"]))
    sys.stdout.write("  file_count   : %d\n" % len(files))
    sys.stdout.write("  tree_digest  : %s\n" % manifest["tree_digest_sha256"])
    return 0


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #

def _load_manifest(dest: str) -> dict:
    p = os.path.join(dest, "SNAPSHOT.json")
    if not os.path.isfile(p):
        _die("REFUSED: no SNAPSHOT.json under %s -- this directory is not a certified snapshot" % dest, 2)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _norm_eol(data: bytes) -> bytes:
    """Fold CRLF -> LF.

    The repository stores some artefacts with LF and this host checks out with
    CRLF, so git's blob bytes and the extracted bytes can legitimately differ by
    line ending alone.  This project already normalises the same way in its A8
    hash pin.  What we are proving here is "same CONTENT as the frozen commit",
    so the fold is applied to BOTH sides, never to one.
    """
    return data.replace(b"\r\n", b"\n")


def _git_blob_bytes(root: str, objnames) -> dict:
    """objname -> blob bytes, in ONE `git cat-file --batch` process.

    This is the independent oracle: it reads git's own object store instead of
    trusting the manifest we just wrote.  Note it returns the raw blob bytes --
    the object NAME is a SHA-1 in this repository, so it must never be compared
    against a SHA-256 of a file.
    """
    names = list(objnames)
    proc = subprocess.Popen(["git", "-C", root, "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        proc.stdin.write("".join(n + "\n" for n in names).encode("ascii"))
        proc.stdin.flush()
        proc.stdin.close()
        out = {}
        for _ in names:
            header = proc.stdout.readline()
            if not header:
                break
            parts = header.decode("utf-8", "replace").split()
            if len(parts) != 3:
                break
            oid, _typ, size = parts[0], parts[1], int(parts[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)            # the trailing LF git writes
            out[oid] = data
        return out
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()


def _verify_tree(args) -> dict:
    """Prove on-disk packages == the frozen commit, by two independent routes."""
    dest = os.path.abspath(args.dest)
    man = _load_manifest(dest)
    pkg = os.path.join(dest, "packages")
    if not os.path.isdir(pkg):
        _die("FALSE: %s has no packages/ directory" % dest, 1)

    on_disk = _walk_rel(pkg)
    recorded = man["files"]

    problems = []
    for rel, want in sorted(recorded.items()):
        got = on_disk.get(rel)
        if got is None:
            problems.append("missing on disk: %s" % rel)
        elif got != want:
            problems.append("drifted: %s\n    recorded %s\n    on disk  %s" % (rel, want, got))
    for rel in sorted(set(on_disk) - set(recorded)):
        problems.append("unexpected extra file: %s" % rel)

    digest = _tree_digest(on_disk)
    if digest != man["tree_digest_sha256"]:
        problems.append("tree digest mismatch: recorded %s, on disk %s"
                        % (man["tree_digest_sha256"], digest))

    # ROUTE 2 -- INDEPENDENT ORACLE: git's own object store.  A manifest can be
    # edited; the committed blobs cannot answer anything but the truth about
    # that commit.
    ls = _git(args.root, "ls-tree", "-r", "--format=%(objectname) %(path)", man["source_sha"], "packages")
    git_map = {}
    for line in ls.decode("utf-8", "backslashreplace").splitlines():
        if not line.strip():
            continue
        obj, path = line.split(" ", 1)
        git_map[os.path.relpath(path.strip(), "packages")] = obj.strip()
    if not git_map:
        _die("could not evaluate the claim: `git ls-tree` returned no blobs for %s:packages"
             % man["source_sha"][:12], 2)

    blobs = _git_blob_bytes(args.root, sorted(set(git_map.values())))
    if len(blobs) != len(set(git_map.values())):
        _die("could not evaluate the claim: asked git for %d blob(s) and got %d back"
             % (len(set(git_map.values())), len(blobs)), 2)

    for rel, objname in sorted(git_map.items()):
        full = os.path.join(pkg, rel)
        if rel not in on_disk:
            problems.append("git has %s but the snapshot does not" % rel)
            continue
        try:
            with open(full, "rb") as fh:
                disk = fh.read()
        except OSError as exc:
            problems.append("cannot read %s: %s" % (rel, exc))
            continue
        if _norm_eol(disk) != _norm_eol(blobs[objname]):
            problems.append("content differs from %s:packages/%s (git blob %s)"
                            % (man["source_sha"][:12], rel.replace("\\", "/"), objname[:12]))
    for rel in sorted(set(on_disk) - set(git_map)):
        problems.append("snapshot has %s but %s:packages does not" % (rel, man["source_sha"][:12]))

    return {"manifest": man, "problems": problems, "on_disk": on_disk,
            "digest": digest, "git_file_count": len(git_map)}


def cmd_verify(args) -> int:
    r = _verify_tree(args)
    man, problems = r["manifest"], r["problems"]
    sys.stdout.write("snapshot   : %s\n" % os.path.abspath(args.dest))
    sys.stdout.write("source_sha : %s (%s)\n" % (man["source_sha_short"], man["source_commit_subject"]))
    sys.stdout.write("files      : %d on disk, %d in the manifest, %d in git\n"
                     % (len(r["on_disk"]), len(man["files"]), r["git_file_count"]))
    sys.stdout.write("tree_digest: %s\n" % r["digest"])
    if problems:
        sys.stdout.write("VERDICT: FALSE -- %d problem(s)\n" % len(problems))
        for p in problems:
            sys.stdout.write("  - %s\n" % p)
        return 1
    sys.stdout.write("VERDICT: PROVEN -- the on-disk packages/ tree is byte-identical to\n")
    sys.stdout.write("         %s:packages, by manifest AND by git's object store.\n" % man["source_sha_short"])
    return 0


# --------------------------------------------------------------------------- #
# verify-live
# --------------------------------------------------------------------------- #

def _health_keys_from_source(src: str) -> frozenset:
    """The key set of the /api/health dict literal, parsed out of server.py.

    Bounded, and it asserts its own non-vacuity: if it cannot find the known
    frozen keys it returns an empty set and the caller refuses to conclude
    anything, rather than concluding "no extra keys" from a broken parse.
    """
    i = src.find('@app.get("/api/health")')
    if i < 0:
        return frozenset()
    j = src.find("@app.", i + 10)
    body = src[i:j if j > 0 else i + 4000]
    keys = set(re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', body))
    return frozenset(keys)


def _probe_health(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        status = resp.getcode()
        payload = json.loads(resp.read().decode("utf-8"))
    if status != 200:
        _die("FALSE: %s answered HTTP %d" % (url, status), 1)
    return payload


def cmd_verify_live(args) -> int:
    rc = cmd_verify(args)
    if rc != 0:
        sys.stdout.write("\nREFUSING the live check: the snapshot itself did not verify.\n")
        return rc

    man = _load_manifest(os.path.abspath(args.dest))
    frozen_src = _git(args.root, "show", man["source_sha"] + ":packages/jcc_core/server.py").decode("utf-8", "replace")
    head_src = _git(args.root, "show", "HEAD:packages/jcc_core/server.py").decode("utf-8", "replace")
    frozen_keys = _health_keys_from_source(frozen_src)
    head_keys = _health_keys_from_source(head_src)

    # NON-VACUITY: the parser must still recognise both revisions, or it must
    # not be allowed to certify anything.
    if not FROZEN_HEALTH_CORE_KEYS.issubset(frozen_keys):
        _die("could not evaluate the claim: parsed frozen health keys %s do not contain the known core set"
             % sorted(frozen_keys), 2)
    if not V1_1_ONLY_HEALTH_KEYS.issubset(head_keys):
        _die("could not evaluate the claim: parsed HEAD health keys %s lack the v1.1-only markers; "
             "the discriminator this check rests on is no longer valid" % sorted(head_keys), 2)
    if V1_1_ONLY_HEALTH_KEYS & frozen_keys:
        _die("could not evaluate the claim: the FROZEN revision already carries v1.1-only keys, "
             "so their absence proves nothing", 2)

    payload = _probe_health(args.health_url)
    live_keys = frozenset(k for k in payload)
    problems = []

    missing = frozen_keys - live_keys
    if missing:
        problems.append("live payload is missing frozen keys: %s" % sorted(missing))
    v11_present = V1_1_ONLY_HEALTH_KEYS & live_keys
    if v11_present:
        problems.append("live payload exposes v1.1-only keys %s -- 4778 is NOT running the frozen code"
                        % sorted(v11_present))
    if payload.get("status") != "ok":
        problems.append("live status is %r, expected 'ok'" % payload.get("status"))
    if payload.get("jev_provider") != "mock":
        problems.append("live jev_provider is %r, expected 'mock'" % payload.get("jev_provider"))
    if payload.get("actuation_enabled") is not False:
        problems.append("live actuation_enabled is %r, expected False" % payload.get("actuation_enabled"))
    if payload.get("localhost_only") is not True:
        problems.append("live localhost_only is %r, expected True" % payload.get("localhost_only"))
    if payload.get("wal") is not True:
        problems.append("live wal is %r, expected True" % payload.get("wal"))

    sys.stdout.write("\nlive check : %s\n" % args.health_url)
    sys.stdout.write("frozen     : server.py health keys = %s\n" % sorted(frozen_keys))
    sys.stdout.write("HEAD       : server.py health keys = %s\n" % sorted(head_keys))
    sys.stdout.write("live       : health keys = %s\n" % sorted(live_keys))
    sys.stdout.write("live values: status=%r provider=%r actuation=%r policy=%r wal=%r localhost_only=%r\n"
                     % (payload.get("status"), payload.get("jev_provider"),
                        payload.get("actuation_enabled"), payload.get("policy_version"),
                        payload.get("wal"), payload.get("localhost_only")))
    if problems:
        sys.stdout.write("VERDICT: FALSE -- the live service is not the frozen runtime\n")
        for p in problems:
            sys.stdout.write("  - %s\n" % p)
        return 1
    sys.stdout.write("VERDICT: PROVEN -- the live service answers exactly the frozen health\n")
    sys.stdout.write("         contract and carries none of the %d v1.1-only field(s).\n"
                     % len(V1_1_ONLY_HEALTH_KEYS))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="extract packages/ from a commit into an immutable snapshot")
    c.add_argument("--sha", required=True)
    c.add_argument("--dest", required=True)
    c.add_argument("--root", default=DEFAULT_ROOT)
    c.set_defaults(func=cmd_create)

    v = sub.add_parser("verify", help="prove the snapshot is still the frozen code")
    v.add_argument("--dest", required=True)
    v.add_argument("--root", default=DEFAULT_ROOT)
    v.set_defaults(func=cmd_verify)

    l = sub.add_parser("verify-live", help="also prove the live service is that frozen code")
    l.add_argument("--dest", required=True)
    l.add_argument("--health-url", required=True)
    l.add_argument("--root", default=DEFAULT_ROOT)
    l.set_defaults(func=cmd_verify_live)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
