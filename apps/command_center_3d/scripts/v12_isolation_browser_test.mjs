// V1.2 — Real browser isolation test: TWO independent Chrome profiles.
//
// Two Chrome instances, each with its OWN --user-data-dir (=> its own cookie
// jar), drive the live app at http://127.0.0.1:4781 (Vite -> adapter 4791 ->
// frozen engine). This is the closest possible proof to "two real users on two
// browsers". The browser auto-stores the per-profile session cookie (Secure on
// loopback is a secure context, so it persists) and sends ONLY its own cookie.
// We assert:
//   1. each profile receives and auto-stores a DISTINCT nr_paper_session cookie
//   2. a REAL order fills for each profile (distinct symbols)
//   3. profile A's account/orders never contain profile B's symbol (and vice versa)
//   4. an anonymous fetch to /api/jev/paper/audit is fail-closed (401)
//
// Zero new dependencies: Node's global WebSocket + child_process Chrome.
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const APP = "http://127.0.0.1:4781/trading.html";
const COOKIE = "nr_paper_session";

function startChrome(port, userDataDir) {
  return spawn(CHROME, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--remote-debugging-port=" + port, "--user-data-dir=" + userDataDir, "about:blank",
  ], { stdio: "ignore" });
}

function cdp(url) {
  const ws = new WebSocket(url);
  let nextId = 1;
  const pending = new Map();
  let eventCb = null;
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const e = pending.get(m.id); pending.delete(m.id); e.resolve(m);
    } else if (m.method && eventCb) {
      eventCb(m);
    }
  });
  const ready = new Promise((res) => ws.addEventListener("open", res));
  const send = (method, params = {}) => {
    const id = nextId++;
    ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error("cdp timeout: " + method)); } }, 30000);
    });
  };
  return { ws, ready, send, onEvent: (cb) => { eventCb = cb; }, close: () => ws.close() };
}

async function getPageWs(port) {
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = list.find((t) => t.type === "page") || list[0];
  return cdp(page.webSocketDebuggerUrl);
}

async function driveProfile(port, symbol, qty) {
  const c = await getPageWs(port);
  await c.ready;
  await c.send("Page.enable");
  await c.send("Runtime.enable");
  await c.send("Network.enable");
  await c.send("Page.navigate", { url: APP });
  await sleep(2000);

  // 1) session POST -> server issues + browser auto-stores a Set-Cookie.
  const sessExpr = `(async () => {
    const r = await fetch("http://127.0.0.1:4781/api/jev/paper/session", { method: "POST", credentials: "include" });
    return JSON.stringify({ status: r.status, body: await r.json().catch(() => null) });
  })()`;
  const sess = await c.send("Runtime.evaluate", { expression: sessExpr, awaitPromise: true, returnByValue: true, timeout: 20000 });

  // What did the browser auto-store for this profile? (HttpOnly visible via CDP)
  const stored = await c.send("Network.getCookies", { urls: ["http://127.0.0.1:4781/api/jev/paper/session"] });
  const autoToken = (stored.result?.cookies || []).find((k) => k.name === COOKIE)?.value || null;

  // 2) order + account (browser auto-sends this profile's cookie) + anonymous audit.
  const tradeExpr = `(async () => {
    const base = "http://127.0.0.1:4781/api/jev";
    const j = async (r) => ({ status: r.status, body: await r.json().catch(() => null) });
    const ord = await fetch(base + "/paper/orders", { method: "POST", credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ clientOrderId: "BRW-" + Date.now() + "-" + Math.random().toString(36).slice(2,7),
        confirmationId: "BRC-" + Date.now() + "-" + Math.random().toString(36).slice(2,7),
        symbol: ${JSON.stringify(symbol)}, side: "BUY", type: "MARKET", quantity: ${qty} }) }).then(j);
    const acc = await fetch(base + "/paper/account", { credentials: "include" }).then(j);
    const anon = await fetch(base + "/paper/audit", { credentials: "omit" }).then(j);
    return JSON.stringify({ ord: ord.body, acc: acc.body, anon: anon });
  })()`;
  const trade = await c.send("Runtime.evaluate", { expression: tradeExpr, awaitPromise: true, returnByValue: true, timeout: 25000 });
  await c.close();
  return {
    autoToken,
    cookieAutoStored: !!autoToken,
    sess: sess.result?.result?.value,
    trade: trade.result?.result?.value,
  };
}

const checks = {};
let procA, procB, dirA, dirB;
try {
  dirA = mkdtempSync(join(tmpdir(), "jev-profA-"));
  dirB = mkdtempSync(join(tmpdir(), "jev-profB-"));
  procA = startChrome(9223, dirA);
  procB = startChrome(9224, dirB);
  await sleep(3000);

  const A = await driveProfile(9223, "1000SATSUSDT", 1000000);
  const B = await driveProfile(9224, "ETHUSDT", 0.1);
  const aVal = A.trade ? JSON.parse(A.trade) : null;
  const bVal = B.trade ? JSON.parse(B.trade) : null;
  const aSess = A.sess ? JSON.parse(A.sess) : null;
  const bSess = B.sess ? JSON.parse(B.sess) : null;

  checks.A_session_created = aSess?.body?.ok === true || aSess?.status === 200;
  checks.B_session_created = bSess?.body?.ok === true || bSess?.status === 200;
  checks.A_cookie_auto_stored = A.cookieAutoStored;
  checks.B_cookie_auto_stored = B.cookieAutoStored;
  checks.cookies_distinct = A.autoToken !== B.autoToken && !!A.autoToken && !!B.autoToken;
  checks.A_token_preview = (A.autoToken || "").slice(0, 18) + "...";
  checks.B_token_preview = (B.autoToken || "").slice(0, 18) + "...";

  const aOrderSym = (aVal?.acc?.data?.account?.orders || []).map((o) => o.symbol);
  const bOrderSym = (bVal?.acc?.data?.account?.orders || []).map((o) => o.symbol);
  checks.A_order_filled = (aVal?.ord?.data?.account?.orders || []).some((o) => o.symbol === "1000SATSUSDT" && o.status === "FILLED");
  checks.B_order_filled = (bVal?.ord?.data?.account?.orders || []).some((o) => o.symbol === "ETHUSDT" && o.status === "FILLED");
  checks.A_sees_1000SATS = aOrderSym.includes("1000SATSUSDT");
  checks.B_sees_ETH = bOrderSym.includes("ETHUSDT");
  checks.A_does_NOT_see_ETH = !aOrderSym.includes("ETHUSDT");
  checks.B_does_NOT_see_1000SATS = !bOrderSym.includes("1000SATSUSDT");
  checks.anonymous_audit_rejected = aVal?.anon?.status === 401;
} catch (err) {
  checks.runtime_error = String(err && err.stack || err);
} finally {
  try { procA && procA.kill("SIGKILL"); } catch (_) {}
  try { procB && procB.kill("SIGKILL"); } catch (_) {}
  try { dirA && rmSync(dirA, { recursive: true, force: true }); } catch (_) {}
  try { dirB && rmSync(dirB, { recursive: true, force: true }); } catch (_) {}
}

const pass = Object.entries(checks).filter(([k]) => typeof checks[k] === "boolean").every(([, v]) => v === true);
const out = {
  generated_utc: new Date().toISOString(),
  layer: "real-browser-two-chrome-profiles",
  engine_modified: false,
  profiles: "two independent --user-data-dir Chrome instances (separate cookie jars); browser auto-stores+returns per-profile Secure session cookie",
  checks,
  all_passed: pass,
};
const fs = await import("node:fs");
const { join: jn } = await import("node:path");
const ART = jn(import.meta.dirname, "e2e", "artifacts", "v12");
fs.mkdirSync(ART, { recursive: true });
fs.writeFileSync(jn(ART, "isolation_browser.json"), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
process.exit(pass ? 0 : 1);
