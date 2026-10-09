/**
 * The end-to-end path the spec calls for: F:\jev is refused, the user presses
 * 「选择 D:\jev」, and the flow must arrive at a form bound to the BACKEND's
 * root — never a rewritten one.
 *
 * This is a separate script from verify-ux.mjs because it navigates away and
 * comes back, which is a different flow from the read-only audit.
 */
const CDP = "http://127.0.0.1:9222";
const APP = "http://127.0.0.1:4780";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let ws, msgId = 0;
const pending = new Map();
function send(m, p = {}) {
  const id = ++msgId;
  ws.send(JSON.stringify({ id, method: m, params: p }));
  return new Promise((res, rej) => {
    pending.set(id, { resolve: res, reject: rej });
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); rej(new Error(`timeout ${m}`)); } }, 30000);
  });
}
async function ev(e) {
  const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result?.value;
}
const results = [];
function check(n, ok, d = "") { results.push({ n, ok, d }); console.log(`${ok ? "PASS" : "FAIL"}  ${n}${d ? ` — ${d}` : ""}`); }

async function main() {
  const targets = await (await fetch(`${CDP}/json/list`)).json();
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
    }
  });
  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: APP });
  await sleep(1500);
  await ev("localStorage.clear(); sessionStorage.clear();");
  await send("Page.navigate", { url: APP });
  await sleep(4000);

  // The suggestion must come from the BACKEND boundary.
  const allowed = await ev(`fetch('http://127.0.0.1:4779/api/v1/domain/home')
    .then(r=>r.json()).then(d=>JSON.stringify(d.project_scope.allowed_project_roots))`);
  check("后端边界为 [D:\\jev]", allowed === '["D:\\\\jev"]', `allowed=${allowed}`);

  // Press 「选择 D:\jev」 on the friendly card.
  const pressed = await ev(`(() => {
    const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim().startsWith('选择 D:'));
    if(!b) return {ok:false, buttons:[...document.querySelectorAll('button')].map(x=>x.textContent.trim()).slice(0,30)};
    b.click(); return {ok:true};
  })()`);
  check("「选择 D:\\jev」按钮存在并可点击", pressed.ok === true, pressed.ok ? "" : JSON.stringify(pressed.buttons));
  await sleep(2500);

  // Scope disclosure, read BEFORE opening the form: the collapsed panel is where
  // the boundary is stated, and opening the form replaces that line with inputs.
  const scopeNote = await ev(`(() => {
    const p=[...document.querySelectorAll('p')].find(x=>/允许的工作区范围/.test(x.textContent));
    return p? p.textContent.trim():'';
  })()`);
  check("页面标明允许范围", scopeNote.includes("D:\\jev"), `note="${scopeNote}"`);

  // Open the create form (it is a panel, not a modal) and assert its contents.
  await ev(`(() => {
    const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='新建项目');
    if(b) b.click();
  })()`);
  await sleep(700);

  const onProjects = await ev(`(() => {
    const h=[...document.querySelectorAll('h2')].find(x=>/Projects|项目/.test(x.textContent));
    const sel=document.querySelector('#np-root');
    const nameInput=document.querySelector('#np-name');
    return {
      heading: h? h.textContent.trim() : '',
      hasForm: !!sel && !!nameInput,
      // The select must offer the authorised root and NOTHING else — a free-text
      // input here would let a project be created outside the boundary.
      options: sel? [...sel.options].map(o=>o.value).filter(Boolean) : [],
      selected: sel? sel.value : '',
      label: (() => { const l=[...document.querySelectorAll('label')].find(x=>/仅限允许范围/.test(x.textContent)); return l? l.textContent.trim():''; })(),
    };
  })()`);

  check("跳转到项目页并打开新建表单", onProjects.hasForm === true, `heading="${onProjects.heading}"`);
  check(
    "新建表单目录选项 = 后端授权 root（非改写）",
    onProjects.options.length === 1 && onProjects.options[0] === "D:\\jev",
    `options=${JSON.stringify(onProjects.options)}`,
  );
  check(
    "目录输入为受限下拉（不可自由输入越界路径）",
    onProjects.label.includes("仅限允许范围"),
    `label="${onProjects.label}"`,
  );

  // Fill and submit WITHOUT a token: the server must refuse, and the UI must say
  // so in a sentence — that is the honest outcome, and it proves the form really
  // reaches the API rather than faking success.
  //
  // React tracks the previous value on the DOM node, so a programmatic write has
  // to go through the native value setter or the `input` event is treated as a
  // no-op. The setter must be applied to the element itself (`setter.call(el)`)
  // — losing that receiver is an "Illegal invocation".
  await ev(`(() => {
    const el=document.querySelector('#np-name');
    if(!el) return false;
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    setter.call(el, 'JEV');
    el.dispatchEvent(new Event('input',{bubbles:true}));
    return true;
  })()`);
  await sleep(300);

  const submitRes = await ev(`(async () => {
    const btn=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='创建项目');
    if(!btn) return {noButton:true};
    if(btn.disabled) return {disabled:true};
    btn.click();
    await new Promise(r=>setTimeout(r,2500));
    const err=document.querySelector('[role="alert"]');
    return { clicked:true, err: err? err.textContent.trim().slice(0,140) : '' };
  })()`);
  check(
    "创建请求真实到达后端（无令牌时被拒绝且说明原因）",
    submitRes.clicked === true && submitRes.err.length > 0,
    `res=${JSON.stringify(submitRes)}`,
  );
  check(
    "拒绝原因不是 JSON 契约",
    submitRes.clicked !== true || !/"code"\s*:|allowed_project_roots/.test(submitRes.err || ""),
    `err="${(submitRes.err || "").slice(0, 80)}"`,
  );

  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== ${results.length - failed.length}/${results.length} checks passed ===`);
  for (const f of failed) console.log(`  FAILED: ${f.n} ${f.d}`);
  ws.close();
  process.exit(failed.length === 0 ? 0 : 1);
}
main().catch((e) => { console.error("verify failed:", e.message); try { ws?.close(); } catch {} process.exit(2); });
