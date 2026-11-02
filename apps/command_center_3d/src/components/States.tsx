/**
 * States — the three screens every data surface must be able to show.
 *
 * WHY A SHARED MODULE
 * -------------------
 * "Loading", "empty" and "error" are the three states a happy-path-only product
 * forgets, and they are also the three most easily confused with each other:
 * an empty list rendered through the error path tells a user something broke
 * when nothing did, and a failure rendered as "no data" hides a real problem.
 *
 * These components exist so every page draws all three the same way, and so the
 * distinction between them is made once:
 *
 *   Loading — we asked and are still waiting.
 *   Empty   — we asked and the answer is genuinely "there is nothing".
 *   Error   — we asked and could not find out. This is NOT empty.
 *   Offline — the transport is down. The loudest of the four.
 */
import { AlertTriangle, Inbox, Loader2, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../ui/cn";

/** Waiting on the first successful read. */
export function LoadingState({
  label = "正在加载…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      className={cn("flex flex-col items-center justify-center gap-3 p-10 text-center", className)}
      role="status"
      aria-live="polite"
    >
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden="true" />
      <p className="t-meta text-muted-foreground">{label}</p>
    </div>
  );
}

/**
 * The answer is "there is nothing here".
 *
 * Distinct from Error on purpose: this is a successful read with a zero result,
 * so it must not be styled as a failure.
 */
export function EmptyState({
  title,
  hint,
  icon: Icon = Inbox,
  action,
  className,
}: {
  title: string;
  hint?: ReactNode;
  icon?: typeof Inbox;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center",
        className,
      )}
    >
      <Icon className="h-6 w-6 text-muted-foreground" aria-hidden="true" />
      <div className="space-y-1">
        <p className="t-section text-foreground">{title}</p>
        {hint ? <p className="t-meta text-muted-foreground">{hint}</p> : null}
      </div>
      {action}
    </div>
  );
}

/**
 * The read failed.
 *
 * `detail` is the server's own words when we have them. It is shown verbatim
 * rather than replaced by friendlier prose, because a paraphrase of an error is
 * how the actual cause gets lost.
 */
export function ErrorState({
  title = "无法读取数据",
  detail,
  onRetry,
  className,
}: {
  title?: string;
  detail?: string | null;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-5",
        className,
      )}
      role="alert"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
        <div className="min-w-0 space-y-1">
          <p className="t-section text-foreground">{title}</p>
          {detail ? (
            <p className="t-meta break-words text-muted-foreground">{detail}</p>
          ) : (
            <p className="t-meta text-muted-foreground">未获得后端返回的具体原因。</p>
          )}
        </div>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 t-meta text-foreground transition-colors hover:bg-accent"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          重试
        </button>
      ) : null}
    </div>
  );
}

/**
 * The transport is down.
 *
 * Kept separate from Error because it has a different fix: an Error is the
 * server refusing, an Offline is the server not answering at all.
 */
export function OfflineState({
  detail,
  onRetry,
  className,
}: {
  detail?: string | null;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-3 rounded-lg border border-status-warn/30 bg-status-warn/5 p-5",
        className,
      )}
      role="alert"
    >
      <div className="flex items-start gap-2.5">
        <WifiOff className="mt-0.5 h-4 w-4 shrink-0 text-status-warn" aria-hidden="true" />
        <div className="min-w-0 space-y-1">
          <p className="t-section text-foreground">未连接到后端服务</p>
          <p className="t-meta text-muted-foreground">
            {detail || "无法访问 JCC API，请确认控制平面正在运行。"}
          </p>
        </div>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 t-meta text-foreground transition-colors hover:bg-accent"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          重新连接
        </button>
      ) : null}
    </div>
  );
}

/**
 * A single connection-state chip for the header.
 *
 * `unknown` is its own state on purpose: a system whose health we could not
 * read is NOT healthy, and rendering it green is the exact dishonesty the rest
 * of this product refuses to commit.
 */
export function ConnectionDot({
  state,
  label,
}: {
  state: "ok" | "warn" | "danger" | "unknown";
  label: string;
}) {
  const color =
    state === "ok"
      ? "bg-status-ok"
      : state === "warn"
        ? "bg-status-warn"
        : state === "danger"
          ? "bg-status-danger"
          : "bg-status-unknown";
  return (
    <span className="inline-flex items-center gap-1.5" role="status">
      <span className={cn("h-1.5 w-1.5 rounded-full", color)} aria-hidden="true" />
      <span className="t-meta text-muted-foreground">{label}</span>
    </span>
  );
}
