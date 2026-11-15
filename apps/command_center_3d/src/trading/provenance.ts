/**
 * Status-label derivation for the ACTIVE trading terminal (src/trading/**).
 *
 * This helper is intentionally self-contained. The legacy C system derives its
 * label through an offline-mock short-circuit that unconditionally reports a
 * fixture tag; that switch does not apply here, because every page in this
 * terminal reads real /api/jev/* data through useJevQuery. The label must be
 * derived purely from the adapter's provenance envelope, fail-closed.
 *
 * Fail-closed rules (verbatim from the P7 derivation, minus the mock
 * short-circuit and the authoritative-source constant):
 *  - transport failure / missing meta               -> "NO_DATA"
 *  - BACKFILL stays BACKFILL (never upgraded to LIVE)
 *  - NO_DATA / UNAVAILABLE / BLOCKED / VERIFIED are returned as-is
 *  - LIVE only with complete meta provenance AND a named source
 *  - CONNECTED is NOT produced here: it is a provider-test verdict, never a
 *    market-data label
 */
export function deriveStatusLabel(q: {
  unreachable?: boolean;
  error?: string;
  meta?: {
    label?: string;
    source?: string;
    channel?: string;
    generated_utc?: string;
    fetched_at_utc?: string;
    http_status?: number;
  };
}): string {
  if (q.unreachable || q.error || !q.meta) return "NO_DATA";
  const m = q.meta;
  if (m.label === "BACKFILL") return "BACKFILL";
  if (m.label === "NO_DATA" || m.label === "UNAVAILABLE" || m.label === "BLOCKED") return m.label;
  if (m.label === "VERIFIED") return "VERIFIED";
  const provenanceComplete = Boolean(
    m.source && m.channel && (m.generated_utc || m.fetched_at_utc) && m.http_status !== undefined,
  );
  const sourceNamed = Boolean(m.source);
  if (m.label === "LIVE" && provenanceComplete && sourceNamed) return "LIVE";
  return "NO_DATA";
}
