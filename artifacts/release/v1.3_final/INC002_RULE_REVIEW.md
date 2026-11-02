# INC-002 verification rule — tested, and sound for its stated purpose, with one blind spot

> **SUPERSEDED IN PART by team-lead's final rule (broadcast).** The final rule is
> `b.replace(b"\r\n", b"\n")` — **CRLF→LF only**. The rule reviewed here used
> `replace(b"\r", b"")` — **delete every CR** — which is the flaw team-lead's version
> corrects. My own `a4fab43` implementation shipped that same flaw *after* I had
> criticised `tr -d '\r'` in forge2's version for precisely this reason. Both committed
> scripts are fixed in the next commit and now carry a self-test that asserts their own
> discrimination instead of assuming it. Kept because the retraction is the finding.
>
> Measured, both forms on the same inputs:
> | shape | delete-every-CR | CRLF→LF-only |
> |---|---|---|
> | pure CRLF line endings | MATCH (right) | MATCH (right) |
> | **mid-line CR injection** | MATCH (**wrong**) | DIFFER (right) |
> | **CR immediately before LF** | MATCH (**wrong**) | DIFFER (right) |
>
> Provenance for every figure in this document: HEAD side via
> `git cat-file -p HEAD:<path>`; worktree side via a direct read of the file; checked at
> commit `a4fab43` on `core.autocrlf=true`.

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

> **CORRECTED — the script below was WRONG until weaver's report (see commit below).**
> It used `tr -d '\r'` on both sides, which is the delete-every-CR form this very
> document criticises. Reproduced against weaver's attack: `HEAD=b'ab\r\n'`,
> `WORK=b'a\rb\r\n'` (in-band CR injection = real content damage) —
> `tr -d '\r'` yields `61 62 0a` on **both** sides and reports **MATCH**: a
> **false negative on real corruption**. `b.replace(b"\r\n", b"\n")` yields
> `61 62 0a` vs `61 0d 62 0a` and correctly reports DIFFER. The shell form is
> `sed 's/\r$//'`, which strips only the line-ending CR.
>
> This is the second time in two commits that the rule I shipped carried the exact
> defect I had criticised in someone else's — first in the Python scripts
> (`981d216`), now in the prose. **The prose is the more dangerous copy**, because
> it is the one people paste. A rule review that quotes a broken rule is worse than
> no rule review.
>
> Also corrected: weaver is right that `git show` and `git cat-file -p` are
> **byte-identical** for this blob on this host — measured, both
> `26d9cd51e44588378dc2…`, 45 366 B. `cat-file -p` is the right choice for
> *semantic clarity*, **not** because it "masks" anything. My earlier claim that it
> "could mask a zeroed file" is withdrawn; that is not what the choice is for.

Keep forge2's rule as-is, and add a third signal so the CR blind spot is closed.
**Normalisation below is CRLF→LF only — `python3 -c`, never `tr -d '\r'`, and never
`sed 's/\r$//'`.**

> **`sed 's/\r$//'` IS NOT EQUIVALENT, and I recommended it here until weaver measured
> the real binary and prism-3dui supplied the empty-script control.** Real GNU sed 4.9
> masks size-preserving differences. Measured, invoking the real `sed` with exact bytes:
>
> | HEAD | worktree | size | `sed 's/\r$//'` | `replace(CRLF,LF)` |
> |---|---|---|---|---|
> | `0d 0a` | `0a 0d` | 2 = 2 | **MATCH — masked** | DIFFER ✅ |
> | `61 0d 0a` | `61 0a 0d` | 3 = 3 | **MATCH — masked** | DIFFER ✅ |
> | `0d 0d 0a` | `0d 0a 0d` | 3 = 3 | **MATCH — masked** | DIFFER ✅ |
> | `0d 0a 0d` | `0d 0d 0a` | 3 = 3 | **MATCH — masked** | DIFFER ✅ |
>
> Exhaustive over `{a, CR, LF}^1..2` (12 payloads): **2 size-preserving distinct pairs
> masked, 0 masked by `replace`.** Every masked pair is size-preserving with different
> content — **precisely the INC-002 shape.**
>
> **⚠️ THE MASKING IS CAUSED BY THE SCRIPT, NOT BY THE READER ALONE — and I initially
> got this wrong too.** prism-3dui's control is decisive: on this host `sed ''` with an
> **empty** script is **not** inert. It already strips a CR that is paired with an LF:
>
> ```
> a\r\n   61 0d 0a  ->  sed ''  61 0a      (data changed by the READER)
> a\r\r\n 61 0d 0d 0a ->  sed ''  61 0d 0a   (data changed by the READER)
> a\n\r   61 0a 0d  ->  sed ''  61 0a 0d   (unchanged)
> cat control on a\r\n -> 61 0d 0a          (cat is clean; sed's reader is not)
> ```
>
> But separating the two on my own four pairs shows the reader is **not** the cause of
> the masking:
>
> | pair | `sed ''` (reader only) | `sed 's/\r$//'` (reader + script) | `replace` |
> |---|---|---|---|
> | CR-LF vs LF-CR | differ | **MASKED** | differ |
> | aCR-LF vs aLF-CR | differ | **MASKED** | differ |
> | CRCRLF vs CRLFCR | differ | **MASKED** | differ |
> | CRLFCR vs CRCRLF | differ | **MASKED** | differ |
>
> **Reader alone masks 0/4. Reader + script masks 4/4.** The script strips the CR the
> reader left behind on the final line, which is what collapses two different inputs
> onto one output. **So the defect is the `s/\r$//` script, and it would still be a
> defect with a perfectly faithful reader.**
>
> **Therefore: any byte-level conclusion drawn through `sed` must first be justified by
> `sed '' <file>` showing the tool is inert when unloaded.** I did not do that in
> `crlf_form_safety.py`, and the reason my conclusion still came out right is luck —
> `replace` has no reader at all, so the reference side was never contaminated. The
> measurement was sound; the *reasoning about the mechanism* was not, and I would have
> published a wrong explanation of a right result.
>
> **I propagated this error twice**: first endorsing `tr -d '\r'`, then "correcting" it
> to `sed 's/\r$//'` and shipping the shell form in this file. Both were wrong, and the
> second was worse because it *looked* verified — I had reasoned about equivalence
> instead of running the tool. **The only form I will now put in a document is
> `b.replace(b"\r\n", b"\n")`, or the shell equivalent below, and the shell equivalent
> is `python3`, not `sed`.**

```bash
verify_pin() {           # $1 = repo-relative path
  local head_raw work_raw
  head_raw=$(git cat-file -p "HEAD:$1" | sha256sum | cut -d' ' -f1)   # UNNORMALISED
  work_raw=$(sha256sum < "$1" | cut -d' ' -f1)
  local head_lf work_lf
  # CRLF -> LF ONLY. `tr -d '\r'` deletes every CR; `sed 's/\r$//'` is line-wise and
  # masks size-preserving CR rearrangements (measured, see above). python3 does the
  # same byte-level replace as the Python form, with no line semantics.
  crlf_to_lf() { python3 -c 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read().replace(b"\r\n",b"\n"))'; }
  head_lf=$(git cat-file -p "HEAD:$1" | crlf_to_lf | sha256sum | cut -d' ' -f1)
  work_lf=$(crlf_to_lf < "$1" | sha256sum | cut -d' ' -f1)
  local s z
  s=$(wc -c < "$1"); z=$(tr -d '\0' < "$1" | wc -c)
  [ "$s" = "$z" ]              || { echo "ZEROED $1";      return 1; }  # INC-002 form (a)
  [ "$head_lf" = "$work_lf" ]  || { echo "CONTENT $1";     return 1; }  # real edit
  [ "$head_raw" = "$work_raw" ]|| { echo "CR-ONLY $1 (host CRLF normalisation)"; return 0; }
  echo "MATCH $1"; return 0
}
```

If `python3` is unavailable, use the **Windows-native** form, which has the same
byte-level semantics on this host:

```powershell
function crlf_to_lf { [Console]::OpenStandardInput().ReadToEnd() `
  .Replace("`r`n", "`n") | Write-Host -NoNewline }
```

The Python equivalent, which is what the committed scripts use:

```python
norm = lambda b: b.replace(b"\r\n", b"\n")     # NOT b.replace(b"\r", b"")
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
