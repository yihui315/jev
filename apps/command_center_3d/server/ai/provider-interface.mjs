// JEV AI Provider Gateway - logical target only.
//
// This module is a REGISTRY + ATTRIBUTION + RESULT-NORMALIZATION contract. It is
// stdlib-only (zero imports, zero dependencies), performs NO network egress, has
// NO credentials, and NEVER makes a real inference call. Every provider is
// `callable: false` because there are 0 real keys in this repo and outbound AI
// calls are disabled.
//
// HONESTY RULES (do not weaken):
//   * "registered" is a code fact (the entry exists in a runtime registry).
//   * "configured" is a credential fact. With 0 real keys it is false for ALL.
//   * "verified" means "an in-repo test exercises that EXACT adapter". It is a
//     local code-coverage claim, NOT connectivity and NOT a real API call.
//   * "callable" means "a real, credentialed, egress-allowed call can be made".
//     It is false for ALL entries here.
//   * 已注册 != 已真实调用. A successful response may be attributed ONLY to the
//     exact providerId that was requested. Never silently re-attribute.
//
// Per-runtime counts are a HARD ruling: python 4 / node 6 / frozen_engine 2.
// They are NEVER referred to collectively as "four providers".

// ---------------------------------------------------------------------------
// Deep freeze
// ---------------------------------------------------------------------------

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const key of Object.keys(value)) {
      deepFreeze(value[key]);
    }
  }
  return value;
}

// ---------------------------------------------------------------------------
// Provider registry (three runtimes, frozen)
// ---------------------------------------------------------------------------
//
// sourcePath values were read from the repo (not invented):
//   packages/jcc_core/providers/generation.py   - 4 concrete generation classes
//   apps/command_center_3d/server/ai/runtime.mjs - 6 providerRegistryFromEnv rows
//   apps/command_center_3d/server/engine/_lib/ai-core.mjs - 2 hardcoded providers
//
// configEnvVar values were read from the repo (not invented):
//   Python  -> packages/jcc_core/secret_store.py:ENV_FOR_PROVIDER
//   Node    -> apps/command_center_3d/server/ai/runtime.mjs:secretRef per row
//   Frozen  -> null (apiKey is read from the request body in ai-core.mjs:97)
//
// `verified` decision (conservative, fail-closed):
//   * python 4: verified=true. packages/jcc_core/tests/test_model_runtime.py
//     directly imports and instantiates/asserts each of the 4 concrete classes
//     (MiniMaxProvider, QwenCodingPlanProvider, DeepSeekProvider,
//     CustomOpenAICompatibleProvider). Citable in-repo test per exact adapter.
//   * node 6: verified=false, cite nothing. The rows are configuration entries
//     in runtime.mjs resolved through ONE shared transport (provider-adapter.mjs).
//     In-repo tests (t2/t5/t7/t9/e2e_real_engine) exercise that transport with
//     synthetic fixtures and only enumerate the registry via listProviders();
//     no test binds a distinct in-repo assertion to each exact registry row.
//     Fail-closed: unverified.
//   * frozen_engine 2: verified=false, cite nothing. Zero in-repo tests import
//     handleAiChat / ai-core.mjs. Fail-closed: unverified.
//   `verifiedBy` is null whenever verified=false (cite nothing).
//
// `configured` is false for ALL entries: 0 real credentials present in repo,
// env-var NAMES are recorded but never resolved to a value.

const PYTHON_SOURCE = "packages/jcc_core/providers/generation.py";
const NODE_SOURCE = "apps/command_center_3d/server/ai/runtime.mjs";
const FROZEN_SOURCE = "apps/command_center_3d/server/engine/_lib/ai-core.mjs";
const PYTHON_TEST = "packages/jcc_core/tests/test_model_runtime.py";

function makeEntry(o) {
  return Object.freeze({
    id: o.id,
    runtime: o.runtime,
    displayName: o.displayName,
    sourcePath: o.sourcePath,
    configEnvVar: o.configEnvVar,
    // four separate status fields
    registered: true,
    configured: false,
    verified: o.verified,
    callable: false,
    // citation only when verified; null = cite nothing
    verifiedBy: o.verified ? o.verifiedBy : null,
    note: o.note,
  });
}

export const PROVIDER_RUNTIMES = deepFreeze({
  // --- python: 4 concrete generation adapters ---------------------------
  python: [
    makeEntry({
      id: "python:minimax", runtime: "python", displayName: "MiniMax M3",
      sourcePath: PYTHON_SOURCE, configEnvVar: "MINIMAX_API_KEY",
      verified: true, verifiedBy: PYTHON_TEST,
      note: "MiniMaxProvider. Concrete class; exercised by test_model_runtime.py. Registered, not configured, not callable.",
    }),
    makeEntry({
      id: "python:qwen_coding_plan_cn", runtime: "python", displayName: "Qwen Coding Plan",
      sourcePath: PYTHON_SOURCE, configEnvVar: "QWEN_API_KEY",
      verified: true, verifiedBy: PYTHON_TEST,
      note: "QwenCodingPlanProvider. Concrete class; exercised by test_model_runtime.py. Registered, not configured, not callable.",
    }),
    makeEntry({
      id: "python:deepseek", runtime: "python", displayName: "DeepSeek",
      sourcePath: PYTHON_SOURCE, configEnvVar: "DEEPSEEK_API_KEY",
      verified: true, verifiedBy: PYTHON_TEST,
      note: "DeepSeekProvider. Concrete class; exercised by test_model_runtime.py. Registered, not configured, not callable.",
    }),
    makeEntry({
      id: "python:custom", runtime: "python", displayName: "Custom OpenAI Compatible",
      sourcePath: PYTHON_SOURCE, configEnvVar: "CUSTOM_API_KEY",
      verified: true, verifiedBy: PYTHON_TEST,
      note: "CustomOpenAICompatibleProvider. Concrete class; exercised by test_model_runtime.py. Registered, not configured, not callable.",
    }),
  ],
  // --- node: 6 providerRegistryFromEnv rows ------------------------------
  node: [
    makeEntry({
      id: "node:openai", runtime: "node", displayName: "OpenAI (Node runtime)",
      sourcePath: NODE_SOURCE, configEnvVar: "OPENAI_API_KEY",
      verified: false, verifiedBy: null,
      note: "runtime.mjs providerRegistryFromEnv row. Config entry over shared provider-adapter.mjs transport; no per-row in-repo test. Fail-closed unverified.",
    }),
    makeEntry({
      id: "node:minimax", runtime: "node", displayName: "MiniMax (Node runtime)",
      sourcePath: NODE_SOURCE, configEnvVar: "MINIMAX_API_KEY",
      verified: false, verifiedBy: null,
      note: "runtime.mjs providerRegistryFromEnv row. Config entry over shared transport; no per-row in-repo test. Fail-closed unverified.",
    }),
    makeEntry({
      id: "node:qwen", runtime: "node", displayName: "Qwen (Node runtime)",
      sourcePath: NODE_SOURCE, configEnvVar: "DASHSCOPE_API_KEY",
      verified: false, verifiedBy: null,
      note: "runtime.mjs providerRegistryFromEnv row (secretRef DASHSCOPE_API_KEY). Config entry over shared transport; no per-row in-repo test. Fail-closed unverified.",
    }),
    makeEntry({
      id: "node:deepseek", runtime: "node", displayName: "DeepSeek (Node runtime)",
      sourcePath: NODE_SOURCE, configEnvVar: "DEEPSEEK_API_KEY",
      verified: false, verifiedBy: null,
      note: "runtime.mjs providerRegistryFromEnv row. Config entry over shared transport; no per-row in-repo test. Fail-closed unverified.",
    }),
    makeEntry({
      id: "node:openrouter", runtime: "node", displayName: "OpenRouter (Node runtime)",
      sourcePath: NODE_SOURCE, configEnvVar: "OPENROUTER_API_KEY",
      verified: false, verifiedBy: null,
      note: "runtime.mjs providerRegistryFromEnv row. Config entry over shared transport; no per-row in-repo test. Fail-closed unverified.",
    }),
    makeEntry({
      id: "node:ollama", runtime: "node", displayName: "Ollama local (Node runtime)",
      sourcePath: NODE_SOURCE, configEnvVar: "OLLAMA_API_KEY",
      verified: false, verifiedBy: null,
      note: "runtime.mjs providerRegistryFromEnv row (optionalSecret local-only). Config entry over shared transport; no per-row in-repo test. Fail-closed unverified.",
    }),
  ],
  // --- frozen_engine: 2 hardcoded providers ------------------------------
  frozen_engine: [
    makeEntry({
      id: "frozen_engine:minimax", runtime: "frozen_engine", displayName: "MiniMax (frozen engine)",
      sourcePath: FROZEN_SOURCE, configEnvVar: null,
      verified: false, verifiedBy: null,
      note: "ai-core.mjs handleAiChat hardcoded provider branch (minimax). apiKey comes from the request body, not an env var. Zero in-repo tests. Fail-closed unverified.",
    }),
    makeEntry({
      id: "frozen_engine:qwen", runtime: "frozen_engine", displayName: "Qwen (frozen engine)",
      sourcePath: FROZEN_SOURCE, configEnvVar: null,
      verified: false, verifiedBy: null,
      note: "ai-core.mjs handleAiChat hardcoded provider branch (qwen / Model Studio). apiKey comes from the request body, not an env var. Zero in-repo tests. Fail-closed unverified.",
    }),
  ],
});

export const STATUS_FIELDS = Object.freeze(["registered", "configured", "verified", "callable"]);

export function allProviderEntries() {
  const out = [];
  for (const runtime of Object.keys(PROVIDER_RUNTIMES)) {
    for (const entry of PROVIDER_RUNTIMES[runtime]) out.push(entry);
  }
  return out;
}

export function findProviderEntry(id) {
  return allProviderEntries().find((e) => e.id === id) || null;
}

// ---------------------------------------------------------------------------
// Error taxonomy + documented result policy
// ---------------------------------------------------------------------------

// Uniform error taxonomy. These are the only errorClass / code values the
// gateway emits. None of them is evidence of a real provider call.
export const ERROR_TAXONOMY = Object.freeze({
  PROVIDER_TIMEOUT: "The call exceeded its timeout budget; no result is recorded.",
  PROVIDER_RETRY_EXHAUSTED: "Bounded retries were exhausted; no result is recorded.",
  PROVIDER_AUTH_MISSING: "No credential was resolvable (fail-closed); nothing was sent.",
  PROVIDER_ATTRIBUTION_MISMATCH: "Response providerId is missing, blank, or differs from the requested providerId; success is never recorded and attribution is never re-assigned.",
  PROVIDER_BAD_RESPONSE: "Response violated the contract (e.g. a requested model is missing).",
  PROVIDER_CALL_DISABLED: "A real call was attempted but is disabled: 0 credentials and egress forbidden; 已注册 != 已真实调用.",
});

export const ERROR_CODES = Object.freeze(Object.keys(ERROR_TAXONOMY));

// Documented timeout / retry / cost / raw-response-retention policy. These are
// declarative fields on the uniform envelope, not config that enables egress.
export const RESULT_POLICY = Object.freeze({
  // timeout: default per-call budget in milliseconds (read-only documentation).
  defaultTimeoutMs: 45000,
  // retry: bounded retries; only retryable failure classes, never auth errors.
  maxRetries: 2,
  retryBackoff: "bounded exponential over retryable classes only",
  // cost: metered-only. cost is null unless a real meter reported it; never
  // invent 0, because a fabricated 0 reads as "free".
  costPolicy: "metered-only",
  costUnit: "USD (informational; no spend incurred in this repo)",
  // raw response: retained by reference, never by embedding the raw body.
  rawResponseRetention: "reference-only: rawResponseRef is an opaque pointer; raw bodies are not stored in the envelope",
  // egress: the gateway never performs outbound AI-provider calls.
  egress: "disabled",
  // provenance: which function owns attribution (normalization must not change it).
  attributionOwner: "assertResponseAttribution",
});

// ---------------------------------------------------------------------------
// normalizeResult - uniform envelope
// ---------------------------------------------------------------------------
//
// Builds one canonical, frozen result envelope. It must NEVER mutate
// attribution: `providerId` is copied verbatim from the input and is never
// re-derived from model, usage, cost, or any other field. Attribution is owned
// solely by assertResponseAttribution(); this function only formats the result.
// The input object is never mutated.
export function normalizeResult(raw) {
  const r = raw || {};
  const errorClass = r.errorClass == null ? null : String(r.errorClass);
  const providerId = Object.prototype.hasOwnProperty.call(r, "providerId") ? r.providerId : null;
  const envelope = {
    schema: "jev/provider-result/1",
    ok: r.ok === true && errorClass == null,
    // -- attribution (verbatim; never mutated here) --
    providerId,
    // -- uniform payload fields --
    model: r.model == null ? null : r.model,
    latencyMs: Number.isFinite(r.latencyMs) ? r.latencyMs : 0,
    usage: r.usage == null ? null : r.usage,
    cost: r.cost == null ? null : r.cost,
    rawResponseRef: r.rawResponseRef == null ? null : r.rawResponseRef,
    finishReason: r.finishReason == null ? null : r.finishReason,
    errorClass,
    code: errorClass, // alias for callers that read `code`
    // -- documented policy fields --
    timeoutMs: Number.isFinite(r.timeoutMs) ? r.timeoutMs : RESULT_POLICY.defaultTimeoutMs,
    retry: Object.freeze({
      attempted: Number.isFinite(r.retryAttempted) ? r.retryAttempted : 0,
      max: RESULT_POLICY.maxRetries,
      exhausted: r.retryExhausted === true,
    }),
    costTracked: r.costTracked === true,
    rawResponseRetention: RESULT_POLICY.rawResponseRetention,
    attributionOwner: RESULT_POLICY.attributionOwner,
  };
  return Object.freeze(envelope);
}

// ---------------------------------------------------------------------------
// assertResponseAttribution - HARD attribution guard
// ---------------------------------------------------------------------------
//
// A successful response may be attributed ONLY to the exact providerId that was
// requested. Missing, blank, or mismatched provider id in the response MUST
// produce an error result with code PROVIDER_ATTRIBUTION_MISMATCH: never
// silently re-attribute, never record success. Also reject a response whose
// model is missing when a model was requested.
export function assertResponseAttribution(request, response) {
  const req = request || {};
  const res = response || {};
  const blank = (v) => v === undefined || v === null || (typeof v === "string" && v.trim().length === 0);

  const requestedId = req.providerId;
  const requestedModel = req.model;
  const responseId = res.providerId;

  const base = {
    providerId: blank(requestedId) ? null : requestedId,
    model: blank(requestedModel) ? null : requestedModel,
  };

  // 1) Response provider id must be present, non-blank, and EXACTLY equal to
  //    the requested provider id. Otherwise: error, never success.
  if (blank(responseId) || blank(requestedId) || responseId !== requestedId) {
    return Object.freeze({
      ...normalizeResult({ ...base, ok: false, errorClass: "PROVIDER_ATTRIBUTION_MISMATCH" }),
      requestedProviderId: blank(requestedId) ? null : requestedId,
      responseProviderId: blank(responseId) ? null : responseId,
      detail: "response.providerId is missing, blank, or differs from the requested providerId; attribution refused, success not recorded",
    });
  }

  // 2) If a model was requested, the response must carry a model. (A returned
  //    model string may legitimately be a versioned id, so only PRESENCE is
  //    enforced here, never equality.)
  if (!blank(requestedModel) && blank(res.model)) {
    return Object.freeze({
      ...normalizeResult({ ...base, ok: false, errorClass: "PROVIDER_BAD_RESPONSE" }),
      detail: "model was requested but response.model is missing",
    });
  }

  // 3) Positive control: exact match. Success is recorded ONLY for the
  //    requested providerId. Attribution is never changed.
  return Object.freeze({
    ...normalizeResult({
      ...base,
      ok: true,
      errorClass: null,
      model: blank(res.model) ? base.model : res.model,
      latencyMs: res.latencyMs,
      usage: res.usage,
      cost: res.cost,
      rawResponseRef: res.rawResponseRef,
      finishReason: res.finishReason,
    }),
  });
}

// ---------------------------------------------------------------------------
// callProvider - fail-closed call gate (PROVIDER_CALL_DISABLED path)
// ---------------------------------------------------------------------------
//
// There is NO real call implementation here on purpose: 0 credentials, egress
// disabled, all entries callable=false. A known, registered provider therefore
// always returns PROVIDER_CALL_DISABLED; an unknown id returns
// PROVIDER_BAD_RESPONSE. No network is ever touched.
export function callProvider(request) {
  const id = request && request.providerId;
  const entry = findProviderEntry(id);
  if (!entry) {
    return normalizeResult({ ok: false, providerId: id == null ? null : id, errorClass: "PROVIDER_BAD_RESPONSE" });
  }
  return normalizeResult({ ok: false, providerId: id, errorClass: "PROVIDER_CALL_DISABLED" });
}

// ---------------------------------------------------------------------------
// PROVIDER_GATEWAY_TARGETS - logical target nodes
// ---------------------------------------------------------------------------
//
// These are LOGICAL targets, not proven connections. connected=false for all:
// nothing was actually proven reachable and no egress is allowed. Each note
// separates "verified in repo" (adapter/source exists, read from disk) from
// "logical-only". M3 is UNVERIFIED: no concrete M3 adapter exists in the repo
// ("MiniMax-M3" appears only as a MODEL ID string), so none is invented.
export const PROVIDER_GATEWAY_TARGETS = deepFreeze({
  minimax: Object.freeze({
    id: "minimax",
    label: "MiniMax",
    connected: false,
    status: "ADAPTER_PRESENT_NOT_CONNECTED",
    sourcePaths: Object.freeze([
      PYTHON_SOURCE,   // MiniMaxProvider
      NODE_SOURCE,     // minimax registry row
      FROZEN_SOURCE,   // minimax branch in handleAiChat
    ]),
    note: "VERIFIED (in-repo): concrete MiniMax adapters exist in all three runtimes (paths above). LOGICAL/PROVEN: not connected - 0 real keys, egress disabled, real API call NOT_RUN. 已注册 != 已真实调用.",
  }),
  m3: Object.freeze({
    id: "m3",
    label: "M3",
    connected: false,
    status: "UNVERIFIED",
    sourcePaths: Object.freeze([]),
    note: "UNVERIFIED: no concrete 'M3' adapter exists in the repo. 'MiniMax-M3' appears only as a MODEL ID string (runtime.mjs modelId, ai-core.mjs MINIMAX_MODEL, generation.py known_models). It is a model name, not a registered provider/adapter. Logical-only; no adapter invented.",
  }),
  bailian: Object.freeze({
    id: "bailian",
    label: "阿里百炼 (Bailian / Alibaba Model Studio)",
    connected: false,
    status: "ADAPTER_PRESENT_NOT_CONNECTED",
    sourcePaths: Object.freeze([
      PYTHON_SOURCE,   // QwenCodingPlanProvider -> coding.dashscope.aliyuncs.com
      FROZEN_SOURCE,   // qwen Model Studio endpoint -> maas.aliyuncs.com
    ]),
    note: "VERIFIED (in-repo): QwenCodingPlanProvider (generation.py) and the frozen-engine qwen/Model Studio branch (ai-core.mjs) are concrete Bailian/DashScope adapters (paths above). LOGICAL/PROVEN: not connected - 0 real keys, egress disabled, real API call NOT_RUN. 已注册 != 已真实调用.",
  }),
  jev_internal_scheduler: Object.freeze({
    id: "jev_internal_scheduler",
    label: "JEV internal scheduler",
    connected: false,
    status: "LOGICAL_ONLY",
    sourcePaths: Object.freeze([
      "packages/jcc_core/model_router.py",                              // ModelRouter: local decision/routing
      "apps/command_center_3d/server/engine/_lib/agent-orchestrator.mjs", // orchestration entry
    ]),
    note: "LOGICAL_ONLY: no concrete in-repo module implements an external 'JEV internal scheduler' AI target. Nearest components are local routing/orchestration (ModelRouter, agent-orchestrator, paths above), NOT a proven external connection. connected=false; 真实调用 NOT_RUN.",
  }),
});

// ---------------------------------------------------------------------------
// validateRegistry - hard assertions (4 / 6 / 2, four status fields)
// ---------------------------------------------------------------------------
export function validateRegistry() {
  const expected = { python: 4, node: 6, frozen_engine: 2 };
  const runtimeNames = Object.keys(PROVIDER_RUNTIMES);
  if (runtimeNames.length !== 3) {
    throw new Error("PROVIDER_RUNTIMES must have exactly 3 runtimes");
  }
  const counts = {};
  for (const runtime of Object.keys(expected)) {
    const entries = PROVIDER_RUNTIMES[runtime];
    if (!Array.isArray(entries) || entries.length !== expected[runtime]) {
      throw new Error("count mismatch for runtime " + runtime + ": expected " + expected[runtime] + ", got " + (entries ? entries.length : "none"));
    }
    counts[runtime] = entries.length;
    for (const entry of entries) {
      if (entry.runtime !== runtime) {
        throw new Error("entry.runtime mismatch: " + entry.id);
      }
      for (const field of STATUS_FIELDS) {
        if (typeof entry[field] !== "boolean") {
          throw new Error("entry " + entry.id + " missing boolean status field: " + field);
        }
      }
      if (!Object.isFrozen(entry)) {
        throw new Error("entry must be frozen: " + entry.id);
      }
    }
  }
  if (!Object.isFrozen(PROVIDER_RUNTIMES)) {
    throw new Error("PROVIDER_RUNTIMES must be frozen");
  }
  return Object.freeze({ ok: true, counts, total: allProviderEntries().length });
}
