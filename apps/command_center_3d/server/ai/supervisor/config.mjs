// JEV V2.0 Phase E/F Supervisor — configuration loader.
//
// PURPOSE: provide a SINGLE source of truth for Supervisor runtime config with
// SAFE DEFAULTS. Everything is OFF / mock / local unless explicitly enabled via
// environment variables. This module performs NO network access, NO provider
// calls, and NO trading. It is read-only with respect to side effects.
//
// The returned object is FROZEN so callers cannot mutate the resolved config
// downstream (defense against accidental re-enablement).

const ALLOWED_MODES = ["OFF", "SHADOW", "PAPER", "LIVE"];

function boolEnv(value, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  return value === "true" || value === "1";
}

function numEnv(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * Resolve the Supervisor configuration from an environment-like object.
 *
 * @param {Record<string,string|undefined>} [env] defaults to process.env
 * @returns {Readonly<{
 *   ENABLE_REAL_TRADING: string,
 *   ALLOW_BUY_SELL: string,
 *   JEV_MODE: "OFF"|"SHADOW"|"PAPER"|"LIVE",
 *   SHADOW_DAYS: number,
 *   PAPER_DAYS: number,
 *   HAS_REAL_IDP: boolean,
 *   HAS_REAL_D1: boolean,
 *   HAS_EXTERNAL_WORM: boolean,
 *   HAS_PAID_API_BUDGET: boolean,
 *   HAS_REAL_CI: boolean,
 * }>}
 */
export function loadSupervisorConfig(env = process.env) {
  const e = env || {};
  const rawMode = (e.JEV_MODE || "OFF").toUpperCase();
  const JEV_MODE = ALLOWED_MODES.includes(rawMode) ? rawMode : "OFF";

  const config = {
    // Trading enablement — string-compared so a stray "1"/"true" is explicit.
    // Defaults to the SAFE value ("false").
    ENABLE_REAL_TRADING: e.ENABLE_REAL_TRADING || "false",
    ALLOW_BUY_SELL: e.ALLOW_BUY_SELL || "false",

    // Operational mode. Default OFF => the Supervisor refuses to start.
    JEV_MODE,

    // Phase E (7-day Shadow) / Phase F (30-day Paper) durations.
    SHADOW_DAYS: numEnv(e.SHADOW_DAYS, 7),
    PAPER_DAYS: numEnv(e.PAPER_DAYS, 30),

    // External authorization signals. All default to FALSE (unauthorized).
    // A fully-authorized environment must flip every one of these explicitly.
    HAS_REAL_IDP: boolEnv(e.HAS_REAL_IDP, false),
    HAS_REAL_D1: boolEnv(e.HAS_REAL_D1, false),
    HAS_EXTERNAL_WORM: boolEnv(e.HAS_EXTERNAL_WORM, false),
    HAS_PAID_API_BUDGET: boolEnv(e.HAS_PAID_API_BUDGET, false),
    HAS_REAL_CI: boolEnv(e.HAS_REAL_CI, false),
  };

  return Object.freeze(config);
}

export { ALLOWED_MODES };
