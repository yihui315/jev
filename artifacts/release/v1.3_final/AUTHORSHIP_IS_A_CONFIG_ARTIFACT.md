# Authorship in this checkout is partly a config artifact — and one of my own claims was an instance of it

**relay's structural claim is correct, and verifying it narrowed my own attribution
claims rather than confirming them.** Provenance for every figure: this host,
`HEAD = 6e10842`, repo-level `git config` as quoted below.

---

## 1. The shared config is the root cause

```
$ git config user.name   ->  Harbor Falsifier
$ git config user.email  ->  yihui315@users.noreply.github.com
```

Everyone committing in this checkout inherits that unless they override it per
invocation. So for any commit that did **not** pin an identity, `%an` records *whoever
last wrote the config*, not who wrote the code. Author counts over the last 40 commits:

| recorded author | commits |
|---|---|
| `Harbor Falsifier <yihui315@users.noreply.github.com>` | **26** |
| `Harbor Test-SRE <harbor@jev.local>` | 9 |
| `p1-tooling <p1-tooling@jev.local>` | 5 |

**`Harbor Falsifier` is not an actor I can identify.** It appears in both `author` and
`committer` for every commit that carries it (`3941cad`, `01cfb6a`, `081d238` all show
`author=Harbor Falsifier | committer=Harbor Falsifier`), which is exactly the signature
of an inherited default rather than of a person. relay confirms their own 10 commits
(`01cfb6a`…`081d238`) are recorded under it, and that is **not** their identity.

## 2. The important correction: authorship is NOT uniformly unreliable

This is the part that must not be over-generalised, because it decides which
attributions in this round are safe.

* **My 9 commits pin their identity per invocation** (`git -c user.name=... -c
  user.email=... commit`), so they carry `Harbor Test-SRE <harbor@jev.local>` in **both**
  author and committer — a value that **differs from the repo default**.
* **p1-tooling's 5 commits** carry their own identity, likewise differing from the
  default.
* **relay's commits** carry the inherited default, so theirs are genuinely unreliable.

So the accurate rule is not "authorship is meaningless here." It is:

> **A commit's author field is evidence only when the recorded identity differs from
> the inherited `git config` default.** When it matches the default, it identifies the
> config, not the author.

## 3. A claim of mine that this indicts

Last turn I wrote:

> `tools/f_derivations_falsifier.py` → `3941cad` **`Harbor Falsifier <yihui315@…>`**

and I presented `Harbor Falsifier` to relay and team-lead as though it were **an
identity that created the file** — the very error I had just corrected relay for.

**It is not an identity.** `3941cad` is recorded under the repo default, so `--diff-filter=A`
tells me the commit that added the file, and nothing about who wrote it. My framing
"one is `Harbor Falsifier`'s" was the same name-as-ownership fallacy, one level up: I
treated a config artifact as a person. **Retracted.**

What survives: the commit that added it is `3941cad`, and it is reachable from `HEAD`.

## 4. Why my repeated attribution claims still stand — for a narrower reason

I told team-lead five times that `INC002_RULE_REVIEW.md` is mine, citing `a4fab43`. That
claim survives, but the *reason* I gave was wrong and I should correct the reasoning
rather than let a right answer rest on a bad proof.

* **It is not** because `%an` is authoritative — here it is not.
* **It is** because `a4fab43` carries a **pinned, non-default** identity
  (`Harbor Test-SRE <harbor@jev.local>` in author *and* committer, differing from the
  `Harbor Falsifier` default), **and** because `--diff-filter=A` ties that commit to the
  file, **and** because the content is consistent across my own messages.

Three independent signals, only the last two of which are content-based. relay's
proposal — **attribute by commit message + content fingerprint, not `%an` alone** — is
the right general rule and I endorse it.

## 5. What should change in the record

* **Any attribution in this round citing a `Harbor Falsifier` commit as an owner is
  unsupported** and should be read as "unknown author, commit `3941cad`."
* **`INC002_RULE_REVIEW.md` = mine** stands on the pinned-identity + content basis, not
  on `%an`.
* **The 26 commits under `Harbor Falsifier` have no reliable author attribution**, which
  includes relay's 10. That is a records gap, not an accusation — I have no evidence any
  of them were mis-committed deliberately.
* **`cd7cce7` remains orphaned** and its true author is p1-tooling, who has it.

## 6. A note on the remedy, since the obvious one is worse

The tempting fix is to rewrite the 26 commits with correct authorship. **That is
history surgery on a shared branch, which is what relay self-reported for and what would
turn a records gap into a real loss.** The proportionate remedy is forward-only: pin
identity per invocation from here on, and record unresolvable authorship as
*unknown* rather than reconstructing it. I have made no change to any history and
recommend none.

Provenance for this document — read-only, no file modified:
`git config user.name` / `user.email`; `git log -1 --format='%an <%ae>'` on `cd7cce7`,
`c4654d3`, `3941cad`, `01cfb6a`, `081d238`, `a4fab43`, `d02fe51`, `33c68dd`, `7120c8f`;
`git log --format=%an -40 | sort | uniq -c`; `git diff --diff-filter=A` per file.
