// JEV Phase C — Explicit model-switch logger.
//
// MOCK-ONLY. Captures every routing decision (policy select, explicit override,
// fallback due to circuit-open or retry-exhausted) as a structured entry. The
// log is the audit trail of WHY a given request landed on a given model. Stored
// in memory; entries are frozen on insert.

export function createSwitchLogger() {
  const log = [];

  function log_(entry) {
    const rec = Object.freeze({
      atMs: entry.atMs ?? null,
      from: entry.from ?? null,
      to: entry.to ?? null,
      reason: entry.reason ?? null,
      policy: entry.policy ?? null,
    });
    log.push(rec);
    return rec;
  }

  return {
    log: log_,
    all() {
      return log.slice();
    },
    count() {
      return log.length;
    },
    clear() {
      log.length = 0;
    },
  };
}
