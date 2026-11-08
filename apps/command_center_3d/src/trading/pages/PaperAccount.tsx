// Page 5 — 模拟交易账户. Real session, real server-authoritative account, real
// orders/trades/events, and an honest event-replay verification. Equity curve is
// shown ONLY when real snapshots exist; otherwise NO_DATA (never synthesized).
import { useEffect, useState } from "react";
import { useJevQuery } from "../api/useJev";
import { jevFetch, cancelPaperOrder } from "../api/client";
import { Panel, Metric, Banner, StatusPill, SourceFooter, StateView, fmtUsd, fmtPrice, fmtQty } from "../components/common";
import { TopBar } from "../components/Shell";

export function PaperAccount({ topbar }: { topbar: React.ReactNode }) {
  const [sessionReady, setSessionReady] = useState(false);

  // Ensure a server session exists once on mount (cookie is HttpOnly+Secure).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const r = await jevFetch("paper/session", { method: "POST", body: {} });
      if (!cancelled && r.ok) setSessionReady(true);
    })();
    return () => { cancelled = true; };
  }, []);

  const acc = useJevQuery<any>("paper/account", { intervalMs: 4000 });
  const orders = useJevQuery<any>("paper/orders", { intervalMs: 4000 });
  const events = useJevQuery<any>("paper/events?after=0&limit=100", { intervalMs: 6000 });
  const replay = useJevQuery<any>("paper/replay", { intervalMs: 8000 });
  const equity = useJevQuery<any>("paper/equity", { intervalMs: 8000 });

  const s = acc.data?.summary;
  const [cancelMsg, setCancelMsg] = useState<string | null>(null);

  async function cancel(id: string) {
    setCancelMsg(null);
    const r = await cancelPaperOrder(id);
    setCancelMsg(r.ok ? `已请求撤单 ${id}` : `撤单失败：${r.error || r.code}`);
    orders.refetch();
  }

  return (
    <>
      {topbar}
      <div className="jev-content">
        <Banner kind="info">
          账户、持仓、委托、成交、事件全部来自<b>服务端权威 Paper 引擎</b>的真实 SQLite 状态。
          下方「事件回放」对服务端状态哈希链做独立验证——若回放结果与物化状态一致，则证明账户未被篡改。
        </Banner>

        <div className="jev-grid cols-4">
          <Panel title="净值 / 现金" right={<StatusPill label="LIVE" />}>
            <Metric label="净值" value={s ? fmtUsd(s.equity) : "—"} />
            <Metric label="可用现金" value={s ? fmtUsd(s.availableCash) : "—"} />
            <Metric label="市值" value={s ? fmtUsd(s.marketValue) : "—"} />
            <SourceFooter meta={acc.meta} />
          </Panel>
          <Panel title="盈亏" right={<StatusPill label="LIVE" />}>
            <Metric label="未实现" value={s ? fmtUsd(s.unrealizedPnl) : "—"} tone={s && s.unrealizedPnl >= 0 ? "up" : "down"} />
            <Metric label="已实现" value={s ? fmtUsd(s.realizedPnl) : "—"} tone={s && s.realizedPnl >= 0 ? "up" : "down"} />
            <Metric label="总盈亏" value={s ? fmtUsd(s.totalPnl) : "—"} tone={s && s.totalPnl >= 0 ? "up" : "down"} />
            <SourceFooter meta={acc.meta} />
          </Panel>
          <Panel title="风险" right={<StatusPill label="LIVE" />}>
            <Metric label="回撤" value={s ? (Number(s.drawdown) * 100).toFixed(2) + "%" : "—"} />
            <Metric label="敞口占比" value={s ? (Number(s.exposurePct) * 100).toFixed(1) + "%" : "—"} />
            <Metric label="日亏损熔断" value={s ? (s.dailyLossLocked ? "触发" : "正常") : "—"} tone={s && s.dailyLossLocked ? "down" : undefined} />
            <SourceFooter meta={acc.meta} />
          </Panel>
          <Panel title="事件回放验证" right={<StatusPill label={replay.data?.verified ? "VERIFIED" : "LIVE"} />}>
            <Metric label="回放结果" value={replay.data ? (replay.data.verified ? "一致 ✓" : "不一致 ✗") : "—"} tone={replay.data?.verified ? "up" : replay.data ? "down" : undefined} />
            <Metric label="快照数" value={replay.data?.snapshots ?? "—"} />
            <Metric label="最后序列" value={replay.data?.lastSequence ?? "—"} />
            <SourceFooter meta={replay.meta} />
          </Panel>
        </div>

        <div className="jev-grid cols-2" style={{ marginTop: 14 }}>
          <Panel title="持仓" sub={s ? `${s.positions?.length ?? 0} 个` : ""} right={<StatusPill label="LIVE" />}>
            <StateView q={acc} emptyText="无持仓" />
            {s?.positions?.length ? (
              <table className="jev-table">
                <thead><tr><th>标的</th><th>数量</th><th>均价</th><th>现价</th><th>市值</th><th>未实现</th></tr></thead>
                <tbody>
                  {s.positions.map((p: any) => (
                    <tr key={p.symbol}><td>{p.symbol}</td><td>{fmtQty(p.qty)}</td><td>{fmtPrice(p.avgPrice)}</td><td>{fmtPrice(p.mark)}</td><td>{fmtUsd(p.marketValue)}</td><td className={p.unrealizedPnl >= 0 ? "jev-up" : "jev-down"}>{fmtUsd(p.unrealizedPnl)}</td></tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            <SourceFooter meta={acc.meta} />
          </Panel>

          <Panel title="权益曲线" sub={equity.data?.table_present ? `${equity.data.count} 点` : "快照表未创建"} right={<StatusPill label={equity.data?.count ? "LIVE" : "NO_DATA"} />}>
            <StateView q={equity} emptyText="无权益快照：冻结引擎前 3 个迁移未创建 equity 快照表，因此不展示任何曲线（绝不伪造）" />
            {equity.data?.count ? (
              <table className="jev-table">
                <thead><tr><th>捕获时间</th><th>净值</th><th>现金</th></tr></thead>
                <tbody>
                  {equity.data.snapshots.map((e: any, i: number) => (
                    <tr key={e.captured_at ?? i}><td>{e.captured_at}</td><td>{fmtUsd(e.equity)}</td><td>{fmtUsd(e.cash)}</td></tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            <SourceFooter meta={equity.meta} />
          </Panel>
        </div>

        <Panel title="委托与撤单" sub={`${orders.data?.orders?.length ?? 0} 笔`} right={<StatusPill label="LIVE" />} >
          <StateView q={orders} emptyText="无委托" />
          {orders.data?.orders?.length ? (
            <table className="jev-table">
              <thead><tr><th>clientOrderId</th><th>标的</th><th>方向</th><th>类型</th><th>数量</th><th>成交价</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                  {orders.data.orders.map((o: any) => (
                    <tr key={o.id}><td style={{ textAlign: "left" }}>{o.clientOrderId}</td><td>{o.symbol}</td><td className={o.side === "BUY" ? "jev-up" : "jev-down"}>{o.side}</td><td>{o.type}</td><td>{fmtQty(o.qty)}</td><td>{o.avgFillPrice ? fmtPrice(o.avgFillPrice) : "—"}</td><td>{o.status}</td><td>{o.status === "NEW" || o.status === "PARTIALLY_FILLED" ? <button className="jev-link" onClick={() => cancel(o.id)}>撤单</button> : "—"}</td></tr>
                  ))}
              </tbody>
            </table>
          ) : null}
          {cancelMsg ? <div style={{ marginTop: 8, fontSize: 12 }} className={cancelMsg.startsWith("已") ? "jev-up" : "jev-down"}>{cancelMsg}</div> : null}
          <SourceFooter meta={orders.meta} />
        </Panel>

        <Panel title="服务端事件流（账户状态哈希链）" sub={`${events.data?.events?.length ?? 0} 条`} right={<StatusPill label="LIVE" />}>
          <StateView q={events} emptyText="无事件" />
          {events.data?.events?.length ? (
            <table className="jev-table">
              <thead><tr><th>seq</th><th>事件类型</th><th>时间</th></tr></thead>
              <tbody>
                {events.data.events.map((e: any) => (
                  <tr key={`${e.seq}-${e.eventType}`}><td>{e.seq ?? "—"}</td><td style={{ textAlign: "left" }}>{e.eventType}</td><td>{e.createdAt}</td></tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <SourceFooter meta={events.meta} />
        </Panel>
      </div>
    </>
  );
}
