// React hook over jevFetch. Single polling/loading/error/stale state machine for
// every page. Honest states only: loading, error (with code), stale, unreachable,
// and the provenance label from the adapter.
import { useCallback, useEffect, useRef, useState } from "react";
import { jevFetch, type JevResult } from "./client";

export interface QueryState<T> {
  data?: T;
  meta?: JevResult<T>["meta"];
  loading: boolean;
  error?: string;
  code?: string;
  unreachable: boolean;
  /** true once we have ever received data and are now re-fetching (stale view) */
  stale: boolean;
  lastFetched?: number;
  refetch: () => void;
}

export function useJevQuery<T = unknown>(
  path: string,
  opts: { intervalMs?: number; enabled?: boolean; method?: string; body?: unknown } = {},
): QueryState<T> {
  const { intervalMs, enabled = true, method = "GET", body } = opts;
  const [state, setState] = useState<QueryState<T>>({
    loading: true,
    unreachable: false,
    stale: false,
    refetch: () => {},
  });
  const inFlight = useRef(false);
  const mounted = useRef(true);

  const run = useCallback(() => {
    if (!mounted.current || !enabled) return;
    if (inFlight.current) return;
    inFlight.current = true;
    setState((s) => ({ ...s, loading: s.data === undefined, stale: s.data !== undefined }));
    jevFetch<T>(path, { method, body })
      .then((r) => {
        if (!mounted.current) return;
        setState({
          data: r.data,
          meta: r.meta,
          loading: false,
          error: r.ok ? undefined : r.error,
          code: r.ok ? undefined : r.code,
          unreachable: r.unreachable,
          stale: false,
          lastFetched: r.receivedAt,
          refetch: run,
        });
      })
      .finally(() => {
        inFlight.current = false;
      });
  }, [path, enabled, method, body]);

  useEffect(() => {
    mounted.current = true;
    run();
    if (intervalMs && intervalMs > 0 && enabled) {
      const id = window.setInterval(run, intervalMs);
      return () => {
        mounted.current = false;
        window.clearInterval(id);
      };
    }
    return () => { mounted.current = false; };
  }, [run, intervalMs, enabled]);

  return { ...state, refetch: run };
}
