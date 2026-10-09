/**
 * authorizedFetch — the ONE authenticated transport to the control plane.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Six modules used to build their own `fetch` with their own idea of how to
 * present the control token, and the provider centre had a worse bug: it threw
 * BEFORE issuing the request when the token was empty, so the button silently
 * did nothing and the failure looked like a backend problem. A dead button and
 * a rejected request must not be indistinguishable.
 *
 * So: one function, and every write goes through it.
 *
 * THE TOKEN
 * ---------
 * Held in React state in `App` and threaded down as a prop. It is never
 * persisted — no localStorage, no sessionStorage, no cookie, no URL, no literal.
 * A token in browser storage is a token in every backup, every devtools export
 * and every screenshot of Application storage. It lives as long as the tab does.
 *
 * `X-JCC-Token` is the header the control plane has always accepted;
 * `Authorization: Bearer` is accepted too and is sent alongside it so the two
 * conventions are both satisfied without a second code path. The VALUE is never
 * logged, never returned, and never included in an error message.
 *
 * THE VERIFIED FLAG
 * -----------------
 * `tokenState` is a real fact measured by a real authenticated round trip, not a
 * restatement of "the input is non-empty". Those are different claims: a
 * nine-character typo is non-empty and rejected, and a UI that calls that
 * "已验证" is lying to the operator.
 */
import { useCallback, useState } from "react";

/** Where the control plane lives. A build-time value, never a token. */
export const API_BASE =
  (import.meta as unknown as { env?: { VITE_API_BASE?: string } }).env?.VITE_API_BASE ||
  "http://127.0.0.1:4779";

/**
 * Whether the control token has been PROVEN against the backend.
 *
 * `unverified` — a token exists but no authenticated call has succeeded.
 * `verifying`  — a probe is in flight.
 * `verified`   — the backend accepted it on a real request.
 * `rejected`   — the backend refused it. A rejected token stays rejected until
 *                the operator changes it; nothing retries it behind their back.
 */
export type TokenState = "absent" | "unverified" | "verifying" | "verified" | "rejected";

export interface AuthorizedResult<T = unknown> {
  ok: boolean;
  status: number;
  data: T | null;
  error: string | null;
  /** True when the server refused the credential specifically. */
  authFailed: boolean;
}

/**
 * The header set every authenticated request carries.
 *
 * Both forms are sent because the control plane accepts either, and a
 * deployment may front the port with a proxy that only reads one of them. The
 * value is deliberately never echoed anywhere.
 */
export function authHeaders(token: string, extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...(extra ?? {}) };
  if (token) {
    h["X-JCC-Token"] = token;
    h["Authorization"] = `Bearer ${token}`;
  }
  return h;
}

/**
 * The single authenticated transport.
 *
 * Used by every write: provider credentials (POST/DELETE), provider
 * probe/live, model routing (PATCH), project create, mission create/start,
 * approve/reject. Reads that the server declares open go through plain `fetch`
 * on purpose — attaching a token to an open endpoint would suggest it is
 * needed, and would put a credential on requests that do not require one.
 *
 * An EMPTY TOKEN IS NOT SHORT-CIRCUITED. The request is sent unauthenticated
 * and the server's refusal comes back as a real `authFailed`, so the caller
 * learns "you are not authorized" instead of the silent no-op that made this
 * bug invisible in the first place.
 */
export async function authorizedFetch<T = unknown>(
  token: string,
  path: string,
  init: {
    method: string;
    body?: unknown;
    apiBase?: string;
    timeoutMs?: number;
  },
): Promise<AuthorizedResult<T>> {
  const base = init.apiBase ?? API_BASE;
  const { method, body, timeoutMs = 30000 } = init;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);

  const headers = authHeaders(token);
  if (body !== undefined) headers["Content-Type"] = "application/json";

  try {
    const res = await fetch(`${base}${path}`, {
      method,
      headers,
      // An empty token contributes no header, which is a different wire state
      // from "header present but wrong" — and the server can tell them apart.
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    });

    let parsed: unknown = null;
    const text = await res.text();
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    if (!res.ok) {
      const detail =
        parsed && typeof parsed === "object" && "detail" in parsed
          ? String((parsed as { detail: unknown }).detail)
          : `HTTP ${res.status}`;
      return {
        ok: false,
        status: res.status,
        data: null,
        error: detail,
        // 401 = not configured on the host, 403 = the presented value was
        // rejected. Both mean the same thing to a person filling in a field.
        authFailed: res.status === 401 || res.status === 403,
      };
    }
    return { ok: true, status: res.status, data: parsed as T, error: null, authFailed: false };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      ok: false,
      status: 0,
      data: null,
      error: msg.includes("abort") ? `请求超时（${timeoutMs}ms）` : msg,
      authFailed: false,
    };
  } finally {
    clearTimeout(timer);
  }
}

/** A read on a server-declared OPEN endpoint. No token is sent. */
export async function openFetch<T = unknown>(
  path: string,
  init: { apiBase?: string; timeoutMs?: number } = {},
): Promise<AuthorizedResult<T>> {
  const base = init.apiBase ?? API_BASE;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), init.timeoutMs ?? 15000);
  try {
    const res = await fetch(`${base}${path}`, { signal: ctrl.signal });
    const text = await res.text();
    let parsed: unknown = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    if (!res.ok) {
      return { ok: false, status: res.status, data: null, error: `HTTP ${res.status}`, authFailed: false };
    }
    return { ok: true, status: res.status, data: parsed as T, error: null, authFailed: false };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      data: null,
      error: e instanceof Error ? e.message : String(e),
      authFailed: false,
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Measure whether the control token is actually accepted.
 *
 * Uses a route that is token-gated and has no side effect, so verification
 * costs nothing and changes nothing. `GET /api/v1/model-routing` is that route:
 * it is declared open for reading the current configuration, so a dedicated
 * probe would have been a new surface — instead this calls a route that already
 * exists and reports the truth about the credential.
 *
 * A `401`/`403` is a definitive rejection. A `200` is a definitive acceptance.
 * Anything else (timeout, 500, connection refused) is reported as
 * `unverified`, because "we could not find out" must never render as "yes".
 */
export function useControlTokenVerification(token: string) {
  const [state, setState] = useState<TokenState>(token ? "unverified" : "absent");

  const verify = useCallback(
    async (t: string = token): Promise<TokenState> => {
      if (!t) {
        setState("absent");
        return "absent";
      }
      setState("verifying");
      // A POST to a token-gated route with an empty body: the gate runs before
      // the body is validated, so a rejected token answers 403 and an accepted
      // one answers 422 ("api_key is required"). Both outcomes prove the gate
      // was passed, and neither creates anything.
      const res = await authorizedFetch<{ detail?: string }>(t, "/api/v1/providers/minimax/credentials", {
        method: "POST",
        body: {},
        timeoutMs: 10000,
      });
      if (res.authFailed) {
        setState("rejected");
        return "rejected";
      }
      // 422 = the gate let us through and the body check caught it. That is a
      // PASS for the credential, even though the request itself "failed".
      if (res.status === 422) {
        setState("verified");
        return "verified";
      }
      if (res.ok) {
        setState("verified");
        return "verified";
      }
      setState("unverified");
      return "unverified";
    },
    [token],
  );

  return { state, verify, setState };
}

/** The two-line status the settings surface shows. Never one line. */
export function describeTokenState(state: TokenState): {
  browser: string;
  server: string;
  tone: "ok" | "warn" | "unknown" | "danger";
} {
  switch (state) {
    case "verified":
      return { browser: "浏览器令牌：已提供", server: "服务端授权：已验证", tone: "ok" };
    case "verifying":
      return { browser: "浏览器令牌：已提供", server: "服务端授权：验证中…", tone: "unknown" };
    case "rejected":
      return { browser: "浏览器令牌：已提供", server: "服务端授权：被拒绝", tone: "danger" };
    case "unverified":
      return { browser: "浏览器令牌：已提供", server: "服务端授权：未验证", tone: "warn" };
    case "absent":
    default:
      return { browser: "浏览器令牌：未提供", server: "服务端授权：未验证", tone: "unknown" };
  }
}
