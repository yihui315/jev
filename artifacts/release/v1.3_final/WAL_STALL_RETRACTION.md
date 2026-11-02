# RETRACTION: the 63 s WAL stall is a HOST ARTEFACT, not a product defect

**Status: this retracts my own BLOCKER-grade claim from ~40 minutes ago. The 63 s
stall is real and measured, but its cause is the host, and the product is fine.**

Supersedes the "two 30 s WAL stalls = 63 s" mechanism in
`P2_HEALTH_FAILURE_ROOT_CAUSE.md` §1 and the "two WAL databases" claim in §3. Those
were arithmetic on two numbers that were never causally linked.

---

## 1. The retraction, in one measurement

Same HEAD (`9108ade`), same command, same unmodified 30 s health budget, `packages/`
clean throughout:

```
20:20 - 20:24   1 failed in 90.96s     (4 consecutive runs)
21:04           1 PASSED in 14.74s     exit 0
```

No code changed. The test did not get faster because anything was fixed — the host
stopped being slow.

## 2. What I got wrong, precisely

I wrote "**63.7 s ≈ 2 × 30 s**, and the two arms are identical to within 0.01 s", and
built the BLOCKER on it. That inference was wrong in two independent ways, and both
are now measured:

**(a) It is ONE stall per process, not two.** `wal_stall_shape.py`, 5 WAL databases in
one process under `synchronous=NORMAL`:

```
close times: 30.13, 0.13, 0.16, 0.12, 0.12     total 30.66s
```

Only the first is slow. So two databases in one process cost ~30 s, not ~60 s. The
"two WAL databases → two stalls" mechanism I asserted cannot be the explanation.

**(b) The stall is not a fixed cost, and it is not fsync.** Two fresh processes:

```
process 1: first close 36.07s
process 2: first close 57.78s
```

Not 30 s. Not equal. Variable by ~2x. A deterministic property of `synchronous=NORMAL`
would not vary like that.

## 3. The `synchronous` pragma is NOT the lever (and I was about to hand over a wrong fix)

The table team-lead cited as "slow fsync" points the other way:

| | first close | subsequent |
|---|---|---|
| `synchronous=NORMAL` | 30.13 s | 0.12 s |
| `synchronous=FULL` | 0.09 s | 0.10 s |

`FULL` fsyncs on **every commit**; `NORMAL` does not. If the cost were fsync, `FULL`
would be the slow one. It is 300x faster. **The fsync reading is backwards**, so
"raise synchronous to fix it" is not a fix.

And the missing pragma I flagged is harmless: SQLite 3.53.1's default is
`synchronous=2` (FULL), so `connections.py:179` — which sets `journal_mode=WAL` and no
`synchronous` — already gets the fast configuration. Measured:

| arm | closes | total |
|---|---|---|
| both NORMAL | 30.14, 0.12 | 30.26 s |
| NORMAL, then default | 0.17, 0.10 | 0.27 s |
| both FULL | 0.09, 0.10 | 0.19 s |

## 4. What the stall actually is

**Host I/O contention, transient and load-dependent.** Measured, in this order:

* 4 fresh processes, NORMAL then FULL then NORMAL then FULL: **0.14, 0.13, 0.18,
  0.23 s.** The stall is *gone*, for NORMAL as well as FULL.
* The stall appeared while **6 python + 4–8 node processes** were running (other
  teammates' legs, per team-lead's "only Harbor is measuring" window).
* The 63.73 / 63.74 s warm-up measurement was taken under that same load; the
  conclusion I drew from it ("warm-up does not help") is still true but was measured
  in a regime where everything cost 30 s, so it cannot separate "warm-up does not
  help" from "everything was slow then".

The most plausible mechanism is first-touch of a WAL/`-shm` file under contention —
AV or a filter driver serialising on new file creation — which would explain a large,
variable, one-time-per-process cost that vanishes when the host is quiet and that
`FULL` avoids only because FULL's first close touches less. **I cannot prove the
mechanism**, and I am not going to name a vendor I have no evidence for. Label:
**HOST, cause UNVERIFIED, magnitude measured.**

## 5. Why this matters for the ruling

team-lead classified this as **"不是环境限制，是产品问题"**, elevated it above CR-H2,
and authorised forge2 to fix (2) pre-warming both databases. **That authorisation
rests on a mechanism I have now retracted, and (2) cannot work anyway**: pre-warming in
the parent measured 63.73 s vs 63.74 s, and pre-warming *inside* the child would not
help either, because the cost is per-process and the child pays it once either way.

**The correct disposition is the opposite of the ruling: there is no product defect
here to fix.** The test's 30 s budget is adequate — proven by it passing in 14.74 s
unmodified on the same commit that failed it 4 times an hour earlier.

## 6. What would actually make this a product concern

Not the pragma. Something measurable, and I would want it before raising it again:
* does the stall reproduce on a **quiet** host at all? Today: no — NORMAL was 0.14 s.
* is a **real deployment** ever the busy host? If a production host runs several JCC
  components, first-touch contention is plausible there and the 30 s budget would be
  thin. I have no evidence either way and did not test it.

Until one of those is answered, the honest status is: **a host artefact that made a
test look like a product bug for about an hour.**

## 7. The methodological point, which is the real cost

I had already told team-lead that my numbers were "each independently reproducible".
That claim was about the *individual* numbers, and it was true — every figure in
`wal_stall_shape.json` and `wal_second_stall.json` reproduces exactly. **What did not
reproduce was the conclusion I built on top of them**, because I compared two
measurements taken under different host load and read the difference as causal:
63.7 s (loaded) versus 30.1 s (quiet) is not evidence of two stalls; it is evidence
that the host was busy for one of them.

The guard that would have caught it: measure the mechanism and its components **in
the same window**. The two 30 s numbers in my table were taken ~40 minutes apart, and
a 2x variable cost is exactly the regime where that mistake is available.

Artifacts: `wal_stall_shape.json`, `wal_second_stall.json`, `p2_recheck_stall_gone.txt`
(`1 passed in 14.74s`, exit 0), plus `wal_stall_shape.py` / `wal_second_stall.py`.
