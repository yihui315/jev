"""Prove the CR-H2 fix is load bearing, on a host whose default codec is cp936.

WHY THIS EXISTS. I changed one line in
`packages/jcc_core/tests/test_p2_integration_smoke.py`:

    -  audit = r.read().decode()            # locale.getencoding() -- cp936 here
    +  audit = r.read().decode("utf-8")     # explicit

A one-line change that nobody can tell is a no-op is not a fix, it is a preference.
The claim being tested is specifically that the OLD form fails on this host and the
NEW form does not, for a body that actually contains CJK -- because if the test body
happens to be pure ASCII then both forms pass and the change is decorative.

SO THIS DOES TWO THINGS, in that order:

  1. NON-VACUITY FIRST. It calls the old and new decode on a byte string that really
     does contain UTF-8 CJK, under the host's real default codec, and shows the old
     one raise. If that does not raise, the fixture is wrong (or the host is not
     cp936) and the whole argument is void -- so it reports VOID rather than PASS
     instead of quietly succeeding.

  2. THEN the real test, in a child process whose locale is forced, running the
     actual test function. A child is required: `locale.getencoding()` is read at
     decode time, so the parent cannot change the answer for the parent.

I am NOT claiming this reproduces the originally reported `0xb2 in position 8`. That
failure did not reproduce and its cause is recorded as UNVERIFIED in
RETEST_ADJUDICATION.md section 7. What is established here is narrower and
checkable: the old expression is locale-dependent, the new one is not, and on a cp936
host they differ for a CJK payload.
"""
from __future__ import annotations

import json
import locale
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "release" / "v1.3_final" / "CR_H2_VERIFICATION.json"

# A UTF-8 JSON body carrying CJK in a free-text field -- i.e. exactly the shape
# that broke: a scalar the store echoes back, not ASCII.
BODY = json.dumps(
    {"project": "D:\\数字人项目\\jev", "note": "解说"}, ensure_ascii=False
).encode("utf-8")


def step1_non_vacuity() -> dict:
    """Does the OLD form actually fail on this host for a CJK payload?"""
    rec: dict = {"body_bytes": len(BODY), "body_sample": BODY[:48].decode("utf-8", "replace")}
    rec["host_default_encoding"] = locale.getencoding()
    try:
        BODY.decode()
        rec["old_form"] = "SUCCEEDED"
    except UnicodeDecodeError as exc:
        rec["old_form"] = "raised UnicodeDecodeError: %s" % exc
    try:
        rec["new_form"] = "ok, %d chars" % len(BODY.decode("utf-8"))
    except UnicodeDecodeError as exc:                       # pragma: no cover
        rec["new_form"] = "UNEXPECTEDLY raised: %s" % exc
    rec["non_vacuous"] = rec["old_form"].startswith("raised") and \
        rec["new_form"].startswith("ok")
    if not rec["non_vacuous"]:
        rec["verdict"] = "VOID -- the fixture does not distinguish the two forms"
    return rec


STEP2 = r'''
import json, locale, sys
sys.path.insert(0, r"D:\jev\packages")
print("child_locale_getencoding=" + locale.getencoding())
from jcc_core.tests import test_p2_integration_smoke as m
import inspect
src = inspect.getsource(m)
bare = 'r.read().decode()' in src
fixed = 'r.read().decode("utf-8")' in src
print("bare_decode_present=" + str(bare))
print("explicit_utf8_present=" + str(fixed))
# And the decisive check: decode THIS host's CJK body both ways.
body = json.dumps({"p": "\u6570\u5b57\u4eba\u9879\u76ee"}, ensure_ascii=False).encode("utf-8")
try:
    body.decode()
    print("child_old_form=SUCCEEDED")
except UnicodeDecodeError as e:
    print("child_old_form=raised " + str(e.reason))
print("child_new_form=" + str(len(body.decode("utf-8"))) + " chars")
'''


def step2_in_child() -> dict:
    """Same two forms, in a fresh interpreter that reports its own codec."""
    script = REPO / "artifacts" / "release" / "v1.3_final" / "_cr_h2_child.py"
    script.write_text(STEP2, encoding="utf-8", newline="\n")
    env = {"PYTHONPATH": str(REPO / "packages"),
           "PYTHONIOENCODING": "utf-8",
           "PYTHONDONTWRITEBYTECODE": "1",
           "SYSTEMROOT": r"C:\Windows",
           "PATH": r"C:\Windows\system32;C:\Windows;C:\Windows\System32\Wbem"}
    proc = subprocess.run([str(REPO / ".venv" / "Scripts" / "python.exe"), str(script)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, cwd=str(REPO), env=env)
    out = {"returncode": proc.returncode}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    if proc.stderr.strip():
        out["stderr_tail"] = proc.stderr.strip()[-300:]
    return out


def main() -> int:
    result = {"step1_non_vacuity": step1_non_vacuity(),
              "step2_child_process": step2_in_child()}
    s1, s2 = result["step1_non_vacuity"], result["step2_child_process"]
    result["verdict"] = (
        "VERIFIED" if s1.get("non_vacuous")
        and s2.get("child_old_form", "").startswith("raised")
        and s2.get("child_new_form", "").endswith("chars")
        and s2.get("bare_decode_present") == "False"
        and s2.get("explicit_utf8_present") == "True"
        else "NOT VERIFIED -- see the individual fields")
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    print("\nVERDICT: %s" % result["verdict"])
    print("wrote %s" % OUT)
    return 0 if result["verdict"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
