// JEV Phase C — Mock pricing tables + cost computation.
//
// MOCK-ONLY. These are SIMULATED unit prices (USD per 1K tokens) used solely to
// demonstrate token metering + cost recording in the offline gateway. They are
// NOT real provider quotes and must never be presented as such. Every computed
// cost carries provenance:"MOCK_PRICING" and an explicit "no real spend" note.

/**
 * Compute a mock cost from a token-usage object and a model pricing fixture.
 * @param {{promptPer1k:number, completionPer1k:number}} pricing
 * @param {{promptTokens?:number, completionTokens?:number, totalTokens?:number}} usage
 * @returns {{usd:number, currency:string, provenance:string, note:string}}
 */
export function computeMockCost(pricing, usage) {
  const promptTokens = Number(usage && usage.promptTokens) || 0;
  const completionTokens = Number(usage && usage.completionTokens) || 0;
  const promptCost = (promptTokens / 1000) * (pricing.promptPer1k || 0);
  const completionCost = (completionTokens / 1000) * (pricing.completionPer1k || 0);
  const usd = Math.round((promptCost + completionCost) * 1e8) / 1e8;
  return {
    usd,
    currency: "USD",
    provenance: "MOCK_PRICING",
    note: "estimated from mock pricing table; no real spend incurred",
  };
}

/** Build an empty usage object with explicit MOCK provenance. */
export function emptyUsage() {
  return { promptTokens: 0, completionTokens: 0, totalTokens: 0, provenance: "MOCK_ESTIMATE" };
}
