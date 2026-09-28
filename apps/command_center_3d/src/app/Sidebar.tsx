/**
 * Sidebar — the product navigation rail.
 *
 * Reads its entries from the single `navigation.ts` registry, so it can never
 * drift from the ⌘K palette or the mobile drawer. The attention count is passed
 * in (not fetched here) because the app shell owns the one poll that produces
 * it — a second fetch inside the sidebar would be a second source of truth.
 */
import { AlertTriangle, Command as CommandIcon, X } from "lucide-react";
import {
  NAV_GROUPS,
  navItemsInGroup,
  type NavGroup,
  type ViewMode,
} from "../app/navigation";
import { cn } from "../ui/cn";
import { Badge } from "../ui/Badge";

export function Sidebar({
  view,
  onSelect,
  attentionCount,
  attentionUnknown,
  onOpenPalette,
  mobileOpen,
  onCloseMobile,
  className,
}: {
  view: ViewMode;
  onSelect: (v: ViewMode) => void;
  attentionCount: number | null;
  attentionUnknown: boolean;
  onOpenPalette: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  className?: string;
}) {
  return (
    <>
      {/* Mobile scrim */}
      <div
        className={cn(
          "fixed inset-0 z-30 bg-black/60 lg:hidden",
          mobileOpen ? "block" : "hidden",
        )}
        onClick={onCloseMobile}
        aria-hidden="true"
      />
      <nav
        aria-label="Product navigation"
        className={cn(
          "z-40 flex h-full w-60 shrink-0 flex-col border-r border-border bg-card",
          // Off-canvas below lg, pinned from lg up.
          "fixed lg:static",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
          "transition-transform duration-200 ease-out",
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-foreground">
              JEV Command Center
            </div>
            <div className="truncate text-[10px] uppercase tracking-wider text-muted-foreground">
              AI Work OS · v1.3
            </div>
          </div>
          <button
            onClick={onCloseMobile}
            className="rounded-md p-1 text-muted-foreground hover:bg-accent lg:hidden"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-3 pt-3">
          <button
            onClick={onOpenPalette}
            className="flex w-full items-center gap-2 rounded-md border border-border bg-background/50 px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            aria-label="Open command palette"
          >
            <CommandIcon className="h-3.5 w-3.5" />
            <span className="flex-1 text-left">Command palette</span>
            <kbd className="rounded border border-border px-1 py-0.5 text-[10px]">
              ⌘K
            </kbd>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-3">
          {NAV_GROUPS.map((group: NavGroup) => (
            <div key={group} className="mb-3">
              <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {group}
              </div>
              <ul className="space-y-0.5">
                {navItemsInGroup(group)
                  .filter((n) => n.inSidebar)
                  .map((item) => {
                    const Icon = item.icon;
                    const active = view === item.id;
                    const isAttention = item.id === "needs-attention";
                    return (
                      <li key={item.id}>
                        <button
                          onClick={() => {
                            onSelect(item.id);
                            onCloseMobile();
                          }}
                          title={item.description}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                            active
                              ? "bg-accent text-accent-foreground"
                              : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                          )}
                        >
                          <Icon
                            className={cn(
                              "h-4 w-4 shrink-0",
                              active ? "text-primary" : "text-muted-foreground",
                            )}
                          />
                          <span className="flex-1 truncate">{item.label}</span>
                          {isAttention ? (
                            <AttentionCountBadge
                              count={attentionCount}
                              unknown={attentionUnknown}
                            />
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
              </ul>
            </div>
          ))}
        </div>

        <div className="border-t border-border px-4 py-2 text-[10px] text-muted-foreground">
          Loopback only · actuation disabled
        </div>
      </nav>
    </>
  );
}

/**
 * The attention count.
 *
 * When the count could not be loaded we render a grey "?" — NOT a zero and NOT
 * nothing. "We could not ask" and "there are none" are different facts, and a
 * silent zero is exactly how an alert gets missed.
 */
function AttentionCountBadge({
  count,
  unknown,
}: {
  count: number | null;
  unknown: boolean;
}) {
  if (unknown || count == null) {
    return (
      <Badge variant="unknown" aria-label="Attention count unknown" title="Attention count unavailable">
        ?
      </Badge>
    );
  }
  if (count === 0) return null;
  return (
    <Badge variant="warn" aria-label={`${count} items need attention`}>
      <AlertTriangle className="h-3 w-3" />
      {count}
    </Badge>
  );
}
