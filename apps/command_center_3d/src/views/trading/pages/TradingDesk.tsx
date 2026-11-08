/**
 * trading/pages/TradingDesk — 交易台 (02 专业交易工作台 的子集)
 *
 * K-line (lightweight-charts with SVG fallback), an order book + depth, and an
 * order entry. The entry builds an OrderProposal and REQUIRES a human
 * confirmation before calling createPaperOrder — TAKER proposals MUST NOT
 * bypass this. No real submit happens in mock mode; the filled order returns for
 * display. All market data is from the authoritative Binance public source.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ShieldCheck, TriangleAlert } from "lucide-react";
import { PageContainer } from "../components/PageContainer";
import { CandlestickChart } from "../components/CandlestickChart";
import { DepthBar } from "../components/DepthBar";
import { usePaperApi } from "../hooks/usePaperApi";
import { formatUsd, validateOrderProposal } from "../lib/finance";
import type {
  Candle,
  Interval,
  OrderBook,
  OrderProposal,
  PaperOrder,
  Symbol,
} from "../services/types";
import { cn } from "../../../ui/cn";

const SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"];
const INTERVALS: Interval[] = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function TradingDesk({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [symbol, setSymbol] = useState<Symbol>("BTCUSDT");
  const [interval, setInterval] = useState<Interval>("5m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // --- order entry form (the PROPOSAL, not yet an order) ---
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"MARKET" | "LIMIT">("LIMIT");
  const [quantity, setQuantity] = useState<string>("0.001");
  const [limitPrice, setLimitPrice] = useState<string>("67000");
  const [confirmed, setConfirmed] = useState(false);
  const [submitState, setSubmitState] = useState<{ ok: boolean; msg: string } | null>(null);
  const [lastOrder, setLastOrder] = useState<PaperOrder | null>(null);
  const [clientOrderId] = useState(() => "cli_" + Math.random().toString(36).slice(2, 12));

  const proposal: OrderProposal = useMemo(
    () => ({
      symbol,
      side,
      type,
      quantity: Number(quantity),
      limitPrice: type === "LIMIT" ? Number(limitPrice) : null,
      confirmationId: confirmed ? `cfm_op_${Date.now().toString(36)}` : "",
      clientOrderId,
    }),
    [symbol, side, type, quantity, limitPrice, confirmed, clientOrderId],
  );

  const validation = validateOrderProposal(proposal);

  const refreshMarket = useCallback(async () => {
    setError(null);
    try {
      const [cs, ob, tk] = await Promise.all([
        api.getCandles(symbol, interval),
        api.getOrderBook(symbol),
        api.getTicker([symbol]).catch(() => []),
      ]);
      setCandles(cs);
      setBook(ob);
      const px = tk.find((t) => t.symbol === symbol)?.price ?? cs.at(-1)?.close ?? null;
      setLastPrice(px);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [api, symbol, interval]);

  useEffect(() => {
    void refreshMarket();
  }, [refreshMarket]);

  async function submit() {
    setSubmitState(null);
    if (!validation.ok) {
      setSubmitState({ ok: false, msg: validation.error ?? "订单不合法" });
      return;
    }
    try {
      const res = await api.createOrder(proposal);
      setLastOrder(res.order);
      setSubmitState({
        ok: res.accepted,
        msg: res.accepted
          ? `已提交（${res.order.status}）${res.order.id}`
          : `被拒绝：${res.rejection?.reason ?? "未知原因"}`,
      });
      setConfirmed(false);
    } catch (e) {
      setSubmitState({ ok: false, msg: e instanceof Error ? e.message : String(e) });
    }
  }

  const maxTotal = book
    ? Math.max(book.bids.at(-1)?.total ?? 1, book.asks.at(-1)?.total ?? 1)
    : 1;

  return (
    <PageContainer
      title="02 · 专业交易工作站"
      description="K 线、订单簿与下单。下单需人工确认（confirmationId），模拟成交仅用于展示，不涉及真实资金。"
    >
      {error ? (
        <div className="mb-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2 rounded-lg border border-border bg-card p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSymbol(s)}
                className={cn(
                  "rounded px-2 py-1 t-meta",
                  symbol === s ? "bg-foreground text-background" : "border border-border text-foreground",
                )}
              >
                {s}
              </button>
            ))}
            <span className="mx-2 h-4 w-px bg-border" />
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
            {lastPrice ? (
              <span className="ml-auto t-meta text-muted-foreground">
                最新 {formatUsd(lastPrice, { decimals: lastPrice < 1 ? 5 : 2 })}
              </span>
            ) : null}
          </div>
          {candles.length ? <CandlestickChart candles={candles} /> : (
            <div className="h-80 animate-pulse rounded-lg border border-border bg-card" />
          )}
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center justify-between">
            <h2 className="t-section text-foreground">订单簿</h2>
            <span className="t-meta text-muted-foreground">{book?.source ?? ""}</span>
          </div>
          {book ? (
            <div className="mt-2">
              <div className="grid grid-cols-3 px-2 t-meta text-muted-foreground">
                <span>价格</span>
                <span className="text-right">数量</span>
                <span className="text-right">累计</span>
              </div>
              {book.asks
                .slice()
                .reverse()
                .map((l, i) => (
                  <DepthBar key={`a${i}`} level={l} side="ask" maxTotal={maxTotal} />
                ))}
              <div className="my-1 border-y border-border px-2 py-1 text-center t-meta text-foreground">
                {formatUsd(book.bids[0]?.price ?? 0, { decimals: lastPrice && lastPrice < 1 ? 5 : 2 })} /{" "}
                {formatUsd(book.asks[0]?.price ?? 0, { decimals: lastPrice && lastPrice < 1 ? 5 : 2 })}
              </div>
              {book.bids.map((l, i) => (
                <DepthBar key={`b${i}`} level={l} side="bid" maxTotal={maxTotal} />
              ))}
            </div>
          ) : (
            <div className="mt-2 h-64 animate-pulse rounded border border-border" />
          )}
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-border bg-card p-4">
        <h2 className="t-section text-foreground">下单（模拟）</h2>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Field label="方向">
            <div className="flex gap-1">
              {(["BUY", "SELL"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSide(s)}
                  className={cn(
                    "flex-1 rounded px-2 py-1.5 t-meta",
                    side === s
                      ? s === "BUY"
                        ? "bg-status-ok text-background"
                        : "bg-status-danger text-background"
                      : "border border-border text-foreground",
                  )}
                >
                  {s === "BUY" ? "买入" : "卖出"}
                </button>
              ))}
            </div>
          </Field>
          <Field label="类型">
            <select
              value={type}
              onChange={(e) => setType(e.target.value as "MARKET" | "LIMIT")}
              className="w-full rounded border border-border bg-background px-2 py-1.5 t-meta text-foreground"
            >
              <option value="LIMIT">限价</option>
              <option value="MARKET">市价</option>
            </select>
          </Field>
          <Field label="数量">
            <input
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              inputMode="decimal"
              className="w-full rounded border border-border bg-background px-2 py-1.5 t-meta text-foreground tabular-nums"
            />
          </Field>
          <Field label="限价">
            <input
              value={limitPrice}
              disabled={type === "MARKET"}
              onChange={(e) => setLimitPrice(e.target.value)}
              inputMode="decimal"
              className="w-full rounded border border-border bg-background px-2 py-1.5 t-meta text-foreground tabular-nums disabled:opacity-50"
            />
          </Field>
          <Field label="预估名义">
            <div className="rounded border border-border bg-background px-2 py-1.5 t-meta text-muted-foreground tabular-nums">
              {formatUsd(
                (Number(quantity) || 0) * (type === "LIMIT" ? Number(limitPrice) || 0 : lastPrice || 0),
                { decimals: 2 },
              )}
            </div>
          </Field>
        </div>

        <label className="mt-3 flex items-start gap-2 rounded border border-border bg-background px-3 py-2">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="mt-1"
          />
          <span className="t-meta text-foreground">
            我确认该订单（人工确认，confirmationId 由本操作生成）。
            <span className="text-muted-foreground"> TAKER 提案不得绕过此确认。</span>
          </span>
        </label>

        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!validation.ok}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-4 py-2 t-meta font-medium",
              validation.ok ? "bg-foreground text-background" : "cursor-not-allowed border border-border text-muted-foreground",
            )}
          >
            <ShieldCheck className="h-4 w-4" /> 提交模拟订单
          </button>
          {!validation.ok ? (
            <span className="flex items-center gap-1 t-meta text-status-warn">
              <TriangleAlert className="h-3.5 w-3.5" /> {validation.error}
            </span>
          ) : null}
        </div>

        {submitState ? (
          <div
            className={cn(
              "mt-3 rounded border p-3 t-meta",
              submitState.ok
                ? "border-status-ok/40 bg-status-ok/10 text-status-ok"
                : "border-status-danger/40 bg-status-danger/10 text-status-danger",
            )}
          >
            {submitState.msg}
          </div>
        ) : null}

        {lastOrder ? (
          <div className="mt-2 t-meta text-muted-foreground">
            最近订单：{lastOrder.id} · {lastOrder.side} {lastOrder.qty} {lastOrder.symbol} @{" "}
            {lastOrder.avgFillPrice ? formatUsd(lastOrder.avgFillPrice) : "市价"} · {lastOrder.status} · 确认{" "}
            {lastOrder.confirmationId}
          </div>
        ) : null}
      </div>
    </PageContainer>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="t-meta text-muted-foreground">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}
