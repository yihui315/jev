/**
 * gate-assertion-floor.mjs — CASE-count floor for the P0/P1/P2/P3P4 gate.
 * (Closes C12 / F-22-G. Node stdlib only — no yaml/yargs/chalk/js-yaml.)
 *
 * WHY THIS EXISTS
 * ---------------
 * The gate step in .github/workflows/ci.yml used to trust exit codes alone: it
 * ran 7 suites and failed only if one of them exited non-zero. That is blind to
 * a whole class of regressions where a suite still exits 0 while having
 * silently lost its assertions:
 *
 *   - someone comments out the body of a test block      → exit 0, 0 assertions
 *   - a module loses its `isMainModule()` guard and the
 *     file is executed for its side effects only         → exit 0, few asserts
 *   - a suite is rewritten to `process.exit(0)` early     → exit 0, few asserts
 *
 * Exit code alone therefore cannot distinguish "all cases passed" from
 * "3 cases passed and the rest were deleted". This script adds a
 * DECLARATIVE MINIMUM: each suite has a frozen expected CASE count, and a
 * suite that reports fewer than its floor (or loses its summary signature, or
 * reports any FAIL, or is missing from the log directory entirely) is a hard
 * failure.
 *
 * CONTRACT WITH ci.yml
 * --------------------
 * The gate step runs each suite as
 *     node <path> 2>&1 | tee "$GATE_LOG_DIR/<slug>.log"
 * under `set -euo pipefail`, so a non-zero suite exit still fails the step.
 * The floor step then runs THIS script against the same directory. Together:
 *   - non-zero exit        → caught by the gate step (pipefail)
 *   - exit 0 but too few   → caught here
 *
 * IMPORTANT (R19): a "case" is one named check block. A single case can hold
 * many `assert.*` statements, so these floors freeze HOW MANY CASES REPORTED,
 * not how many assertions ran inside them. Deleting assertions INSIDE a case is
 * therefore NOT caught here — that is a known open gap.
 *
 * NO total case count and NO assertion-statement count is written in this
 * comment, on purpose. Both move every time a guard is added, and this file's
 * own history already contains a hard-failing gate caused by a stale count.
 * A stale hard number is worse than none. The authoritative numbers are:
 *   1. the `expect` table below — exact, and enforced on every run;
 *   2. the `GATE_FLOOR_OK suites=N total_cases=M exit=0` line that CI prints.
 *
 * The floors are EXACT, not ranges. An intentional new assertion must bump the
 * number in this table, which makes every count change a visible, reviewable
 * diff instead of a silent drift.
 *
 * Usage:
 *   node apps/command_center_3d/scripts/gate-assertion-floor.mjs [logDir]
 *   GATE_LOG_DIR=/path/to/logs node .../gate-assertion-floor.mjs
 * Default logDir: <repoRoot>/.gate
 *
 * exit 0 = every suite met its floor; exit 1 = at least one violation.
 */
import { readFileSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));   // .../apps/command_center_3d/scripts
const APP = dirname(HERE);                             // .../apps/command_center_3d
const REPO = dirname(dirname(APP));                    // repo root

function pickLogDir() {
  const argv = process.argv.slice(2).filter((a) => a !== "--dir");
  if (argv.length > 0) return resolve(argv[0]);
  if (process.env.GATE_LOG_DIR) return resolve(process.env.GATE_LOG_DIR);
  return join(REPO, ".gate");
}
const LOG_DIR = pickLogDir();

/**
 * Frozen expectation table. `expect` is the number of assertions the suite MUST
 * report. Every entry must also yield `fail === 0`.
 *
 * Signatures are anchored (`^...$` with the `m` flag) so a stray mention inside
 * an EVIDENCE dump cannot be mistaken for the real summary line.
 */
const SUITES = [
  {
    slug: "1-p0-contracts",
    label: "server/ai/tests/day14-p0-contracts.mjs (P0-F CONTRACTS)",
    expect: 7,
    // "SUMMARY P0-F CONTRACTS (frozen modules, VERIFIED now): 7 pass, 0 fail"
    re: /^SUMMARY P0-F CONTRACTS\b.*?:\s*(\d+) pass,\s*(\d+) fail\s*$/m,
    failGroup: 2,
  },
  {
    slug: "2-p0-runner",
    label: "server/ai/tests/day14-p0-runner.mjs (P0-F RUNNER)",
    expect: 24,
    // "SUMMARY P0-F RUNNER (sandbox fault injection + R1-R6 + F-12): 24 pass, 0 fail"
    re: /^SUMMARY P0-F RUNNER\b.*?:\s*(\d+) pass,\s*(\d+) fail\s*$/m,
    failGroup: 2,
  },
  {
    slug: "3-market-data",
    label: "equity_collector_v45/tests/market-data.test.mjs",
    expect: 10,
    // "PASS=10 FAIL=0" (+ "RESULT=PASS")
    re: /^PASS=(\d+) FAIL=(\d+)\s*$/m,
    failGroup: 2,
    extra: (log) => (/^RESULT=PASS\s*$/m.test(log) ? null : 'missing "RESULT=PASS" verdict line'),
  },
  {
    slug: "4-inference-evidence",
    label: "server/ai/evidence/tests/inference-evidence.test.mjs",
    expect: 17,
    // "PASS=17 FAIL=0" — take the LAST occurrence in this suite's own log.
    re: /^PASS=(\d+) FAIL=(\d+)\s*$/m,
    last: true,
    failGroup: 2,
    extra: (log) =>
      /---- P2 inference-evidence/.test(log) ? null : "missing P2 inference-evidence report header",
  },
  {
    slug: "5-p3-contracts",
    label: "scripts/day14-research/p3-contracts.mjs",
    expect: 12,
    // "PASS=12 FAIL=0 TOTAL=12"
    re: /^PASS=(\d+) FAIL=(\d+) TOTAL=(\d+)\s*$/m,
    countGroup: 3,
    failGroup: 2,
    passGroup: 1,
  },
  {
    slug: "6-supervisor",
    label: "scripts/supervisor/supervisor.test.mjs",
    // 14 -> 15: ⑮ verifyChain 非数组入参一律 throw（CHAIN_INPUT_NOT_ARRAY）。
    // 15 -> 16: ⑯ 内存 fs 尊重 open 模式（a 追写 / w 截断 / fsync 可计数）。
    // 16 -> 18: ⑰⑱ 永久负对照 —— 旧 string fixture 必须被证明是错的（接力写丢失 / 半字节变 U+FFFD）。
    //           Without these, a later "simplification" back to strings turns every
    //           tearing test green, and green is the most easily accepted wrong state.
    // 18 -> 23: ⑲e/⑲f/⑲g/⑲h/⑲i —— write-ahead ordering (D-14-0): the intent is on
    //           disk before the side effect, a crash in between is IN_DOUBT and is
    //           never re-run, completion without intent throws, lease_epoch is a
    //           monotonic fence, and the lease token never enters the ledger in clear.
    //           5 new named blocks, so 18 + 5 = 23. The number equals the MEASURED
    //           count, it is not a target.
    // 23 -> 25: ⑰⑱ 拆成 ⑰a/⑰b/⑱a/⑱b —— each of the two used to carry TWO opposing
    //           claims under one name ("the legacy model is wrong" AND "this fixture
    //           is right"). Measured consequence: breaking the real fixture turned
    //           ⑰ red while its message pointed at the legacy model — right colour,
    //           wrong reason. Split so each side owns one claim and each has a
    //           mutation that reddens it and nothing else. delta = +2, so 23 + 2 = 25.
    // 25 -> 28: ⑲c/⑲d/⑳ — torn-tail truncation with discarded_bytes, a non-chaining
    //           tail that must stay LEDGER_TAMPERED (and must NOT be truncated away),
    //           and the bounded re-verify window that makes a same-length in-place
    //           tamper detectable. 3 new named blocks, so 25 + 3 = 28. Measured, not
    //           a target.
    // 28 -> 33: P1 ledger-io fail-closed + the two windows + lease handover.
    //           ㉑a a whole-cycle suffix truncation with the anchor present must throw
    //               (it used to be classified and then "repaired" into a runnable
    //               state, and the measured consequence was a duplicated side effect);
    //           ㉑b a non-empty ledger with no anchor must throw — deleting the anchor
    //               file used to switch the whole control off and report CLEAN;
    //           ㉑c a refusal must be repeatable and must not rewrite the anchor,
    //               otherwise the same code destroys the evidence it just produced;
    //           ㉒ a lease handover voids the cursor (the cursor compares LENGTH, so a
    //               same-length write by the previous holder would pass it);
    //           ㉓ the operation window, which is the only bound that expires for a
    //               term that never appends — it had no evidence at all before.
    //           5 new named blocks, so 28 + 5 = 33. Measured, not a target.
    // 33 -> 36: ⑳'s contract changed from "within K reads" to "on the next read"
    //           (the cursor's invalidation condition gained mtimeMs — byte length
    //           cannot see a same-length rewrite), and three blocks were added for
    //           the consequences: ⑳b (an attacker who forges mtime is bounded only
    //           by K/M — that is what K/M are FOR), ㉔ (M > K x measured max row
    //           width asserted, not commented) and ㉕ (an unknown option key must
    //           throw; a renamed option silently reverting to defaults is how a
    //           64x wider window grew out of a typo). 33 + 3 = 36. Measured.
    // 36 -> 40: `⑯c` split into three (a clamp that must leave a trail, history in
    //           (CURRENT_MAX, ABSOLUTE_MAX] that must stay READABLE, history above
    //           ABSOLUTE_MAX that must not get a permanent exception) and `㉖` (the
    //           anchor now compares headHash/count, so an equal-length rehash is
    //           LEDGER_ANCHOR_HEAD_MISMATCH). 36 + 4 = 40. Measured.
    // 40 -> 41: `㉗` — the anchor's three fields must ALL be present and correctly
    //           typed, or the load fails with LEDGER_ANCHOR_INVALID. This is not a
    //           fourth recovery mode, it is the missing half of ㉖: ㉖ compares
    //           count/headHash, but a field that is allowed to be absent expresses
    //           "not applicable", and a comparison that is skipped for absence is the
    //           same defect as ㉑b (deleting the anchor file to switch the control
    //           off) moved inside one JSON object. 40 + 1 = 41. Measured, not a target.
    // 41 -> 42: `⑲j` — a lease handover must not license a re-run of an in-doubt
    //           cycle. ⑲f covers "same process restarts"; this covers "a different
    //           lease holder reads it". The re-open forced by the handover is a
    //           state-rebuild point, and a rebuild that loses the in-doubt slot
    //           downgrades IN_DOUBT to ABSENT, which reads as "never started".
    //           41 + 1 = 42. Measured.
    // 42 -> 43: `㉘` — the three window constants must be finite positive integers.
    //           ㉔ only pins the LOWER bound (M > K x row width), so setting M to
    //           Infinity satisfied it and left every case green: a bound of Infinity
    //           is not a wide bound, it is no bound, while the code still reads as
    //           "re-verify every 256 KiB". The case deliberately does NOT freeze the
    //           numbers (values get ruled on; see M: 1 MiB -> 256 KiB). 42 + 1 = 43.
    //           Measured.
    // 43 -> 45: two claims that were both "a declaration wider than its case".
    //           `㉙` proves M DOES fire first when rows are wide (legal 4096 B
    //           history: ~66 rows trips M while K=256 is far away) — without it,
    //           "M never fires" was invisible to the whole suite. `㉚` gives the
    //           anchor's `count` comparison its own witness: removing just that half
    //           used to redden nothing, i.e. count was only riding along on the
    //           headHash comparison while reading like an independent field.
    //           43 + 1 + 1 = 45. Measured. (`㉔` also gained its UPPER half in this
    //           same commit but it is the same case, not a new one: the old assertion
    //           `M > K x measured width` was literally the condition under which M
    //           can never fire — at 307 B rows M needs ~853 appends vs K=256.)
    // Earlier the ⑮ guard was missing, so a caller passing a wrapper object made
    // `0 < undefined` false, the loop ran zero times, and verifyChain returned
    // {intact:true, count:undefined} — a silent pass. See core.mjs:verifyChain.
    expect: 45,
    // "RESULT PASS=45 FAIL=0"
    re: /^RESULT PASS=(\d+) FAIL=(\d+)\s*$/m,
    failGroup: 2,
  },
  {
    slug: "7-no-fake-live",
    label: "scripts/check-no-fake-live.mjs (static P7 + YAML self-checks)",
    expect: 15,
    // "RESULT: CORE_CASES=7 EXTRA_CASES=8 TOTAL_CASES=15 exit=0"
    // 10 -> 12 -> 13 -> 14 -> 15: Y3 (F-25 floor survival + fatality invariant), Y4 (F-26
    // Track B evidence freshness), Y5 (R22 V1.6 evidence freshness) and Y9 (the
    // service-auth trust path is UNREACHABLE — 0 call sites) are separate named
    // checks ON PURPOSE, so that deleting any of them is caught HERE. A guard
    // folded inside another check would itself be unguarded.
    // Y9 is a REGISTRATION, not a safety claim: it asserts an absence (five exports,
    // zero call sites), so "PASS" here means "still unreachable", not "safe".
    // 14 -> 15: Y6 (Phase A) glob-matches the CONCRETE integration branch and
    // PR #7 base against BOTH workflows' trigger lists, so a literal
    // `includes("feature/**")` can no longer stand in for real coverage.
    // Here group 2 is EXTRA_CASES, NOT a fail count — so this suite
    // declares no failGroup and instead asserts its own `exit=` field is 0.
    re: /^RESULT: CORE_CASES=(\d+) EXTRA_CASES=(\d+) TOTAL_CASES=(\d+) exit=(\d+)\s*$/m,
    countGroup: 3,
    exitGroup: 4,
    extra: (log, m) =>
      Number(m[4]) === 0 ? null : `self-reported exit=${m[4]} (must be 0)`,
  },
  {
    slug: "8-api-body-read",
    label: "server/tests/http-body-read.mjs (request-body byte fidelity + byte cap)",
    expect: 17,
    // "RESULT PASS=17 FAIL=0"
    // 17 cases cover BOTH facades: byte fidelity under chunk splits (including a
    // permanent negative control that runs the PRE-FIX reader and asserts it
    // corrupts the body), a byte — not character — cap, the streaming/declared
    // two-stage bound, the abort path settling instead of hanging, the 413 being
    // flushed before teardown (over a real socket), and static wiring guards that
    // fail if a facade stops calling the shared reader.
    re: /^RESULT PASS=(\d+) FAIL=(\d+)\s*$/m,
    failGroup: 2,
  },
  {
    slug: "9-final10-judge",
    label: "scripts/tests/final10-judge.test.mjs (FINAL-10 reboot-evidence judge)",
    expect: 20,
    // "RESULT PASS=20 FAIL=0"
    // 20 cases. 8 of them are named NEGATIVES that all assert the same shape:
    // "I do not know" must not read as "fine". GATE_1_RC_ABSENT and
    // ATTACH_RC_ABSENT (an absent exit_code, which happens exactly on launch
    // failure, timeout, and attach), FRESHNESS_SINCE_MISSING and
    // FRESHNESS_SINCE_UNPARSABLE (an absent or unparsable `since`, which used to
    // mean "fresh"), ATTACH_NO_STARTED_AT (RUN_LOST), plus ALLOWLIST_EMPTY and
    // EXEMPT_EMPTY (the two exemption channels, both empty and both guarded at
    // import time so an empty tuple cannot silently become a populated one).
    // Two cases exist only to keep the carry semantics honest in BOTH
    // directions: RC_NONE_LAUNCH_FAILED / GATE_1_RC_ABSENT require the earlier
    // reason to survive a negative verdict, and GOOD_DETAIL_IS_JUDGE_TEXT
    // requires a PASS to carry the judge's own sentence and nothing prepended --
    // without it, "always keep the old detail" would pass every other case.
    // This suite is the JUDGE. It says nothing about whether the reboot evidence
    // exists; it only says the judge refuses evidence it cannot interpret.
    re: /^RESULT PASS=(\d+) FAIL=(\d+)\s*$/m,
    failGroup: 2,
  },
  {
    slug: "10-human-publish-gate",
    label: "scripts/tests/human-publish-gate.test.mjs (P0-A HUMAN_PUBLISH_GATE reverse tests)",
    expect: 28,
    // "RESULT PASS=28 FAIL=0". Six mandated reverse tests (no grant, forged
    // confirmationId, expired, replay, edited content, valid grant) plus
    // identity/scope/signature/audit/red-line checks and the Paper unattended
    // regression, plus N23/N24 (Aegis C1 delimiter-collision FAIL fix: a
    // shifted-boundary grant must not verify, at module AND HTTP level),
    // N25 (Aegis C6: missing state/audit fails closed), and N26/N27/N28
    // (Aegis recheck round 2: malformed lone-surrogate utf8-folding
    // re-label must be refused at module, HTTP and issuer levels). This
    // entry is what stops the new gate's tests from being silently deleted
    // while the suite still exits 0.
    re: /^RESULT PASS=(\d+) FAIL=(\d+)\s*$/m,
    failGroup: 2,
  },
  {
    slug: "11-provider-interface",
    label: "server/ai/tests/t10-provider-interface.test.mjs (P0-C JEV AI Provider Gateway)",
    expect: 16,
    // "RESULT PASS=16 FAIL=0". Per-runtime 4/6/2, four-state booleans,
    // no callable provider, and the attribution guard (A7-A11). Keeps the
    // Provider inventory from silently losing its teeth while CI stays green.
    re: /^RESULT PASS=(\d+) FAIL=(\d+)\s*$/m,
    failGroup: 2,
  },
];

const violations = [];
let checked = 0;
let totalCases = 0;

// Phase A / P0 weak-assertion fix. At bde4223 only 5-p3-contracts declared
// failGroup and 7-no-fake-live declared exitGroup; the other SEVEN suites had
// neither, so a log reading `RESULT PASS=45 FAIL=3` satisfied `expect: 45`
// and was reported GATE_FLOOR_OK. Measured mutation: with
// `RESULT PASS=20 FAIL=1` in 9-final10-judge.log the pre-fix floor exits 0;
// this version exits 1 with reason=fail-count=1. Every entry must now declare
// where its failure signal comes from, or the floor refuses to run at all.
for (const s of SUITES) {
  if (!s.failGroup && !s.exitGroup) {
    console.error(
      `GATE_FLOOR_CONFIG ${s.slug} declares neither failGroup nor exitGroup: ` +
      `its failures would be invisible to this gate`
    );
    process.exit(1);
  }
}

console.log("=== gate-assertion-floor.mjs — C12 / F-22-G case-count floor ===");
console.log("LOG_DIR " + LOG_DIR);

for (const s of SUITES) {
  checked++;
  const file = join(LOG_DIR, s.slug + ".log");
  if (!existsSync(file)) {
    violations.push(`GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=0 reason=log-missing path=${file}`);
    continue;
  }
  const log = readFileSync(file, "utf8");

  // --- summary signature ---
  // `s.last` suites may print the same signature more than once (e.g. an
  // EVIDENCE dump quoting a PASS=/FAIL= line before the real summary); take the
  // final occurrence. matchAll() requires the `g` flag.
  const all = s.last
    ? [...log.matchAll(new RegExp(s.re.source, s.re.flags + "g"))]
    : [log.match(s.re)].filter(Boolean);
  const m = s.last ? (all.length ? all[all.length - 1] : null) : all[0];
  if (!m) {
    violations.push(
      `GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=0 reason=summary-signature-not-found`
    );
    continue;
  }

  const countGroup = s.countGroup || 1;
  const count = Number(m[countGroup]);
  const fail = s.failGroup ? Number(m[s.failGroup]) : null;

  if (!Number.isFinite(count)) {
    violations.push(`GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=NaN reason=count-unparseable`);
    continue;
  }

  // --- internal consistency: TOTAL must equal PASS + FAIL where both are given ---
  if (s.passGroup && s.failGroup && Number(m[s.passGroup]) + Number(m[s.failGroup]) !== count) {
    violations.push(
      `GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=${count} reason=total!=pass+fail (pass=${m[s.passGroup]} fail=${m[s.failGroup]} total=${count})`
    );
  }

  // --- no failing assertion is allowed to hide behind a green exit code ---
  if (fail !== null && fail !== 0) {
    violations.push(`GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=${count} reason=fail-count=${fail}`);
  }

  if (s.extra) {
    const why = s.extra(log, m);
    if (why) violations.push(`GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=${count} reason=${why}`);
  }

  // --- the floor itself: EXACT count, so both shrinkage AND silent growth show up ---
  if (count < s.expect) {
    violations.push(`GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=${count}`);
  } else if (count > s.expect) {
    // Growth is not a security regression — but it means the frozen table is
    // stale, so it is a hard failure too: the table must be updated in the same
    // commit that adds assertions.
    violations.push(
      `GATE_FLOOR_VIOLATION ${s.slug} expected=${s.expect} actual=${count} reason=count-grew-update-the-floor-table`
    );
  } else {
    console.log(`GATE_FLOOR ok ${s.slug} expected=${s.expect} actual=${count} — ${s.label}`);
  }
  totalCases += count;
}

if (violations.length > 0) {
  console.error("");
  for (const v of violations) console.error(v);
  console.error("");
  console.error(
    `GATE_FLOOR_FAILED suites_checked=${checked} violations=${violations.length} total_cases_seen=${totalCases}`
  );
  process.exit(1);
}

console.log("");
console.log(`GATE_FLOOR_OK suites=${checked} total_cases=${totalCases} exit=0`);
