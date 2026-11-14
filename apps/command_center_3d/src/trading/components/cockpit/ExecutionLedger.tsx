// V2 Cockpit — Region 4: the paper ledger (orders / fills / positions / fees)
// plus the server event stream. PURE PRESENTATION over parent-supplied raw
// payloads + honest SourceStatus. Cancellation goes ONLY through client.ts.
import { useState, type ReactNode } from "react";
import { fmtPrice, fmtQty } from "../common";
import { cancelPaperOrder } from "../../api/client";
import { DomainBadge, statusBadge, type SourceStatus } from "./MarketPanel";

type Row = Record<string, unknown>;
function arr(x: unknown): Row[] { return Array.isArray(x) ? (x as Row[]) : []; }
function obj(x: unknown): Row { return x && typeof x === "object" ? (x as Row) : {}; }
function pick(o: Row, keys: string[]): unknown {
  for (const k of keys) if (o[k] !== undefined && o[k] !== null) return o[k];
  return undefined;
}

// Fees are shown ONLY when a real fee/commission field exists on the server
// row. No client-side calculation, no invented numbers.
const FEE_KEYS = ["fee", "feeUsd", "feeUSD", "commission", "commissionAmount", "commissionUsd", "feeAmount", "fillFee", "feePaid"];
function feeOf(o: Row): unknown { return pick(o, FEE_KEYS); }

const CLOSED = new Set(["FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"]);
function cancelable(status: unknown): boolean { return !CLOSED.has(String(status ?? "").toUpperCase()); }

function rowsNA(status: SourceStatus, rows: unknown[]): string | null {
  if (status.unreachable || status.error || status.label === "UNAVAILABLE") return "N/A（接口不可用）";
  if (!rows.length) return status.loading ? "读取中…" : "N/A（暂无数据）";
  return null;
}

function Block({ title, status, domain, na, children }: {
  title: string; status: SourceStatus; domain: "PAPER"; na: string | null; children: ReactNode;
}) {
  const b = statusBadge(status);
  return (
    <div className="cockpit-panel" style={{ minHeight: 150 }}>
      <div className="ck-head">
        <span className="ck-title">{title}</span>
        <span className="ck-spacer" />
        <DomainBadge domain={domain} />
        <span className={b.cls}>{b.text}</span>
      </div>
      <div className="ck-body" style={{ overflow: "auto" }}>
        {na ? <div className="ck-na">{na}</div> : children}
      </div>
    </div>
  );
}

export function ExecutionLedger({ account, accountStatus, orders, ordersStatus, events, onChanged }: {
  account: unknown; accountStatus: SourceStatus;
  orders: unknown; ordersStatus: SourceStatus;
  events: unknown; onChanged: () => void;
}) {
  const acc = obj(account);
  const inner = obj(acc.account ?? acc);
  const ord = obj(orders);
  const ev = obj(events);

  const orderRows = arr(ord.orders ?? inner.orders);
  const tradeRows = arr(ord.trades ?? inner.trades ?? inner.fills);
  const eventRows = arr(ev.events);
  const positions = inner.positions ?? {};
  const posRows: Row[] = Array.isArray(positions)
    ? positions
    : Object.entries(obj(positions)).map(([sym, p]) => ({ symbol: sym, ...obj(p) }));
  const feeRows = [...orderRows, ...tradeRows]
    .map((o) => ({ sym: pick(o, ["symbol"]) ?? "—", fee: feeOf(o) }))
    .filter((x) => x.fee !== undefined);

  const evStatus: SourceStatus = { label: ev && eventRows.length ? "LIVE" : "NO_DATA", unreachable: false, loading: false, hasData: eventRows.length > 0 };
  const evBadge = statusBadge(evStatus);

  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [cancelMsg, setCancelMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function doCancel(id: string) {
    setBusyId(id); setCancelMsg(null);
    const r = await cancelPaperOrder(id);
    setBusyId(null); setConfirmId(null);
    setCancelMsg({
      ok: r.ok,
      text: `撤单返回码：HTTP ${r.status} · code: ${r.code ?? "—"} · ok: ${String(r.ok)}${r.error ? ` · ${r.error}` : ""}`,
    });
    if (r.ok) onChanged();
  }

  const gridStyle = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 10 };

  return (
    <section className="cockpit-panel" style={{ height: "100%" }}>
      <div className="ck-head">
        <span className="ck-title">账本 · 订单 / 成交 / 持仓 / 手续费</span>
        <span className="ck-spacer" />
        <DomainBadge domain="PAPER" />
        <span className="ck-badge">orders {orderRows.length} · fills {tradeRows.length} · events {eventRows.length}</span>
      </div>
      <div className="ck-body" style={{ overflow: "auto" }}>
        <div style={gridStyle}>
          {/* Orders */}
          <Block title="订单流水" status={ordersStatus} domain="PAPER" na={rowsNA(ordersStatus, orderRows)}>
            <table className="ck-table">
              <thead><tr><th>ID</th><th>状态</th><th>标的</th><th>方向</th><th>数量</th><th>操作</th></tr></thead>
              <tbody>{orderRows.map((o, i) => {
                const id = String(pick(o, ["id", "orderId", "clientOrderId"]) ?? i);
                const status = pick(o, ["status"]);
                const sym = String(pick(o, ["symbol"]) ?? "—");
                return (
                  <tr key={i}>
                    <td title={id}>{id.length > 12 ? id.slice(0, 12) + "…" : id}</td>
                    <td>{String(status ?? "—")}</td>
                    <td>{sym}</td>
                    <td className={String(pick(o, ["side"])) === "BUY" ? "ck-up" : "ck-down"}>{String(pick(o, ["side"]) ?? "—")}</td>
                    <td>{fmtQty(pick(o, ["qty", "quantity", "origQty"]))}</td>
                    <td>{cancelable(status) ? (
                      confirmId === id ? (
                        <span>
                          <button className="ck-btn confirm" style={{ padding: "2px 6px", fontSize: 10 }} disabled={busyId === id} onClick={() => doCancel(id)}>{busyId === id ? "…" : "确认撤单"}</button>
                          <button className="ck-btn ghost" style={{ padding: "2px 6px", fontSize: 10, marginLeft: 4 }} onClick={() => setConfirmId(null)}>取消</button>
                        </span>
                      ) : (
                        <button className="ck-btn ghost" style={{ padding: "2px 6px", fontSize: 10 }} onClick={() => setConfirmId(id)}>撤单</button>
                      )
                    ) : "—"}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </Block>

          {/* Fills */}
          <Block title="成交记录" status={ordersStatus} domain="PAPER" na={rowsNA(ordersStatus, tradeRows)}>
            <table className="ck-table">
              <thead><tr><th>标的</th><th>数量</th><th>成交价</th><th>方向</th></tr></thead>
              <tbody>{tradeRows.map((t, i) => (
                <tr key={i}>
                  <td>{String(pick(t, ["symbol"]) ?? "—")}</td>
                  <td>{fmtQty(pick(t, ["qty", "quantity", "filledQty"]))}</td>
                  <td>{fmtPrice(pick(t, ["price", "fillPrice", "avgFillPrice"]))}</td>
                  <td className={String(pick(t, ["side"])) === "BUY" ? "ck-up" : "ck-down"}>{String(pick(t, ["side"]) ?? "—")}</td>
                </tr>
              ))}</tbody>
            </table>
          </Block>

          {/* Positions */}
          <Block title="持仓" status={accountStatus} domain="PAPER" na={rowsNA(accountStatus, posRows)}>
            <table className="ck-table">
              <thead><tr><th>标的</th><th>数量</th><th>均价</th><th>现价</th></tr></thead>
              <tbody>{posRows.map((p, i) => (
                <tr key={i}>
                  <td>{String(pick(p, ["symbol"]) ?? "—")}</td>
                  <td>{fmtQty(pick(p, ["qty", "quantity"]))}</td>
                  <td>{fmtPrice(pick(p, ["avgPrice", "entryPrice"]))}</td>
                  <td>{fmtPrice(pick(p, ["mark", "markPrice", "lastPrice"]))}</td>
                </tr>
              ))}</tbody>
            </table>
          </Block>

          {/* Fees (only real fields) */}
          <Block title="手续费" status={ordersStatus} domain="PAPER" na={feeRows.length ? null : "N/A（无真实手续费字段）"}>
            <table className="ck-table">
              <thead><tr><th>标的</th><th>手续费</th></tr></thead>
              <tbody>{feeRows.map((f, i) => (
                <tr key={i}><td>{String(f.sym)}</td><td>{fmtPrice(f.fee)}</td></tr>
              ))}</tbody>
            </table>
          </Block>
        </div>

        {cancelMsg ? <div className={`ck-msg ${cancelMsg.ok ? "ok" : "err"}`} style={{ marginTop: 8 }}>{cancelMsg.text}</div> : null}

        <div className="ck-source">
          <span>账户 <span className="v">{statusBadge(accountStatus).text}</span></span>
          <span>委托 <span className="v">{statusBadge(ordersStatus).text}</span></span>
          <span>服务端事件 <span className="v">{evBadge.text} · {eventRows.length}</span></span>
          <span className="v">会话作用域 · 服务端权威账本</span>
        </div>
      </div>
    </section>
  );
}
