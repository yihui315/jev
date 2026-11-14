// index.mjs — 聚合导出（纯 ESM）
export {
  makeDeps, createMemoryFs, createMemoryClock, defaultCycleImpl,
  GENESIS, BASE_DIR, FILES, canonicalize, sha256, resolvePath,
  readJsonl, verifyChain, appendChainEntry, atomicWriteJson, readJson,
} from './core.mjs';
export { createLeaderElection } from './leader-election.mjs';
export { createIdempotency, KIND as LEDGER_KIND } from './idempotency.mjs';
export { createResume } from './resume.mjs';
export { createKillSwitch } from './kill-switch.mjs';
export { createCostMonitor, NOT_MEASURED_NOTE, NO_DATA_NOTE } from './cost-monitor.mjs';
export { createWatchdog } from './watchdog.mjs';
export { createSupervisor, DAEMON_BINDING, STAGES } from './supervisor.mjs';
export {
  createLedgerIo, ABSOLUTE_MAX, CURRENT_MAX, RECOVER,
  MAX_ENTRIES_BEFORE_FULL_REVERIFY, MAX_BYTES_BEFORE_FULL_REVERIFY,
  MAX_OPS_BEFORE_FULL_REVERIFY,
} from './ledger-io.mjs';
