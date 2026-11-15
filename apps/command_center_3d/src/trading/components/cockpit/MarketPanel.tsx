// V2 Cockpit — central market block. PURE PRESENTATION + the paper order
// ticket. All market reads come from the parent (TradingCockpit) as raw
// payloads + an honest SourceStatus. Order actions go ONLY through the
// server-authoritative client.ts helpers; the client never prices or books.
import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, ColorType } from "lightweight-charts";
import { fmtPrice, fmtQty } from "../common";
import { makeIds, placePaperOrder } from "../../api/client";

// lightweight-charts v4 series/chart handles are kept as `any` (same approach
// as the existing TradingWorkbench) so the terminal is decoupled from the
// chart lib's internal branded types.
type AnyChart = any;
type AnySeries = any;
type Candle = { time: number; open: number; high: number; low: number; close: number; volume?: number };

// ---- honest data-source status (shared with ExecutionLedger) ----------------
// Deliberately uses meta.label ONLY — never deriveStatusLabel from
// src/views/trading (that helper mislabels real LIVE data as FIXTURE when
// USE_MOCK=true).
export interface SourceStatus {
  label?: string;
  unreachable: boolean;
  error?: string;
  loading: boolean;
  hasData: boolean;
}

export function statusBadge(s: SourceStatus): { text: string; cls: string } {
  if (s.unreachable || s.error || s.label === "UNAVAILABLE") return { text: "UNAVAILABLE", cls: "ck-badge unavailable" };
  switch (s.label) {
    case "LIVE": return { text: "LIVE", cls: "ck-badge live" };
    case "VERIFIED": return { text: "VERIFIED", cls: "ck-badge real" };
    case "BACKFILL": return { text: "BACKFILL", cls: "ck-badge real" };
    case "BLOCKED": return { text: "BLOCKED", cls: "ck-badge blocked" };
    case "NO_DATA": return { text: "NO_DATA", cls: "ck-badge nodata" };
    default: return { text: s.label ?? "—", cls: "ck-badge nodata" };
  }
}

export function statusNA(s: SourceStatus): string | null {
  if (s.unreachable || s.error || s.label === "UNAVAILABLE") return "N/A（接口不可用）";
  if (!s.hasData) return s.loading ? "读取中…" : "N/A（暂无数据）";
  return null;
}

export function DomainBadge({ domain }: { domain: "REAL_MARKET" | "PAPER" | "SYSTEM" }) {
  const cls = domain === "REAL_MARKET" ? "real" : domain === "PAPER" ? "paper" : "nodata";
  const text = domain === "REAL_MARKET" ? "REAL MARKET" : domain === "PAPER" ? "PAPER" : "SYSTEM";
  return <span className={`ck-badge ${cls}`}>{text}</span>;
}

// ---- K-line parsing (Binance klines: [openTime,O,H,L,C,volume,...]) --------
function parseKlines(rows: unknown): Candle[] {
  if (!Array.isArray(rows)) return [];
  const out: Candle[] = [];
  for (const k of rows) {
    if (!Array.isArray(k) || k.length < 5) continue;
    const time = Math.floor(Number(k[0]) / 1000);
    const open = Number(k[1]), high = Number(k[2]), low = Number(k[3]), close = Number(k[4]);
    if (![time, open, high, low, close].every(Number.isFinite)) continue;
    const v = Number(k[5]);
    out.push({ time, open, high, low, close, ...(Number.isFinite(v) ? { volume: v } : {}) });
  }
  return out.sort((a, b) => a.time - b.time);
}

// Technical indicators are DERIVED from real closes — never fabricated bars.
function sma(candles: Candle[], period: number): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;
    if (i >= period) sum -= candles[i - period].close;
    if (i >= period - 1) out.push({ time: candles[i].time, value: sum / period });
  }
  return out;
}

// ---- Region 2: K-line + volume + indicators ---------------------------------
export function KlinePanel({ symbol, interval, klines, status }: {
  symbol: string; interval: string; klines: unknown; status: SourceStatus;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<AnyChart | null>(null);
  const candleRef = useRef<AnySeries | null>(null);
  const volRef = useRef<AnySeries | null>(null);
  const ma7Ref = useRef<AnySeries | null>(null);
  const ma25Ref = useRef<AnySeries | null>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const chart = createChart(el, {
      layout: { background: { type: ColorType.Solid, color: "#0B121C" }, textColor: "#6E819A", fontFamily: "ui-monospace, monospace", fontSize: 10 },
      grid: { vertLines: { color: "#141F2E" }, horzLines: { color: "#141F2E" } },
      width: el.clientWidth || 600, height: 320,
      rightPriceScale: { borderColor: "#1E2C3E" },
      timeScale: { borderColor: "#1E2C3E", timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: { mode: 0 },
    });
    const candle = chart.addCandlestickSeries({
      upColor: "#1FD1A5", downColor: "#FF5364", borderVisible: false,
      wickUpColor: "#1FD1A5", wickDownColor: "#FF5364", priceLineColor: "#4C8DFF",
    });
    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const ma7 = chart.addLineSeries({ color: "#F5B544", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const ma25 = chart.addLineSeries({ color: "#4C8DFF", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    chartRef.current = chart; candleRef.current = candle; volRef.current = vol;
    ma7Ref.current = ma7; ma25Ref.current = ma25;
    // ResizeObserver (not just window.resize) so dragging the cockpit splitter
    // still reflows the chart width — no zero-width/zero-height chart.
    const ro = new ResizeObserver(() => {
      if (boxRef.current) chart.applyOptions({ width: boxRef.current.clientWidth });
    });
    ro.observe(el);
    return () => {
      ro.disconnect(); chart.remove();
      chartRef.current = candleRef.current = volRef.current = ma7Ref.current = ma25Ref.current = null;
    };
  }, []);

  const na = statusNA(status);
  const candles = useMemo(() => (na ? [] : parseKlines(klines)), [klines, na]);
  const hasVol = candles.length > 0 && candles.every((c) => c.volume !== undefined);

  useEffect(() => {
    if (!candleRef.current) return;
    candleRef.current.setData(candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
    // Volume histogram ONLY when every bar carries a real volume field.
    volRef.current?.setData(hasVol
      ? candles.map((c) => ({ time: c.time, value: c.volume as number, color: c.close >= c.open ? "rgba(31,209,165,.40)" : "rgba(255,83,100,.40)" }))
      : []);
    ma7Ref.current?.setData(sma(candles, 7));
    ma25Ref.current?.setData(sma(candles, 25));
    if (candles.length) chartRef.current?.timeScale().fitContent();
  }, [candles, hasVol]);

  const badge = statusBadge(status);
  return (
    <section className="cockpit-panel" style={{ height: "100%" }}>
      <div className="ck-head">
        <span className="ck-title">K 线 · 成交量 · 指标</span>
        <span className="ck-sub">{symbol} · {interval}</span>
        <span className="ck-spacer" />
        <DomainBadge domain="REAL_MARKET" />
        <span className={badge.cls}>{badge.text}</span>
      </div>
      <div className="ck-body flush" style={{ position: "relative", overflow: "hidden" }}>
        <div className="cockpit-chart-wrap">
          <div ref={boxRef} className="cockpit-chart" />
          <div className="cockpit-chart-legend">
            MA7 <span style={{ color: "#F5B544" }}>—</span> · MA25 <span style={{ color: "#4C8DFF" }}>—</span>{hasVol ? " · VOL" : " · 无 VOL 字段"}
          </div>
        </div>
        {na ? <div className="ck-na big" style={{ position: "absolute", inset: 0, margin: 0, height: "auto" }}>{na} · 行情不可用</div> : null}
        <div className="ck-source">
          <span>来源 <span className="v">{symbol} · {interval}</span></span>
          <span>通道 <span className="v">REST_POLLING</span></span>
          <span>标签 <span className="v">{badge.text}</span></span>
        </div>
      </div>
    </section>
  );
}

// ---- Region 3: order book + tape + order ticket -----------------------------
export interface OrderResult { ok: boolean; status: number; code?: string; error?: string; }

function OrderTicket({ symbol, onPlaced }: { symbol: string; onPlaced: () => void }) {
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [qty, setQty] = useState("0.0002");
  const [limit, setLimit] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OrderResult | null>(null);

  function validate(): string | null {
    const q = Number(qty);
    if (!Number.isFinite(q) || q <= 0) return `数量无效（${qty}）`;
    if (type === "LIMIT") {
      const l = Number(limit);
      if (!Number.isFinite(l) || l <= 0) return `限价无效（${limit}）`;
    }
    return null;
  }
  function beginConfirm() {
    setResult(null);
    const err = validate();
    if (err) { setResult({ ok: false, status: 0, error: `拒绝：${err}（客户端输入校验）` }); return; }
    setConfirming(true); // human must press a second, explicit confirm button
  }
  async function confirm() {
    setBusy(true);
    const ids = makeIds("ck");
    const body: {
      clientOrderId: string; confirmationId: string; symbol: string;
      side: "BUY" | "SELL"; type: "MARKET" | "LIMIT"; quantity: number; limitPrice?: number;
    } = { clientOrderId: ids.clientOrderId, confirmationId: ids.confirmationId, symbol, side, type, quantity: Number(qty) };
    if (type === "LIMIT") body.limitPrice = Number(limit);
    const r = await placePaperOrder(body);
    setBusy(false); setConfirming(false);
    setResult({ ok: r.ok, status: r.status, code: r.code, error: r.error });
    if (r.ok) onPlaced();
  }

  return (
    <div style={{ marginTop: 12, borderTop: "1px solid var(--ck-border)", paddingTop: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span className="ck-sub">模拟下单（服务端权威）</span>
        <DomainBadge domain="PAPER" />
      </div>
      {!confirming ? (
        <div className="ck-form">
          <div className="ck-toolbar">
            <div className="ck-seg">
              <button className={side === "BUY" ? "on" : ""} onClick={() => setSide("BUY")}>买入</button>
              <button className={side === "SELL" ? "on" : ""} onClick={() => setSide("SELL")}>卖出</button>
            </div>
            <div className="ck-seg">
              <button className={type === "MARKET" ? "on" : ""} onClick={() => setType("MARKET")}>市价</button>
              <button className={type === "LIMIT" ? "on" : ""} onClick={() => setType("LIMIT")}>限价</button>
            </div>
          </div>
          <div className="ck-field"><label>数量（{symbol.replace("USDT", "")}）</label>
            <input className="ck-input" value={qty} onChange={(e) => setQty(e.target.value)} inputMode="decimal" /></div>
          {type === "LIMIT" ? (
            <div className="ck-field"><label>限价（USDT）</label>
              <input className="ck-input" value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" /></div>
          ) : null}
          <button className={`ck-btn ${side === "BUY" ? "buy" : "sell"}`} onClick={beginConfirm}>提交订单 → 人工确认</button>
        </div>
      ) : (
        <div className="ck-form">
          <div className="ck-msg">请人工确认：<b>{side}</b> · {type} · {symbol} · 数量 {qty}{type === "LIMIT" ? ` · 限价 ${limit}` : ""}</div>
          <div className="ck-toolbar">
            <button className="ck-btn confirm" disabled={busy} onClick={confirm}>{busy ? "提交中…" : "确认下单（服务端撮合）"}</button>
            <button className="ck-btn ghost" disabled={busy} onClick={() => setConfirming(false)}>取消</button>
          </div>
        </div>
      )}
      {result ? (
        <div className={`ck-msg ${result.ok ? "ok" : "err"}`} style={{ marginTop: 8 }}>
          服务端返回码：HTTP {result.status} · code: {result.code ?? "—"} · ok: {String(result.ok)}{result.error ? ` · ${result.error}` : ""}
        </div>
      ) : null}
      <div className="ck-na" style={{ marginTop: 8 }}>仅经服务端权威引擎撮合，客户端不定价、不记账。快捷键永不触发下单。</div>
    </div>
  );
}

export function MarketRightPanel({ symbol, depth, depthStatus, trades, tradesStatus, onPlaced }: {
  symbol: string; depth: unknown; depthStatus: SourceStatus;
  trades: unknown; tradesStatus: SourceStatus; onPlaced: () => void;
}) {
  const dq = depth as { bids?: [string, string][]; asks?: [string, string][] } | undefined;
  const asks = useMemo(() => (dq?.asks || []).slice(0, 10).reverse(), [dq]);
  const bids = useMemo(() => (dq?.bids || []).slice(0, 10), [dq]);
  const maxQ = Math.max(0, ...[...asks, ...bids].map((r) => Number(r[1]) || 0));
  const depthNA = statusNA(depthStatus);
  const tradesNA = statusNA(tradesStatus);
  const tradeRows = Array.isArray(trades) ? (trades as Record<string, unknown>[]).slice(0, 12) : [];
  const dBadge = statusBadge(depthStatus);
  const tBadge = statusBadge(tradesStatus);

  return (
    <section className="cockpit-panel" style={{ height: "100%" }}>
      <div className="ck-head">
        <span className="ck-title">盘口 · 逐笔 · 下单</span>
        <span className="ck-sub">{symbol}</span>
        <span className="ck-spacer" />
        <span className="ck-badge real">REAL MARKET</span>
      </div>
      <div className="ck-body" style={{ overflow: "auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
          <span className="ck-sub">买卖深度</span><span className={dBadge.cls}>{dBadge.text}</span>
        </div>
        {depthNA ? <div className="ck-na">{depthNA} · 行情不可用</div> : (
          <div className="ck-book">
            <div className="head row" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr" }}>
              <span style={{ textAlign: "left" }}>价格</span><span style={{ textAlign: "right" }}>数量</span><span style={{ textAlign: "right" }}>累计</span>
            </div>
            <div className="asks">
              {asks.map((r, i) => {
                const tot = asks.slice(0, i + 1).reduce((a, b) => a + (Number(b[1]) || 0), 0);
                return (
                  <div className="row" key={"a" + i}>
                    <span className="bar" style={{ background: "var(--ck-red)", width: `${maxQ ? (Number(r[1]) || 0) / maxQ * 100 : 0}%` }} />
                    <span className="px">{fmtPrice(r[0])}</span><span>{fmtQty(r[1])}</span><span className="tot">{fmtQty(tot)}</span>
                  </div>
                );
              })}
            </div>
            <div className="mid">— 买卖中间价 —</div>
            <div className="bids">
              {bids.map((r, i) => {
                const tot = bids.slice(0, i + 1).reduce((a, b) => a + (Number(b[1]) || 0), 0);
                return (
                  <div className="row" key={"b" + i}>
                    <span className="bar" style={{ background: "var(--ck-green)", width: `${maxQ ? (Number(r[1]) || 0) / maxQ * 100 : 0}%` }} />
                    <span className="px">{fmtPrice(r[0])}</span><span>{fmtQty(r[1])}</span><span className="tot">{fmtQty(tot)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", margin: "12px 0 4px" }}>
          <span className="ck-sub">逐笔成交</span><span className={tBadge.cls}>{tBadge.text}</span>
        </div>
        {tradesNA ? <div className="ck-na">{tradesNA}</div> : (
          <table className="ck-table">
            <thead><tr><th>价格</th><th>数量</th><th>时间</th></tr></thead>
            <tbody>{tradeRows.map((t, i) => (
              <tr key={i}><td className="ck-up">{fmtPrice(t.price ?? t.p)}</td><td>{fmtQty(t.qty ?? t.q)}</td>
                <td>{t.time ? new Date(Number(t.time)).toLocaleTimeString("en-US") : "—"}</td></tr>
            ))}</tbody>
          </table>
        )}

        <OrderTicket symbol={symbol} onPlaced={onPlaced} />
      </div>
    </section>
  );
}
