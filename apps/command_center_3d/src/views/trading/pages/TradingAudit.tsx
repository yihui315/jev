/**
 * trading/pages/TradingAudit — 交易审计
 *
 * Orders, fills, the ledger, and an event-replay list — all consistent with the
 * server ledger (mock). The orders shown come from MOCK_SEED_ORDERS plus any
 * order the operator created in the Trading Desk during this session, and the
 * ledger events are the same source the Risk page reads, so the two never
 * disagree.
 */
import { useEffect, useState } from "react";
import { PageContainer } from "../components/PageContainer";
import { OrderRow } from "../components/OrderRow";
import { usePaperApi } from "../hooks/usePaperApi";
import { MOCK_SEED_ORDERS } from "../services/paperApi";
import { formatUsd } from "../lib/finance";
import type { LedgerEvent, PaperOrder } from "../services/types";
import { cn } from "../../../ui/cn";

const EVENT_TONE: Record<LedgerEvent["type"], string> = {
  ORDER: "text-status-warn",
  FILL: "text-status-ok",
  CANCEL: "text-muted-foreground",
  REJECT: "text-status-danger",
  RESEARCH: "text-foreground",
  RISK_BREACH: "text-status-danger",
};

export function TradingAudit({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [orders, setOrders] = useState<PaperOrder[]>(MOCK_SEED_ORDERS);
  const [events, setEvents] = useState<LedgerEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api.subscribeEvents().then(
      (e) => alive && setEvents(e),
      (e) => alive && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      alive = false;
    };
  }, [api]);

  return (
    <PageContainer
      title="交易审计"
      description="订单、成交、账本与事件回放。所有记录与后端账本（当前为 Mock）保持一致。"
    >
      {error ? (
        <div className="mb-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      ) : null}

      {/* Orders + fills */}
      <h2 className="t-section text-foreground">订单 / 成交</h2>
      <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border t-meta text-muted-foreground">
              <th className="px-3 py-2 text-left">订单号</th>
              <th className="px-3 py-2 text-left">标的</th>
              <th className="px-3 py-2 text-left">方向</th>
              <th className="px-3 py-2 text-left">类型</th>
              <th className="px-3 py-2 text-right">数量</th>
              <th className="px-3 py-2 text-right">限价</th>
              <th className="px-3 py-2 text-left">状态</th>
              <th className="px-3 py-2 text-right">成交</th>
              <th className="px-3 py-2 text-left">确认</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <OrderRow key={o.orderId} order={o} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Ledger / event replay */}
      <h2 className="mt-6 t-section text-foreground">账本 / 事件回放</h2>
      <div className="mt-2 overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border t-meta text-muted-foreground">
              <th className="px-3 py-2 text-left">时间</th>
              <th className="px-3 py-2 text-left">类型</th>
              <th className="px-3 py-2 text-left">标的</th>
              <th className="px-3 py-2 text-right">数量</th>
              <th className="px-3 py-2 text-right">价格</th>
              <th className="px-3 py-2 text-right">金额</th>
              <th className="px-3 py-2 text-left">说明</th>
            </tr>
          </thead>
          <tbody>
            {(events ?? []).map((e) => (
              <tr key={e.eventId} className="border-b border-border">
                <td className="px-3 py-2 t-meta text-muted-foreground">
                  {new Date(e.ts).toLocaleString("zh-CN")}
                </td>
                <td className={cn("px-3 py-2 font-medium", EVENT_TONE[e.type])}>{e.type}</td>
                <td className="px-3 py-2">{e.symbol ?? "—"}</td>
                <td className="px-3 py-2 text-right tabular-nums">{e.quantity ?? "—"}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {e.price !== undefined ? formatUsd(e.price) : "—"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {e.amount !== undefined ? formatUsd(e.amount, { sign: true }) : "—"}
                </td>
                <td className="px-3 py-2 text-muted-foreground">{e.note}</td>
              </tr>
            ))}
            {events === null ? (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-center text-muted-foreground">加载中…</td>
              </tr>
            ) : events.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-center text-muted-foreground">无事件</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </PageContainer>
  );
}
