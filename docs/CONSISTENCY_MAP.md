# Consistency Map — JCC v1.3 final

**Owner:** metric-verify (independent verifier)
**Measured at HEAD:** `2899bab` (branch `release/jcc-v1.3-final`)
**Regenerate with:** `D:\jev\.venv\Scripts\python.exe runtime\_verify_cr002\d2_consistency_map.py` (read-only; writes only `runtime/_verify_cr002/consistency_measured.json`)

## The rule this document exists to enforce

> **The same fact recorded in more than one place must declare how those places are aligned. Absent such a declaration, assume they will disagree — and check.**

The CR-002 episode is the worked example. `run.py`'s content hash lived in **three** places: the A8 test pin, `artifacts/release/manifest.json`, and the committed git blob. Only **one** of them — gate leg `P-02` in `artifacts/acceptance/final_gate_run.py` — actually cross-checked the other two. When `config.py` changed for the relocation re-baseline and later for CR-002, the A8 pin went stale, and **`manifest.json` silently went stale too, 84 commits back, with no gate and no re-baseline trigger pointing at it.** One fact, three records, one alignment check. That is not a safe ratio.

## Measured result

| Fact | Records | Alignment declared? | Cross-checked by | Live disagreements |
|---|---|---|---|---|
| Product source file hash | 3 | **YES** | `final_gate_run.py` leg `P-02` | **0** |
| GREEN protected port = 4778 | many | **NO** | none | **0** |
| Four-agent identity / capability status | 4 | **NO** | none known | **0** |
| Repo root `D:\jev`, no live `F:` literals | many | **NO** | none | **0** (42 prose-only) |

**Zero live disagreements at this HEAD.** That is a *measurement*, not a guarantee — and note the second column: **only 1 of 4 facts has a declared alignment mechanism.** The other three agree *right now*, with nothing enforcing that they continue to.

---

## Fact 1 — product source file content hash

**Records**
1. `packages/jcc_core/tests/test_p3_acceptance_gate.py` → `_BASELINE_HASHES`
2. `artifacts/release/manifest.json` → `entries[].sha256`, `hash_convention: "git_blob"`
3. `git cat-file blob HEAD:<path>` — the committed blob

**Alignment: DECLARED.** `artifacts/acceptance/final_gate_run.py` leg `P-02` ("release manifest vs A8 gate vs committed blobs") requires all three to agree and fails otherwise. This is the **only** real cross-check among the four facts.

**Measured:** 12 paths, **0 disagreements**. A8 pins equal HEAD blobs exactly.

**Historical record — do not skip this.** At `ac1ef4a` this fact was **broken**:
- `run.py` and `config.py` pins still held pre-CR-002 values → `test_A8_negative_control_enabled_without_allowlist` FAILED. Root cause was **not** "someone edited the pin": it was a legitimate relocation re-baseline that landed *before* CR-002 changed `config.py`, leaving the pin stale by one legitimate change.
- `manifest.json` was stale on **6 of 12** entries with `frozen_at=c2c8367`, **84 commits** behind, and **no re-baseline trigger existed for it at all**. Every stale value equalled the blob at `frozen_at` — the file was internally consistent and describing an old tree.

**Known deferred dependency:** the lead has deferred re-emitting `manifest.json` (decision #10) because `config/jcc.yaml` is inside the 12 and **CR-003 will change that file**. Re-emitting before CR-003 lands would guarantee an immediate second staleness. **When CR-003 lands, the manifest needs a re-emit in the same window as the pin re-baseline** — see `docs/REBASELINE_WINDOW_VS_STALE_PIN.md`.

**Generator warning:** `manifest.json` declares `generator: scripts/emit_release_evidence.py`. That is **not** in the `tools/emit_*_manifest.py` family (`emit_final_manifest.py`, `emit_v1_2_release_manifest.py`, `emit_v1_3_release_manifest.py`, `jcc_release_manifest.py`), which emit different artifacts. **Do not substitute one for the other.** Hand-editing the 6 stale entries is also wrong: it would leave `frozen_at` pointing at a commit whose blobs no longer match the recorded hashes — trading one inconsistency for a subtler one.

---

## Fact 2 — GREEN protected port is 4778

**Records:** `GREEN_PROTECTED_PORT` in `config.py`; `server.port` in `config/jcc.yaml`; the literal `4778` repeated across `scripts/`, `packages/`, `tools/`, `apps/`; the port in the A8 pin set via `config/jcc.yaml`; `runtime/jcc/jcc.ownership.json` at runtime.

**Alignment: NOT DECLARED.** There is no single authority that the other sites are checked against.

**Measured — all shipped configs self-consistent:**
| Config | Declares | Expected (instance) | Agrees |
|---|---|---|---|
| `config/jcc.yaml` (GREEN) | 4778 | 4778 | ✅ |
| `config/jcc-v1.3.yaml` (product) | 4779 | 4779 | ✅ |
| `config/jcc-v1.2.yaml` | 4779 | 4779 | ✅ |

**The live mechanism that *does* enforce the important part:** `run.check_green_port_ownership` refuses any **non-GREEN** instance asking for 4778, and identity is the **loaded config file**, never the port value. Verified: non-GREEN on 4778 → refused; GREEN on 4778 → allowed; 22-case port-spelling battery all refused; host-independence proven by stubbing `getaddrinfo`.

**Residual (LOW, documented not fixed):** on a volume with 8dot3 **enabled**, an NTFS short name (e.g. `JCC_LONG~1.YAM`) resolves to `config/jcc.yaml` for `open()` but `abspath`+`normcase` will not recognise it as GREEN → a *legitimate* GREEN start would be **refused**. That is fail-closed (safe direction) and unreachable on this host (8dot3 off). **Agreed treatment: a conditional falsifier case that skips when 8dot3 is off, so the hole surfaces on other machines instead of being papered over by documentation.**

---

## Fact 3 — four-agent identity and capability status

**Records:** `agents.capabilities` in `config/jcc-v1.3.yaml`; the same block in `config/jcc.yaml`; `artifacts/acceptance/adapter_capability/{workbuddy,claude,codex,hermes}.json`; `artifacts/release/V1_1_CAPABILITY_MATRIX.json`.

**Alignment: NOT DECLARED** — no known gate cross-checks these four against each other.

**Measured — 8 agent/config entries, 0 structural conflicts.** All four agents present in both configs.

**The stronger reason alignment is missing (independently measured on this host):** the `status` fields in config are **assertions, never read back from the live agents.** Verified:

| Check | Result |
|---|---|
| `~/.claude` | **ABSENT** |
| `~/.codex` | **EXISTS** |
| `~/.hermes` | **ABSENT** |
| `~/.workbuddy/settings.json` | exists, **`mentions jcc = False`**, and has **no `hooks` key at all** |

So `config/jcc-v1.3.yaml` asserts workbuddy/claude/hermes `CONNECTED` and codex `DEGRADED`, while on this host **two of the three "CONNECTED" agent directories do not exist and the fourth has no JCC hook installed.** The status is a hand-maintained claim with no verification path.

**Correction to the figure I was given.** I was told "three of the four agent directories do not exist". Measured: **two of three** — `~/.codex` exists. The stronger statement, which I verified directly, is the workbuddy one: there is **no `hooks` key at all**, so "JCC hook count = 0" understates it — no hooks of any kind are configured.

This is a better argument for "no declared alignment" than the tuple collision: it is not that the four agents look alike, it is that **the record is not connected to reality at all.**

**Honest limitation, stated because it matters for the release gate:** the four agents are **not** distinguishable by their `{status, aep, verified}` tuple — workbuddy and claude share `{CONNECTED, true, true}`, giving **2 distinct tuples across 4 agents**. They are distinguishable by **name + `config_path` + `version`**, which satisfies "four-agent identity can be distinguished". **Do not cite status as the distinguishing dimension**; it cannot carry that weight.

---

## Fact 4 — repo root is `D:\jev`; live `F:` drive literals

**Records:** every `F:/jev` or `F:\jev` literal in the tree.

**Alignment: NOT DECLARED.** No gate checks for dead drive roots in source.

**Status: NOT CONVERGED.** This fact previously read "0 live occurrences" — that measurement was **wrong through no fault of the instrument but because of its SCOPE**: the first pass globbed `packages/`, `tools/`, `integrations/`, `apps/` and never looked at `artifacts/`.

### The confirmed live defect

`artifacts/security/freeze_check.py` — **the only file whose breakage is caused by the `F:` literal**:

| Line | Literal | Kind |
|---|---|---|
| 40 | `ROOT = Path(r"F:/jev")` | module-level assignment |
| 51 | `_GATE_DIR = Path(r"F:/jev/artifacts/acceptance")` | module-level assignment, then `sys.path.insert` |

**Measured consequence — the file cannot run at all:**
```
$ python artifacts/security/freeze_check.py 2899bab
ModuleNotFoundError: No module named 'final_gate_run'
```
It injects the dead `F:\jev\artifacts\acceptance` into `sys.path`, then fails to import the gate module that lives on `D:`. `Path("F:/jev").exists()` → **False** on this host.

**Causation isolated, not assumed.** A scratch copy with **only** the two literals changed to `D:/jev` imports cleanly. That is a controlled single-variable change, so the `F:` literal is the **sole cause** — not a coincidence co-occurring with a broken import.

**Why it matters more than a stale docstring:** `tools/release_truth.py:282` lists this file in `GATE_CODE_PATHS` and classifies it **PRODUCT**, on the explicit ruling that it is *"可执行门禁代码"* — executable gate code. Archived security evidence (`artifacts/acceptance/final_security_go.json`) records it being run as a script: `F:/jev/.venv/Scripts/python.exe F:/jev/artifacts/security/freeze_check.py <sha> -> FREEZE CHECK: COMPLIANT (exit 0)`. **That invocation cannot succeed on this host today.** A freeze check that cannot run cannot fail closed — it simply is not a check.

**Note on classification vs. capability:** the ruling that this file is PRODUCT was **correct** — it is gate code, and admitting it as evidence would have opened a hole. But classification answers *"may this be treated as evidence?"*, not *"does this work?"*. Both were needed, and only the first was asked.

### Scope of the wider problem (measured, not extrapolated)

A repo-wide token pass (excluding scratch, backups, `__pycache__`, `.venv`) finds **442 live `F:` occurrences**, of which:

| Category | Count | Status |
|---|---|---|
| `artifacts/security/` + `artifacts/acceptance/` gate code (excl. snapshots/backups) | **10 files** | see below |
| `artifacts/probe/` scratch probes | 11 | scratch, not gate |
| `artifacts/security/snapshots/`, `artifacts/release/.../backup_*` | 10 | historical copies |

Of the 10 gate/security files, I imported each and measured whether the `F:` literal is what breaks it:

| File | Imports today | `F:` is the cause? |
|---|---|---|
| `artifacts/security/freeze_check.py` | **NO** — `ModuleNotFoundError` | **YES — sole cause** (path-only fix makes it import) |
| `artifacts/security/freeze_check_guarded.py` | yes | no (latent) |
| `artifacts/security/import_closure.py` | yes | no (latent) |
| `artifacts/security/probe_kd002.py` | yes | no (latent) |
| `artifacts/security/verify_security_leg.py` | yes | no (latent) |
| `artifacts/acceptance/lead_p1_verify.py` | yes | no (latent) |
| `artifacts/acceptance/_forge_p3_probe.py` | no — `TypeError: add_directive() missing 'origin'` | **no — different defect** (API drift) |
| `artifacts/acceptance/_forge_p3_serverprobe.py` | no — `SystemExit: 0` | no — different defect |
| `artifacts/acceptance/_lead_p2_harness_check.py` | no — `FileNotFoundError` | no — different defect |

**This is why attribution matters.** Three of the four broken files have a live `F:` literal, and a reader who stopped at "has a dead path" would have mis-filed all three. Each has an **independent** cause, and each will still be broken after the paths are fixed.

**Owner:** forge2 (`artifacts/` is his; the fix also needs coordination with the A8 pin re-baseline).

---

## Rule 0 — precedence: a delivered causal claim must point at a command I ran

> **Every causal assertion in a deliverable must point at a command I executed myself, with its output.** Quoting someone else's conclusion is **not** my evidence. Quoting is allowed, but it must be labelled *relayed, not independently re-measured*, and it **may not** be the basis of a PASS/FAIL.

This sits above every rule below because it was learned the expensive way. In one round I wrote first and verified five times; four were caught, and each failure mode was different:

| # | What I nearly shipped | What caught it |
|---|---|---|
| 1 | a false regression from a stubbed `getaddrinfo` that did not strip whitespace | re-measuring against the real function |
| 2 | "0 live `F:` literals", false for the repo | noticing the globs omitted `artifacts/` |
| 3 | a relayed claim (`jcc_emit.py` has a live `F:` literal) | one command |
| 4 | a harness that flagged plain CRLF as "WRONG" for **both** rules | holding the HEAD side fixed and re-running |
| 5 | a "live `tr -d` script" defect in a document already corrected | reading the prose instead of grepping a string |

None of the five would have survived a reader who trusted the first draft. **A check always costs less than a retraction someone has already acted on** — and in a team, someone else acts on it first.

**Corollary 1 — a control's construction must itself be validated.** When a rule "fails", first ask whether the input really is the shape you meant to test. I got this wrong three times in one round: a CR placed immediately before an LF (that is a *legal* CRLF, so every rule "failed" and I nearly reported that even the correct rule was unreliable); a CR inside an identifier (`ast.parse` fails immediately, so the damage is not subtle); and an injection that landed on a blank line rather than mid-line. **Until a control is shown to be valid, its result cannot support any conclusion — including "this rule is defective."**

**Corollary 2 — a correction is not evidence either.** When a teammate retracts a claim, the retraction gets run before it is accepted, exactly like the original. The `INC-002` rule has now been corrected, re-broadcast and re-verified **three times**; each round *narrowed* it (`tr -d '\r'` → `sed 's/\r$//'` → `replace(b"\r\n", b"\n")`) — and the middle step was then **retracted on its own merits**, see the sub-bullet below, so the sequence is not a monotone tightening. **The only form with no measured blind spot is `replace(b"\r\n", b"\n")`.** The cheapest way to break a claim independently is to redo it on a **different file at a different injection point** — that is how weaver's counter-example and mine came to agree without either of us relaying the other.

## Drift rules (the actionable part)

1. **Changing a file named in Fact 1 invalidates two records, not one.** Re-baseline the A8 pin **and** re-emit `manifest.json` in the **same** commit. See `docs/REBASELINE_WINDOW_VS_STALE_PIN.md`.
2. **Never hand-edit `manifest.json`.** Re-emit it, so `frozen_at` and the hashes move together.
3. **A fact with no declared alignment is unprotected.** Facts 2, 3 and 4 agree today because nothing is *enforcing* that they agree — not because a gate proved it. If one of them ever matters enough to gate on, **first declare its alignment**, then enforce it.
4. **Re-run the generator before citing any number in this file.** Every figure here is a point-in-time reading at `2899bab`.
5. **Prefer token-level measurement for "is this literal live?" questions.** `grep` cannot distinguish a docstring from an executable path, and the difference was 42-vs-0 here.
6. **Classify a finding BEFORE acting on it: documentation, configuration, or executable path.** The three have different fixes and different risk. Documentation misleads a reader; configuration changes behaviour; an executable path is a bug that fires. A finding measured with the wrong instrument manufactures false positives that someone else then has to disprove — which costs more than not measuring at all.
7. **A "zero occurrences" result is itself a claim and needs a second method.** Two independent token analysers disagreed in this very round (one classified a module docstring as live code by a line-prefix heuristic, one by `tokenize` semantics) and produced **opposite** conclusions on the same tree. Separately, a correct instrument scoped to the wrong directories reported "0 live occurrences" while 442 existed elsewhere in the repo.
   - **Re-check a zero with a different method, not the same method re-run.**
   - **State the search scope explicitly.** "0 live `F:` literals" is true of `packages/`+`tools/`+`integrations/`+`apps/` and false of the repository. A zero without a scope is not a measurement.
8. **Pin the revision a negative control runs against, and assert that baseline's shape before trusting its output.** A control that says "run the pre-fix code" via a moving reference (`HEAD~1`) silently becomes "run the current code" the moment anyone commits — and then reports PASS for a defect that was never exercised. Pin the SHA, then assert the baseline has the shape you expect (e.g. the vulnerable construct is present, N occurrences), and only then read the result.
9. **A file-hash comparison against HEAD must normalise line endings.** Measured on this repo (`core.autocrlf=true`): for `packages/jcc_core/host_launcher.py` the HEAD blob is **45,366 bytes** and the worktree file is **46,355** — a delta of exactly **989**, which equals the number of CR bytes. Stripping `\r` makes the two hashes identical, and `git diff` correctly reports clean. A raw `git show HEAD:<path> | sha256sum` therefore reports **DIFFER on an unmodified file**.
   - **This is not hypothetical: my own Fact 1 measurement used exactly that pattern.** It happened to return "0 disagreements" because none of the 12 pinned files were CRLF-divergent at the time — the instrument was capable of a false DIFFER, and I would not have known which kind I was looking at.
   - **CR-stripping is the only load-bearing half.** On this repo `git show` and `git cat-file -p` return **byte-identical** output for the same blob, so preferring `cat-file` is defensive hygiene, not the thing that fixes the false positive. (It could matter on a host where a filter or textconv is configured.)
   - **The non-zero-byte check is a STRONG signal, not a weak fallback.** Measured: it
     compares total bytes against non-NUL bytes, so it fires on **any** NUL — not only on
     a fully-zeroed file. Synthetic cases at identical size: clean `18/18` passes;
     **partially zeroed `18/12` FLAGS**; fully zeroed `18/0` FLAGS. An earlier version of
     this document said it "only checks all-zero" and that it would miss form (a-prime) —
     **that was wrong, and is corrected here.**
   - **The two halves are not backups for each other; they cover different classes.**
     NUL-class damage → the byte check (and the normalised hash also sees it). In-band CR
     damage → only the line-ending-only normalisation, because that damage contains **no
     NUL** and the byte check is structurally blind to it. **Both halves passing ≠ the file
     is intact** — that is the reading to avoid.
   - **The two halves are ORTHOGONAL, not redundant.** Each has a blind spot for a
     *different* class of damage, so "keeping either one" is false and "one misses, the
     other catches it" is also false:
     - in-band CR damage contains **no NUL** → only the line-ending-only normalisation
       sees it; the byte check passes it silently.
     - NUL-class damage → the byte check flags it, and the normalised hash usually does
       too, but the byte check is the one that names it (`ZEROED`).
     **Never describe them as defence-in-depth, backstop, or complementary** — those
     words imply redundancy, and redundancy is exactly what is absent.
   - **`sed 's/\r$//'` is RETRACTED as a recommended form. Use `b.replace(b"\r\n", b"\n")` — and in shell, do not reach for `sed` at all.** Measured with the **real GNU sed 4.9 binary** over every payload of 1–3 bytes from `{a, CR, LF}` (39 payloads), counting **size-preserving pairs that the rule maps to the same output**:
     `sed 's/\r$//'`  → **14** ordered collision pairs
     `replace(CRLF,LF)` → **2** ordered (1 unordered: `0d 0a 0a` vs `0a 0d 0a`)
   Example: HEAD `61 0d 0a` vs work `61 0a 0d` (a same-size reorder) — real sed yields `61 0a` on **both** sides and reports MATCH, masking real content damage; `replace` yields `61 0a` vs `61 0a 0d` and reports DIFFER. **The blind spot is itself size-preserving, which is the defining shape of INC-002.** `replace` is not perfect (it has one such pair) but it is the only one of the three with no *measured* collision in this space, and it is what `tools/inc002_tree_check.py` actually uses.
   **Corollary — never adjudicate a tool with a reimplementation of it.** This mistake was made twice in one round: a Python model of `sed` was used to declare it equivalent to `replace` (it is not — the model stripped only one trailing CR per line, real sed collapses `\r\r\n` and `\n\r` too), and a similar model produced a wrong pair count. **To decide what a tool does, run the tool.**
   **Corollary — assert your instrument on a known-positive control before trusting a negative.** My first `sed` probe passed the pattern as a Python string, so `\r` was a literal backslash-r, sed matched nothing, and it emitted **empty output** — which reads as "MATCH on everything". Without a control assertion I would have reported a spectacular and entirely false result. `runtime/_verify_cr002/sed_vs_replace_real2.py` now aborts unless the invocation is proven functional first.
   - **Never gate the byte check on a NUL run-length threshold** (e.g. "longest run
     ≥ 64"). Measured on same-size samples: `s != z` flags **1 NUL**, **3 scattered
     NULs** and **8 contiguous NULs**, while a `≥ 64` threshold flags **none** of them —
     and it also misses a **fully zeroed 39-byte file**, because 39 < 64. A whole-file
     zeroing therefore evades the threshold purely by being small. **The spec check is
     `s != z` and nothing else.**
   - **“Both methods agree” is only evidence if the data could make them disagree.**
     On files with `CR count = 0`, `tr -d` and `sed` are mathematically incapable of
     diverging, so their agreement is a tautology, not a result. **Immunity is not
     safety.** Before accepting any "the old method was harmless here" argument, check
     whether the sample could have exposed it.
   - **A grep hit is not a live defect. Classify with context before reporting.** Deciding
     "live defect" vs "correct citation" requires reading the surrounding fence, the heading
     above it, and any dated correction note — a per-line grep produces **two** opposite
     errors, and the more dangerous one is reporting a *warning* as a defect: someone then
     "fixes" the document by **deleting the safety warning**. This happened in this round —
     a `tr -d '\r'` script was reported as `✅ 正确用法` (correct usage) and was fixed within
     the hour, but the report that drove the fix was then itself re-reported against the
     *already-corrected* file, where the same line now reads `❌ 错误用法` (wrong usage).
     **A report is a snapshot; re-read the file before acting on someone else's, and before
     re-issuing your own.** Use a context-aware adjudicator for whole-repo sweeps;
     `runtime/_aegis_adjudicate_item5.py` is the current one (verified exit 0, correctly
     adjudicating 1 live site and 0 for the corrected file).
   - **Do not use `git status ??` to test trackedness.** It conflates *ignored* with
     *untracked*. Use `git ls-files --error-unmatch <path>`; `git log` is silent for
     untracked files, so "was it fixed?" must also be answered from file content.

10. **Attribute before fixing: separate "has a dead path" from "is broken because of it".** Four gate/security files were broken. A controlled single-variable change (patch the path only, in a scratch copy) showed **one** was caused by the `F:` literal and **three** had independent causes — API drift, a `SystemExit`, a missing file. Fixing the paths would have left three still broken while looking like a completed sweep.

### Why rule 6 exists (measured, not abstract)

The 42-vs-0 split above is the worked example:

- A `grep`-based relocation gate would report **42 failures** and be wrong about every one — all 42 are docstrings.
- Re-measured with `tokenize` across `packages/`, `tools/`, `integrations/`, `apps/`: **0 files** carry `F:/jev` in executable code; 40 files carry it in prose only.

**Correction to an earlier claim, and to my own measurement.** Two corrections are recorded rather than deleted, because both are the kind of error that survives into a release if you only keep the final number.

1. **A claim I was given, and repeated, was false.** I recorded that a *live* `F:/jev` existed at `jcc_emit.py:36` and that `grep` "missed" it. Verified: the file is `integrations/_common/jcc_emit.py`; that area is the comment block explaining the hardcoded literal **was already replaced** (commit `01cfb6a`). There is no live literal there.
2. **My own "0 live occurrences" was wrong, and so was my instrument's *scope*.** The token pass was correct; the **globs were not** — they covered `packages/`, `tools/`, `integrations/`, `apps/` and never `artifacts/`. A correct instrument pointed at the wrong tree returns a confident, clean, false zero. The 42 prose-only hits it found were real; the zero was not.

**The general form of the mistake is not "grep over-reports" or "grep misses".** It is that **a count never tells you which category a finding is in, and never tells you what it did not look at.** Both categories are real here — 40 files of stale prose that mislead a reader, and genuinely live literals that are bugs. Fix the instrument, state the scope, verify a zero by a *different* method, then act.

## What this document does NOT establish

- It does not prove the four facts will still agree at the next commit.
- It does not cover facts nobody has enumerated yet. This map is a **starting set chosen from what CR-002 touched**, not a census.
- It records **0 disagreements**, which must not be reported as "consistency is enforced". On **3 of 4** facts, consistency is *observed*, not *enforced*.
