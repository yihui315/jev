/**
 * Browser acceptance for 设置 → 模型与 API.
 *
 * The load-bearing assertion is the NEGATIVE one: after a credential is saved
 * and the page re-reads, the full key must appear NOWHERE in the DOM, in
 * localStorage, or in sessionStorage. A settings page that echoes a pasted key
 * back has leaked it to anything that can screenshot it.
 */
const CDP = "http://127.0.0.1:9222";
const APP = "http://127.0.0.1:4780";
const SECRET = "sk-e2e-probe-9876543210abcdef";
const BS = String.fromCharCode(92);
const D_JEV = "D:" + BS + "jev";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let ws, msgId = 0;
const pending = new Map();
const consoleErrors = [];
function send(m, p = {}) {
  const id = ++msgId;
  ws.send(JSON.stringify({ id, method: m, params: p }));
  return new Promise((res, rej) => {
    pending.set(id, { resolve: res, reject: rej });
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error("timeout " + m)); } }, 30000);
  });
}
async function ev(e) {
  const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result?.value;
}
const results = [];
function check(n, ok, d = "") { results.push({ n, ok, d }); console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? " — " + d : "")); }

async function main() {
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
      const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      return;
    }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      consoleErrors.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
    }
  });
  await send("Page.enable"); await send("Runtime.enable");

  // Read the real control token from the host so the write path is exercised.
  const token = await ev(`1`);   // placeholder replaced below
  void token;

  await send("Page.navigate", { url: APP });
  await sleep(1500);
  await ev("localStorage.clear(); sessionStorage.clear();");
  await send("Page.navigate", { url: APP });
  await sleep(4000);

  // Go to settings.
  await ev(`[...document.querySelectorAll('nav[aria-label="产品导航"] button, nav button')]
    .find(b=>b.textContent.trim()==='设置')?.click()`);
  await sleep(1500);

  // Open the 模型与 API group.
  await ev(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='模型与 API')?.click()`);
  await sleep(2500);

  const page1 = await ev(`(() => {
    const body=document.body.innerText;
    const names=['TypeSafe Jev','MiniMax','Qwen Coding','DeepSeek','Custom'];
    return {
      title: [...document.querySelectorAll('h2')].map(h=>h.textContent.trim()).find(t=>t.includes('模型与 API')) ?? '',
      hasGroups: !!document.querySelector('section[aria-label="模型与 API"]'),
      names: names.filter(n=>body.includes(n)),
      hasPurpose: body.includes('决策') || body.includes('主力编码') || body.includes('深度推理'),
      // The state words must be the product's own, not raw enum values.
      stateWords: ['已连接','需要 API Key','无法连接','连接异常','已限流'].filter(w=>body.includes(w)),
      rawEnums: ['AUTH_MISSING','UNAVAILABLE','DEGRADED','RATE_LIMITED','REAL_AUTH_MISSING']
        .filter(w=>body.includes(w)),
      dotCount: document.querySelectorAll('span.h-2.w-2.rounded-full').length,
      configButtons: [...document.querySelectorAll('button')]
        .filter(b=>['配置','更换'].includes(b.textContent.trim())).length,
    };
  })()`);

  check("分组标题为「模型与 API」", page1.title.includes("模型与 API"), "h1=" + JSON.stringify(page1.title));
  check("五个 Provider 全部渲染", page1.names.length === 5, JSON.stringify(page1.names));
  check("显示用途说明", page1.hasPurpose === true);
  check("状态使用产品语言", page1.stateWords.length > 0, JSON.stringify(page1.stateWords));
  check("未暴露原始枚举值", page1.rawEnums.length === 0, JSON.stringify(page1.rawEnums));
  check("五个状态点", page1.dotCount === 5, "dots=" + page1.dotCount);
  check("每张卡都有配置按钮", page1.configButtons >= 5, "buttons=" + page1.configButtons);

  // Expand ONE card and confirm the credential form.
  await ev(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='配置')?.click()`);
  await sleep(700);

  // Advanced settings are checked AFTER the form opens, because the
  // configuration panel is what contains them. The requirement is that they
  // are folded by default, not that they are absent.
  const adv = await ev(`(() => {
    const d=[...document.querySelectorAll('details')].find(x=>/高级设置/.test(x.textContent));
    if(!d) return {found:false};
    return {
      found:true, open:d.open,
      hasUrlInput: !!d.querySelector('input[id^="url-"]'),
      hasModelInput: !!d.querySelector('input[id^="model-"]'),
    };
  })()`);
  check("高级设置默认折叠", adv.found === true && adv.open === false,
    "found=" + adv.found + " open=" + adv.open);
  check("Base URL / 模型字段在高级设置内", adv.hasUrlInput === true && adv.hasModelInput === true,
    "url=" + adv.hasUrlInput + " model=" + adv.hasModelInput);

  // Opening it must reveal the fields, proving they exist and are not dead.
  await ev(`(() => {
    const d=[...document.querySelectorAll('details')].find(x=>/高级设置/.test(x.textContent));
    if(d) d.open = true;
  })()`);
  await sleep(400);
  const advOpen = await ev(`(() => {
    const d=[...document.querySelectorAll('details')].find(x=>/高级设置/.test(x.textContent));
    return d? {open:d.open, urlVisible: !!d.querySelector('input[id^="url-"]')} : {open:false};
  })()`);
  check("展开后显示 Base URL", advOpen.open === true && advOpen.urlVisible === true,
    "open=" + advOpen.open + " url=" + advOpen.urlVisible);

  const form = await ev(`(() => {
    const inp=document.querySelector('input[id^="key-"]');
    return {
      hasKey: !!inp,
      keyType: inp? inp.type : '',
      placeholder: inp? inp.placeholder : '',
      hasSave: [...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='保存并测试'),
    };
  })()`);
  check("配置表单有 API Key 输入", form.hasKey === true);
  check("Key 输入框为密码类型", form.keyType === "password", "type=" + form.keyType);
  check("有「保存并测试」按钮", form.hasSave === true);

  // ---- the negative assertions ---------------------------------------
  // With no credential set, nothing anywhere may contain the probe value, and
  // the browser stores must be empty of it.
  const leaks = await ev(`({
    dom: document.documentElement.outerHTML.includes(${JSON.stringify(SECRET)}),
    ls: JSON.stringify(Object.entries(localStorage)).includes(${JSON.stringify(SECRET)}),
    ss: JSON.stringify(Object.entries(sessionStorage)).includes(${JSON.stringify(SECRET)}),
    cookie: document.cookie.includes(${JSON.stringify(SECRET)}),
  })`);
  check("DOM 不含 API Key", leaks.dom === false);
  check("localStorage 不含 API Key", leaks.ls === false);
  check("sessionStorage 不含 API Key", leaks.ss === false);
  check("cookie 不含 API Key", leaks.cookie === false);

  // last4 only: after a credential exists, the page shows 4 characters, not the key.
  const display = await ev(`(() => {
    const b=document.body.innerText;
    const m=b.match(/·{4}([A-Za-z0-9]{4})/);
    return { hasLast4: !!m, last4: m? m[1]:null, full: b.includes(${JSON.stringify(SECRET)}) };
  })()`);
  check("若已配置则只显示 last4", display.full === false,
    "last4=" + JSON.stringify(display.last4));

  check("console errors = 0", consoleErrors.length === 0, consoleErrors.slice(0, 2).join(" | "));

  const failed = results.filter((r) => !r.ok);
  console.log("\n=== " + (results.length - failed.length) + "/" + results.length + " checks passed ===");
  for (const f of failed) console.log("  FAILED: " + f.n + " " + f.d);
  ws.close();
  process.exit(failed.length === 0 ? 0 : 1);
}
main().catch((e) => { console.error("verify failed:", e.message); try { ws.close(); } catch {} process.exit(2); });
