/**
 * Deterministic hashing helpers. Uses Web Crypto (crypto.subtle) so the exact
 * same code runs on Cloudflare Workers and on Node 22 (global crypto.subtle).
 */
import { ENGINE_ANCHORS } from "./engine-anchor.mjs";

export async function sha256Hex(str) {
  const bytes = new TextEncoder().encode(String(str));
  const buf = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Deterministic snapshot id — the IDEMPOTENCY KEY.
 * Derived ONLY from (session_id, generation, cycle_key, account_version,
 * state_hash). It deliberately does NOT include nowMs, prices or any volatile
 * input, so re-running the SAME cycle always produces the SAME id and
 * INSERT OR IGNORE keeps both tables unchanged.
 */
export async function deterministicSnapshotId({ sessionId, generation, cycleKey, accountVersion, stateHash }) {
  const canonical = JSON.stringify({
    v: 1,
    session_id: String(sessionId),
    generation: Number(generation),
    cycle_key: String(cycleKey),
    account_version: Number(accountVersion),
    state_hash: String(stateHash),
  });
  return "v45eq_" + (await sha256Hex(canonical));
}

/** sha256 of a canonical (sorted-key) object row, for content comparison. */
export async function hashRow(row) {
  const canonical = canonicalize(row);
  return sha256Hex(canonical);
}

function canonicalize(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  const keys = Object.keys(value).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalize(value[k])).join(",") + "}";
}

/** Aggregate engine hash: sha256 over the canonical anchor map. */
export function computeEngineSha256(anchors = ENGINE_ANCHORS) {
  const keys = Object.keys(anchors).sort();
  const canonical = "{" + keys.map((k) => JSON.stringify(k) + ":" + JSON.stringify(anchors[k])).join(",") + "}";
  return sha256Hex(canonical);
}
