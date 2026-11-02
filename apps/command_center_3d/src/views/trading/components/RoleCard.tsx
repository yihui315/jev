/**
 * trading/components/RoleCard — a single Night Researcher role run.
 *
 * Shows run_id (the parent run), the role, model, provider, tokens, latency,
 * decision, and — critically — the rejection_reason when a role blocked the
 * proposal (e.g. TAKER exceeding the drawdown cap). A blocked decision is
 * rendered distinctly so the operator sees WHY, not just that it failed.
 */
import { CheckCircle2, ShieldX } from "lucide-react";
import type { RoleRun } from "../services/types";
import { cn } from "../../../ui/cn";

const ROLE_ORDER: RoleRun["role"][] = [
  "SPOTTER",
  "PRIOR",
  "EDGE",
  "KELLY",
  "TAKER",
  "CLOSER",
];

export const ROLE_LABELS: Record<RoleRun["role"], string> = {
  SPOTTER: "机会发现",
  PRIOR: "优先级",
  EDGE: "边际优势",
  KELLY: "仓位尺度",
  TAKER: "执行决策",
  CLOSER: "平仓管理",
};

export function roleIndex(role: RoleRun["role"]): number {
  return ROLE_ORDER.indexOf(role);
}

export function RoleCard({ run, runId }: { run: RoleRun; runId: string }) {
  const blocked = Boolean(run.rejectionReason);
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-3",
        blocked ? "border-status-danger/40" : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded bg-muted px-1.5 py-0.5 t-meta font-medium text-foreground">
            {run.role}
          </span>
          <span className="t-meta text-muted-foreground">{ROLE_LABELS[run.role]}</span>
        </div>
        {blocked ? (
          <ShieldX className="h-4 w-4 text-status-danger" aria-label="blocked" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-status-ok" aria-label="ok" />
        )}
      </div>

      <div className="mt-2 t-meta text-muted-foreground">
        <span className="text-foreground">{run.model}</span> · {run.provider} · {run.tokensUsed} tok ·{" "}
        {run.latencyMs}ms
      </div>

      <div
        className={cn(
          "mt-2 rounded px-2 py-1 text-sm",
          blocked ? "bg-status-danger/10 text-status-danger" : "bg-status-ok/10 text-status-ok",
        )}
      >
        {run.decision}
      </div>

      {blocked ? (
        <div className="mt-1 t-meta text-status-danger">拒绝原因：{run.rejectionReason}</div>
      ) : null}

      <div className="mt-2 truncate t-meta text-muted-foreground" title={runId}>
        run_id: {runId}
      </div>
    </div>
  );
}
