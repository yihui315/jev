# The ZEROED label over-claims: predicate is correct, the wording is not

**weaver's false-positive report, independently reproduced. relay's predicate is
right; only the note text is wrong — and the label is the kind of false that
provokes a rescue of a healthy file.**

Provenance: all figures measured on this host at commit `33c68dd`,
`core.autocrlf=true`. `tools/inc002_tree_check.py` read only, never modified;
`git status` for it clean at the time of writing.

---

## 1. The defect, and why predicate and prose disagree

`tools/inc002_tree_check.py:286` — the predicate:

```python
rec["zeroed"] = rec["nul_bytes"] > 0        # correct; equivalent to s != z
```

`:288-290` — the note that predicate then causes to be appended:

```python
f"INC-002 shape (a): {rec['size']} bytes, ALL NUL -- content destroyed, "
"size preserved so git's stat cache does not notice"
```

**The predicate was fixed and the prose was not.** The wording is a fossil of the
earlier `nul_bytes == len(work)` semantics, so **any** file containing one NUL byte
is described as `ALL NUL` and `content destroyed`.

Reproduced with a 45-byte text file containing one NUL byte literal:

```
fixture: 45 bytes, 1 NUL
predicate (nul_bytes > 0) -> True        -> the ZEROED branch runs
note printed: "INC-002 shape (a): 45 bytes, ALL NUL -- content destroyed, ..."
```

The file is 99.99 % intact prose. **The predicate is doing its job; the sentence
attached to it is lying.**

## 2. Why it stopped reproducing, and why that is worse

weaver measured `AEGIS_INC002_ADJUDICATION.txt` as worktree 8 043 B / **0 NUL** vs
HEAD 8 042 B / **1 NUL** — one NUL byte literal in a document *about* NUL, which is
exactly the shape the stale wording misreads.

Measured again now, the same file is **8 043 B / 0 NUL in both worktree and HEAD**.
aegis has amended it since, and my own `--all-tracked` run is clean
(`checked=750`, `verdict=FAIL` on unrelated DRIFT, no `[ZEROED]`).

**So the false positive no longer reproduces — and the defect is still there.** The
note text is unchanged. It will fire again on the next document that writes a NUL
literal, and this repository will keep producing such documents, because any document
*about* NUL necessarily contains NUL. **A clean run today is not a fix**, and should
not be recorded as one.

## 3. The missing reverse control is the substantive gap

The suite already covers a genuinely zeroed file: `:453-460` writes
`b"\x00" * len(original)` and asserts `SEV_ZEROED`. And per the comment at `:275-281`
— with the threshold described as *"a threshold wearing a predicate's clothes"* — the
guard was mutation-tested and **does** fail correctly when the predicate is reverted.
weaver independently confirms that, and I agree: **this is the one guard this round
that caught its own regression and named the mechanism.**

What is missing is the **other** direction. There is no case asserting how a
*legitimate* single-NUL document should be handled. That absence matters because the
predicate is **shared** between the two cases and only the prose differs — so the
suite cannot currently tell "flagged a NUL-containing document" (correct, and
required by `s != z`) apart from "declared the content destroyed" (wrong).

### Suggested correction, if relay wants it

Keep the predicate — it is right and the spec requires it — and make the note state
the fact rather than a conclusion the tool has not earned:

| condition | note |
|---|---|
| `nul_bytes == size` | `all bytes NUL — content destroyed (INC-002 shape (a))` |
| `0 < nul_bytes < size` | `contains NUL byte(s) — flagged per s != z; verify these are byte literals, not damage` |

Plus a selftest case pinning the second string, so the **wording** cannot regress the
way the predicate nearly did.

## 4. The baseline drifted inside hours — and that is the point

| measurement | tracked files | files containing any NUL |
|---|---|---|
| relay's baseline (earlier) | 748 | **0** |
| weaver, recent | **722** | **1** (`AEGIS_INC002_ADJUDICATION.txt`) |
| mine, now | **750** | **0** |

Three different numbers, hours apart, on a moving tree. The file that broke the
"zero NULs" baseline was created *after* relay measured and *before* aegis committed
`1e45475`, and has since been amended away.

**This is the two-part criterion aegis and weaver converged on, failing in the wild:**
(a) does the change eliminate a **demonstrated** false negative, **and (b)** can that
false negative be **reproduced on the target data**? Without (b), *"stricter"* is
unfalsifiable — and here the target data did not merely fail to reproduce, it
**changed identity between the two measurements.**

## 5. Do not "fix"

- **`tr -d '\0'` is correct** — stripping NULs is how non-empty bytes are counted.
  aegis's scanner v1 flagged it as a defect and a whitelist corrected that; it nearly
  broke a correct check.
- **`tools/inc002_tree_check.py:165` normalisation is correct**
  (`data.replace(b"\r\n", b"\n")`) and the predicate at `:286` is correct. Neither is
  the defect; the prose at `:288-290` is the whole defect.
- The one genuinely open file in this cluster is
  `artifacts/release/v1.3_final/INC002_CRLF_HASH_NOTE.txt` — **forge2's**,
  **NOT-TRACKED** — `:41-42` live `tr -d '\r'` recipe, and `:45-46` a false
  `git show` causal.

## 6. A habit this round keeps demanding

Every stale claim I have retracted this round shared one shape: **a measurement taken
at one moment, reported as a standing fact.** My `"packages/` clean" assertion, the
forge report I retracted in `33c68dd`, the pin verdict restricted after the CR-count
finding, and now this false positive that has quietly stopped reproducing. The check
is always the same and always cheap — *re-read the artefact at the moment you rely on
it* — and the discipline is to state a measurement with its timestamp and the
conditions it was taken under, so the next reader knows whether it still holds.
