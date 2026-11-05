/**
 * trading/components/RoleCard — a single Night Researcher role result.
 *
 * Shows the role, decision/verdict, and — critically — when a role BLOCKED the
 * proposal (e.g. KELLY or TAKER gate, or EDGE=WATCH) the reason is surfaced so
 * the operator sees WHY. `KELLY`/`TAKER` are deterministic server gates, not LLM
 * output; in a research-only run they are BLOCKED by design because no
 * server-verified backtest/OOS metrics were supplied.
 */
import { CheckCircle2, ShieldX } from "lucide-react";
import type { AiRoleId } from "../services/types";
import { cn } from "../../../ui/cn";

export const ROLE_LABELS: Record<AiRoleId, string> = {
  SPOTTER: "机会发现",
  PRIOR: "市场先验",
  EDGE: "优势验证",
  KELLY: "仓位尺度",
  TAKER: "执行决策",
  CLOSER: "退出复盘",
};

export interface RoleView {
  role: AiRoleId;
  blocked: boolean;
  decision: string;
  detail: string;
  model?: string;
  tokens?: number;
  latencyMs?: number;
  extra?: Record<string, unknown>;
}

export function RoleCard({ view }: { view: RoleView }) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-3",
        view.blocked ? "border-status-danger/40" : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded bg-muted px-1.5 py-0.5 t-meta font-medium text-foreground">
            {view.role}
          </span>
          <span className="t-meta text-muted-foreground">{ROLE_LABELS[view.role]}</span>
        </div>
        {view.blocked ? (
          <ShieldX className="h-4 w-4 text-status-danger" aria-label="blocked" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-status-ok" aria-label="ok" />
        )}
      </div>

      <div className="mt-2 t-meta text-muted-foreground">
        {view.model ? <span className="text-foreground">{view.model}</span> : null}
        {view.tokens != null ? ` · ${view.tokens} tok` : ""}
        {view.latencyMs != null ? ` · ${view.latencyMs}ms` : ""}
      </div>

      <div
        className={cn(
          "mt-2 rounded px-2 py-1 text-sm",
          view.blocked ? "bg-status-danger/10 text-status-danger" : "bg-status-ok/10 text-status-ok",
        )}
      >
        {view.decision}
      </div>

      <div className="mt-1 t-meta text-muted-foreground">{view.detail}</div>
    </div>
  );
}
