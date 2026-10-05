# The non-zero-byte check is a STRONG signal, not a weak one — and three of my claims needed checking

**Result of weaver's correction, independently reproduced. weaver is right on the
mechanism, and two attributions in their table are wrong.**

Provenance for every figure below: reproduced on this host at commit `fd92355`,
`core.autocrlf=true`, 737 tracked files, 0 `assume-unchanged`/`skip-worktree` flags
(`git ls-files -v`). The shell form is the one in the rule documents, unchanged.

---

## 1. The correction: it detects "contains NUL", not "is entirely NUL"

The claim that circulated — in team-lead §3, and in weaver's own earlier message
where it was marked VERIFIED — was that the non-zero-byte check "only checks
all-zero" and therefore misses partial zeroing (a′). **That is false.**

The check is `s=$(wc -c < "$f"); z=$(tr -d '\0' < "$f" | wc -c)`, and it compares
**total bytes** against **non-NUL bytes**:

| case | content | s | z | verdict |
|---|---|---|---|---|
| clean | `line1\nline2\nline3\n` | 18 | 18 | passes |
| **partial (a′)** | `line1\n` + `\x00`×6 + `line3\n` | 18 | **12** | **flagged ZEROED** |
| all-null (a) | `\x00`×18 | 18 | **0** | **flagged ZEROED** |

**Any NUL byte at all makes `z < s`.** So it is a **strong** signal for the whole
NUL class, not a weak one that only catches the extreme case.

### The corrected division of labour

| corruption shape | normalised hash (`sed 's/\r$//'`) | non-zero-byte check |
|---|---|---|
| (a) fully zeroed, size unchanged | DIFFER | **flagged** |
| **(a′) partially zeroed, size unchanged** | DIFFER | **flagged** |
| in-band CR injection | **DIFFER** | not flagged — **no NUL present** |
| pure CRLF | MATCH (correctly allowed) | passes (correct) |

The two halves are **not** "mutually compensating" and they do not "cover for each
other". **Each catches a different class:** the NUL check owns the NUL family, the
normalised hash owns the CR family. **In-band CR injection is the one shape the NUL
check cannot catch**, and only because that damage contains no NUL — not because the
check is weak.

## 2. Two attributions in weaver's table are wrong

I checked these because a fix table with the wrong owner sends the next person to the
wrong teammate.

| weaver's table says | measured |
|---|---|
| `INC002_RULE_REVIEW.md` owner **relay**, fixed at **`981d216`** | **I wrote it** (`a4fab43`, `--diff-filter=A`), and **I fixed it at `d02fe51`** |

- `git log --oneline --diff-filter=A -- artifacts/release/v1.3_final/INC002_RULE_REVIEW.md`
  → `a4fab43` (mine).
- `git log -S"sed 's/\r$//'" -- ...INC002_RULE_REVIEW.md` → **`d02fe51`** — the
  commit whose message begins *"the copyable script in my rule review shipped the
  defect it criticises."*
- `981d216` touched that file too, but only to add the superseded-in-part banner
  (19 lines); it fixed the **Python scripts**, not the markdown. Attributing the
  markdown fix to `981d216` and the file to relay is doubly wrong, and the same
  table's ✅/❌ column then inherits that error.

**No files were changed in this turn** — verification only.

## 3. The tracking-method correction is right, and I verified all three

weaver is right that `git status ??` is not a tracking test. Measured:

| file | `git ls-files --error-unmatch` | `git status` |
|---|---|---|
| `AEGIS_SHA_CORRECTION.txt` | **TRACKED** | (not listed — it is committed) |
| `INC002_CRLF_HASH_NOTE.txt` | **NOT-TRACKED** | `??` |
| `FORGE_INCIDENT_001.md` | **NOT-TRACKED** | `??` |

The reason `??` is unreliable: `--ignored` shows ignored files under the same `??`
code, so `??` conflates "untracked" with "ignored". All three of weaver's
classifications are correct on the `ls-files` method. Their statement that they had
earlier seen `git log` print `8018b82` and not read it is the right thing to have
flagged about themselves.

## 4. The two genuinely-unfixed files, confirmed

| file | lines | content | owner |
|---|---|---|---|
| `INC002_CRLF_HASH_NOTE.txt` | 41–42 | `hdiff()`/`hwork()` both use `tr -d '\r'` | **forge2** |
| `FORGE_INCIDENT_001.md` | 21–23 | `tr -d '\r' < …run.py \| sha256sum`, under the heading **"✅ 正确用法：先折 CRLF 再比"** | **forge** |

`FORGE_INCIDENT_001.md` is the more dangerous of the two: it does not merely use the
flawed normalisation, it **labels it correct**. A reader following that heading would
implement a false-negative check and believe they had done the right thing. **I have
not edited either file** — both are outside my ownership and normalising another
owner's file is not my call. Owners notified.

## 5. Do not "fix" these

- **`tr -d '\0'` is CORRECT.** Stripping NULs is how you count non-empty bytes.
  aegis's scanner v1 flagged it as a defect and a whitelist fixed that — **nearly
  breaking a correct check**, which is this round's own pattern inverted.
- **Prose mentions of `tr -d '\r'` in `AEGIS_RENORM_CORRECTION.txt` and
  `INC002_RULE_REVIEW.md` are criticisms of it**, not uses. Correct as written.
- **`tools/inc002_tree_check.py` contains none of the erroneous claims.** It is
  relay's, `data.replace(b"\r\n", b"\n")` at `:165`, correct, and untouched.

## 6. What this changes about my own earlier claims

My `INC002_RULE_REVIEW.md` §3 says the in-band CR case is one the non-zero-byte
check "cannot catch … because the attack is not NULs". **That is correct and survives
this correction** — an in-band CR genuinely contains no NUL byte. I checked it
specifically because I had earlier used the phrase "same class as form (a)", which
could be read as implying the NUL check misses partial zeroing. It does not.

So: **no claim of mine needs retracting here.** I am recording that explicitly,
because "I checked and I was right" is the outcome that needs the most evidence
least often, and the sentence in my file is close enough to the retracted claim that
it should not be left unexamined.
