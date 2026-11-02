# Re-baseline windows vs stale pins

**Repo root: `D:\jev`.** Companion to `AGENTS.md` §6.

This exists because a release gate went red for a reason nobody had classified,
and the two possible classifications lead to opposite actions. Getting it wrong
in either direction is expensive: re-baselining a genuine drift deletes the
evidence that something changed unexamined; treating a legitimate window as drift
sends people hunting a bug that does not exist.

---

## 1. The distinction

A **content pin** (an A8 `_BASELINE_HASHES` entry, a manifest hash) records a
fact: *this exact content was reviewed and accepted.*

A **re-baseline window** is the legitimate interval during which a reviewed,
intentional change happens and the pin is updated **in the same commit**.

These are different events, and only one of them is a defect:

| | Legitimate re-baseline window | Stale pin (defect) |
|---|---|---|
| The content change | reviewed, intentional | unreviewed, incidental |
| The pin update | **same commit** as the content change | absent, or in a later commit |
| The pin value | taken from the **committed blob**, never the worktree | still describes older content |
| The test | green | red |
| What it signals | the process worked | the process did not run |

A pin that is red is **always** actionable. The question is only *which* action:
record the new reviewed value, or investigate what changed.

---

## 2. The case that produced this document

CR-002 (`ac1ef4a`) changed `packages/jcc_core/config.py` and `run.py`. That
made `test_p3_acceptance_gate.py::test_A8_negative_control_enabled_without_allowlist`
fail — it pins the sha256 of both files.

The pin value `0f9acd37…` was not sabotage and not a careless edit. Reading the
pin's own comment showed the actual sequence:

```
# RE-BASELINED v1.3 (2026-10-04, repo-relocation): the absolute F:/jev literals
# for DEFAULT_CONFIG_PATH / state_store_db_path are replaced by REPO_ROOT derived
# from __file__ ...
```

So: a **legitimate relocation re-baseline had already run on 2026-10-04**, and
it moved `config.py` away from the F: drive. CR-002 then changed `config.py`
**again**, on top of that. Each event was individually correct. The pin went red
only because the second change had no re-baseline of its own.

**Classification: the re-baseline process lacks a trigger.** Not a broken pin, a
missing step. That is a more valuable finding than "a pin is stale", because a
stale pin is a one-line fix while a missing trigger recurs on every change.

Corollary: the same commit had to move the `run.py` pin a second time when the
port-normalisation fix touched `run.py` again. A pin that must be re-baselined
by every unrelated change to the file is a **maintenance tax that will be paid
wrong under deadline pressure** — the exact conditions under which it gets
skipped rather than done properly.

---

## 3. There was a second, independent lag nobody was watching

`artifacts/release/manifest.json` also records content hashes. It is **not** a
second copy of the A8 pin, and this is the part that is easy to get wrong.

Gate leg **P-02** in `artifacts/acceptance/final_gate_run.py` (~lines 1078–1137)
reads *both* and requires **three-way agreement**:

```
manifest entry  ==  A8 _BASELINE_HASHES entry  ==  sha256(git cat-file blob HEAD:<path>)
```

So the manifest is a **third witness**, cross-checked against the other two — not
an independent authority. When they disagree, P-02 fails.

Measured on this tree:

```
manifest.frozen_at = c2c8367   ("re-point RELEASE_FREEZE to the v1.1 release freeze 4925bb4")
commits since frozen_at = 84
entries = 12, of which 6 no longer match HEAD
```

The six: `config/jcc.yaml`, `jcc_core/{config,core,run,server,state_store}.py`.
Every stale manifest value **equals the blob at `c2c8367`** — so the manifest is
an internally consistent frozen v1.1-era record describing an 84-commit-old tree.
It is not corrupt and not hand-edited. It simply stopped being regenerated, and
**no one was watching it**, because nothing re-baselines a manifest the way a
test re-baselines a pin.

Replaying P-02 offline (read-only, without running the whole gate):
**P-02 FAIL, 12 findings** — the same 6 files, counted once by each of the two
disagreeing checks.

**The lesson generalises past this file:** a record that is only refreshed when
somebody remembers is not a control. It is a claim that happens to be true until
the next change. Anything load-bearing needs either a regenerating step or a
checker, and the two are not interchangeable.

---

## 4. Rules

1. **A red pin is actionable immediately.** Classify before fixing: intentional
   change, or unreviewed drift? Record which, in the same sentence.
2. **Re-baseline in the same commit as the content change.** A pin updated later
   is indistinguishable, to the next reader, from a pin nobody checked.
3. **Take the new hash from the committed blob, never the worktree.**
   `git rev-parse <commit>:<path>` / `git cat-file blob`. A worktree value taken
   before the commit is a candidate, not a fact — and if the commit changes the
   file again, it silently becomes wrong.
4. **Carry `old -> new` plus the reason** in the pin's comment. The comment is
   what let this be diagnosed in one step instead of an afternoon.
5. **Do not hand-edit a generated manifest.** Re-emit it with its own declared
   generator, so `frozen_at` and the hashes move together. Hand-editing produces
   a *new* inconsistency: a `frozen_at` commit whose blobs no longer match the
   recorded hashes.
6. **Check every record source, not the one that failed.** The A8 pin going red
   is what surfaced the manifest lag. Neither would have found the other.
7. **Know your generator's family.** `scripts/emit_release_evidence.py` and the
   `tools/emit_*_manifest.py` group produce *different* artifacts
   (`manifest.json` vs `final-manifest.json` / `freeze_manifest.json` / the v1.x
   manifests). Substituting one for the other produces a plausible, wrong file.
8. **A "re-baseline" that changes no hash is not a re-baseline.** If the content
   did not move, the pin must not either.

---

## 5. Verification aids (read-only)

| Question | Command |
|---|---|
| Do all A8 pins match committed HEAD blobs? | `D:\jev\.venv\Scripts\python.exe runtime/_verify_cr002/p02_replay.py` |
| How stale is the manifest, and since when? | `D:\jev\.venv\Scripts\python.exe runtime/_verify_cr002/manifest_staleness.py` |
| Which sources disagree about which files? | `D:\jev\.venv\Scripts\python.exe runtime/_verify_cr002/manifest_vs_pins.py` |

`p02_replay.py` reproduces P-02's three checks without running the full gate —
useful because the gate is expensive and the question is usually narrow.

**State as of this writing:** A8 pins 12/12 match HEAD. `manifest.json` still
records 6/12 stale entries, still `frozen_at=c2c8367`. **P-02 therefore still
replays as FAIL.** That is an open item owned by the release-artifact owner, not
by CR-002, and it is listed here rather than quietly fixed because re-emitting
the manifest is a release action with its own review step.
