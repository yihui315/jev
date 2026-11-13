// JEV V2.0 Phase E/F Supervisor — start gate (FAIL-CLOSED).
//
// PURPOSE: decide, in a single pure function, whether the Supervisor is allowed
// to start a continuous run (Phase E Shadow / Phase F Paper). The decision is
// FAIL-CLOSED: any missing precondition yields canStart=false with a list of
// human-readable reasons. NO I/O, NO network, NO provider calls happen here.
//
// Rules (evaluated top-to-bottom; all blockers are accumulated):
//   1. JEV_MODE === "OFF"  -> refuse (MODE_OFF).
//   2. Any of phaseBGo/phaseCGo/phaseDGo false -> refuse (lists unmet phases).
//   3. Any external-auth flag false -> refuse (lists each missing flag).
//   4. LIVE additionally requires ENABLE_REAL_TRADING==="true" AND
//      ALLOW_BUY_SELL==="true"; otherwise refuse (REAL_TRADING_DISABLED).
//   5. Only if ALL of the above pass -> canStart=true.

const REQUIRED_AUTH_FLAGS = [
  "HAS_REAL_IDP",
  "HAS_REAL_D1",
  "HAS_EXTERNAL_WORM",
  "HAS_PAID_API_BUDGET",
  "HAS_REAL_CI",
];

/**
 * Evaluate whether the Supervisor may start.
 *
 * @param {object} config  resolved config (see config.mjs loadSupervisorConfig)
 * @param {{phaseBGo?:boolean, phaseCGo?:boolean, phaseDGo?:boolean}} flags
 *        mock-layer GO booleans. Defaults to false (refuse) when omitted.
 * @returns {{canStart:boolean, reasons:string[]}}
 */
export function evaluateStartGate(config, flags = {}) {
  const reasons = [];

  // --- 1. Mode OFF -------------------------------------------------------
  if (!config || config.JEV_MODE === "OFF") {
    reasons.push("MODE_OFF");
  }

  // --- 2. Phase GO gates -------------------------------------------------
  const phaseLetters = { phaseBGo: "B", phaseCGo: "C", phaseDGo: "D" };
  for (const name of Object.keys(phaseLetters)) {
    if (!flags[name]) {
      reasons.push("PHASE_" + phaseLetters[name] + "_NOT_GO");
    }
  }

  // --- 3. External authorization flags ----------------------------------
  // Each missing flag is reported individually so operators see exactly what
  // external system has not been authorized.
  for (const flag of REQUIRED_AUTH_FLAGS) {
    if (!config || !config[flag]) {
      reasons.push("MISSING_AUTH:" + flag);
    }
  }

  // --- 4. LIVE real-trading enablement ----------------------------------
  if (config && config.JEV_MODE === "LIVE") {
    const tradingEnabled =
      config.ENABLE_REAL_TRADING === "true" && config.ALLOW_BUY_SELL === "true";
    if (!tradingEnabled) {
      reasons.push("REAL_TRADING_DISABLED");
    }
  }

  const canStart = reasons.length === 0;
  return { canStart, reasons };
}

export { REQUIRED_AUTH_FLAGS };
