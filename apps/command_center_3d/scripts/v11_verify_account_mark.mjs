// Focused verification (P0.1 closure): launch a fresh persistent Chrome, place a
// real MARKET paper order for 1000SATS (this establishes the session + position,
// exactly like the production order flow), then navigate to the account page in
// the SAME session, wait until the 持仓 (positions) table has rendered rows, and
// read the EXACT rendered text of the 现价 (mark) cell + re-screenshot. This
// removes any Vision-ambiguity about whether the tiny price renders precisely.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const APP_URL = "http://127.0.0.1:4781";
const PORT = 9224;
const OUT_DIR = resolve("e2e/artifacts/v11");
const PROFILE = resolve(`e2e/cdp-profile-mark-${Date.now()}`);
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
  await cdp.send("Page.enable"); await cdp.send("Runtime.enable");
  await cdp.send("Page.navigate", { url: `${APP_URL}/trading.html` });
  await waitFor(cdp, () => () => !!document.querySelector(".jev-sidebar"));
  await sleep(1500);
  // Establish session + position: go to workbench, switch to 1000SATS, place MARKET BUY.
  await evalJs(cdp, `(() => { const b = Array.from(document.querySelectorAll('.jev-nav-item')).find(x => x.textContent.includes('专业交易工作台')); if (b) b.click(); })()`);
  await waitFor(cdp, () => () => !!document.querySelector("input.jev-input"), 20000);
  await sleep(2000);
  await evalJs(cdp, `(() => { const btn = Array.from(document.querySelectorAll('.jev-seg button')).find(b => b.textContent.trim() === '1000SATS'); if (btn) btn.click(); })()`);
  await sleep(2000);
  // Set qty via focus + CDP Input.insertText (robust React onChange trigger).
  await evalJs(cdp, `(() => { const inp = document.querySelector('input.jev-input'); if (inp) inp.focus(); return !!inp; })()`);
  await cdp.send("Input.insertText", { text: "1000000" });
  await sleep(400);
  const qtyValue = await evalJs(cdp, `document.querySelector('input.jev-input').value`);
  console.log("qty input value:", qtyValue);
  await evalJs(cdp, `(() => { const b = document.querySelector('button.jev-btn.buy'); if (b) b.click(); })()`);
  await sleep(3000);

  // Navigate to account page in the SAME session.
  await evalJs(cdp, `(() => { const b = Array.from(document.querySelectorAll('.jev-nav-item')).find(x => x.textContent.includes('模拟交易账户')); if (b) b.click(); })()`);
  await sleep(2000);
  // Wait until the 持仓 table has rendered rows with the 现价 column.
  await waitFor(cdp, () => () => { const tbls = document.querySelectorAll('.jev-table'); return Array.from(tbls).some(t => /现价/.test(t.innerText) && t.querySelectorAll('tbody tr').length > 0); }, 25000);
  await sleep(2000); // let interval poll refresh
  const holding = await evalJs(cdp, `(() => {
    const tbls = Array.from(document.querySelectorAll('.jev-table'));
    const t = tbls.find(x => /现价/.test(x.innerText));
    if (!t) return { found: false };
    const headers = Array.from(t.querySelectorAll('thead th')).map(h => h.innerText.trim());
    const rows = Array.from(t.querySelectorAll('tbody tr')).map(r => Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim()));
    return { found: true, headers, rows };
  })()`);
  await screenshot(cdp, "page-account-mark.png");
  const srv = await evalJs(cdp, `fetch('/api/jev/paper/account').then(r=>r.json()).then(d=>{ const acc=(d&&d.data&&d.data.account)||null; const p=acc&&acc.positions?acc.positions['1000SATSUSDT']:null; return { account_present: !!acc, position_1000SATS: p?{qty:p.qty,avgPrice:p.avgPrice,mark:p.mark}:null }; })`);
  const out = { generated_utc: new Date().toISOString(), holding_table: holding, server_account_probe: srv };
  writeFileSync(join(OUT_DIR, "V11_ACCOUNT_MARK_VERIFY.json"), JSON.stringify(out, null, 2));
  console.log("MARK_VERIFY:", JSON.stringify(out, null, 2));
  try { await cdp.send("Page.close"); } catch {}
  process.exit(0);
}
run().catch((e) => { console.error("FAILED:", e); process.exit(1); });
