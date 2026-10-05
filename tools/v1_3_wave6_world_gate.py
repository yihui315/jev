"""WAVE 6 PRODUCT GATE — OPERATIONAL 3D WORLD (owner: Metric).

THE GATE'S QUESTION
-------------------
"The 3D plane is a pure projection of the v1.3 domain: it shows the user's own
projects / missions / deliverables, an empty domain renders an EMPTY world (no
decorative nodes), colour is a pure function of a closed status vocabulary so
UNKNOWN is never green, positions are deterministic, and the plane has NO write
path at all (it is not a source of truth)."

This gate is frontend-only, so it (a) drives the real TypeScript compiler and the
real vitest runner, and (b) adds STRUCTURAL falsifiers that read the source and
fail if the world ever grows a mutation path.

FAIL-CLOSED PHILOSOPHY
----------------------
  * ``TSC_CLEAN`` — the build type-checks.
  * ``WORLD_TESTS_PASS`` — the projection + navigation + status suites pass.
  * ``WORLD_HAS_NO_WRITE_PATH`` — neither the projection nor the scene contains
    fetch / POST / token, so the plane cannot mutate domain state.
  * ``WORLD_IMPORTS_TYPES_ONLY`` — domainWorld.ts imports the API client
    TYPE-ONLY (it must never hold a live client).
  * ``NAV_DECLARES_OPERATIONAL_WORLD`` — the registry exposes `world` (operational)
    and `engine-world` (the v1.2 engine scene), so nothing was silently deleted.

Evidence is written to ``.workbuddy/scratch/v1_3_wave6_gate_<stamp>.json``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "apps" / "command_center_3d"
SCRATCH = REPO / ".workbuddy" / "scratch"

NODE_CANDIDATES = [
    os.environ.get("JCC_NODE"),
    r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3\node.exe",
    r"D:\Program Files\nodejs\node.exe",
]


def _node() -> Optional[str]:
    for c in NODE_CANDIDATES:
        if c and os.path.exists(c):
            return c
    return None


def _run(cmd: List[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss" % timeout


class Checks:
    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []

    def add(self, step: str, ok: bool, value: Any = None, note: str = "") -> None:
        self.steps.append({"step": step, "ok": bool(ok), "value": value, "note": note})

    @property
    def failed(self) -> List[Dict[str, Any]]:
        return [s for s in self.steps if not s["ok"]]

    def payload(self, gate: str) -> Dict[str, Any]:
        return {
            "gate": gate,
            "verdict": "PASS" if not self.failed and self.steps else "FAIL",
            "passed": sum(1 for s in self.steps if s["ok"]),
            "total": len(self.steps),
            "failures": self.failed,
            "steps": self.steps,
            "ts": time.time(),
        }


def _write(checks: Checks) -> str:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = SCRATCH / ("v1_3_wave6_gate_%s.json" % stamp)
    out.write_text(json.dumps(checks.payload("WAVE6_OPERATIONAL_WORLD"), indent=2),
                   encoding="utf-8")
    return str(out)


def main() -> int:
    checks = Checks()
    node = _node()
    if node is None:
        checks.add("NODE_FOUND", False, None, "no node runtime found")
        _write(checks)
        return 1
    checks.add("NODE_FOUND", True, node, "node runtime located")

    tsc = APP / "node_modules" / "typescript" / "bin" / "tsc"
    vitest = APP / "node_modules" / "vitest" / "vitest.mjs"

    # 1. the real type-checker
    rc, out = _run([node, str(tsc), "--noEmit"], APP)
    checks.add("TSC_CLEAN", rc == 0, {"rc": rc, "tail": out.strip()[-400:]},
               "tsc --noEmit exits 0")

    # 2. the projection / navigation / status suites (the behavioural falsifiers)
    env = dict(os.environ)
    env["TEMP"] = str(APP / ".vitetmp")
    env["TMP"] = str(APP / ".vitetmp")
    try:
        p = subprocess.run(
            [node, str(vitest), "run",
             "src/store/domainWorld.test.ts",
             "src/app/navigation.test.ts",
             "src/app/status.test.ts"],
            cwd=str(APP), capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace", env=env)
        vrc, vout = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        vrc, vout = 124, "TIMEOUT"
    checks.add("WORLD_TESTS_PASS", vrc == 0,
               {"rc": vrc, "tail": vout.strip()[-400:]},
               "projection + navigation + status suites pass")

    # 3. structural: the projection and scene have NO write path
    #
    # NEGATIVE EXISTENCE ASSERTION -- THIS COMES FIRST, AND IT COMES FIRST ON
    # PURPOSE.
    #
    # Everything below is a FEATURE COUNT: count the forbidden tokens, and if
    # there are none, declare that the plane has no write path. That inference
    # is only sound while the file still HAS content. INC-002 replaced a tracked
    # file's bytes with NULs and left its size -- and therefore git's stat
    # cache -- intact; a file with no tokens produces no matches, so a
    # count-the-features check reports "no write path" about a file that no
    # longer exists as source. Measured on a size-preserving-zeroed fixture
    # (tools/detector_blindspot_probe.py): this check returned ok=true with
    # zeroed inputs.
    #
    # So the sources must be shown to EXIST as source before their contents are
    # allowed to imply anything. Refusing is not optional here: "I read an empty
    # file and found no write path" is not a weaker claim, it is a false one.
    #
    # Thresholds are deliberately far below any real source file, so this can
    # only fire on a file whose content has actually been removed.
    MIN_SOURCE_BYTES = 32
    _unreadable: List[str] = []
    for _label, _rel in (("projection", "src/store/domainWorld.ts"),
                         ("scene", "src/scene/OperationalWorld.tsx"),
                         ("navigation", "src/app/navigation.ts")):
        _p = APP / _rel
        try:
            _raw = _p.read_bytes()
        except OSError as exc:
            _unreadable.append("%s (%s): cannot read: %s" % (_label, _rel, exc))
            continue
        if len(_raw) < MIN_SOURCE_BYTES:
            _unreadable.append(
                "%s (%s): %d bytes on disk, below the %d-byte floor -- content "
                "has been removed (zeroed or truncated), so nothing can be "
                "concluded from it" % (_label, _rel, len(_raw), MIN_SOURCE_BYTES))
            continue
        if b"\x00" in _raw:
            _unreadable.append(
                "%s (%s): contains NUL bytes -- INC-002 signature (content "
                "replaced, size preserved)" % (_label, _rel))
            continue
        # A source file must also carry SOME non-whitespace text. A file of the
        # right size made entirely of spaces is the same class of damage.
        if not _raw.decode("utf-8", "replace").strip():
            _unreadable.append("%s (%s): no non-whitespace content" % (_label, _rel))

    checks.add("SOURCES_ARE_READABLE_SOURCE",
               not _unreadable,
               {"unreadable": _unreadable, "min_bytes": MIN_SOURCE_BYTES},
               "every file this gate reasons about still holds real source; an "
               "unreadable or emptied file is UNKNOWN, never compliant")

    world_src = (APP / "src" / "store" / "domainWorld.ts").read_text(encoding="utf-8")
    scene_src = (APP / "src" / "scene" / "OperationalWorld.tsx").read_text(encoding="utf-8")
    forbidden = re.compile(r"\bfetch\s*\(|\bPOST\b|X-JCC-Token|\.createProject\s*\(|"
                           r"\.verifyDeliverable\s*\(|\.resolveAttention\s*\(|"
                           r"\.addArtifact\s*\(")
    w_hits = forbidden.findall(world_src)
    s_hits = forbidden.findall(scene_src)
    # `and not _unreadable` is load-bearing, not belt-and-braces. A count of
    # zero forbidden tokens is only evidence of a clean file when there WAS a
    # file to count. Without this term the check reports "no write path" on
    # precisely the input where it has no right to an opinion, and the gate's
    # overall red would rest entirely on the assertion above -- which is
    # correct, but would leave this check individually lying.
    checks.add("WORLD_HAS_NO_WRITE_PATH",
               not w_hits and not s_hits and not _unreadable,
               {"domainWorld": w_hits, "scene": s_hits,
                "unreadable_sources": _unreadable},
               "neither the projection nor the scene can mutate domain state")

    # 4. domainWorld.ts imports the API client TYPE-ONLY
    api_imports = re.findall(r'import\s+(?:type\s+)?\{?[^}]*\}?\s*from\s+["\']([^"\']+)["\']',
                             world_src)
    imports_client_value = bool(re.search(
        r'import\s+(?!type\b)\{[^}]*\bDomainApi\b[^}]*\}\s*from', world_src))
    checks.add("WORLD_IMPORTS_TYPES_ONLY",
               not imports_client_value and any("domainApi" in i for i in api_imports),
               {"imports": api_imports, "value_client": imports_client_value},
               "the projection imports the API surface type-only")

    # 5. the registry still declares BOTH worlds (nothing silently deleted)
    nav_src = (APP / "src" / "app" / "navigation.ts").read_text(encoding="utf-8")
    has_world = 'id: "world"' in nav_src
    has_engine = 'id: "engine-world"' in nav_src
    checks.add("NAV_DECLARES_OPERATIONAL_WORLD",
               has_world and has_engine,
               {"world": has_world, "engine-world": has_engine},
               "both the operational world and the v1.2 engine world exist")

    out_path = _write(checks)
    pl = checks.payload("x")
    print("gate WAVE6_OPERATIONAL_WORLD verdict=%s passed=%d/%d"
          % (pl["verdict"], pl["passed"], len(checks.steps)))
    for s in checks.failed:
        print("  FAIL %s: %s" % (s["step"], s["value"]))
    print("wrote", out_path)
    return 0 if not checks.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
