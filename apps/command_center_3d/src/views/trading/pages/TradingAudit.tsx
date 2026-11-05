/**
 * trading/pages/TradingAudit — 交易审计 (05 模拟交易账户 的审计视图)
 *
 * Orders and fills come from the authoritative account (`getAccount().account`),
 * and the ledger / event replay comes from the server event stream
 * (`getServerEvents`). The two never disagree because they share the server as
 * the single source of truth.
 */
import { useEffect, useState } from "react";
import { PageContainer } from "../components/PageContainer";
import { OrderRow } from "../components/OrderRow";
import { usePaperApi } from "../hooks/usePaperApi";
import { formatUsd } from "../lib/finance";
import type { AccountResponse, PaperOrder, ServerEvent } from "../services/types";
import { cn } from "../../../ui/cn";

const EVENT_TONE: Record<string, string> = {
  "paper.server.order.created": "text-status-warn",
  "paper.server.order.filled": "text-status-ok",
  "paper.server.order.rejected": "text-status-danger",
  "paper.server.order.cancelled": "text-status-warn",
  "paper.server.order.updated": "text-status-warn",
  "paper.server.account.reset": "text-status-info",
  "paper.server.account.snapshot": "text-muted-foreground",
};

export function TradingAudit({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [account, setAccount] = useState<AccountResponse | null>(null);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [events, setEvents] = useState<ServerEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void api.getAccount().then(
      (a) => {
        if (!alive) return;
        setAccount(a);
        setOrders(a.account.orders);
      },
      (e) => alive && setError(e instanceof Error ? e.message : String(e)),
    );
    void api.getServerEvents(0, 100).then(
      (r) => alive && setEvents(r.events),
      () => alive && setEvents([]),
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
              <OrderRow key={o.id} order={o} />
            ))}
            {orders.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-3 py-4 text-center text-muted-foreground">无订单</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

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
            {(events ?? []).map((e) => {
              const payload = (e.payload ?? {}) as {
                order?: { symbol?: string; qty?: number; reason?: string; rejectionCode?: string };
                reason?: string;
              };
              const symbol = payload.order?.symbol ?? "";
              const qty = payload.order?.qty;
              const reason = payload.order?.reason ?? payload.reason ?? "";
              return (
                <tr key={e.eventId} className="border-b border-border">
                  <td className="px-3 py-2 t-meta text-muted-foreground">
                    {new Date(e.createdAt).toLocaleString("zh-CN")}
                  </td>
                  <td className={cn("px-3 py-2 font-medium", EVENT_TONE[e.eventType] ?? "text-foreground")}>
                    {e.eventType.replace("paper.server.", "")}
                  </td>
                  <td className="px-3 py-2">{symbol || "—"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{qty ?? "—"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">—</td>
                  <td className="px-3 py-2 text-right tabular-nums">—</td>
                  <td className="px-3 py-2 text-muted-foreground">{reason}</td>
                </tr>
              );
            })}
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
