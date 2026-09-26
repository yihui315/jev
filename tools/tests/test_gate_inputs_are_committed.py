#!/usr/bin/env python3
"""Lock every input the FINAL pipeline needs to HEAD, not to the index.

THE DEFECT THIS EXISTS TO PREVENT. The FINAL release pipeline is driven by a
``STEPS`` table in ``tools/jcc_continuation.py``; each step names a ``script``.
Three of those runner scripts were not in version control, and one of them
(``artifacts/acceptance/project_run_gate.py``) was not merely untracked but
INVISIBLE -- ``.gitignore`` carries ``artifacts/acceptance/**`` with no negation,
so it never even appeared as ``??``. This class has bitten twice already
(``artifacts/acceptance/final_gate_run.py`` was force-added for exactly this
reason and ``project_run_gate.py`` was missed). ``docs/FINAL_STOP_CONDITIONS.md``
states the rule verbatim: "An untracked gate is an unreproducible verdict."

FINAL-12 additionally HARD-REQUIRES five control-plane proof files that are
produced outside the pipeline and read by it; those paths are declared in
``tools/continuation/proof_contract.py`` (module constant ``PROOFS``), which is
itself a hard dependency because FINAL-12 imports it. So this test examines
EVERY path the pipeline needs: the 11 step scripts, the 5 proof artifacts, and
the proof contract module -- 17 paths in total.

HEAD AND NOT THE INDEX -- AND WHY IT MATTERS. This test asks git whether each
path is present in the HEAD commit, NOT whether it is tracked by ``git
ls-files``. ``git ls-files`` reads the INDEX, so a file that is merely
``git add``-ed would pass here while a fresh clone still would not have it --
that false green is the precise failure this project keeps paying for. Only the
committed tree is what a clone receives, so only HEAD answers the real question.
We use ``git cat-file -e HEAD:<path>`` and assert on its EXIT CODE (never on
stderr prose).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

CONTROLLER = REPO / "tools" / "jcc_continuation.py"
CONTRACT = REPO / "tools" / "continuation" / "proof_contract.py"

#: The contract itself, as a repo-relative POSIX path.
CONTRACT_REL = "tools/continuation/proof_contract.py"

#: The exact number of paths this lock examines at hand-off:
#:   11 step scripts + 5 proof artifacts + the proof contract itself = 17.
#: Written down so a future reader SEES the set shrink; if it does, the
#: arithmetic above must be re-stated, not silently adjusted.
EXPECTED_INPUTS = 17


def _load_by_path(module_name: str, path: Path):
    """Import a module by path, exactly as project_run_gate._load_controller does.

    Importing these modules runs no pipeline: everything that starts a step lives
    behind ``main()``.
    """
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _is_committed_at_head(rel_posix: str) -> bool:
    """True iff ``rel_posix`` exists as a committed blob in HEAD.

    ``git cat-file -e HEAD:<path>`` exits 0 iff the blob is committed at HEAD; we
    assert on the exit code only. ``HEAD:<path>`` reads the commit tree, so a
    path that is merely staged in the index does NOT pass.
    """
    proc = subprocess.run(
        ["git", "cat-file", "-e", "HEAD:%s" % rel_posix],
        cwd=str(REPO), capture_output=True,
    )
    return proc.returncode == 0


def _pipeline_inputs():
    """Every path the FINAL pipeline needs, DERIVED from its own declarations.

    Returns a list of ``(source, path)`` pairs. The step scripts come from the
    controller's ``STEPS`` table, the proof artifacts from the contract's
    ``PROOFS`` map. Retyping them would be the defect: a new step or a new proof
    claim must make this test notice WITHOUT anyone editing it.

    A step whose ``script`` is falsy is skipped, but the number of scripts
    actually examined is counted so an empty walk can be refused below.
    """
    controller = _load_by_path("jcc_continuation_for_gate_inputs", CONTROLLER)
    contract = _load_by_path("jcc_proof_contract_for_gate_inputs", CONTRACT)

    inputs = []
    steps_examined = 0
    for step in controller.STEPS:
        script = getattr(step, "script", None)
        if not script:
            continue
        steps_examined += 1
        inputs.append(("STEPS[%s].script" % step.id, str(script).replace("\\", "/")))

    proofs_examined = 0
    for name in contract.PROOFS:
        proofs_examined += 1
        inputs.append(("PROOFS[%s]" % name, contract.proof_artifact(name)))

    inputs.append(("the proof contract module", CONTRACT_REL))

    assert steps_examined, (
        "the controller's STEPS table yielded ZERO scripts -- this test examined "
        "nothing, so it can prove nothing. 'Passes because it looked at nothing' "
        "is the defect class this suite exists to catch.")
    assert proofs_examined, (
        "the proof contract's PROOFS map yielded ZERO paths -- this test examined "
        "nothing, so it can prove nothing.")
    return inputs


def test_every_input_the_pipeline_needs_is_committed_at_head():
    """Lock the pipeline's READ set to HEAD -- an untracked gate is unreproducible.

    Examines HEAD and NOT the index: ``git ls-files`` (the index) would pass a
    merely ``git add``-ed path, but a clone never receives the index, so only the
    committed tree answers "would a clone have this?". See the module docstring.

    Collects ALL offending paths before failing, so one run names the whole gap.
    """
    inputs = _pipeline_inputs()

    assert len(inputs) == EXPECTED_INPUTS, (
        "this lock is written to examine exactly %d paths (11 step scripts + 5 "
        "proof artifacts + the proof contract); it examined %d. If the pipeline "
        "genuinely gained or lost an input, restate the arithmetic here -- a "
        "silently shrinking set is the whole defect this lock exists to catch."
        % (EXPECTED_INPUTS, len(inputs)))

    missing = [(source, path) for source, path in inputs
               if not _is_committed_at_head(path)]

    assert not missing, (
        "%d of %d pipeline input(s) are NOT committed at HEAD. An untracked gate "
        "is an unreproducible verdict -- a clone would not have them. (Checked "
        "HEAD, not the index: staged is not tracked.) Offenders:\n  %s"
        % (len(missing), len(inputs),
           "\n  ".join("%s -> %s" % (source, path) for source, path in missing)))
