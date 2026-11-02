/**
 * TopStatus — the header, and the product's one piece of deliberate restraint.
 *
 * THE RULE IT ENCODES
 * -------------------
 * A new user does not know what "SSE" or "WorkBuddy probe" means, so the header
 * says one thing: 系统正常. Everything technical is one click away, behind a
 * popover, and only shown when the system has actually reported it.
 *
 * THE HONESTY RULE
 * ----------------
 * The green dot is the strongest claim in the product, so it has to be earned.
 * `系统正常` is shown ONLY when every probe actually succeeded. When a probe
 * failed, or could not run, the header says so in the same weight — an amber
 * "部分异常" and a grey "状态未知" are both real answers. A degraded backend must
 * never be able to render as a healthy one, because the whole point of the
 * status indicator is to be believed.
 */
import { useEffect, useRef, useState } from "react";
import { Activity, ChevronDown, Cpu, Menu, RefreshCw, Shield } from "lucide-react";
import { cn } from "../ui/cn";

/** One measured probe. `null` means the probe did not answer — not that it passed. */
export interface StatusProbe {
  label: string;
  /** `ok` passed, `bad` failed, `unknown` did not answer. */
  state: "ok" | "bad" | "unknown";
  detail: string;
}

export type SystemHealth = "ok" | "degraded" | "unknown";

/**
 * Compose the headline verdict from the probes.
 *
 * This is the only place that decides "系统正常", so the rule is auditable in
 * one place: ANY bad probe degrades the headline, and ANY unknown probe makes it
 * UNKNOWN rather than letting the good ones speak for the whole system.
 */
export function overallHealth(probes: StatusProbe[]): SystemHealth {
  if (probes.length === 0) return "unknown";
  if (probes.some((p) => p.state === "bad")) return "degraded";
  if (probes.some((p) => p.state === "unknown")) return "unknown";
  return "ok";
}

export const HEALTH_LABEL: Record<SystemHealth, string> = {
  ok: "系统正常",
  degraded: "部分异常",
  unknown: "状态未知",
};

const HEALTH_DOT: Record<SystemHealth, string> = {
  ok: "bg-status-ok",
  degraded: "bg-status-warn",
  unknown: "bg-status-unknown",
};

export function TopStatus({
  probes,
  refreshing = false,
  onRefresh,
  onOpenMobileNav,
  version,
  className,
}: {
  probes: StatusProbe[];
  refreshing?: boolean;
  onRefresh?: () => void;
  onOpenMobileNav: () => void;
  version?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const health = overallHealth(probes);

  // Close on outside click and on Escape: a popover that traps the page is worse
  // than no popover.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <header
      className={cn(
        "flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-4 lg:px-6",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={onOpenMobileNav}
          className="rounded-md p-2 text-muted-foreground hover:bg-accent lg:hidden"
          aria-label="打开导航"
        >
          <Menu className="h-4 w-4" aria-hidden="true" />
        </button>
        <span className="truncate t-meta text-muted-foreground lg:hidden">JEV</span>
      </div>

      <div className="flex shrink-0 items-center gap-1.5" ref={wrapRef}>
        {onRefresh ? (
          <button
            type="button"
            onClick={onRefresh}
            className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="刷新数据"
            title="刷新数据"
          >
            <RefreshCw
              className={cn("h-4 w-4", refreshing && "animate-spin")}
              aria-hidden="true"
            />
          </button>
        ) : null}

        <div className="relative">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-haspopup="dialog"
            className="flex items-center gap-2 rounded-md px-2.5 py-1.5 transition-colors hover:bg-accent"
          >
            <span
              className={cn("h-1.5 w-1.5 rounded-full", HEALTH_DOT[health])}
              aria-hidden="true"
            />
            <span className="t-meta text-foreground">{HEALTH_LABEL[health]}</span>
            <ChevronDown
              className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
              aria-hidden="true"
            />
          </button>

          {open ? (
            <div
              role="dialog"
              aria-label="系统状态详情"
              className="absolute right-0 top-full z-50 mt-2 w-[320px] rounded-lg border border-border bg-popover p-4 shadow-pop"
            >
              <div className="mb-3 flex items-center justify-between">
                <h2 className="t-section text-foreground">系统状态</h2>
                {version ? (
                  <span className="font-mono text-[11px] text-muted-foreground">{version}</span>
                ) : null}
              </div>
              <ul className="space-y-2.5">
                {probes.map((p) => (
                  <li key={p.label} className="flex items-start gap-2.5">
                    <span
                      className={cn(
                        "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                        p.state === "ok"
                          ? "bg-status-ok"
                          : p.state === "bad"
                            ? "bg-status-danger"
                            : "bg-status-unknown",
                      )}
                      aria-hidden="true"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="t-meta font-medium text-foreground">{p.label}</span>
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {p.state === "ok" ? "正常" : p.state === "bad" ? "异常" : "未知"}
                        </span>
                      </div>
                      <p className="break-words text-[11px] leading-relaxed text-muted-foreground">
                        {p.detail}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="mt-4 flex items-center gap-3 border-t border-border pt-3 text-[11px] text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <Activity className="h-3 w-3" aria-hidden="true" />
                  仅本机
                </span>
                <span className="inline-flex items-center gap-1">
                  <Shield className="h-3 w-3" aria-hidden="true" />
                  策略受控
                </span>
                <span className="inline-flex items-center gap-1">
                  <Cpu className="h-3 w-3" aria-hidden="true" />
                  本地运行
                </span>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
