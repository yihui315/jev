/**
 * EventTimeline — what the task actually did, in order.
 *
 * THE CONTRACT WITH THE USER
 * --------------------------
 * Every line here is a real backend event. When the stream is down, this panel
 * says so in as many words as the events it would otherwise have shown, because
 * an empty list and a disconnected feed look identical otherwise — and only one
 * of them means "nothing is happening".
 */
import { Radio, WifiOff } from "lucide-react";
import { cn } from "../ui/cn";
import { formatClock, type ActivityLine, type FeedState } from "./eventService";

const FEED_LABEL: Record<FeedState, string> = {
  connecting: "正在连接事件流…",
  live: "实时",
  reconnecting: "连接已断开，正在重连",
  offline: "事件流未连接",
};

const FEED_DOT: Record<FeedState, string> = {
  connecting: "bg-status-unknown",
  live: "bg-status-ok",
  reconnecting: "bg-status-warn",
  offline: "bg-status-unknown",
};

export function EventTimeline({
  lines,
  state,
  className,
}: {
  lines: ActivityLine[];
  state: FeedState;
  className?: string;
}) {
  // Newest first: while a task runs, the latest line is the one being read.
  const ordered = [...lines].sort((a, b) => b.at - a.at);

  return (
    <section className={cn("flex flex-col", className)} aria-label="实时事件">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="t-section text-foreground">实时事件</h3>
        <span
          className="inline-flex items-center gap-1.5 t-meta text-muted-foreground"
          role="status"
          aria-live="polite"
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", FEED_DOT[state])} aria-hidden="true" />
          {FEED_LABEL[state]}
        </span>
      </div>

      {lines.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center">
          {state === "live" ? (
            <p className="t-body text-muted-foreground">暂无事件。任务开始执行后，这里会实时显示进展。</p>
          ) : (
            <div className="flex flex-col items-center gap-2">
              <WifiOff className="h-5 w-5 text-status-warn" aria-hidden="true" />
              <p className="t-body text-foreground">事件流未连接，暂时无法显示实时进展</p>
              <p className="t-meta text-muted-foreground">
                后端事件流恢复后会自动重新连接，无需刷新页面。
              </p>
            </div>
          )}
        </div>
      ) : (
        <ol className="space-y-0">
          {ordered.map((l, i) => (
            <li key={l.id} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-border-strong" aria-hidden="true" />
                {i < ordered.length - 1 ? (
                  <span className="w-px flex-1 bg-border" aria-hidden="true" />
                ) : null}
              </div>
              <div className="min-w-0 flex-1 pb-4">
                <div className="flex items-baseline gap-2.5">
                  <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                    {formatClock(l.at)}
                  </span>
                  <span className="min-w-0 text-[13px] text-foreground">{l.text}</span>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      {state === "reconnecting" ? (
        <p className="mt-2 flex items-center gap-1.5 t-meta text-status-warn" role="status">
          <Radio className="h-3.5 w-3.5" aria-hidden="true" />
          正在重新连接后端事件流
        </p>
      ) : null}
    </section>
  );
}
