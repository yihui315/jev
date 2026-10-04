"""Per-process pytest basetemp isolation (CR-H1).

THE DEFECT
----------
``packages/conftest.py`` and ``tools/conftest.py`` both pinned pytest's basetemp
to a single FIXED path::

    root = Path(tempfile.gettempdir()) / "jev-pytest"
    root.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(root)

pytest ``rm_rf``s a user-supplied basetemp ON ENTRY. So two concurrent runs
that resolve to the same fixed path delete each other's scratch out from under
the tests that are using it.

MEASURED ON THIS HOST, BEFORE THE FIX (5 concurrent pairs, no ``--basetemp``)::

    trial 1: A rc=0 [16 passed]  | B rc=0 [11 passed]
    trial 2: A rc=1 [16 errors] | B rc=0 [11 passed]   <-- CONTAMINATED
    trial 3: A rc=0 [16 passed]  | B rc=0 [11 passed]
    trial 4: A rc=1 [16 errors] | B rc=0 [11 passed]   <-- CONTAMINATED
    trial 5: A rc=0 [16 passed]  | B rc=1 [11 errors]  <-- CONTAMINATED

3 of 5 pairs had a non-zero leg, and the losing side reported errors during
SETUP -- the test is never entered, so "error" here means "the environment
deleted my scratch", not "the product is broken". This is the shared root cause
behind the round's repeated concurrent-leg contamination.

Note the FAILURE IS SILENT AND ORDER-DEPENDENT: an early probe of the same two
suites came back clean, and trial 1 here is clean too. A single passing run is
therefore NOT evidence that the collision is gone -- which is why the check in
``tools/crh1_basetemp_falsifier.py`` runs concurrent pairs repeatedly instead
of once.

THE FIX
-------
Name the basetemp per PROCESS. Each pytest process gets a directory only it
knows about, so the ``rm_rf`` on entry can never reach another live run.

Three properties this deliberately keeps:

1. **Derived, never typed.** The root is ``tempfile.gettempdir()``, never a
   literal ``D:\\jev``. This project has been broken by a hardcoded drive letter
   three times already (the F:/jev migration, CR-002's GREEN port, the
   Get-JccPort regression). It is also load-bearing for a second reason: the
   safe-delete shim (``sitecustomize.py``) short-circuits deletes that resolve
   under ``tempfile.gettempdir()`` and routes everything else through its
   bulk-delete guard, so a basetemp anywhere else makes every ``tmp_path``
   fixture error out with ``SAFE_DELETE_BULK_CONFIRM_REQUIRED``.

2. **An explicit ``--basetemp`` still wins.** The acceptance gate always passes
   one, and a caller who names a directory means it.

3. **The pid is not load-bearing on its own.** A pid can be recycled, and a
   recycled pid would inherit a stale directory. The uuid suffix means a stale
   directory is never reused, and :func:`pytest_unconfigure` removes THIS
   process's own directory on the way out.

NOT REUSED: the old fixed name. Anything that genuinely needs to share scratch
across processes must say so explicitly -- and nothing in the product does.
``tools/tests/test_doctor_next_hint.py`` writes to ``$TEMP/jev-pytest/nhh``,
but it derives that from ``os.environ["TEMP"]`` and creates the directory
itself, so it is independent of this basetemp.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

#: The stable PARENT of every per-process basetemp. Kept as one name so the two
#: conftests in this tree cannot drift into computing different roots -- they
#: previously carried duplicate copies of this logic, which is how a fix would
#: have had to be applied twice and could be applied once.
BASETEMP_PARENT_NAME = "jev-pytest"


def basetemp_root() -> Path:
    """``<gettempdir()>/jev-pytest`` -- the parent every process subdir lives in.

    Exposed so a test can assert the parent is shared while the LEAF is not.
    """
    return Path(tempfile.gettempdir()) / BASETEMP_PARENT_NAME


def per_process_basetemp() -> Path:
    """A basetemp directory unique to this process, created if absent.

    The name is ``jev-pytest<pid>-<uuid8>``. Both halves matter: the pid makes
    the directory attributable while a run is live (so a human reading a crash
    can tell which run owned it), and the uuid means a recycled pid can never
    inherit a previous run's leftovers.
    """
    root = basetemp_root() / ("%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))
    root.mkdir(parents=True, exist_ok=True)
    return root


def configure_basetemp(config) -> Optional[str]:
    """Apply the per-process basetemp unless the caller named one.

    Returns the directory that was configured, or ``None`` when an explicit
    ``--basetemp`` was honoured. ``None`` is a real outcome and not an error:
    the acceptance gate always passes ``--basetemp`` and must keep winning.
    """
    if config.option.basetemp:  # an explicit --basetemp wins
        return None
    # MUST live under tempfile.gettempdir(): that is the only path prefix the
    # safe-delete shim exempts from the bulk-delete guard. See module docstring.
    root = per_process_basetemp()
    config.option.basetemp = str(root)
    # Clean up after THIS process, and only this process. Registered rather than
    # done in pytest_unconfigure so it also runs when the session is interrupted
    # (a bare ``pytest.UsageError`` at collection never reaches unconfigure) --
    # which is precisely how scratch accumulates on a host where runs abort.
    atexit.register(_remove_tree_quietly, root)
    return str(root)


def _remove_tree_quietly(path: Path) -> None:
    """Remove ``path`` if it is one of ours, and never raise.

    Two guards, both deliberate. It only ever deletes a directory whose name
    carries this process's pid, so a bug in the caller cannot turn a cleanup
    into a delete of somebody else's scratch. And it stays under the shared
    parent, so even a wrong path cannot reach outside the basetemp tree.
    """
    try:
        parent = path.parent.resolve()
        if parent != basetemp_root().resolve():
            return
        if ("%d-" % os.getpid()) not in path.name:
            return
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001 -- cleanup must never mask a test result
        pass
