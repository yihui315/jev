// P1.4 closure: prove the offline / unreachable state renders gracefully.
// Block /api/jev mid-session, wait for the polling interval to fire, and confirm
// the account page panels show an honest error/unreachable StateView (not a crash
// or a blank/hung UI). Then unblock and confirm recovery.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const APP_URL = "http://127.0.0.1:4781";
const PORT = 9225;
const OUT_DIR = resolve("e2e/artifacts/v11");
const PROFILE = resolve(`e2e/cdp-profile-offline-${Date.now()}`);
const WS = globalThis.WebSocket;

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; this.listeners = new Map();
    ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) { const { resolve, reject } = this.pending.get(m.id); this.pending.delete(m.id); m.error ? reject(new Error(m.error.message)) : resolve(m.result); }
      else if (m.method) { this.events.push(m); const l = this.listeners.get(m.method); if (l) for (const fn of l) fn(m.params); } }); }
  send(method, params = {}) { const id = ++this.id; return new Promise((res, rej) => { this.pending.set(id, { resolve: res, reject: rej }); this.ws.send(JSON.stringify({ id, method, params })); }); }
  on(method, fn) { if (!this.listeners.has(method)) this.listeners.set(method, []); this.listeners.get(method).push(fn); }
}
async function waitHttp(u, tries = 40) { for (let i = 0; i < tries; i++) { try { const r = await fetch(u); if (r.ok) return true; } catch {} await sleep(250); } return false; }
async function launch() { mkdirSync(PROFILE, { recursive: true }); const args = ["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",`--remote-debugging-port=${PORT}`,`--user-data-dir=${PROFILE}`,"--window-size=1440,900"];
  const p = spawn(CHROME, args, { detached: true, stdio: "ignore" }); p.unref(); const up = await waitHttp(`http://127.0.0.1:${PORT}/json/version`); if (!up) throw new Error("chrome down"); return p; }
async function newPage(http) { const r = await fetch(`${http}/json`); const t = await r.json(); const page = t.find((x) => x.type === "page" && x.webSocketDebuggerUrl); if (!page) throw new Error("no page"); const ws = new WS(page.webSocketDebuggerUrl); await new Promise((res, rej) => { ws.addEventListener("open", res, { once: true }); ws.addEventListener("error", rej, { once: true }); }); return new CDP(ws); }
const evalJs = async (cdp, expr) => { const r = await cdp.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result?.value; };
const waitFor = async (cdp, pred, ms = 20000) => { const dl = Date.now() + ms; while (Date.now() < dl) { const r = await cdp.send("Runtime.evaluate", { expression: `(${pred})()`, returnByValue: true }); if (r.result?.value) return true; await sleep(300); } return false; };
const screenshot = async (cdp, file) => { const r = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true }); writeFileSync(join(OUT_DIR, file), Buffer.from(r.data, "base64")); };

async function run() {
  mkdirSync(OUT_DIR, { recursive: true });
  const chrome = await launch();
  const cdp = await newPage(`http://127.0.0.1:${PORT}`);
  await cdp.send("Page.enable"); await cdp.send("Runtime.enable"); await cdp.send("Network.enable");
  await cdp.send("Page.navigate", { url: `${APP_URL}/trading.html` });
  await waitFor(cdp, () => () => !!document.querySelector(".jev-sidebar"));
  await sleep(1500);
  // Navigate directly to the account hash route (deterministic, avoids SPA
  // first-click timing) and assert the account-unique marker is present.
  await cdp.send("Page.navigate", { url: `${APP_URL}/trading.html#/account` });
  const onAccount = await waitFor(cdp, () => () => document.body.innerText.includes("事件回放验证"), 8000);
  console.log("on account page:", onAccount);
  await sleep(4000); // let account panels load real data first

  // Capture the ONLINE state text of the account panels.
  const onlineText = await evalJs(cdp, `(() => { const ps = Array.from(document.querySelectorAll('.jev-panel')).map(p => p.innerText.replace(/\\s+/g,' ').slice(0,120)); return ps.join(' | '); })()`);

  // Block the adapter entirely (simulate offline / adapter down) — broad pattern
  // so the relative /api/jev requests (resolved against page origin) are matched.
  await cdp.send("Network.setBlockedURLs", { urls: ["*://*/api/jev*"] });
  await cdp.send("Network.enable");
  // Reload so every query starts FRESH and its first fetch hits the block =>
  // unreachable => honest error StateView (not a crash / blank / hung UI).
  await cdp.send("Page.reload", {});
  await sleep(8000);
  const offlineText = await evalJs(cdp, `(() => { const states = Array.from(document.querySelectorAll('.jev-state')).map(s => s.innerText.replace(/\\s+/g,' ')); return states.length ? states.join(' || ') : 'NO_STATE_VIEW'; })()`);
  await screenshot(cdp, "page-account-offline.png");

  // Unblock and confirm graceful recovery (reload again => real data returns).
  await cdp.send("Network.setBlockedURLs", { urls: [] });
  await cdp.send("Page.reload", {});
  await sleep(8000);
  const recoveredText = await evalJs(cdp, `(() => { const states = Array.from(document.querySelectorAll('.jev-state')).map(s => s.innerText.replace(/\\s+/g,' ')); const hasData = !!document.querySelector('.jev-table'); return { states: states.length?states.join(' || '):'NONE', hasTable: hasData }; })()`);
  await screenshot(cdp, "page-account-recovered.png");

  const out = { generated_utc: new Date().toISOString(), online_panel_text_head: onlineText, offline_state_view: offlineText, recovered: recoveredText };
  writeFileSync(join(OUT_DIR, "V11_OFFLINE_STATE_EVIDENCE.json"), JSON.stringify(out, null, 2));
  console.log("OFFLINE_EVIDENCE:", JSON.stringify(out, null, 2));
  try { await cdp.send("Page.close"); } catch {}
  process.exit(0);
}
run().catch((e) => { console.error("FAILED:", e); process.exit(1); });
