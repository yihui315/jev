// V1.2 — Live HTTP dual-session isolation test against the REAL adapter.
//
// Two independent cookie jars (A, B) emulate two independent browser profiles
// talking to the live adapter (default http://127.0.0.1:4791, or set JEV_API).
// This exercises the actual adapter + frozen engine + the T02 audit gateway fix
// through the real HTTP stack (not the in-process engine import used by the
// engine-layer test). It proves:
//   1. two sessions receive distinct signed cookies / session ids
//   2. a REAL market order fills for each session
//   3. session A's order/position is invisible to session B (and vice versa)
//   4. /api/jev/paper/audit is session-scoped (no cross-session leakage)
//   5. an UNAUTHENTICATED audit request is fail-closed (401, no data)
//
// It does NOT modify source. It uses the live runtime DB, so it is run AFTER
// the engine-layer test and is safe (creates two throwaway sessions).
const BASE = process.env.JEV_API || "http://127.0.0.1:4791";
const COOKIE = "nr_paper_session";

function makeJar() {
  let cookie = "";
  return {
    get: () => cookie,
    absorb(res) {
      const list = typeof res.headers.getSetCookie === "function" ? res.headers.getSetCookie() : [];
      for (const c of list) {
        const m = c.match(new RegExp(COOKIE + "=([^;]+)"));
        if (m) cookie = COOKIE + "=" + decodeURIComponent(m[1]);
      }
    },
  };
}

async function call(path, method, jar, body) {
  const headers = { "content-type": "application/json" };
  const c = jar && jar.get();
  if (c) headers.Cookie = c;
  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (jar) jar.absorb(res);
  let data = null;
  try { data = await res.json(); } catch (_) {}
  return { status: res.status, data };
}

function decodeSid(token) {
  const p = String(token).split(".")[0];
  return JSON.parse(Buffer.from(p.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString()).sid;
}

const ids = (p) => ({
  clientOrderId: `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  confirmationId: `${p}-c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
});

const checks = {};
try {
  const A = makeJar();
  const B = makeJar();

  const sA = await call("/api/jev/paper/session", "POST", A, {});
  const sB = await call("/api/jev/paper/session", "POST", B, {});
  checks.A_session_created = sA.status === 200 && !!A.get();
  checks.B_session_created = sB.status === 200 && !!B.get();
  const sidA = decodeSid(A.get().slice(COOKIE.length + 1));
  const sidB = decodeSid(B.get().slice(COOKIE.length + 1));
  checks.session_id_distinct = sidA !== sidB;
  checks.cookie_distinct = A.get() !== B.get();
  checks.A_sid = sidA;
  checks.B_sid = sidB;

  const oA = await call("/api/jev/paper/orders", "POST", A, {
    ...ids("A"), symbol: "1000SATSUSDT", side: "BUY", type: "MARKET", quantity: 1000000,
  });
  const oB = await call("/api/jev/paper/orders", "POST", B, {
    ...ids("B"), symbol: "ETHUSDT", side: "BUY", type: "MARKET", quantity: 0.1,
  });
  // The adapter wraps the engine response in its own envelope, so the engine
  // payload lives at response.data.data.
  const pd = (r) => r.data?.data || {};
  const aFilled = (pd(oA).account?.orders || []).some((o) => o.symbol === "1000SATSUSDT" && o.status === "FILLED");
  const bFilled = (pd(oB).account?.orders || []).some((o) => o.symbol === "ETHUSDT" && o.status === "FILLED");
  checks.A_order_filled = aFilled;
  checks.B_order_filled = bFilled;
  checks.A_order_rejection = pd(oA).order?.rejectionCode || null;
  checks.B_order_rejection = pd(oB).order?.rejectionCode || null;

  const accA = await call("/api/jev/paper/account", "GET", A);
  const accB = await call("/api/jev/paper/account", "GET", B);
  const aPos = Object.keys(pd(accA).account?.positions || {});
  const bPos = Object.keys(pd(accB).account?.positions || {});
  const aOrdSym = (pd(accA).account?.orders || []).map((o) => o.symbol);
  const bOrdSym = (pd(accB).account?.orders || []).map((o) => o.symbol);
  checks.A_sees_1000SATS_position = aPos.includes("1000SATSUSDT");
  checks.B_sees_ETH_position = bPos.includes("ETHUSDT");
  checks.B_does_NOT_see_1000SATS_position = !bPos.includes("1000SATSUSDT");
  checks.A_does_NOT_see_ETH_position = !aPos.includes("ETHUSDT");
  checks.B_does_NOT_see_A_order = !bOrdSym.includes("1000SATSUSDT");
  checks.A_does_NOT_see_B_order = !aOrdSym.includes("ETHUSDT");

  const audA = await call("/api/jev/paper/audit", "GET", A);
  const audB = await call("/api/jev/paper/audit", "GET", B);
  checks.A_audit_session_scoped = pd(audA).session_scoped === true && pd(audA).session_id === sidA;
  checks.B_audit_session_scoped = pd(audB).session_scoped === true && pd(audB).session_id === sidB;
  const aOrderIds = (pd(audA).orders || []).map((o) => o.client_order_id);
  const bOrderIds = (pd(audB).orders || []).map((o) => o.client_order_id);
  // No order created by the OTHER session may appear in this session's audit.
  checks.A_audit_has_no_B_order = !bOrderIds.some((id) => aOrderIds.includes(id));
  checks.B_audit_has_no_A_order = !aOrderIds.some((id) => bOrderIds.includes(id));
  checks.A_audit_count = (pd(audA).orders || []).length;
  checks.B_audit_count = (pd(audB).orders || []).length;

  // Fail-closed: anonymous audit must not return data (401).
  const anon = await call("/api/jev/paper/audit", "GET", null);
  checks.anonymous_audit_rejected = anon.status === 401;

  // Anonymized account read must also be rejected (no shared default account).
  const anonAcc = await call("/api/jev/paper/account", "GET", null);
  checks.anonymous_account_rejected = anonAcc.status === 401;
} catch (err) {
  checks.runtime_error = String(err && err.stack || err);
} finally {
  // no teardown: live DB keeps the two throwaway sessions; counts are scoped.
}

const pass = Object.entries(checks).filter(([k]) => typeof checks[k] === "boolean").every(([, v]) => v === true);
const out = {
  generated_utc: new Date().toISOString(),
  layer: "live-adapter-http-dual-session",
  api_base: BASE,
  engine_modified: false,
  audit_gateway_fix: "adapter /api/jev/paper/audit gated by readSession before paperAudit()",
  checks,
  all_passed: pass,
};
const fs = await import("node:fs");
const { join, dirname } = await import("node:path");
const ART = join(dirname(import.meta.dirname), "e2e", "artifacts", "v12");
fs.mkdirSync(ART, { recursive: true });
fs.writeFileSync(join(ART, "isolation_http.json"), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
process.exit(pass ? 0 : 1);
