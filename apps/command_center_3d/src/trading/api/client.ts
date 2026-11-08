// JEV Trading Desk V1.0 — unified backend data adapter client.
//
// HARD RULE (reinforced from the execution order):
//   This is the ONLY module that talks to /api/jev/*. No page imports another
//   fetch helper, no page ships static JSON, no page computes fills or account
//   state. Every read carries provenance metadata (meta.label / meta.source /
//   meta.channel / meta.fetched_at_utc) and every page renders it honestly.
//
// The browser never holds a Provider API key. Paper orders go ONLY through the
// server-authoritative engine. WebSocket is not used — the market channel is
// REST_POLLING and is labelled as such everywhere.

import { authHeader } from "./previewAuth";

export const API_BASE = "/api/jev";

export type Label = "LIVE" | "BACKFILL" | "BLOCKED" | "NO_DATA" | "UNAVAILABLE" | "VERIFIED";

export interface JevMeta {
  generated_utc?: string;
  source?: string;
  channel?: string;
  label?: Label;
  fetched_at_utc?: string;
  http_status?: number;
  websocket_note?: string;
  [key: string]: unknown;
}

export interface JevResponse<T = unknown> {
  schema: string;
  ok: boolean;
  data?: T;
  error?: string;
  code?: string;
  meta: JevMeta;
}

export interface JevResult<T> {
  ok: boolean;
  data?: T;
  error?: string;
  code?: string;
  meta?: JevMeta;
  status: number;
  /** wall-clock when the browser received the response */
  receivedAt: number;
  /** true when the request never reached the adapter (network/offline/timeout) */
  unreachable: boolean;
}

export class JevApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const DEFAULT_TIMEOUT_MS = 15000;

export async function jevFetch<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<JevResult<T>> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  if (opts.signal) {
    if (opts.signal.aborted) ctrl.abort();
    else opts.signal.addEventListener("abort", () => ctrl.abort(), { once: true });
  }
  const receivedAt = Date.now();
  // Normalize base + path so both "/paper/orders" (leading slash) and
  // "market/klines" (no leading slash) join to exactly one slash. A missing
  // slash previously produced "/api/jevmarket/..." which the adapter could not
  // route and returned UNKNOWN_ROUTE / NOT_FOUND for every request.
  const base = API_BASE.endsWith("/") ? API_BASE.slice(0, -1) : API_BASE;
  const rel = path.startsWith("/") ? path : "/" + path;
  const url = base + rel;
  const headers: Record<string, string> = { "content-type": "application/json", accept: "application/json" };
  // Attach the preview Basic credentials when present so the staging gate is
  // satisfied. Absent credentials → the Function returns 401 and the UI prompts.
  const ah = authHeader();
  if (ah) headers["authorization"] = ah;
  try {
    const res = await fetch(url, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: ctrl.signal,
      credentials: "same-origin",
    });
    const text = await res.text();
    let parsed: JevResponse<T>;
    try {
      parsed = JSON.parse(text) as JevResponse<T>;
    } catch {
      return {
        ok: false,
        error: "响应不是合法的 JSON（适配器可能不可用）",
        code: "BAD_RESPONSE",
        status: res.status,
        receivedAt,
        unreachable: false,
      };
    }
    return {
      ok: res.ok && parsed.ok !== false,
      data: parsed.data,
      error: parsed.error,
      code: parsed.code,
      meta: parsed.meta,
      status: res.status,
      receivedAt,
      unreachable: false,
    };
  } catch (err) {
    const e = err as Error;
    const timedOut = e.name === "AbortError";
    return {
      ok: false,
      error: timedOut ? "请求超时（适配器未响应）" : e.message || "网络不可达",
      code: timedOut ? "REQUEST_TIMEOUT" : "NETWORK_ERROR",
      status: 0,
      receivedAt,
      unreachable: true,
    };
  } finally {
    clearTimeout(t);
  }
}

// ---- Paper order actions (server-authoritative only) -------------------------
export interface PlaceOrderBody {
  clientOrderId: string;
  confirmationId: string;
  symbol: string;
  side: "BUY" | "SELL";
  type: "MARKET" | "LIMIT";
  quantity: number;
  limitPrice?: number;
}

export function makeIds(prefix: string): { clientOrderId: string; confirmationId: string } {
  const t = Date.now();
  const rand = Math.random().toString(36).slice(2, 10);
  return {
    clientOrderId: `${prefix}-${t}-${rand}`,
    confirmationId: `${prefix}-conf-${t}-${rand}`,
  };
}

export async function placePaperOrder(body: PlaceOrderBody) {
  return jevFetch("/paper/orders", { method: "POST", body });
}

export async function cancelPaperOrder(orderId: string) {
  return jevFetch(`/paper/orders/${encodeURIComponent(orderId)}/cancel`, {
    method: "POST",
    body: { clientActionId: makeIds("cancel").clientOrderId },
  });
}

// ---- Label → CSS class -------------------------------------------------------
export function labelClass(label?: Label): string {
  switch (label) {
    case "LIVE": return "jev-pill live";
    case "BACKFILL": return "jev-pill backfill";
    case "BLOCKED": return "jev-pill blocked";
    case "NO_DATA": return "jev-pill nodata";
    case "UNAVAILABLE": return "jev-pill unavailable";
    case "VERIFIED": return "jev-pill verified";
    default: return "jev-pill nodata";
  }
}
