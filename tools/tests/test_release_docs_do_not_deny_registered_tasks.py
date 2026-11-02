"""Release documents must not deny a scheduled task that is registered.

WHY THIS EXISTS
---------------
Four generators and THREE generated reports asserted, as a *scope limit and
honest caveat*, that the scheduled task ``JEV-JCC-Continuation-Watchdog`` is
NOT registered on this host. That claim was produced by a probe that could not
answer at all: inside the agent sandbox ``Get-ScheduledTask`` fails (the Task
Scheduler CIM provider is unreachable), and the probe read "could not query" as
"does not exist".

The task is in fact registered and fires every 2 minutes. The evidence is
twofold and was checked independently:

* the task's own definition under ``C:\\Windows\\System32\\Tasks\\`` --
  ``Enabled=true``, ``Interval=PT2M``, principal ``S-1-5-18``,
  ``RunLevel=HighestAvailable``, ``-NonInteractive -File`` naming the
  continuation watchdog script; and
* ``.workbuddy/scheduled/logs/continuation-watchdog.jsonl``, which gains
  exactly one line every 120 s.

So the caveat was itself the defect: a document that says "we could not verify
X" is honest, but a document that says "X is false" because a tool was silent
is not.

WHY THE FIRST VERSION OF THIS LOCK WAS INSUFFICIENT
---------------------------------------------------
It listed the sites by hand and missed one -- ``artifacts/acceptance/
FINAL_ACCEPTANCE_REPORT.md`` -- so the false claim survived in a tracked file
while the lock reported success. The lock passed because it looked where it had
been told to look, which is the same failure it was written to catch. The core
sites are still named (they must exist and must state the truth), and on top of
them **every markdown document under ``artifacts/`` is swept**, so a new
generated report cannot reintroduce the claim outside the list.

WHAT IT ASSERTS, AND WHY EACH PART IS LOAD-BEARING

1. The core sites EXIST. Without this, deleting a file would make the scan pass
   by scanning nothing -- the "zero tests also pass" failure this project has
   already hit.
2. The false wording is GONE from every core site.
3. The corrected wording is PRESENT in every core site. Otherwise a file could
   pass by having the false sentence deleted, which is not the same as having
   been repaired.
4. NO document under ``artifacts/`` denies the registration, and the sweep
   found a plausible number of documents (so it cannot pass vacuously).
5. ``_FALSE_WORDING`` really does match the old text (positive control). A lock
   whose pattern can never match anything is not a lock.
"""
from __future__ import annotations

import glob
import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The generators -- checked so that regenerating a document cannot
#: reintroduce the false claim.
GENERATORS = [
    "tools/continuation/soak_driver.py",
    "tools/continuation/write_watchdog_report.py",
    "tools/continuation/write_final_acceptance_report.py",
    "tools/continuation/publish_release_evidence.py",
]

#: Every generated report that discusses the task. The third entry is the one
#: the first version of this lock missed.
REPORTS = [
    "artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md",
    "artifacts/release/JCC_SOAK_REPORT.md",
    "artifacts/release/WATCHDOG_RECOVERY_REPORT.md",
]

CORE = GENERATORS + REPORTS

#: The sentence that was shipped. It states a fact about the HOST that the
#: tool had no way to establish.
_FALSE_WORDING = re.compile(r"is\s+\**not\s+registered\**", re.I)

#: The reply that must be there instead.
_TRUE_WORDING = re.compile(r"\**is\s+registered\**", re.I)

_OLD_SAMPLE = (
    "* The scheduled task `JEV-JCC-Continuation-Watchdog` is not registered on this host; "
    "registering it needs elevation, which was not requested."
)

#: How many documents the sweep must find before it is allowed to be believed.
_MIN_SWEPT = 10

#: Smallest a swept document may be and still be a document. Far below any real
#: report, so this cannot fire on a legitimately terse one.
_MIN_DOC_BYTES = 32

#: Repo-relative documents whose bytes could not be read as text. Populated by
#: :func:`_read`; the sweep asserts it is empty.
UNREADABLE_DOCS = []


def _read(rel: str) -> str:
    """Read a swept document, refusing to return "" for a destroyed one.

    WHY THIS IS NOT A PLAIN ``fh.read()``
    ------------------------------------
    Every assertion downstream of this helper is a NEGATIVE one: "no document
    under artifacts/ denies the registration". A negative assertion is only as
    good as the read behind it. INC-002 replaced a tracked file's bytes with
    NULs and left its size -- and therefore git's stat cache -- intact. A
    zeroed document contains no forbidden wording, so it is never an offender,
    so the sweep passes. That is not a gap in the wording pattern; it is the
    detector reading "the evidence disappeared" as "the evidence is clean".

    Measured on a size-preserving-zeroed fixture (tools/detector_blindspot_probe.py):
    with one swept document zeroed, this sweep reported no offenders and passed.

    So a document that cannot be read as a document is recorded as UNREADABLE
    and the sweep refuses. Note WHERE the refusal happens: here, at read time,
    BEFORE the offender list is built -- not as a filter applied afterwards. An
    exemption applied after the fact could silence a destroyed file, which is
    the same hole wearing a different hat.
    """
    path = os.path.join(ROOT, *rel.split("/"))
    try:
        with io.open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        UNREADABLE_DOCS.append("%s: cannot read: %s" % (rel, exc))
        return ""
    if len(raw) < _MIN_DOC_BYTES:
        UNREADABLE_DOCS.append(
            "%s: %d bytes on disk, below the %d-byte floor -- content has been "
            "removed (zeroed or truncated), so its wording cannot be judged"
            % (rel, len(raw), _MIN_DOC_BYTES))
        return ""
    if b"\x00" in raw:
        UNREADABLE_DOCS.append(
            "%s: contains NUL bytes -- INC-002 signature (content replaced, "
            "size preserved)" % rel)
        return ""
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        UNREADABLE_DOCS.append("%s: no non-whitespace content" % rel)
        return ""
    return text


def _swept_documents():
    """Every markdown document under artifacts/, repo-relative."""
    pattern = os.path.join(ROOT, "artifacts", "**", "*.md")
    found = glob.glob(pattern, recursive=True)
    return sorted(os.path.relpath(p, ROOT).replace(os.sep, "/") for p in found)


def test_every_core_site_is_on_disk():
    """Guard against a scan that passes by finding no files."""
    missing = [t for t in CORE if not os.path.isfile(os.path.join(ROOT, *t.split("/")))]
    assert not missing, "core sites missing from the tree: %s" % missing
    assert len(CORE) == 7, len(CORE)


@pytest.mark.parametrize("rel", CORE)
def test_no_core_site_denies_the_registered_task(rel):
    text = _read(rel)
    m = _FALSE_WORDING.search(text)
    assert m is None, (
        "%s claims the continuation watchdog's scheduled task is not registered on this "
        "host. That is false: the task is registered and fires every 2 minutes. The "
        "sandbox cannot query Task Scheduler through CIM, and 'could not query' must not "
        "be published as 'not registered'. Offending text: %r"
        % (rel, m.group(0) if m else "")
    )


@pytest.mark.parametrize("rel", CORE)
def test_no_core_site_silently_dropped_the_caveat(rel):
    """Deleting the sentence is not repairing it: the truth must be stated."""
    text = _read(rel)
    assert _TRUE_WORDING.search(text), (
        "%s no longer says anything about whether the scheduled task is registered. The "
        "caveat must be CORRECTED (the task is registered; the sandbox cannot query the "
        "scheduler API), not deleted -- a deleted caveat reads as 'no residual risk here'."
        % rel
    )


def test_no_document_under_artifacts_denies_the_registration():
    """The hole the first version of this lock had: a site outside the list.

    The core list above is hand-maintained, so a NEW generated report would not
    be covered by it. This sweeps the whole ``artifacts/`` markdown tree instead.
    """
    docs = _swept_documents()
    assert len(docs) >= _MIN_SWEPT, (
        "only %d markdown document(s) under artifacts/ -- the sweep is passing "
        "vacuously. Found: %s" % (len(docs), docs)
    )
    # Only the REPORTS can be reached by an artifacts/ markdown sweep; the four
    # generators live under tools/ and are covered by the parametrised cases.
    missing = [d for d in REPORTS if d not in docs]
    assert not missing, "core reports not reached by the sweep: %s" % missing

    offenders = [d for d in docs if _FALSE_WORDING.search(_read(d))]

    # A document that could not be read is NOT a document that passed. This
    # assertion is what stops "the evidence vanished" from being reported as
    # "the evidence is clean". It sits AFTER the read loop (which is what
    # populates UNREADABLE_DOCS) but BEFORE the offender verdict is trusted --
    # _MIN_DOC_BYTES already refuses to add a document with no readable content
    # to `offenders`, so an unreadable document can never be quietly exonerated
    # by being skipped.
    assert not UNREADABLE_DOCS, (
        "swept documents could not be read as text, so their wording was never "
        "actually checked: %s" % UNREADABLE_DOCS
    )
    assert not offenders, "artifacts documents deny the registration: %s" % offenders


def test_the_false_wording_pattern_actually_matches_the_shipped_sentence():
    """Positive control: prove this lock can fail.

    If ``_FALSE_WORDING`` could not match the text that actually shipped, every
    case above would pass unconditionally.
    """
    assert _FALSE_WORDING.search(_OLD_SAMPLE), (
        "the guard pattern does not match the sentence this defect shipped with, so the "
        "guard is vacuous"
    )
    corrected = _OLD_SAMPLE.replace("is not registered", "**is registered**")
    assert not _FALSE_WORDING.search(corrected), corrected
