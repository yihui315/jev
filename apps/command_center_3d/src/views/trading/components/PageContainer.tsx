/**
 * trading/components/PageContainer — the page frame used by every trading view.
 *
 * Reuses the existing shell primitives (PageBody / PageHeader) so trading pages
 * share the product's rhythm. Kept tiny and dependency-light.
 */
import type { ReactNode } from "react";
import { PageBody, PageHeader } from "../../../components/AppShell";
import { ProvenanceBadge } from "../../../components/ProvenanceBadge";
import { USE_MOCK, MARKET_DATA_SOURCE } from "../services/paperApi";
import { DataModeBadge } from "./DataModeBadge";

/**
 * P7 — provenance-driven status label for the trading panels.
 * A hardcoded `label="LIVE"` is forbidden: the tag is derived from the
 * adapter's provenance envelope, the offline mock switch and the named
 * authoritative market-data source.
 *
 * Fail-closed rules (team-lead adjudication):
 *  - USE_MOCK === true  -> "FIXTURE / NOT LIVE" (offline simulation is NOT real)
 *  - transport failure / missing meta -> "NO_DATA"
 *  - BACKFILL stays BACKFILL (never upgraded to LIVE)
 *  - LIVE only with complete meta provenance AND a named authoritative source
 *  - CONNECTED is NOT produced here: it is a provider-test verdict
 *    (POST /api/jev/ai/providers/test), never a market-data label.
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
  if (USE_MOCK) return "FIXTURE / NOT LIVE";
  if (q.unreachable || q.error || !q.meta) return "NO_DATA";
  const m = q.meta;
  if (m.label === "BACKFILL") return "BACKFILL";
  if (m.label === "NO_DATA" || m.label === "UNAVAILABLE" || m.label === "BLOCKED") return m.label;
  if (m.label === "VERIFIED") return "VERIFIED";
  const provenanceComplete = Boolean(
    m.source && m.channel && (m.generated_utc || m.fetched_at_utc) && m.http_status !== undefined,
  );
  const sourceNamed = Boolean(MARKET_DATA_SOURCE);
  if (m.label === "LIVE" && provenanceComplete && sourceNamed) return "LIVE";
  return "NO_DATA";
}

export function PageContainer({
  title,
  description,
  actions,
  wide = true,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}) {
  // `jev-trading` scopes the V4.0 navy/cyan institutional theme to the trading
  // subsystem only — JEV's global token layer is never touched.
  return (
    <PageBody wide={wide} className="jev-trading">
      <PageHeader title={title} description={description} actions={actions} />
      {USE_MOCK && (
        <div
          role="status"
          className="mb-3 flex items-center gap-2 rounded border border-status-warn/40 bg-status-warn/10 px-3 py-2 t-meta text-status-warn"
        >
          <span className="h-2 w-2 shrink-0 rounded-full bg-status-warn" />
          模拟数据模式：账户、订单、成交、行情均为离线模拟（USE_MOCK=true），并非真实交易接口返回。接入真实 Paper API 需将 USE_MOCK 设为 false 并启动 Night Researcher 后端。
          {/* P7 / B.4: explicit fixture provenance beside the existing mock banner. */}
          <ProvenanceBadge provenance={{ isReal: false, dataMode: "fixture", captureMode: "mock" }} />
        </div>
      )}
      {children}
      <div className="mt-4 flex justify-end">
        <DataModeBadge />
      </div>
    </PageBody>
  );
}
