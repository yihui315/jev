/**
 * trading/hooks/usePaperApi — thin React wrappers around the Paper API client.
 *
 * Each method is an async callback. The legacy `controlToken` is retained for a
 * stable signature but is ignored: `/api/paper/*` authorises via the
 * `nr_paper_session` cookie bootstrapped by `ensureSession`; `/api/agents` only
 * needs a provider id (credentials stay server-side).
 */
import { useCallback, useState } from "react";
import { getPaperApi } from "../services/paperApi";
import type {
  AccountResponse,
  CancelOrderResponse,
  CreateOrderResponse,
  Interval,
  OrderBook,
  OrderProposal,
  ResearchInput,
  ResearchRunResult,
  ServerEventsResponse,
  Symbol,
  Ticker,
  Candle,
} from "../services/types";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useAsync<T>(): AsyncState<T> & { run: (p: Promise<T>) => Promise<T> } {
  const [state, setState] = useState<AsyncState<T>>({ data: null, loading: false, error: null });
  const run = useCallback(async (p: Promise<T>) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await p;
      setState({ data, loading: false, error: null });
      return data;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setState((s) => ({ ...s, loading: false, error: msg }));
      throw e;
    }
  }, []);
  return { ...state, run };
}

export function usePaperApi(controlToken = "") {
  const client = getPaperApi(controlToken);

  const ensureSession = useCallback(() => client.ensureSession(), [client]);
  const getAccount = useCallback(() => client.getAccount(), [client]);
  const createOrder = useCallback(
    (input: OrderProposal) => client.createOrder(input),
    [client],
  );
  const cancelOrder = useCallback(
    (orderId: string, clientActionId: string) => client.cancelOrder(orderId, clientActionId),
    [client],
  );
  const resetAccount = useCallback(
    (input: { clientActionId: string; confirmationId: string }) => client.resetAccount(input),
    [client],
  );
  const getTicker = useCallback(
    (symbols: Symbol[]) => client.getTicker(symbols),
    [client],
  );
  const getCandles = useCallback(
    (symbol: Symbol, interval: Interval, limit?: number) => client.getCandles(symbol, interval, limit),
    [client],
  );
  const getOrderBook = useCallback(
    (symbol: Symbol, limit?: number) => client.getOrderBook(symbol, limit),
    [client],
  );
  const createResearchRun = useCallback(
    (input: ResearchInput) => client.createResearchRun(input),
    [client],
  );
  const getServerEvents = useCallback(
    (after?: number, limit?: number) => client.getServerEvents(after, limit),
    [client],
  );

  return {
    ensureSession,
    getAccount,
    createOrder,
    cancelOrder,
    resetAccount,
    getTicker,
    getCandles,
    getOrderBook,
    createResearchRun,
    getServerEvents,
  };
}

export type { AccountResponse, CancelOrderResponse, CreateOrderResponse, OrderBook, ResearchRunResult, ServerEventsResponse, Ticker, Candle };
