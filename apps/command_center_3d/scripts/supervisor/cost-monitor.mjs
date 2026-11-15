// cost-monitor.mjs — 累计预算监控，三口径（budget / estimated / actual）严格分列
import { FILES, resolvePath, readJson, atomicWriteJson } from './core.mjs';

export const NOT_MEASURED_NOTE =
  'UNKNOWN, never 0 — actual cost is NOT_MEASURED; budget/estimated are separate columns and are NEVER backfilled into actual';
export const NO_DATA_NOTE =
  'UNKNOWN, never 0 — 0 samples; NO_DATA is reported instead of 0';

function blankColumn(status, note) {
  return { amount: null, status, note };
}

export function createCostMonitor(deps, options = {}) {
  const { fsImpl, baseDir, nowMs } = deps;
  const statePath = resolvePath(baseDir, FILES.costState);
  const budgetLimit = options.budgetLimit ?? null;   // null = 未设定，绝不写 0

  function load() {
    const s = readJson(fsImpl, statePath, null);
    if (!s || s.version !== 1) {
      return { version: 1, budgetLimit, sampleCount: 0, samples: [], updatedAtMs: null };
    }
    return s;
  }
  let state = load();

  function persist() { atomicWriteJson(fsImpl, statePath, state); }

  function sumColumn(key) {
    const vals = state.samples
      .map((s) => s[key])
      .filter((v) => typeof v === 'number' && Number.isFinite(v));
    if (vals.length === 0) return null;
    return vals.reduce((a, b) => a + b, 0);
  }

  function record(entry = {}) {
    const budget = typeof entry.budget === 'number' && Number.isFinite(entry.budget) ? entry.budget : null;
    const estimated = typeof entry.estimated === 'number' && Number.isFinite(entry.estimated) ? entry.estimated : null;
    const actualMeasured = typeof entry.actual === 'number' && Number.isFinite(entry.actual);
    const actual = actualMeasured ? entry.actual : null;   // 未实测一律 null，绝不写 0、绝不回填
    state.sampleCount += 1;
    state.samples.push({
      index: state.sampleCount,
      budget,
      estimated,
      actual,
      actualMeasured,
      atMs: nowMs(),
    });
    state.updatedAtMs = nowMs();
    persist();
    return snapshot();
  }

  function snapshot() {
    const n = state.sampleCount;
    if (n === 0) {
      const col = () => blankColumn('NO_DATA', NO_DATA_NOTE);
      return {
        status: 'NO_DATA',
        sampleCount: 0,
        budgetLimit: state.budgetLimit,
        budget: col(),
        estimated: col(),
        actual: col(),
        note: NO_DATA_NOTE,
        columns: ['budget', 'estimated', 'actual'],
        merged: false,
      };
    }
    const budgetSum = sumColumn('budget');
    const estimatedSum = sumColumn('estimated');
    const actualSum = sumColumn('actual');
    const measuredCount = state.samples.filter((s) => s.actualMeasured).length;

    const budget = budgetSum === null
      ? blankColumn('NOT_SET', NO_DATA_NOTE)
      : { amount: budgetSum, status: 'BUDGET_SET', note: 'budget column only; never merged into actual' };
    const estimated = estimatedSum === null
      ? blankColumn('NOT_MEASURED', NOT_MEASURED_NOTE)
      : { amount: estimatedSum, status: 'ESTIMATED', note: 'estimated column only; never merged into actual' };
    const actual = measuredCount === 0 || actualSum === null
      ? blankColumn('NOT_MEASURED', NOT_MEASURED_NOTE)
      : { amount: actualSum, status: 'MEASURED', measuredCount, note: 'actual column; independently measured' };

    return {
      status: 'OK',
      sampleCount: n,
      budgetLimit: state.budgetLimit,
      budget, estimated, actual,
      note: 'three columns are strictly separated (budget / estimated / actual)',
      columns: ['budget', 'estimated', 'actual'],
      merged: false,
    };
  }

  // 断言三口径未合并、未实测未写 0、预算/估算未回填进实测；任一违反即抛错。
  function assertNoMerge(report) {
    if (!report || typeof report !== 'object') throw new Error('COST_ASSERT_INVALID_REPORT');
    // (1) 不允许单一合并字段
    if (report.cost !== undefined || report.amount !== undefined || report.total !== undefined) {
      throw new Error('COST_MERGE_VIOLATION: single merged cost field');
    }
    // (2) 三列必须独立存在
    for (const key of ['budget', 'estimated', 'actual']) {
      if (!report[key] || typeof report[key] !== 'object') {
        throw new Error('COST_MERGE_VIOLATION: missing column ' + key);
      }
    }
    const a = report.actual;
    const isUnmeasured = a.status === 'NOT_MEASURED' || a.status === 'NO_DATA';
    // (3) 回填检测优先：未实测 actual 金额不得等于预算 / 估算（全为 null 属合法）
    if (isUnmeasured && (a.amount === report.budget.amount || a.amount === report.estimated.amount)) {
      if (!(a.amount === null && report.budget.amount === null && report.estimated.amount === null)) {
        throw new Error('COST_MERGE_VIOLATION: budget/estimated backfilled into actual');
      }
    }
    // (4) 未实测一律 null，绝不写 0；NOT_MEASURED 必须带 note
    if (isUnmeasured) {
      if (a.amount !== null) throw new Error('COST_MERGE_VIOLATION: unmeasured actual not null');
      if (a.status === 'NOT_MEASURED' && !String(a.note || '').includes('NOT_MEASURED')) {
        throw new Error('COST_MERGE_VIOLATION: NOT_MEASURED note missing');
      }
    }
    // (5) 任何未实测 / 无数据列不得写 0
    for (const key of ['budget', 'estimated', 'actual']) {
      const c = report[key];
      const blank = c.status === 'NOT_MEASURED' || c.status === 'NO_DATA' || c.status === 'NOT_SET';
      if (blank && c.amount === 0) throw new Error('COST_MERGE_VIOLATION: 0 written into ' + key);
    }
    return true;
  }

  return {
    record, snapshot, assertNoMerge,
    reload: () => { state = load(); return state; },
    statePath,
  };
}
