/**
 * Shared isolated-test harness. Everything here is LOCAL:
 *   - in-memory SQLite D1 shim (no cloud DB)
 *   - injected fetchers (no live network dependency for the deterministic
 *     assertions; a REAL network probe is done separately in T0)
 *   - injected clocks (nowMs) for reproducibility
 */
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { D1Shim } from "./d1-shim.mjs";
import { sha256Hex, hashRow } from "../../src/hash.mjs";
import { runCycle } from "../../src/collector.mjs";
import collector from "../../src/collector.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, "../..");
export const SCHEMA_PATH = resolve(ROOT, "schema.sql");
export const EVIDENCE_DIR = resolve(ROOT, "evidence");

export const { scheduled, fetch: fetchEntrypoint } = collector;
export { runCycle, D1Shim, sha256Hex, hashRow };

// REAL base prices measured from api.binance.us via a genuine HTTP probe on
// 2026-11-09 (BTCUSDT 74951.77 / 74968.36, ETHUSDT 2143.35). Used as the
// anchor for controlled test tickers so prices are REAL-shaped, never random.
// Cycle-to-cycle variation is a CONTROLLED test input (explicitly labelled),
// not a claim of a live feed: the local proxy cannot reach api.binance.us.
export const REAL_BASE_PRICES = {
  BTCUSDT: "74951.77000000",
  ETHUSDT: "2143.35000000",
};

export function openDb() {
  return D1Shim.fromSchemaFile(SCHEMA_PATH);
}

export function makeEnv(db, fetcher, opts = {}) {
  return {
    EQ_DB: db,
    ENABLE_REAL_TRADING: opts.enableRealTrading ?? "false",
    V45_TEST_TRIGGER: opts.testTrigger ?? "false",
    __fetch: fetcher,
  };
}

export async function makeState({ cash, initialCash = 100000, positions = {}, realizedPnl = 0, generation = 1 }) {
  const state = {
    generation,
    cash: cash ?? initialCash,
    initialCash,
    positions,
    realizedPnl,
  };
  const state_json = JSON.stringify(state);
  const state_hash = await sha256Hex(state_json);
  return { state, state_json, state_hash };
}

export async function insertSession(db, sid, { nowMs = Date.now(), expiresInMs = 3600000, revokedAt = null } = {}) {
  const createdAt = new Date(nowMs - 1000).toISOString();
  const expiresAt = new Date(nowMs + expiresInMs).toISOString();
  await db.prepare(
    "INSERT INTO paper_sessions (session_id, created_at, expires_at, revoked_at) VALUES (?, ?, ?, ?)"
  ).bind(sid, createdAt, expiresAt, revokedAt).run();
  return { createdAt, expiresAt };
}

export async function insertAccount(db, sid, state_json, state_hash, { version = 1, nowMs = Date.now() } = {}) {
  const iso = new Date(nowMs).toISOString();
  await db.prepare(
    "INSERT INTO paper_accounts (session_id, version, state_json, state_hash, last_command_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
  ).bind(sid, version, state_json, state_hash, "seed", iso, iso).run();
}

/**
 * Controlled ticker fetcher. Returns real Binance-shaped Response objects.
 * mode: ok | throw | http503 | http400 | missing | nonpositive | notjson
 * `serverTime` controls freshness (nowMs - 120000 => STALE).
 */
export function controlledFetcher(opts = {}) {
  const {
    prices = REAL_BASE_PRICES,
    nowMs = Date.now(),
    serverTime = nowMs,
    mode = "ok",
  } = opts;
  return async (url) => {
    const u = new URL(url);
    if (u.pathname === "/api/v3/time") {
      return new Response(JSON.stringify({ serverTime }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    if (u.pathname === "/api/v3/ticker/price") {
      if (mode === "throw") throw new TypeError("fetch failed (simulated outage)");
      if (mode === "http503") return new Response("upstream unavailable (simulated)", { status: 503 });
      if (mode === "http400") return new Response(JSON.stringify({ code: -1121, msg: "Invalid symbol." }), {
        status: 400, headers: { "content-type": "application/json" },
      });
      if (mode === "notjson") return new Response("<html>WAF block</html>", {
        status: 200, headers: { "content-type": "text/html", date: new Date(nowMs).toUTCString() },
      });
      const symsParam = u.searchParams.get("symbols");
      const single = u.searchParams.get("symbol");
      const syms = symsParam ? JSON.parse(symsParam) : (single ? [single] : []);
      let items = syms.map((s) => ({ symbol: s, price: prices[s] ?? "0" }));
      if (mode === "missing") items = items.slice(1);
      if (mode === "nonpositive") items = items.map((it, i) => (i === 0 ? { ...it, price: "0" } : it));
      return new Response(JSON.stringify(items), {
        status: 200,
        headers: { "content-type": "application/json", date: new Date(nowMs).toUTCString() },
      });
    }
    return new Response("{}", { status: 404 });
  };
}

export function scalePrice(base, factor) {
  // deterministic controlled price change, 8 decimals
  return (Number(base) * factor).toFixed(8);
}

export function newRecorder(testId, meta = {}) {
  const r = { testId, meta, checks: [], startedAt: new Date().toISOString() };
  r.check = (name, cond, detail = null) => r.checks.push({ name, pass: !!cond, detail });
  r.finish = (extra = {}) => {
    const pass = r.checks.length > 0 && r.checks.every((c) => c.pass);
    const out = {
      testId,
      verdict: pass ? "PASS" : "FAIL",
      startedAt: r.startedAt,
      finishedAt: new Date().toISOString(),
      meta: r.meta,
      checks: r.checks,
      extra,
    };
    console.log("RESULT_JSON:" + JSON.stringify(out));
    process.exit(pass ? 0 : 1);
  };
  return r;
}

export async function rows(db, sql, ...args) {
  return (await db.prepare(sql).bind(...args).all()).results;
}

export function assert(cond, msg) {
  if (!cond) throw new Error("ASSERT_FAILED: " + msg);
}
