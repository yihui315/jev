# INC-002 verification rule — tested, and sound for its stated purpose, with one blind spot

**Adopting forge2's rule as the team standard, with one added clause.** I tested it
rather than agreeing with it, because the rule is going to be executed by everyone and
a rule that false-positives once gets ignored — which forge2 correctly identified as
worse than having no rule at all.

I did **not** investigate who caused INC-002. forge2 stated they neither claim
causation nor innocence and that attribution is untraced; I have no evidence either
way and this note is only about whether the rule is sound.

Artifact: `INC002_RULE_TEST.json`. Tester:
`artifacts/release/v1.3_final/test_inc002_rule.py`.

## 1. forge2's measurement, independently reproduced

`packages/jcc_core/host_launcher.py`:

| | bytes | CRLF count |
|---|---|---|
| HEAD blob (`git cat-file -p`) | 45 366 | 0 |
| worktree | 46 355 | 989 |

delta **989 = exactly the CRLF line count**, and the content is byte-identical after
stripping CR. `git diff` is empty. So `git diff` is right and a bare `sha256sum` is
wrong — form (b), not corruption. Confirmed.

## 2. The rule passes every shape it was written for

Tested with `git cat-file -p` for the HEAD side (not `git show`, which applies
worktree conversion and could mask form (a)) and CR-stripped sha256 for both sides,
plus an independent non-zero-byte count.

| case | expected | rule says | correct |
|---|---|---|---|
| CRLF-only, form (b) | MATCH | MATCH | yes |
| **one-byte real edit** | DIFFER | DIFFER | yes |
| **zeroed, same size — form (a), the real INC-002** | DIFFER | DIFFER | yes |
| zeroed, truncated to 0 | DIFFER | DIFFER | yes |

The one-byte-edit case is the load-bearing one and it passes: a normalisation rule
that is too aggressive would call a real edit identical, and that would be worse than
a false positive. It does not.

## 3. The blind spot: a same-size change involving CR is invisible to BOTH halves

`tr -d '\r'` discards the *position* of every CR, not just the CRLF ones. So:

```
a = b'line1\nline2\n'        c = b'li\rne1\nline2\n'     # CR mid-line, not a line ending

stripped-hash equal?   True    <- forge2's rule says MATCH
size equal?            False   <- the NUL check also passes it
raw sha256 differ?     True    <- only an unnormalised hash sees it
```

A genuine content change — a CR injected into the middle of a line — reads as a
perfect match, **and the non-zero-byte check cannot catch it either**, because the
attack is not NULs. This is the same *class* as form (a): real damage that the
instrument reports as clean. Probability on this tree is low (nothing injects CRs
mid-line), but the rule is being adopted team-wide for exactly the purpose of
detecting silent corruption, so the gap is worth naming rather than leaving implicit.

I also initially mis-tested this — I compared two stripped *hashes* and reported "no
collision" when the stripped *bytes* were identical. Printing the stripped bytes is
what caught it. A hash comparison can hide a collision; printing the reduced content
cannot.

## 4. The added clause

Keep forge2's rule as-is, and add a third signal so the CR blind spot is closed:

```bash
verify_pin() {           # $1 = repo-relative path
  local head_raw work_raw
  head_raw=$(git cat-file -p "HEAD:$1" | sha256sum | cut -d' ' -f1)   # UNNORMALISED
  work_raw=$(sha256sum < "$1" | cut -d' ' -f1)
  local head_lf work_lf
  head_lf=$(git cat-file -p "HEAD:$1" | tr -d '\r' | sha256sum | cut -d' ' -f1)
  work_lf=$(tr -d '\r' < "$1" | sha256sum | cut -d' ' -f1)
  local s z
  s=$(wc -c < "$1"); z=$(tr -d '\0' < "$1" | wc -c)
  [ "$s" = "$z" ]              || { echo "ZEROED $1";      return 1; }  # INC-002 form (a)
  [ "$head_lf" = "$work_lf" ]  || { echo "CONTENT $1";     return 1; }  # real edit
  [ "$head_raw" = "$work_raw" ]|| { echo "CR-ONLY $1 (host CRLF normalisation)"; return 0; }
  echo "MATCH $1"; return 0
}
```

Three outcomes instead of two, and the middle one is what closes the gap: a
difference visible in the raw hash but not the CR-stripped hash is classified
**CR-ONLY** (benign, this host's `core.autocrlf=true`) instead of being silently
folded into MATCH. Anything invisible to *both* hashes and equal in size is the
genuinely dangerous residue, and with three signals it has to defeat all three.

Order matters: the NUL check runs first, because it is the one that names INC-002
directly, and a zeroed file must never be able to reach the hash comparison and be
explained away as CRLF.

## 5. What this does not change

The ruling team-lead already recorded stands: `host_launcher.py` is a host
normalisation artefact, must not be committed, `.gitattributes` is correct as
written, and E1 is `NOT_MEASURED` on a `core.autocrlf=true` host. This note adds
verification hygiene for the pins; it does not reopen that.

## 6. On forge2's `HEAD~1` finding — agreed, and it generalises

forge2's own negative control used `HEAD~1` as "the code before the fix", and when
the A8 re-baseline moved HEAD, `HEAD~1` became the fixed code — so "8 passed" was
measuring the fix. That is the same error class as INC-002 verification itself: a
**drifting reference used to support a conclusion it cannot support.**

The general rule, which I will hold myself to as well: **a negative control must pin
an absolute SHA and assert that the baseline has the expected shape before its output
is believed.** Asserting shape first is what makes the control falsifiable — without
it, a control that silently measures the wrong thing looks exactly like a control that
passed.
