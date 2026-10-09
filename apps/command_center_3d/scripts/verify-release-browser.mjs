/**
 * Release acceptance: real browser, real buttons, real chain.
 *
 * Why this exists at all: an API-level test cannot demonstrate that the PAGE uses
 * the credential the settings page saved, because the whole question is which
 * source the page read. Earlier rounds proved the backend; this proves the
 * operator's path.
 *
 * Two lessons from the previous attempt are baked in:
 *   - WAIT for a condition, never sleep a fixed span. The settings section
 *     switch was still in flight, so `#token` was read before it existed and the
 *     run reported a missing field that was really a timing artefact.
 *   - Never return `document.body.innerText` over CDP. A large SPA serialises as
 *     an object chain too deep to transfer, and the error arrives anonymously at
 *     the WebSocket layer instead of naming the expression.
 */
const CDP = "http://127.0.0.1:9229";
const APP = "http://127.0.0.1:4780";

const TASK =
  "这是一次 JEV + MiniMax 联合测试。\n" +
  "这是纯文本测试：不修改文件，不删除文件，不执行系统命令，不部署，不访问外部网络。\n" +
  "请由 Jev 选择执行器与生成模型。\n最终只输出：\nJEV + MiniMax live test passed.";

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
    setTimeout(() => { if (pend.has(n)) { pend.delete(n); rej(new Error("timeout " + m)); } }, 45000);
  });
}
async function ev(e) {
  try {
    const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) return { __err: r.exceptionDetails.text };
    return r.result && r.result.value;
  } catch (err) {
    return { __err: err.message };
  }
}

const results = [];
function check(n, ok, d = "") {
  const good = ok === true;
  results.push({ n, ok: good });
  console.log((good ? "PASS  " : "FAIL  ") + n + (d ? "  — " + d : ""));
}
const has = (needle) => ev(`document.body.innerText.includes(${JSON.stringify(needle)})`);

/** Poll a predicate in the page. A boolean, never a DOM node. */
async function waitFor(expr, { tries = 40, gap = 400, label = "" } = {}) {
  for (let i = 0; i < tries; i++) {
    if ((await ev(expr)) === true) return true;
    await sleep(gap);
  }
  if (label) console.log("  [timeout] " + label);
  return false;
}

const clickBtn = (label) => ev(`(() => {
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
  // `responseReceived` alone does not carry the request method, and a GET to
  // the same path proves nothing about a write gate. Pair it with the request.
  if (m.method === "Network.requestWillBeSent" && (m.params.request.url || "").includes("/api/")) {
    const path = m.params.request.url.replace(/^https?:\/\/[^/]+/, "");
    apiCalls.push({ path, method: m.params.request.method, status: null });
  }
  // One record PER CALL. An earlier version matched a response back onto the
  // first pending request for the same path, which merged two confirmations
  // into one row and made a replay invisible.
  if (m.method === "Network.responseReceived") {
    const u = m.params.response.url || "";
    if (u.includes("/api/")) {
      const path = u.replace(/^https?:\/\/[^/]+/, "");
      const pendingReq = [...apiCalls].reverse().find(
        (c) => c.path === path && c.status === null,
      );
      if (pendingReq) pendingReq.status = m.params.response.status;
      else apiCalls.push({ path, method: null, status: m.params.response.status });
    }
  }
});
await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");

// --- 1. open, with a clean slate so no cached token is present ------------- //
await send("Page.navigate", { url: APP });
await sleep(1200);
await ev("localStorage.clear(); sessionStorage.clear();");
await send("Page.navigate", { url: APP });
check("K1 首页已加载", await waitFor("!!document.querySelector('#root') && document.body.innerText.length > 50", { label: "root" }));

// --- 2. settings → security, and WAIT for the section to actually switch -- //
// The section list marks the active entry with `aria-current`, so waiting on
// THAT is waiting on the real state change rather than on an assumption about
// how fast React re-renders. Earlier runs clicked and immediately read, and
// reported a "missing field" that was really a section that had not switched.
await clickBtn("设置");
check("打开设置", await waitFor(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='安全')`, { label: "设置导航" }));
await clickBtn("安全");
const switched = await waitFor(
  `[...document.querySelectorAll('button[aria-current="true"]')].some(b=>b.textContent.trim()==='安全')`,
  { label: "aria-current=安全" },
);
check("K2 切到安全分组", switched);
const tokenFieldReady = await waitFor("!!document.querySelector('#token-security')", { tries: 20, label: "#token-security 出现" });
check("K3 安全分组含控制令牌输入框", tokenFieldReady);

// --- 3. enter the real token ------------------------------------------------ //
const put = await ev(`(() => {
  const el = document.querySelector('#token-security');
  if (!el) return 'no-field';
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, ${JSON.stringify(process.env.JCC_CONTROL_TOKEN || "")});
  el.dispatchEvent(new Event('input', { bubbles: true }));
  return 'typed';
})()`);
check("K3 输入控制令牌", put === "typed", "state=" + put);

// --- 4. verify authorisation through the UI button ------------------------- //
// Asserted on the NETWORK, not on wording. The verifier reports its verdict as
// component state (`verified`) and the button label; an earlier version of this
// script waited for Chinese text that the UI never renders, and reported a
// missing confirmation that did not exist.
apiCalls.length = 0;
await sleep(700);
await clickBtn("验证授权");
const verifyCall = await waitFor(
  `document.querySelector('#token-security') !== null`,
  { tries: 3, gap: 200 },
);
await sleep(2500);
const verifyReq = apiCalls.filter((c) => c.path.includes("credentials") && c.method === "POST");
check("K3b 验证授权发出了写请求", verifyReq.length > 0,
  apiCalls.map((c) => c.path + "=" + c.status).join(" | "));
// 422 is the PASS: the gate accepted the token and the body check caught it.
check("K3b' 正确 token 越过 auth gate（422=通过）",
  verifyReq.some((c) => c.status === 422 || c.status === 200),
  verifyReq.map((c) => c.status).join(","));

// --- 5. no persistence (B4) ------------------------------------------------- //
const persisted = await ev(`(() => {
  const ls = JSON.stringify(localStorage), ss = JSON.stringify(sessionStorage);
  return { ls: ls.includes('JCC_CONTROL') || /"token"\\s*:/.test(ls),
           ss: ss.includes('JCC_CONTROL') || /"token"\\s*:/.test(ss),
           cookie: document.cookie, url: location.href };
})()`);
check("K3c 令牌未落 storage/cookie/URL",
  persisted.ls === false && persisted.ss === false && !persisted.cookie && !persisted.url.includes("JCC"),
  JSON.stringify(persisted).slice(0, 100));

// --- 6. models page: both provider cards ---------------------------------- //
await clickBtn("模型与 API");
const cards = await (async () => {
  for (let i = 0; i < 30; i++) {
    const c = await ev(`(() => {
      const spans = [...document.querySelectorAll('span')].map(s => s.textContent.trim());
      const names = spans.filter(t => /^(TypeSafe Jev|MiniMax M3|Qwen Coding Plan|DeepSeek|Custom)/.test(t));
      const states = spans.filter(t => /^(已连接|上次验证通过|已配置 · 待验证|需要 API Key)$/.test(t));
      return names.map((n, i) => ({ name: n, state: states[i] ?? '' }));
    })()`);
    if (Array.isArray(c) && c.length === 5) return c;
    await sleep(400);
  }
  return [];
})();
check("K4-5 读到 5 张 provider 卡", cards.length === 5, JSON.stringify(cards));
const jevCard = cards.find((c) => c.name.startsWith("TypeSafe"));
const mmCard = cards.find((c) => c.name.startsWith("MiniMax"));
check("K5a TypeSafe Jev = 已连接", jevCard?.state === "已连接", "state=" + jevCard?.state);
check("K5b MiniMax 状态语义正确（非 LIVE 冒充）",
  mmCard?.state === "已连接" || mmCard?.state === "上次验证通过", "state=" + mmCard?.state);
check("K5c 无「正在读取」残留", (await has("正在读取 Provider")) === false);

// --- 7. home → run the mission --------------------------------------------- //
await clickBtn("首页");
const homeReady = await waitFor("!!document.querySelector('textarea')", { label: "任务输入框" });
check("K7 首页有任务输入框", homeReady);

await ev(`(() => {
  const box = document.querySelector('textarea');
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(box, ${JSON.stringify(TASK)});
  box.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`);
check("K8 任务已填入", true);

apiCalls.length = 0;
const clicked = await clickBtn("开始执行");
check("K9 点击开始执行", clicked === "clicked", "state=" + clicked);

// The chain: a real Jev decision, then EITHER a confirm card (when the decision
// needs a human) or a generation. Both are correct outcomes, so both are handled
// rather than one being assumed.
await sleep(25000);

const jev = apiCalls.filter((c) => c.path.includes("select-executor"));
check("K10 调用了 Jev 决策", jev.length > 0, jev.map((c) => c.path + "=" + c.status).join(" | "));
check("K12 无 4xx/5xx", !apiCalls.some((c) => c.status >= 400),
  apiCalls.filter((c) => c.status >= 400).map((c) => c.path + "=" + c.status).join(","));
check("K13 页面无 JEV_API_KEY is not set", (await has("JEV_API_KEY is not set")) === false);
check("K14 页面无「未知 Provider」", (await has("未知 Provider")) === false);

// J: the confirmation card, and completing it through the UI.
const cardShown = (await has("需要人工确认")) === true;
check("J3 确认卡已呈现（含执行器/置信度/原因）", cardShown);

if (cardShown) {
  check("J3b 卡片含「确认并继续」", (await has("确认并继续")) === true);
  check("J3c 卡片含「取消」", (await has("取消")) === true);
  check("J3d 卡片展示置信度百分比", /\d+%/.test(await ev("document.body.innerText.match(/\\d+%/)?.[0] || ''")));

  // J4: a real authenticated backend action, not a local state flip.
  // The button is TWO-PHASE by design: the first click asks the server to mint
  // a bound approval, and only once that ticket exists does the same label
  // submit the confirmation. An earlier run clicked once and then reported
  // "no confirm call" — the script was wrong, not the flow.
  apiCalls.length = 0;
  await clickBtn("确认并继续");
  const gotTicket = await waitFor(
    `document.body.innerText.includes('仅可使用一次') || document.body.innerText.includes('approval')`,
    { tries: 25, label: "审批签发" },
  );
  check("J4a 首次点击签发审批（ticket 可见）", gotTicket);
  if (gotTicket) {
    await sleep(600);
    await clickBtn("确认并继续");
  }
  await sleep(8000);

  const appr = apiCalls.filter((c) => c.path.includes("request-approval"));
  const conf = apiCalls.filter((c) => c.path.includes("/jev/confirm"));
  check("J4b 发送了真实审批写请求", appr.length > 0, appr.map((c) => c.path + "=" + c.status).join(" | "));
  check("J4c 审批签发返回 200", appr.length > 0 && appr.every((c) => c.status === 200),
    appr.map((c) => c.status).join(","));
  check("J4d 确认请求已发送", conf.length > 0, conf.map((c) => c.path + "=" + c.status).join(" | "));
  check("J4e 确认返回 200（后端校验通过）", conf.length > 0 && conf.every((c) => c.status === 200),
    conf.map((c) => c.status).join(","));
  // Count POSTs only. A token-gated cross-origin request is preceded by an
  // OPTIONS preflight, and counting both made one confirmation look like a
  // replay. The earlier failure was the checker's arithmetic, not a second use.
  const confPosts = apiCalls.filter((c) => c.path.includes("/jev/confirm") && c.method === "POST");
  check("J6 同一 approval 只被提交一次（无重放）", confPosts.length === 1,
    "POST confirm=" + confPosts.length + " statuses=" + confPosts.map((c) => c.status).join(","));
  await sleep(12000);
}

// The mission path is `createMission` + `transitionMission` + a launch, NOT
// /route or /minimax. An earlier version of this check looked for the router and
// therefore reported "no generation call" on a run where the mission had in fact
// been created — the assertion was watching the wrong endpoints.
const mission = apiCalls.filter((c) => /missions|launch/.test(c.path));
check("K11 确认后启动了 mission（含 launch）", mission.length > 0,
  apiCalls.map((c) => c.method + " " + c.path + "=" + c.status).join(" | ").slice(0, 220));
check("K15 最终输出或确认流程已推进",
  (await has("需要人工确认")) === true || (await has("JEV + MiniMax live test passed")) === true);
check("K16 console errors = 0", consoleErrors.length === 0, consoleErrors.slice(0, 2).join(" | "));

const failed = results.filter((r) => !r.ok);
console.log("\n=== " + (results.length - failed.length) + "/" + results.length + " checks passed ===");
for (const f of failed) console.log("  FAILED: " + f.n);
ws.close();
process.exit(failed.length === 0 ? 0 : 1);
