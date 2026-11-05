/**
 * trading/components/PageContainer — the page frame used by every trading view.
 *
 * Reuses the existing shell primitives (PageBody / PageHeader) so trading pages
 * share the product's rhythm. Kept tiny and dependency-light.
 */
import type { ReactNode } from "react";
import { PageBody, PageHeader } from "../../../components/AppShell";

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
      {children}
    </PageBody>
  );
}
