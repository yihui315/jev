/**
 * E2E acceptance: the REAL task, read from the PRODUCTION frontend.
 *
 * This script proves nothing by calling the API directly — the API was already
 * proven by the harness. It proves the running product at :4780 can SEE a task a
 * real WorkBuddy launch produced: a real project on D:\jev, a real mission, a
 * real session, a real timeline.
 *
 * If the frontend had to fabricate any of this to render it, these checks fail.
 */
const CDP = "http://127.0.0.1:9222";
const APP = "http://127.0.0.1:4780";
const MID = "msn_f0a2fe6587484677";
const SID = "ea79092e-f406-4345-acde-5fe3373b346c";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A Windows path in the DOM holds ONE backslash. Building the needle by
// concatenation rather than by regex removes the escaping trap that made these
// assertions read false against output that was actually correct.
const D_JEV = "D:" + String.fromCharCode(92) + "jev";
const F_JEV = "F:" + String.fromCharCode(92) + "jev";

let ws, msgId = 0;
const pending = new Map();
const consoleErrors = [];

function send(m, p = {}) {
  const id = ++msgId;
  ws.send(JSON.stringify({ id, method: m, params: p }));
  return new Promise((res, rej) => {
    pending.set(id, { resolve: res, reject: rej });
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); rej(new Error("timeout " + m)); }
    }, 30000);
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

async function main() {
  const targets = await (await fetch(CDP + "/json/list")).json();
  const target = targets.find((t) => t.type === "page");
  ws = new WebSocket(target.webSocketDebuggerUrl);
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
  });
  await send("Page.enable");
  await send("Runtime.enable");

  await send("Page.navigate", { url: APP });
  await sleep(1500);
  await ev("localStorage.clear(); sessionStorage.clear();");
  await send("Page.navigate", { url: APP });
  await sleep(5000);

  // ---- the workspace boundary, as the product enforces it ----------------
  // The project button's label is the SELECTED PROJECT's name — data, not a
  // fixed string — so it is located structurally: the icon button immediately
  // before the executor picker in the composer row.
  // Open the picker, then let React render before reading it. Reading in the
  // same tick as the click returned an empty list because the popover had not
  // been committed yet.
  const opened = await ev(`(() => {
    const all=[...document.querySelectorAll('button')];
    const i=all.findIndex(x=>x.getAttribute('aria-label')==='执行方式');
    // Located from EVIDENCE: the executor button carries aria-label="执行方式"
    // and the project button sits immediately before it. An earlier version
    // searched a 4-button window and hit the header status pill instead.
    const proj=all[i-1];
    if(!proj) return { err:'project button not found' };
    const label=proj.textContent.trim();
    proj.click();
    return { label, expanded: proj.getAttribute('aria-expanded') };
  })()`);
  await sleep(900);

  const picker = await ev(`(() => {
    const lb=document.querySelector('[role="listbox"]');
    return {
      options: lb? [...lb.querySelectorAll('[role="option"]')].map(o=>o.textContent.replace(/\\s+/g,' ').trim()) : [],
      items: lb? [...lb.querySelectorAll('li')].map(o=>o.textContent.replace(/\\s+/g,' ').trim()) : [],
    };
  })()`);

  // The button shows the project NAME (data), not its path. The path is proved
  // on the task page instead, where the backend's own root is rendered.
  check("项目选择器显示当前项目名", (opened.label || "").length > 0,
    "label=" + JSON.stringify(opened.label));
  // Expansion is proved by the popover EXISTING, not by re-reading an
  // attribute on the pre-click node reference (which is a stale snapshot).
  check("项目选择器可展开", (picker.options || []).length > 0,
    "options=" + (picker.options || []).length);
  check("可选项目中包含 D:\\jev 的合法项目",
    (picker.options || []).some((o) => o.includes(D_JEV)),
    "options=" + JSON.stringify(picker.options || []).slice(0, 150));
  check("F:\\jev 项目被标为失效且不可选",
    !(picker.options || []).some((o) => o.includes(F_JEV)) &&
      (picker.items || []).some((i) => i.includes("已失效")),
    "items=" + JSON.stringify((picker.items || []).slice(0, 3)).slice(0, 170));

  // ---- the real task list -----------------------------------------------
  await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  await sleep(500);

  const cards = await ev(`[...document.querySelectorAll('button[aria-label^="打开任务"]')].map(c=>c.getAttribute('aria-label'))`);
  check("前端任务列表读到后端真实任务", cards.length > 0, "count=" + cards.length);
  check("列表中含本次真实任务",
    cards.some((c) => /JEV E2E real task/.test(c)),
    "found=" + cards.some((c) => /JEV E2E real task/.test(c)));

  // ---- the real task page -----------------------------------------------
  await send("Page.navigate", { url: APP + "/#/task-detail?mission=" + MID });
  await sleep(4500);

  const tp = await ev(`(() => {
    const h1=document.querySelector('h1');
    const block=h1? h1.parentElement.parentElement : null;
    const stages=[...document.querySelectorAll('ol[aria-label="任务进度"] li')]
      .map(li=>li.textContent.replace(/\\s+/g,' ').trim());
    return {
      h1: h1? h1.textContent.trim() : '',
      header: block? block.textContent.replace(/\\s+/g,' ').trim() : '',
      body: document.body.innerText,
      stages,
    };
  })()`);

  check("真实任务详情页可打开", tp.h1.length > 0, 'h1="' + tp.h1 + '"');
  check("任务页显示 JEV 协调者角色", tp.header.includes("JEV 正在协调此任务"),
    'header="' + tp.header.slice(0, 100) + '"');
  check("任务页显示真实执行器 workbuddy", /执行器：\s*workbuddy/i.test(tp.header),
    'header="' + tp.header.slice(0, 130) + '"');
  check("任务页显示真实项目路径 D:\\jev", tp.header.includes(D_JEV),
    'header="' + tp.header.slice(0, 130) + '"');
  check("五阶段进度条按真实状态渲染", tp.stages.length === 5,
    "stages=" + JSON.stringify(tp.stages.map((s) => s.slice(0, 14))));
  check("RUNNING：JEV 已完成理解/计划，执行器进行中",
    tp.stages[0].includes("已完成") && tp.stages[1].includes("已完成") &&
      tp.stages[2].includes("进行中") && !tp.stages[2].startsWith("JEV"),
    'stage3="' + (tp.stages[2] || "").slice(0, 30) + '"');

  // ---- the real timeline -------------------------------------------------
  const tl = await ev(`(() => {
    const sec=document.querySelector('section[aria-label="实时事件"]');
    return sec? sec.textContent.replace(/\\s+/g,' ').trim() : '';
  })()`);
  check("实时事件区块有真实数据", tl.length > 0, 'timeline="' + tl.slice(0, 100) + '"');
  // The lines must carry the BACKEND's own words: `created` and
  // `mission runtime launch` are the server's recorded reasons, and the clock
  // times are its timestamps. Nothing here is authored by the frontend.
  check("时间线为后端真实记录（非伪造）",
    tl.includes("created") && tl.includes("mission runtime launch") &&
      /\d{2}:\d{2}:\d{2}/.test(tl),
    'timeline="' + tl.slice(0, 130) + '"');

  const tabs = await ev(`[...document.querySelectorAll('[role="tab"]')].map(t=>t.textContent.trim())`);
  check("任务页五个 Tab 齐备", Array.isArray(tabs) && tabs.length === 5, JSON.stringify(tabs));

  // ---- expert details carry the backend's real identifiers ---------------
  await ev(`[...document.querySelectorAll('[role="tab"]')].find(t=>t.textContent.includes('高级详情'))?.click()`);
  await sleep(2000);
  const expert = await ev("document.body.innerText");
  check("高级详情显示后端真实会话 ID", expert.includes(SID), "session=" + SID);
  check("高级详情显示真实 mission ID", expert.includes(MID), "mission=" + MID);

  // ---- result tab: honest empty state for a still-running task ------------
  await ev(`[...document.querySelectorAll('[role="tab"]')].find(t=>t.textContent.trim()==='结果')?.click()`);
  await sleep(1800);
  const result = await ev("document.body.innerText");
  check("结果页对未产出任务给出诚实空状态",
    /暂无交付文件|任务尚未结束|还没有产出/.test(result),
    "snippet=" + (result.match(/暂无[^\n]{0,16}|任务尚未结束/) || [""])[0]);
  check("结果页未伪造交付文件",
    !/148 tests passed|修复了 \d+ 个问题/.test(result),
    "no fabricated counts");

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
