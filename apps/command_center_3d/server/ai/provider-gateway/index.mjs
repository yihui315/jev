// JEV Phase C — Unified Provider Gateway: public surface (MOCK-ONLY).
//
// Re-exports the building blocks so callers import from one place. Everything
// here operates offline against mock providers; no real API calls are made.

export { PROVIDER_MODELS, MOCK_DISCLAIMER, resolvePreferred, indexModels } from "./catalog.mjs";
export { sha256Hex, hashInput, hashOutput, finalizeSuccess, finalizeError, GATEWAY_RESULT_SCHEMA, DATA_MODE, IS_REAL, MOCK_DISCLAIMER as PROTOCOL_MOCK_DISCLAIMER } from "./protocol.mjs";
export { computeMockCost, emptyUsage } from "./pricing.mjs";
export { createMockProvider } from "./mock-providers.mjs";
export { createCircuitBreaker, BREAKER_STATES } from "./circuit-breaker.mjs";
export { createMetrics } from "./metrics.mjs";
export { createEvidenceArchive } from "./evidence-archive.mjs";
export { createSwitchLogger } from "./logging.mjs";
export { selectModel, ROUTING_POLICIES } from "./router.mjs";
export { createGateway, GATEWAY_ERROR_CODES, DEFAULT_TIMEOUT_MS } from "./gateway.mjs";
export { runComparison, buildSyntheticDataset } from "./comparison.mjs";
