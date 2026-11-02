/**
 * trading/pages/RiskManagement — 风险管理
 *
 * Positions, exposure, max drawdown and risk rejections — ALL from the server
 * (getPaperAccount); the browser is never the authority on these numbers. A
 * severity-ordered rejection list surfaces the recent risk breaches.
 */
import { useEffect, useState } from "react";
import { PageContainer } from "../components/PageContainer";
import { StatCard } from "../components/StatCard";
import { usePaperApi } from "../hooks/usePaperApi";
import {
  exposurePct,
  formatPct,
  formatUsd,
  riskTone,
  toneClass,
} from "../lib/finance";
import type { PaperAccount } from "../services/types";
import { cn } from "../../../ui/cn";

export function RiskManagement({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [acc, setAcc] = useState<PaperAccount | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api.getPaperAccount().then(
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

  const expo = exposurePct(acc);

  return (
    <PageContainer
      title="风险管理"
      description="持仓、敞口、最大回撤与风险拒绝。所有数值来自服务端（当前为 Mock），浏览器不作为余额/持仓的权威来源。"
    >
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="总权益" value={formatUsd(acc.equity)} tone="neutral" />
        <StatCard label="总敞口" value={formatUsd(expo * acc.equity)} tone="neutral" />
        <StatCard label="敞口/权益" value={formatPct(expo)} tone="neutral" />
        <StatCard
          label="风险状态"
          value={acc.riskStatus === "ok" ? "正常" : acc.riskStatus === "warning" ? "预警" : "超限"}
          className={cn(riskTone(acc.riskStatus))}
        />
        <StatCard label="占用保证金" value={formatUsd(acc.marginUsed)} tone="neutral" />
        <StatCard label="最大回撤" value={formatPct(acc.maxDrawdown)} tone="neutral" />
        <StatCard label="当日盈亏" value={formatUsd(acc.pnlDay, { sign: true })} tone="auto" />
        <StatCard label="累计盈亏" value={formatUsd(acc.pnlTotal, { sign: true })} tone="auto" />
      </div>

      {/* Positions (server-derived) */}
      <h2 className="mt-6 t-section text-foreground">持仓</h2>
      <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border t-meta text-muted-foreground">
              <th className="px-3 py-2 text-left">标的</th>
              <th className="px-3 py-2 text-left">方向</th>
              <th className="px-3 py-2 text-right">数量</th>
              <th className="px-3 py-2 text-right">均价</th>
              <th className="px-3 py-2 text-right">标记价</th>
              <th className="px-3 py-2 text-right">未实现盈亏</th>
              <th className="px-3 py-2 text-right">杠杆</th>
            </tr>
          </thead>
          <tbody>
            {acc.positions.map((p) => (
              <tr key={p.symbol} className="border-b border-border">
                <td className="px-3 py-2">{p.symbol}</td>
                <td className={cn("px-3 py-2 font-medium", p.side === "long" ? "text-status-ok" : "text-status-danger")}>
                  {p.side === "long" ? "多" : "空"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{p.quantity}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatUsd(p.avgPrice)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatUsd(p.markPrice)}</td>
                <td className={cn("px-3 py-2 text-right tabular-nums font-medium", toneClass(p.unrealizedPnl))}>
                  {formatUsd(p.unrealizedPnl, { sign: true })}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{p.leverage}x</td>
              </tr>
            ))}
            {acc.positions.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-center text-muted-foreground">无持仓</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {/* Risk rejections (from the ledger, server-derived) */}
      <RiskRejections api={api} />
    </PageContainer>
  );
}

function RiskRejections({ api }: { api: ReturnType<typeof usePaperApi> }) {
  const [events, setEvents] = useState<Awaited<ReturnType<typeof api.subscribeEvents>> | null>(null);
  useEffect(() => {
    let alive = true;
    void api.subscribeEvents().then((e) => alive && setEvents(e), () => alive && setEvents([]));
    return () => {
      alive = false;
    };
  }, [api]);

  const rejections = (events ?? []).filter((e) => e.type === "RISK_BREACH" || e.type === "REJECT");
  return (
    <div className="mt-6">
      <h2 className="t-section text-foreground">风险拒绝 / 预警</h2>
      <div className="mt-2 space-y-2">
        {rejections.map((e) => (
          <div
            key={e.eventId}
            className={cn(
              "rounded-lg border p-3",
              e.type === "RISK_BREACH"
                ? "border-status-danger/40 bg-status-danger/10"
                : "border-status-warn/40 bg-status-warn/10",
            )}
          >
            <div className="flex items-center justify-between">
              <span className={cn("t-meta font-medium", e.type === "RISK_BREACH" ? "text-status-danger" : "text-status-warn")}>
                {e.type === "RISK_BREACH" ? "风险超限" : "拒绝"}
                {e.symbol ? ` · ${e.symbol}` : ""}
              </span>
              <span className="t-meta text-muted-foreground">{new Date(e.ts).toLocaleString("zh-CN")}</span>
            </div>
            <div className="mt-1 t-meta text-muted-foreground">{e.note}</div>
          </div>
        ))}
        {rejections.length === 0 ? (
          <div className="rounded-lg border border-border bg-card p-3 t-meta text-muted-foreground">暂无风险拒绝记录</div>
        ) : null}
      </div>
    </div>
  );
}
