/**
 * ApprovalCard — a decision this task is waiting on.
 *
 * MOVED, NOT ADDED
 * ----------------
 * Approvals used to live in their own top-level menu, which meant a task could be
 * visibly stuck while the thing blocking it was on a different screen. An
 * approval is part of a task's story, so it is rendered on the task.
 *
 * EVERY APPROVAL IS BOUND TO ITS MISSION
 * --------------------------------------
 * The card carries the `missionId` it belongs to, and the decision is sent with
 * it. There is no code path here that can approve a task the user is not
 * looking at, and no "approve all" affordance — a bulk approve is exactly how one
 * mistaken click authorises six things nobody read.
 *
 * THE RISK LEVEL IS NOT OURS
 * --------------------------
 * `risk` comes from the backend's own resolution document. This component renders
 * it and never computes it, and an absent risk renders as 未标注 rather than as
 * "low" — assuming a risk level is how an unreviewed action gets waved through.
 */
import { ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { cn } from "../ui/cn";
import type { AttentionAction, AttentionItem } from "./domainApi";

function riskLabel(risk: string | undefined): { text: string; tone: string } {
  switch (risk) {
    case "low":
      return { text: "低风险", tone: "text-status-ok" };
    case "high":
      return { text: "高风险", tone: "text-status-danger" };
    default:
      return { text: "风险未标注", tone: "text-status-unknown" };
  }
}

export function ApprovalCard({
  item,
  action,
  busy,
  error,
  onAllow,
  onDeny,
  className,
}: {
  /** The attention item this approval belongs to. */
  item: AttentionItem;
  /** The server's proposed control action for this item, if it published one. */
  action: AttentionAction | null;
  busy: boolean;
  error: string | null;
  onAllow: () => void;
  onDeny: () => void;
  className?: string;
}) {
  const risk = riskLabel(action?.risk);
  const allowed = action ? action.policy_permitted : null;
  // `UNKNOWN` executability is drawn neutral, never as a green "ready".
  const executable = action?.executability === "EXECUTABLE";
  const unknownExec = action?.executability === "UNKNOWN";

  return (
    <section
      className={cn(
        "rounded-lg border border-status-warn/30 bg-status-warn/5 p-5",
        className,
      )}
      aria-label="需要你的确认"
    >
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-status-warn" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="t-section text-foreground">需要你的确认</h3>
          <p className="mt-1 t-body text-muted-foreground">
            {action ? action.label : "执行代理请求执行一个操作"}
          </p>
          {action?.detail ? (
            <p className="mt-1.5 whitespace-pre-wrap rounded-md bg-background/50 p-2.5 font-mono text-[12px] text-foreground">
              {action.detail}
            </p>
          ) : item.reason ? (
            <p className="mt-1.5 t-meta text-muted-foreground">{item.reason}</p>
          ) : null}

          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 t-meta">
            <span className={risk.tone}>风险：{risk.text}</span>
            {allowed !== null ? (
              <span className={allowed ? "text-status-ok" : "text-status-danger"}>
                策略：{allowed ? "允许" : "禁止"}
              </span>
            ) : null}
            {unknownExec ? (
              <span className="text-status-unknown">可执行性：未知</span>
            ) : executable ? null : action ? (
              <span className="text-status-warn">当前无法执行</span>
            ) : null}
            {action?.blocked_reason ? (
              <span className="text-muted-foreground">{action.blocked_reason}</span>
            ) : null}
          </div>

          {error ? (
            <p className="mt-3 t-body text-destructive" role="alert">
              {error}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onAllow}
              disabled={busy || !executable}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-[14px] font-medium transition-colors",
                busy || !executable
                  ? "cursor-not-allowed bg-muted text-muted-foreground"
                  : "bg-primary text-primary-foreground hover:brightness-110",
              )}
            >
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              允许
            </button>
            <button
              type="button"
              onClick={onDeny}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-[14px] text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ShieldX className="h-4 w-4" aria-hidden="true" />
              拒绝
            </button>
            {!executable ? (
              <span className="t-meta text-muted-foreground">
                后端尚未确认该操作可执行，因此不允许批准。
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
