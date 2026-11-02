/**
 * trading/pages/TradingDesk — 交易台
 *
 * K-line (lightweight-charts with SVG fallback), an order book + depth, and a
 * mock order entry. The order entry builds a PaperOrderProposal and REQUIRES a
 * human confirmation before calling createPaperOrder — TAKER proposals MUST NOT
 * bypass this. No real submit happens; the mock fills instantly for display.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ShieldCheck, TriangleAlert } from "lucide-react";
import { PageContainer } from "../components/PageContainer";
import { CandlestickChart } from "../components/CandlestickChart";
import { DepthBar } from "../components/DepthBar";
import { usePaperApi } from "../hooks/usePaperApi";
import { formatUsd, validateOrderProposal } from "../lib/finance";
import type {
  Interval,
  MarketSnapshot,
  OrderBook,
  PaperOrder,
  PaperOrderProposal,
  Symbol,
} from "../services/types";
import { cn } from "../../../ui/cn";

const SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "ORDIUSDT"];
const INTERVALS: Interval[] = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function TradingDesk({ controlToken }: { controlToken: string }) {
  const api = usePaperApi(controlToken);
  const [symbol, setSymbol] = useState<Symbol>("BTCUSDT");
  const [interval, setInterval] = useState<Interval>("5m");
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [book, setBook] = useState<OrderBook | null>(null);
  const [error, setError] = useState<string | null>(null);

  // --- order entry form state (the PROPOSAL, not yet an order) ---
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"MARKET" | "LIMIT">("LIMIT");
  const [quantity, setQuantity] = useState<string>("0.01");
  const [limitPrice, setLimitPrice] = useState<string>("67000");
  const [confirmed, setConfirmed] = useState(false);
  const [submitState, setSubmitState] = useState<{ ok: boolean; msg: string } | null>(null);
  const [lastOrder, setLastOrder] = useState<PaperOrder | null>(null);

  const proposal: PaperOrderProposal = useMemo(
    () => ({
      symbol,
      side,
      type,
      quantity: Number(quantity),
      limitPrice: type === "LIMIT" ? Number(limitPrice) : undefined,
      // confirmationId is injected ONLY after the operator checks the box.
      confirmationId: confirmed ? `cfm_op_${Date.now().toString(36)}` : undefined,
    }),
    [symbol, side, type, quantity, limitPrice, confirmed],
  );

  const validation = validateOrderProposal(proposal);

  const refreshMarket = useCallback(async () => {
    setError(null);
    try {
      const [snap, ob] = await Promise.all([
        api.getMarketSnapshot({ symbols: [symbol], interval }).then((a) => a[0]),
        api.getOrderBook({ venue: "PAPER-BOOK", symbol }),
      ]);
      setSnapshot(snap);
      setBook(ob);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [api, symbol, interval]);

  useEffect(() => {
    void refreshMarket();
  }, [refreshMarket]);

  async function submit() {
    setSubmitState(null);
    // The proposal is validated client-side; createPaperOrder will ALSO reject a
    // missing confirmation server-side. This is the human gate TAKER cannot skip.
    if (!validation.ok) {
      setSubmitState({ ok: false, msg: validation.error ?? "订单不合法" });
      return;
    }
    try {
      // validation.ok guarantees confirmationId is present (the human gate).
      const order = await api.createPaperOrder({
        symbol: proposal.symbol,
        side: proposal.side,
        type: proposal.type,
        quantity: proposal.quantity,
        limitPrice: proposal.limitPrice,
        confirmationId: proposal.confirmationId!,
        researchRunId: proposal.researchRunId,
      });
      setLastOrder(order);
      setSubmitState({ ok: true, msg: `已提交（模拟成交）${order.orderId}` });
      setConfirmed(false);
    } catch (e) {
      setSubmitState({ ok: false, msg: e instanceof Error ? e.message : String(e) });
    }
  }

  const maxTotal = book
    ? Math.max(
        book.bids.at(-1)?.total ?? 1,
        book.asks.at(-1)?.total ?? 1,
      )
    : 1;

  return (
    <PageContainer
      title="交易台"
      description="K 线、订单簿与下单。下单需人工确认（confirmationId），模拟成交仅用于展示，不涉及真实资金。"
    >
      {error ? (
        <div className="mb-4 rounded-lg border border-status-danger/40 bg-status-danger/10 p-3 text-status-danger">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* K-line + symbol/interval selector */}
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
            {snapshot ? (
              <span className="ml-auto t-meta text-muted-foreground">
                最新 {formatUsd(snapshot.last, { decimals: snapshot.last < 100 ? 3 : 2 })}
              </span>
            ) : null}
          </div>
          {snapshot ? <CandlestickChart candles={snapshot.candles} /> : (
            <div className="h-80 animate-pulse rounded-lg border border-border bg-card" />
          )}
        </div>

        {/* Order book + depth */}
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="t-section text-foreground">订单簿</h2>
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
                {formatUsd(book.bids[0]?.price ?? 0)} / {formatUsd(book.asks[0]?.price ?? 0)}
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

      {/* Order entry */}
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
              {formatUsd((Number(quantity) || 0) * (type === "LIMIT" ? Number(limitPrice) || 0 : snapshot?.last || 0))}
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
            最近订单：{lastOrder.orderId} · {lastOrder.side} {lastOrder.quantity} {lastOrder.symbol} @{" "}
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
