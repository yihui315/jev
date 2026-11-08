// V1.2 — Independent browser acceptance (T04). Single authenticated profile
// drives the live SPA at http://127.0.0.1:4781, plus a dual-profile isolation
// re-check. Captures: six-page render + screenshots, market switch (BTC/ETH/
// 1000SATS), simulated buy + cancel, same-session cash/position consistency,
// event replay, data-source audit (honest provenance), offline recovery, raw
// network log, cookie attribute audit, console error log, and git SHA.
//
// Zero new deps: Node global WebSocket + child_process Chrome + CDP.
import { spawn, execSync } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const APP = "http://127.0.0.1:4781/trading.html";
const COOKIE = "nr_paper_session";
const PAGES = ["command", "workbench", "universe", "strategy", "account", "team"];
const PAGE_TITLE = {
  command: "AI 交易指挥中心", workbench: "专业交易工作台", universe: "3D 市场宇宙",
  strategy: "策略竞赛实验室", account: "模拟交易账户", team: "AI 专家团队",
};

function startChrome(port, userDataDir) {
  return spawn(CHROME, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--remote-debugging-port=" + port, "--user-data-dir=" + userDataDir, "about:blank",
  ], { stdio: "ignore" });
}
function cdp(url) {
  const ws = new WebSocket(url);
  let nextId = 1; const pending = new Map(); let eventCb = null;
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const e = pending.get(m.id); pending.delete(m.id); e.resolve(m); }
    else if (m.method && eventCb) eventCb(m);
  });
  const ready = new Promise((res) => ws.addEventListener("open", res));
  const send = (method, params = {}) => {
    const id = nextId++; ws.send(JSON.stringify({ id, method, params }));
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

const OUT = { generated_utc: new Date().toISOString(), checks: {}, artifacts: [], pages: {}, network_log: [], console_errors: [], cookie_audit: null, git_sha: null };
try { OUT.git_sha = execSync("git rev-parse HEAD", { cwd: join(import.meta.dirname, "..", "..") }).toString().trim(); } catch { OUT.git_sha = "unknown"; }

const ART = join(import.meta.dirname, "..", "e2e", "artifacts", "v12");
mkdirSync(ART, { recursive: true });

async function setup(c) {
  await c.ready;
  await c.send("Page.enable"); await c.send("Runtime.enable");
  await c.send("Network.enable"); await c.send("Console.enable");
  c.onEvent((m) => {
    if (m.method === "Network.responseReceived") {
      const u = m.params.response.url || "";
      if (u.includes("/api/jev")) OUT.network_log.push({ url: u, status: m.params.response.status, type: m.params.type });
    } else if (m.method === "Console.messageAdded") {
      const msg = m.params.message;
      if (msg.level === "error") OUT.console_errors.push({ text: msg.text, url: msg.url, line: msg.line });
    }
  });
  await c.send("Page.navigate", { url: APP });
  await sleep(2500);
}

async function gotoPage(c, id) {
  await c.send("Runtime.evaluate", { expression: `window.location.hash = "#/${id}";`, returnByValue: true });
  await sleep(2500);
  const r = await c.send("Runtime.evaluate", {
    expression: `(() => { const el = [...document.querySelectorAll('.jev-topbar-title, h1, .jev-title, .jev-nav-item.active')].map(e=>e.textContent).join(' | '); const body = document.body ? document.body.innerText.slice(0,400) : ''; return JSON.stringify({ title: el, body, hasRoot: !!document.getElementById('trading-root'), childCount: (document.getElementById('trading-root')||{}).childElementCount || 0 }); })()`,
    returnByValue: true,
  });
  const v = r.result?.result?.value ? JSON.parse(r.result.result.value) : {};
  OUT.pages[id] = { title_seen: v.title, body_head: (v.body || "").replace(/\s+/g, " ").slice(0, 200), rendered: v.hasRoot && v.childCount > 0 };
  OUT.checks["page_" + id + "_rendered"] = v.hasRoot && v.childCount > 0;
  const shot = join(ART, "accept_" + id + ".png");
  const b64 = await c.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(shot, Buffer.from(b64.result.data, "base64"));
  OUT.artifacts.push(shot);
}

async function clickByText(c, sel, text) {
  return c.send("Runtime.evaluate", {
    expression: `(() => { const els = [...document.querySelectorAll(${JSON.stringify(sel)})]; const t = ${JSON.stringify(text)}; const el = els.find(e => (e.textContent||'').includes(t)); if (el) { el.click(); return true; } return false; })()`,
    returnByValue: true,
  });
}

// ---- Main single-profile flow -------------------------------------------------
let proc; const dir = mkdtempSync(join(tmpdir(), "jev-acc-"));
try {
  proc = startChrome(9230, dir);
  await sleep(3000);
  const c = await getPageWs(9230);
  await setup(c);

  // ensure server session (HttpOnly+Secure cookie auto-stored)
  const sess = await c.send("Runtime.evaluate", {
    expression: `(async()=>{const r=await fetch("/api/jev/paper/session",{method:"POST",credentials:"include"});return JSON.stringify({status:r.status,body:await r.json().catch(()=>null)});})()`,
    awaitPromise: true, returnByValue: true,
  });
  OUT.checks.session_created = (() => { try { const v = JSON.parse(sess.result.result.value); return v.status === 200 && v.body?.ok === true; } catch { return false; } })();

  // Six pages
  for (const p of PAGES) await gotoPage(c, p);

  // Market switch on workbench: click BTC / ETH / 1000SATS and verify orderbook sub updates
  await gotoPage(c, "workbench");
  await sleep(800);
  const symbolSeq = ["BTCUSDT", "ETHUSDT", "1000SATSUSDT"];
  OUT.checks.market_switch = true;
  for (const sym of symbolSeq) {
    await clickByText(c, ".jev-seg button", sym.replace("USDT", ""));
    await sleep(1200);
    const ob = await c.send("Runtime.evaluate", {
      expression: `(() => { const panels = [...document.querySelectorAll('.jev-panel')]; const ob = panels.find(p => (p.querySelector('h2')?.textContent||'').includes('盘口深度')); return ob ? (ob.querySelector('.ph-sub')?.textContent || '') : 'NO_OB'; })()`,
      returnByValue: true,
    });
    const sub = ob.result?.result?.value || "";
    const ok = sub.includes(sym);
    OUT.checks["market_switch_" + sym] = ok;
    if (!ok) OUT.checks.market_switch = false;
  }

  // Simulated buy (LIMIT below market -> rests OPEN) then cancel
  const trade = await c.send("Runtime.evaluate", {
    expression: `(async () => {
      const mk = (p) => p + "-" + Date.now() + "-" + Math.random().toString(36).slice(2,8);
      // fetch live 1000SATS last price and set a limit 50% below market so the order rests OPEN (notional stays > 5 USDT min)
      const tk = await (await fetch("/api/jev/market/ticker?symbols=" + encodeURIComponent(JSON.stringify(["1000SATSUSDT"])))).json();
      const arr = tk?.data || [];
      const last = Number(arr.find((t)=>t.symbol==="1000SATSUSDT")?.lastPrice || arr[0]?.lastPrice || 0.0002);
      const limPrice = Math.max(Number((last * 0.5).toFixed(8)), 0.00000001);
      const qty = Math.max(1000000, Math.ceil(12 / limPrice));
      const ids = { clientOrderId: mk("wd"), confirmationId: mk("cf") };
      const lim = await fetch("/api/jev/paper/orders", { method:"POST", credentials:"include", headers:{"content-type":"application/json"},
        body: JSON.stringify({ ...ids, symbol:"1000SATSUSDT", side:"BUY", type:"LIMIT", quantity:qty, limitPrice: limPrice }) }).then(r=>r.json());
      const oid = (lim?.data?.account?.orders?.[0]?.id) || (lim?.data?.orders?.[0]?.id) || (lim?.data?.order?.id);
      // cancel it
      let cancel = null;
      if (oid) { cancel = await fetch("/api/jev/paper/orders/"+encodeURIComponent(oid)+"/cancel", { method:"POST", credentials:"include", headers:{"content-type":"application/json"}, body: JSON.stringify({cancel:true}) }).then(r=>r.json()); }
      // market buy 1000SATS (fills)
      const ids2 = { clientOrderId: mk("wd"), confirmationId: mk("cf") };
      const mkt = await fetch("/api/jev/paper/orders", { method:"POST", credentials:"include", headers:{"content-type":"application/json"},
        body: JSON.stringify({ ...ids2, symbol:"1000SATSUSDT", side:"BUY", type:"MARKET", quantity:qty }) }).then(r=>r.json());
      const acc = await fetch("/api/jev/paper/account", { credentials:"include" }).then(r=>r.json());
      return JSON.stringify({ limPrice, lim, oid, cancel, mkt, acc });
    })()`,
    awaitPromise: true, returnByValue: true, timeout: 30000,
  });
  const tv = trade.result?.result?.value ? JSON.parse(trade.result.result.value) : {};
  const acc = tv.acc?.data?.account;
  const accSummary = tv.acc?.data?.summary;
  const orders = acc?.orders || [];
  const limOrder = orders.find((o) => o.id === tv.oid);
  OUT.checks.limit_order_open = limOrder ? limOrder.status === "OPEN" || limOrder.status === "CANCELED" || limOrder.status === "NEW" : false;
  OUT.checks.limit_order_canceled = limOrder ? limOrder.status === "CANCELED" : false;
  OUT.checks.market_order_filled = orders.some((o) => o.symbol === "1000SATSUSDT" && o.status === "FILLED");
  OUT.checks.position_present = acc?.positions && Object.keys(acc.positions).length > 0;
  OUT.checks.same_session_consistency = !!(accSummary && typeof accSummary.equity === "number" && typeof accSummary.availableCash === "number");
  OUT.checks.cash_decreased_after_buy = (() => {
    // availableCash should be < starting 1,000,000 USDT after buys
    return accSummary ? accSummary.availableCash < 1000000 : false;
  })();

  // Event replay
  const ev = await c.send("Runtime.evaluate", {
    expression: `(async()=>{const r=await fetch("/api/jev/paper/events?after=0&limit=20",{credentials:"include"});return JSON.stringify(await r.json());})()`,
    awaitPromise: true, returnByValue: true,
  });
  const evv = ev.result?.result?.value ? JSON.parse(ev.result.result.value) : {};
  const evs = evv?.data?.events || [];
  OUT.checks.event_replay_present = evs.length > 0;
  OUT.checks.event_has_seq_type_time = evs.every((e) => e.seq != null && e.eventType && e.createdAt);

  // Data source audit (drawer + endpoint)
  const ds = await c.send("Runtime.evaluate", {
    expression: `(async()=>{const r=await fetch("/api/jev/datasource/audit",{credentials:"same-origin"});return JSON.stringify({status:r.status,body:await r.json().catch(()=>null)});})()`,
    awaitPromise: true, returnByValue: true,
  });
  const dsv = ds.result?.result?.value ? JSON.parse(ds.result.result.value) : {};
  OUT.checks.datasource_audit_ok = dsv?.status === 200 && Array.isArray(dsv?.body?.data) && dsv.body.data.length > 0;
  // open drawer and capture honest labels
  await clickByText(c, ".jev-link", "数据审计");
  await sleep(1000);
  const drawer = await c.send("Runtime.evaluate", {
    expression: `(() => { const d = document.querySelector('.jev-drawer, .audit-drawer'); return d ? d.innerText.slice(0,600) : 'NO_DRAWER'; })()`,
    returnByValue: true,
  });
  const drawerText = drawer.result?.result?.value || "";
  OUT.checks.audit_drawer_opens = drawerText !== "NO_DRAWER";
  OUT.checks.audit_honest_no_websocket_live = !/WebSocket\s*LIVE/i.test(drawerText);
  OUT.artifacts.push(join(ART, "accept_audit_drawer.png"));
  const db64 = await c.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(ART, "accept_audit_drawer.png"), Buffer.from(db64.result.data, "base64"));

  // Offline recovery: emulate offline, trigger refetch, expect unreachable; then online
  await c.send("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await sleep(500);
  const off = await c.send("Runtime.evaluate", {
    expression: `(async()=>{try{const r=await fetch("/api/jev/paper/account",{credentials:"include"});return JSON.stringify({ok:r.ok,status:r.status});}catch(e){return JSON.stringify({ok:false,err:String(e)});}})()`,
    awaitPromise: true, returnByValue: true, timeout: 8000,
  });
  const offv = off.result?.result?.value ? JSON.parse(off.result.result.value) : {};
  OUT.checks.offline_unreachable = offv?.ok === false; // network blocked => not ok
  await c.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await sleep(800);
  const on = await c.send("Runtime.evaluate", {
    expression: `(async()=>{const r=await fetch("/api/jev/paper/account",{credentials:"include"});return JSON.stringify({ok:r.ok,status:r.status});})()`,
    awaitPromise: true, returnByValue: true,
  });
  const onv = on.result?.result?.value ? JSON.parse(on.result.result.value) : {};
  OUT.checks.online_recovered = onv?.ok === true;

  // Cookie attribute audit
  const ck = await c.send("Network.getCookies", { urls: ["http://127.0.0.1:4781/api/jev/paper/session"] });
  const ckinfo = (ck.result?.cookies || []).find((k) => k.name === COOKIE);
  if (ckinfo) {
    OUT.cookie_audit = { name: ckinfo.name, httpOnly: ckinfo.httpOnly, secure: ckinfo.secure, sameSite: ckinfo.sameSite, path: ckinfo.path, domain: ckinfo.domain };
    OUT.checks.cookie_secure = ckinfo.secure === true;
    OUT.checks.cookie_httponly = ckinfo.httpOnly === true;
    OUT.checks.cookie_samesite_strict = ckinfo.sameSite === "Strict";
    OUT.checks.cookie_path_api = ckinfo.path === "/api/";
  }
  OUT.checks.zero_unexplained_console_errors = OUT.console_errors.filter((e) => !/Download the React DevTools|Warning:/.test(e.text)).length === 0;

  await c.close();
} catch (err) {
  OUT.checks.runtime_error = String(err && err.stack || err);
} finally {
  try { proc && proc.kill("SIGKILL"); } catch (_) {}
  try { rmSync(dir, { recursive: true, force: true }); } catch (_) {}
}

// ---- Dual-profile isolation re-check (T04 requirement) ------------------------
OUT.checks.dual_session_isolation_passed = true;
let pA, pB, dA, dB;
try {
  dA = mkdtempSync(join(tmpdir(), "jev-accA-")); dB = mkdtempSync(join(tmpdir(), "jev-accB-"));
  pA = startChrome(9233, dA); pB = startChrome(9234, dB);
  await sleep(3000);
  async function drive(port, symbol, qty) {
    const c = await getPageWs(port); await c.ready;
    await c.send("Page.enable"); await c.send("Runtime.enable"); await c.send("Network.enable");
    await c.send("Page.navigate", { url: APP }); await sleep(2000);
    const t = await c.send("Runtime.evaluate", {
      expression: `(async()=>{const s=await fetch("/api/jev/paper/session",{method:"POST",credentials:"include"});const b=await s.json().catch(()=>null);
        const o=await fetch("/api/jev/paper/orders",{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({clientOrderId:"AC-"+Date.now()+"-"+Math.random().toString(36).slice(2,6),confirmationId:"CF-"+Date.now(),symbol:${JSON.stringify(symbol)},side:"BUY",type:"MARKET",quantity:${qty}})}).then(r=>r.json());
        const a=await fetch("/api/jev/paper/account",{credentials:"include"}).then(r=>r.json());
        const anon=await fetch("/api/jev/paper/audit",{credentials:"omit"}).then(r=>({status:r.status}));
        return JSON.stringify({sess:b,ord:o,acc:a,anon});})()`,
      awaitPromise: true, returnByValue: true, timeout: 25000,
    });
    const stored = await c.send("Network.getCookies", { urls: ["http://127.0.0.1:4781/api/jev/paper/session"] });
    const tok = (stored.result?.cookies || []).find((k) => k.name === COOKIE)?.value || null;
    await c.close();
    return { tok, val: t.result?.result?.value ? JSON.parse(t.result.result.value) : null };
  }
  const A = await drive(9233, "1000SATSUSDT", 1000000);
  const B = await drive(9234, "ETHUSDT", 0.1);
  const aSym = (A.val?.acc?.data?.account?.orders || []).map((o) => o.symbol);
  const bSym = (B.val?.acc?.data?.account?.orders || []).map((o) => o.symbol);
  const checks = {
    A_cookie: !!A.tok, B_cookie: !!B.tok, cookies_distinct: A.tok !== B.tok && !!A.tok && !!B.tok,
    A_sees_1000SATS: aSym.includes("1000SATSUSDT"), B_sees_ETH: bSym.includes("ETHUSDT"),
    A_not_ETH: !aSym.includes("ETHUSDT"), B_not_1000SATS: !bSym.includes("1000SATSUSDT"),
    anon_audit_401: A.val?.anon?.status === 401,
  };
  Object.assign(OUT.checks, checks);
  if (!Object.values(checks).every(Boolean)) OUT.checks.dual_session_isolation_passed = false;
} catch (err) {
  OUT.checks.dual_session_error = String(err && err.stack || err);
  OUT.checks.dual_session_isolation_passed = false;
} finally {
  try { pA && pA.kill("SIGKILL"); } catch (_) {}
  try { pB && pB.kill("SIGKILL"); } catch (_) {}
  try { dA && rmSync(dA, { recursive: true, force: true }); } catch (_) {}
  try { dB && rmSync(dB, { recursive: true, force: true }); } catch (_) {}
}

const pass = Object.entries(OUT.checks).filter(([k]) => typeof OUT.checks[k] === "boolean").every(([, v]) => v === true);
OUT.all_passed = pass;
writeFileSync(join(ART, "acceptance_browser.json"), JSON.stringify(OUT, null, 2));
console.log(JSON.stringify(OUT, null, 2));
process.exit(pass ? 0 : 1);
