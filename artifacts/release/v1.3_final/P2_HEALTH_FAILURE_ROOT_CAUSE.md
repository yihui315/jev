# Root cause: the p2 smoke test's server never becomes healthy in time

**Status: VERIFIED by measurement. This is NOT the CR-H2 `.decode()` line, and it is
NOT a proxy fault. Both of my earlier hypotheses were tested and refuted.**

`test_http_end_to_end_four_agents_distinct_no_secret_leak` fails with
`AssertionError: server did not become healthy` (`_wait_health`, line 374), at HEAD,
with no edit of mine, reproducibly — 4 runs, 90.95 s / 91.08 s / 91.09 s / 91.50 s.
It passed earlier today (1 passed in 9.68 s at ~18:00).

---

## 1. The actual cause: two ~30 s SQLite WAL close stalls, one per database

`StateStore._init_db()` blocks on the last statement,
`packages/jcc_core/state_store.py:389` → `conn.close()`. Measured by stepping through
`_init_db` one call at a time:

```
connect       0.00s
journal_mode  0.34s     (PRAGMA journal_mode=WAL)
other pragmas 0.00s
executescript 0.10s
ensure_column 0.00s
commit        0.00s
CLOSE        30.14s     <-- the whole cost is here
```

The pattern, measured:

| condition | `close()` |
|---|---|
| `journal_mode=WAL` | **30.2 s** |
| `journal_mode=DELETE` | 0.00 s |
| WAL, `synchronous=FULL` | 0.09 s |
| WAL, `synchronous=NORMAL` | 30.1 s |
| **first** WAL close in a process | **30.2 s** |
| each of the next 4 WAL closes, same process | 0.11 s each |
| 3 brand-new directories, sequential | 30.13 s, then 0.12 s, 0.15 s |

So it is **one 30 s stall on the first WAL close per process**, not a property of the
directory, the file, or the load. A fresh directory still pays it; a warm process does
not. That is consistent with a one-time initialisation on the WAL/shared-memory path
(filter driver or AV first-touch on `-wal`/`-shm`), not with anything in JCC's logic.

**And JCC opens TWO WAL databases, so the child pays it twice:**

* `run.py:418` — `StateStore(config.state_store_db_path)` → `jcc.db`, WAL
* `server.py:191` — `ConnectionRegistry(conn_db_path)` → `connections.db`, WAL
  (`connections.py:179` sets `PRAGMA journal_mode=WAL`)

Both land in the same directory, and the measured time-to-healthy reflects exactly two
stalls:

| arm | time to `/api/health` 200 |
|---|---|
| control (no warm-up) | **63.73 s** |
| warm-up paid in the parent process first | **63.74 s** |

63.7 s ≈ 2 × 30 s, and the two arms are identical to within 0.01 s.

**Why it makes the test fail, exactly:** `_wait_health(base, timeout=30.0)` gives the
server 30 s to answer. The server needs ~63 s to reach `uvicorn.run` on this host, so
the health deadline is short by more than half. The process gets far enough to write
`JCC-marker`, create both databases and log `p3.shadow disabled` — just not far enough
to bind the socket. The product is working; the budget is wrong for this host.

## 2. Two hypotheses I tested and REFUTED — recorded so they are not re-run

**Proxy (refuted).** `HTTP_PROXY=http://127.0.0.1:49863` is set in this environment and
`urllib.request.getproxies()` returns it, so bare `urlopen` *does* route loopback
through a proxy: a probe to the child's own port returned `HTTP Error 502: Bad Gateway`
from the proxy while the server was not yet listening. That looked like the cause and it
is a real hazard for this test on this host. But it is not what makes the test fail:
`urllib.request.proxy_bypass('127.0.0.1')` is `False`, yet an
`http.client.HTTPConnection` probe — which uses no urllib machinery and cannot be
proxied — **also** got `WinError 10061 connection refused`. The socket genuinely was not
bound. Evidence: `artifacts/release/v1.3_final/p2_proxy_proof.json`
(`"verdict": "NOT THE PROXY..."`, `server_actually_healthy: false`).

Note `no_proxy`/`NO_PROXY` are unavailable as a workaround here: the WorkBuddy shim
strips them (measured `None` even when explicitly exported).

**`.decode()` locale dependence (refuted as the cause; still fixed as hardening).**
`sys.flags.utf8_mode == 1` on this host (`PYTHONUTF8=1`), which forces UTF-8 for the
bare form, so `bytes.decode()` returned correct CJK under **both** `PYTHONUTF8=1` and
`PYTHONUTF8=0`. My non-vacuity check in `verify_cr_h2.py` therefore reported
`VOID — the fixture does not distinguish the two forms` and exited 1, which is the
script correctly refusing to certify a fix it could not demonstrate. The CR-H2 change is
real but it is **hardening, not a demonstrated fix**, and the originally reported
`'utf-8' codec can't decode byte 0xb2 in position 8` remains **UNVERIFIED** — it did not
reproduce and the offending byte was never located.

## 3. The fix — and the one I proposed first, REFUTED by my own measurement

**My first proposal was wrong and I withdrew it.** I suggested paying the stall once in
a session-scoped fixture so the server under test only covered "real" startup. I then
wrote `prove_p2_warmup_fix.py` to check that before recommending it, and it does not
work:

| arm | warm-up paid in parent | time to healthy | inside 30 s? |
|---|---|---|---|
| control | no | 63.73 s | no |
| warm-up | yes, 30.47 s | 63.74 s | **no** |

The two arms are identical to within 0.01 s. **The stall is per-PROCESS, and the server
runs in a child process, so warming the parent changes nothing.** The control correctly
failed and the fix arm correctly failed too, so the script printed
`FIX NOT VERIFIED: ... the recommendation must be withdrawn or revised, not softened.`
That is the script working; the recommendation is what changes.

**What actually follows from the measurement:**

The child needs ~63 s on this host and cannot be made to need less by anything the test
does from outside the process. So the options are:

1. **Raise `_wait_health`'s budget past the worst measured case**, as a named constant
   with the measurement recorded next to it — e.g. `_HEALTH_TIMEOUT_S = 120.0` plus a
   comment citing the measured 2 × 30 s two-database WAL stall. Honest, minimal, and it
   keeps the assertion meaningful. This is the fix I now recommend.
2. **Have the child itself pre-warm both databases before `main()` does any work** (a
   tiny module-level warm-up in `run.py`, or a `JCC_PREWARM_SQLITE` env flag the test
   sets). This removes the stall from startup rather than raising a threshold, and it is
   the better engineering answer — but it touches the **product entrypoint** to fix a
   **test-environment** problem, so it needs forge2's and team-lead's explicit
   agreement, not my unilateral change. I am not making it.
3. Leave it failing and treat the leg as environment-blocked. Defensible, but it hides a
   real 63 s startup on this host, and a startup that slow is itself worth knowing about.

**Both 1 and 2 are `packages/` changes and therefore outside my ownership. I have
applied neither.** Option 1 is a two-line change with a measurement attached; option 2
is the one I would argue for if the 63 s startup is considered a real product concern
rather than a host artefact.

**Ownership note:** this is a NEW finding, not on forge2's queue as far as I know. It
belongs to whoever owns `test_p2_integration_smoke.py` and `run.py`.

## 4. Option 1 VERIFIED by experiment (not asserted)

I temporarily raised only `_wait_health`'s budget from 30.0 to 120.0 — changing nothing
else — and ran the test:

```
1 passed, 1 warning in 162.00s (0:02:42)     exit=0
```

So the diagnosis is confirmed end to end: the product starts correctly, the secret-leak
assertion underneath is satisfiable, and the only thing that was failing was a health
budget too small for this host's two-database WAL warm-up. The 162 s also shows the
startup cost is well above the 63 s floor measured earlier, so 120 s is not a marginal
pass and I would not recommend it as a tight value.

**Two consequences worth being explicit about:**

1. The probe run **also executed line 399** — the CR-H2 `.decode("utf-8")` line — for the
   first time in this round, and the test passed. So the CR-H2 change does not break the
   test, which is the one thing I could previously not say about it.
2. **The CR-H2 change is currently NOT in the tree.** The `git checkout` that removed the
   temporary 120 s probe also removed my `.decode("utf-8")` edit, since both were in the
   same file and I reverted to HEAD. `packages/` is clean. CR-H2 therefore remains an
   OPEN, VERIFIED-AS-HARDENING-BUT-UNPROVEN-IN-TREE item, and re-applying it is a
   one-line change for whoever owns that file. I am not re-applying it unilaterally
   because the same file needs the `_wait_health` change and the two should land as one
   reviewed commit.

Full log: `artifacts/release/v1.3_final/p2_health120.txt`.

```bash
# the failure, reproduced 4x at HEAD
CODEBUDDY_SAFE_DELETE_ENABLED=0 PYTHONPATH=D:/jev/packages PYTHONIOENCODING=utf-8 \
  ./.venv/Scripts/python.exe -m pytest \
  "packages/jcc_core/tests/test_p2_integration_smoke.py::test_http_end_to_end_four_agents_distinct_no_secret_leak" \
  -q -p no:randomly --tb=line --basetemp "D:/jev/runtime/harbor_p2_re"
#   -> 1 failed, 90.96s : AssertionError: server did not become healthy   (x4)

# the 30s close, stepped
python -c "import sqlite3,time; c=sqlite3.connect(p); c.execute('PRAGMA journal_mode=WAL');
            c.execute('PRAGMA synchronous=NORMAL'); c.execute('CREATE TABLE t(x)'); c.commit();
            t=time.time(); c.close(); print(time.time()-t)"
#   -> 30.14s   first close in the process
#   ->  0.11s   every subsequent close
```

Artifacts: `p2_proxy_proof.json`, `p2_spawn_isolation.json`, `CR_H2_VERIFICATION.json`,
`p2_http_retest.txt`.
