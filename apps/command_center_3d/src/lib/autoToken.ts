/**
 * Auto-configuration of the control token: an OPT-IN local convenience.
 *
 * WHY THIS EXISTS
 * ---------------
 * The control token is a CREDENTIAL, and by design it lives in memory only -- it
 * is never written to localStorage, a cookie, the URL or a log (see
 * `authorizedFetch.ts`, `SettingsView.tsx` and `App.tsx`). That contract is
 * correct and this module does NOT weaken it: the value fetched here is put into
 * React state, i.e. memory, and nowhere else.
 *
 * What it adds is a way for the token to ARRIVE without being typed. The process
 * serving the SPA (`serve_visual.py`) may hand out the token it was started with
 * over a same-origin `GET /api/config`. That keeps the secret out of the built
 * bundle -- nothing is baked into `dist/`, which is a committed build artifact --
 * and out of browser storage. The only thing that changes is that the operator
 * does not have to paste it.
 *
 * WHY "SAME-ORIGIN" AND NOT THE API BASE
 * --------------------------------------
 * The SPA and the API are deliberately two processes (API 4779, SPA 4780) and the
 * UI talks to the API cross-origin. But `/api/config` is served by the SPA's OWN
 * origin, so this fetch is same-origin: it needs no CORS allowlist entry, and it
 * cannot be confused with the product API surface on 4779.
 *
 * WHY IT IS OPT-IN
 * ----------------
 * Serving a credential to whoever loads the page is only defensible because this
 * product is loopback-only and single-operator. `serve_visual.py` therefore does
 * it only when explicitly enabled (`JCC_UI_AUTO_TOKEN=1`), so the shipped default
 * is unchanged: no token is served, and the operator is prompted as before.
 *
 * A missing endpoint, a non-JSON body, a 404 or a network error all mean the same
 * thing -- "no auto token" -- and must never surface as a UI error, because auto
 * configuration is a convenience, not a requirement.
 */

/** Read the auto-configured control token, or `null` when there is none. */
export async function fetchAutoControlToken(signal?: AbortSignal): Promise<string | null> {
  try {
    const res = await fetch("/api/config", {
      // The response is not a credential-protected resource; omitting cookies
      // keeps this request from ever carrying session state.
      credentials: "omit",
      cache: "no-store",
      ...(signal ? { signal } : {}),
    });
    if (!res.ok) return null;
    const body: unknown = await res.json();
    if (body === null || typeof body !== "object") return null;
    const value = (body as { controlToken?: unknown }).controlToken;
    return typeof value === "string" && value.length > 0 ? value : null;
  } catch {
    // Offline, blocked, or the server does not implement /api/config: no auto token.
    return null;
  }
}
