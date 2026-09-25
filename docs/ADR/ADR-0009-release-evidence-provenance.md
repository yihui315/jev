# ADR-0009 — Release Evidence Provenance (a version-controlled, recomputable evidence manifest)

- **Status:** **DECIDED** — lead-authorised, P4 item 9 / task #78. Two implementing items are
  outstanding and are listed in *Status of the implementation* below; neither changes any decision
  in this ADR.
- **Date:** 2026-09-25
- **Owner:** Atlas-2 (jcc-protocol-architect)
- **Supersedes:** none
- **Superseded by:** none
- **Extends (does not re-open):** ADR-0006 (version control & rollback), ADR-0008 (D-1…D-41,
  **unchanged** — this ADR adds no decision to that range).
- **Triggered by:** `docs/p3/P3_RELEASE_GATE.md` §1.2 ("The evidence is NOT in version control") and
  §6 item 9; the §1.4 provenance incident (a verdict backed only by a gitignored log).
- **Implementing artefact:** `scripts/emit_release_evidence.py` (**owner: harbor** — this ADR
  records the contract; it does not own the code).
- **Change control:** decisions in this ADR are **EV-1…EV-12** and may only be altered by a
  superseding ADR or an explicit lead ruling. Altering any of them silently would re-create the exact
  defect this document exists to close.

---

## Context

P3's acceptance gate produces its machine evidence under `artifacts/`, and **`artifacts/` is
gitignored** (historically `.gitignore:8`). The consequence recorded in `P3_RELEASE_GATE.md` §1.2 is
that a gate's verdict can be lost without any commit changing:

- the canonical `p3_gate_result.json` is a single **mutable** path (§1.4: it was overwritten by a
  concurrent dirty run at `1082fff`);
- harbor's own `P3_SRE_GATE_HARBOR.md`, the immutable `verdict_3936c96.json`, and the whole
  `p3_gate_result.*` family were all outside version control;
- the only tracked file under `artifacts/` was the runner itself, force-added.

The interim mitigation chosen at the time was to hand-transcribe **sha256 anchors** into the tracked
record (§1.2, 10 rows). That mitigation held the verdict together for P3 and is preserved verbatim —
but it was always a mitigation, for three concrete reasons developed below.

This ADR decides how the release evidence is made revision-controlled, what "release evidence" means,
and how the old anchors and the new manifest relate.

---

## Decision

### EV-1 — One narrow hole in `artifacts/`: `artifacts/release/` is version-controlled

Everything under `artifacts/` stays out of version control **except** `artifacts/release/`. The hole
is one directory, added at the end of P3's evidence mechanism, and it is the only place where
evidence is *expected* to be committed.

### EV-2 — The manifest is the unit of release evidence (fixed schema, `schema_version` `1.0`)

A release is evidenced by `artifacts/release/manifest.json` and nothing else. The schema is fixed:

```json
{
  "schema_version": "1.0",
  "generated_at": "<ISO8601 with timezone>",
  "generator": "scripts/emit_release_evidence.py",
  "frozen_at": "<git sha>",
  "entries": [
    {
      "path": "<repo-relative posix path>",
      "sha256": "<full lowercase hex sha256>",
      "bytes": "<exact byte count>",
      "generated_by": "<free-form provenance claim>"
    }
  ]
}
```

`schema_version` is **mandatory** and `--check` fails on a mismatch. A future change to the shape
requires a new `schema_version`; nothing is added to `1.0` in place.

### EV-3 — The evidence set is an explicit allow-list, never a recursive sweep

The default evidence set is a fixed list of paths. A recursive sweep of `artifacts/` is **prohibited**
as a default because it would sweep pytest basetemp trees, multi-megabyte console logs and
third-party scratch directories into a "release manifest", which would make the manifest's meaning
undefined. Recursive folding (`--scan`) exists for the cases where someone deliberately wants it and
must still pass explicit `--exclude` globs.

### EV-4 — Byte-level hashing; no EOL normalisation

Digests are computed over the raw bytes on disk (`open(..., 'rb')` + chunked `hashlib`). There is no
end-of-line normalisation, no encoding guess, no trailing-newline trim and no Windows special case.
This is deliberate and it **contrasts with the A8 freeze pin**, which normalises CRLF→LF so the pin
survives a checkout under `core.autocrlf=true`. The two conventions are chosen for different enemies:
A8 must be *stable across checkouts*, the evidence manifest must be *truthful about the bytes a
reviewer is certifying*. A CRLF/LF difference must surface as a digest difference, never be smoothed
away.

### EV-5 — Evidence must be recomputable, not pasteable

The manifest is a **product** of a run, not a transcription. Any anchor that a human typed into a
document is an **interim** artefact: it can only be checked by another human reading two columns and
comparing them. The generator therefore has a `--check` mode that recomputes every digest and exits
non-zero on any divergence, and the P3 §1.2 anchors are embedded in the generator so that the old,
hand-transcribed table can be *verified by machine* rather than trusted.

### EV-6 — No secrets, no logs, no transcripts, no scratch

The manifest records **paths, digests and sizes only**. It MUST NOT contain, and MUST NOT be used to
reconstruct: keys or tokens, `.log` captures, agent transcripts, prompt/response payloads, host
containment dumps, or any other third-party scratch. The generator's own default exclude list
(`__pycache__`, `*.pyc`, `.pytest_cache`, `*.tmp`, `*.log`, `*.backup-*`) is the minimum, not a
substitute for EV-3. `generated_by` is a **claim** (the script cannot detect who produced a file) and
is therefore advisory: `--check` validates digests and sizes, never that field.

### EV-7 — Nothing is reclassified; drift is an exit code, not a comment

`--check` reports every divergence as a named row (`SCHEMA`, `FROZEN`, `MISSING`, `UNRECORDED`,
`DIGEST`, `SIZE`, `STALE`, `ANCHOR-MISSING`, `ANCHOR-SIZE`, `ANCHOR-DIGEST`) and exits **1**. No
divergence is ever folded into an "OK" line, and no mismatch is printed in an OK shape. This is the
same rule the P3 gate applied to its own falsifiers: a deliberately-red row inside a passing suite is
an absorbed failure with a label, which is worse than a red row.

### EV-8 — Ownership and the re-baseline path

The generator is owned by **harbor** (`scripts/` is not mine to write). Re-baselining the evidence
set follows the A8 pattern rather than a workaround: `--anchor-file` accepts a
`path<TAB>sha256[<TAB>bytes]` table that overrides the embedded anchors, and a legitimate change to a
§1.2-anchored file means **updating the record and re-running `--check`**, not relaxing the check.

### EV-9 — The `.gitignore` form is load-bearing and must not be "simplified"

See *The version-control rule below*. The naive form `artifacts/` + `!artifacts/release/` **does not
work** and must not be reintroduced in its place.

### EV-10 — The evidence set is closed under version control

Every `path` in a committed manifest MUST resolve to a file that a third party actually receives, i.e.
a **tracked** file. A row that points at a git-ignored path is a digest of something the reader cannot
obtain: `--check` verifies it only for whoever still happens to hold the working tree, which is the
condition EV-5 ("recomputable, not pasteable") exists to prevent.

Measured at `frozen_at: "3936c96"`: **7 of the 10** entries point into `artifacts/`
(`artifacts/acceptance/**`), and `git ls-files artifacts/` lists exactly **one** path —
`artifacts/acceptance/p3_gate_run.py`, force-added before EV-1 existed. So after a clone a reader
receives the 3 `packages/**` rows and **nothing else**; the other 7 are digests of files that never
reached them. Recorded here because the naive reading ("most of the manifest is fine") is wrong in
both directions: 7 rows are unreachable, and 1 of the reachable 3 has a further defect (EV-11).

Remediation is **promotion, not an allowance** (see EV-12). The one shape that is *no longer
permitted* is a per-file allow-list inside `artifacts/acceptance/`: see EV-12 for the reasoning.

The manifest is not the place where run artefacts are archived; run artefacts stay out (EV-6).

### EV-11 — An entry must be reproducible, not merely present

EV-10 says the file must be reachable; EV-11 says it must be **obtainable by rerunning the command**.
A digest of a file containing a wall-clock timestamp, a machine-local absolute path, a PID or a random
basetemp directory cannot pass `--check` twice in a row — `--check` compares *the bytes currently on
disk* to *the bytes recorded at freeze time*, which is not the same question as "would regenerating
this give the same bytes". Such a row yields **zero reproducibility with a harmful zero**: it is not
merely absent evidence, it makes the manifest look closed when it is not.

Therefore:

- **An entry MUST be byte-reproducible on the same commit.** Any output that embeds wall-clock time,
  host paths, PIDs or randomised temporary directories MUST NOT be an manifest entry.
- **Test before entry:** run the producing command **twice** on the same commit and compare `sha256`.
  Equal → it may be accounted. Different → it may not, without exception.
- **Stripping, not hiding.** Volatile fields are stripped into a separate sidecar file **that is not
  accounted**; the stable remainder is what enters the manifest. Stripping the *fields* keeps the
  record honest; stripping the *row* would discard the stable part for the sake of the unstable one.
- `--check` recomputes digests and sizes. It is not, and must not be extended into, a re-execution
  harness; the two-run measurement is the gate that decides whether something may be entered, and it
  is a human/owner act, not a `--check` act.

### EV-12 — Evidence is promoted into the release directory; `artifacts/acceptance/` stays ignored

Anything that is to be retained as evidence is **promoted** into `artifacts/release/` (generated
scripts go to `scripts/`). `artifacts/acceptance/` remains ignored as a whole, permanently.

**Why not an explicit per-file allow-list inside `artifacts/acceptance/`** (the alternative shape
recorded in EV-10 before this ADR was amended):

1. **It re-imports the noise it was meant to escape.** `artifacts/acceptance/` is where every gate run
   drops its captures. Allow-listing one file today means the next run adds another; within a week the
   archive re-acquires the churn that EV-3's non-recursive rule exists to keep out of history.
2. **It trades a one-shot problem for a recurring one.** The defect EV-10 describes happens once per
   record. An allow-list makes it happen on every run that produces a record — and, worse, it happens
   silently, because the ignore rule still looks intact from the outside.
3. **It dirties the working tree on every run.** Promote-on-demand is explicit and reviewable: an
   entry appears because someone decided to promote it. An allow-list is passive: the directory fills
   up and `git status` stops being a signal about *changes* rather than *archives*.
4. **It weakens the fail-closed property.** `artifacts/*` + `!artifacts/release/**` fails closed by
   construction. Each added negation is one more way for an unintended artefact to become tracked, and
   every such line has to be re-reviewed against EV-6 (no keys, no logs, no transcripts, no scratch).

Promotion also composes with EV-11: a run JSON that carries `ran_at` and `basetemp` stays out of the
manifest, the stable digest of the code it exercised goes in.

---

## Why — the three questions this ADR had to answer

### Why not just commit all of `artifacts/`?

1. **It would make the manifest meaningless.** EV-3's point is the whole argument: a manifest you
   cannot say is *complete* is not evidence. "Everything we could find" is not a set definition.
2. **Noise and size.** `artifacts/` holds pytest basetemp trees, multi-megabyte console captures and
   `_aegis2_scratch/` (aegis-2's third-party probe corpus). Committed, they would dominate history
   for a repo whose value is its reasoned decisions.
3. **Volatility.** Per-run capture files change on every run. Committed wholesale they would bury the
   diffs that actually mean something (which product bytes moved) under churn.
4. **Security.** The same tree carries redteam captures and host-containment dumps. `.gitignore`
   already excludes `*.key`, `*.pem`, `*.pfx`, `credentials*`, `auth.json`, `.env.*` for exactly this
   reason; a wholesale commit sweep would defeat those rules by accident, and "someone remembered
   later" is not a control.
5. **The exclusion is free.** Keeping `artifacts/` ignored costs nothing; the narrow hole costs one
   directory and one manifest.

### Why the interim sha256 anchor is only a mitigation

1. **It is a transcription, so it can be mis-transcribed — and one already was.** `9972288` corrected
   a mis-attributed hash in this very table. The failure mode is not hypothetical; it is the most
   recent thing that happened to this document.
2. **It is checked by reading, not by recomputing.** A pasted anchor does not prove the file still has
   that content; it proves the author once saw that content. Nothing else runs.
3. **It has no exit code.** A drifted anchor produces a green doc. Under EV-5/EV-7 drift produces a
   non-zero exit from `--check`, which means something can be wired to gate on it. That is the
   difference between a record and an instrument.

### Why evidence must be recomputable rather than pasteable

Pasteable evidence proves the *author's* copy; recomputable evidence proves the *claim*. A recomputed
manifest can be produced by a reviewer who was not in the room, on a different machine, in a
different worktree, and compared mechanically. That property — not the hash value — is what makes a
gate's verdict durable after the people who ran it move on. It is also the direct lesson of the §1.4
incident: the record survived; the artefact it pointed at did not.

---

## The version-control rule (and why it is not the obvious one)

Decided `.gitignore` form, at the position where `artifacts/` used to be:

```gitignore
artifacts/*
!artifacts/release/
!artifacts/release/**
```

This is **not** the `artifacts/` + `!artifacts/release/` pair, and the reason is mechanical, not
stylistic. A directory pattern such as `artifacts/` swallows its entire subtree, and git will not
descend into an excluded directory. Consequences, all measured (see *Verification*):

| Form | `manifest.json` trackable? | Other `artifacts/` content still ignored? |
|---|---|---|
| `artifacts/` + `!artifacts/release/` (the naive pair) | **NO** — `git add --dry-run` refuses it | yes |
| `artifacts/` + `!artifacts/` + `!artifacts/release/` + `!artifacts/release/**` | yes | **NO** — `p3_gate_result.json`, `step1_probe.ps1` become trackable |
| `artifacts/*` + `!artifacts/release/**` (no directory line) | **NO** — the directory stays excluded, git never descends | yes |
| `artifacts/*` + `!artifacts/release/` | yes | yes |
| **`artifacts/*` + `!artifacts/release/` + `!artifacts/release/**`** | **yes** | **yes** |
| enumerate the content dirs instead of `artifacts/` | yes | mostly, but a stray root file (`step1_probe.ps1`) leaks and the list must be maintained forever |

The chosen form is also **fail-closed**: any *new* content directory under `artifacts/` is ignored by
default, exactly as before. Re-inclusion is an explicit act.

Note the trap for anyone verifying this: `git check-ignore -v artifacts/release/manifest.json`
against the naive pair still prints a line, and it prints the **exclusion**
(`.gitignore:8:artifacts/`) — so the naive pair looks verified by the standard tool while silently
doing nothing. `git add --dry-run <path>` (or `git status --porcelain -uall`) is the check that
decides.

---

## Relationship to the old (P3) anchors

Two anchor sets exist and they are deliberately **not** in conflict:

| | Old set (P3) | New set (P4 onward) |
|---|---|---|
| Where | `docs/p3/P3_RELEASE_GATE.md` §1.2 | `artifacts/release/manifest.json` |
| Form | hand-transcribed 16-hex prefix + byte count | full 64-hex sha256 + exact byte count |
| Authority | the **historical** P3 certification record | authoritative for releases from P4 on |
| Checked by | a human reading two columns | `scripts/emit_release_evidence.py --check` |
| Recomputable | **no, by construction** | yes — any reviewer, any machine, any worktree |
| Rewritable | **no; §1.2 is never edited to match reality** | replaced by a new `frozen_at` per release |

- The new values are **not a different convention from** the old ones: the old 16-hex prefixes are
  literal prefixes of the new digests. Nothing is padded or invented, because the generator stores
  exactly the width the record published (`HASH_PREFIX_LEN = 16`).
- The new mechanism **consumes the old one as its first input**: the generator embeds the §1.2
  anchors and reconciles the files on disk against them on every `--check` (disable only with
  `--no-anchors`). The old table is therefore *verified*, not discarded — which is the only honest
  treatment, since it is the record of a certification that has already happened.
- When a P4 change legitimately alters one of those files, the A8 rule applies: update the record and
  re-baseline, do not work around the check.

---

## Status of the implementation

The contract above is implemented by `scripts/emit_release_evidence.py` (harbor). Two items are
still outstanding and are **not** merely bookkeeping:

1. **`scripts/` is untracked.** `git ls-files scripts` returns nothing, so the generator that makes
   the evidence recomputable is itself outside version control — i.e. the exact defect this ADR
   closes, one level down. Until the generator is committed, "the evidence is recomputable" is a
   claim about an unversioned script. **Blocking for P4 item 9.**
2. **Generator doc drift.** The generator's header says "`artifacts/` is gitignored (see `.gitignore`,
   the `artifacts/` line)". After EV-1 that is no longer accurate and the file will not be readable
   without a correction. Harbor owns the file; raise with harbor, not by editing `scripts/`.

Also worth recording: `artifacts/acceptance/p3_gate_run.py` remains **force-added** (a tracked file
inside an ignored directory is still tracked; the negation does not widen). That is unchanged and
intentional.

---

## Consequences

**Positive**

- The release evidence of a gate becomes a tracked, diffable, tamper-evident artefact: if a manifest
  entry changes, the diff is the story.
- Drift becomes an exit code instead of a footnote (EV-5, EV-7).
- A reviewer outside the team can reproduce the certification with one command.

**Negative / accepted costs**

- Every regeneration of a manifest creates a commit-sized diff under `artifacts/release/`. Accepted:
  the diff *is* the evidence trail.
- `manifest.previous.json` and `manifest.sha256` are also inside the tracked directory. Accepted — they
  are evidence too, and excluding them would re-create the "previous state is unreachable" problem
  §1.4 recorded.
- Two hashing conventions now coexist in the repo (byte-level here, CRLF-normalised in the A8 pin).
  This is intentional (EV-4) and is documented here so it is not "fixed" by someone who assumes the
  A8 rule is universal.

**Neutral / process**

- `artifacts/` semantics changed for one directory; any tooling that assumed "nothing under
  `artifacts/` is ever committed" must be re-checked (the runner's force-add is unaffected).

---

## Verification

Measured on `F:\jev` at HEAD `9972288`, with the probe file created and then **moved** to
`.workbuddy/scratch/atlas2_release_probe/` (never deleted):

```
$ git add --dry-run artifacts/release/manifest.json
add 'artifacts/release/manifest.json'                      # exit 0  -> TRACKABLE (EV-1)

$ git add --dry-run artifacts/acceptance/_atlas2_probe.log
The following paths are ignored by one of your .gitignore files:   # exit 1 -> still ignored

$ git add --dry-run artifacts/acceptance/p3_gate_result.json       # exit 1 -> still ignored
$ git check-ignore -v --no-index artifacts/release/manifest.json
.gitignore:20:!artifacts/release/**    artifacts/release/manifest.json
```

and under the naive pair, in an isolated probe repo, `git add --dry-run
artifacts/release/manifest.json` returns exit 1 — which is the reason EV-9 exists.

Applied to the **real** manifest already produced by harbor. It was re-emitted once during P4 and the
snapshot recorded here moved with it — the first emission was `frozen_at: "9972288"`, and the one on
disk at the time of this edit is `frozen_at: "3936c96"` with **10 entries** (3 tracked `packages/**`
rows using the `crlf_to_lf` convention, 7 ignored `artifacts/acceptance/**` rows using `raw`). Both
figures are reproducible with `python -c "import json;m=json.load(open('artifacts/release/manifest.json'));print(m['frozen_at'],len(m['entries']))"`;
if they no longer match this paragraph, the generator was re-run again and this paragraph is the thing
that is stale — correct the record, not the manifest.

```
$ git check-ignore -v --no-index artifacts/release/manifest.json
.gitignore:20:!artifacts/release/**    artifacts/release/manifest.json
$ git add --dry-run artifacts/release/manifest.json
add 'artifacts/release/manifest.json'                      # exit 0
$ git add --dry-run artifacts/release/manifest.sha256
add 'artifacts/release/manifest.sha256'                    # exit 0 (sidecar, see Consequences)
$ git ls-files artifacts/
artifacts/acceptance/p3_gate_run.py                        # 1 path only -> EV-10
```

The manifest is now trackable; committing it is the next step and is harbor's to make.

**EV-11 measurement, assigned and not yet returned.** `artifacts/acceptance/p3_gate_result.json`
carries `ran_at` (`2026-09-25T02:52:40.616338+00:00`) and `basetemp`
(`C:\Windows\TEMP\jev-pytest-runs\g20260925_025203_008090`) — a wall-clock stamp and a machine-local
path, so its digest is not reproducible across runs (lead, 2026-09-25). The gate for admitting such a
file is the two-run comparison on one commit, and the numbers are harbor's to produce:
emit the artefact twice at a fixed commit, compare `sha256`, and record both values. Equal → it may be
accounted; different → it may not (EV-11). Until that measurement exists, **no** artefact that embeds
a timestamp or a host path enters the manifest, whatever its location.

Pattern matrix, produced in throwaway probe repos (`.workbuddy/scratch/negmatrix/`), verdict taken
from real traversal (`git add --dry-run`), not from `check-ignore`:

| Form | verdict |
|---|---|
| `artifacts/` + `!artifacts/release/` | ignored (refused) |
| `artifacts/*` + `!artifacts/release/**` | ignored (refused) |
| `artifacts/*` + `!artifacts/release/` | trackable, siblings ignored |
| `artifacts/*` + `!artifacts/release/` + `!artifacts/release/**` | **trackable, siblings ignored** ← chosen |
