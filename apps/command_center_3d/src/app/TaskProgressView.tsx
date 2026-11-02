/**
 * TaskProgress — the five-stage rail.
 *
 * This is the component that replaces the ten-state machine for a human reader.
 * The backend keeps its own vocabulary; this file is the only place that knows
 * how to say it in five plain steps, which is why the mapping lives in
 * `taskProgress.ts` and is unit-tested there.
 *
 * COLOUR IS NEVER THE ONLY SIGNAL
 * -------------------------------
 * Each row carries a glyph (✓ / ● / ○ / ✕) and a text state in addition to its
 * colour, so the rail is fully readable without colour vision and in a
 * screenshot printed in greyscale.
 */
import { Check, Circle, Loader2, X } from "lucide-react";
import { cn } from "../ui/cn";
import { buildStageViews, type StageState, type StageView } from "./taskProgress";

function RowIcon({ state }: { state: StageState }) {
  if (state === "done") {
    return <Check className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  if (state === "error") {
    return <X className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  if (state === "active") {
    return <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />;
  }
  return <Circle className="h-3 w-3" aria-hidden="true" />;
}

/** The accessible phrase for a row, so the glyph is never the only meaning. */
function stateWord(state: StageState): string {
  switch (state) {
    case "done":
      return "已完成";
    case "active":
      return "进行中";
    case "error":
      return "未完成";
    default:
      return "未开始";
  }
}

const ROW_TONE: Record<StageState, string> = {
  done: "text-status-ok",
  active: "text-status-live",
  error: "text-status-danger",
  pending: "text-muted-foreground/50",
};

export function TaskProgress({
  status,
  executor,
  className,
}: {
  status: string | null;
  /** The mission's executor, used to name the one delegated stage. */
  executor?: string | null;
  className?: string;
}) {
  const views: StageView[] = buildStageViews(status, executor);

  return (
    <ol className={cn("space-y-0.5", className)} aria-label="任务进度">
      {views.map((v) => (
        <li
          key={v.stage}
          className="flex items-center gap-3 rounded-md px-1 py-1.5"
          // A screen reader hears "执行任务, WorkBuddy, 进行中" rather than a
          // bare glyph.
          aria-current={v.state === "active" ? "step" : undefined}
        >
          <span
            className={cn(
              "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border",
              v.state === "pending"
                ? "border-border text-muted-foreground/50"
                : "border-current",
              ROW_TONE[v.state],
            )}
          >
            <RowIcon state={v.state} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              {/* The owner is part of the sentence, not decoration: it is what
                  tells the user JEV is running this task and the executor is
                  doing the one delegated stage. */}
              <span
                className={cn(
                  "text-[12px]",
                  v.owner === "jev" ? "text-primary/90" : "text-status-live",
                  v.state === "pending" && "text-muted-foreground/60",
                )}
              >
                {v.ownerLabel} ·
              </span>
              <span
                className={cn(
                  "text-[14px]",
                  v.state === "pending" ? "text-muted-foreground/70" : "text-foreground",
                  v.state === "active" && "font-medium",
                )}
              >
                {v.label}
              </span>
              <span className={cn("text-[11px]", ROW_TONE[v.state])}>{stateWord(v.state)}</span>
            </div>
            <p className="truncate text-[11px] text-muted-foreground/70">{v.hint}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
