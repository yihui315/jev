/**
 * Browser acceptance for the control-token path.
 *
 * What matters here is the WIRE, not the UI:
 *   1. POST /api/v1/providers/minimax/credentials actually leaves the browser.
 *   2. It carries an Authorization header with the Bearer scheme.
 *   3. The token VALUE is never printed — only its length and scheme.
 *   4. With a deliberately invalid provider key, the answer is a PROVIDER-layer
 *      result and never "Invalid or missing control token", which proves the JCC
 *      control gate was crossed.
 *   5. The status surface separates "token provided" from "server verified", and
 *      the second is produced by a real authenticated round trip.
 *
 * The token is read from a host file and typed into the field like a person
 * would. It is never written into this script, never logged, and never compared
 * by value — only by length and scheme.
 */
import { readFileSync } from "node:fs";

const CDP = "http://127.0.0.1:9222";
const APP = "http://127.0.0.1:4780";
const BAD_KEY = "sk-deliberately-invalid-key-for-e2e-0000";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const TOKEN = readFileSync(process.env.JEV_TOKEN_FILE ?? "", "utf8")
  .replace(/^﻿/, "").trim();

let ws, msgId = 0;
const pending = new Map();
const consoleErrors = [];
/** Control-plane requests seen by the page, with headers. */
const wire = [];

function send(m, p = {}) {
  const id = ++msgId;
  ws.send(JSON.stringify({ id, method: m, params: p }));
  return new Promise((res, rej) => {
    pending.set(id, { resolve: res, reject: rej });
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); rej(new Error("timeout " + m)); }
    }, 40000);
  });
}
async function ev(e) {
  const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result?.value;
}
const results = [];
function check(n, ok, d = "") {
  results.push({ n, ok, d });
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? " — " + d : ""));
}

/** Click a settings group by its exact label. */
const openGroup = (label) =>
  ev(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)})?.click()`);

/** Type into a field the way a person does, through the native value setter. */
const typeInto = (selector, value) =>
  ev(`(() => {
    const el=document.querySelector(${JSON.stringify(selector)});
    if(!el) return { notFound:true };
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    setter.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event('input',{bubbles:true}));
    return { typed:true, id: el.id||'(none)', type: el.type };
  })()`);

/** Open one provider's credential form, deterministically. */
const openProvider = (name) =>
  ev(`(() => {
    const rows=[...document.querySelectorAll('div.rounded-lg')].filter(
      d=>/${name}/.test(d.textContent||''));
    // The row that names THIS provider and no other one.
    const others={MiniMax:/Qwen|DeepSeek|TypeSafe|Custom/,Qwen:/MiniMax|DeepSeek|TypeSafe|Custom/,DeepSeek:/MiniMax|Qwen|TypeSafe|Custom/}[${JSON.stringify(name)}];
    const row=rows.find(d=>!others.test(d.textContent.replace(/${name}/g,''))) || rows[0];
    if(!row) return { ok:false, rows: rows.length };
    const btn=[...row.querySelectorAll('button')].find(b=>/配置|更换/.test(b.textContent.trim()));
    if(!btn) return { ok:false, reason:'no config button' };
    btn.click();
    return { ok:true };
  })()`);

async function main() {
  if (!TOKEN) {
    console.error("no token file / empty token — aborting rather than reporting a false pass");
    process.exit(2);
  }
  const tokenLen = TOKEN.length;

  const targets = await (await fetch(CDP + "/json/list")).json();
  const page = targets.find((t) => t.type === "page");
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", () => res(), { once: true });
    ws.addEventListener("error", () => rej(new Error("socket")), { once: true });
  });
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(typeof e.data === "string" ? e.data : String(e.data));
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      return;
    }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      consoleErrors.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (m.method === "Network.requestWillBeSent") {
      const u = m.params.request.url || "";
      if (u.includes("/api/v1/providers/")) {
        wire.push({
          method: m.params.request.method,
          url: u.replace(/^https?:\/\/[^/]+/, ""),
          headers: m.params.request.headers || {},
          status: undefined,
        });
      }
    }
    if (m.method === "Network.responseReceived") {
      const path = (m.params.response.url || "").replace(/^https?:\/\/[^/]+/, "");
      const hit = [...wire].reverse().find((w) => w.url === path && w.status === undefined);
      if (hit) hit.status = m.params.response.status;
    }
  });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");

  await send("Page.navigate", { url: APP });
  await sleep(1500);
  await ev("localStorage.clear(); sessionStorage.clear();");
  await send("Page.navigate", { url: APP });
  await sleep(4000);

  await ev(`[...document.querySelectorAll('nav button, nav[aria-label] button')]
    .find(b=>b.textContent.trim()==='设置')?.click()`);
  await sleep(1200);

  // ---- the status surface, before any token exists ----------------------
  await openGroup("高级");
  await sleep(1000);
  const before = await ev(`(() => {
    const b=document.body.innerText;
    return {
      hasBrowserLine: /浏览器令牌/.test(b),
      hasServerLine: /服务端授权/.test(b),
      claimsVerified: /服务端授权：已验证/.test(b),
      claimsSet: /已设置/.test(b),
    };
  })()`);
  check("状态区分「浏览器令牌」与「服务端授权」",
    before.hasBrowserLine && before.hasServerLine,
    "browser=" + before.hasBrowserLine + " server=" + before.hasServerLine);
  check("未输入令牌时不谎称已验证", before.claimsVerified === false);
  check("不再使用含糊的「已设置」", before.claimsSet === false);

  // ---- a write with NO token must still be issued -----------------------
  await openGroup("模型与 API");
  await sleep(1500);
  const openedNoTok = await openProvider("MiniMax");
  await sleep(900);
  wire.length = 0;
  const noToken = await ev(`(async () => {
    const inp=document.querySelector('input[id^="key-"]');
    if(!inp) return { noInput:true };
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    setter.call(inp, ${JSON.stringify(BAD_KEY)});
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    await new Promise(r=>setTimeout(r,300));
    const btn=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存并测试');
    if(!btn) return { noButton:true };
    if(btn.disabled) return { disabled:true };
    btn.click();
    await new Promise(r=>setTimeout(r,4000));
    const n=document.querySelector('[role="status"]');
    return { clicked:true, notice: n? n.textContent.replace(/\\s+/g,' ').trim().slice(0,160):'' };
  })()`);

  const noTokPost = wire.find((w) => w.method === "POST") || {};
  check("无令牌时请求仍然发出（不再静默 no-op）",
    noToken.clicked === true && !!noTokPost.method,
    "opened=" + JSON.stringify(openedNoTok) + " methods=" + wire.map((w) => w.method).join(","));
  check("无令牌时服务端拒绝且提示指向令牌",
    /控制令牌/.test(noToken.notice || ""),
    "notice=" + JSON.stringify((noToken.notice || "").slice(0, 80)));
  const noTokNames = Object.keys(noTokPost.headers || {}).map((h) => h.toLowerCase());
  check("无令牌时不发送 Authorization 头（真实缺失）",
    !noTokNames.includes("authorization") && !noTokNames.includes("x-jcc-token"),
    "headers=" + noTokNames.join(","));

  // ---- type the real control token -------------------------------------
  await openGroup("高级");
  await sleep(1000);
  const typed = await typeInto("#token", TOKEN);
  check("控制令牌可输入（单一 state · id=token）", typed.typed === true, JSON.stringify(typed));
  await sleep(500);

  const stored = await ev(`({ cookie: document.cookie.length, url: location.href.includes('token') })`);
  check("令牌未落入 cookie / URL",
    stored.cookie === 0 && stored.url === false,
    "cookie=" + stored.cookie + " urlHasToken=" + stored.url);

  // ---- 验证授权: a real authenticated round trip -----------------------
  wire.length = 0;
  await ev(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='验证授权')?.click()`);
  await sleep(3500);

  // A custom Authorization header forces a CORS preflight, so the first captured
  // entry is an OPTIONS carrying no credentials. Select the real request.
  const vPost = wire.find((w) => w.method === "POST") || wire[0] || {};
  const vHeaders = vPost.headers || {};
  const authHeader = vHeaders.Authorization || vHeaders.authorization || "";
  const vNames = Object.keys(vHeaders).map((h) => h.toLowerCase());

  check("验证请求确实发出", !!vPost.method, "methods=" + wire.map((w) => w.method).join(","));
  check("AUTH_HEADER_PRESENT = true", authHeader.length > 0, "present=" + (authHeader.length > 0));
  check("AUTH_SCHEME = Bearer", authHeader.split(" ")[0] === "Bearer",
    "scheme=" + (authHeader.split(" ")[0] || "(none)"));
  check("Authorization 长度正确（值本身不打印）",
    authHeader.replace(/^Bearer /, "").length === tokenLen,
    "sentLen=" + authHeader.replace(/^Bearer /, "").length + " expected=" + tokenLen);
  check("同时携带 X-JCC-Token", vNames.includes("x-jcc-token"), "present=" + vNames.includes("x-jcc-token"));
  check("验证请求越过鉴权闸门（非 401/403）",
    vPost.status !== 401 && vPost.status !== 403, "status=" + vPost.status);

  const afterVerify = await ev(`(() => {
    const b=document.body.innerText;
    return { verified: /服务端授权：已验证/.test(b), browser: /浏览器令牌：已提供/.test(b) };
  })()`);
  check("「服务端授权：已验证」来自真实往返", afterVerify.verified === true);
  check("「浏览器令牌：已提供」同时显示", afterVerify.browser === true);

  // ---- provider save WITH the token -----------------------------------
  await openGroup("模型与 API");
  await sleep(1500);
  const openedSave = await openProvider("MiniMax");
  await sleep(900);
  wire.length = 0;
  const saveRes = await ev(`(async () => {
    const inp=document.querySelector('input[id^="key-"]');
    if(!inp) return { noInput:true };
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    setter.call(inp, ${JSON.stringify(BAD_KEY)});
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    await new Promise(r=>setTimeout(r,300));
    const btn=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存并测试');
    if(!btn) return { noButton:true };
    btn.click();
    await new Promise(r=>setTimeout(r,6000));
    const n=document.querySelector('[role="status"]');
    return { notice: n? n.textContent.replace(/\\s+/g,' ').trim().slice(0,220):'' };
  })()`);

  const sPost = wire.find((w) => w.url.includes("/credentials")) || wire[0] || {};
  const sAuth = sPost.headers?.Authorization || sPost.headers?.authorization || "";
  check("保存请求携带 Bearer 头", sAuth.startsWith("Bearer "),
    "scheme=" + (sAuth.split(" ")[0] || "(none)") + " opened=" + JSON.stringify(openedSave));
  check("保存请求越过鉴权闸门（200）", sPost.status === 200, "status=" + sPost.status);
  check("保存未得到 control-auth 拒绝",
    !/Invalid or missing control|控制令牌未被服务端接受/.test(saveRes.notice || ""),
    "notice=" + JSON.stringify((saveRes.notice || "").slice(0, 100)));
  check("无效 Key 得到 Provider 层结论（未伪造成功）",
    /未就绪|无法连接|失败|未配置/.test(saveRes.notice || ""),
    "notice=" + JSON.stringify((saveRes.notice || "").slice(0, 110)));

  // ---- clean up the probe credential -----------------------------------
  await ev(`(async () => {
    const del=[...document.querySelectorAll('button')]
      .find(b=>/删除/.test(b.getAttribute('aria-label')||''));
    if(del) del.click();
    await new Promise(r=>setTimeout(r,2500));
  })()`);

  const leaked = await ev(`({
    dom: document.documentElement.outerHTML.includes(${JSON.stringify(BAD_KEY)}),
    ls: JSON.stringify(Object.entries(localStorage)).includes(${JSON.stringify(BAD_KEY)}),
    ss: JSON.stringify(Object.entries(sessionStorage)).includes(${JSON.stringify(BAD_KEY)}),
  })`);
  check("Provider Key 提交后不在 DOM 中", leaked.dom === false);
  check("Provider Key 不在 localStorage", leaked.ls === false);
  check("Provider Key 不在 sessionStorage", leaked.ss === false);

  check("console errors = 0", consoleErrors.length === 0, consoleErrors.slice(0, 2).join(" | "));

  const failed = results.filter((r) => !r.ok);
  console.log("\n=== " + (results.length - failed.length) + "/" + results.length + " checks passed ===");
  for (const f of failed) console.log("  FAILED: " + f.n + " " + f.d);
  ws.close();
  process.exit(failed.length === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error("verify failed:", e.message);
  try { ws.close(); } catch { /* ignore */ }
  process.exit(2);
});
