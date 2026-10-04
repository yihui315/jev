# Host normalisation and E1 — verified finding, with a mechanism correction

**Recorded so a ruling of record is not written down on a mechanism that is not the
one the code uses. Verification only: nothing was changed, no file in `packages/` was
touched, no test was re-run.**

## 1. What is confirmed

`.gitattributes` at the repo root, read byte-exactly with `od -c` (45 bytes):

```
* text=auto eol=lf
*.png binary
*.pdf binary
```

`git ls-files --eol packages/jcc_core/host_launcher.py`:

```
i/lf    w/crlf    attr/text=auto eol=lf
```

The repository requires LF; this host's `core.autocrlf=true` wrote CRLF into the
worktree. The two configurations conflict, and the file shows both halves of that
(`i/lf` index, `w/crlf` worktree). `host_launcher.py` therefore shows as modified in
`git status` with **zero content difference** — `git diff --numstat` on it is empty.

**Decision confirmed correct: do not commit it.** Committing would pollute
`1d7d55a` (CR-003, forge2) and the next checkout would re-dirty the file anyway.
**`.gitattributes` is correct as written** and must not be changed to chase a host
config problem.

## 2. The correction: E1 does not compute content hashes

The ruling as first stated said E1 is "按内容哈希算冻结树" and warned of a
"幻影 clean（如果文件恰好反向归一化）". The first is not what the code does, and the
second cannot happen.

`tools/final_gate_run_v1_3.py:916-924` is a **porcelain parser**:

```python
for line in porcelain.splitlines():
    code, path = line[:2], line[3:].strip().strip('"')
    if code == "??":
        untracked.append(path)
    elif path.startswith(PRODUCT_PREFIXES):
        product.append(path)
    else:
        metadata.append(path)
problems = []
if product:
    problems.append("tracked product file(s) modified: %s" % product)
```

`PRODUCT_PREFIXES` (line 901) is `packages/ apps/ facades/ scripts/ config/
fixtures/ tools/ integrations/ adapters/ policies/ evals/ tests/`. The leg records
`cmd=["git status --porcelain"]`. No hashing anywhere in the path.

Consequences for the wording:

* The byte-level facts are **true and worth keeping** — worktree sha256
  `7b1ba0cb5b70040c` with 989 CRLF, HEAD blob `17d36c5223aff329` with 0 CRLF — but
  they are **not the mechanism**. E1 fails because porcelain prints
  `M packages/jcc_core/host_launcher.py`, not because a digest diverged.
* **"Phantom clean" is not a reachable failure mode here.** Porcelain reported dirty
  for a worktree that differs from the index. A phantom *clean* would require
  porcelain to report clean while the bytes differ — the opposite of the observed
  behaviour. Keeping the phrase in the record would point a future reader at a risk
  this code cannot produce.

**The practical conclusion is unchanged and still holds:** E1 is downstream of
`git status`, so on a `core.autocrlf=true` host it fails with no code having changed.
It must not be made to pass by committing the CRLF difference. `NOT_MEASURED` on such
hosts stands — and independently, `V1_3_LEG_E1_FROZEN_TREE.json` does not exist, so
there is no FAIL record being softened into a pass.

## 3. Suggested record wording

> E1_FROZEN_TREE parses `git status --porcelain`; it does not hash content. On a
> `core.autocrlf=true` host the worktree is written CRLF while the index is LF,
> porcelain reports `M`, and E1 fails without any code having changed. It must not be
> made to pass by committing the CRLF difference. NOT_MEASURED on such hosts.

## 4. The transferable lesson, which is the second instance today

Two independent instances of the same mistake, one mine and one team-lead's:

* team-lead asserted "GREEN 未被污染" from 4 discrete samples — a sampling assertion
  dressed as a fact.
* I asserted "`packages/` clean" as a standing property; it was true at 21:07 and
  false by 21:12, when `host_launcher.py` appeared with mtime 21:05:58.

**Invariant statements about workspace or host state must carry a timestamp and the
method, and must be re-verified at the moment they are relied upon.** The honest form
is "clean as of HH:MM, verified by `<command>`".

A second, smaller lesson: my own `cat .gitattributes` in a chained command returned
nothing visible, and I nearly reported the file as empty. A swallowed line in a
compound command is not evidence. `od -c` settled it in one call. **Rendered output
that looks empty should be re-read bytewise before it becomes a claim.**

## Artifacts

No new artifact files for this turn; everything referenced is in `a33998a` and the
working tree. Verified with `od -c .gitattributes`, `git ls-files --eol`,
`sed -n '915,940p' tools/final_gate_run_v1_3.py`, `git diff --numstat`, and a
worktree-vs-HEAD sha256 comparison.
