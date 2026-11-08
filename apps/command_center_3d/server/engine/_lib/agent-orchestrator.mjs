import { handleAiChat } from "./ai-core.mjs";
import { appendTrustedEvent } from "./paper-ledger.mjs";

const ALLOWED_SYMBOLS = new Set(["BTCUSDT", "ETHUSDT", "ORDIUSDT", "1000SATSUSDT"]);

const AI_ROLES = [
  {
    id: "SPOTTER",
    title: "机会发现",
    instruction: "扫描给定快照，最多提出 3 个候选机会。只允许使用快照中出现的交易对和事实字段。没有可靠机会就返回空数组。输出 JSON：{\"opportunities\":[{\"symbol\":\"BTCUSDT\",\"setup\":\"...\",\"evidence\":[\"引用具体快照字段\"],\"missing_data\":[\"...\"],\"watch_only\":true}]}。不得虚构价格、成交、大额交易或链上事件。"
  },
  {
    id: "PRIOR",
    title: "市场先验",
    instruction: "基于快照及 SPOTTER 结果判断市场状态和先验假设。区分事实与推断，不得把模型置信度冒充统计概率。输出 JSON：{\"regime\":\"risk_on|risk_off|range|unknown\",\"prior_confidence\":0.0,\"evidence\":[\"...\"],\"counter_evidence\":[\"...\"],\"unknowns\":[\"...\"]}。"
  },
  {
    id: "EDGE",
    title: "优势验证",
    instruction: "审查候选机会是否存在可证伪的统计优势。必须考虑手续费、价差、滑点、流动性、样本量、OOS 和对照基准。不要编造胜率、收益率或样本数。输出 JSON：{\"verdict\":\"RESEARCH|WATCH|REJECT\",\"symbol\":\"BTCUSDT或空字符串\",\"side\":\"BUY|SELL|WATCH\",\"hypothesis\":\"...\",\"edge_source\":\"...\",\"entry_trigger\":\"...\",\"invalidation\":\"...\",\"costs_to_test\":[\"...\"],\"required_evidence\":[\"...\"]}。"
  },
  {
    id: "CLOSER",
    title: "退出与复盘",
    instruction: "作为最后一道独立审查，审视前面所有结果、确定性 KELLY 计算和 TAKER 计划。指出最脆弱的假设，给出退出/失效条件和下一步验证。不能批准真实订单，不能要求绕过风控。输出 JSON：{\"decision\":\"HOLD_RESEARCH|REJECT|MONITOR\",\"exit_conditions\":[\"...\"],\"failure_modes\":[\"...\"],\"next_validation\":[\"...\"],\"final_risk_note\":\"...\"}。"
  }
];

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store, max-age=0",
      "x-content-type-options": "nosniff"
    }
  });
}

function parseAgentObject(text) {
  const clean = String(text || "").replace(/```(?:json)?/gi, "").trim();
  const start = clean.indexOf("{");
  const end = clean.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    const value = JSON.parse(clean.slice(start, end + 1));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (_) {
    return null;
  }
}

function wilsonLowerBound(winRate, sampleCount, z = 1.96) {
  const n = Number(sampleCount);
  const p = Number(winRate);
  if (!(n > 0) || !(p >= 0 && p <= 1)) return null;
  const z2 = z * z;
  const denominator = 1 + z2 / n;
  const centre = p + z2 / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p) / n) + z2 / (4 * n * n));
  return Math.max(0, (centre - margin) / denominator);
}

export function computeFractionalKelly(metrics) {
  const m = metrics && typeof metrics === "object" ? metrics : {};
  const sampleCount = Number(m.sampleCount || m.trades || 0);
  const oosTrades = Number(m.oosTrades || 0);
  const winRate = Number(m.winRate);
  const avgWinPct = Number(m.avgWinPct);
  const avgLossPct = Math.abs(Number(m.avgLossPct));
  const reasons = [];

  if (m.strategyPass !== true || m.oosPass !== true) reasons.push("策略未同时通过历史与 OOS 放行门禁");
  if (sampleCount < 100) reasons.push("全样本有效交易数不足 100");
  if (oosTrades < 30) reasons.push("OOS 有效交易数不足 30");
  if (!Number.isFinite(winRate) || winRate <= 0 || winRate >= 1) reasons.push("缺少有效的样本胜率");
  if (!(avgWinPct > 0) || !(avgLossPct > 0)) reasons.push("缺少正向平均盈利/亏损幅度");

  if (reasons.length) {
    return { status: "BLOCKED", allocationPct: 0, fullKellyPct: 0, conservativeWinRate: null, sampleCount, oosTrades, reasons };
  }

  const conservativeWinRate = wilsonLowerBound(winRate, sampleCount);
  const payoffRatio = avgWinPct / avgLossPct;
  const fullKelly = conservativeWinRate - ((1 - conservativeWinRate) / payoffRatio);
  const quarterKelly = Math.max(0, fullKelly * 0.25);
  const allocationPct = Math.min(0.10, quarterKelly);

  if (!(allocationPct > 0)) {
    return {
      status: "BLOCKED",
      allocationPct: 0,
      fullKellyPct: fullKelly,
      conservativeWinRate,
      payoffRatio,
      sampleCount,
      oosTrades,
      reasons: ["使用 95% Wilson 胜率下界计算后，Kelly 优势不为正"]
    };
  }

  return {
    status: "READY",
    method: "quarter-kelly-wilson-lower-bound",
    allocationPct,
    fullKellyPct: fullKelly,
    conservativeWinRate,
    payoffRatio,
    sampleCount,
    oosTrades,
    capPct: 0.10,
    reasons: ["仅作仓位上限建议；仍需经过独立确定性风险闸门"]
  };
}

export function buildTakerPlan(edge, kelly, snapshot) {
  const symbol = String(edge && edge.symbol || "");
  const side = String(edge && edge.side || "WATCH").toUpperCase();
  const market = snapshot && snapshot.structuredMarket || {};
  const book = market.orderBook || {};
  const capturedAt = Number(book.capturedAt || 0);
  const ageMs = capturedAt > 0 ? Math.max(0, Date.now() - capturedAt) : Infinity;
  const bestAsk = Number((book.asks || [])[0] && book.asks[0][0] || 0);
  const bestBid = Number((book.bids || [])[0] && book.bids[0][0] || 0);

  if (String(edge && edge.verdict || "") !== "RESEARCH") {
    return { status: "BLOCKED", symbol, side, autoSubmit: false, reason: "EDGE 未给出 RESEARCH 结论，TAKER 不得创建提案" };
  }
  if (!kelly || kelly.status !== "READY" || !(kelly.allocationPct > 0)) {
    return { status: "BLOCKED", symbol, side, autoSubmit: false, reason: "KELLY 未放行仓位，TAKER 不得创建委托" };
  }
  if (!ALLOWED_SYMBOLS.has(symbol)) {
    return { status: "BLOCKED", symbol, side, autoSubmit: false, reason: "交易对不在模拟交易白名单" };
  }
  if (side !== "BUY") {
    return { status: "BLOCKED", symbol, side, autoSubmit: false, reason: "当前仅允许 TAKER 提出经过验证的现货买入候选；卖出必须由持仓与减仓规则单独验证" };
  }
  if (market.selectedSymbol !== symbol || ageMs > 3000 || !(bestAsk > 0) || !(bestBid > 0)) {
    return { status: "BLOCKED", symbol, side, autoSubmit: false, reason: "缺少同一交易对的 3 秒内有效结构化盘口" };
  }

  const equity = Number((market.account || {}).equity || 0);
  if (!(equity > 0)) return { status: "BLOCKED", symbol, side, autoSubmit: false, reason: "模拟账户净值未知" };
  const targetNotional = equity * Math.min(0.10, kelly.allocationPct);
  return {
    status: "PROPOSAL_ONLY",
    symbol,
    side,
    referenceAsk: bestAsk,
    referenceBid: bestBid,
    targetNotional,
    suggestedQty: targetNotional / bestAsk,
    autoSubmit: false,
    requiresManualConfirmation: true,
    note: "仅生成纸面交易提案；实际提交仍须经过 paper-engine 的最新盘口、余额、敞口和熔断检查"
  };
}

function isStringArray(value, max = 12) {
  return Array.isArray(value) && value.length <= max && value.every(item => typeof item === "string" && item.length <= 500);
}

function roleValid(role, value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (role === "SPOTTER") {
    return Array.isArray(value.opportunities)
      && value.opportunities.length <= 3
      && value.opportunities.every(item =>
        item && typeof item === "object"
        && ALLOWED_SYMBOLS.has(String(item.symbol || ""))
        && typeof item.setup === "string"
        && isStringArray(item.evidence, 8)
        && isStringArray(item.missing_data, 8)
        && item.watch_only === true
      );
  }
  if (role === "PRIOR") {
    return ["risk_on", "risk_off", "range", "unknown"].includes(value.regime)
      && isStringArray(value.evidence)
      && isStringArray(value.counter_evidence)
      && isStringArray(value.unknowns)
      && (value.prior_confidence == null || (Number.isFinite(Number(value.prior_confidence)) && Number(value.prior_confidence) >= 0 && Number(value.prior_confidence) <= 1));
  }
  if (role === "EDGE") {
    return ["RESEARCH", "WATCH", "REJECT"].includes(value.verdict)
      && (value.symbol === "" || ALLOWED_SYMBOLS.has(String(value.symbol || "")))
      && ["BUY", "SELL", "WATCH"].includes(value.side)
      && typeof value.invalidation === "string"
      && (value.hypothesis == null || typeof value.hypothesis === "string")
      && isStringArray(value.costs_to_test || [])
      && isStringArray(value.required_evidence || []);
  }
  if (role === "CLOSER") {
    return ["HOLD_RESEARCH", "REJECT", "MONITOR"].includes(value.decision)
      && isStringArray(value.exit_conditions)
      && isStringArray(value.failure_modes || [])
      && isStringArray(value.next_validation || [])
      && (value.final_risk_note == null || typeof value.final_risk_note === "string");
  }
  return false;
}

async function sha256Hex(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * V4.3: `trustContext` is supplied ONLY by the HTTP route after a trusted
 * service signature has been verified. When present, provider credentials come
 * exclusively from that server-side context, so a browser-supplied apiKey is
 * ignored. When absent (offline unit tests calling the handler directly) the
 * legacy behaviour is preserved, but the real route never reaches it.
 */
export async function handleAgentResearch(request, aiHandler = handleAiChat, env = null, trustContext = null) {
  if (request.method !== "POST") return json({ error: "仅支持 POST" }, 405);
  const origin = request.headers.get("Origin");
  const requestOrigin = new URL(request.url).origin;
  if (origin && origin !== requestOrigin) return json({ error: "跨站请求已拒绝" }, 403);
  if (!(request.headers.get("content-type") || "").toLowerCase().includes("application/json")) {
    return json({ error: "请求必须使用 application/json" }, 415);
  }

  let body;
  try {
    const raw = await request.text();
    if (raw.length > 30000) return json({ error: "研究快照过大" }, 413);
    body = JSON.parse(raw);
  } catch (_) {
    return json({ error: "JSON 请求体无效" }, 400);
  }

  const snapshot = body.snapshot && typeof body.snapshot === "object" ? body.snapshot : null;
  if (!snapshot || JSON.stringify(snapshot).length > 12000) return json({ error: "缺少有效市场快照或快照过大" }, 400);
  const suppliedRunId = String(body.runId || "");
  const runId = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(suppliedRunId)
    ? suppliedRunId
    : crypto.randomUUID();
  const startedAt = new Date().toISOString();
  const snapshotHash = await sha256Hex(JSON.stringify(snapshot));
  const recordRunEvent = async (eventType, payload) => {
    if (!env) return false;
    return appendTrustedEvent(request, env, {
      eventId: eventType + ":" + runId,
      eventType,
      payload: { runId, snapshotHash, ...payload }
    });
  };
  await recordRunEvent("agent.run.started", {
    provider: String(body.provider || ""),
    model: String(body.model || ""),
    promptVersion: "nr-agent-research-v1",
    startedAt,
    snapshotSource: (trustContext && trustContext.snapshotSource) || "browser-request-untrusted",
    snapshot
  });
  const trustedCredentials = trustContext && trustContext.providerCredentials || null;
  const providerConfig = {
    provider: body.provider,
    model: body.model,
    apiKey: trustedCredentials ? trustedCredentials.apiKey : body.apiKey,
    region: trustedCredentials ? trustedCredentials.region : body.region,
    workspaceId: trustedCredentials ? trustedCredentials.workspaceId : body.workspaceId,
    thinkingMode: body.provider === "minimax" ? "disabled" : undefined,
    maxTokens: 8192
  };

  const outputs = {};
  const usage = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
  const aiUrl = new URL("/api/ai/chat", request.url).toString();

  for (const role of AI_ROLES) {
    const context = {
      marketSnapshot: snapshot,
      priorAgentResults: outputs
    };
    const content = [
      "你正在执行 Night Researcher 六角色研究流程。",
      "只允许使用输入快照中的可观察事实；未知就明确写 unknown。禁止编造行情、订单簿、链上事件、资金流、成交或回测结果。",
      "输出必须是一个 JSON 对象，不要 Markdown 代码块，不要在 JSON 之外输出文字。",
      "AGENT_ID: " + role.id,
      "AGENT_TITLE: " + role.title,
      "ROLE_INSTRUCTIONS: " + role.instruction,
      "INPUT_CONTEXT_JSON:",
      JSON.stringify(context)
    ].join("\n");

    const internalRequest = new Request(aiUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "origin": requestOrigin
      },
      body: JSON.stringify({
        ...providerConfig,
        messages: [{ role: "user", content }]
      })
    });

    let response;
    try {
      response = await aiHandler(internalRequest);
    } catch (_) {
      await recordRunEvent("agent.run.failed", { failedAgent: role.id, error: "AI 请求失败", partial: outputs, completedAt: new Date().toISOString() });
      return json({ ok: false, runId, snapshotHash, startedAt, agent: role.id, error: "AI 请求失败", partial: outputs, autoExecution: false }, 502);
    }
    const result = await response.json();
    if (!response.ok || !result.ok) {
      await recordRunEvent("agent.run.failed", {
        failedAgent: role.id,
        error: result.error || "AI 研究步骤失败",
        providerErrorCode: result.code || null,
        finishReason: result.finishReason || null,
        diagnostics: result.diagnostics || null,
        partial: outputs,
        completedAt: new Date().toISOString()
      });
      return json({
        ok: false,
        runId,
        snapshotHash,
        startedAt,
        agent: role.id,
        error: result.error || "AI 研究步骤失败",
        providerErrorCode: result.code || null,
        finishReason: result.finishReason || null,
        diagnostics: result.diagnostics || null,
        partial: outputs,
        autoExecution: false
      }, response.status || 502);
    }

    const parsed = parseAgentObject(result.text);
    if (!roleValid(role.id, parsed)) {
      await recordRunEvent("agent.run.failed", {
        failedAgent: role.id,
        error: "模型没有返回符合约定的结构化 JSON",
        partial: outputs,
        completedAt: new Date().toISOString()
      });
      return json({ ok: false, runId, snapshotHash, startedAt, agent: role.id, error: "模型没有返回符合约定的结构化 JSON；本轮停止，不生成执行提案", partial: outputs, autoExecution: false }, 502);
    }

    outputs[role.id] = { ...parsed, title: role.title, model: result.model };
    if (result.usage) {
      usage.prompt_tokens += Number(result.usage.prompt_tokens || 0);
      usage.completion_tokens += Number(result.usage.completion_tokens || 0);
      usage.total_tokens += Number(result.usage.total_tokens || 0);
    }

    if (role.id === "EDGE") {
      outputs.KELLY = computeFractionalKelly(null); // Only a server-verified research ledger may feed Kelly; client snapshots are untrusted.
      outputs.TAKER = buildTakerPlan(outputs.EDGE, outputs.KELLY, snapshot);
    }
  }

  const completedAt = new Date().toISOString();
  const runResult = {
    runId,
    snapshotHash,
    promptVersion: "nr-agent-research-v1",
    provider: String(body.provider || ""),
    requestedModel: String(body.model || ""),
    startedAt,
    completedAt,
    outputs,
    usage,
    safety: {
      autoExecution: false,
      liveTrading: false,
      paperOrderCreated: false,
      note: "AI 只产生研究结论；KELLY 与 TAKER 为确定性程序；任何模拟订单都必须由用户点击并重新通过 paper-engine 风控"
    }
  };
  await recordRunEvent("risk.decision", {
    decision: outputs.TAKER && outputs.TAKER.status ? outputs.TAKER.status : "BLOCKED",
    kellyStatus: outputs.KELLY && outputs.KELLY.status ? outputs.KELLY.status : "BLOCKED",
    allocationPct: outputs.KELLY && Number.isFinite(Number(outputs.KELLY.allocationPct)) ? Number(outputs.KELLY.allocationPct) : 0,
    autoSubmit: false,
    liveTrading: false,
    rationale: "研究结果不是订单授权；任何模拟订单仍需用户主动提交并重新通过确定性 paper-engine 风控"
  });
  await recordRunEvent("agent.run.completed", runResult);
  return json({
    ok: true,
    runId,
    snapshotHash,
    promptVersion: "nr-agent-research-v1",
    provider: String(body.provider || ""),
    requestedModel: String(body.model || ""),
    startedAt,
    completedAt,
    durationMs: Math.max(0, Date.parse(completedAt) - Date.parse(startedAt)),
    runMode: "research-only",
    roles: ["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"],
    outputs,
    usage,
    safety: runResult.safety
  });
}
