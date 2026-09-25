# JCC Operator / WorkBuddy orchestration policy — worker leases and replacement

Status: policy. Adopted 2026-09-25 after the `forge-4` stall (`LEASE_EXPIRED_NO_DURABLE_PROGRESS`),
in which a worker held `scripts/` ownership for more than two hours and produced no durable
progress, while the platform's single remaining blocker stayed red.

This document defines what happens when a worker stops reporting. It is not a description of what
happened; it is the rule that followed from it.

---

## 1. Constants

| Constant | Value | Meaning |
|---|---|---|
| `WORKER_LEASE_MINUTES` | 20 | A lease on a path set is time-boxed, not a lock. |
| `HEARTBEAT_INTERVAL_MINUTES` | 5 | Maximum gap between durable signals from an owning worker. |
| `NO_DURABLE_PROGRESS_MINUTES` | 15 | Gap at which the worker is treated as STALLED, before revocation. |
| `MAX_ACTIVE_WORKERS` | 4 | Concurrency ceiling across the whole programme. |
| `REPLACEMENT_AFTER_MINUTES` | 20 | When the sole blocking owner has produced nothing for this long, replacement is dispatched in the same execution cycle — not the next one. |

## 2. Ownership is a lease, not a lock

A worker owns a path set only for the duration of a live lease. Ownership is acquired by dispatch
and is lost on revocation, and revocation does not require the worker's consent, its reply, or its
agreement that it was stuck.

Consequences that follow from that sentence:

- A worker that goes quiet is not in a WAIT state. It is in one of the states of section 3.
- The lead does not end an execution cycle on the sentence "waiting for `forge-x` to report". When
  the sole blocking owner has exceeded `REPLACEMENT_AFTER_MINUTES`, the replacement is dispatched
  within the same cycle.
- Chat output is not durable progress. A durable signal is a commit, a test-result file, a
  heartbeat state update, or a failing reproduction artifact — something on disk that someone else
  can read without asking the worker.

## 3. State machine

```
RUNNING -> STALLED            (no durable signal for NO_DURABLE_PROGRESS_MINUTES)
STALLED -> LEASE_EXPIRED      (revoked; written to jcc_task_history.jsonl)
LEASE_EXPIRED -> REPLACED     (replacement dispatched and owns the paths)
REPLACED -> CONTINUE          (the blocker the programme was stuck on is now owned elsewhere)
```

Any other transition requires an explicit ruling by the lead, recorded like everything else.

## 4. Stale workers

A worker whose lease has expired does not regain ownership by reporting late.

- Its output is `STALE_WORKER_EVIDENCE`: not merged, not committed, not signed.
- The new owner compares it against the current tree and states in writing whether any part of it is
  usable. Until that comparison exists, none of it counts, including output that looks correct.
- Re-authorisation of a stale worker is an explicit act by the lead. It does not happen implicitly
  because the worker answered a question.

## 5. Single-writer rules for signed artifacts

A signature file has exactly one writer at a time. Where two signers have contributed to one artifact
the co-author is recorded as a reader in a `cosigners` list, never as a second writer.

- The signer writes. The verifier reads and reports, and may contradict it.
- A contradiction block is not resolved by splitting the difference; the file stays unsigned until
  the measurement that contradicts it is explained.
- Corrections to a signed file are recorded in place (`amendments` / `provenance`), together with
  what was changed, why, what stayed the same, and when. An unrecorded correction is a defect even
  when its content is right — that is how this project first found itself needing this section.
- Timestamps in a signature carry meaning: a signing time and a file-write time are different facts,
  and a field that holds one while claiming to hold the other is a false record.

## 6. Evidence discipline that this policy assumes

- Untracked files make the tree dirty and therefore make an authoritative gate run invalid. Scratch
  goes under `.workbuddy/scratch/`, never into a tracked tree.
- An instrument's return value is never the evidence; the evidence is the ground truth it was taken
  from. Where a harness cannot observe something, the correct response is to make it able to
  observe, not to record the gap as a pass.
- A verdict is never negotiated towards a desired colour. It changes only when the measurement
  changes, and every entry states which mechanism it actually tested.
