/**
 * trading/components/PageContainer — the page frame used by every trading view.
 *
 * Reuses the existing shell primitives (PageBody / PageHeader) so trading pages
 * share the product's rhythm. Kept tiny and dependency-light.
 */
import type { ReactNode } from "react";
import { PageBody, PageHeader } from "../../../components/AppShell";
import { USE_MOCK } from "../services/paperApi";
import { DataModeBadge } from "./DataModeBadge";

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
        </div>
      )}
      {children}
      <div className="mt-4 flex justify-end">
        <DataModeBadge />
      </div>
    </PageBody>
  );
}
