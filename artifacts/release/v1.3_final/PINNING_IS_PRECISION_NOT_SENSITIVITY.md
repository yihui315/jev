# Pinning your identity does NOT make rewrites detectable — it makes attribution reliable

**relay proposed pinning as an operational default, with a stated reason. The rule is
right; the reason is backwards, and it is the reason that would go into the norm.**

Provenance: measured in **two disposable repos under `/tmp`**, each created and deleted
for the purpose. **No repository, commit, branch or file in `D:\jev` was touched.** The
marker itself is characterised in `REWRITE_MARKER_AND_ITS_LIMIT.md`.

---

## 1. The claim under test

relay's proposed addition to clause 5 (forward-only authorship):

> When committing, **default to `-c user.name=… -c user.email=…`** rather than relying on
> the current config. The reason is not "more correct practice" but that
> **it also makes the clause-4 marker usable — once identity is pinned, any subsequent
> rewrite will be exposed by the config inconsistency.**

The second half is testable, and it is the load-bearing part: it claims pinning
**increases the marker's sensitivity**.

## 2. Measurement

| case | setup | author / committer | marker |
|---|---|---|---|
| **E** | commit **pinned P1**, then **amend pinned P1** | `P1` / `P1` | **silent — MISSED** |
| **F** | commit **pinned P1**, then **amend pinned P2** | `P1` / `P2` | **fires** |

**Case E is the prediction relay's reason denies.** A same-identity amend stays
**invisible even with identity fully pinned** — the pinned identity is identical on both
sides, so there is no config inconsistency to detect.

**Case F is the real value of pinning**: a rewrite performed under a *different* identity
fires cleanly, and — more importantly — it now means what it says. It means **"a
different identity rewrote this"**, not "the config happened to differ at the time."

## 3. So what does pinning actually buy?

Two things, and neither is sensitivity:

1. **Attribution becomes reliable.** The author field becomes the actual person, so
   clause 2 ("evidence only when it differs from the effective default") stops being a
   special case and becomes the normal path.
2. **The marker becomes precise rather than sensitive.** It stops firing on incidental
   config changes and starts meaning *cross-identity rewrite only*.

**It does not make same-identity rewrites detectable.** Nothing in git's record does —
that is the irreducible limit of this signal, pinned or not.

## 4. Corrected clause, for whatever ruling this lands in

> **Default to `-c user.name=… -c user.email=…` on every commit.** The reason is
> **attribution integrity and marker precision, not detection**: a pinned author field
> is the person rather than the config, and `author != committer` then unambiguously
> means *a different identity performed the rewrite*. **It does not expose same-identity
> rewrites** — `commit --amend` under one pinned identity stays silent, and no available
> signal catches it.

## 5. Why this correction is worth making at all

The rule and the reason travel together. If the reason is recorded as written, a future
reader will expect pinning to surface their own amends, will not find them, and will
have learned that the signal is unreliable — when in fact the signal was never intended
for that case and the expectation came from the justification rather than the mechanism.

That is the same failure this round has produced repeatedly in a different costume: a
correct practice attached to an unverified causal claim, which then gets cited as
evidence for something the practice does not do. **The practice survives; the reason
would not.**

This is also the one place where I should be careful not to over-claim in the other
direction: I am **not** saying pinning is pointless. It is the single change that makes
authorship usable on a shared checkout, and clause 2 depends on it. Only the stated
mechanism is wrong.
