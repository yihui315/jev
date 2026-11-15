/**
 * Pure historical replay — recompute equity_peak / drawdown for ONE
 * (session_id, generation) account in created_at order, and compare with the
 * stored values row by row.
 *
 * Formula (FORMULA_VERSION = "v45-equity-fp/1", all 1e8 fixed-point integers):
 *   runningPeak starts at initialCashFp (the account's initial cash baseline).
 *   For each VERIFIED snapshot: runningPeak = max(runningPeak, equity);
 *     recomputedPeak = runningPeak;
 *     recomputedDd   = peak>0 && equity<peak ? (peak-equity)/peak : 0   [1e8 fp]
 *   For each UNKNOWN snapshot: recomputedPeak = 0, recomputedDd = 0, and it
 *     NEVER updates runningPeak (peaks use VERIFIED snapshots ONLY).
 *
 * This mirrors the frozen computeEquitySnapshot()+collectEquitySnapshots()
 * semantics: historyPeak = MAX(equity) over earlier VERIFIED snapshots of the
 * SAME (session, generation); peak = max(initialCash, historyPeak, equity).
 * Independent accounts are NEVER summed.
 *
 * Pure: no DB, no I/O, deterministic. Input rows are plain objects; the caller
 * may supply `initial_cash_fp` per row (from provenance) or via options.
 */
import { div, sub } from "./vendor/decimal.mjs";

export function replay(snapshotRows, options = {}) {
  const rows = [...snapshotRows].sort((a, b) =>
    String(a.created_at).localeCompare(String(b.created_at)) ||
    String(a.snapshot_id).localeCompare(String(b.snapshot_id))
  );

  let seed = 0n;
  if (options.initialCashFp != null) seed = BigInt(options.initialCashFp);
  else if (rows.length && rows[0].initial_cash_fp != null) seed = BigInt(rows[0].initial_cash_fp);
  let runningPeak = seed;

  const results = [];
  let allMatch = true;
  for (const r of rows) {
    const quality = String(r.mark_quality);
    const equity = BigInt(r.equity || 0);
    let recomputedPeak, recomputedDd;
    if (quality !== "verified") {
      recomputedPeak = 0n;
      recomputedDd = 0n;
    } else {
      if (equity > runningPeak) runningPeak = equity;
      recomputedPeak = runningPeak;
      recomputedDd = runningPeak > 0n && equity < runningPeak
        ? div(sub(runningPeak, equity), runningPeak) : 0n;
    }
    const storedPeak = BigInt(r.equity_peak || 0);
    const storedDd = BigInt(r.drawdown || 0);
    const match = storedPeak === recomputedPeak && storedDd === recomputedDd;
    if (!match) allMatch = false;
    results.push({
      snapshot_id: r.snapshot_id,
      created_at: r.created_at,
      cycle_key: r.cycle_key ?? null,
      mark_quality: quality,
      equity: Number(equity),
      stored_peak: Number(storedPeak),
      recomputed_peak: Number(recomputedPeak),
      stored_dd: Number(storedDd),
      recomputed_dd: Number(recomputedDd),
      match,
    });
  }
  return { seed: Number(seed), count: rows.length, allMatch, rows: results };
}
