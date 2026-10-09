/**
 * Real browser mission E2E — the operator's own path, with JEV_API_KEY UNSET.
 *
 * The defect: the settings page showed 「已连接」 (reading the DPAPI store) while
 * the mission path answered "JEV_API_KEY is not set" (reading the environment).
 * Two credential sources, two truths. An API-level test cannot prove this is
 * fixed, because the whole point is which source the PAGE used. So this drives
 * the buttons.
 *
 * The control token is entered through the real settings field, because it is
 * deliberately never persisted to localStorage — there is nothing to inject, and
 * faking it would test a different product than the one shipped.
 *
 * The token VALUE is read from the user-scoped environment (where the launcher
 * reads it from) and never written to disk, logged, or printed.
 */
const CDP = "http://127.0.0.1:9226";
const APP = "http://127.0.0.1:4780";
const TASK = "这是一次 Jev + MiniMax 联合测试。\n最终只输出：JEV + MiniMax live test passed.";

/**
 * The control token, handed in through the environment by the launcher.
 *
 * It used to be read by shelling out to PowerShell from here, which this
 * environment blocks — and rightly so. The launcher already has the value from
 * the user scope, where the server reads it from, so the environment carries the
 * same value rather than the script reaching for it. It is never written to
 * disk, printed, or logged.
 */
function userScopedToken() {
  return (process.env.JCC_CONTROL_TOKEN || "").replace(/^\uFEFF/, "").trim();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws, id = 0;
const pend = new Map();
const consoleErrors = [];
const apiCalls = [];

function send(m, p = {}) {
  const n = ++id;
  ws.send(JSON.stringify({ id: n, method: m, params: p }));
  return new Promise((res, rej) => {
    pend.set(n, { res, rej });
    setTimeout(() => { if (pend.has(n)) { pend.delete(n); rej(new Error("timeout " + m)); } }, 40000);
  });
}
async function ev(e) {
  try {
    const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) return { __err: r.exceptionDetails.text };
    return r.result && r.result.value;
  } catch (err) {
    console.log("  [eval failed] " + e.slice(0, 80).replace(/\n/g, " ") + " -> " + err.message);
    return { __err: err.message };
  }
}

const results = [];
function check(n, ok, d = "") {
  results.push({ n, ok: ok === true });
  console.log((ok === true ? "PASS  " : "FAIL  ") + n + (d ? "  — " + d : ""));
}
const has = (needle) => ev(`document.body.innerText.includes(${JSON.stringify(needle)})`);
const clickButton = (label) => ev(`(() => {
  const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === ${JSON.stringify(label)});
  if (!b) return 'no-button';
  if (b.disabled) return 'disabled';
  b.click();
  return 'clicked';
})()`);

const t = await (await fetch(CDP + "/json/list")).json();
ws = new WebSocket(t.find((x) => x.type === "page").webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.addEventListener("open", () => res(), { once: true });
  ws.addEventListener("error", () => rej(new Error("sock")), { once: true });
});
ws.addEventListener("message", (e) => {
  const m = JSON.parse(typeof e.data === "string" ? e.data : String(e.data));
  if (m.id && pend.has(m.id)) { const p = pend.get(m.id); pend.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); return; }
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
    consoleErrors.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
  }
  if (m.method === "Network.responseReceived") {
    const u = m.params.response.url || "";
    if (u.includes("/api/")) apiCalls.push({ path: u.replace(/^https?:\/\/[^/]+/, ""), status: m.params.response.status });
  }
});
await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");

const token = userScopedToken();
if (!token) {
  console.log("JCC_CONTROL_TOKEN is not set in the user environment; cannot run the UI flow.");
  process.exit(2);
}

await send("Page.navigate", { url: APP });
await sleep(5000);
check("首页已加载", (await ev("!!document.querySelector('#root') && document.body.innerText.length > 50")) === true);

// --- settings: the control token, entered the way an operator enters it ---- //
await clickButton("设置");
await sleep(1500);
await clickButton("安全");
await sleep(2500);
const secState = await ev("!!document.querySelector(\"#token\") ? \'token-present\' : (document.body.innerText.includes(\'控制令牌\') ? \'section-shown-no-field\' : \'section-not-shown\')");
if (secState !== "token-present") console.log("  [nav] 安全 -> " + secState);
const put = await ev(`(() => {
  const el = document.querySelector('#token');
  if (!el) return 'no-field';
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, ${JSON.stringify(token)});
  el.dispatchEvent(new Event('input', { bubbles: true }));
  return 'typed';
})()`);
check("在设置页输入控制令牌", put === "typed", "state=" + put);
await sleep(800);

// --- the settings page must show the Jev card as connected ---------------- //
await clickButton("模型与 API");
await sleep(3000);
const cards = await ev(`(() => {
  const spans = [...document.querySelectorAll('span')].map(s => s.textContent.trim());
  const names = spans.filter(t => /^(TypeSafe Jev|MiniMax M3|Qwen Coding Plan|DeepSeek|Custom)/.test(t));
  const states = spans.filter(t => /^(已连接|上次验证通过|已配置 · 待验证|需要 API Key|API Key 无效|已限流|无法连接)$/.test(t));
  return names.map((n, i) => ({ name: n, state: states[i] ?? '' }));
})()`);
check("设置页读到 5 张卡", Array.isArray(cards) && cards.length === 5, JSON.stringify(cards));
const jevCard = (cards || []).find((c) => c.name.startsWith("TypeSafe"));
check("TypeSafe Jev 卡片 = 已连接", jevCard?.state === "已连接", "state=" + jevCard?.state);

// --- home: type the task and press 开始执行 -------------------------------- //
await clickButton("首页");
await sleep(2000);
const typed = await ev(`(() => {
  const box = document.querySelector('textarea') || document.querySelector('input[type=text]');
  if (!box) return 'no-box';
  const proto = box.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(box, ${JSON.stringify(TASK)});
  box.dispatchEvent(new Event('input', { bubbles: true }));
  return 'typed';
})()`);
check("任务已填入", typed === "typed", "state=" + typed);

apiCalls.length = 0;
const clicked = await clickButton("开始执行");
check("点击「开始执行」", clicked === "clicked", "state=" + clicked);

// A real Jev decision (~1.3s) plus a real MiniMax call (~2.8s). Heartbeats in
// between are not progress, so the wait is generous.
await sleep(25000);

const jevCalls = apiCalls.filter((c) => c.path.includes("select-executor"));
const genCalls = apiCalls.filter((c) => c.path.includes("minimax") || c.path.includes("/route"));
check("调用了 Jev 决策路由", jevCalls.length > 0, jevCalls.map((c) => c.path + "=" + c.status).join(" | "));
check("Jev 决策返回 200", jevCalls.some((c) => c.status === 200), jevCalls.map((c) => c.status).join(","));
check("调用了生成/路由", genCalls.length > 0, genCalls.map((c) => c.path + "=" + c.status).join(" | "));
check("无 4xx/5xx", !apiCalls.some((c) => c.status >= 400),
  apiCalls.filter((c) => c.status >= 400).map((c) => c.path + "=" + c.status).join(","));

check("未出现 JEV_API_KEY is not set", (await has("JEV_API_KEY is not set")) === false);
check("未出现凭据缺失提示", (await has("未提供控制令牌")) === false);
check("输出出现在页面上", (await has("JEV + MiniMax live test passed")) === true);
check("console errors = 0", consoleErrors.length === 0, consoleErrors.slice(0, 2).join(" | "));

const failed = results.filter((r) => !r.ok);
console.log("\n=== " + (results.length - failed.length) + "/" + results.length + " checks passed ===");
for (const f of failed) console.log("  FAILED: " + f.n);
ws.close();
process.exit(failed.length === 0 ? 0 : 1);
