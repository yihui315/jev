/**
 * trading/pages/StrategyResearch — 策略研究
 *
 * Backtest / OOS / returns / strategy-version table (mock). Pure display of the
 * strategies returned by listStrategies; no editing, no second provider system.
 */
import { useEffect, useState } from "react";
import { PageContainer } from "../components/PageContainer";
import { usePaperApi } from "../hooks/usePaperApi";
import { formatPct, toneClass } from "../lib/finance";
import type { Strategy } from "../services/types";
import { cn } from "../../../ui/cn";

const STATUS_LABEL: Record<Strategy["status"], string> = {
  active: "运行中",
  shadow: "影子",
  retired: "已退役",
};

export function StrategyResearch({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [strategies, setStrategies] = useState<Strategy[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api.listStrategies().then(
      (s) => alive && setStrategies(s),
      (e) => alive && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      alive = false;
    };
  }, [api]);

  if (error) {
    return (
      <PageContainer title="策略研究">
        <div className="rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title="策略研究"
      description="回测 / 样本外 / 收益与策略版本表（当前为 Mock 数据）。"
    >
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border t-meta text-muted-foreground">
              <th className="px-3 py-2 text-left">策略</th>
              <th className="px-3 py-2 text-left">版本</th>
              <th className="px-3 py-2 text-right">累计收益</th>
              <th className="px-3 py-2 text-right">Sharpe</th>
              <th className="px-3 py-2 text-right">最大回撤</th>
              <th className="px-3 py-2 text-right">胜率</th>
              <th className="px-3 py-2 text-left">回测区间</th>
              <th className="px-3 py-2 text-left">样本外</th>
              <th className="px-3 py-2 text-left">状态</th>
            </tr>
          </thead>
          <tbody>
            {(strategies ?? []).map((s) => (
              <tr key={s.strategyId} className="border-b border-border">
                <td className="px-3 py-2 text-foreground">{s.name}</td>
                <td className="px-3 py-2 font-mono text-muted-foreground">{s.version}</td>
                <td className={cn("px-3 py-2 text-right tabular-nums font-medium", toneClass(s.returns))}>
                  {formatPct(s.returns)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{s.sharpe.toFixed(2)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-status-danger">{formatPct(s.maxDrawdown)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{(s.winRate * 100).toFixed(0)}%</td>
                <td className="px-3 py-2 text-muted-foreground">{s.backtestPeriod}</td>
                <td className="px-3 py-2 text-muted-foreground">{s.oosPeriod}</td>
                <td className="px-3 py-2">
                  <span
                    className={cn(
                      "rounded px-1.5 py-0.5 t-meta",
                      s.status === "active"
                        ? "bg-status-ok/10 text-status-ok"
                        : s.status === "shadow"
                          ? "bg-status-warn/10 text-status-warn"
                          : "bg-muted text-muted-foreground",
                    )}
                  >
                    {STATUS_LABEL[s.status]}
                  </span>
                </td>
              </tr>
            ))}
            {strategies === null ? (
              <tr>
                <td colSpan={9} className="px-3 py-4 text-center text-muted-foreground">加载中…</td>
              </tr>
            ) : strategies.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-3 py-4 text-center text-muted-foreground">无策略</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </PageContainer>
  );
}
