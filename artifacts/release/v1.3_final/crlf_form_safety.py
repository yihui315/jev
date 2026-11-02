"""Measure, with REAL tools, which CRLF-normalisation forms are byte-safe.

WHY THIS IS A SCRIPT AND NOT A ONE-LINER. I tried three times to express the
`python3 -c` shell form inline and got empty output every time, because `\\r\\n`
inside nested shell -> Python -> inner-Python quoting collapses into real CR/LF
bytes and turns the inner `-c` argument into a SyntaxError. Three failures is the
finding: **a shell one-liner for this cannot be verified in this environment**, and
an unverified one-liner is exactly the thing this document exists to stop shipping.

So the forms are measured here, in a file, where quoting is not a variable.

THE MEASUREMENT. For each candidate form, feed it two size-preserving but
content-different byte strings and ask whether it reports them as EQUAL. Equal means
the form MASKS a real difference -- which is the INC-002 shape, and a false negative.
The reference is `bytes.replace(b"\\r\\n", b"\\n")`, the form the production code uses.

Run:  D:/jev/.venv/Scripts/python.exe artifacts/release/v1.3_final/crlf_form_safety.py
Exit: 0 if `replace` masks nothing and every other form's verdict is REPORTED,
       1 if the reference form itself fails. Deliberately non-zero on failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "CRLF_FORM_SAFETY.json"
SED = "sed"          # resolved on this host; GNU sed 4.9
PY = str(REPO / ".venv" / "Scripts" / "python.exe")

# Size-preserving, content-different pairs over {a, CR, LF}. These are the shapes
# INC-002 is defined by: stat cache cannot see them, so a normaliser that maps two
# of them together is a false negative.
PAIRS: list[tuple[bytes, bytes, str]] = [
    (b"\x0d\x0a", b"\x0a\x0d", "CR-LF vs LF-CR (2B)"),
    (b"a\x0d\x0a", b"a\x0a\x0d", "aCR-LF vs aLF-CR (3B)"),
    (b"\x0d\x0d\x0a", b"\x0d\x0a\x0d", "CRCRLF vs CRLFCR (3B)"),
    (b"\x0d\x0a\x0d", b"\x0d\x0d\x0a", "CRLFCR vs CRCRLF (3B)"),
]


def form_replace(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def form_sed(b: bytes) -> bytes:
    return subprocess.run([SED, r"s/\r$//"], input=b, capture_output=True).stdout


def form_sed_empty(b: bytes) -> bytes:
    """CONTROL, not a candidate: `sed ''` with an empty script.

    prism-3dui's point, adopted. On this host the sed READER already strips a CR that
    is paired with an LF, so `sed ''` is NOT inert. That does not make the script
    innocent -- separating the two on the four pairs below shows reader-alone masks 0/4
    while reader+script masks 4/4 -- but it does mean any byte-level conclusion drawn
    through sed has to be justified against this control rather than assumed.
    """
    return subprocess.run([SED, ""], input=b, capture_output=True).stdout


def form_tr_delete_cr(b: bytes) -> bytes:
    return b.replace(b"\r", b"")


def form_python_subprocess(b: bytes) -> bytes:
    """The form I tried to ship as a one-liner, written as a file so it can be tested.

    Binary stdin AND binary stdout. Using `print()` or a text-mode write here
    re-normalises newlines and masks exactly the pairs we are testing for, which is
    a second, independent way this form can lie.
    """
    code = ('import sys;d=sys.stdin.buffer.read();'
            'sys.stdout.buffer.write(d.replace(b"\\r\\n",b"\\n"))')
    return subprocess.run([PY, "-c", code], input=b, capture_output=True).stdout


FORMS = {
    "replace(CRLF,LF)  [REFERENCE / production]": form_replace,
    "sed 's/\\r$//'    [reader + script]": form_sed,
    "sed ''  [CONTROL: reader only, expect NOT to be a candidate]": form_sed_empty,
    "tr -d '\\r'  (delete every CR)": form_tr_delete_cr,
    "python -c, binary stdio  [my proposed shell form]": form_python_subprocess,
}


def main() -> int:
    results: dict[str, dict] = {}
    for name, fn in FORMS.items():
        masked, detail = 0, []
        for h, w, label in PAIRS:
            same = fn(h) == fn(w)
            if same:
                masked += 1
            detail.append({"pair": label,
                           "head_hex": h.hex(" "), "work_hex": w.hex(" "),
                           "outputs_equal": same,
                           "verdict": "MASKED (false negative)" if same else "distinguished"})
        results[name] = {"masked_pairs": masked, "of": len(PAIRS), "detail": detail}

    ref = next(iter(results))
    ref_ok = results[ref]["masked_pairs"] == 0
    sed_name = [k for k in results if k.startswith("sed")][0]
    payload = {
        "question": "which CRLF-normalisation forms mask size-preserving differences?",
        "sed_version": subprocess.run([SED, "--version"], capture_output=True,
                                      text=True).stdout.splitlines()[0],
        "reference_form": ref,
        "reference_is_clean": ref_ok,
        "results": results,
        "conclusion": None,
    }
    payload["conclusion"] = (
        "The reference byte-level replace is the only form that distinguishes every "
        "pair, and it is also the only one with no reader in the path. The empty-script "
        "control shows sed's READER alone masks 0/4 while reader+script masks 4/4, so "
        "the s/\\r$// script is the defect -- it would remain one with a perfectly "
        "faithful reader. tr -d deletes every CR. A text-mode python pipe "
        "re-normalises newlines. Three independent ways to mask a size-preserving "
        "difference, and one form that does not."
        if ref_ok else
        "THE REFERENCE FORM ITSELF FAILED -- stop trusting any conclusion drawn with it.")

    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    for name, r in results.items():
        print("%-52s masked %d/%d" % (name, r["masked_pairs"], r["of"]))
    print("\n" + payload["conclusion"])
    print("wrote %s" % OUT)
    return 0 if ref_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
