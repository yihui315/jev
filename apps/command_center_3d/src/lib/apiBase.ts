/**
 * THE single definition of the JCC product API base.
 *
 * WHY THIS MODULE EXISTS. Before it, four modules each derived the API base
 * independently, and only one of them understood the `same-origin` sentinel:
 * `App.tsx` collapsed it to "" while `JevProviderCard.tsx` and
 * `ConnectionsView.tsx` concatenated it literally, producing `same-origin/api/...`
 * -- a path, not an origin. A configuration knob that is honoured in one of four
 * places is worse than no knob: it looks supported and silently half-works.
 *
 * THE SHIPPED TOPOLOGY, measured (v1.3, config/jcc-v1.3.yaml):
 *   * the API is one process on 127.0.0.1:4779;
 *   * the SPA is another process on 127.0.0.1:4780, served by
 *     `apps/command_center_3d/serve_visual.py` (uvicorn/ASGI), which serves
 *     `/health`, `/api/health` and the static bundle -- it does NOT proxy
 *     `/api/v1/*`;
 *   * the SPA therefore talks to the API CROSS-ORIGIN, and `server.cors_origins`
 *     lists 4780/4781 for exactly that reason.
 *
 * So the shipped default -- `VITE_API_BASE` unset -- is the absolute loopback
 * URL below, and that is what the built bundle must carry.
 */

/** The API origin the shipped build talks to when no override is given. */
export const DEFAULT_API_BASE = "http://127.0.0.1:4779";

/**
 * Set `VITE_API_BASE=same-origin` to emit relative URLs, for a deployment that
 * puts the SPA and the API behind one origin (an external reverse proxy). No
 * JCC process ships that way today: `serve_visual.py` is a static server.
 */
export const SAME_ORIGIN = "same-origin";

/**
 * Resolve a raw `VITE_API_BASE` value into the origin to fetch from.
 *
 * - `undefined` (no env at build time) or `""` -> the shipped default;
 * - `"same-origin"` -> `""`, i.e. relative to the page origin;
 * - anything else -> that value, with trailing slashes removed so callers can
 *   always write `resolveApiBase(x) + "/api/..."`.
 */
export function resolveApiBase(raw?: string | null): string {
  const v = typeof raw === "string" ? raw.trim() : "";
  if (v === SAME_ORIGIN) {
    return "";
  }
  if (v === "") {
    return DEFAULT_API_BASE;
  }
  return v.replace(/\/+$/, "");
}

/** The value of `import.meta.env.VITE_API_BASE`, as a plain string or undefined. */
export function envApiBase(): string | undefined {
  const e = (import.meta as unknown as { env?: { VITE_API_BASE?: string } }).env;
  return e?.VITE_API_BASE;
}
