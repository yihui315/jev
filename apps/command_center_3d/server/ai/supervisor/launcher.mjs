// JEV V2.0 Phase E/F Supervisor — launcher (FAIL-CLOSED).
//
// PURPOSE: the single entry point that turns a start decision into a running
// Supervisor handle. This session is FAIL-CLOSED and SCAFFOLD-ONLY:
//
//   * If the gate refuses (canStart === false) this function returns
//     { started:false, reasons } and MUST NOT create any timer / interval /
//     loop / scheduler of any kind.
//   * If the gate would allow start (only in a FUTURE, fully-authorized
//     environment) the continuous-loop body is STUBBED with an explicit comment
//     marking it as NOT EXECUTED in this session. No real trading, no network,
//     no paid APIs are reachable from here.
//
// The module exposes a counter `getActualStartCount()` that is incremented ONLY
// when an actual continuous loop lifecycle is created. In this scaffold version
// it stays at 0 for every default / unauthorized configuration, which the
// self-test uses to prove no timer was ever created.

import { evaluateStartGate } from "./gate.mjs";

// Incremented only when a REAL continuous loop is actually built. Never touched
// on a refused start. Intentionally 0 throughout this fail-closed session.
let __actualStartCount = 0;

export function getActualStartCount() {
  return __actualStartCount;
}

/**
 * Attempt to start the Supervisor.
 *
 * @param {object} config  resolved config
 * @param {{phaseBGo?:boolean, phaseCGo?:boolean, phaseDGo?:boolean}} flags
 * @param {object} [opts]  reserved for future (ledgerDir, etc.)
 * @returns {{started:boolean, reasons?:string[], handle?:object}}
 */
export function startSupervisor(config, flags = {}, opts = {}) {
  const { canStart, reasons } = evaluateStartGate(config, flags);

  // FAIL-CLOSED: refuse without any side effects / timers.
  if (!canStart) {
    return { started: false, reasons };
  }

  // =========================================================================
  // FUTURE / FULLY-AUTHORIZED ENVIRONMENT ONLY — NOT EXECUTED IN THIS SESSION.
  // -------------------------------------------------------------------------
  // The block below is intentionally a STUB. When (and only when) the gate has
  // passed in a properly authorized environment, this is where the Supervisor
  // would build:
  //   * a daily-archive rotation job (Phase E 7-day / Phase F 30-day),
  //   * a periodic health-check job (see health.mjs),
  //   * an auto-pause loop that halts the run if health degrades.
  //
  // It MUST NOT run while this scaffold is fail-closed. No setInterval /
  // setTimeout / loop is created here. The real-trading path is unreachable
  // because the gate above already forbids starting without full authorization.
  // =========================================================================
  //
  // __actualStartCount += 1; // <- only on a REAL start (future env)
  // const dailyArchive = setInterval(() => { ... }, 24h);
  // const healthCheck  = setInterval(() => { ... }, 60s);
  // return {
  //   started: true,
  //   handle: { stop: () => { clearInterval(dailyArchive); clearInterval(healthCheck); } },
  // };

  // Defensive guard: this scaffold session never reaches a real start.
  // (Left as a no-op to make intent explicit; __actualStartCount is untouched.)
  return { started: false, reasons: ["SCAFFOLD_NO_START"] };
}
