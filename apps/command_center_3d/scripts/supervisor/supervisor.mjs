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
// The order below is the safety property. Two stages are new for a reason:
//   leaseEpoch  — the term this cycle runs under. It is declared after every
//                 read-only decision, so the ledger only grows when work is
//                 actually about to start: a caller that keeps re-presenting a
//                 duplicate or an in-doubt cycle must not be able to grow the
//                 ledger, because `entries()` re-reads the whole file.
//   cycleIntent — WRITE-AHEAD. It lands on disk (fsync'd) BEFORE cycleImpl, so the
//                 window between "side effect happened" and "ledger knows" is empty
//                 instead of silent. See D-14-0.
// Reads strictly precede writes in this list; that is checkable by eye and is the
// reason `idempotency` and `resume` sit before `leaseEpoch`.
//
// THIS NINE-STAGE LIST IS A NEW FROZEN SET. The earlier seven-stage list (and the
// ten-stage list that was proposed before this landed) are both void; there was
// never a ten-stage list in code. What the freeze means, and what it does NOT:
//   * `trace` records HOW FAR a cycle got. It does not record WHY it stopped.
//     DUPLICATE and IN_DOUBT both leave the same three-element prefix
//     ['killSwitch','lease','idempotency'], because both are read-only decisions.
//   * IN_DOUBT's observability is therefore NOT carried by `trace`. It is carried
//     by the ledger, and ⑲f pins both halves: the trace prefix (3 stages = no write
//     stage was entered) and the record count (2, unchanged).
//   * Stage names are never evidence of stage ORDER. Order is evidenced from inside
//     the side effect, by reading the disk (⑲e). Any assertion of the form
//     STAGES.indexOf(a) < STAGES.indexOf(b) compares one declaration to another.
export const STAGES = Object.freeze([
  'killSwitch', 'lease', 'idempotency', 'resume', 'leaseEpoch',
  'cycleIntent', 'cycleImpl', 'record', 'heartbeat',
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
      // 阶段 3：idempotency（只读判定：先弄清这个周期到底处于什么状态）
      trace.push('idempotency');
      const state = idempotency.cycleState(runId, cycle);
      if (state === 'COMPLETED') {
        return {
          status: 'DUPLICATE', runId, cycle, cycleExecuted: false,
          daemonBinding: DAEMON_BINDING, trace,
          note: 'duplicate (runId, cycle) rejected; cycle body not run',
        };
      }
      // IN_DOUBT is NOT a failure and NOT a retry. The body may already have run,
      // so re-running would turn at-most-once into at-least-once; reporting
      // cycleExecuted:false alone would be a lie, so the possibility is named.
      if (state === 'IN_DOUBT') {
        return {
          status: 'IN_DOUBT', runId, cycle, cycleExecuted: false,
          sideEffectMayHaveOccurred: true,
          daemonBinding: DAEMON_BINDING, trace,
          note: 'write-ahead intent exists with no completion; the cycle body may already '
            + 'have run. Refusing to re-run (at-most-once); needs an explicit operator decision.',
        };
      }

      // 阶段 4：resume（全量校验链；损坏则 throw，绝不执行周期）
      trace.push('resume');
      const resumeInfo = resume.resume(runId);

      // 阶段 5：leaseEpoch（栅栏令牌）。放在这里而不是租约之后，是为了让
      // "重复 / in-doubt" 这两条早退路径一条记录都不写 —— entries() 每次全量
      // 重读账本，一个不前进的调用方不得靠重复提交把账本撑大。
      trace.push('leaseEpoch');
      const epochRecord = idempotency.recordLeaseEpoch({
        runId, ownerToken, acquiredAtMs: lease.acquiredAtMs,
      });
      const epoch = epochRecord.epoch;

      // 阶段 6：cycleIntent（写前意图，fsync 之后才允许副作用）
      trace.push('cycleIntent');
      idempotency.recordIntent(runId, cycle, { epoch });

      // 阶段 7：cycleImpl（默认纯内存 no-op fixture）
      trace.push('cycleImpl');
      const cycleResult = await d.cycleImpl({ runId, cycle, epoch, nowMs: d.nowMs() });

      // 阶段 8：record
      trace.push('record');
      idempotency.recordCompletion(runId, cycle, {
        cycleResultLabel: cycleResult && cycleResult.dataLabel,
      });

      // 阶段 9：heartbeat
      trace.push('heartbeat');
      watchdog.beat({ runId, cycle });

      return {
        status: 'COMPLETED', runId, cycle, cycleExecuted: true, epoch,
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
