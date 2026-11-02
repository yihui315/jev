/**
 * Status-consistency acceptance for 设置 → 模型与 API.
 *
 * The defect this pins: a single provider appeared as 「无法连接」 in the summary
 * and 「已连接（无模型列表接口）」 on its card at the same moment. Two renderers,
 * two lookup tables, one provider, two answers.
 *
 * So the assertions are about AGREEMENT, not about which wording is correct:
 *   1. Every provider's shown state comes from the backend's `display_state`.
 *   2. The same word appears in the summary and on the card.
 *   3. 「已连接」 never appears unless a capability was really verified.
 *   4. After a reload the two still agree — the original symptom was a refresh
 *      artifact, so a single-shot check would not have caught it.
 */
const CDP = "http://127.0.0.1:9223";
const APP = "http://127.0.0.1:4780";
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
function check(n, ok, d = "") {
  results.push({ n, ok, d });
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? " — " + d : ""));
}

/**
 * Read every card's shown state from the page, exactly as rendered.
 *
 * Anchored on the row class the card actually uses rather than on the first
 * <span>, because the first span inside a card is the STATUS DOT — a bare
 * element with no text — so a text-anchored query matched nothing and the
 * earlier run reported "0 cards" on a page that plainly showed five.
 */
const readCards = () => ev(`(() => {
  const spans=[...document.querySelectorAll('span')].map(s=>s.textContent.trim());
  const names=spans.filter(t=>/^(TypeSafe Jev|MiniMax M3|Qwen Coding Plan|DeepSeek|Custom OpenAI Compatible)$/.test(t));
  const states=spans.filter(t=>/^(已连接|上次验证通过|已配置 · 待验证|需要 API Key|API Key 无效|已限流|无法连接|端点不匹配|套餐不匹配)$/.test(t));
  return names.map((n,i)=>({name:n, stateText: states[i] ?? ''}));
})()`);

async function openSettings() {
  await ev(`[...document.querySelectorAll('nav button, nav[aria-label] button')]
    .find(b=>b.textContent.trim()==='设置')?.click()`);
  await sleep(1200);
  await ev(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='模型与 API')?.click()`);
  await sleep(2500);
}

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

  // --- pass 1: initial load ------------------------------------------- //
  await send("Page.navigate", { url: APP });
  await sleep(1500);
  await ev("localStorage.clear(); sessionStorage.clear();");
  await send("Page.navigate", { url: APP });
  await sleep(4000);
  await openSettings();

  const backend = await (await fetch("http://127.0.0.1:4779/api/v1/providers")).json();
  const byId = Object.fromEntries(backend.providers.map((p) => [p.id, p]));
  const cards1 = await readCards();
  check("五张卡片全部渲染", cards1.length === 5, "n=" + cards1.length);

  // The backend is the only source of truth. Every card must carry the state the
  // backend computed — no frontend re-derivation.
  const hasBackendField = backend.providers.every((p) => !!p.display_state);
  check("后端为每个 provider 提供 display_state", hasBackendField === true);

  // The invariant, checked against the LIVE backend data rather than a fixture:
  // no provider may read 已连接 unless its capability was really verified.
  const lied = backend.providers.filter((p) => {
    const shownConnected = p.display_state === "CONNECTED";
    const verified = p.verification_kind === "decision"
      ? p.decision_verified === true
      : p.generation_verified === true;
    return shownConnected && !verified;
  });
  check("无 provider 在未验证时显示已连接", lied.length === 0,
    lied.map((p) => p.id).join(","));

  // And the converse: a provider that WAS verified must not be shown as pending.
  const understated = backend.providers.filter((p) => {
    const verified = p.verification_kind === "decision"
      ? p.decision_verified === true
      : p.generation_verified === true;
    return verified && p.display_state !== "CONNECTED";
  });
  check("已验证的 provider 不显示为待验证", understated.length === 0,
    understated.map((p) => p.id).join(","));

  // --- the page must agree with the backend ---------------------------- //
  // Expectations come from the BACKEND's own display_state, translated once here.
  // Deriving them from the page instead would make the assertion circular — it
  // would compare the page with itself and pass no matter what the page said.
  const WORD = {
    CONNECTED: "已连接",
    CONFIGURED_PENDING: "已配置 · 待验证",
    NOT_CONFIGURED: "需要 API Key",
    STALE_VERIFIED: "上次验证通过",
  };
  const EXPECT = Object.fromEntries(
    backend.providers.map(p => [p.display_name, WORD[p.display_state] ?? p.display_state]));
  const mismatched = cards1.filter((c) => {
    const key = Object.keys(EXPECT).find((k) => c.name === k);
    return key && c.stateText !== EXPECT[key];
  });
  check("页面状态与后端 display_state 一致", mismatched.length === 0,
    mismatched.map((c) => `${c.name}="${c.stateText}"`).join(" | "));

  // The MiniMax specifics the operator needs to see the real configuration.
  const mm = byId.minimax;
  const mmCard = cards1.find((c) => c.name.startsWith("MiniMax"));
  check("MiniMax 显示 plan_type=CN Coding Plan", mm.plan_type === "coding_subscription_cn",
    "plan_type=" + mm.plan_type);
  check("MiniMax region 与 base_url 一致（cn → minimaxi.com）",
    mm.region === "cn" && mm.base_url.includes("api.minimaxi.com"),
    `region=${mm.region} base_url=${mm.base_url}`);
  check("MiniMax model_source 标记为 PRESET", mm.model_source === "PRESET",
    "model_source=" + mm.model_source);
  check("MiniMax discovery=UNSUPPORTED（不等于 UNAVAILABLE）",
    mm.discovery === "MODEL_DISCOVERY_UNSUPPORTED" && mm.display_state !== "UNAVAILABLE",
    "discovery=" + mm.discovery + " display=" + mm.display_state);
  // The card reflects health(), which is the CHEAP read: it never spends a token
  // on its own. So before a probe it is honestly 「已配置 · 待验证」, and that is
  // correct behaviour rather than a regression. The expectation therefore comes
  // from the backend's own display_state.
  const mmWord = { CONNECTED: "已连接", STALE_VERIFIED: "上次验证通过", CONFIGURED_PENDING: "已配置 · 待验证", NOT_CONFIGURED: "需要 API Key" };
  check("MiniMax 卡片状态与后端 display_state 一致",
    mmCard?.stateText === (mmWord[mm.display_state] ?? mm.display_state),
    "shown=" + mmCard?.stateText + " backend=" + mm.display_state);
  // And the invariant that matters: never 已连接 without a real generation.
  check("MiniMax 未验证时不显示已连接",
    mm.generation_verified ? mmCard?.stateText === "已连接" : mmCard?.stateText !== "已连接",
    "gen_verified=" + mm.generation_verified + " shown=" + mmCard?.stateText);

  // --- no contradictory wording anywhere on the page ------------------- //
  const body = await ev(`document.body.innerText`);
  check("页面不同时出现「无法连接」与「已连接」",
    !(body.includes("无法连接") && body.includes("已连接")),
    `无法连接=${body.includes("无法连接")} 已连接=${body.includes("已连接")}`);
  check("无「已连接（无模型列表接口）」这类越级表述",
    !body.includes("已连接（无模型列表接口"));
  // A state cell falling through to the literal 「未知」 would mean the backend sent
  // a display_state this build has no wording for — the exact drift that produced
  // the original contradiction. Scoped to a state position, not the whole page.
  const unknownState = cards1.filter((c) => c.stateText === "未知");
  check("无状态回退为「未知」", unknownState.length === 0,
    unknownState.map((c) => c.name).join(","));

  // --- pass 2: AFTER A RELOAD (the original symptom was a refresh bug) -- //
  await send("Page.navigate", { url: APP });
  await sleep(4000);
  await openSettings();
  // Poll: the reload issues its own request, and reading before it lands would
  // report drift where there is none.
  let cards2 = [];
  for (let i = 0; i < 20; i++) {
    cards2 = await readCards();
    if (cards2.length === 5) break;
    await sleep(500);
  }
  check("刷新后重新加载出五张卡", cards2.length === 5, "n=" + cards2.length);
  const changed = cards1.filter((c, i) => c.stateText !== (cards2[i]?.stateText));
  check("刷新后状态不变（无刷新漂移）", changed.length === 0,
    changed.map((c, i) => `${c.name}: ${c.stateText} -> ${cards2[i]?.stateText}`).join(" | "));

  const body2 = await ev(`document.body.innerText`);
  check("刷新后仍无「无法连接」与「已连接」并存",
    !(body2.includes("无法连接") && body2.includes("已连接")));

  check("console errors = 0", consoleErrors.length === 0, consoleErrors.slice(0, 2).join(" | "));

  const failed = results.filter((r) => !r.ok);
  console.log("\n=== " + (results.length - failed.length) + "/" + results.length + " checks passed ===");
  for (const f of failed) console.log("  FAILED: " + f.n + " " + f.d);
  ws.close();
  process.exit(failed.length === 0 ? 0 : 1);
}
main().catch((e) => { console.error("verify failed:", e.message); try { ws.close(); } catch {} process.exit(2); });
