# MORNING STATUS — JCC v1.1 verification line

Branch `feature/jcc-v1.1-activation` · HEAD `e0713f1821fd57a6266fd6a3613e519057dde937` · tree clean
Written for the owner, to be read before touching anything.

## TL;DR — the system is up and you can test it now

| check | command | result |
|---|---|---|
| runtime is up | `powershell -File scripts\status.ps1` | **UP**, pid 7588, port 4778 (loopback only) |
| host is healthy | `powershell -File scripts\doctor.ps1` | **9/9 PASS** |
| API answers | `Invoke-RestMethod http://127.0.0.1:4778/api/health` | **HTTP 200**, `status=ok` |

Live health currently reads:

```
status=ok  jev_provider=mock  wal=True  actuation_enabled=False
policy_version=p3.0.0  localhost_only=True
```

That is the **frozen v1.0.1 runtime**, deliberately served from an immutable
snapshot so that v1.1 development could not corrupt it. Its `/api/health`
answers **7 keys**. The v1.1 build answers **12** — that difference is the
single fact that proves which build you are talking to.

## Things you can try right now

```powershell
# is it up, and what does it think about itself
powershell -File F:\jev\scripts\status.ps1
powershell -File F:\jev\scripts\doctor.ps1

# the read-only surface (no token needed)
Invoke-RestMethod http://127.0.0.1:4778/api/health
Invoke-RestMethod http://127.0.0.1:4778/api/sessions
Invoke-RestMethod http://127.0.0.1:4778/api/audit
Invoke-RestMethod http://127.0.0.1:4778/api/decisions
Invoke-RestMethod http://127.0.0.1:4778/api/audit/verify      # hash chain self-check
Invoke-RestMethod http://127.0.0.1:4778/api/sessions/nope     # -> 404, never 500

# mutating routes are deliberately locked (401 = no token configured, fail-closed)
Invoke-WebRequest -Method POST http://127.0.0.1:4778/api/steer -Body '{}' `
  -ContentType application/json        # -> 401, by design
```

Nothing to install, nothing to start: three scheduled tasks keep this alive, and
they proved it overnight (see below).

## What was verified since the last report

| item | outcome |
|---|---|
| `pytest tools/tests` | **226 passed**, two consecutive runs → **byte-identical** verdict artifact |
| §21 Security gate re-run at HEAD | **GO**, high=0, 9/9 checks ran |
| §22 Reliability gate (new) | **GO, 19/19** against an isolated BLUE on 4779 |
| §24 Control-UI smoke | 8/8 screens fetched over real HTTP |
| Reboot recovery | exercised 6 times by real crashes — see below |

### The host crashed six times, and that is the best result of the night

Between 18:24 yesterday and 06:39 today the machine restarted uncleanly
(Kernel-Power 41) at **18:23:51, 21:20:06, 21:27:20, 00:06:04, 06:34:34**, plus a
SYSTEM-initiated shutdown at **06:35:23**. None of those were caused by JCC.

Every single time, the scheduled tasks came back on their own and restored the
frozen runtime on 4778 without a human. The restore is *fail-closed*: it refuses
to certify its own result unless the live service proves it is the snapshot, and
it never signals a process it cannot positively identify. Five separate
`RECOVERED` events are in `runtime/logs/stable-runtime-watchdog.log`, each
`frozen-runtime proof: PROVEN`.

**Expect this to happen during your testing too.** If 4778 stops answering, wait
about two minutes — `JEV-JCC-Stable-Health` will put it back. If it does not
come back, the logs above are where the truth is.

## What is genuinely NOT done

`PROJECT_COMPLETE = false`, and it is false for reasons that are correct rather
than cosmetic. Six checks are red:

| check | why it is red | honest assessment |
|---|---|---|
| **E-05 / E-06 / E-07** | The architecture, security and SRE legs were signed at the release freeze `5ab5e20d`. Since then **37 product paths changed** — because that is what v1.1 work did. | The gate is right. A signature left behind by v1.0.1 does not describe what would ship today. Fixing this means genuinely re-verifying all three legs against HEAD, not editing anything. |
| **F-04 / F-05** | Derived directly from E-05/06/07 (3 blocking items, 3 unresolved HIGH). | They fall automatically once the legs are re-signed. |
| **P-02** | `manifest.json` disagrees with the **A8 hash pin** for `packages/jcc_core/core.py` and `packages/jcc_core/server.py`. | Real and deliberate: v1.1 extends `/api/health` from 7 to 12 keys, so `server.py` cannot equal its v1.0.1 hash. A8 pins exist to make exactly this visible. |

### One decision is yours, and I am not making it for you

A8 pins are this project's tamper-evidence anchor. The rule says an intentional
change is recorded as an explicit **old → new** re-baseline in a freeze commit,
ledgered and reviewable. Re-baselining is the correct move *for a v1.1 release*,
but it moves the anchor that everything else is verified against, so it is an
owner decision — not something to do quietly in order to turn a gate green.

Either way, nothing is broken and the runtime you are testing is unaffected.

## Files worth reading

- `artifacts/release/V1_1_RELIABILITY_REPORT.md` — the 19 scenarios and their evidence
- `artifacts/release/V1_1_SECURITY_GATE.json` — security GO at this HEAD
- `runtime/logs/stable-runtime-watchdog.log` — every tick, including all five recoveries
- `runtime/recovery/` — every rotated/archived file with before-and-after hashes (nothing was deleted)

**Reproduce anything:** HEAD `e0713f1` reproduces all of the above.
No push, no remote merge, no public deploy was performed, and 4778 is bound to
loopback only.
