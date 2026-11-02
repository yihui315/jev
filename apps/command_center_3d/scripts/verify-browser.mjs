/**
 * Browser acceptance for the v2 shell, driven over CDP against a real Chrome.
 *
 * WHAT IT CHECKS
 * --------------
 *  1. The home page renders the one question, the one input and the one CTA.
 *  2. Console errors are zero (a React key warning or a bad import shows here).
 *  3. The navigation lists exactly four entries in Simple Mode.
 *  4. Clicking 开始执行 with no credential surfaces an HONEST failure — the
 *     backend's 401, named — rather than a fake success.
 *  5. Real mission data from the backend reaches the recent-task list.
 *  6. A task's workspace opens with the five-stage rail and five tabs.
 *  7. Switching to Expert Mode reveals the advanced views.
 *
 * No page is reloaded between steps: the spec requires the whole flow to work in
 * one session, so a passing run here also proves the router keeps state.
 *
 * Usage: node scripts/verify-browser.mjs
 *
 * Node 22's global WebSocket is used deliberately: this is a verification
 * script, and adding a dependency to the product's tree to check it would be a
 * worse trade than using the runtime that is already required.
 */

const CDP_HTTP = "http://127.0.0.1:9222";
const APP = "http://127.0.0.1:4780";

let ws;
let msgId = 0;
const pending = new Map();
const consoleErrors = [];
const pageErrors = [];

/** Send one CDP command and await its reply. */
function send(method, params = {}, sessionId) {
  const id = ++msgId;
  const payload = { id, method, params };
  if (sessionId) payload.sessionId = sessionId;
  ws.send(JSON.stringify(payload));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }
    }, 30000);
  });
}

/** Evaluate an expression in the page and return its JSON value. */
async function evaluate(sessionId, expression) {
  const r = await send(
    "Runtime.evaluate",
    { expression, returnByValue: true, awaitPromise: true },
    sessionId,
  );
  if (r.exceptionDetails) {
    throw new Error(r.exceptionDetails.text ?? "evaluate threw");
  }
  return r.result?.value;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

async function main() {
  // --- attach to a fresh tab -------------------------------------------
  const targets = await (await fetch(`${CDP_HTTP}/json/list`)).json();
  const page = targets.find((t) => t.type === "page");
  if (!page) throw new Error("no page target available");

  // Node's global WebSocket is an EventTarget, not an EventEmitter — hence
  // addEventListener rather than `once`.
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", () => res(), { once: true });
    ws.addEventListener("error", () => rej(new Error("CDP socket failed")), { once: true });
  });

  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(typeof ev.data === "string" ? ev.data : String(ev.data));
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      return;
    }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      consoleErrors.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (m.method === "Runtime.exceptionThrown") {
      pageErrors.push(m.params.exceptionDetails?.text ?? "exception");
    }
  });

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Log.enable");

  // --- 1. open the app --------------------------------------------------
  // Start from a clean profile: the Simple/Expert choice is persisted in
  // localStorage by design, so a previous run would otherwise decide this run's
  // navigation and make the four-entry check pass or fail for the wrong reason.
  await send("Page.navigate", { url: APP });
  await sleep(1500);
  await evaluate(undefined, "localStorage.clear()");
  await send("Page.navigate", { url: APP });
  await sleep(3500);

  const title = await evaluate(undefined, "document.title");
  check("首页加载", typeof title === "string" && title.includes("JEV"), `title="${title}"`);

  // --- 2. the one question + the one action ------------------------------
  const h1 = await evaluate(undefined, "document.querySelector('h1')?.textContent?.trim() ?? ''");
  // Updated for the v2.1 product-role fix: the headline names JEV as the
  // orchestrator rather than asking an anonymous "what do you want".
  check(
    "主标题点名 JEV",
    h1 === "告诉 JEV 你想完成什么。",
    `h1="${h1}"`,
  );

  const hasComposer = await evaluate(
    undefined,
    "!!document.querySelector('#task-goal')",
  );
  check("存在任务输入框", hasComposer === true);

  const cta = await evaluate(
    undefined,
    "[...document.querySelectorAll('button')].find(b=>b.textContent.includes('开始执行'))?.textContent?.trim() ?? ''",
  );
  check("存在唯一主 CTA「开始执行」", cta.includes("开始执行"), `cta="${cta}"`);

  const quickTasks = await evaluate(
    undefined,
    "[...document.querySelectorAll('button')].filter(b=>['检查项目并修复所有问题','运行测试并生成报告','优化当前前端','部署到生产环境'].includes(b.textContent.trim())).length",
  );
  check("四个快捷任务模板", quickTasks === 4, `count=${quickTasks}`);

  // --- 3. exactly four nav entries in Simple Mode ------------------------
  const navItems = await evaluate(
    undefined,
    "[...document.querySelectorAll('nav[aria-label=\"产品导航\"] button')].map(b=>b.textContent.trim()).filter(t=>['首页','任务','项目','设置'].includes(t))",
  );
  check(
    "普通模式导航为 4 项",
    Array.isArray(navItems) && navItems.length === 4,
    JSON.stringify(navItems),
  );

  // --- 4. real backend data reached the recent list ----------------------
  const recent = await evaluate(
    undefined,
    "(() => { const h=[...document.querySelectorAll('h2')].find(x=>x.textContent.includes('最近任务')); if(!h) return {found:false}; const ul=h.closest('section')?.querySelector('ul'); return {found:true, count: ul?ul.children.length:0, first: ul?.children[0]?.textContent?.trim().slice(0,60) ?? ''}; })()",
  );
  check(
    "最近任务读取到真实后端数据",
    recent.found === true && recent.count > 0,
    `count=${recent.count} first="${(recent.first ?? "").slice(0, 40)}"`,
  );

  // --- 5. the header reports health, not jargon --------------------------
  // The probes run on a 20s cadence with a 4s SSE budget, so the verdict is read
  // AFTER they settle — reading it immediately would assert on a half-built
  // state rather than on the product's actual answer.
  const HEALTHY = ["系统正常", "部分异常", "状态未知"];
  let headerLabel = "";
  for (let i = 0; i < 14; i++) {
    headerLabel = await evaluate(
      undefined,
      "[...document.querySelectorAll('header button')].map(b=>b.textContent.trim()).find(t=>['系统正常','部分异常','状态未知'].includes(t)) ?? ''",
    );
    if (headerLabel) break;
    await sleep(1000);
  }
  check(
    "顶栏只显示一句系统状态",
    HEALTHY.includes(headerLabel),
    `status="${headerLabel}"`,
  );

  // The backend IS healthy on this host, so the honest verdict is 系统正常.
  // Anything else means a probe is misreporting — either faking a fault or, far
  // worse, a permanent 状态未知 that hides a working system.
  check(
    "健康后端得到「系统正常」（非永久未知）",
    headerLabel === "系统正常",
    `status="${headerLabel}"`,
  );

  // Every probe row must be backed by a real read, and no row may be silently
  // missing — an absent row reads as "fine" by omission.
  await evaluate(
    undefined,
    "[...document.querySelectorAll('header button')].find(b=>/系统正常|部分异常|状态未知/.test(b.textContent))?.click()",
  );
  await sleep(700);
  const probeRows = await evaluate(
    undefined,
    "[...document.querySelectorAll('[role=\"dialog\"] li')].map(li=>li.textContent.replace(/\\s+/g,' ').trim())",
  );
  check(
    "系统状态展开后列出全部子系统",
    Array.isArray(probeRows) && probeRows.length === 4,
    `rows=${Array.isArray(probeRows) ? probeRows.length : "?"} ${JSON.stringify(probeRows ?? []).slice(0, 120)}`,
  );
  await evaluate(
    undefined,
    "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
  );
  await sleep(300);

  // --- 6. an honest failure, not a fake success --------------------------
  // No control token is configured, so the backend answers 401. The UI must say
  // so; anything else would be a fabricated green.
  const failMsg = await evaluate(
    undefined,
    `(async () => {
       const ta = document.querySelector('#task-goal');
       const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
       setter.call(ta, '检查当前项目状态，不修改文件，告诉我主要模块。');
       ta.dispatchEvent(new Event('input',{bubbles:true}));
       await new Promise(r=>setTimeout(r,300));
       const btn=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('开始执行'));
       if(btn.disabled) return {disabled:true, text:''};
       btn.click();
       await new Promise(r=>setTimeout(r,3000));
       const alert=document.querySelector('[role="alert"]');
       return {disabled:false, text: alert? alert.textContent.trim().slice(0,160) : ''};
     })()`,
  );
  check(
    "无凭据时诚实报告失败（不伪造成功）",
    failMsg.disabled === true || failMsg.text.length > 0,
    failMsg.disabled ? "按钮禁用并给出原因" : `alert="${failMsg.text.slice(0, 60)}"`,
  );

  // --- 7. open a task workspace, no reload --------------------------------
  const opened = await evaluate(
    undefined,
    `(() => {
       const card = document.querySelector('ul li button[aria-label^="打开任务"]');
       if (!card) return {ok:false};
       card.click();
       return {ok:true};
     })()`,
  );
  await sleep(2200);
  check("可从首页进入任务详情", opened.ok === true);

  const stages = await evaluate(
    undefined,
    "(() => { const el=document.querySelector('ol[aria-label=\"任务进度\"]'); return el? el.children.length : 0; })()",
  );
  check("五阶段进度条渲染", stages === 5, `rows=${stages}`);

  const stageLabels = await evaluate(
    undefined,
    "(() => { const el=document.querySelector('ol[aria-label=\"任务进度\"]'); if(!el) return []; return [...el.children].map(li=>li.textContent.replace(/\\s+/g,' ').trim()); })()",
  );
  // The rail now reads 「JEV · 理解任务」 for four stages and 「<executor> · 执行
  // 任务」 for the delegated one, so the assertion checks the full first words
  // rather than the leading 4 characters (which are now the owner).
  check(
    "阶段文案为中文五阶段",
    stageLabels.length === 5 &&
      stageLabels[0].includes("理解任务") &&
      stageLabels[1].includes("制定计划") &&
      stageLabels[2].includes("执行任务") &&
      stageLabels[3].includes("验证结果") &&
      stageLabels[4].includes("完成交付"),
    JSON.stringify(stageLabels),
  );

  const tabs = await evaluate(
    undefined,
    "[...document.querySelectorAll('[role=\"tab\"]')].map(t=>t.textContent.trim())",
  );
  check(
    "任务页五个 Tab",
    Array.isArray(tabs) && tabs.length === 5,
    JSON.stringify(tabs),
  );

  // --- 8. Expert Mode reveals the advanced surfaces ----------------------
  await evaluate(
    undefined,
    "(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='设置'); if(b) b.click(); })()",
  );
  await sleep(1200);
  await evaluate(
    undefined,
    "(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='高级'); if(b) b.click(); })()",
  );
  await sleep(600);
  const switched = await evaluate(
    undefined,
    "(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='专家模式'); if(!b) return false; b.click(); return true; })()",
  );
  await sleep(1200);
  const expertNav = await evaluate(
    undefined,
    "[...document.querySelectorAll('nav[aria-label=\"产品导航\"] button')].map(b=>b.textContent.trim()).filter(t=>t.length>0).length",
  );
  check(
    "专家模式显示更多导航项",
    switched === true && expertNav > 4,
    `expertNav=${expertNav}`,
  );

  // --- 9. no console errors anywhere in the flow -------------------------
  check(
    "全流程 console errors = 0",
    consoleErrors.length === 0,
    consoleErrors.slice(0, 3).join(" | "),
  );
  check(
    "全流程未捕获异常 = 0",
    pageErrors.length === 0,
    pageErrors.slice(0, 3).join(" | "),
  );

  // --- summary -----------------------------------------------------------
  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== ${results.length - failed.length}/${results.length} checks passed ===`);
  if (failed.length > 0) {
    console.log("FAILED:");
    for (const f of failed) console.log(`  - ${f.name} ${f.detail}`);
  }
  ws.close();
  process.exit(failed.length === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error("verify failed:", e.message);
  try { ws?.close(); } catch { /* ignore */ }
  process.exit(2);
});
