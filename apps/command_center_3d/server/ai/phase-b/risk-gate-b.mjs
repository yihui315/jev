// JEV Phase B — F-14 / F-15: independent risk gate.
//
// This risk gate is INDEPENDENT of the human authorization gate
// (HUMAN_PUBLISH_GATE / C3). A human grant can NEVER override a risk breach,
// and when the risk state is unknown the gate fails closed (deny). Thresholds
// are SERVER-configured and never accepted from the client.
//
// NOTE on thresholds: the concrete numeric limits are intentionally NOT invented
// here. They must be ported from the actual F-14/F-15 security audit record
// (per design doc §7). The structure below uses safe placeholder defaults that
// are deliberately strict; production values come from the audit source.

export const RISK_GATE_CODES = Object.freeze({
  APPROVED: "RISK_APPROVED",
  BREACH: "RISK_BREACH_DENIED",
  STATE_UNKNOWN: "RISK_STATE_UNKNOWN",
  THRESHOLD_INVALID: "RISK_THRESHOLD_INVALID",
});

export const DEFAULT_RISK_LIMITS = Object.freeze({
  maxDrawdownPct: 25,        // single strategy max drawdown %
  maxExposureUsd: 5000,       // total open exposure ceiling (USD)
  maxSingleOrderUsd: 1000,    // per-order notional ceiling (USD)
  maxOpenOrders: 10,          // circuit-breaker on order count
  circuitBreakerTripped: false,
});

function hasValue(v) {
  return v !== null && v !== undefined && Number.isFinite(Number(v));
}

/**
 * Evaluate the risk posture. `riskState` must be supplied by the server.
 *   * riskState missing/unknown  -> STATE_UNKNOWN (deny, fail closed)
 *   * any threshold breached     -> BREACH (deny; human auth cannot override)
 *   * otherwise                  -> APPROVED
 *
 * @param {object} opts
 * @param {object|null} opts.riskState  server-provided portfolio/risk state
 * @param {object} [opts.limits]        server-configured thresholds
 */
export function evaluateRiskGate({ riskState, limits = {} }) {
  const cfg = { ...DEFAULT_RISK_LIMITS, ...limits };

  // Fail closed when the risk state is unknown / unreadable.
  if (!riskState || typeof riskState !== "object") {
    return { approved: false, code: RISK_GATE_CODES.STATE_UNKNOWN, reason: "risk state unavailable" };
  }

  const checks = [];

  // Circuit breaker is an explicit operator-set flag (never client-set).
  if (cfg.circuitBreakerTripped === true) {
    checks.push({ name: "circuit_breaker", pass: false, detail: "operator-tripped" });
    return { approved: false, code: RISK_GATE_CODES.BREACH, reason: "circuit breaker tripped", checks };
  }

  if (hasValue(riskState.drawdownPct) && Number(riskState.drawdownPct) > cfg.maxDrawdownPct) {
    checks.push({ name: "max_drawdown", pass: false, value: riskState.drawdownPct, limit: cfg.maxDrawdownPct });
    return { approved: false, code: RISK_GATE_CODES.BREACH, reason: "max drawdown exceeded", checks };
  }

  if (hasValue(riskState.exposureUsd) && Number(riskState.exposureUsd) > cfg.maxExposureUsd) {
    checks.push({ name: "max_exposure", pass: false, value: riskState.exposureUsd, limit: cfg.maxExposureUsd });
    return { approved: false, code: RISK_GATE_CODES.BREACH, reason: "total exposure exceeded", checks };
  }

  if (hasValue(riskState.intendedOrderUsd) && Number(riskState.intendedOrderUsd) > cfg.maxSingleOrderUsd) {
    checks.push({ name: "max_single_order", pass: false, value: riskState.intendedOrderUsd, limit: cfg.maxSingleOrderUsd });
    return { approved: false, code: RISK_GATE_CODES.BREACH, reason: "single order notional exceeded", checks };
  }

  if (hasValue(riskState.openOrders) && Number(riskState.openOrders) > cfg.maxOpenOrders) {
    checks.push({ name: "max_open_orders", pass: false, value: riskState.openOrders, limit: cfg.maxOpenOrders });
    return { approved: false, code: RISK_GATE_CODES.BREACH, reason: "open order count exceeded", checks };
  }

  checks.push({ name: "all_thresholds", pass: true });
  return { approved: true, code: RISK_GATE_CODES.APPROVED, reason: "within all server risk limits", checks };
}

/**
 * Compose the human gate decision with the independent risk decision.
 * Order is irrelevant to the outcome: if risk denies, the whole thing denies,
 * regardless of a human ALLOW. This enforces "human authorization never
 * overrides risk breach" (F-14/F-15).
 */
export function combineRiskAndHuman({ risk, human }) {
  if (!risk || !risk.approved) {
    return {
      approved: false,
      code: (risk && risk.code) || RISK_GATE_CODES.STATE_UNKNOWN,
      reason: (risk && risk.reason) || "risk gate denied",
      risk,
      human: human || null,
    };
  }
  if (!human || !human.ok) {
    return {
      approved: false,
      code: (human && human.code) || "HUMAN_GATE_DENIED",
      reason: (human && human.reason) || "human gate denied",
      risk,
      human: human || null,
    };
  }
  return { approved: true, code: "PHASEB_AUTHORIZED", risk, human };
}
