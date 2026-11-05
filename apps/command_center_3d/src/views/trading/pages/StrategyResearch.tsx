/**
 * trading/pages/StrategyResearch — 策略竞赛实验室 (04)
 *
 * Presents the OOS gating pipeline every strategy must clear:
 *   Research → Backtest → OOS → Paper → Qualified
 *
 * KELLY and TAKER are deterministic server gates (NOT LLM output). In a
 * research-only run they are BLOCKED because no server-verified backtest / OOS
 * metrics were supplied — the UI states this truth instead of faking a
 * "qualified" strategy. Backtest / OOS metrics, when present, must come from a
 * server-verified ledger; anything the operator pastes is labelled
 * "user-supplied, not server-verified".
 */
import { useCallback, useState } from "react";
import { Play, ShieldX, ShieldCheck } from "lucide-react";
import { PageContainer } from "../components/PageContainer";
import { usePaperApi } from "../hooks/usePaperApi";
import type { ResearchRunResult, Symbol } from "../services/types";
import { cn } from "../../../ui/cn";

const STAGES = ["Research", "Backtest", "OOS", "Paper", "Qualified"] as const;
type Stage = (typeof STAGES)[number];

export function StrategyResearch({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [symbols] = useState<Symbol[]>(["BTCUSDT", "ETHUSDT"]);
  const [run, setRun] = useState<ResearchRunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runResearch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const ticker = await api.getTicker(symbols).catch(() => []);
      const priceMap: Record<string, number> = {};
      for (const t of ticker) priceMap[t.symbol] = t.price;
      const snapshot = {
        symbols,
        prices: priceMap,
        generatedAt: new Date().toISOString(),
        source: "JEV client assembled from Binance public REST (untrusted by server)",
      };
      const result = await api.createResearchRun({ snapshot, provider: "default" });
      setRun(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [api, symbols]);

  const kelly = run?.outputs.KELLY as { status?: string; reasons?: string[] } | undefined;
  const taker = run?.outputs.TAKER as { status?: string; reason?: string } | undefined;

  // Gate states derived honestly from the run (offline: BLOCKED by design).
  const stageStatus: Record<Stage, "done" | "pending" | "blocked" | "skipped"> = {
    Research: run ? "done" : "pending",
    Backtest: "pending", // 需要服务端校验的回测指标（离线无来源）
    OOS: "pending", // 需要服务端校验的 OOS 样本
    Paper: "skipped", // research-only：autoExecution=false，不触及真实/模拟下单
    Qualified:
      kelly?.status === "READY" && taker?.status === "PROPOSAL_ONLY" ? "done" : "blocked",
  };

  return (
    <PageContainer
      title="04 · 策略竞技场"
      description="策略必须通过 OOS 闸门：Research → Backtest → OOS → Paper → Qualified。KELLY/TAKER 为确定性闸门；研究模式下若无服务端校验指标，恒为 BLOCKED。"
      actions={
        <button
          type="button"
          onClick={() => void runResearch()}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 t-meta text-foreground disabled:opacity-50"
        >
          <Play className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> 运行研究闸门
        </button>
      }
    >
      {error ? (
        <div className="mb-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      ) : null}

      {/* OOS pipeline */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-4">
        {STAGES.map((stage, i) => {
          const st = stageStatus[stage];
          const dot =
            st === "done"
              ? "bg-status-ok text-background"
              : st === "blocked"
                ? "bg-status-danger text-background"
                : st === "skipped"
                  ? "bg-muted text-muted-foreground"
                  : "border border-border text-muted-foreground";
          const label =
            st === "done" ? "完成" : st === "blocked" ? "BLOCKED" : st === "skipped" ? "未触及" : "待校验";
          return (
            <div key={stage} className="flex items-center gap-2">
              <div className="text-center">
                <div className={cn("mx-auto flex h-8 w-8 items-center justify-center rounded-full t-meta font-medium", dot)}>
                  {i + 1}
                </div>
                <div className="mt-1 t-meta text-foreground">{stage}</div>
                <div className="t-meta text-muted-foreground">{label}</div>
              </div>
              {i < STAGES.length - 1 ? <span className="mx-1 text-muted-foreground">→</span> : null}
            </div>
          );
        })}
      </div>

      {/* Gate detail */}
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        <GateCard
          title="KELLY 仓位闸门"
          ok={kelly?.status === "READY"}
          headline={kelly ? String(kelly.status) : "未运行"}
          detail={
            kelly?.reasons && kelly.reasons.length
              ? kelly.reasons.join("；")
              : kelly?.status === "READY"
                ? "已放行（仍需独立确定性风险闸门）"
                : "运行研究后显示放行原因"
          }
        />
        <GateCard
          title="TAKER 执行闸门"
          ok={taker?.status === "PROPOSAL_ONLY"}
          headline={taker ? String(taker.status) : "未运行"}
          detail={taker?.reason ?? "运行研究后显示执行提案状态"}
        />
      </div>

      <div className="mt-4 rounded-lg border border-status-warn/40 bg-status-warn/10 p-3 t-meta text-status-warn">
        说明：Backtest / OOS 阶段需要<b>服务端校验</b>的回测与样本外指标。当前离线模式没有该来源；
        任何由用户粘贴的指标都必须标注“用户自提供、未服务端校验”，不得冒充真实放行。
      </div>
    </PageContainer>
  );
}

function GateCard({
  title,
  ok,
  headline,
  detail,
}: {
  title: string;
  ok: boolean;
  headline: string;
  detail: string;
}) {
  return (
    <div className={cn("rounded-lg border p-3", ok ? "border-status-ok/40 bg-status-ok/10" : "border-border bg-card")}>
      <div className="flex items-center justify-between">
        <span className="t-section text-foreground">{title}</span>
        {ok ? (
          <ShieldCheck className="h-4 w-4 text-status-ok" />
        ) : (
          <ShieldX className="h-4 w-4 text-status-danger" />
        )}
      </div>
      <div className={cn("mt-2 text-sm font-medium", ok ? "text-status-ok" : "text-status-danger")}>{headline}</div>
      <div className="mt-1 t-meta text-muted-foreground">{detail}</div>
    </div>
  );
}
