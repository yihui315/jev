/**
 * The confirmation card shown when Jev's decision cannot be acted on automatically.
 *
 * WHY A CARD AND NOT AN ERROR
 * ----------------------------
 * A low-confidence decision is a PAUSE, not a failure. Rendering it as an error
 * message left the operator with nothing to do but reload — the decision was
 * perfectly usable, it just needed a person to say yes. So the card shows what
 * Jev recommended, how sure it was, and offers the two real choices.
 *
 * WHY THE BUTTONS CALL THE SERVER
 * -------------------------------
 * The browser does not decide anything here. It asks for an approval id, then
 * presents that id to the backend, which re-checks every binding (task, project,
 * executor, provider, model, decision) before authorising. Setting
 * `safe_to_execute` to true locally would look like a confirmation while being
 * an unaudited bypass — and the whole point of the gate is that a human's yes is
 * recorded, not assumed.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, ShieldQuestion, X } from "lucide-react";
import { API_BASE, authorizedFetch } from "../lib/authorizedFetch";
import type { ExecutorDecision } from "../app/TaskComposer";
import { cn } from "../ui/cn";

/** The same threshold the backend narrative uses. One number, two places. */
export const AUTO_EXECUTE_CONFIDENCE = 0.75;

export interface ApprovalTicket {
  approval_id: string;
  task_hash: string;
  expires_at: number;
  ttl_s: number;
  executor: string;
  provider: string | null;
  model: string | null;
  confidence: number | null;
  reason: string | null;
}

export interface ConfirmationCardProps {
  decision: ExecutorDecision;
  task: string;
  projectRoot: string;
  provider: string | null;
  model: string | null;
  controlToken: string;
  /** Called with the APPROVED executor — taken from the approval, not the page. */
  onApproved: (executor: string) => void;
  onCancelled: () => void;
}

type Phase = "idle" | "requesting" | "confirming" | "done";

export function ConfirmationCard({
  decision, task, projectRoot, provider, model, controlToken,
  onApproved, onCancelled,
}: ConfirmationCardProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [ticket, setTicket] = useState<ApprovalTicket | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pct = Math.round((decision.confidence ?? 0) * 100);
  const belowThreshold = (decision.confidence ?? 0) < AUTO_EXECUTE_CONFIDENCE;
  const notSafe = decision.safe_to_execute !== true;

  // A ticket is short-lived, so the countdown is a fact rather than decoration:
  // an operator should not be able to stare at a stale 「确认并继续」 and assume
  // it still works.
  useEffect(() => {
    if (!ticket) return;
    const left = Math.max(0, Math.round(ticket.expires_at * 1000 - Date.now()) / 1000);
    if (left <= 0) {
      setTicket(null);
      setPhase("idle");
      setError("审批已过期，请重新点击「确认并继续」以获取新的审批。");
    }
  }, [ticket, phase]);

  const request = useCallback(async () => {
    setError(null);
    setPhase("requesting");
    try {
      const res = await authorizedFetch<ApprovalTicket>(
        controlToken,
        "/api/v1/domain/jev/request-approval",
        {
          method: "POST",
          apiBase: API_BASE,
          body: {
            jev_decision_id: decision.jev_decision_id,
            task,
            executor: decision.executor,
            project_root: projectRoot,
            provider,
            model,
            confidence: decision.confidence,
            reason: decision.reason,
          },
        },
      );
      if (!res.ok || !res.data) {
        setError(res.error ?? `申请审批失败（HTTP ${res.status}）`);
        setPhase("idle");
        return;
      }
      setTicket(res.data);
      setPhase("idle");
    } catch (e) {
      setError(e instanceof Error ? e.message : "申请审批失败");
      setPhase("idle");
    }
  }, [controlToken, decision, task, projectRoot, provider, model]);

  const confirm = useCallback(async () => {
    if (!ticket) return;
    setError(null);
    setPhase("confirming");
    try {
      const res = await authorizedFetch<{ executor: string; state: string }>(
        controlToken,
        "/api/v1/domain/jev/confirm",
        {
          method: "POST",
          apiBase: API_BASE,
          body: {
            approval_id: ticket.approval_id,
            // Sent for the server to CHECK, not for it to trust. A confirmation
            // that did not say so is not a confirmation.
            human_confirmed: true,
            jev_decision_id: decision.jev_decision_id,
            task,
            executor: decision.executor,
            project_root: projectRoot,
            provider,
            model,
          },
        },
      );
      if (!res.ok || !res.data) {
        setError(res.error ?? `确认失败（HTTP ${res.status}）`);
        setPhase("idle");
        setTicket(null);
        return;
      }
      setPhase("done");
      // The executor comes from the approval the server verified.
      onApproved(res.data.executor);
    } catch (e) {
      setError(e instanceof Error ? e.message : "确认失败");
      setPhase("idle");
      setTicket(null);
    }
  }, [ticket, controlToken, decision, task, projectRoot, provider, model, onApproved]);

  const cancel = useCallback(async () => {
    if (ticket) {
      // Burn it, so a cancelled approval cannot be used later from another tab.
      await authorizedFetch(controlToken, "/api/v1/domain/jev/cancel", {
        method: "POST", apiBase: API_BASE, body: { approval_id: ticket.approval_id },
      }).catch(() => undefined);
    }
    onCancelled();
  }, [ticket, controlToken, onCancelled]);

  const busy = phase === "requesting" || phase === "confirming";

  return (
    <section
      className="rounded-lg border border-status-warn/40 bg-status-warn/5 p-4"
      role="alertdialog"
      aria-labelledby="confirm-title"
      data-testid="jev-confirm-card"
    >
      <div className="mb-3 flex items-start gap-2">
        <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0 text-status-warn" aria-hidden="true" />
        <div>
          <h2 id="confirm-title" className="t-body font-medium text-foreground">
            需要人工确认
          </h2>
          <p className="t-meta text-muted-foreground">
            Jev 给出的建议不满足自动执行条件，任务尚未开始。
          </p>
        </div>
      </div>

      <dl className="mb-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
        <dt className="text-muted-foreground">执行器</dt>
        <dd className="font-medium text-foreground">{decision.executor || "—"}</dd>

        {model ? (
          <>
            <dt className="text-muted-foreground">模型</dt>
            <dd className="font-medium text-foreground">
              {model}
              {provider ? <span className="text-muted-foreground">（{provider}）</span> : null}
            </dd>
          </>
        ) : null}

        <dt className="text-muted-foreground">置信度</dt>
        <dd className={cn("font-medium", belowThreshold ? "text-status-warn" : "text-foreground")}>
          {pct}%
          {belowThreshold ? (
            <span className="ml-1 text-muted-foreground">
              （自动执行阈值 {Math.round(AUTO_EXECUTE_CONFIDENCE * 100)}%）
            </span>
          ) : null}
        </dd>

        <dt className="text-muted-foreground">原因</dt>
        <dd className="text-foreground">
          {decision.reason
            || (belowThreshold
              ? "置信度低于自动执行阈值"
              : notSafe ? "Jev 未确认可安全执行" : "需要人工确认")}
        </dd>
      </dl>

      {error ? (
        <p className="mb-3 flex items-start gap-1.5 text-[12px] text-status-danger" role="alert">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={ticket ? confirm : request}
          // Disabled once approved: the approval is already consumed, so a
          // second click could only ever be refused. Leaving it enabled made a
          // confirmed run look unconfirmed and invited a pointless 409.
          disabled={busy || phase === "done"}
          data-testid="jev-confirm-button"
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-[13px] text-foreground transition-colors hover:bg-accent disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Check className="h-3.5 w-3.5" aria-hidden="true" />}
          {phase === "requesting" ? "申请审批…"
            : phase === "confirming" ? "确认中…"
            : phase === "done" ? "已确认"
            : "确认并继续"}
        </button>
        <button
          type="button"
          onClick={cancel}
          disabled={busy}
          data-testid="jev-cancel-button"
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[13px] text-muted-foreground transition-colors hover:bg-accent disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
          取消
        </button>
      </div>

      {ticket ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          审批 {ticket.approval_id} 已签发，{Math.round(ticket.ttl_s / 60)} 分钟内有效，仅可使用一次。
        </p>
      ) : null}
    </section>
  );
}
