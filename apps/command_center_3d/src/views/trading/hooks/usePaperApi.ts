/**
 * trading/hooks/usePaperApi — a thin React wrapper around the Paper API client.
 *
 * Each method is exposed as an async callback that threads the control token
 * through (it is ignored in mock mode). State helpers (`useAsync`) give the
 * pages a uniform loading/error/data shape without re-implementing it six times.
 */
import { useCallback, useState } from "react";
import { getPaperApi } from "../services/paperApi";

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

  const getMarketSnapshot = useCallback(
    (input: Parameters<typeof client.getMarketSnapshot>[0]) => client.getMarketSnapshot(input),
    [client],
  );
  const getOrderBook = useCallback(
    (input: Parameters<typeof client.getOrderBook>[0]) => client.getOrderBook(input),
    [client],
  );
  const createResearchRun = useCallback(
    (input: Parameters<typeof client.createResearchRun>[0]) =>
      client.createResearchRun(input, controlToken),
    [client, controlToken],
  );
  const getResearchRun = useCallback(
    (runId: string) => client.getResearchRun(runId, controlToken),
    [client, controlToken],
  );
  const listStrategies = useCallback(
    () => client.listStrategies(controlToken),
    [client, controlToken],
  );
  const getPaperAccount = useCallback(
    () => client.getPaperAccount(controlToken),
    [client, controlToken],
  );
  const createPaperOrder = useCallback(
    (input: Parameters<typeof client.createPaperOrder>[0]) =>
      client.createPaperOrder(input, controlToken),
    [client, controlToken],
  );
  const cancelPaperOrder = useCallback(
    (orderId: string) => client.cancelPaperOrder(orderId, controlToken),
    [client, controlToken],
  );
  const subscribeEvents = useCallback(
    (input?: Parameters<typeof client.subscribeEvents>[0]) =>
      client.subscribeEvents(input),
    [client],
  );

  return {
    getMarketSnapshot,
    getOrderBook,
    createResearchRun,
    getResearchRun,
    listStrategies,
    getPaperAccount,
    createPaperOrder,
    cancelPaperOrder,
    subscribeEvents,
  };
}
