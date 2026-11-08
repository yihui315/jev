// Page 2 — 专业交易工作台. Real K-line (lightweight-charts), real depth order
// book, real trades, real 24h ticker, and a paper order form that ONLY posts to
// the server-authoritative engine. No client-side pricing, no client fills.
import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, ColorType } from "lightweight-charts";
import { useJevQuery } from "../api/useJev";
import { jevFetch, placePaperOrder, makeIds } from "../api/client";
import { Panel, Metric, Banner, StatusPill, SourceFooter, StateView, fmtUsd, fmtPrice, fmtQty } from "../components/common";
import { TopBar } from "../components/Shell";

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];

// lightweight-charts v4 exports value API; series/chart handles are typed as any
// to keep the trading terminal decoupled from the chart lib's internal types.
type AnyChart = any;
type AnySeries = any;
type Candle = { time: number; open: number; high: number; low: number; close: number };

function KlineChart({ symbol, interval }: { symbol: string; interval: string }) {
  const kq = useJevQuery<any[][]>(`market/klines?symbol=${symbol}&interval=${interval}&limit=200`);
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<AnyChart | null>(null);
  const seriesRef = useRef<AnySeries | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: "#0D141F" }, textColor: "#7C8DA3", fontFamily: "ui-monospace, monospace" },
      grid: { vertLines: { color: "#1B2A3B" }, horzLines: { color: "#1B2A3B" } },
      width: ref.current.clientWidth, height: 320,
      rightPriceScale: { borderColor: "#1B2A3B" },
      timeScale: { borderColor: "#1B2A3B", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#22D3A6", downColor: "#FF5A6A", borderVisible: false,
      wickUpColor: "#22D3A6", wickDownColor: "#FF5A6A",
    });
    chartRef.current = chart; seriesRef.current = series;
    const onResize = () => { if (ref.current) chart.applyOptions({ width: ref.current.clientWidth }); };
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.remove(); chartRef.current = null; seriesRef.current = null; };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !kq.data) return;
    const data: Candle[] = kq.data.map((k) => ({
      time: Math.floor(Number(k[0]) / 1000),
      open: Number(k[1]), high: Number(k[2]), low: Number(k[3]), close: Number(k[4]),
    }));
    seriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [kq.data]);

  return (
    <Panel title="K 线" sub={`${symbol} · ${interval}`} right={<StatusPill label="LIVE" />}>
      <StateView q={kq} emptyText="暂无 K 线数据" />
      <div ref={ref} style={{ width: "100%" }} />
      <SourceFooter meta={kq.meta} />
    </Panel>
  );
}

function OrderBook({ symbol }: { symbol: string }) {
  const dq = useJevQuery<{ bids: [string, string][]; asks: [string, string][] }>(`market/depth?symbol=${symbol}&limit=20`);
  const depth = dq.data;
  const asks = useMemo(() => (depth?.asks || []).slice(0, 12).reverse(), [depth]);
  const bids = useMemo(() => (depth?.bids || []).slice(0, 12), [depth]);
  const maxQ = Math.max(0, ...[...asks, ...bids].map((r) => Number(r[1])));
  return (
    <Panel title="盘口深度" sub={symbol} right={<StatusPill label="LIVE" />}>
      <StateView q={dq} emptyText="暂无盘口" />
      <div className="jev-book">
        <div className="head jev-row" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr" }}>
          <span style={{ textAlign: "left" }}>价格</span><span style={{ textAlign: "right" }}>数量</span><span style={{ textAlign: "right" }}>累计</span>
        </div>
        <div className="asks">
          {asks.map((r, i) => {
            const tot = asks.slice(0, i + 1).reduce((a, b) => a + Number(b[1]), 0);
            return (
              <div className="row" key={"a" + i}>
                <span className="bar" style={{ background: "var(--down)", width: `${(Number(r[1]) / maxQ) * 100}%` }} />
                <span className="px">{fmtPrice(r[0])}</span>
                <span>{fmtQty(r[1])}</span><span className="tot">{fmtQty(tot)}</span>
              </div>
            );
          })}
        </div>
        <div className="mid">— 买卖中间价 —</div>
        <div className="bids">
          {bids.map((r, i) => {
            const tot = bids.slice(0, i + 1).reduce((a, b) => a + Number(b[1]), 0);
            return (
              <div className="row" key={"b" + i}>
                <span className="bar" style={{ background: "var(--up)", width: `${(Number(r[1]) / maxQ) * 100}%` }} />
                <span className="px">{fmtPrice(r[0])}</span>
                <span>{fmtQty(r[1])}</span><span className="tot">{fmtQty(tot)}</span>
              </div>
            );
          })}
        </div>
      </div>
      <SourceFooter meta={dq.meta} />
    </Panel>
  );
}

function OrderForm({ symbol, onPlaced }: { symbol: string; onPlaced: () => void }) {
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [qty, setQty] = useState("0.0002");
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit() {
    setBusy(true); setMsg(null);
    // P0.2: reject invalid quantity / price at the client before any server call.
    // The server remains authoritative, but a malformed input must never be sent.
    const qtyN = Number(qty);
    if (!Number.isFinite(qtyN) || qtyN <= 0) {
      setBusy(false);
      setMsg({ ok: false, text: `拒绝：数量无效（${qty}），必须是大于 0 的数字` });
      return;
    }
    if (type === "LIMIT") {
      const lp = Number(limit);
      if (!Number.isFinite(lp) || lp <= 0) {
        setBusy(false);
        setMsg({ ok: false, text: `拒绝：限价无效（${limit}），必须是大于 0 的数字` });
        return;
      }
    }
    const ids = makeIds("wd");
    const body: any = {
      clientOrderId: ids.clientOrderId, confirmationId: ids.confirmationId,
      symbol, side, type, quantity: qtyN,
    };
    if (type === "LIMIT") body.limitPrice = Number(limit);
    const r = await placePaperOrder(body);
    setBusy(false);
    if (r.ok) {
      setMsg({ ok: true, text: `已提交，订单状态：${((r.data as any)?.account?.orders?.[0]?.status) ?? "?"}（由服务端撮合）` });
      onPlaced();
    } else {
      setMsg({ ok: false, text: `拒绝：${r.error || r.code}（服务端返回，非客户端）` });
    }
  }

  return (
    <Panel title="模拟下单" sub="仅经服务端权威引擎">
      <div className="jev-form">
        <div className="jev-row">
          <div className="jev-field"><label>方向</label>
            <div className="jev-seg">
              <button className={side === "BUY" ? "on" : ""} onClick={() => setSide("BUY")}>买入</button>
              <button className={side === "SELL" ? "on" : ""} onClick={() => setSide("SELL")}>卖出</button>
            </div>
          </div>
          <div className="jev-field"><label>类型</label>
            <div className="jev-seg">
              <button className={type === "MARKET" ? "on" : ""} onClick={() => setType("MARKET")}>市价</button>
              <button className={type === "LIMIT" ? "on" : ""} onClick={() => setType("LIMIT")}>限价</button>
            </div>
          </div>
        </div>
        <div className="jev-field"><label>数量 ({symbol.replace("USDT", "")})</label>
          <input className="jev-input" value={qty} onChange={(e) => setQty(e.target.value)} inputMode="decimal" />
        </div>
        {type === "LIMIT" ? (
          <div className="jev-field"><label>限价 (USDT)</label>
            <input className="jev-input" value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" />
          </div>
        ) : null}
        <button className={`jev-btn ${side === "BUY" ? "buy" : "sell"}`} disabled={busy} onClick={submit}>
          {busy ? "提交中…" : `${side === "BUY" ? "买入" : "卖出"} ${symbol}（模拟）`}
        </button>
        {msg ? <div className={msg.ok ? "jev-up" : "jev-down"} style={{ fontSize: 12 }}>{msg.text}</div> : null}
        <div style={{ color: "var(--muted)", fontSize: 11 }}>
          仅支持 BTC/ETH/ORDI/1000SATS。BNB/SOL 仅有采集层数据，冻结引擎白名单不支持交易。本页绝不伪造成交。
        </div>
      </div>
    </Panel>
  );
}

function AccountAndOrders({ refreshKey }: { refreshKey: number }) {
  const acc = useJevQuery<any>("paper/account", { enabled: true, intervalMs: 4000 });
  const orders = useJevQuery<any>("paper/orders", { enabled: true, intervalMs: 4000 });
  const ev = useJevQuery<any>("paper/events?after=0&limit=20", { intervalMs: 6000 });
  // re-fetch after a placement
  useEffect(() => { acc.refetch(); orders.refetch(); }, [refreshKey]);
  const a = acc.data?.account;
  const s = acc.data?.summary;

  return (
    <>
      <Panel title="模拟账户" sub="服务端权威" right={<StatusPill label="LIVE" />}>
        <StateView q={acc} />
        {s ? (
          <div className="jev-grid cols-4">
            <Metric label="净值" value={fmtUsd(s.equity)} />
            <Metric label="可用现金" value={fmtUsd(s.availableCash)} />
            <Metric label="市值" value={fmtUsd(s.marketValue)} />
            <Metric label="未实现盈亏" value={fmtUsd(s.unrealizedPnl)} tone={s.unrealizedPnl >= 0 ? "up" : "down"} />
            <Metric label="已实现盈亏" value={fmtUsd(s.realizedPnl)} tone={s.realizedPnl >= 0 ? "up" : "down"} />
            <Metric label="总盈亏" value={fmtUsd(s.totalPnl)} tone={s.totalPnl >= 0 ? "up" : "down"} />
            <Metric label="日盈亏" value={fmtUsd(s.dailyPnl)} tone={s.dailyPnl >= 0 ? "up" : "down"} />
            <Metric label="回撤" value={(Number(s.drawdown) * 100).toFixed(2) + "%"} />
          </div>
        ) : null}
        {a?.positions && Object.keys(a.positions).length ? (
          <table className="jev-table" style={{ marginTop: 10 }}>
            <thead><tr><th>持仓</th><th>数量</th><th>均价</th><th>现价</th><th>市值</th><th>未实现</th></tr></thead>
            <tbody>
                {Object.entries(a.positions).map(([sym, p]: any) => (
                <tr key={sym}><td>{sym}</td><td>{fmtQty(p.qty)}</td><td>{fmtPrice(p.avgPrice)}</td><td>{fmtPrice(p.mark)}</td><td>{fmtUsd(p.marketValue)}</td><td className={p.unrealizedPnl >= 0 ? "jev-up" : "jev-down"}>{fmtUsd(p.unrealizedPnl)}</td></tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <SourceFooter meta={acc.meta} />
      </Panel>

      <Panel title="委托 / 成交" sub={`orders ${orders.data?.orders?.length ?? 0}`} right={<StatusPill label="LIVE" />}>
        <StateView q={orders} emptyText="无委托记录" />
        {orders.data?.orders?.length ? (
          <table className="jev-table">
            <thead><tr><th>ID</th><th>标的</th><th>方向</th><th>类型</th><th>数量</th><th>成交价</th><th>状态</th><th>成交</th></tr></thead>
            <tbody>
                {orders.data.orders.map((o: any) => (
                <tr key={o.id}><td>{o.clientOrderId}</td><td>{o.symbol}</td><td className={o.side === "BUY" ? "jev-up" : "jev-down"}>{o.side}</td><td>{o.type}</td><td>{fmtQty(o.qty)}</td><td>{o.avgFillPrice ? fmtPrice(o.avgFillPrice) : "—"}</td><td>{o.status}</td><td>{fmtQty(o.filledQty)}</td></tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <SourceFooter meta={orders.meta} />
      </Panel>

      <Panel title="服务端事件流" sub={`events ${ev.data?.events?.length ?? 0}`} right={<StatusPill label="LIVE" />}>
        <StateView q={ev} emptyText="无服务端事件" />
        {ev.data?.events?.length ? (
          <table className="jev-table">
            <thead><tr><th>seq</th><th>事件类型</th><th>时间</th></tr></thead>
            <tbody>
              {ev.data.events.map((e: any) => (
                <tr key={`${e.seq}-${e.eventType}`}><td>{e.seq}</td><td style={{ textAlign: "left" }}>{e.eventType}</td><td>{e.createdAt}</td></tr>
              ))}
            </tbody>
          </table>
        ) : null}
        <SourceFooter meta={ev.meta} />
      </Panel>
    </>
  );
}

export function TradingWorkbench({ topbar }: { topbar: React.ReactNode }) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setInterval] = useState("1h");
  const [refreshKey, setRefreshKey] = useState(0);
  const ticker = useJevQuery<any[]>(`market/ticker?symbols=${encodeURIComponent(JSON.stringify(SYMBOLS))}`);
  const trades = useJevQuery<any[]>(`market/trades?symbol=${symbol}&limit=30`);

  // Ensure a server session exists (HttpOnly+Secure cookie) so the account and
  // order reads return real state rather than 401. Same pattern as PaperAccount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const r = await jevFetch("paper/session", { method: "POST", body: {} });
      if (!cancelled && !r.ok) console.warn("paper session ensure failed", r.error);
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      {topbar}
      <div className="jev-content">
        <Banner kind="info">
          本页所有行情为 <b>真实公开 REST 数据</b>（Binance Vision 公共接口），通道为 <b>REST_POLLING</b>。
          WebSocket 尚未打通，因此绝不标为 WebSocket LIVE。所有模拟成交均由<b>服务端权威引擎</b>撮合，客户端不定价、不记账。
        </Banner>

        <div className="jev-row" style={{ marginBottom: 12, flexWrap: "wrap" }}>
          <div className="jev-seg">
            {SYMBOLS.map((s) => <button key={s} className={symbol === s ? "on" : ""} onClick={() => setSymbol(s)}>{s.replace("USDT", "")}</button>)}
          </div>
          <div className="jev-seg">
            {INTERVALS.map((i) => <button key={i} className={interval === i ? "on" : ""} onClick={() => setInterval(i)}>{i}</button>)}
          </div>
          <div className="spacer" style={{ flex: 1 }} />
          {ticker.data?.map((t: any) => {
            const chg = Number(t.priceChangePercent);
            return <span key={t.symbol} className="jev-metric" style={{ minWidth: 110 }}><span className="label">{t.symbol}</span><span className={`value sm ${chg >= 0 ? "jev-up" : "jev-down"}`}>{fmtPrice(t.lastPrice)}</span></span>;
          })}
        </div>

        <div className="jev-grid cols-3" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
          <div style={{ gridColumn: "span 2" }}><KlineChart symbol={symbol} interval={interval} /></div>
          <OrderForm symbol={symbol} onPlaced={() => setRefreshKey((k) => k + 1)} />
          <OrderBook symbol={symbol} />
          <Panel title="逐笔成交" sub={symbol} right={<StatusPill label="LIVE" />}>
            <StateView q={trades} emptyText="暂无成交" />
            {trades.data?.map((t: any) => (
              <div className="jev-row" key={t.id} style={{ justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                <span className="jev-up">{fmtPrice(t.price)}</span>
                <span>{fmtQty(t.qty)}</span>
                <span style={{ color: "var(--muted)" }}>{new Date(Number(t.time)).toLocaleTimeString("en-US")}</span>
              </div>
            ))}
            <SourceFooter meta={trades.meta} />
          </Panel>
        </div>

        <div className="jev-grid cols-2" style={{ marginTop: 14 }}>
          <AccountAndOrders refreshKey={refreshKey} />
        </div>
      </div>
    </>
  );
}
