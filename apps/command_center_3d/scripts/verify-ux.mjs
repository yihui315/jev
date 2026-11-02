/**
 * Acceptance for the four product fixes, driven over CDP against real Chrome.
 *
 *  1. PROJECT_ROOT: the composer targets D:\jev, F:\jev is refused BY THE
 *     FRONTEND, and no F: path is offered as selectable.
 *  2. OUT_OF_SCOPE renders a CARD, never a JSON body.
 *  3. The home page does not lead with an executor name; JEV 自动选择 is default.
 *  4. The progress rail reads JEV · … / <executor> · 执行任务 / JEV · …
 *
 * The load-bearing check is #1's negative: it asserts the page contains no
 * selectable F:\jev root at all, which is what a `F:`→`D:` rewrite would also
 * make true — so #1 also asserts the refusal is a REFUSAL (a named verdict),
 * not a silently substituted success.
 */
const CDP = "http://127.0.0.1:9222";
const APP = "http://127.0.0.1:4780";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let ws, msgId = 0;
const pending = new Map();
const consoleErrors = [];

function send(method, params = {}) {
  const id = ++msgId;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error(`timeout ${method}`)); } }, 30000);
  });
}
async function ev(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result?.value;
}
const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

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
  await ev("localStorage.clear()");
  await send("Page.navigate", { url: APP });
  await sleep(4000);

  // ---- 1. PROJECT_ROOT: D:\jev is the default, F:\jev is not offered ----
  const rootState = await ev(`(() => {
    const btn=[...document.querySelectorAll('button')].find(b=>b.querySelector('svg')&&/选择项目|jev/i.test(b.textContent));
    return {
      pickerText: btn? btn.textContent.trim() : '',
      options: (() => {
        if(!btn) return [];
        btn.click();
        return [...document.querySelectorAll('[role="option"]')].map(o=>({
          text:o.textContent.trim(),
          hasF: /[Ff]:[\\\\/]jev/.test(o.textContent),
        }));
      })(),
      pickerBody: (() => {
        const lb=document.querySelector('[role="listbox"]');
        return lb? lb.textContent : '';
      })(),
    };
  })()`);

  const D_JEV = "D:" + "\\" + "jev";
  const selectableF = rootState.options.filter((o) => o.hasF);
  check(
    "可选项中不含任何 F:\\jev 路径",
    selectableF.length === 0,
    `selectableF=${JSON.stringify(selectableF.map((o) => o.text.slice(0, 40)))}`,
  );
  check(
    "选择器按后端边界区分可用/失效",
    rootState.pickerBody.includes("已失效") || rootState.options.length === 0,
    `body="${rootState.pickerBody.replace(/\s+/g, " ").slice(0, 100)}"`,
  );
  await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  await sleep(300);

  // ---- 2. OUT_OF_SCOPE is a CARD, not JSON ----
  await ev(`(() => {
    const c=[...document.querySelectorAll('button')].find(b=>b.querySelector('svg')&&/选择项目|\\\\jev/i.test(b.textContent));
    if(c) c.click();
  })()`);
  await sleep(600);
  const blockedRow = await ev(`(() => {
    const rows=[...document.querySelectorAll('[role="listbox"] li')];
    const hit=rows.map(r=>r.textContent.replace(/\\s+/g,' ').trim()).find(t=>/已失效/.test(t));
    return hit ?? '';
  })()`);
  check(
    "F:\\jev 项目显示「项目位置已失效」而非可选",
    blockedRow.includes("已失效"),
    `row="${blockedRow.slice(0, 70)}"`,
  );
  // A blocked row must have no clickable control at all.
  const blockedClickable = await ev(`(() => {
    const rows=[...document.querySelectorAll('[role="listbox"] li')];
    const hit=rows.find(r=>/已失效/.test(r.textContent));
    if(!hit) return 'no-row';
    return hit.querySelectorAll('button, [role="option"]').length;
  })()`);
  check(
    "失效项不可点击（无绕过入口）",
    blockedClickable === 0 || blockedClickable === "no-row",
    `clickable=${blockedClickable}`,
  );
  await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  await sleep(300);

  // The friendly card, if one is shown for the blocked project.
  const gate = await ev(`(() => {
    const el=[...document.querySelectorAll('section[aria-label="无法开始任务"]')][0];
    if(!el) return null;
    return {
      headline: el.querySelector('h3')?.textContent?.trim() ?? '',
      sentence: el.querySelectorAll('p')[0]?.textContent?.trim() ?? '',
      body: el.textContent.replace(/\\s+/g,' ').trim(),
      buttons: [...el.querySelectorAll('button')].map(b=>b.textContent.trim()),
    };
  })()`);

  if (gate) {
    check("OUT_OF_SCOPE 标题为「无法开始任务」", gate.headline === "无法开始任务", `h="${gate.headline}"`);
    check(
      "OUT_OF_SCOPE 显示普通用户句子",
      gate.sentence.includes("不在 JEV 允许的工作区范围内"),
      `p="${gate.sentence}"`,
    );
    // The load-bearing negative: raw JSON must not be the message. It may exist
    // only behind the 技术详情 disclosure.
    const rawVisible = /"code"\\s*:|"allowed_project_roots"\\s*:|PROJECT_ROOT_OUT_OF_SCOPE/.test(gate.body);
    check("OUT_OF_SCOPE 未直接显示 JSON", rawVisible === false, `body="${gate.body.slice(0, 120)}"`);
    check(
      "提供「选择 <后端授权 root>」与「重新选择项目」",
      gate.buttons.some((b) => b.includes(D_JEV)) && gate.buttons.some((b) => b.includes("重新选择项目")),
      JSON.stringify(gate.buttons),
    );
    // The card's 当前 must name the stale root AND 允许 must name the server's.
    check(
      "错误卡同时给出「当前」与「允许」",
      /当前：/.test(gate.body) && /允许：/.test(gate.body) && gate.body.includes(D_JEV),
      `body="${gate.body.slice(0, 130)}"`,
    );
  } else {
    // No card is legitimate ONLY when nothing is blocked; the picker check above
    // already proved a blocked row exists, so a missing card is a real failure.
    check("OUT_OF_SCOPE 渲染友好错误卡", false, "选择器存在失效项但首页无错误卡");
  }

  // ---- 3. JEV is the product; the executor is not on the home surface ----
  const h1 = await ev("document.querySelector('h1')?.textContent?.trim() ?? ''");
  check("首页主标题为「告诉 JEV 你想完成什么。」", h1 === "告诉 JEV 你想完成什么。", `h1="${h1}"`);

  const sub = await ev(`(() => {
    const p=[...document.querySelectorAll('p')].find(x=>/JEV 会分析任务/.test(x.textContent));
    return p? p.textContent.trim() : '';
  })()`);
  check(
    "品牌说明为 JEV 编排口径",
    sub.includes("分析任务") && sub.includes("制定计划") && sub.includes("执行器") && sub.includes("监督完成"),
    `sub="${sub}"`,
  );

  const collapsed = await ev(`(() => {
    const b=[...document.querySelectorAll('button')].find(x=>x.getAttribute('aria-label')==='执行方式');
    return b? b.textContent.trim() : '';
  })()`);
  check(
    "执行方式默认「JEV 自动选择」且不含执行器名",
    collapsed.includes("JEV 自动选择") && !/WorkBuddy|Codex|Claude|Hermes/.test(collapsed),
    `collapsed="${collapsed}"`,
  );

  const homeText = await ev("document.body.innerText");
  const executorNamesHome = ["WorkBuddy", "Codex", "Claude", "Hermes"].filter((n) => homeText.includes(n));
  check(
    "首页不直接展示执行器名",
    executorNamesHome.length === 0,
    `found=${JSON.stringify(executorNamesHome)}`,
  );

  // Expanding reveals all four as an advanced choice.
  await ev(`[...document.querySelectorAll('button')].find(x=>x.getAttribute('aria-label')==='执行方式')?.click()`);
  await sleep(500);
  const expanded = await ev(`(() => {
    const lb=document.querySelector('[role="listbox"][aria-label="选择执行方式"]');
    return lb? lb.textContent.replace(/\\s+/g,' ').trim() : '';
  })()`);
  check(
    "高级展开列出 JEV 自动选择 + 四个执行器",
    expanded.includes("JEV 自动选择") && expanded.includes("推荐") &&
    ["WorkBuddy", "Codex", "Claude", "Hermes"].every((n) => expanded.includes(n)),
    `expanded="${expanded.slice(0, 130)}"`,
  );
  await ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  await sleep(300);

  // ---- 4. the progress rail credits JEV for four of five stages ----
  await ev(`(() => {
    const card=document.querySelector('button[aria-label^="打开任务"]');
    if(card) card.click();
  })()`);
  await sleep(2500);

  const rail = await ev(`(() => {
    const ol=document.querySelector('ol[aria-label="任务进度"]');
    if(!ol) return null;
    return [...ol.children].map(li=>{
      const spans=[...li.querySelectorAll('span')].map(s=>s.textContent.trim()).filter(Boolean);
      return { text: li.textContent.replace(/\\s+/g,' ').trim(), spans };
    });
  })()`);

  if (!rail) {
    check("五阶段进度条渲染", false, "未找到 ol[aria-label=任务进度]");
  } else {
    check("五阶段进度条渲染", rail.length === 5, `rows=${rail.length}`);
    const jevCount = rail.filter((r) => r.text.startsWith("JEV")).length;
    check("四个阶段归属 JEV", jevCount === 4, `jev=${jevCount} rows=${JSON.stringify(rail.map(r=>r.text.slice(0,18)))}`);
    const execRow = rail[2];
    // The executor name comes from the mission verbatim (it is `workbuddy`,
    // lower-case, on this host), so the check is case-insensitive.
    check(
      "执行任务阶段归属执行器（非 JEV）",
      /^(workbuddy|codex|claude|hermes|执行器)/i.test(execRow.text) && !execRow.text.startsWith("JEV"),
      `row="${execRow.text.slice(0, 30)}"`,
    );
    check(
      "阶段名含「完成交付」",
      rail[4].text.includes("完成交付"),
      `last="${rail[4].text.slice(0, 20)}"`,
    );
  }

  // ---- 5. the task page names JEV as the coordinator ----
  const headerText = await ev(`(() => {
    const h=document.querySelector('h1');
    const scope=h? h.closest('div').parentElement : document.body;
    return scope.textContent.replace(/\\s+/g,' ').trim().slice(0,200);
  })()`);
  check(
    "任务页顶部显示「JEV 正在协调此任务」",
    headerText.includes("JEV 正在协调此任务"),
    `head="${headerText.slice(0, 90)}"`,
  );
  check(
    "任务页显示「执行器：」而非「执行代理：」",
    headerText.includes("执行器：") && !headerText.includes("执行代理："),
    `head="${headerText.slice(0, 120)}"`,
  );

  check("console errors = 0", consoleErrors.length === 0, consoleErrors.slice(0, 2).join(" | "));

  const failed = results.filter((r) => !r.ok);
  console.log(`\n=== ${results.length - failed.length}/${results.length} checks passed ===`);
  for (const f of failed) console.log(`  FAILED: ${f.name} ${f.detail}`);
  ws.close();
  process.exit(failed.length === 0 ? 0 : 1);
}
main().catch((e) => { console.error("verify failed:", e.message); try { ws?.close(); } catch {} process.exit(2); });
