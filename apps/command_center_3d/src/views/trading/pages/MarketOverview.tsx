/**
 * trading/pages/MarketOverview — 行情总览
 *
 * Quotes for BTC/ETH/ORDI, the account asset summary, the strategy status, and a
 * "real-time quote" interface that calls getMarketSnapshot. All displayed
 * balance/risk numbers come from the server (mock today) — never the browser.
 */
import { useCallback, useEffect, useState } from "react";
import { RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { PageContainer } from "../components/PageContainer";
import { StatCard } from "../components/StatCard";
import { usePaperApi } from "../hooks/usePaperApi";
import { formatPct, formatUsd, toneClass } from "../lib/finance";
import type { Interval, MarketSnapshot, Symbol } from "../services/types";
import { cn } from "../../../ui/cn";

const SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT"];
const INTERVALS: Interval[] = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function MarketOverview({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [interval, setInterval] = useState<Interval>("5m");
  const [quotes, setQuotes] = useState<MarketSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const snaps = await api.getMarketSnapshot({ symbols: SYMBOLS, interval });
      setQuotes(snaps);
      setLastUpdated(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [api, interval]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <PageContainer
      title="行情总览"
      description="BTC / ETH / ORDI 实时行情、账户资产概览与策略状态。所有数值均来自服务端（当前为 Mock）。"
      actions={
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 t-meta text-foreground disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> 刷新
        </button>
      }
    >
      {/* Real-time quote interface */}
      <div className="mb-6 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3">
        <span className="t-meta text-muted-foreground">实时行情周期：</span>
        {INTERVALS.map((iv) => (
          <button
            key={iv}
            type="button"
            onClick={() => setInterval(iv)}
            className={cn(
              "rounded px-2 py-1 t-meta",
              interval === iv ? "bg-foreground text-background" : "border border-border text-foreground",
            )}
          >
            {iv}
          </button>
        ))}
        {lastUpdated ? (
          <span className="ml-auto t-meta text-muted-foreground">
            更新于 {new Date(lastUpdated).toLocaleTimeString("zh-CN")}
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="mb-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      ) : null}

      {/* Quotes */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {quotes.map((q) => (
          <div key={q.symbol} className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="t-section text-foreground">{q.symbol}</span>
              <span className={cn("flex items-center gap-1 t-meta", toneClass(q.changePct))}>
                {q.changePct >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                {formatPct(q.changePct / 100)}
              </span>
            </div>
            <div className={cn("mt-2 text-2xl font-semibold tabular-nums", toneClass(q.changePct))}>
              {formatUsd(q.last)}
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1 t-meta text-muted-foreground">
              <span>高 {formatUsd(q.high, { decimals: q.last < 100 ? 3 : 2 })}</span>
              <span>低 {formatUsd(q.low, { decimals: q.last < 100 ? 3 : 2 })}</span>
              <span>开 {formatUsd(q.open, { decimals: q.last < 100 ? 3 : 2 })}</span>
              <span>量 {q.volume.toLocaleString("en-US")}</span>
            </div>
          </div>
        ))}
        {quotes.length === 0 && !loading ? (
          <div className="col-span-3 rounded-lg border border-border bg-card p-6 text-muted-foreground">
            暂无行情数据
          </div>
        ) : null}
      </div>

      {/* Account assets summary (server-derived) */}
      <AccountSummary api={api} />
    </PageContainer>
  );
}

/** Account asset summary lives on its own small loader so the quote grid and the
 *  account card refresh independently — both still read server data only. */
function AccountSummary({ api }: { api: ReturnType<typeof usePaperApi> }) {
  const [acc, setAcc] = useState<Awaited<ReturnType<typeof api.getPaperAccount>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void api.getPaperAccount().then(
      (a) => alive && setAcc(a),
      (e) => alive && setErr(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      alive = false;
    };
  }, [api]);
  if (err) {
    return (
      <div className="mt-6 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
        {err}
      </div>
    );
  }
  if (!acc) return <div className="mt-6 h-24 animate-pulse rounded-lg border border-border bg-card" />;
  return (
    <div className="mt-6">
      <h2 className="t-section text-foreground">账户资产</h2>
      <div className="mt-3 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="权益" value={formatUsd(acc.equity)} tone="neutral" />
        <StatCard label="现金" value={formatUsd(acc.cash)} tone="neutral" />
        <StatCard label="占用保证金" value={formatUsd(acc.marginUsed)} tone="neutral" />
        <StatCard label="当日盈亏" value={formatUsd(acc.pnlDay, { sign: true })} tone="auto" />
        <StatCard label="累计盈亏" value={formatUsd(acc.pnlTotal, { sign: true })} tone="auto" />
        <StatCard label="最大回撤" value={formatPct(acc.maxDrawdown)} tone="neutral" />
        <StatCard label="持仓数" value={acc.positions.length} tone="neutral" />
        <StatCard
          label="风险状态"
          value={acc.riskStatus === "ok" ? "正常" : acc.riskStatus === "warning" ? "预警" : "超限"}
          tone={acc.riskStatus === "ok" ? "up" : acc.riskStatus === "warning" ? "down" : "down"}
        />
      </div>
    </div>
  );
}
