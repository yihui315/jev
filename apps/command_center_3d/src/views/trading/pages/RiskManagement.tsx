/**
 * trading/pages/RiskManagement — 风险管理 (05 模拟交易账户 的风险视图)
 *
 * Positions, exposure, drawdown and risk events — ALL from the server
 * (getAccount / getServerEvents); the browser is never the authority. Spot-only:
 * no long/short and no leverage columns. Risk events come from the real server
 * event ledger.
 */
import { useEffect, useState } from "react";
import { PageContainer } from "../components/PageContainer";
import { StatCard } from "../components/StatCard";
import { usePaperApi } from "../hooks/usePaperApi";
import {
  formatPct,
  formatUsd,
  riskStatusOf,
  riskTone,
  toneClass,
} from "../lib/finance";
import type { AccountResponse, ServerEvent } from "../services/types";
import { cn } from "../../../ui/cn";

export function RiskManagement({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [acc, setAcc] = useState<AccountResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api.getAccount().then(
      (a) => alive && setAcc(a),
      (e) => alive && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      alive = false;
    };
  }, [api]);

  if (error) {
    return (
      <PageContainer title="风险管理">
        <div className="rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      </PageContainer>
    );
  }
  if (!acc) {
    return (
      <PageContainer title="风险管理">
        <div className="h-40 animate-pulse rounded-lg border border-border bg-card" />
      </PageContainer>
    );
  }

  const s = acc.summary;
  const risk = riskStatusOf(s);

  return (
    <PageContainer
      title="05 · 模拟交易账户"
      description="持仓、敞口、最大回撤与风险事件。所有数值来自服务端（当前为 Mock），浏览器不作为余额/持仓的权威来源。"
    >
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="总权益" value={formatUsd(s.equity)} tone="neutral" />
        <StatCard label="总持仓市值" value={formatUsd(s.marketValue)} tone="neutral" />
        <StatCard label="敞口/权益" value={formatPct(s.exposurePct)} tone="neutral" />
        <StatCard
          label="风险状态"
          value={risk === "ok" ? "正常" : risk === "warning" ? "预警" : "超限"}
          className={cn(riskTone(risk))}
        />
        <StatCard label="可用现金" value={formatUsd(s.availableCash)} tone="neutral" />
        <StatCard label="最大回撤" value={formatPct(s.drawdown)} tone="neutral" />
        <StatCard label="当日盈亏" value={formatUsd(s.dailyPnl, { sign: true })} tone="auto" />
        <StatCard label="累计盈亏" value={formatUsd(s.totalPnl, { sign: true })} tone="auto" />
      </div>

      <h2 className="mt-6 t-section text-foreground">持仓（现货，无杠杆）</h2>
      <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border t-meta text-muted-foreground">
              <th className="px-3 py-2 text-left">标的</th>
              <th className="px-3 py-2 text-right">数量</th>
              <th className="px-3 py-2 text-right">均价</th>
              <th className="px-3 py-2 text-right">标记价</th>
              <th className="px-3 py-2 text-right">市值</th>
              <th className="px-3 py-2 text-right">未实现盈亏</th>
              <th className="px-3 py-2 text-right">预留量</th>
            </tr>
          </thead>
          <tbody>
            {s.positions.map((p) => (
              <tr key={p.symbol} className="border-b border-border">
                <td className="px-3 py-2 text-foreground">{p.symbol}</td>
                <td className="px-3 py-2 text-right tabular-nums">{p.qty}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatUsd(p.avgPrice)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatUsd(p.mark)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatUsd(p.marketValue)}</td>
                <td className={cn("px-3 py-2 text-right tabular-nums font-medium", toneClass(p.unrealizedPnl))}>
                  {formatUsd(p.unrealizedPnl, { sign: true })}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{p.reservedQty}</td>
              </tr>
            ))}
            {s.positions.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-center text-muted-foreground">无持仓</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <RiskEvents api={api} />
    </PageContainer>
  );
}

function RiskEvents({ api }: { api: ReturnType<typeof usePaperApi> }) {
  const [events, setEvents] = useState<ServerEvent[] | null>(null);
  useEffect(() => {
    let alive = true;
    void api.getServerEvents(0, 100).then(
      (r) => alive && setEvents(r.events),
      () => alive && setEvents([]),
    );
    return () => {
      alive = false;
    };
  }, [api]);

  const riskEvents = (events ?? []).filter(
    (e) =>
      e.eventType === "paper.server.order.rejected" ||
      e.eventType === "paper.server.order.cancelled" ||
      e.eventType === "paper.server.account.reset",
  );

  return (
    <div className="mt-6">
      <h2 className="t-section text-foreground">风险拒绝 / 预警（服务端事件）</h2>
      <div className="mt-2 space-y-2">
        {riskEvents.map((e) => {
          const payload = (e.payload ?? {}) as {
            order?: { symbol?: string; reason?: string; rejectionCode?: string };
            reason?: string;
          };
          const reason = payload.order?.reason ?? payload.reason ?? "";
          const symbol = payload.order?.symbol ?? "";
          const isReject = e.eventType === "paper.server.order.rejected";
          const isReset = e.eventType === "paper.server.account.reset";
          return (
            <div
              key={e.eventId}
              className={cn(
                "rounded-lg border p-3",
                isReset
                  ? "border-status-info/40 bg-status-info/10"
                  : isReject
                    ? "border-status-danger/40 bg-status-danger/10"
                    : "border-status-warn/40 bg-status-warn/10",
              )}
            >
              <div className="flex items-center justify-between">
                <span
                  className={cn(
                    "t-meta font-medium",
                    isReset ? "text-status-info" : isReject ? "text-status-danger" : "text-status-warn",
                  )}
                >
                  {isReset ? "账户重置" : isReject ? "订单拒绝" : "订单取消"}
                  {symbol ? ` · ${symbol}` : ""}
                </span>
                <span className="t-meta text-muted-foreground">
                  {new Date(e.createdAt).toLocaleString("zh-CN")}
                </span>
              </div>
              <div className="mt-1 t-meta text-muted-foreground">{reason}</div>
            </div>
          );
        })}
        {riskEvents.length === 0 ? (
          <div className="rounded-lg border border-border bg-card p-3 t-meta text-muted-foreground">暂无风险事件</div>
        ) : null}
      </div>
    </div>
  );
}
