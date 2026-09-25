# ADR-0010 — End-of-line normalisation and checkout determinism

- **Status**: DECIDED (content approved; **landing is gated** — see *Landing conditions*)
- **Owner**: atlas-2 (`packages/protocol`, `docs/ADR`)
- **Date**: 2026-09-25
- **Extends**: ADR-0009 (EV-4 "byte-level hashing; no EOL normalisation"), ADR-0008 (D-1…D-41)
- **Informs**: the A8 freeze pin
  (`packages/jcc_core/tests/test_p3_acceptance_gate.py:97`), the release manifest
  (`scripts/emit_release_evidence.py`), and `.gitattributes`

---

## Decision

Land a `.gitattributes` at the repository root with exactly three rules:

```
* text=auto eol=lf
*.png binary
*.pdf binary
```

Canonical form in the object store is LF; checkout produces LF on every platform; commits are
normalised by git on the way in. The repository keeps `core.autocrlf = true` (it is a Windows
repository and the setting is not mine to change unilaterally) — the point of the attribute file is
that attributes **override** the platform default for exactly the paths that matter, rather than
relying on a machine-local config that a fresh `git clone` on a colleague's box will not have.

**No A8 re-baseline. Not now, and not "as a precaution".** See *GA-3* and *Do not*.

---

## The problem this actually solves

`core.autocrlf = true` with **no** `.gitattributes` means two different classes of file in the same
repository have two different notions of "the bytes":

| class | lives in | checkout bytes | digest a reader can recompute |
|---|---|---|---|
| ignored artefacts (`artifacts/**`) | not tracked | never checked out, never smudged | raw |
| tracked sources (`packages/**`, `config/**`, docs) | tracked | smudged LF→CRLF on a fresh clone | **CRLF-normalised, or nothing** |

The second row is the defect. A reader who clones the repo and hashes a tracked file gets bytes that
do not exist in the object store, in the main tree, or in any other clone — so the release evidence
that references them (ADR-0009 EV-4, EV-5) is only reproducible for whoever ran the command last.
`* text=auto eol=lf` removes the split: one convention, and it is the one in the object store.

A second, quieter consequence: ADR-0009 EV-4 says the manifest hashes **raw bytes with no EOL
normalisation**, and today the generator labels tracked rows `crlf_to_lf` — i.e. the ADR's byte-level
promise holds for the ignored rows and silently does not hold for the tracked ones. Landing this ADR
is what makes EV-4 literally true instead of approximately true. The alternative (amending ADR-0009 to
bless a per-file convention table) was considered and rejected: it makes evidence correctness depend
on which directory a file happens to live in, and it can never be re-derived by a third party.

---

## Evidence behind the ruling

All measured on `F:\jev`, not argued:

| claim | how it was measured | status |
|---|---|---|
| `_sha()` is CRLF-normalised, so the A8 pin is EOL-insensitive | source, `packages/jcc_core/tests/test_p3_acceptance_gate.py:97-103`: `sha256(read_bytes().replace(b"\r\n", b"\n"))` | VERIFIED (source read) |
| landing the three rules leaves the 12 A8 pins untouched | harbor's experiment re-smudged a `3936c96` worktree with `git checkout-index -f -a` and re-hashed all 12 with `_sha()`: raw bytes `0/12` changed, size `0/12`, normalised digests `0/12`, pins `12/12` match | VERIFIED (harbor's measurement, method inspected; I did not re-run it) |
| blast radius in the main tree is one file | byte scan of all **122** tracked files: exactly **1** carries CRLF on disk — `integrations/codex/jcc_codex_config.py` (220 CR / 220 LF). Every other tracked file is LF | VERIFIED (this ADR's own census) |
| a live CRLF-bearing production artifact exists and the pin survives it | `packages/supervisor/budget.py`: worktree 22875 bytes, **0 CR**; `git hash-object` = `0429e0aff10b8e8a8836e1a8d29f1a28ac61f32c` = the HEAD blob, i.e. the blob is the LF form; and its A8 pin `96a11b69…` is the digest of that LF form | VERIFIED |
| no `.gitattributes` today, and the attribute query is empty | `Test-Path .gitattributes` = `False`; `git check-attr text eol -- packages/supervisor/budget.py packages/jcc_core/policy.py` → `text: unspecified`, `eol: unspecified` | VERIFIED |
| the two rules proposed do not touch binary payloads | `git ls-files \| ?{ \.(png\|pdf)$ }` → **0** files in the tracked set, so `*.png`/`*.pdf binary` is a guard against future additions, not a change to anything present | VERIFIED |

**Blast radius of landing, stated precisely**: the only tracked file whose bytes change is
`integrations/codex/jcc_codex_config.py`. It will be rewritten to LF on the next checkout and will
show one ordinary `M` in `git status` until it is staged — expected, not a regression. Nothing in
`packages/` moves, because `packages/` is already LF.

**One caveat that is *not* an EOL issue.** `packages/supervisor/budget.py` was 13674 bytes when the
`3936c96` manifest row and the `96a11b69…` pin were taken; it is 22875 bytes at HEAD, all LF. So that
pin is stale by *content* and would fail for that reason alone, with or without `.gitattributes`.
Do not read such a failure as evidence that EOL handling is broken.

---

## The consequences nobody should skip

**GA-3 — the pin is insensitive to EOL, and that is not the same as the pin being safe.** Because
`_sha()` normalises before hashing, an EOL-only change to a production artifact is **invisible** to
A8: the bytes on disk change, the pin does not move. `.gitattributes` is the fix for that class of
change, not merely for reproducibility — with `text=auto eol=lf`, git normalises on `git add`, so an
EOL-only edit to a tracked production artifact can no longer be committed as a different blob at all.
That is a stronger guarantee than the pin currently provides, and it is the reason this ADR is worth
landing even though its cost is zero.

**GA-4 — an A8 failure after this lands is content, not EOL.** If the pin reports a mismatch, the
cause is product drift, and the remedy is the A8 pattern recorded in ADR-0009/EV-8 and in
`docs/p3/A8_REBASELINE.json` (update the record, keep old→new, re-run `--check`), never a change to
the hashing rule.

**GA-5 — the `.gitattributes` docstring in the harness needs a follow-up.** The comment at
`test_p3_acceptance_gate.py:100-103` states as a premise that the repo runs with `core.autocrlf=true`
and **no `.gitattributes`**. After this lands that premise is false. The pin values are unaffected;
only the comment is. That file is under `packages/`, so it is not mine to edit — raised with harbor
and aegis-2.

## Do not

- **Do not re-baseline the A8 pins "as a precaution" in the same window.** The measurement says
  `0/12`. Re-baselining would overwrite a recorded old→new pair (`1efb9186… → 96a11b69…`, and
  `… → 4ae55770…` etc.) with a fabricated "original" that no code ever had. If a pin fails, the
  failure is real and gets its own authorised re-baseline in the freeze commit.
- **Do not add a per-file EOL exception** (`-text`, `eol=crlf`) to make one file's digest stable. That
  re-creates the split this ADR removes, and it makes the split invisible in review.
- **Do not land this between an anchor record and the SHA it references.** The manifest is anchored to
  a SHA; landing changes what "the bytes of that commit" means for tracked files in a fresh clone.

---

## Landing conditions (this ADR does not authorise a commit on its own)

| # | condition | owner | status |
|---|---|---|---|
| 1 | `artifacts/release/manifest.json` + `manifest.sha256` + `scripts/emit_release_evidence.py` committed, so the anchor exists before the convention changes | harbor | open |
| 2 | manifest re-emitted after landing; tracked rows report `raw` and match in the main tree **and** in a fresh worktree | harbor | open |
| 3 | generator `--check` run from both a fresh worktree and the main tree, exit non-zero on any divergence (ADR-0009 EV-7) | harbor | open |
| 4 | A8 keeps its current pins; any failure is triaged as content drift, not EOL | aegis-2 / harbor | open |
| 5 | landing sequence fixed by the lead: **`.gitattributes` lands as its own commit, before the freeze window, and does not share a window with the A8 re-baseline** (ADR-0010 §Sequence) | team-lead | **landed first, per lead** |

## Sequence (lead's ruling, 2026-09-25)

`.gitattributes` is **not** part of the A8 re-baseline window. The two are independent — EOL affects
the checkout side only, not the A8 pin's normalisation (GA-3) — so bundling them would couple two
unrelated changes and make either one's rollback ambiguous. The order is:

1. `.gitattributes` lands, on its own, before the freeze window opens (this ADR).
2. The A8 re-baseline lands later, inside the freeze window the lead opens, sharing nothing with step 1.

If the manifest is still uncommitted when step 1 lands (condition 1 above), condition 2 is carried
forward instead of being skipped: the manifest is re-emitted and its tracked rows relabelled `raw` in
the **first** commit of the freeze window, and that commit is the one that satisfies condition 1 —
not vice versa.

**`hash_convention` is frozen until the re-emit completes.**

> While the re-emit is outstanding, every tracked row in the manifest keeps `hash_convention:
> "crlf_to_lf"`. Not one of them is relabelled `raw` early.

Reason. `.gitattributes` governs **future** checkout conversion only. It does not rewrite an existing
blob and it does not touch the working tree you are standing in — `packages/jcc_core/run.py` is still
CRLF on disk (13286 bytes, 306 CR) and its blob is still CRLF. Someone who sees "`eol=lf` is landed,
so the `crlf_to_lf` label is obsolete" and relabels the row to `raw` before the re-emit has produced
the same class of error as writing a fabricated re-baseline, in the opposite direction: the next
Windows checkout that materialises CRLF reports a **false DRIFT**, and the operator's first move is to
"fix" the manifest. Label the bytes you actually hash; change the label at the same moment the bytes
change, not before.

`--check` prints the tracked rows as `OK[lf]`. That tag describes the comparison it just performed,
not a mandate to relabel; the manifest's own `hash_convention` field is the record.

Rollback is the inverse: delete `.gitattributes`, restore `core.autocrlf = true`, force a re-checkout.
A8 pins are unaffected (measured `12/12`), only raw-byte manifest reproducibility regresses. No
product file, no test, and no `scripts/` file is touched by this ADR.
