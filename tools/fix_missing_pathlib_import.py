"""Repair the missing `from pathlib import Path` import in 26 shipped scripts.

THE DEFECT. Commit cf9b2ea ("fix(relocation): the control plane derives its
repo root instead of naming a drive") replaced a hardcoded ``r"F:\\jev"`` with
``Path(__file__).resolve()`` in twenty-six shipped scripts, but did not add the
``pathlib`` import to any of them. Every one of those scripts is now a
LOAD-TIME failure: ``NameError: name 'Path' is not defined`` raised while the
module body executes, before ``main()`` is ever reached. The controller the FINAL
pipeline depends on is one of the twenty-six.

WHY IT SURVIVED. ``ROOT = os.environ.get("JCC_ROOT") or str(Path(...))``
short-circuits: the watchdog exports JCC_ROOT, so a watchdog-launched run never
evaluates the right-hand side and never raises. The NameError only fires when
JCC_ROOT is ABSENT -- i.e. exactly when a human or a test runs the script
directly. The failure was therefore invisible to every run that went through the
watchdog, and visible to every run that did not.

THE FIX. Insert ``from pathlib import Path`` into the existing stdlib import
block, keeping alphabetical order by module name (os < pathlib < subprocess <
sys), so the diff is one line per file and no file gets a second import block.

Idempotent: a file that already imports pathlib is skipped. Exits non-zero if
any file still fails to import afterwards.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMPORT_LINE = "from pathlib import Path"

#: Matches a contiguous run of plain `import x` / `from x import y` statements.
#: Deliberately narrow: it must not swallow the module docstring, `from __future__`,
#: or any decorated/indented code.
_BLOCK = re.compile(
    r"^(?:import [A-Za-z_][\w.]*\s*$"
    r"|from [A-Za-z_][\w.]* import [^\n(]+$"
    r"|from [A-Za-z_][\w.]* import \([^\n]*$"
    r"|from [A-Za-z_][\w.]* import [^\n]*\([^\n]*$)",
    re.M,
)

#: stdlib modules sort after `pathlib` iff their name is > "pathlib".
_AFTER = {"re", "shutil", "signal", "socket", "sqlite3", "subprocess", "sys",
          "tempfile", "threading", "time", "traceback", "typing", "uuid",
          "warnings", "winreg", "xml", "zipfile"}


def already_imports(text: str) -> bool:
    return bool(re.search(r"^\s*(?:from pathlib import|import pathlib)\b", text, re.M))


def module_of(line: str) -> str:
    m = re.match(r"^import ([A-Za-z_][\w.]*)", line) or \
        re.match(r"^from ([A-Za-z_][\w.]*) import", line)
    return m.group(1) if m else ""


def insert_import(text: str) -> str | None:
    """Return `text` with the import added, or None if no safe anchor exists."""
    if already_imports(text):
        return None
    lines = text.splitlines(keepends=True)
    idxs = [i for i, ln in enumerate(lines) if _BLOCK.match(ln)]
    if not idxs:
        return None
    # Insert after the last stdlib import whose module sorts <= "pathlib",
    # so the block stays alphabetical. Fall back to the end of the run.
    anchor = None
    for i in idxs:
        if module_of(lines[i]) not in _AFTER:
            anchor = i
    if anchor is None:
        anchor = idxs[-1]
    lines.insert(anchor + 1, IMPORT_LINE + "\n")
    return "".join(lines)


def _binds_path_at_module_scope(tree: "ast.Module") -> bool:
    """True when `from pathlib import Path` (or `import pathlib`) is at top level.

    Walks only `tree.body`, so an `import Path` nested inside a function does not
    count -- the defect is precisely a module-level use with no module-level
    binding.
    """
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            if any(alias.name == "Path" for alias in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name == "pathlib" for alias in node.names):
                return True
    return False


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]]
    if not targets:
        print("usage: fix_missing_pathlib_import.py <file> [<file> ...]")
        return 2
    changed, skipped, failed = [], [], []
    # Every target, whether it was repaired or already correct, is verified in
    # the pass below. The `to_verify` list exists because the repair loop used to
    # `continue` on the already-imports branch, which meant a file whose import
    # was present but WHOSE `Path` USE was not at module scope never got checked
    # -- found by mutation M2 in
    # artifacts/release/v1.3_final/mutation_check.py, which re-indents the
    # `Path(__file__)` use into a function body and watches this tool wave it
    # through with `changed=0 skipped=1 failed=0` and exit 0. Skipping
    # verification for the files that most obviously need it was exactly backwards.
    to_verify = []
    for path in targets:
        # Callers may pass repo-relative paths; resolve before the REPO check.
        path = (REPO / path).resolve()
        rel = path.relative_to(REPO)
        original = path.read_text(encoding="utf-8")
        new = insert_import(original)
        to_verify.append(path)
        if new is None:
            (skipped if already_imports(original) else failed).append(str(rel))
            continue
        path.write_text(new, encoding="utf-8", newline="")
        changed.append(str(rel))

    # Prove every file COMPILES and RESOLVES `Path` -- without EXECUTING it.
    #
    # MEASURED HAZARD, and the reason this is not `exec_module`. Several of
    # these scripts do real work at MODULE level: importing
    # `tools/continuation/regenerate_surfaces.py` rewrote 16 tracked files
    # across `integrations/`, `config/` and `packages/`, created three new
    # untracked files, and launched two background processes, because
    # `exec_module` runs the module body top to bottom and `if __name__ ==
    # "__main__"` does not guard that code. A repair script that verifies by
    # executing the file it is repairing is a repair script that can rewrite the
    # repository. So: compile to bytecode (catches SyntaxError) and resolve the
    # `Path` binding through the AST (catches the actual defect, a name that is
    # never bound at module scope).
    for path in to_verify:
        path = (REPO / path).resolve() if not path.is_absolute() else path
        rel = path.relative_to(REPO)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
            compile(tree, str(rel), "exec")
        except SyntaxError as exc:
            failed.append("%s -> SyntaxError: %s" % (rel, exc))
            continue
        if not _binds_path_at_module_scope(tree):
            failed.append("%s -> `Path` still not bound at module scope" % rel)

    print("changed=%d skipped=%d failed=%d" % (len(changed), len(skipped), len(failed)))
    for name in changed:
        print("  CHANGED %s" % name)
    for name in skipped:
        print("  SKIPPED (already imports pathlib) %s" % name)
    for name in failed:
        print("  FAILED  %s" % name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
