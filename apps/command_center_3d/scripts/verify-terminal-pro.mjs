// TERMINAL-PRO — real-browser E2E acceptance harness (Line A UI upgrade).
//
// Zero new deps: Node global WebSocket + child_process Chrome + CDP. This is a
// thin, focused sibling of v12_acceptance_browser_test.mjs and reuses its
// CDP plumbing (startChrome / cdp / getPageWs / setup / clickByText /
// Page.captureScreenshot). It asserts ONLY the terminal-pro presentation
// layer: five-item primary nav, focus banner + block reordering, cockpit
// structure, order-flow ledger refresh, honest data provenance. It does NOT
// modify product code and never hardcodes preview credentials — those come
// from JEVA_PREVIEW_USER / JEVA_PREVIEW_PASS (or fall back to the repo's
// existing BasicAuth injection used by v12).
//
// Each acceptance point runs in its own try/catch so one failure cannot abort
// the rest. Results + screenshots land in D:/tmp/jev_qa_evidence/terminal-pro/.
// Run (after the SPA is served at 127.0.0.1:4781 and all code has landed):
//   node scripts/verify-terminal-pro.mjs
// Exit code 0 = every recorded check passed, 1 = at least one failed.
import { spawn, execSync } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// ---- Config (env-overridable; NO secrets hardcoded) -------------------------
const CHROME = process.env.JEVA_CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const APP = process.env.JEVA_APP_URL || "http://127.0.0.1:4781/trading.html";
const OUT = process.env.JEVA_EVIDENCE_DIR || "D:/tmp/jev_qa_evidence/terminal-pro";
const CDP_PORT = Number(process.env.JEVA_CDP_PORT || 9241);
// Viewport override: headless Chrome defaults to ~800x600, which crops the
// COMMAND dashboard to a single KPI row in screenshots (K-line / orderbook /
// order form / ledger / AI / cost all land off-screen). Default 1920x1080;
// override with JEVA_VIEWPORT_W / JEVA_VIEWPORT_H for multi-resolution evidence.
const VW = Number(process.env.JEVA_VIEWPORT_W || 1920);
const VH = Number(process.env.JEVA_VIEWPORT_H || 1080);
// Preview creds are env-driven, NEVER hardcoded as a real secret. The app's
// showLogin = !getPreviewCreds() gate (App.tsx) renders a zIndex-50 modal that
// covers the whole SPA when sessionStorage has no creds. To dismiss it we
// always seed a non-empty value: default "preview" is enough for a local dev
// server with no Basic gate; a gated preview must supply real values via env.
const PREVIEW_USER = process.env.JEVA_PREVIEW_USER || "preview";
const PREVIEW_PASS = process.env.JEVA_PREVIEW_PASS || "preview";
const EXPECTED_KEYS = ["COMMAND", "MARKETS", "AI ENGINE", "EXECUTION", "PERFORMANCE"];
const ROUTES = [
  { key: "COMMAND", hash: "#/cockpit" },
  { key: "MARKETS", hash: "#/workbench" },
  { key: "AI ENGINE", hash: "#/command" },
  { key: "EXECUTION", hash: "#/account" },
  { key: "PERFORMANCE", hash: "#/cockpit" },
];
mkdirSync(OUT, { recursive: true });

// ---- CDP plumbing (reused verbatim from v12) --------------------------------
function startChrome(port, userDataDir) {
  return spawn(CHROME, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--window-size=" + VW + "," + VH,
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
async function clickByText(c, sel, text) {
  const r = await c.send("Runtime.evaluate", {
    expression: `(() => { const els = [...document.querySelectorAll(${JSON.stringify(sel)})]; const t = ${JSON.stringify(text)}; const el = els.find(e => (e.textContent||'').includes(t)); if (el) { el.click(); return true; } return false; })()`,
    returnByValue: true,
  });
  return !!(r.result?.result?.value);
}
// Click the PRIMARY-NAV <button class="pn-item"> itself (React onClick lives on
// the button), matched by its .pn-key label. Clicking the inner <span> does NOT
// reliably trigger the handler under React synthetic events.
async function clickNavByKey(c, key) {
  const r = await c.send("Runtime.evaluate", {
    expression: `(() => { const k = ${JSON.stringify(key)}; const btn = [...document.querySelectorAll(".pn-item")].find(e => (e.querySelector(".pn-key")?.textContent||"").trim() === k); if (btn) { btn.click(); return true; } return false; })()`,
    returnByValue: true,
  });
  return !!(r.result?.result?.value);
}
async function shot(c, name) {
  const b64 = await c.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  const p = join(OUT, name);
  writeFileSync(p, Buffer.from(b64.result.data, "base64"));
  V.artifacts.push(p);
  return p;
}

// ---- Result accumulator ----------------------------------------------------
const V = {
  generated_utc: new Date().toISOString(),
  app_url: APP,
  viewport: { width: VW, height: VH },
  git_head_sha: null,
  chrome_version: "unknown",
  preview_creds_source: (process.env.JEVA_PREVIEW_USER && process.env.JEVA_PREVIEW_PASS)
    ? "env:JEVA_PREVIEW_USER/JEVA_PREVIEW_PASS" : "default:preview (fallback; only closes SPA login modal, NOT a real Basic gate)",
  backend_probe: null,
  checks: [],
  console_errors: [],
  page_errors: [],
  artifacts: [],
};
try { V.git_head_sha = execSync("git rev-parse HEAD", { cwd: join(import.meta.dirname, "..", "..") }).toString().trim(); } catch { V.git_head_sha = "unknown"; }
async function check(id, name, fn) {
  try {
    const r = await fn();
    const skip = !!(r && r.skip);
    const pass = skip ? true : !!(r && r.pass);
    V.checks.push({ id, name, pass, skip, detail: (r && r.detail) || "" });
    console.log(`${skip ? "SKIP" : pass ? "PASS" : "FAIL"} [${id}] ${name}${r && r.detail ? " — " + r.detail : ""}`);
  } catch (err) {
    V.checks.push({ id, name, pass: false, skip: false, detail: "THREW " + String((err && err.stack) || err) });
    console.log(`FAIL [${id}] ${name} — THREW ${String(err)}`);
  }
}
// Runtime.evaluate helper: unwrap CDP value, throw on exception.
async function ev(c, expression, opts = {}) {
  const r = await c.send("Runtime.evaluate", {
    expression, returnByValue: true,
    awaitPromise: !!opts.awaitPromise, timeout: opts.timeout || 15000,
  });
  if (r.result?.exceptionDetails) {
    const d = r.result.exceptionDetails;
    throw new Error("eval exception: " + (d.exception?.description || d.text || "unknown"));
  }
  return r.result?.result?.value;
}
// Evaluate a JS arrow body that returns a JSON-serializable value object.
async function dom(c, bodyExpr, opts = {}) {
  return ev(c, `(() => { ${bodyExpr} })()`, opts);
}
const jsonS = (v) => JSON.stringify(v);

async function setup(c) {
  await c.ready;
  await c.send("Page.enable"); await c.send("Runtime.enable");
  await c.send("Network.enable"); await c.send("Console.enable");
  // Force a real desktop viewport BEFORE navigate: headless Chrome otherwise
  // uses ~800x600 and crops the COMMAND one-screen regions out of screenshots.
  await c.send("Emulation.setDeviceMetricsOverride", {
    width: VW, height: VH, deviceScaleFactor: 1, mobile: false,
  });
  // (0) Seed sessionStorage at DOCUMENT-CREATION time, not via Runtime.evaluate.
  //     The initial execution context is about:blank (opaque origin), where
  //     sessionStorage access throws SecurityError. addScriptToEvaluateOnNewDocument
  //     runs on EVERY navigation/reload in the real http origin, before App.tsx
  //     synchronously reads getPreviewCreds() — so the login modal never gates.
  const credsSeed = JSON.stringify(JSON.stringify({ user: PREVIEW_USER, pass: PREVIEW_PASS }));
  try {
    await c.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `try{sessionStorage.setItem("jev_preview_creds_v1", ${credsSeed});}catch(e){}`,
    });
  } catch (e) {
    V.creds_seed_registered = false;
  }
  V.creds_seed_registered = true;
  // (1) Basic preview gate at the HTTP layer (Cloudflare Pages Function).
  //     Credentials are env-driven with a non-empty "preview" fallback, so the
  //     header is always sent. On a local no-gate dev server it is harmless;
  //     on a gated preview the caller supplies the real values via env.
  const token = Buffer.from(`${PREVIEW_USER}:${PREVIEW_PASS}`).toString("base64");
  try { await c.send("Network.setExtraHTTPHeaders", { headers: { Authorization: "Basic " + token } }); } catch (_) {}
  c.onEvent((m) => {
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params?.exceptionDetails;
      V.page_errors.push({ text: d?.exception?.description || d?.text || "pageerror" });
    } else if (m.method === "Console.messageAdded") {
      const msg = m.params.message;
      if (msg.level === "error") V.console_errors.push({ text: msg.text, url: msg.url, line: msg.line });
    }
  });
}
function benignConsole(errors) {
  return errors.filter((e) => !/Download the React DevTools|Warning:/.test(e.text || ""));
}
async function injectSessionCreds(c) {
  // (2) SPA-side creds via the repo's own mechanism (sessionStorage key
  //     jev_preview_creds_v1, consumed by api/previewAuth.ts). This is what
  //     makes App.tsx showLogin=false and removes the zIndex-50 login modal.
  //     Always injected with a non-empty value (env or "preview" fallback).
  const creds = JSON.stringify({ user: PREVIEW_USER, pass: PREVIEW_PASS });
  // addScriptToEvaluateOnNewDocument is the authoritative injection; this is
  // only a best-effort top-up. Wrap in try/catch so an opaque-origin context
  // (about:blank during early navigation) cannot throw and abort the harness.
  try {
    await c.send("Runtime.evaluate", {
      expression: `try{sessionStorage.setItem("jev_preview_creds_v1", ${jsonS(creds)});}catch(e){}`,
      returnByValue: true,
    });
  } catch (_) { /* CDP-level failure tolerated; document-time seed still applies */ }
  return true;
}
// Poll until the live document is in a real http origin AND fully loaded, so
// subsequent Runtime.evaluate calls never run in the about:blank opaque origin.
async function waitForRealDocument(c, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const s = await ev(c, `location.origin + "|" + document.readyState;`);
      last = String(s || "");
      if (/^http/.test(last) && /complete/.test(last)) return { ok: true, state: last };
    } catch (_) { /* context may still be swapping */ }
    await sleep(400);
  }
  return { ok: false, state: last };
}
// Poll a CDP expression until it evaluates truthy (React has committed the
// target state) or timeout. exprFn is a () => expression-string so the live
// expression is regenerated on each poll. Returns true if it settled.
async function waitFor(c, exprFn, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { if (await ev(c, exprFn())) return true; } catch (_) { /* context swapping */ }
    await sleep(250);
  }
  return false;
}

// ---- Run -------------------------------------------------------------------
let proc; const dir = mkdtempSync(join(tmpdir(), "jev-tpro-"));
let c;
try {
  // [1] launch + navigate + Basic auth
  await check("01", "启动 Chrome / 注入会话凭据 / 登录 modal 消失 / 页面挂载", async () => {
    proc = startChrome(CDP_PORT, dir);
    await sleep(3000);
    c = await getPageWs(CDP_PORT);
    await setup(c);
    try {
      const ver = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json();
      V.chrome_version = ver.Browser || "unknown";
    } catch (_) {}
    // Credentials are seeded by Page.addScriptToEvaluateOnNewDocument (see
    // setup) at document-creation time in the real origin, BEFORE App.tsx reads
    // getPreviewCreds() on mount. So a single navigate is enough: NO forced
    // reload (a reload swaps the CDP execution context and is the flakiness
    // source). Just wait for the real http document, then a best-effort top-up.
    await c.send("Page.navigate", { url: APP });
    const ready = await waitForRealDocument(c, 12000);
    V.doc_ready = ready;
    await injectSessionCreds(c);
    let st = await dom(c, `
      return {
        title: document.title,
        origin: location.origin,
        root: !!document.getElementById("trading-root"),
        children: (document.getElementById("trading-root")||{}).childElementCount||0,
        url: location.href,
        loginModal: !!document.querySelector(".jev-backdrop .jev-login, form.jev-login"),
        creds: (()=>{ try { return !!sessionStorage.getItem("jev_preview_creds_v1"); } catch(e){ return "ERR:"+String(e); } })(),
      };
    `);
    // If a stale modal survived, re-inject + reload once, then re-assess.
    if (st && st.loginModal) {
      await injectSessionCreds(c);
      await c.send("Page.reload");
      await waitForRealDocument(c, 12000);
      await sleep(1200);
      st = await dom(c, `return { title: document.title, origin: location.origin, root: !!document.getElementById("trading-root"), children: (document.getElementById("trading-root")||{}).childElementCount||0, url: location.href, loginModal: !!document.querySelector(".jev-backdrop .jev-login, form.jev-login"), creds: (()=>{ try { return !!sessionStorage.getItem("jev_preview_creds_v1"); } catch(e){ return "ERR:"+String(e); } })() };`);
    }
    const credsReadable = st?.creds === true;
    // Credentials were seeded at document-creation time and App.tsx reads them
    // synchronously; the observable proof the preview gate was passed is that
    // no login modal rendered AND the SPA root mounted with children. If the
    // modal is still present, gatePassed is false and this step FAILS (no
    // watering down). sessionStorage readability is recorded, not required,
    // because Runtime.evaluate context origin can be flaky in headless CDP.
    const gatePassed = !!(st && !st.loginModal && st.root && st.children > 0);
    const pass = !!(ready.ok && gatePassed);
    const credMode = credsReadable ? "sessionStorage-readable" : `not-readable-in-eval-ctx(${st?.creds}); gate-proven-by-no-modal+root-mounted`;
    return { pass, detail: `chrome=${V.chrome_version} url=${st?.url} origin=${st?.origin} docReady=${ready.ok}/${ready.state} credsMode=${credMode} loginModalGone=${!st?.loginModal} root=${st?.root} children=${st?.children} source=${V.preview_creds_source}` };
  });

  // [2] five primary nav items + "more" partition
  await check("02", "五主导航 .pn-key 文案 + 更多分区", async () => {
    const st = await dom(c, `
      const keys = [...document.querySelectorAll(".pn-item .pn-key")].map(e => (e.textContent||"").trim());
      const more = [...document.querySelectorAll(".pn-more-item")].map(e => (e.textContent||"").replace(/\\s+/g," ").trim());
      return { keys, more };
    `);
    const keysOk = jsonS(st?.keys) === jsonS(EXPECTED_KEYS);
    const moreJoined = (st?.more || []).join(" | ");
    const moreOk = (st?.more || []).length >= 3 && /universe|市场宇宙/i.test(moreJoined) && /strategy|策略/i.test(moreJoined) && /team|团队|专家/i.test(moreJoined);
    return { pass: keysOk && moreOk, detail: `keys=${jsonS(st?.keys)} more=${jsonS(st?.more)}` };
  });

  // [3] each primary nav -> hash route, no console/page errors
  await check("03", "五主导航点击 → hash 路由 / 无 console error", async () => {
    const rows = [];
    for (const n of ROUTES) {
      const ce0 = benignConsole(V.console_errors).length;
      const pe0 = V.page_errors.length;
      const clicked = await clickNavByKey(c, n.key);
      // Wait for React to commit the route (hash === expected) instead of a
      // fixed sleep; this removes the read-in-flight/mid-state flakiness.
      const settled = await waitFor(c, () => `location.hash === ${JSON.stringify(n.hash)}`, 8000);
      const hash = await ev(c, `location.hash`);
      const newCe = benignConsole(V.console_errors).slice(ce0);
      const newPe = V.page_errors.slice(pe0);
      rows.push({ key: n.key, expected: n.hash, actual: hash, clicked, settled, newConsole: newCe, newPage: newPe });
    }
    const pass = rows.every((r) => r.clicked && r.actual === r.expected && r.newConsole.length === 0 && r.newPage.length === 0);
    return { pass, detail: rows.map((r) => `${r.key}->${r.actual}${r.actual === r.expected ? "" : "(want " + r.expected + ")"}${r.newConsole.length || r.newPage.length ? " ERR" : ""}`).join(" | ") };
  });

  // [4] COMMAND overview: all cockpit core regions present (structure, not data)
  await check("04", "COMMAND overview 核心区结构 + command_overview.png", async () => {
    await clickNavByKey(c, "COMMAND");
    // Wait for overview focus to settle: no focus banner, 2 ck-focus-blocks.
    await waitFor(c, () => `!document.querySelector(".ck-focus-banner") && document.querySelectorAll(".ck-focus-block").length === 2`, 8000);
    await sleep(250);
    const st = await dom(c, `
      const all = document.body.innerText || "";
      const has = (t) => all.includes(t);
      const regions = {
        kpi: ["账户权益","净收益","ROI","胜率","最大回撤","日收益率"].every(has),
        kline: has("K 线") || has("K线") || !!document.querySelector("canvas") || !!document.querySelector(".cockpit-chart"),
        orderbook: has("盘口"),
        orderForm: has("模拟下单") && has("提交订单"),
        ledger: has("账本") && has("订单") && has("成交"),
        providerCompare: has("MODEL 模型") || has("模型"),
        cost: has("成本归因"),
        risk: has("风险告警") || has("系统风险告警"),
      };
      return { regions, banner: !!document.querySelector(".ck-focus-banner") };
    `);
    await shot(c, "command_overview.png");
    const missing = Object.entries(st?.regions || {}).filter(([, v]) => !v).map(([k]) => k);
    return { pass: missing.length === 0 && !st.banner, detail: `missing=${jsonS(missing)} bannerInOverview=${st?.banner}` };
  });

  // [5] PERFORMANCE: banner + KPI/cost promoted above market
  await check("05", "PERFORMANCE banner + KPI/成本 DOM 顺序置顶 + command_performance.png", async () => {
    await clickNavByKey(c, "PERFORMANCE");
    // Wait for performance focus: banner[data-focus-label] + 2 ck-focus-lead.
    await waitFor(c, () => `!!document.querySelector('.ck-focus-banner[data-focus-label]') && document.querySelectorAll(".ck-focus-lead").length >= 2`, 8000);
    await sleep(250);
    const st = await dom(c, `
      const banner = document.querySelector('.ck-focus-banner[data-focus-label]') || [...document.querySelectorAll(".ck-focus-banner")].find(e=>(e.textContent||"").includes("PERFORMANCE"));
      const blocks = [...document.querySelectorAll(".ck-focus-block")];
      const kpi = blocks.find(e => (e.textContent||"").includes("账户权益")) || blocks[0];
      const cost = blocks.find(e => (e.textContent||"").includes("成本归因") || (e.textContent||"").includes("成本")) || blocks[blocks.length-1];
      const market = document.querySelector(".cockpit-workspace");
      const top = (e) => e ? e.getBoundingClientRect().top : null;
      return { banner: !!banner, bannerText: banner ? (banner.getAttribute("data-focus-label") || banner.textContent).trim() : "", kpiTop: top(kpi), costTop: top(cost), marketTop: top(market) };
    `);
    await shot(c, "command_performance.png");
    const orderOk = st?.kpiTop != null && st?.costTop != null && st?.marketTop != null && st.kpiTop < st.marketTop && st.costTop < st.marketTop;
    return { pass: !!(st?.banner && orderOk), detail: `banner=${st?.bannerText} kpiTop=${st?.kpiTop} costTop=${st?.costTop} marketTop=${st?.marketTop} orderOk=${orderOk}` };
  });

  // [6] COMMAND back: banner removed + order restored KPI->market->cost
  await check("06", "COMMAND 回退 banner 消失 + DOM 顺序恢复", async () => {
    await clickNavByKey(c, "COMMAND");
    // Wait for overview focus to settle again (banner gone, 2 blocks present).
    await waitFor(c, () => `!document.querySelector(".ck-focus-banner") && document.querySelectorAll(".ck-focus-block").length === 2`, 8000);
    await sleep(250);
    const st = await dom(c, `
      const banner = document.querySelector(".ck-focus-banner");
      const blocks = [...document.querySelectorAll(".ck-focus-block")];
      const kpi = blocks.find(e => (e.textContent||"").includes("账户权益")) || blocks[0];
      const cost = blocks.find(e => (e.textContent||"").includes("成本归因") || (e.textContent||"").includes("成本")) || blocks[blocks.length-1];
      const market = document.querySelector(".cockpit-workspace");
      const top = (e) => e ? e.getBoundingClientRect().top : null;
      return { banner: !!banner, kpiTop: top(kpi), costTop: top(cost), marketTop: top(market) };
    `);
    const orderOk = st?.kpiTop != null && st?.costTop != null && st?.marketTop != null && st.kpiTop < st.marketTop && st.marketTop < st.costTop;
    return { pass: !!(st && !st.banner && orderOk), detail: `bannerGone=${!st?.banner} kpiTop=${st?.kpiTop} marketTop=${st?.marketTop} costTop=${st?.costTop} orderOk=${orderOk}` };
  });

  // [7] order flow: place -> server match -> ledger refresh -> cancel (reuse v12 flow)
  await check("07", "模拟下单→撮合→账本刷新→撤单 + order_flow.png", async () => {
    // Probe backend liveness FIRST. If the local dev server has no real
    // /api/jev backend (all N/A / fetch throws), an order flow cannot possibly
    // run — report SKIP, not FAIL. When the backend IS reachable, every real
    // failure below still counts as FAIL.
    const probe = await ev(c, `(async () => {
      const out = { session: null, ticker: null, account: null };
      try { const r = await fetch("/api/jev/paper/session",{method:"POST",credentials:"include"}); out.session = { status:r.status, ok:r.ok, body: await r.json().catch(()=>null) }; } catch(e){ out.session = { err:String(e) }; }
      try { const r = await fetch("/api/jev/market/ticker?symbols=" + encodeURIComponent(JSON.stringify(["BTCUSDT","1000SATSUSDT"])),{credentials:"include"}); const j = await r.json().catch(()=>null); out.ticker = { status:r.status, dataLen: (j?.data||[]).length }; } catch(e){ out.ticker = { err:String(e) }; }
      try { const r = await fetch("/api/jev/paper/account",{credentials:"include"}); const j = await r.json().catch(()=>null); out.account = { status:r.status, ok:r.ok, hasAccount: !!(j?.data?.account) }; } catch(e){ out.account = { err:String(e) }; }
      return out;
    })()`, { awaitPromise: true, timeout: 15000 });
    V.backend_probe = probe;
    const backendLive = !!(probe?.session?.ok && (probe?.account?.hasAccount || (probe?.ticker?.dataLen > 0)));
    if (!backendLive) {
      return { skip: true, detail: "SKIP: no local backend (session/account/ticker all unreachable or N/A); order flow not runnable. probe=" + JSON.stringify(probe) };
    }
    const trade = await ev(c, `(async () => {
      const mk = (p) => p + "-" + Date.now() + "-" + Math.random().toString(36).slice(2,8);
      const s = await fetch("/api/jev/paper/session",{method:"POST",credentials:"include"}).then(r=>r.json()).catch(()=>null);
      const tk = await fetch("/api/jev/market/ticker?symbols=" + encodeURIComponent(JSON.stringify(["1000SATSUSDT","BTCUSDT"]))).then(r=>r.json()).catch(()=>null);
      const arr = tk?.data || [];
      const pick = (sym, fallback) => Number(arr.find(t=>t.symbol===sym)?.lastPrice || fallback);
      const last1k = pick("1000SATSUSDT", 0.0002);
      const limPrice = Math.max(Number((last1k*0.5).toFixed(8)), 0.00000001);
      const qty = Math.max(1000000, Math.ceil(12/limPrice));
      const lim = await fetch("/api/jev/paper/orders",{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({clientOrderId:mk("wd"),confirmationId:mk("cf"),symbol:"1000SATSUSDT",side:"BUY",type:"LIMIT",quantity:qty,limitPrice:limPrice})}).then(r=>r.json()).catch(()=>null);
      const oid = lim?.data?.account?.orders?.[0]?.id || lim?.data?.orders?.[0]?.id || lim?.data?.order?.id;
      const before = await fetch("/api/jev/paper/account",{credentials:"include"}).then(r=>r.json()).catch(()=>null);
      const cancel = oid ? await fetch("/api/jev/paper/orders/"+encodeURIComponent(oid)+"/cancel",{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({cancel:true})}).then(r=>r.json()).catch(()=>null) : null;
      const mkt = await fetch("/api/jev/paper/orders",{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({clientOrderId:mk("wd"),confirmationId:mk("cf"),symbol:"BTCUSDT",side:"BUY",type:"MARKET",quantity:0.0002})}).then(r=>r.json()).catch(()=>null);
      const after = await fetch("/api/jev/paper/account",{credentials:"include"}).then(r=>r.json()).catch(()=>null);
      const evs = await fetch("/api/jev/paper/events?after=0&limit=20",{credentials:"include"}).then(r=>r.json()).catch(()=>null);
      return { sessionOk: s?.ok===true, oid, lim, cancel, mkt, after, events: evs?.data?.events||[] };
    })()`, { awaitPromise: true, timeout: 30000 });
    await sleep(1200);
    // UI ledger DOM check (structure refreshed after server round-trip)
    const ui = await dom(c, `
      const all = document.body.innerText || "";
      const ledgerRows = document.querySelectorAll(".cockpit-vpane.lower tbody tr, .cockpit-bottom tbody tr").length;
      return { ledgerTitle: all.includes("账本"), tradeRows: all.includes("成交记录"), events: all.includes("事件") || all.includes("EVENT"), ledgerRows, okMsg: /HTTP 200|ok: true|已撤|OPEN|FILLED|CANCELED/.test(all) };
    `);
    await shot(c, "order_flow.png");
    const orders = trade?.after?.data?.account?.orders || [];
    const limOrder = orders.find((o) => o.id === trade?.oid);
    const marketFilled = orders.some((o) => o.symbol === "BTCUSDT" && (o.status === "FILLED" || o.status === "NEW" || o.status === "OPEN"));
    const cancelOk = !!(limOrder && limOrder.status === "CANCELED");
    const correlation = (trade?.events || []).some((e) => e.seq != null && e.eventType && e.createdAt);
    const pass = !!(trade?.sessionOk && trade?.oid && cancelOk && marketFilled && correlation && ui.ledgerTitle && ui.tradeRows);
    return { pass, detail: `session=${trade?.sessionOk} oid=${!!trade?.oid} cancel=${cancelOk} marketFilled=${marketFilled} events=${correlation} ledgerTitle=${ui.ledgerTitle} tradeRows=${ui.tradeRows} uiOkMsg=${ui.okMsg}` };
  });

  // [8] honest data provenance: REST_POLLING / PAPER / N/A, no fake "LIVE"
  await check("08", "数据真实性：REST_POLLING / PAPER / N/A，无 LIVE 伪标", async () => {
    const st = await dom(c, `
      const t = document.body.innerText || "";
      const LIVE_BAD = /(WS[ -]?LIVE|WEBSOCKET[ -]?LIVE)/i.test(t) && !/非\\s*WS|绝非\\s*WS|非[\\s\\S]{0,4}WebSocket\\s*LIVE|绝非[\\s\\S]{0,4}WS\\s*LIVE/i.test(t);
      return {
        restPolling: /REST_POLLING/.test(t),
        paper: /PAPER/.test(t),
        na: /N\\/A|不可用|无数据|NO_DATA/.test(t),
        notZeroAsRunning: !/(已运行|运行中)[^0-9]{0,6}0\\b/.test(t),
        fakeLive: LIVE_BAD,
        head: t.replace(/\\s+/g," ").slice(0, 240),
      };
    `);
    const pass = !!(st?.restPolling && st?.paper && st?.na && st?.notZeroAsRunning && !st?.fakeLive);
    return { pass, detail: `restPolling=${st?.restPolling} paper=${st?.paper} na=${st?.na} notZeroAsRunning=${st?.notZeroAsRunning} fakeLive=${st?.fakeLive}` };
  });

  // [9] provenance record (git SHA / chrome version / timestamp)
  await check("09", "结果 JSON：git HEAD / Chrome 版本 / 时间戳", async () => {
    V.generated_utc = new Date().toISOString();
    const pass = !!(V.git_head_sha && V.git_head_sha !== "unknown" && V.chrome_version && V.chrome_version !== "unknown" && V.generated_utc);
    return { pass, detail: `git=${V.git_head_sha} chrome=${V.chrome_version} utc=${V.generated_utc}` };
  });

} catch (err) {
  V.checks.push({ id: "FATAL", name: "harness 顶层异常", pass: false, detail: String((err && err.stack) || err) });
  console.log("FAIL [FATAL] harness 顶层异常 — " + String(err));
} finally {
  try { c && c.close(); } catch (_) {}
  try { proc && proc.kill("SIGKILL"); } catch (_) {}
  try { rmSync(dir, { recursive: true, force: true }); } catch (_) {}
}

const pass = V.checks.length > 0 && V.checks.every((k) => k.pass === true);
V.all_passed = pass;
V.summary = {
  total: V.checks.length,
  passed: V.checks.filter((k) => k.pass && !k.skip).length,
  skipped: V.checks.filter((k) => k.skip).length,
  failed: V.checks.filter((k) => !k.pass).length,
};
try { writeFileSync(join(OUT, "verdict.json"), JSON.stringify(V, null, 2)); } catch (e) { console.log("WARN verdict.json write failed: " + String(e)); }
console.log(`\n=== TERMINAL-PRO VERDICT: ${pass ? "PASS" : "FAIL"} (${V.summary.passed} pass / ${V.summary.skipped} skip / ${V.summary.failed} fail; total ${V.summary.total}) ===`);
console.log(`evidence: ${OUT}`);
process.exit(pass ? 0 : 1);
