/**
 * Sidebar — the product navigation rail.
 *
 * Reads its entries from the single `navigation.ts` registry, so it can never
 * drift from the command palette. In Simple Mode it renders exactly four
 * entries; Expert Mode adds the engineering surfaces underneath, visually
 * separated, so the professional capability is one toggle away rather than
 * deleted.
 *
 * The attention count is passed in (not fetched here) because the app shell owns
 * the one poll that produces it — a second fetch inside the sidebar would be a
 * second source of truth.
 */
import { AlertTriangle, Command as CommandIcon, X } from "lucide-react";
import { NAV_GROUPS, navItemsInGroup, type NavGroup, type ViewMode } from "../app/navigation";
import { cn } from "../ui/cn";
import { Badge } from "../ui/Badge";

/** Group headings, in the user's language. */
const GROUP_LABEL: Record<NavGroup, string> = {
  Main: "",
  Work: "任务相关",
  System: "高级",
};

export function Sidebar({
  view,
  expert,
  onSelect,
  attentionCount,
  attentionUnknown,
  onOpenPalette,
  mobileOpen,
  onCloseMobile,
  className,
}: {
  view: ViewMode;
  expert: boolean;
  onSelect: (v: ViewMode) => void;
  attentionCount: number | null;
  attentionUnknown: boolean;
  onOpenPalette: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  className?: string;
}) {
  // A group with nothing visible in the current mode is not rendered at all —
  // an empty "高级" heading in Simple Mode would imply missing functionality.
  const groups = NAV_GROUPS.map((g) => ({ group: g, items: navItemsInGroup(g, expert) })).filter(
    (g) => g.items.some((n) => n.inSidebar),
  );

  return (
    <>
      <div
        className={cn(
          "fixed inset-0 z-30 bg-black/60 transition-opacity lg:hidden",
          mobileOpen ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        onClick={onCloseMobile}
        aria-hidden="true"
      />
      <nav
        aria-label="产品导航"
        className={cn(
          "z-40 flex h-full w-60 shrink-0 flex-col border-r border-border bg-card",
          "fixed lg:static",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
          "transition-transform duration-200 ease-out",
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2 px-5 py-5">
          <div className="min-w-0">
            <div className="truncate text-[15px] font-semibold tracking-tight text-foreground">
              JEV
            </div>
            <div className="truncate t-meta text-muted-foreground">AI 工作系统</div>
          </div>
          <button
            onClick={onCloseMobile}
            className="rounded-md p-1.5 text-muted-foreground hover:bg-accent lg:hidden"
            aria-label="关闭导航"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-4 pb-2">
          <button
            onClick={onOpenPalette}
            className="flex w-full items-center gap-2 rounded-md border border-border bg-background/40 px-2.5 py-2 t-meta text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            aria-label="打开命令面板"
          >
            <CommandIcon className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="flex-1 text-left">命令面板</span>
            <kbd className="rounded border border-border px-1 py-0.5 text-[10px]">⌘K</kbd>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3">
          {groups.map(({ group, items }) => {
            const heading = GROUP_LABEL[group];
            return (
              <div key={group} className={cn(group !== "Main" && "mt-5")}>
                {heading ? (
                  <div className="px-2.5 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
                    {heading}
                  </div>
                ) : null}
                <ul className="space-y-0.5">
                  {items
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
                              "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[14px] transition-colors",
                              active
                                ? "bg-primary-soft font-medium text-foreground"
                                : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                            )}
                          >
                            <Icon
                              className={cn(
                                "h-4 w-4 shrink-0",
                                active ? "text-primary" : "text-muted-foreground",
                              )}
                              aria-hidden="true"
                            />
                            <span className="flex-1 truncate">
                              {expert ? item.label : item.labelZh}
                            </span>
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
            );
          })}
        </div>

        <div className="border-t border-border px-5 py-3 t-meta text-muted-foreground/80">
          {expert ? "Loopback only · actuation disabled" : "仅本机运行"}
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
      <Badge variant="unknown" aria-label="待确认数量未知" title="无法获取待确认数量">
        ?
      </Badge>
    );
  }
  if (count === 0) return null;
  return (
    <Badge variant="warn" aria-label={`${count} 项需要确认`}>
      <AlertTriangle className="h-3 w-3" aria-hidden="true" />
      {count}
    </Badge>
  );
}
