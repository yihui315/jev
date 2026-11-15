// JEV V1.6 / P2 — evidence 层聚合导出 + Provider 清单权威「只声明、不接线」登记。
// 纯 ESM，无副作用、不触网；不 import runtime.mjs / ai-core.mjs / provider-adapter.mjs /
// key-store.mjs（后者会触发凭据读取）。本文件只登记权威来源字符串，不产出第五套清单。

export * from "./inference-evidence.mjs";
export * from "./multi-model-provider.mjs";

// 决策期 modelId 的唯一权威来源（Track B）：仅登记，不 import、不调用、不接线。
export const MODEL_ID_AUTHORITY = "server/ai/runtime.mjs:providerRegistryFromEnv";
export const BINDING = "DECLARED_NOT_WIRED";

// 未来接线目标，本轮无授权：ai_market_contexts 表已存在（0006_ai_decisions.sql），
// 冻结 runner 推迟 P3 解冻；hash_scope 本轮仅 raw_upstream_bytes。
export const BINDING_TARGETS = Object.freeze({
  ai_market_contexts_table: "DECLARED_NOT_WIRED",
  day14_runner: "DECLARED_NOT_WIRED",
  hash_scope: "raw_upstream_bytes",
});

// 六角色映射由主理人裁定，本轮不自定、不接线。
export const SIX_ROLE_MAPPING = "OWNER-DECIDED / UNVERIFIED";
