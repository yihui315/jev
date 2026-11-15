// V2 Trading Cockpit — the 7th page. Assembles the six regions around the
// unified metrics layer. ALL data reads go through useJevQuery over /api/jev/*;
// every source is labelled by meta.label (never deriveStatusLabel). Unreachable
// sources degrade to honest "N/A（接口不可用）"; no cached/fake data is shown.
import { useEffect, useState, type ReactNode } from "react";
import { useJevQuery } from "../api/useJev";
import { jevFetch } from "../api/client";
import { NAV } from "../components/Shell";
import { fmtPrice } from "../components/common";
import { MetricStrip, ProviderComparison, CostAttribution } from "../components/cockpit";
import {
  deriveCockpitSnapshot, deriveProviderRows, type CockpitRaw,
} from "../metrics/contracts";
import {
  KlinePanel, MarketRightPanel, DomainBadge, statusBadge, type SourceStatus,
} from "../components/cockpit/MarketPanel";
import { ExecutionLedger } from "../components/cockpit/ExecutionLedger";
import { useHotkeys, HOTKEY_HELP } from "../hooks/useHotkeys";
import { useResizablePanels } from "../hooks/useResizablePanels";

type Sym = "BTCUSDT" | "ETHUSDT";
const SYMBOLS: Sym[] = ["BTCUSDT", "ETHUSDT"];
const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"];
const POLL = 6000; // 4–8s polling band required by the spec

// ---- honest status adapter (meta.label ONLY) --------------------------------
type QLike = { meta?: { label?: string }; unreachable: boolean; error?: string; loading: boolean; data?: unknown };
const hasArray = (d: unknown): boolean => Array.isArray(d) && d.length > 0;
const hasObject = (d: unknown): boolean => !!d && typeof d === "object";
const hasBook = (d: unknown): boolean => {
  if (!d || typeof d !== "object") return false;
  const x = d as { bids?: unknown; asks?: unknown };
  return (Array.isArray(x.bids) && x.bids.length > 0) || (Array.isArray(x.asks) && x.asks.length > 0);
};
function toStatus(q: QLike, has: (d: unknown) => boolean): SourceStatus {
  return { label: q.meta?.label, unreachable: q.unreachable, error: q.error, loading: q.loading, hasData: has(q.data) };
}

export function TradingCockpit({ topbar }: { topbar: ReactNode }) {
  const [symbol, setSymbol] = useState<Sym>("BTCUSDT");
  const [interval, setInterval] = useState("1h");

  // HttpOnly+Secure paper session cookie — same pattern as the six pages.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const r = await jevFetch("paper/session", { method: "POST", body: {} });
      if (!cancelled && !r.ok) console.warn("cockpit paper session ensure failed", r.error);
    })();
    return () => { cancelled = true; };
  }, []);

  // ---- Market reads (path changes re-subscribe automatically) ---------------
  const klinesQ = useJevQuery<unknown[]>(`market/klines?symbol=${symbol}&interval=${interval}&limit=200`, { intervalMs: POLL });
  const tickerQ = useJevQuery<Record<string, unknown>[]>(`market/ticker?symbols=${encodeURIComponent(JSON.stringify(SYMBOLS))}`, { intervalMs: POLL });
  const depthQ = useJevQuery<{ bids: [string, string][]; asks: [string, string][] }>(`market/depth?symbol=${symbol}&limit=20`, { intervalMs: 5000 });
  const tradesQ = useJevQuery<Record<string, unknown>[]>(`market/trades?symbol=${symbol}&limit=30`, { intervalMs: 5000 });

  // ---- Paper ledger reads ---------------------------------------------------
  const accountQ = useJevQuery<unknown>("paper/account", { intervalMs: 5000 });
  const ordersQ = useJevQuery<unknown>("paper/orders", { intervalMs: 5000 });
  const eventsQ = useJevQuery<unknown>("paper/events?after=0&limit=20", { intervalMs: POLL });
  const equityQ = useJevQuery<unknown>("paper/equity", { intervalMs: 8000 });

  // ---- System / strategy / provider reads (metrics + cost + risk) ----------
  const strategiesQ = useJevQuery<unknown>("strategies/status", { intervalMs: 8000 });
  const providersQ = useJevQuery<unknown>("providers/status", { intervalMs: 8000 });
  const systemQ = useJevQuery<unknown>("system/status", { intervalMs: 8000 });
  const healthQ = useJevQuery<unknown>("health", { intervalMs: 8000 });

  // ---- Assemble CockpitRaw exactly per the frozen contract -----------------
  // CockpitRaw = { account, orders, events, equity, strategies, providers,
  // system } (all optional unknown). Market payloads are NOT part of it and are
  // passed straight to the display components. Missing data stays undefined so
  // the metrics layer returns honest N/A (never a synthesized value).
  const raw: CockpitRaw = {
    account: accountQ.data,
    orders: ordersQ.data,
    events: eventsQ.data,
    equity: equityQ.data,
    strategies: strategiesQ.data,
    providers: providersQ.data,
    system: systemQ.data,
  };
  const snapshot = deriveCockpitSnapshot(raw);
  const rows = deriveProviderRows(raw);

  const klineStatus = toStatus(klinesQ, hasArray);
  const depthStatus = toStatus(depthQ, hasBook);
  const tradesStatus = toStatus(tradesQ, hasArray);
  const accountStatus = toStatus(accountQ, hasObject);
  const ordersStatus = toStatus(ordersQ, hasObject);
  const systemStatus = toStatus(systemQ, hasObject);
  const providersStatus = toStatus(providersQ, hasObject);
  const healthStatus = toStatus(healthQ, hasObject);
  const sysBadge = statusBadge(systemStatus);
  const marketBadge = statusBadge(klineStatus);
  const paperBadge = statusBadge(accountStatus);
  const providerBadge = statusBadge(providersStatus);
  const healthBadge = statusBadge(healthStatus);

  const refreshLedger = () => {
    accountQ.refetch(); ordersQ.refetch(); eventsQ.refetch(); equityQ.refetch();
  };

  // ---- Hotkeys (navigation / symbol / interval / help only) -----------------
  const gotoByNum = (num: number) => {
    const target = NAV.find((n) => n.num === num);
    if (target) { try { window.location.hash = "#/" + target.id; } catch { /* non-browser */ } }
  };
  const { helpOpen, setHelpOpen } = useHotkeys({
    onNavigate: gotoByNum,
    onSymbol: (s) => setSymbol(s === "BTC" ? "BTCUSDT" : "ETHUSDT"),
    onIntervalCycle: (dir) => setInterval((prev) => {
      const i = INTERVALS.indexOf(prev);
      return INTERVALS[(i + dir + INTERVALS.length) % INTERVALS.length];
    }),
    onIntervalSet: (iv) => { if (INTERVALS.includes(iv)) setInterval(iv); },
  });

  const { hSplit, vSplit, hContainerRef, vContainerRef, onHDragStart, onVDragStart, reset } = useResizablePanels();

  const anyUnreachable = [klinesQ, depthQ, tradesQ, accountQ, ordersQ, eventsQ, equityQ, strategiesQ, providersQ, systemQ, healthQ]
    .some((q) => q.unreachable);

  // ---- Risk alerts (from real system/health fields + metrics warnings) ------
  const riskItems: { level: "crit" | "warn" | "info"; text: string }[] = [];
  for (const w of snapshot.warnings) riskItems.push({ level: "warn", text: w });
  const sys = (systemQ.data ?? {}) as { limits?: Record<string, unknown> };
  const lim = sys.limits ?? {};
  if (lim.websocket && String(lim.websocket).toUpperCase() === "BLOCKED")
    riskItems.push({ level: "crit", text: `行情通道 WebSocket ${String(lim.websocket)}：当前为 REST_POLLING，绝非 WS LIVE` });
  if (lim.ai_six_roles && String(lim.ai_six_roles).toUpperCase() === "BLOCKED")
    riskItems.push({ level: "warn", text: `AI 六角色推理：${String(lim.ai_six_roles)}（无真实推理记录）` });
  if (lim.strategy_oos && String(lim.strategy_oos).includes("NO_DATA"))
    riskItems.push({ level: "warn", text: "策略 OOS：NO_DATA，无真实样本不生成排名" });
  const h = (healthQ.data ?? {}) as { real_money?: boolean; real_exchange_orders?: boolean };
  const safetyState = healthQ.unreachable || healthQ.error || !healthQ.data
    ? { text: "UNKNOWN", cls: "unavailable", note: "health 未核验" }
    : h.real_money === false && h.real_exchange_orders === false
      ? { text: "LOCKED", cls: "paper", note: "真实资金 / 交易所下单均关闭" }
      : h.real_money === true || h.real_exchange_orders === true
        ? { text: "UNSAFE", cls: "blocked", note: "检测到真实交易权限开启" }
        : { text: "UNKNOWN", cls: "unavailable", note: "安全字段不完整" };

  if (healthQ.unreachable || healthQ.error || !healthQ.data) {
    riskItems.push({ level: "crit", text: "health N/A（接口不可用）：真实资金与交易权限无法核验，按不安全处理" });
  } else if (h.real_money === true || h.real_exchange_orders === true) {
    riskItems.push({ level: "crit", text: "合规红线：检测到真实资金或真实交易所下单权限开启；停止所有交易动作并关闭权限" });
  } else if (h.real_money === false && h.real_exchange_orders === false) {
    riskItems.push({ level: "info", text: "合规红线：real_money=false · real_exchange_orders=false（禁止真实资金/真实下单）" });
  } else {
    riskItems.push({ level: "crit", text: "合规红线：health 未同时明确返回 real_money=false 与 real_exchange_orders=false；安全 Gate 未通过" });
  }
  if (systemQ.unreachable) riskItems.push({ level: "crit", text: "system/status N/A（接口不可用）：风险红线无法核验" });

  return (
    <>
      {topbar}
      <div className="jev-content cockpit-content">
        <div className="cockpit">
          {/* Toolbar: symbol / interval / ticker / domain / help / layout */}
          <div className="ck-toolbar">
            <span className="ck-sub" style={{ fontWeight: 700, color: "var(--ck-text)" }}>TRADING COCKPIT</span>
            <div className="ck-seg">
              {SYMBOLS.map((s) => <button key={s} className={symbol === s ? "on" : ""} onClick={() => setSymbol(s)}>{s.replace("USDT", "")}</button>)}
            </div>
            <div className="ck-seg">
              {INTERVALS.map((iv) => <button key={iv} className={interval === iv ? "on" : ""} onClick={() => setInterval(iv)}>{iv}</button>)}
            </div>
            <span style={{ flex: 1 }} />
            {tickerQ.data?.map((t) => {
              const chg = Number(t.priceChangePercent);
              return (
                <span key={String(t.symbol)} className="ck-badge nodata">
                  {String(t.symbol ?? "").replace("USDT", "")} {fmtPrice(t.lastPrice)}
                  <span className={chg >= 0 ? "ck-up" : "ck-down"}> {Number.isFinite(chg) ? chg.toFixed(2) : "—"}%</span>
                </span>
              );
            })}
            <span className="ck-spacer" style={{ flex: "0 0 auto" }} />
            <DomainBadge domain="REAL_MARKET" />
            <DomainBadge domain="PAPER" />
            <DomainBadge domain="SYSTEM" />
            {anyUnreachable ? <span className="ck-badge unavailable">部分数据源 N/A（接口不可用）</span> : null}
            <button className="ck-btn ghost" onClick={() => setHelpOpen(true)} title="快捷键帮助">? 快捷键</button>
            <button className="ck-btn ghost" onClick={reset} title="重置面板布局">重置布局</button>
          </div>

          <section className="ck-terminal-ribbon" aria-label="交易终端运行状态">
            <div className="ck-terminal-brand">
              <span className="ck-eyebrow">JEV / QUANT OPERATIONS</span>
              <strong>专业交易终端</strong>
              <span className="ck-terminal-caption">PAPER-FIRST · SERVER-AUTHORITATIVE</span>
              <small>服务端数据时间：{snapshot.generatedAtUtc ?? "N/A"}</small>
            </div>
            <div className="ck-status-grid">
              <div className="ck-status-cell">
                <span className="ck-status-label">MARKET DATA</span>
                <span className={marketBadge.cls}>{marketBadge.text}</span>
                <small>REST_POLLING · 非 WebSocket LIVE</small>
              </div>
              <div className="ck-status-cell">
                <span className="ck-status-label">PAPER ACCOUNT</span>
                <span className={paperBadge.cls}>{paperBadge.text}</span>
                <small>服务端权威模拟账本</small>
              </div>
              <div className="ck-status-cell">
                <span className="ck-status-label">AI PROVIDERS</span>
                <span className={providerBadge.cls}>{providerBadge.text}</span>
                <small>接口状态不代表策略胜率</small>
              </div>
              <div className="ck-status-cell">
                <span className="ck-status-label">SYSTEM HEALTH</span>
                <span className={healthBadge.cls}>{healthBadge.text}</span>
                <small>{healthQ.error ?? (healthQ.loading ? "正在检查" : "服务端健康状态")}</small>
              </div>
              <div className="ck-status-cell">
                <span className="ck-status-label">REAL-ORDER GATE</span>
                <span className={"ck-badge " + safetyState.cls}>{safetyState.text}</span>
                <small>{safetyState.note}</small>
              </div>
            </div>
          </section>

          <div style={{ fontSize: 10.5, color: "var(--ck-muted)", lineHeight: 1.5 }}>
            真实公开 REST 行情（Binance.US spot，通道 <b>REST_POLLING</b>，非 WebSocket LIVE）· PAPER 模拟由<b>服务端权威引擎</b>撮合 ·
            客户端不定价 / 不记账 · 真实与 PAPER 指标域由统一指标层分离并标注 · 所有数据源按 meta.label 判定，接口不可用一律 N/A，绝不缓存假数据 ·
            按 <b>?</b> 查看快捷键
          </div>

          {/* Region 1 — unified KPI strip */}
          <MetricStrip snapshot={snapshot} />

          {/* Regions 2/3/4/5 — resizable workspace */}
          <div className="cockpit-workspace">
            <div className="cockpit-vsplit" ref={vContainerRef}>
              {/* upper: Region 2 (chart) | Region 3 (book + ticket) */}
              <div className="cockpit-vpane upper" style={{ minHeight: vSplit }}>
                <div className="cockpit-hsplit" ref={hContainerRef}>
                  <div className="cockpit-hpane left" style={{ flex: `0 0 ${hSplit}%` }}>
                    <KlinePanel symbol={symbol} interval={interval} klines={klinesQ.data} status={klineStatus} />
                  </div>
                  <div className="ck-splitter" onPointerDown={onHDragStart} role="separator" aria-orientation="vertical" />
                  <div className="cockpit-hpane right">
                    <MarketRightPanel
                      symbol={symbol} depth={depthQ.data} depthStatus={depthStatus}
                      trades={tradesQ.data} tradesStatus={tradesStatus} onPlaced={refreshLedger}
                    />
                  </div>
                </div>
              </div>
              <div className="ck-splitter v" onPointerDown={onVDragStart} role="separator" aria-orientation="horizontal" />
              {/* lower: Region 4 (ledger) */}
              <div className="cockpit-vpane lower">
                <ExecutionLedger
                  account={accountQ.data} accountStatus={accountStatus}
                  orders={ordersQ.data} ordersStatus={ordersStatus}
                  events={eventsQ.data} onChanged={refreshLedger}
                />
              </div>
            </div>
            {/* Region 5 — right analysis rail */}
            <div className="cockpit-rail">
              <ProviderComparison rows={rows} />
            </div>
          </div>

          {/* Region 6 — cost attribution + risk alerts */}
          <div className="cockpit-bottom">
            <CostAttribution snapshot={snapshot} system={systemQ.data} />
            <section className="cockpit-panel">
              <div className="ck-head">
                <span className="ck-title">系统风险告警</span>
                <span className="ck-spacer" />
                <DomainBadge domain="SYSTEM" />
                <span className={sysBadge.cls}>{sysBadge.text}</span>
              </div>
              <div className="ck-body">
                <div className="ck-risk-list">
                  {riskItems.length
                    ? riskItems.map((r, i) => <div key={i} className={`ck-risk ${r.level}`}><span className="dot" />{r.text}</div>)
                    : <div className="ck-na">N/A（接口不可用 / 无告警数据）</div>}
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>

      {/* Shortcut help overlay */}
      {helpOpen ? (
        <div className="ck-help-backdrop" onClick={() => setHelpOpen(false)}>
          <div className="ck-help" onClick={(e) => e.stopPropagation()}>
            <h2>交易驾驶舱 · 快捷键</h2>
            <table><tbody>
              {HOTKEY_HELP.map((row, i) => <tr key={i}><td className="k">{row.keys}</td><td>{row.desc}</td></tr>)}
            </tbody></table>
            <div className="note">快捷键仅用于切换页面 / 标的 / 周期 / 帮助，绝不触发下单或撤单。下单与撤单必须经人工确认按钮。</div>
            <button className="ck-btn ghost" style={{ marginTop: 12 }} onClick={() => setHelpOpen(false)}>关闭 (Esc)</button>
          </div>
        </div>
      ) : null}
    </>
  );
}
