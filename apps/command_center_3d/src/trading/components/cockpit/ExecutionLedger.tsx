// V2 Cockpit — Region 4: the paper ledger (orders / fills / positions / fees)
// plus the server event stream. PURE PRESENTATION over parent-supplied raw
// payloads + honest SourceStatus. Cancellation goes ONLY through client.ts.
import { useState, type CSSProperties, type ReactNode } from "react";
import { fmtPrice, fmtQty } from "../common";
import { cancelPaperOrder } from "../../api/client";
import { DomainBadge, statusBadge, type SourceStatus } from "./MarketPanel";
import { COCKPIT_PALETTE as P } from "../../metrics/contracts";

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

// High-density table tokens. Terminal semantics kept: --up green / --down red
// (via the shared palette), never reversed.
const tableStyle: CSSProperties = {
  width: "100%", borderCollapse: "collapse",
  fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
};
const denseTh: CSSProperties = {
  textAlign: "left", padding: "4px 6px", fontSize: 10, fontWeight: 700,
  color: P.muted, textTransform: "uppercase", letterSpacing: 0.4,
  borderBottom: `1px solid ${P.border}`, whiteSpace: "nowrap",
};
const denseTd: CSSProperties = {
  padding: "4px 6px", fontSize: 10.5, lineHeight: 1.3,
  borderBottom: `1px solid ${P.border}`, whiteSpace: "nowrap", color: P.text,
};

function statusTagStyle(status: unknown): CSSProperties {
  const st = String(status ?? "").toUpperCase();
  let color = P.muted, border = P.border, bg = "transparent";
  if (st === "FILLED") { color = P.up; border = P.up; bg = "rgba(34,211,166,.10)"; }
  else if (["NEW", "OPEN", "PARTIALLY_FILLED"].includes(st)) { color = P.amber; border = P.amber; bg = "rgba(245,181,68,.10)"; }
  else if (["REJECTED", "EXPIRED", "FAILED"].includes(st)) { color = P.down; border = P.down; bg = "rgba(255,90,106,.10)"; }
  return {
    display: "inline-block", padding: "1px 5px", borderRadius: 999,
    border: `1px solid ${border}`, background: bg, color,
    fontSize: 9.5, fontWeight: 700, letterSpacing: 0.3, whiteSpace: "nowrap",
  };
}

function Block({ title, status, domain, na, children }: {
  title: string; status: SourceStatus; domain: "PAPER"; na: string | null; children: ReactNode;
}) {
  const b = statusBadge(status);
  return (
    <div className="cockpit-panel" style={{ minHeight: 140 }}>
      <div className="ck-head">
        <span className="ck-title">{title}</span>
        <span className="ck-spacer" />
        <DomainBadge domain={domain} />
        <span className={b.cls}>{b.text}</span>
      </div>
      <div className="ck-body" style={{ overflow: "auto", padding: "8px 10px" }}>
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
          {/* Orders — two-step cancel logic below is intentionally unchanged */}
          <Block title="订单流水" status={ordersStatus} domain="PAPER" na={rowsNA(ordersStatus, orderRows)}>
            <table className="ck-table" style={tableStyle}>
              <thead><tr><th style={denseTh}>ID</th><th style={denseTh}>状态</th><th style={denseTh}>标的</th><th style={denseTh}>方向</th><th style={denseTh}>数量</th><th style={denseTh}>操作</th></tr></thead>
              <tbody>{orderRows.map((o, i) => {
                const id = String(pick(o, ["id", "orderId", "clientOrderId"]) ?? i);
                const status = pick(o, ["status"]);
                const sym = String(pick(o, ["symbol"]) ?? "—");
                return (
                  <tr key={i}>
                    <td style={denseTd} title={id}>{id.length > 12 ? id.slice(0, 12) + "…" : id}</td>
                    <td style={denseTd}><span style={statusTagStyle(status)}>{String(status ?? "—")}</span></td>
                    <td style={denseTd}>{sym}</td>
                    <td style={{ ...denseTd, color: String(pick(o, ["side"])) === "BUY" ? P.up : P.down }}>{String(pick(o, ["side"]) ?? "—")}</td>
                    <td style={denseTd}>{fmtQty(pick(o, ["qty", "quantity", "origQty"]))}</td>
                    <td style={denseTd}>{cancelable(status) ? (
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
            <table className="ck-table" style={tableStyle}>
              <thead><tr><th style={denseTh}>标的</th><th style={denseTh}>数量</th><th style={denseTh}>成交价</th><th style={denseTh}>方向</th></tr></thead>
              <tbody>{tradeRows.map((t, i) => (
                <tr key={i}>
                  <td style={denseTd}>{String(pick(t, ["symbol"]) ?? "—")}</td>
                  <td style={denseTd}>{fmtQty(pick(t, ["qty", "quantity", "filledQty"]))}</td>
                  <td style={denseTd}>{fmtPrice(pick(t, ["price", "fillPrice", "avgFillPrice"]))}</td>
                  <td style={{ ...denseTd, color: String(pick(t, ["side"])) === "BUY" ? P.up : P.down }}>{String(pick(t, ["side"]) ?? "—")}</td>
                </tr>
              ))}</tbody>
            </table>
          </Block>

          {/* Positions */}
          <Block title="持仓" status={accountStatus} domain="PAPER" na={rowsNA(accountStatus, posRows)}>
            <table className="ck-table" style={tableStyle}>
              <thead><tr><th style={denseTh}>标的</th><th style={denseTh}>数量</th><th style={denseTh}>均价</th><th style={denseTh}>现价</th></tr></thead>
              <tbody>{posRows.map((p, i) => (
                <tr key={i}>
                  <td style={denseTd}>{String(pick(p, ["symbol"]) ?? "—")}</td>
                  <td style={denseTd}>{fmtQty(pick(p, ["qty", "quantity"]))}</td>
                  <td style={denseTd}>{fmtPrice(pick(p, ["avgPrice", "entryPrice"]))}</td>
                  <td style={denseTd}>{fmtPrice(pick(p, ["mark", "markPrice", "lastPrice"]))}</td>
                </tr>
              ))}</tbody>
            </table>
          </Block>

          {/* Fees (only real fields) */}
          <Block title="手续费" status={ordersStatus} domain="PAPER" na={feeRows.length ? null : "N/A（无真实手续费字段）"}>
            <table className="ck-table" style={tableStyle}>
              <thead><tr><th style={denseTh}>标的</th><th style={denseTh}>手续费</th></tr></thead>
              <tbody>{feeRows.map((f, i) => (
                <tr key={i}><td style={denseTd}>{String(f.sym)}</td><td style={denseTd}>{fmtPrice(f.fee)}</td></tr>
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
