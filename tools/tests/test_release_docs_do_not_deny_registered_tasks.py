"""Release documents must not deny a scheduled task that is registered.

WHY THIS EXISTS
---------------
Four generators and two generated reports asserted, as a *scope limit and
honest caveat*, that the scheduled task ``JEV-JCC-Continuation-Watchdog``
is NOT registered on this host. That claim was produced by a probe that
could not answer at all: inside the agent sandbox ``Get-ScheduledTask``
fails (the Task Scheduler CIM provider is unreachable), and the probe read
"could not query" as "does not exist".

The task is in fact registered and fires every 2 minutes. The evidence is
twofold and was checked independently:

* the task's own definition under ``C:\\Windows\\System32\\Tasks\\`` --
  ``Enabled=true``, ``Interval=PT2M``, principal ``S-1-5-18``,
  ``RunLevel=HighestAvailable``, ``-NonInteractive -File`` naming the
  continuation watchdog script; and
* ``.workbuddy/scheduled/logs/continuation-watchdog.jsonl``, which gains
  exactly one line every 120 s.

So the caveat was itself the defect: a document that says "we could not
verify X" is honest, but a document that says "X is false" because a tool
was silent is not. The claim is corrected in all six places, and this
module is the lock.

WHAT IT ASSERTS, AND WHY EACH PART IS LOAD-BEARING

1. Every target file EXISTS. Without this, deleting a file would make the
   scan pass by scanning nothing -- the "zero tests also pass" failure
   this project has already hit.
2. The false wording is GONE from every target.
3. The corrected wording is PRESENT in every target. Otherwise a file
   could pass by simply having the false sentence deleted, which is not
   the same has having been repaired.
4. ``_FALSE_WORDING`` really does match the old text (positive control).
   A lock whose pattern can never match anything is not a lock.
"""
from __future__ import annotations

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

#: The documents as they stand on disk.
REPORTS = [
    "artifacts/release/JCC_SOAK_REPORT.md",
    "artifacts/release/WATCHDOG_RECOVERY_REPORT.md",
]

TARGETS = GENERATORS + REPORTS

#: The sentence that was shipped. It states a fact about the HOST that the
#: tool had no way to establish.
_FALSE_WORDING = re.compile(r"is\s+\**not\s+registered\**\s+on\s+this\s+host", re.I)

#: The reply that must be there instead.
_TRUE_WORDING = re.compile(r"\**is\s+registered\**", re.I)

_OLD_SAMPLE = (
    "* The scheduled task `JEV-JCC-Continuation-Watchdog` is not registered on this host; "
    "registering it needs elevation, which was not requested."
)


def _read(rel: str) -> str:
    with io.open(os.path.join(ROOT, *rel.split("/")), "r", encoding="utf-8",
                 errors="replace") as fh:
        return fh.read()


def test_every_target_is_on_disk():
    """Guard against a scan that passes by finding no files."""
    missing = [t for t in TARGETS if not os.path.isfile(os.path.join(ROOT, *t.split("/")))]
    assert not missing, "targets missing from the tree: %s" % missing
    assert len(TARGETS) == 6, len(TARGETS)


@pytest.mark.parametrize("rel", TARGETS)
def test_no_target_denies_the_registered_task(rel):
    text = _read(rel)
    m = _FALSE_WORDING.search(text)
    assert m is None, (
        "%s claims the continuation watchdog's scheduled task is not registered on this "
        "host. That is false: the task is registered and fires every 2 minutes. The "
        "sandbox cannot query Task Scheduler through CIM, and 'could not query' must not "
        "be published as 'not registered'. Offending text: %r"
        % (rel, m.group(0) if m else "")
    )


@pytest.mark.parametrize("rel", TARGETS)
def test_no_target_silently_dropped_the_caveat(rel):
    """Deleting the sentence is not repairing it: the truth must be stated."""
    text = _read(rel)
    assert _TRUE_WORDING.search(text), (
        "%s no longer says anything about whether the scheduled task is registered. The "
        "caveat must be CORRECTED (the task is registered; the sandbox cannot query the "
        "scheduler API), not deleted -- a deleted caveat reads as 'no residual risk here'."
        % rel
    )


def test_the_false_wording_pattern_actually_matches_the_shipped_sentence():
    """Positive control: prove this lock can fail.

    If ``_FALSE_WORDING`` could not match the text that actually shipped,
    every parametrised case above would pass unconditionally.
    """
    assert _FALSE_WORDING.search(_OLD_SAMPLE), (
        "the guard pattern does not match the sentence this defect shipped with, so the "
        "guard is vacuous"
    )
    # And it must not fire on the corrected wording.
    corrected = _OLD_SAMPLE.replace("is not registered", "**is registered**")
    assert not _FALSE_WORDING.search(corrected), corrected
