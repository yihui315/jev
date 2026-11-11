// supervisor.mjs — 24/7 Supervisor 可验证本地骨架的编排入口
// 明确登记：本轮仅落地可验证本地骨架，24/7 守护进程未接线、未启动。
import { makeDeps } from './core.mjs';
import { createLeaderElection } from './leader-election.mjs';
import { createIdempotency } from './idempotency.mjs';
import { createResume } from './resume.mjs';
import { createKillSwitch } from './kill-switch.mjs';
import { createCostMonitor } from './cost-monitor.mjs';
import { createWatchdog } from './watchdog.mjs';

export const DAEMON_BINDING = 'DECLARED_NOT_WIRED';
export const STAGES = Object.freeze([
  'killSwitch', 'lease', 'idempotency', 'resume', 'cycleImpl', 'record', 'heartbeat',
]);

export function createSupervisor(deps) {
  const d = deps || makeDeps();
  const killSwitch = createKillSwitch(d);
  const election = createLeaderElection(d);
  const idempotency = createIdempotency(d);
  const resume = createResume(d);
  const costMonitor = createCostMonitor(d);
  const watchdog = createWatchdog(d);
  const ownerToken = d.ownerToken;

  async function runCycle(args) {
    const runId = args.runId;
    const cycle = args.cycle;
    if (!Number.isInteger(cycle)) throw new Error('CYCLE_MUST_BE_INTEGER');
    const trace = [];

    // 阶段 1：killSwitch
    trace.push('killSwitch');
    if (killSwitch.isHalted()) {
      return {
        status: 'HALTED', runId, cycle, cycleExecuted: false,
        daemonBinding: DAEMON_BINDING, trace,
        note: 'kill switch engaged; no cycle body run; never auto-cleared',
      };
    }

    // 阶段 2：lease
    trace.push('lease');
    const lease = election.acquireLease({ ownerToken });
    if (!lease.acquired) {
      return {
        status: 'NO_LEASE', runId, cycle, cycleExecuted: false,
        daemonBinding: DAEMON_BINDING, trace, lease,
      };
    }

    try {
      // 阶段 3：idempotency
      trace.push('idempotency');
      if (idempotency.hasCompleted(runId, cycle)) {
        return {
          status: 'DUPLICATE', runId, cycle, cycleExecuted: false,
          daemonBinding: DAEMON_BINDING, trace,
          note: 'duplicate (runId, cycle) rejected; cycle body not run',
        };
      }

      // 阶段 4：resume（全量校验链；损坏则 throw，绝不执行周期）
      trace.push('resume');
      const resumeInfo = resume.resume(runId);

      // 阶段 5：cycleImpl（默认纯内存 no-op fixture）
      trace.push('cycleImpl');
      const cycleResult = await d.cycleImpl({ runId, cycle, nowMs: d.nowMs() });

      // 阶段 6：record
      trace.push('record');
      idempotency.recordCompletion(runId, cycle, {
        cycleResultLabel: cycleResult && cycleResult.dataLabel,
      });

      // 阶段 7：heartbeat
      trace.push('heartbeat');
      watchdog.beat({ runId, cycle });

      return {
        status: 'COMPLETED', runId, cycle, cycleExecuted: true,
        daemonBinding: DAEMON_BINDING, trace,
        lease, lastCycle: resumeInfo.lastCycle, cycleResult,
      };
    } finally {
      // 每个周期结束释放自己持有的租约（内部校验 ownerToken）。
      election.releaseLease(ownerToken);
    }
  }

  async function runNextCycle(args) {
    const info = resume.resume(args.runId);   // fail-closed 校验链
    return runCycle({ runId: args.runId, cycle: info.nextCycle });
  }

  return {
    runCycle, runNextCycle,
    resume: (runId) => resume.resume(runId),
    subsystems: { killSwitch, election, idempotency, resume, costMonitor, watchdog },
    constants: { DAEMON_BINDING, STAGES },
  };
}
