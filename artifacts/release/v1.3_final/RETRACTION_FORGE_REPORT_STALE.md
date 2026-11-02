# Retraction: my FORGE_INCIDENT_001.md report was stale — and grep-line ≠ live defect

**I relayed a stale report to forge and team-lead, and both were wrong. weaver
retracted item 5 on aegis's challenge; I verified independently and am retracting my
relay of it here.** Commit recorded below.

Provenance: all figures read on this host at commit `b541f9f`, `core.autocrlf=true`.
File mtimes quoted as measured, not inferred.

---

## 1. What I got wrong

I messaged forge:

> `FORGE_INCIDENT_001.md:21-23` … **under the heading "✅ 正确用法"** … A reader
> following that heading implements a check that cannot see in-band CR damage and
> believes they succeeded.

I repeated it to team-lead as the more dangerous of the two unfixed files, and called
the state "2 files still unfixed."

**Measured now:**

```
L21: # ❌ 错误用法 2：tr -d '\r' 剥掉【每一个】CR，包括行内注入的那个
L26: > ⚠️【2026-10-05 订正，prism-3dui 提出并复现】上面标 ❌ 的第 2 条是错的用法
mtime: 11:12:12
```

The heading is now **❌ 错误用法**, and line 26 carries a dated correction naming `sed`
as the correct form. **The file was fixed — by prism-3dui's finding and forge's
edit — after I read it and after I sent the report.** I relayed a pre-fix snapshot as
current state.

**aegis's adjudicator confirms it independently:**
```
$ python runtime/_aegis_adjudicate_item5.py      # exit 0
INC002_CRLF_HASH_NOTE.txt      live defects: 1
FORGE_INCIDENT_001.md          live defects: 0
```
Its reasoning is the part worth keeping: lines 21–23 sit inside a fenced block headed
`# ❌ 错误用法 2`, with a dated correction at line 26 — **the same shape as
`AEGIS_RENORM_CORRECTION.txt`, which weaver already agreed must not be rewritten.**

**My report risked exactly the failure weaver's process warning describes:** if forge
had "fixed" line 21 without reading line 26, **the correction would have been deleted
from the document** — removing a safety warning, which is worse than leaving a live
defect in place.

## 2. weaver's L252 claim was also wrong

I relayed "`:21-23,252`". L250–254 is a **`netstat` observation block**
(`netstat: TCP 127.0.0.1:4778 ... LISTENING 21088`) and contains **no `tr -d` at
all.** weaver took a grep line number as a conclusion without reading the context. I
passed it on without checking, so the error is mine for propagating and theirs for
producing.

## 3. The process warning, adopted — and it is the real finding

> **A grep line hit is not a live defect.** Distinguishing "live defect" from
> "correct citation" requires context: the fence, the heading, and the surrounding
> paragraphs.

Line-oriented grep produces **both** failure directions:

* **warning read as defect** → someone "fixes" a **correct warning**, deleting it.
  This is the dangerous one: the fix looks like progress and removes a safeguard.
* **defect read as citation** → a real defect is missed.

I performed the first kind myself, twice in one message: `tr -d '\r'` appears **12
times** in `FORGE_INCIDENT_001.md`, and I cited a line number from a 12-hit file
without classifying any of them.

**Adopted going forward: no defect is reported from a line number alone.** Cite the
heading or fence status, or run a context-aware classifier. `runtime/_aegis_adjudicate_item5.py`
is the reference implementation (exit 0, prints per-file live-defect counts).

## 4. The single genuinely-open file, with TWO defects

`artifacts/release/v1.3_final/INC002_CRLF_HASH_NOTE.txt` — **forge2's, NOT-TRACKED.**

**Defect 1 — lines 41–42, live false-negative recipe:**
```bash
hdiff() { git cat-file -p "HEAD:$1" | tr -d '\r' | sha256sum | cut -d' ' -f1; }
hwork() { tr -d '\r' < "$1" | sha256sum | cut -d' ' -f1; }
```
`tr -d '\r'` deletes every CR, not just CRLF line endings, so an in-band CR injection
is invisible:
```
HEAD = b'ab\r\n'      clean
WORK = b'a\rb\r\n'    in-band CR injection = real damage
tr -d '\r' -> 61 62 0a vs 61 62 0a  MATCH   <- FALSE NEGATIVE
```

**Defect 2 — lines 45–46, a FALSE CAUSAL:**
```
-- note `git cat-file -p`, NOT `git show`: `git show` applies the same
   working-tree conversion, so it can hide (a).
```
**The causal half is false.** Measured: `git show` and `git cat-file -p` on
`HEAD:packages/jcc_core/host_launcher.py` are byte-identical — both
`26d9cd51e4458837…`, both **45 366 B**. `cat-file -p` is correct **for semantic
clarity only**.

**I made this exact error earlier in the round and withdrew it**; it is still
sitting in forge2's note, which is how a retracted claim keeps propagating. That is
the argument for correcting it at the source rather than only in a message.

## 5. Attribution — corrected table, and one row still wrong

| file | live `tr -d` recipe | state | owner |
|---|---|---|---|
| `FORGE_INCIDENT_001.md` | **0** | ✅ fixed (prism-3dui found, forge fixed) | forge |
| `AEGIS_SHA_CORRECTION.txt` | 0 | ✅ fixed `85302ea` | aegis |
| `INC002_RULE_REVIEW.md` | 0 | ✅ fixed — **but see below** | **me** |
| **`INC002_CRLF_HASH_NOTE.txt`** | **1** (+1 false causal) | ❌ **open, NOT-TRACKED** | **forge2** |

**`INC002_RULE_REVIEW.md` is mine, not relay's.** Author `a4fab43`
(`git log --diff-filter=A`), fixed `d02fe51` (`git log -S"sed 's/\r$//'"`).
`981d216` added the superseded banner and fixed the **Python scripts**. This row has
now been wrong in three consecutive shared tables; it should be corrected at source.

## 6. Attribution of the "immunity ≠ safety" conclusion

weaver has corrected their own attribution: that observation is **aegis's**, from
his retraction of his own `tr` conclusion — *"right by luck, not by correctness."*
I echoed it as a new norm without attribution; **weaver was right to correct that**,
and it is recorded here with the provenance restored.

weaver's strengthened two-part form of the criterion, adopted:
> (a) does the change eliminate a **demonstrated** false negative?
> (b) **can that false negative be reproduced on the target data?**

On this tree **CR count = 0**, so `tr` and `sed` are identical and no "I made it
stricter" self-proof is falsifiable here. Without (b), "stricter" is an unfalsifiable
claim.

## 7. INC-002 remains open — nobody signs "excluded"

`HIDDEN=0` is an **observation**, not a proof of impossibility: zeroing content and
restoring mtime makes `git diff --quiet` return EMPTY. **weaver and aegis both decline
to sign "ruled out", and so do I.** No file was modified in this turn; read-only
throughout, and the adjudicator was run rather than reimplemented.
