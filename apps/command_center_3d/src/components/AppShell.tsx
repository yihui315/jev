/**
 * AppShell — the frame every page renders inside.
 *
 * One shell owns the four things that must never vary per page: the navigation
 * rail, the header with the system status, the stale-data banner, and the
 * responsive drawer behaviour. Pages supply their own content and nothing else,
 * which is what keeps fourteen surfaces from each re-implementing a layout.
 *
 * RESPONSIVE
 * ----------
 * Desktop-first, as specified. The rail is pinned from `lg` up; below it the
 * same registry becomes an off-canvas drawer, so a phone can still check task
 * status without a separate mobile product.
 */
import type { ReactNode } from "react";
import { cn } from "../ui/cn";

export interface AppShellProps {
  sidebar: ReactNode;
  header: ReactNode;
  children: ReactNode;
  /** A thin banner under the header (stale data, offline, approval pending). */
  banner?: ReactNode;
  /** The right-hand inspector rail (session detail). Expert surfaces only. */
  aside?: ReactNode;
  className?: string;
}

export function AppShell({
  sidebar,
  header,
  children,
  banner,
  aside,
  className,
}: AppShellProps) {
  return (
    <div className={cn("flex h-screen w-screen overflow-hidden bg-background text-foreground", className)}>
      {sidebar}

      <div className="flex min-w-0 flex-1 flex-col">
        {header}
        {banner}
        <main className="relative min-h-0 flex-1">{children}</main>
      </div>

      {aside}
    </div>
  );
}

/**
 * The standard scrolling page body.
 *
 * Centred and width-capped so a line of text never runs the full width of a
 * 1920px monitor — the single most reliable way to make a dense product feel
 * calm. `wide` is for surfaces that genuinely need the room (tables, the 3D
 * world, the command palette).
 */
export function PageBody({
  children,
  wide = false,
  className,
}: {
  children: ReactNode;
  wide?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("page-scroll", className)}>
      <div className={cn("mx-auto w-full px-6 py-8 lg:px-10", wide ? "max-w-none" : "max-w-5xl")}>
        {children}
      </div>
    </div>
  );
}

/** The page title block. One component so every page has the same rhythm. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 space-y-1.5">
        <h1 className="t-page text-foreground">{title}</h1>
        {description ? <div className="t-body text-muted-foreground">{description}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}
