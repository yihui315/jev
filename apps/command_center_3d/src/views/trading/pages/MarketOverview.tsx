/**
 * trading/pages/MarketOverview — 行情总览 (01 AI 交易指挥中心 的子集视图)
 *
 * Quotes for BTC/ETH/ORDI/1000SATS from the authoritative Binance public
 * ticker, a change % derived from real candles for the selected interval, and a
 * server-authoritative account summary. All balance/risk numbers come from
 * `getAccount()` (mock today) — never the browser.
 */
import { useCallback, useEffect, useState } from "react";
import { RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { PageContainer } from "../components/PageContainer";
import { StatCard } from "../components/StatCard";
import { usePaperApi } from "../hooks/usePaperApi";
import { formatPct, formatUsd, riskStatusOf, riskTone, toneClass } from "../lib/finance";
import type { AccountResponse, Candle, Interval, Symbol } from "../services/types";
import { cn } from "../../../ui/cn";

const SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const INTERVALS: Interval[] = ["1m", "5m", "15m", "1h", "4h", "1d"];

interface Quote {
  symbol: Symbol;
  last: number;
  changePct: number;
}

export function MarketOverview({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [interval, setInterval] = useState<Interval>("5m");
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [tickers, candleSets] = await Promise.all([
        api.getTicker(SYMBOLS),
        Promise.all(SYMBOLS.map((s) => api.getCandles(s, interval).catch(() => [] as Candle[]))),
      ]);
      const priceMap = new Map(tickers.map((t) => [t.symbol, t.price]));
      const next: Quote[] = SYMBOLS.map((symbol, i) => {
        const candles = candleSets[i];
        const last = priceMap.get(symbol) ?? candles.at(-1)?.close ?? 0;
        const first = candles[0]?.open ?? last;
        const changePct = first > 0 ? (last - first) / first : 0;
        return { symbol, last, changePct };
      });
      setQuotes(next);
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
      title="01 · AI 交易指挥中心"
      description="BTC / ETH / ORDI / 1000SATS 实时行情（来源：Binance 公开行情）、账户资产概览。所有数值均来自服务端（当前为 Mock）。"
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
      <div className="mb-6 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3">
        <span className="t-meta text-muted-foreground">行情周期：</span>
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

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {quotes.map((q) => (
          <div key={q.symbol} className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="t-section text-foreground">{q.symbol}</span>
              <span className={cn("flex items-center gap-1 t-meta", toneClass(q.changePct))}>
                {q.changePct >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                {formatPct(q.changePct)}
              </span>
            </div>
            <div className={cn("mt-2 text-2xl font-semibold tabular-nums", toneClass(q.changePct))}>
              {formatUsd(q.last, { decimals: q.last < 1 ? 5 : 2 })}
            </div>
          </div>
        ))}
        {quotes.length === 0 && !loading ? (
          <div className="col-span-full rounded-lg border border-border bg-card p-6 text-muted-foreground">
            暂无行情数据
          </div>
        ) : null}
      </div>

      <AccountSummary api={api} />
    </PageContainer>
  );
}

function AccountSummary({ api }: { api: ReturnType<typeof usePaperApi> }) {
  const [acc, setAcc] = useState<AccountResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void api.getAccount().then(
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
  const s = acc.summary;
  const risk = riskStatusOf(s);
  return (
    <div className="mt-6">
      <h2 className="t-section text-foreground">账户资产（服务端权威）</h2>
      <div className="mt-3 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="权益" value={formatUsd(s.equity)} tone="neutral" />
        <StatCard label="现金" value={formatUsd(s.cash)} tone="neutral" />
        <StatCard label="可用现金" value={formatUsd(s.availableCash)} tone="neutral" />
        <StatCard label="持仓市值" value={formatUsd(s.marketValue)} tone="neutral" />
        <StatCard label="未实现盈亏" value={formatUsd(s.unrealizedPnl, { sign: true })} tone="auto" />
        <StatCard label="当日盈亏" value={formatUsd(s.dailyPnl, { sign: true })} tone="auto" />
        <StatCard label="累计盈亏" value={formatUsd(s.totalPnl, { sign: true })} tone="auto" />
        <StatCard label="最大回撤" value={formatPct(s.drawdown)} tone="neutral" />
        <StatCard label="敞口/权益" value={formatPct(s.exposurePct)} tone="neutral" />
        <StatCard
          label="风险状态"
          value={risk === "ok" ? "正常" : risk === "warning" ? "预警" : "超限"}
          className={cn(riskTone(risk))}
        />
      </div>
    </div>
  );
}
