// JEV — real-backends entry point.
//
// Aggregates the three real backend configs (IdP / D1 / WORM) and exposes a
// single `realBackendsAvailable()` decision used by the Supervisor gate (see
// server/ai/supervisor/gate.mjs) and by any production wiring.
//
// Every adapter is FAIL-CLOSED: unconfigured => throws NotConfiguredError, never
// a mock result. See UNBLOCK_CHECKLIST.md for the exact env vars to provide.

import { loadRealIdPConfig, RealIdP } from "./real-idp.mjs";
import { loadRealD1Config, RealD1Ledger } from "./real-d1.mjs";
import { loadRealWormConfig, RealWormAnchor } from "./real-worm.mjs";
import { NotConfiguredError } from "./errors.mjs";

export { NotConfiguredError } from "./errors.mjs";
export { loadRealIdPConfig, RealIdP } from "./real-idp.mjs";
export { loadRealD1Config, RealD1Ledger, REAL_D1_SCHEMA } from "./real-d1.mjs";
export { loadRealWormConfig, RealWormAnchor } from "./real-worm.mjs";

/**
 * Aggregate readiness of all three real backends.
 * @returns {{ all:boolean, idp:boolean, d1:boolean, worm:boolean }}
 */
export function realBackendsAvailable(env = process.env) {
  const idp = loadRealIdPConfig(env).configured;
  const d1 = loadRealD1Config(env).configured;
  const worm = loadRealWormConfig(env).configured;
  return { all: idp && d1 && worm, idp, d1, worm };
}

/** Convenience: construct all three adapters when configured (throws if not). */
export function buildRealBackends({ idpClient, d1Client, wormClient } = {}, env = process.env) {
  return {
    idp: new RealIdP(loadRealIdPConfig(env)),
    d1: new RealD1Ledger(loadRealD1Config(env), d1Client),
    worm: new RealWormAnchor(loadRealWormConfig(env), wormClient),
  };
}
