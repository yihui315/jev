/**
 * RecentTaskCard — one task, five facts, no jargon.
 *
 * WHAT A CARD SHOWS
 * -----------------
 * Name, a one-line goal, the state, how long it has been running, and when it
 * last changed. That is the entire specification, and it is deliberately short:
 * a card that lists a mission id, a session id and a policy version is a
 * database row wearing a costume, and it is unreadable at a glance.
 *
 * STATE IS NEVER INFERRED FROM AGE
 * --------------------------------
 * A card that has not been updated for a while is not "stale" or "dead" unless
 * the server said so — it shows the last update time and nothing more. Elapsed
 * time is drawn from the server's own timestamps, so a browser clock skewed by
 * hours cannot make a task look like it ran for a week.
 */
import { Clock } from "lucide-react";
import { cn } from "../ui/cn";
import { fmtRelative, type Mission } from "./domainApi";
import { taskStateFor, TASK_STATE_LABEL, type TaskState } from "./taskProgress";

/** The four product states plus the two honest extras, with their colours. */
const STATE_STYLE: Record<TaskState, { dot: string; text: string }> = {
  running: { dot: "bg-status-live", text: "text-status-live" },
  waiting: { dot: "bg-status-warn", text: "text-status-warn" },
  completed: { dot: "bg-status-ok", text: "text-status-ok" },
  failed: { dot: "bg-status-danger", text: "text-status-danger" },
  // Unknown is grey and never inherits a "healthy" colour.
  unknown: { dot: "bg-status-unknown", text: "text-status-unknown" },
};

/**
 * Elapsed run time as `m s`, from the server's own clock.
 *
 * Returns null when the task never started, so the caller can hide the field
 * rather than print a misleading `0m 0s` for a task that is still a draft.
 */
export function elapsedLabel(m: Mission, now = Date.now()): string | null {
  if (m.status === "RUNNING" || m.status === "STARTING" || m.status === "VERIFYING") {
    const from = m.started_at ?? m.updated_at;
    if (!from) return null;
    return formatDuration(now / 1000 - from);
  }
  if (m.started_at && m.finished_at) {
    return formatDuration(m.finished_at - m.started_at);
  }
  return null;
}

/** `m s` under an hour, `h m` above it. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0s";
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m === 0) return `${s}s`;
  if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

export function RecentTaskCard({
  mission,
  onOpen,
  now,
}: {
  mission: Mission;
  onOpen: () => void;
  now?: number;
}) {
  const state = taskStateFor(mission.status);
  const style = STATE_STYLE[state];
  const elapsed = elapsedLabel(mission, now);

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "group flex w-full flex-col gap-2 rounded-lg border border-border bg-card p-4 text-left",
        "transition-colors hover:border-border-strong focus-visible:border-ring",
      )}
      aria-label={`打开任务 ${mission.title}`}
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="min-w-0 flex-1 truncate text-[15px] font-medium text-foreground">
          {mission.title}
        </h3>
        <span className={cn("inline-flex shrink-0 items-center gap-1.5 t-meta", style.text)}>
          <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} aria-hidden="true" />
          {TASK_STATE_LABEL[state]}
        </span>
      </div>

      {mission.goal ? (
        <p className="line-clamp-2 t-meta text-muted-foreground">{mission.goal}</p>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 t-meta text-muted-foreground/80">
        {elapsed ? (
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3 w-3" aria-hidden="true" />
            {elapsed}
          </span>
        ) : null}
        <span>更新于 {fmtRelative(mission.updated_at, now)}</span>
      </div>
    </button>
  );
}
