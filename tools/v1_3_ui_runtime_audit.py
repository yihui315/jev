#!/usr/bin/env python3
"""JCC v1.3 -- audit the Visual UI serving path (Wave 8 contract).

WHY THIS EXISTS (and why it is not a grep)
------------------------------------------
Wave 8 requires that the product SPA is served by a real ASGI server under
uvicorn, and that python's stdlib demo server (``http.server``) is not in the
runtime path.

The first version of this check was a substring scan of serve_visual.py for
"http.server" / "SimpleHTTPRequestHandler" / "HTTPServer". It reported FAIL on a
file that is *correct*: serve_visual.py's module docstring explains the rewrite
("It used to be a ``http.server.HTTPServer`` + ``SimpleHTTPRequestHandler``").
Prose about a removed dependency is not a dependency. That is the same defect
class this project has hit before -- **the instrument reported a finding that the
ground truth did not support** -- and the fix is to make the instrument answer
the real question.

The real question is "does this file IMPORT or REFERENCE the stdlib server?",
which is exactly what the AST can answer and what text cannot:

  * ``import http.server``                  -> ast.Import          (caught)
  * ``from http.server import HTTPServer``   -> ast.ImportFrom      (caught)
  * ``from http import server``              -> ast.ImportFrom      (caught)
  * ``HTTPServer(...)``                      -> ast.Name/Attribute   (caught)
  * a docstring or comment mentioning it     -> string / not in AST  (NOT caught)

NON-VACUOUS BY CONSTRUCTION
---------------------------
"Found no banned token" is trivially true of an empty file and of a stub, so a
clean bill of health is only issued when the POSITIVE evidence is also present:
uvicorn is imported, ``uvicorn.run`` is called, a Starlette application is
referenced, and the file really serves files (FileResponse). A missing positive
is a FAIL, not a silent pass.

Exit codes: 0 = PASS, 1 = FAIL, 2 = the file is missing or does not parse.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

#: Modules whose presence means the stdlib demo server is in the serving path.
BANNED_MODULES = ("http.server",)

#: Identifiers that can only come from the stdlib server (or a re-implementation
#: of it). Referencing one of these in CODE is what we refuse.
BANNED_SYMBOLS = (
    "HTTPServer",
    "ThreadingHTTPServer",
    "SimpleHTTPRequestHandler",
    "BaseHTTPRequestHandler",
)


def _dotted(node: ast.AST) -> str:
    """Best-effort dotted name for an Attribute/Name chain, else ''."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def audit_source(path: str, source: str) -> dict:
    """Return the audit finding for one source file. Pure; no I/O."""
    tree = ast.parse(source, filename=path)

    banned_imports: list[str] = []
    banned_refs: list[str] = []
    imports: set[str] = set()
    attrs: set[str] = set()
    calls: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                for mod in BANNED_MODULES:
                    if alias.name == mod or alias.name.startswith(mod + "."):
                        banned_imports.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod:
                imports.add(mod)
            for bm in BANNED_MODULES:
                if mod == bm or mod.startswith(bm + "."):
                    banned_imports.append(f"line {node.lineno}: from {mod} import ...")
                # `from http import server` reaches http.server too.
                if mod == "http":
                    for alias in node.names:
                        if alias.name == "server":
                            banned_imports.append(
                                f"line {node.lineno}: from http import server"
                            )
            for alias in node.names:
                if mod:
                    imports.add(f"{mod}.{alias.name}")
                # The BARE name matters too: "from x import FileResponse" is used
                # as `FileResponse(...)`, which is an ast.Name, never an
                # ast.Attribute -- so an attribute-only scan would miss it and
                # report "does not serve files" about a file that does. That was
                # a real false FAIL while building this tool.
                imports.add(alias.name)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
            if node.attr in BANNED_SYMBOLS:
                banned_refs.append(f"line {node.lineno}: .{node.attr}")
            dotted = _dotted(node)
            if dotted:
                for bm in BANNED_MODULES:
                    if dotted == bm or dotted.startswith(bm + "."):
                        banned_imports.append(f"line {node.lineno}: {dotted}")
                calls.add(dotted)
        elif isinstance(node, ast.Name):
            if node.id in BANNED_SYMBOLS:
                banned_refs.append(f"line {node.lineno}: {node.id}")

    positives = {
        "uvicorn_imported": any(
            i == "uvicorn" or i.startswith("uvicorn.") for i in imports
        ),
        "uvicorn_run_called": any(c == "uvicorn.run" for c in calls),
        "starlette_app_referenced": any(
            i.startswith("starlette") for i in imports
        ),
        "serves_files": any(
            name in attrs or name in imports for name in ("FileResponse", "StaticFiles")
        ),
    }

    problems: list[str] = []
    if banned_imports:
        problems.append(
            "stdlib http.server is imported: " + ", ".join(sorted(set(banned_imports)))
        )
    if banned_refs:
        problems.append(
            "stdlib http.server symbols are referenced in code: "
            + ", ".join(sorted(set(banned_refs)))
        )
    for key, ok in positives.items():
        if not ok:
            problems.append("missing positive evidence: " + key)

    return {
        "file": path,
        "verdict": "PASS" if not problems else "FAIL",
        "problems": problems,
        "banned_imports": sorted(set(banned_imports)),
        "banned_references": sorted(set(banned_refs)),
        "positives": positives,
        "note": (
            "AST-based: a docstring or comment that mentions http.server is not "
            "an import and is deliberately not reported."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="audit the JCC v1.3 UI serving path")
    ap.add_argument("--file", required=True, help="path to serve_visual.py")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ns = ap.parse_args(argv)

    if not os.path.isfile(ns.file):
        out = {"file": ns.file, "verdict": "FAIL", "problems": ["file not found"]}
        sys.stdout.write(json.dumps(out) + "\n")
        return 2

    with open(ns.file, "r", encoding="utf-8") as fh:
        source = fh.read()

    try:
        finding = audit_source(ns.file, source)
    except SyntaxError as exc:
        finding = {
            "file": ns.file,
            "verdict": "FAIL",
            "problems": [f"does not parse: {exc}"],
        }
        sys.stdout.write(json.dumps(finding) + "\n")
        return 2

    if ns.json:
        sys.stdout.write(json.dumps(finding) + "\n")
    else:
        sys.stdout.write(f"file    : {finding['file']}\n")
        sys.stdout.write(f"verdict : {finding['verdict']}\n")
        for k, v in sorted(finding["positives"].items()):
            sys.stdout.write(f"  positive {k}: {v}\n")
        for p in finding["problems"]:
            sys.stdout.write(f"  PROBLEM: {p}\n")
        if not finding["problems"]:
            sys.stdout.write("  ok: ASGI/uvicorn serving path; no stdlib http.server\n")

    return 0 if finding["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
