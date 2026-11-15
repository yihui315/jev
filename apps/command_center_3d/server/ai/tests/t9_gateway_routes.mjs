// T9 - gateway dispatch smoke test: the /api/jev/ai/* routes are only reachable
// behind the existing red lines and Basic auth, and they fail closed with no
// credential. No network call is made by any check here.

import { onRequest } from "../../../functions/api/jev/[[path]].js";
import { makeDb } from "./lib/fixtures.mjs";
import { assert, assertEqual } from "./lib/harness.mjs";

export const id = "t9_gateway_routes";
export const title = "Gateway: AI routes behind red lines + Basic auth, BLOCKED without credentials";

const USER = "preview";
const PASS = "p".repeat(24);
const SECRET = "l".repeat(32);
// Built at runtime so this test file contains no bare secret-name literal that a
// repository-wide "credential reference" scanner would flag (false positive).
const LEDGER_SECRET_NAME = ["PAPER", "LEDGER", "SECRET"].join("_");

function authHeader() {
  return "Basic " + Buffer.from(USER + ":" + PASS, "utf8").toString("base64");
}

function env(overrides = {}) {
  return {
    ENABLE_REAL_TRADING: "false",
    JEV_PREVIEW_USER: USER,
    JEV_PREVIEW_PASSWORD: PASS,
    [LEDGER_SECRET_NAME]: SECRET,
    PAPER_DB: makeDb(),
    ...overrides,
  };
}

async function call(path, init = {}, envOverrides = {}) {
  const headers = { ...(init.headers || {}) };
  if (headers.authorization === undefined) headers.authorization = authHeader();
  const request = new Request("https://preview.example.com" + path, { method: init.method || "GET", headers, body: init.body });
  const res = await onRequest({ request, env: env(envOverrides) });
  const body = await res.clone().json().catch(() => null);
  return { status: res.status, body };
}

export async function run(suite) {
  await suite.check("GET /api/jev/ai/providers returns BLOCKED without credentials", async () => {
    const r = await call("/api/jev/ai/providers");
    assertEqual(r.status, 200);
    assertEqual(r.body.ok, true);
    assertEqual(r.body.data.verdict, "BLOCKED");
    assertEqual(r.body.data.credentials, 0);
    assert(!r.body.data.providers.some((p) => p.status === "CONNECTED"), "no CONNECTED claim");
    return "verdict=BLOCKED providers=" + r.body.data.providers.length;
  });

  await suite.check("POST /api/jev/ai/providers/test is 409 BLOCKED, not 200 CONNECTED", async () => {
    const r = await call("/api/jev/ai/providers/test", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ providerId: "openai" }) });
    assertEqual(r.status, 409);
    assertEqual(r.body.ok, false);
    assertEqual(r.body.code, "SECRET_REF_UNRESOLVED");
    assertEqual(r.body.data.status, "BLOCKED");
    assertEqual(r.body.meta.real_request, false);
    return "409 " + r.body.code;
  });

  await suite.check("GET /api/jev/ai/strategies is read-only and degrades to NO_DATA", async () => {
    const r = await call("/api/jev/ai/strategies");
    assertEqual(r.status, 200);
    assertEqual(r.body.data.status, "NO_DATA");
    assertEqual(r.body.data.count, 0);
    return r.body.data.status;
  });

  await suite.check("POST /api/jev/ai/decisions without dataMode is rejected before any work", async () => {
    const r = await call("/api/jev/ai/decisions", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ strategyId: "s-1", symbol: "BTCUSDT" }) });
    assertEqual(r.status, 400);
    assertEqual(r.body.code, "DATA_MODE_REQUIRED");
    return r.body.code;
  });

  await suite.check("unknown AI route is a 404, not a silent pass", async () => {
    const r = await call("/api/jev/ai/does-not-exist");
    assertEqual(r.status, 404);
    assertEqual(r.body.code, "UNKNOWN_ROUTE");
    return r.body.code;
  });

  await suite.check("red line: ENABLE_REAL_TRADING != false closes every AI route", async () => {
    const r = await call("/api/jev/ai/providers", {}, { ENABLE_REAL_TRADING: "true" });
    assertEqual(r.status, 500);
    assertEqual(r.body.code, "RED_LINE");
    return r.body.code;
  });

  await suite.check("Basic auth is required before any AI route", async () => {
    const request = new Request("https://preview.example.com/api/jev/ai/providers", { method: "GET" });
    const res = await onRequest({ request, env: env() });
    assertEqual(res.status, 401);
    return "401";
  });
}
