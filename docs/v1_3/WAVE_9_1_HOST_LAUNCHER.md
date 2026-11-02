# JCC v1.3.1 — WAVE 9.1: THE HOST LAUNCHER

> Branch: `release/jcc-v1.3-final` (opened from the RC anchor `e836636`).
> The RC anchors `c3060f7` / `e836636` / tag `jcc-v1.3-product-local` are **kept
> unchanged** as history. Nothing in this Wave edits them, and nothing in this
> Wave claims the RC freeze covers the new code.

## 1. The hole this Wave closes

Wave 3 shipped a **bridge**, not a launcher. `workbuddy_launch` reads
`~/.workbuddy/sessions/<pid>.json` — a session the **operator already started** —
and hands that record to the adapter. `POST /api/v1/domain/missions/{id}/launch`
calls exactly that, and `server.py` carried the comment:

> `manual_launch_required: WorkBuddy is the host IDE/CLI; the UI …`

Under the overnight order's own taxonomy that capability is
`OBSERVED_EXISTING_LIVE_SESSION`. It is honest work and it is **not** a launch:
nothing the product did caused the work to exist.

**That comment is now falsified by measurement**, so it must not stay.

## 2. What was measured on this host (ground truth, not assumption)

WorkBuddy ships a first-class headless CLI:

| Fact | Value (measured) |
|---|---|
| Entry point | `F:\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy` |
| Product name / version | `CodeBuddy Code` / **2.147.0** (`--version` exit 0) |
| Non-interactive | `-p, --print` |
| **Native event stream** | `--output-format stream-json` (NDJSON, one JSON object per line) |
| Identity | `--session-id <uuid>` (echoed back in every event) |
| Root binding | `--add-dir <dir>`, plus process cwd |
| Capability narrowing | `--tools`, `--allowedTools`, `--disallowedTools`, `--permission-mode` |
| Server mode | `--serve` (REST + ACP over SSE), `--port`, `--host`, `--auth` |
| Process ownership helper | `bin/windows-job.cjs` (Windows Job Object) |

The **real** invocation of a WorkBuddy session, read out of a live process's PEB
(read-only), is:

```
WorkBuddy.exe <cli>/bin/codebuddy --serve --session-id <uuid> \
    --permission-mode fullAccess --tools … --model … --port 0
```

⚠️ **That command line contains live MCP bearer tokens and a signed MCP context
JWT.** It is never copied into a log, an artifact, this document, or a commit.
See §6.

Every session also exposes a loopback **CodeBuddy Gateway**
(`POST /api/v1/runs`, `GET /api/v1/runs/:id/stream`, `GET/POST /api/v1/acp`,
`/api/v1/health|status`); without a token those answer **401**.

## 3. The three-environment measurement — and what it proves

`launch_probe.py` starts the real CLI headless and asserts over the child's own
bytes. The **difference between the modes is the measurement**:

| Mode | What is passed | Result |
|---|---|---|
| `minimal` | node + config-dir vars only | **exit 0 in 7.4 s, 6 well-formed NDJSON events** (`system` / `file-history-snapshot` / `assistant` / `result`), our `--session-id` echoed back — and the model refuses: `Authentication required`. Reproducible (two runs). |
| `inherit` | the caller's whole environment | **150 s timeout, stdout 0 bytes.** Inheriting the environment is *not* a path to a working headless launch. |
| `app-dirs` | `minimal` + all secret-free `WORKBUDDY_*` / `CODEBUDDY_*` vars | blocked by the sandbox (`reg.exe` on the Program Blacklist) — **inconclusive**, and recorded as such rather than folded into either answer. |

**Conclusion, stated as narrowly as the evidence allows:** the launch mechanism is
real and the process/event contract works; a launcher-spawned CLI **cannot
authenticate** in the contexts measured here. Therefore
`JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION` **must not be claimed**, and neither must
`PROJECT_COMPLETE=true`.

## 4. Design — `packages/jcc_core/host_launcher.py`

The launcher is the only place in JCC that starts a workload. Four properties are
structural, not stylistic:

1. **The goal is never a command.** It is appended as **one** argv element to a
   template whose every other element is a build constant; `shell=False`
   everywhere. There is no quoting rule to get right because there is no joined
   string. A test feeds the goal `x"; calc.exe; echo "` and asserts it stays a
   single slot.
2. **Nothing is built from caller input.** The executable comes from a named
   resolver (`SCRIPT_RESOLVERS["workbuddy_cli"]`) over the host environment; the
   cwd is the mission's own project root; the template comes from the allowlisted
   registry. `build_head()` returns a *machine-readable problem code* rather than
   a silent `None`.
3. **Capability is separate from actuation.** `USER_INITIATED_MISSION_LAUNCH` is
   what a human's click confers. It is a different name from anything the
   autonomous controller uses, and the module holds no actuator reference: it
   cannot enable `actuation`.
4. **It refuses to upgrade.** `classify_stream()` will not turn a structurally
   perfect stream into a launch. A stream whose model turn was refused is
   `AUTH_REQUIRED`; a stream with no model turn is `STARTED_NO_TELEMETRY`.

The nine checks are the order frozen in `signing.py` (cheap integrity before
context) and reuse `verify_directive` for checks 1–4, so the integrity semantics
are literally the same code as the effect boundary. Five more are the launcher's:
mission binding, capability, policy, budget, human approval. Every refusal returns
a reason from that module's closed vocabulary and **spawns nothing**.

### Process ownership

`JobGuard` wraps the child in a Windows Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, which is why the CLI ships
`bin/windows-job.cjs`: it is the platform's supported mechanism. Availability is
**reported** (`job_object: assigned` or `unavailable:<reason>`), never assumed —
losing ownership silently is exactly the failure this project bans.

### stdout is a file, not a pipe

The first probe used `subprocess.run(capture_output=True, timeout=180)` and **hung
past its own timeout** producing nothing: the CLI starts helper children, a
grandchild inherits the pipe write end, and `communicate()` never sees EOF. This
is why the launcher writes to real files and passes `stdin=DEVNULL`.

## 5. Tests — `packages/jcc_core/tests/test_host_launcher_v1_3.py`

**28 passed in 0.80 s.** Each property above is pinned by a test with a named
falsifier, including the nine refusal classes (parametrised) asserted to leave
`pid is None` and never reach `LAUNCH_ACCEPTED`.

## 6. Security finding (AEGIS)

**A parent WorkBuddy agent command line carries live secrets.** Recording a
launch by copying the parent's argv or environment would write MCP bearer tokens
and a signed context JWT into the audit tape. Rules adopted:

* the launcher never reads, copies or logs a parent command line or environment;
* child environments are constructed by allowlist, never inherited wholesale;
* `env_names.py` (evidence tooling) prints variable **names** with values
  redacted, and that discipline is stated in its docstring;
* this document and the tests contain no token material.

## 7. Honest scope — what this Wave does NOT claim

* **No real WorkBuddy mission was launched end to end.** The credential boundary
  was not crossed (§3).
* **The HTTP surface is untouched.** `POST /api/v1/domain/missions/{id}/launch`
  still does what it did in Wave 3. Wiring the launcher into the API requires
  editing `server.py`, which is one of the 12 A8-pinned production artifacts, and
  a route change additionally requires four coordinated reviewed edits **plus**
  re-emitting `V1_1_SECURITY_GATE.json`. Doing that without the coordinated
  re-baseline would break the pin gate, so it is deliberately **not** done in the
  same step as the library work.
* **No new `PRODUCT_FREEZE_SHA`.** This Wave adds a module and a test file; the
  A8 pins are untouched (the security gate re-ran `GO high=0 medium=0 low=0
  ran=9/9` with `[check] OK: the committed artifact matches`).
* **No UI change.** The Mission Studio does not yet offer a host launch.

## 8. Remaining blocker (single, and not a normal engineering task)

```
BLOCKER  : a launcher-spawned WorkBuddy CLI cannot authenticate
WHY      : the CLI's stored login is not reachable from the contexts measured
           (minimal env -> "Authentication required"; inherited env -> no output)
EVIDENCE : .workbuddy/scratch/overnight/probe_min2.txt  (exit 0, 6 events, AUTH_REQUIRED)
           .workbuddy/scratch/overnight/probe_inherit2.txt (timed_out, 0 events)
IMPACT   : JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION cannot be claimed;
           PROJECT_COMPLETE=false
MORNING  : either (a) sign the CLI in for the account that will run the JCC API
           service, or (b) confirm the supported way for a non-app process to
           obtain the CLI's credential. Then the launcher can be wired to the API
           and the loop closed.
```

## 9. Files

```
packages/jcc_core/host_launcher.py                 new — the launcher
packages/jcc_core/tests/test_host_launcher_v1_3.py new — 28 tests
docs/v1_3/WAVE_9_1_HOST_LAUNCHER.md                new — this document
```

Evidence tooling (untracked by design, under `.workbuddy/scratch/overnight/`):
`win_proc_info.py`, `cmdline_probe2.py`, `asar_reader.py`, `launch_probe.py`,
`env_names.py`, `hl_smoke.py`.

## 10. Rollback

`git checkout <rollback tag>` or drop the three files above. The RC anchors and
every earlier tag are untouched by this Wave.
