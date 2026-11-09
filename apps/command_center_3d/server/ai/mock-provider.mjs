// JEV V1.6 Track B - deterministic mock provider.
//
// Purpose: exercise the ENTIRE decision pipeline (adapter response parsing ->
// schema validation -> risk gate -> persistence -> execution idempotency)
// without any network call and without any possibility of being mistaken for a
// real inference.
//
// Properties
//   * Deterministic: the same logical input yields byte-identical output.
//   * Explicitly labelled: data_mode is always 'mock' and the model id is
//     'mock-deterministic-v1'; a mock decision can never be written as 'real'.
//   * Shaped exactly like an OpenAI chat/completions response, so the real
//     parsing path is what gets tested (no shortcut).

export const MOCK_MODEL_ID = "mock-deterministic-v1";
export const MOCK_PROVIDER_ID = "mock";

function utf8(input) {
  return new TextEncoder().encode(String(input));
}

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", utf8(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function pick(bytes, i) { return bytes[i % bytes.length]; }

/** 0.001 .. 10 units, 8dp, derived from the seed - never a fixed planted size. */
function quantityForPrice(priceUsd, targetNotionalUsd) {
  if (!(priceUsd > 0)) return 0;
  const raw = targetNotionalUsd / priceUsd;
  const rounded = Math.round(raw * 1e8) / 1e8;
  return rounded > 0 ? rounded : 0.00000001;
}

/**
 * Build a deterministic decision for a real, stored market context.
 * `context` must carry {marketContextId, symbol, markPriceUsd, priceTs}.
 */
export async function generateMockDecision({ strategyId, context, seedExtra }) {
  const marketContextId = String(context.marketContextId);
  const symbol = String(context.symbol).toUpperCase();
  const price = Number(context.markPriceUsd);
  const seed = await sha256Hex([strategyId, symbol, marketContextId, String(context.priceTs), String(price), String(seedExtra || "")].join("|"));
  const bytes = [...seed.match(/.{2}/g)].map((h) => parseInt(h, 16));

  const bucket = pick(bytes, 0) % 3;
  const action = bucket === 0 ? "HOLD" : bucket === 1 ? "BUY" : "SELL";
  const confidence = Math.round((0.5 + (pick(bytes, 2) / 255) * 0.45) * 1e6) / 1e6;
  const base = {
    schemaVersion: "jev.ai.decision/v1",
    strategyId,
    symbol,
    action,
    side: null,
    orderType: null,
    quantity: null,
    limitPrice: null,
    confidence,
    reason: "mock deterministic decision (seed " + seed.slice(0, 16) + ")",
    riskFlags: ["MOCK_DATA"],
    marketContextId,
    validForSeconds: 60,
  };
  if (action !== "HOLD") {
    const targetNotional = 20 + (pick(bytes, 1) % 60);
    base.side = action;
    base.orderType = "MARKET";
    base.quantity = quantityForPrice(price, targetNotional);
  }
  return {
    decision: base,
    meta: {
      seed,
      dataMode: "mock",
      modelId: MOCK_MODEL_ID,
      providerId: MOCK_PROVIDER_ID,
      promptVersion: "jev.ai.prompt.mock/v1",
    },
  };
}

/** Wrap a decision in an OpenAI-shaped completion body (parsed by the real path). */
export function mockChatCompletion(decision) {
  return {
    id: "mockcmpl-" + String(decision.marketContextId).slice(-12),
    object: "chat.completion",
    model: MOCK_MODEL_ID,
    choices: [{ index: 0, message: { role: "assistant", content: JSON.stringify(decision) }, finish_reason: "stop" }],
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    data_mode: "mock",
  };
}
