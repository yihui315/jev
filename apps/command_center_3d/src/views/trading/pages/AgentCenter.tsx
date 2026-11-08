/**
 * trading/pages/AgentCenter — AI 专家团队 (06)
 *
 * Launches a six-role research run against the REAL `/api/agents/research`
 * endpoint (mock today) and audits every role. SPOTTER / PRIOR / EDGE / CLOSER
 * are LLM roles; KELLY and TAKER are deterministic server gates that are
 * BLOCKED unless server-verified backtest/OOS metrics are supplied. The UI shows
 * the gate truth honestly — it never fakes a "qualified" state.
 *
 * The market snapshot sent to the server is assembled from the authoritative
 * Binance public data (ticker / book / candles) and is explicitly untrusted by
 * the server. Only a provider id is sent; credentials stay server-side.
 */
import { useCallback, useState } from "react";
import { Play } from "lucide-react";
import { PageContainer } from "../components/PageContainer";
import { RoleCard, type RoleView } from "../components/RoleCard";
import { usePaperApi } from "../hooks/usePaperApi";
import type { AiRoleId, ResearchRunResult, Symbol } from "../services/types";
import { cn } from "../../../ui/cn";

const ALL_SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const ROLE_ORDER: AiRoleId[] = ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"];

export function AgentCenter({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [symbols, setSymbols] = useState<Symbol[]>(["BTCUSDT", "ETHUSDT"]);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [run, setRun] = useState<ResearchRunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runResearch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const ticker = await api.getTicker(symbols).catch(() => []);
      const ob = await api.getOrderBook(symbols[0]).catch(() => null);
      const priceMap: Record<string, number> = {};
      for (const t of ticker) priceMap[t.symbol] = t.price;
      const snapshot = {
        symbols,
        prices: priceMap,
        orderBook: ob
          ? { symbol: ob.symbol, bids: ob.bids.slice(0, 10), asks: ob.asks.slice(0, 10) }
          : null,
        generatedAt: new Date().toISOString(),
        source: "JEV client assembled from Binance public REST (untrusted by server)",
      };
      const result = await api.createResearchRun({
        snapshot,
        provider: provider || "default",
        model: model || undefined,
      });
      setRun(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [api, symbols, provider, model]);

  const views: RoleView[] | null = run
    ? ROLE_ORDER.map((role) => toView(role, run.outputs[role] ?? {}))
    : null;

  return (
    <PageContainer
      title="06 · AI 专家团队"
      description="六角色（SPOTTER / PRIOR / EDGE / KELLY / TAKER / CLOSER）研究运行与审计。KELLY 与 TAKER 为确定性闸门；研究模式下若无服务端校验的回测/OOS 指标，二者恒为 BLOCKED。"
      actions={
        <button
          type="button"
          onClick={() => void runResearch()}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 t-meta text-foreground disabled:opacity-50"
        >
          <Play className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> 运行研究
        </button>
      }
    >
      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-3">
        <div>
          <div className="t-meta text-muted-foreground">交易对</div>
          <div className="mt-1 flex gap-1">
            {ALL_SYMBOLS.map((s) => {
              const on = symbols.includes(s);
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() =>
                    setSymbols((prev) => (on ? prev.filter((x) => x !== s) : [...prev, s]))
                  }
                  className={cn(
                    "rounded px-2 py-1 t-meta",
                    on ? "bg-foreground text-background" : "border border-border text-foreground",
                  )}
                >
                  {s}
                </button>
              );
            })}
          </div>
        </div>
        <label className="block">
          <span className="t-meta text-muted-foreground">Provider ID（仅传 ID，密钥走服务端）</span>
          <input
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            placeholder="default"
            className="mt-1 w-40 rounded border border-border bg-background px-2 py-1.5 t-meta text-foreground"
          />
        </label>
        <label className="block">
          <span className="t-meta text-muted-foreground">Model（可选）</span>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="auto"
            className="mt-1 w-32 rounded border border-border bg-background px-2 py-1.5 t-meta text-foreground"
          />
        </label>
      </div>

      {error ? (
        <div className="mb-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      ) : null}

      {run ? (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-border bg-card p-3 t-meta text-muted-foreground">
            <span>run_id: <span className="font-mono text-foreground">{run.runId}</span></span>
            <span>provider: <span className="text-foreground">{run.provider}</span></span>
            <span>model: <span className="text-foreground">{run.requestedModel}</span></span>
            <span className="text-status-warn">autoExecution = false（禁止自动下单）</span>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {views?.map((v) => <RoleCard key={v.role} view={v} />)}
          </div>
        </>
      ) : (
        <div className="h-24 animate-pulse rounded-lg border border-border bg-card" />
      )}
    </PageContainer>
  );
}

function toView(role: AiRoleId, out: Record<string, unknown>): RoleView {
  switch (role) {
    case "SPOTTER": {
      const opps = Array.isArray(out.opportunities) ? (out.opportunities as unknown[]) : [];
      const first = (opps[0] ?? {}) as { setup?: string };
      return {
        role,
        blocked: false,
        decision: `发现 ${opps.length} 个候选`,
        detail: first.setup ?? "无可靠机会",
        model: typeof out.model === "string" ? out.model : undefined,
      };
    }
    case "PRIOR": {
      const regime = String(out.regime ?? "unknown");
      const conf = typeof out.prior_confidence === "number" ? out.prior_confidence : null;
      return {
        role,
        blocked: false,
        decision: `regime: ${regime}`,
        detail: conf != null ? `先验置信度 ${conf.toFixed(2)}` : "未提供置信度",
        model: typeof out.model === "string" ? out.model : undefined,
      };
    }
    case "EDGE": {
      const verdict = String(out.verdict ?? "WATCH");
      const hypothesis = typeof out.hypothesis === "string" ? out.hypothesis : "—";
      const invalidation = typeof out.invalidation === "string" ? out.invalidation : "—";
      return {
        role,
        blocked: verdict !== "RESEARCH",
        decision: `verdict: ${verdict}`,
        detail: `假设：${hypothesis}；失效：${invalidation}`,
        model: typeof out.model === "string" ? out.model : undefined,
      };
    }
    case "KELLY": {
      const status = String(out.status ?? "BLOCKED");
      const reasons = Array.isArray(out.reasons) ? (out.reasons as string[]).join("；") : "";
      const alloc = typeof out.allocationPct === "number" ? out.allocationPct : 0;
      return {
        role,
        blocked: status !== "READY",
        decision: `status: ${status}${alloc ? ` · ${(alloc * 100).toFixed(2)}%` : ""}`,
        detail: reasons || (status === "READY" ? "已放行（仍需独立确定性风险闸门）" : "未放行"),
      };
    }
    case "TAKER": {
      const status = String(out.status ?? "BLOCKED");
      const reason = typeof out.reason === "string" ? out.reason : "";
      return {
        role,
        blocked: status !== "PROPOSAL_ONLY",
        decision: `status: ${status}`,
        detail: reason || "执行提案闸门未生成",
      };
    }
    case "CLOSER": {
      const decision = String(out.decision ?? "MONITOR");
      const note = typeof out.final_risk_note === "string" ? out.final_risk_note : "—";
      return {
        role,
        blocked: decision === "REJECT",
        decision,
        detail: note,
        model: typeof out.model === "string" ? out.model : undefined,
      };
    }
  }
}
