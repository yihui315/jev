// index.mjs — 聚合导出（纯 ESM）
export {
  makeDeps, createMemoryFs, createMemoryClock, defaultCycleImpl,
  GENESIS, BASE_DIR, FILES, canonicalize, sha256, resolvePath,
  readJsonl, verifyChain, appendChainEntry, atomicWriteJson, readJson,
} from './core.mjs';
export { createLeaderElection } from './leader-election.mjs';
export { createIdempotency } from './idempotency.mjs';
export { createResume } from './resume.mjs';
export { createKillSwitch } from './kill-switch.mjs';
export { createCostMonitor, NOT_MEASURED_NOTE, NO_DATA_NOTE } from './cost-monitor.mjs';
export { createWatchdog } from './watchdog.mjs';
export { createSupervisor, DAEMON_BINDING, STAGES } from './supervisor.mjs';
