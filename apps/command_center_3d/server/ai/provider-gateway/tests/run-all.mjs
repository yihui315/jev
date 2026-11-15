// JEV Phase C — Provider Gateway offline test runner (stdlib-only, no network).
//
// Aggregates every t-c-*.test.mjs suite and prints a single RESULT line:
//   RESULT PASS=<n> FAIL=<m>
// Exits 1 if any suite failed, else 0. This runner is NOT part of the frozen
// gate floor (gate-assertion-floor.mjs only pins the 11 listed suites, and t10
// stays at exactly 16 PASS untouched).

import * as router from "./t-c-router.test.mjs";
import * as timeout from "./t-c-timeout-retry.test.mjs";
import * as breaker from "./t-c-circuit-breaker.test.mjs";
import * as metrics from "./t-c-metrics.test.mjs";
import * as evidence from "./t-c-evidence.test.mjs";
import * as comparison from "./t-c-comparison.test.mjs";
import * as gateway from "./t-c-gateway.test.mjs";

const suites = [router, timeout, breaker, metrics, evidence, comparison, gateway];

let totalPass = 0;
let totalFail = 0;

console.log("=== JEV Phase C — Provider Gateway offline suite (MOCK-ONLY) ===");
for (const s of suites) {
  const r = await s.run();
  totalPass += r.pass;
  totalFail += r.fail;
  const flag = r.fail === 0 ? "OK" : "FAILED";
  console.log(`---- suite ${r.name}: PASS=${r.pass} FAIL=${r.fail} [${flag}]`);
}
console.log(`SUMMARY phase-c-gateway: pass=${totalPass} fail=${totalFail}`);
console.log("RESULT PASS=" + totalPass + " FAIL=" + totalFail);
process.exit(totalFail > 0 ? 1 : 0);
