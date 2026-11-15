// JEV Phase C — Per-provider circuit breaker (state machine).
//
// MOCK-ONLY (no network). Protects the gateway from hammering a failing backend.
// States: CLOSED -> OPEN (after failureThreshold failures) -> HALF_OPEN (after
// cooldownMs) -> CLOSED (success) / re-OPEN (failure). A clock is injectable so
// tests are deterministic without real sleeps.

export const BREAKER_STATES = Object.freeze({
  CLOSED: "CLOSED",
  OPEN: "OPEN",
  HALF_OPEN: "HALF_OPEN",
});

/**
 * @param {object} [opts]
 * @param {number} [opts.failureThreshold=3] failures (within CLOSED) to trip
 * @param {number} [opts.cooldownMs=5000]   OPEN duration before HALF_OPEN
 * @param {number} [opts.halfOpenMax=1]     trials permitted in HALF_OPEN
 * @param {Function} [opts.clock]           () => number(ms) injectable
 */
export function createCircuitBreaker(opts = {}) {
  const failureThreshold = Number.isFinite(opts.failureThreshold) ? opts.failureThreshold : 3;
  const cooldownMs = Number.isFinite(opts.cooldownMs) ? opts.cooldownMs : 5000;
  const halfOpenMax = Number.isFinite(opts.halfOpenMax) ? opts.halfOpenMax : 1;
  const clock = typeof opts.clock === "function" ? opts.clock : () => Date.now();

  let state = BREAKER_STATES.CLOSED;
  let failures = 0;
  let openedAt = 0;
  let halfOpenTrials = 0;

  function trip() {
    state = BREAKER_STATES.OPEN;
    openedAt = clock();
    failures = 0;
  }

  return {
    state: () => state,

    /** Record a successful call. Half-open success closes the breaker. */
    recordSuccess() {
      if (state === BREAKER_STATES.HALF_OPEN) {
        state = BREAKER_STATES.CLOSED;
        failures = 0;
        halfOpenTrials = 0;
      } else if (state === BREAKER_STATES.CLOSED) {
        failures = 0;
      }
    },

    /** Record a failed call. Trips to OPEN on threshold; re-trips from HALF_OPEN. */
    recordFailure() {
      if (state === BREAKER_STATES.OPEN) return; // already open; ignore
      if (state === BREAKER_STATES.HALF_OPEN) {
        trip();
        return;
      }
      failures += 1;
      if (failures >= failureThreshold) trip();
    },

    /**
     * Decide whether a new call is permitted. Transitions OPEN -> HALF_OPEN
     * when cooldown elapses. Returns true if the call should proceed.
     */
    allow() {
      if (state === BREAKER_STATES.CLOSED) return true;
      if (state === BREAKER_STATES.OPEN) {
        if (clock() - openedAt >= cooldownMs) {
          state = BREAKER_STATES.HALF_OPEN;
          halfOpenTrials = 0;
          return true;
        }
        return false;
      }
      // HALF_OPEN
      if (halfOpenTrials < halfOpenMax) {
        halfOpenTrials += 1;
        return true;
      }
      return false;
    },

    openUntilMs: () => (state === BREAKER_STATES.OPEN ? openedAt + cooldownMs : 0),
    _debug: () => ({ state, failures, openedAt }),
  };
}
