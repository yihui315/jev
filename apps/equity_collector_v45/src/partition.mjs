/**
 * V1.6 / M3 - Partitioned history-peak query (contract 3.4).
 *
 * The OLD V1.5 query was "SELECT MAX(equity) ... WHERE mark_quality='verified'"
 * with NO market partition. That let a row from a different (exchange,market)
 * or an unprovable history row pollute peak / drawdown. This query is the
 * CORE fix: peak is computed ONLY over rows of the SAME (exchange,market),
 * with proven VERIFIED data, NONE failure_class, a verified price_ts_source,
 * and a non-UNKNOWN network_context.
 *
 * HISTORY_UNPROVEN rows (from migration 0002) are excluded by failure_class.
 * received_at / UNKNOWN price_ts_source and UNKNOWN network_context are
 * excluded explicitly. No market-mixing, no history contamination.
 */
export const HISTORY_PEAK_SQL = `SELECT MAX(s.equity) AS peak
  FROM paper_equity_snapshots s
  JOIN v45_snapshot_provenance p ON p.snapshot_id = s.snapshot_id
 WHERE s.session_id = ? AND s.generation = ? AND s.mark_quality = 'verified'
   AND p.data_quality = 'VERIFIED'
   AND p.exchange = ? AND p.market = ?
   AND p.failure_class = 'NONE'
   AND p.price_ts_source IN ('exchange_serverTime','trade_time','http_date')
   AND p.network_context <> 'UNKNOWN'`;

export async function selectHistoryPeak(db, { sessionId, generation, exchange, market }) {
  const row = await db.prepare(HISTORY_PEAK_SQL)
    .bind(String(sessionId), Number(generation), String(exchange), String(market))
    .first();
  return row ? row.peak : null;
}
