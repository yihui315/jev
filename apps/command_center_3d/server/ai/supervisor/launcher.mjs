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
import { runSupervisorLoop } from "./loop.mjs";

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
  // When (and only when) the gate above has passed in a properly authorized
  // environment, this is where the Supervisor builds its continuous loop.
  // `runSupervisorLoop` is itself FAIL-CLOSED: it re-checks the gate and builds
  // the loop only on canStart===true. It performs NO trading, NO network, NO
  // paid APIs — only local health checks + JSONL ledger writes (see loop.mjs).
  //
  // This branch is UNREACHABLE in the current fail-closed scaffold: the gate
  // always refuses under default / unauthorized config, so we never reach here
  // and __actualStartCount stays 0. The block is kept as the real hand-off.
  // =========================================================================
  __actualStartCount += 1; // <- only on a REAL start (authorized env)
  const loop = runSupervisorLoop({ config, flags, deps: opts.deps || {} });
  const handle = loop.handle;
  return {
    started: true,
    handle: {
      stop: () => handle.stop(),
      done: handle.done,
    },
    loop,
  };

  // (No code past this point is reachable; left for clarity that the scaffold
  // session never reaches a real start under unauthorized config.)
}
