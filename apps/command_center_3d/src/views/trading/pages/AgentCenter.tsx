/**
 * trading/pages/AgentCenter — 智能体中心
 *
 * Shows the six Night Researcher role runs (SPOTTER / PRIOR / EDGE / KELLY /
 * TAKER / CLOSER) for a research run: run_id, model, provider, tokens, latency,
 * decision and rejection_reason. The TAKER rejection (size cap) is surfaced
 * explicitly so the operator sees WHY a proposal was blocked.
 */
import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { PageContainer } from "../components/PageContainer";
import { RoleCard } from "../components/RoleCard";
import { usePaperApi } from "../hooks/usePaperApi";
import { cn } from "../../../ui/cn";

export function AgentCenter({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [runId, setRunId] = useState<string>("run_night_8f3a21");
  const [run, setRun] = useState<Awaited<ReturnType<typeof api.getResearchRun>> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.getResearchRun(runId);
      setRun(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [api, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const blockedCount = run ? run.roles.filter((r) => r.rejectionReason).length : 0;

  return (
    <PageContainer
      title="智能体中心"
      description="六角色（SPOTTER / PRIOR / EDGE / KELLY / TAKER / CLOSER）研究运行记录。被拒绝的角色会显示拒绝原因。"
      actions={
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 t-meta text-foreground disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> 重新运行
        </button>
      }
    >
      {error ? (
        <div className="mb-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      ) : null}

      {run ? (
        <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-card p-3 t-meta text-muted-foreground">
          <span>
            run_id: <span className="font-mono text-foreground">{run.runId}</span>
          </span>
          <span>provider: <span className="text-foreground">{run.providerId}</span></span>
          <span>symbols: <span className="text-foreground">{run.symbols.join(", ")}</span></span>
          <span>snapshot: <span className="text-foreground">{run.snapshotId}</span></span>
          <span>strategy: <span className="text-foreground">{run.strategyVersion}</span></span>
          {run.modelOverride ? (
            <span>model: <span className="text-foreground">{run.modelOverride}</span></span>
          ) : null}
          <span className={cn("ml-auto", blockedCount > 0 ? "text-status-danger" : "text-status-ok")}>
            {blockedCount > 0 ? `${blockedCount} 个角色拒绝` : "全部通过"}
          </span>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {run?.roles.map((r) => <RoleCard key={r.role} run={r} runId={run.runId} />)}
        {!run && !error ? (
          <div className="col-span-full h-24 animate-pulse rounded-lg border border-border bg-card" />
        ) : null}
      </div>
    </PageContainer>
  );
}
