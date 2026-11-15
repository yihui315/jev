// JEV V1.6 Track B - unified AI Provider Adapter.
//
// WHY THIS FILE EXISTS
//   The frozen engine module server/engine/_lib/ai-core.mjs hardcodes exactly
//   two providers (minimax / qwen) and cannot be modified (frozen tree, whole
//   directory sha256 anchor). This adapter is a PARALLEL, new implementation
//   of one OpenAI-wire protocol with per-provider configuration, so OpenAI
//   native, any OpenAI-compatible endpoint (MiniMax / Qwen / DeepSeek / GLM /
//   Kimi / OpenRouter) and a local Ollama share a single code path.
//
// HARD RULES
//   * No credential literal ever appears in this file. A provider record only
//     carries `secretRef` (the NAME of an env var / secret). The value is
//     resolved at call time by runtime.mjs and passed in via options.credential.
//   * The resolved key never enters a response body, a log line, or D1. The
//     only key-derived value the API may return is a last4 digest, produced by
//     describeSecret() below.
//   * Every outbound URL must pass assertAllowedUrl(): https only, except a
//     local Ollama host (127.0.0.1 / localhost / [::1]); private-network
//     addresses are rejected. This is the SSRF guard for user-influenced
//     baseUrl values.
//
// PURE FUNCTIONS (unit-testable, no network)
//   buildRequest(provider, messages, options)
//   buildModelsRequest(provider, options)
//   classifyResponse(provider, httpStatus, body, elapsedMs)
//   assertAllowedUrl(url, provider)
//   describeSecret(value)

export const PROVIDER_TYPES = Object.freeze(["openai", "openai-compatible", "ollama"]);

export const DEFAULT_BASE_URLS = Object.freeze({
  openai: "https://api.openai.com/v1",
  "openai-compatible": "",
  ollama: "http://127.0.0.1:11434",
});

// The seven required error classes. Each one carries real evidence from the
// upstream call (HTTP status / elapsed ms / provider error code), never a
// synthesized verdict.
export const ERROR_CODES = Object.freeze({
  NOT_CONNECTED: "NOT_CONNECTED",
  AUTH_FAILED: "AUTH_FAILED",
  MODEL_UNAVAILABLE: "MODEL_UNAVAILABLE",
  TIMEOUT: "TIMEOUT",
  RATE_LIMITED: "RATE_LIMITED",
  INVALID_RESPONSE: "INVALID_RESPONSE",
  SERVER_ERROR: "SERVER_ERROR",
});

export const RETRYABLE = Object.freeze({
  NOT_CONNECTED: true,
  AUTH_FAILED: false,
  MODEL_UNAVAILABLE: false,
  TIMEOUT: true,
  RATE_LIMITED: true,
  INVALID_RESPONSE: false,
  SERVER_ERROR: true,
});

const ID_RE = /^[A-Za-z0-9._:-]{2,64}$/;
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);
const ALLOWED_ROLES = new Set(["system", "user", "assistant"]);
const MAX_CONTENT_CHARS = 20000;

function providerError(code, detail) {
  const err = new Error(detail || code);
  err.code = code;
  return err;
}

function asInt(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

/**
 * Validate and normalize a provider config record. Throws with a `code` on
 * structural problems, so a malformed config can never become a live call.
 */
export function normalizeProvider(provider) {
  if (!provider || typeof provider !== "object") throw providerError("PROVIDER_INVALID", "provider config required");
  const providerId = String(provider.providerId || "");
  if (!ID_RE.test(providerId)) throw providerError("PROVIDER_INVALID", "providerId must match " + String(ID_RE));
  const providerType = String(provider.providerType || "");
  if (!PROVIDER_TYPES.includes(providerType)) throw providerError("PROVIDER_INVALID", "unsupported providerType: " + providerType);

  const rawBase = provider.baseUrl == null ? "" : String(provider.baseUrl).trim();
  const baseUrl = normalizeBaseUrl(providerType, rawBase);

  const modelId = String(provider.modelId || "").trim();
  const secretRef = provider.secretRef == null ? "" : String(provider.secretRef).trim();
  if (secretRef && !/^[A-Za-z0-9_.:-]{2,80}$/.test(secretRef)) {
    throw providerError("PROVIDER_INVALID", "secretRef is a NAME and must match [A-Za-z0-9_.:-]{2,80}");
  }
  const timeoutMs = asInt(provider.timeoutMs, 45000);
  if (timeoutMs < 1000 || timeoutMs > 120000) throw providerError("PROVIDER_INVALID", "timeoutMs out of range 1000..120000");
  const maxTokens = asInt(provider.maxTokens, 512);
  if (maxTokens < 1 || maxTokens > 8192) throw providerError("PROVIDER_INVALID", "maxTokens out of range 1..8192");

  return {
    providerId,
    providerType,
    baseUrl,
    modelId,
    secretRef,
    timeoutMs,
    maxTokens,
    enabled: provider.enabled !== false,
    capabilities: Object.freeze({
      chat: true,
      models: provider.capabilities && provider.capabilities.models === false ? false : true,
      requiresApiKey: providerType !== "ollama",
      jsonMode: true,
    }),
  };
}

export function normalizeBaseUrl(providerType, rawBase) {
  const fallback = DEFAULT_BASE_URLS[providerType] || "";
  const candidate = (rawBase || fallback).replace(/\/+$/, "");
  if (!candidate) throw providerError("PROVIDER_INVALID", "baseUrl is required for " + providerType);
  let url;
  try { url = new URL(candidate); } catch { throw providerError("PROVIDER_INVALID", "baseUrl is not a valid URL"); }
  assertAllowedUrl(url.toString(), { providerType, baseUrl: candidate });
  return candidate;
}

/**
 * SSRF guard. https is mandatory; http is tolerated ONLY for a local Ollama
 * endpoint on loopback. RFC1918 / link-local / metadata ranges are rejected.
 */
export function assertAllowedUrl(rawUrl, provider) {
  let url;
  try { url = new URL(String(rawUrl)); } catch { throw providerError("PROVIDER_INVALID", "invalid URL"); }
  const host = url.hostname.toLowerCase();
  if (url.protocol === "https:") {
    if (isPrivateHost(host)) throw providerError("PROVIDER_URL_FORBIDDEN", "private host not allowed over https: " + host);
    return true;
  }
  if (url.protocol === "http:") {
    const type = provider && provider.providerType;
    if (type === "ollama" && LOCAL_HOSTS.has(host)) return true;
    throw providerError("PROVIDER_URL_FORBIDDEN", "http is only allowed for a loopback Ollama endpoint");
  }
  throw providerError("PROVIDER_URL_FORBIDDEN", "protocol not allowed: " + url.protocol);
}

function isPrivateHost(host) {
  if (host === "169.254.169.254" || host.endsWith(".internal") || host.endsWith(".local")) return true;
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  if (a === 10) return true;
  if (a === 192 && b === 168) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 127) return true;
  if (a === 169 && b === 254) return true;
  return false;
}

function withV1(baseUrl) {
  const u = new URL(baseUrl);
  const path = u.pathname.replace(/\/+$/, "");
  if (path === "" || path === "/") return baseUrl + "/v1";
  return baseUrl;
}

export function chatCompletionsUrl(provider) {
  const p = normalizeProvider(provider);
  if (/\/chat\/completions$/.test(p.baseUrl)) return p.baseUrl;
  return withV1(p.baseUrl) + "/chat/completions";
}

export function modelsUrl(provider) {
  const p = normalizeProvider(provider);
  if (/\/models$/.test(p.baseUrl)) return p.baseUrl;
  return withV1(p.baseUrl) + "/models";
}

function normalizeMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0) throw providerError("PROVIDER_INVALID", "messages must be a non-empty array");
  if (messages.length > 40) throw providerError("PROVIDER_INVALID", "messages length must be <= 40");
  return messages.map((m, i) => {
    if (!m || typeof m !== "object") throw providerError("PROVIDER_INVALID", "messages[" + i + "] must be an object");
    const role = String(m.role || "");
    if (!ALLOWED_ROLES.has(role)) throw providerError("PROVIDER_INVALID", "messages[" + i + "].role invalid");
    const content = typeof m.content === "string" ? m.content : "";
    if (!content.trim()) throw providerError("PROVIDER_INVALID", "messages[" + i + "].content empty");
    if (content.length > MAX_CONTENT_CHARS) throw providerError("PROVIDER_INVALID", "messages[" + i + "].content too long");
    return { role, content };
  });
}

/**
 * Build the exact HTTP request for a chat completion. No secret is embedded:
 * the caller supplies the resolved key explicitly. The returned `headers`
 * object is the ONLY place a key may appear, and describeRequest() redacts it.
 */
export function buildRequest(provider, messages, options = {}) {
  const p = normalizeProvider(provider);
  if (!p.enabled) throw providerError("PROVIDER_DISABLED", "provider is disabled");
  const model = String(options.model || p.modelId || "").trim();
  if (!model) throw providerError("PROVIDER_INVALID", "modelId is required");
  const credential = options.credential == null ? "" : String(options.credential);
  if (p.capabilities.requiresApiKey && !credential) throw providerError("NOT_CONNECTED", "credential not resolved for secretRef=" + (p.secretRef || "<none>"));
  const maxTokens = Math.min(8192, Math.max(1, asInt(options.maxTokens, p.maxTokens)));
  const temperature = Number.isFinite(Number(options.temperature)) ? Number(options.temperature) : 0.2;

  const headers = { "content-type": "application/json", accept: "application/json" };
  if (credential) headers.authorization = "Bearer " + credential;

  const body = {
    model,
    messages: normalizeMessages(messages),
    stream: false,
    temperature,
    max_tokens: maxTokens,
  };
  if (options.jsonMode !== false) body.response_format = { type: "json_object" };

  return {
    url: chatCompletionsUrl(p),
    method: "POST",
    headers,
    body,
    timeoutMs: Math.min(120000, Math.max(1000, asInt(options.timeoutMs, p.timeoutMs))),
    model,
    providerId: p.providerId,
  };
}

export function buildModelsRequest(provider, options = {}) {
  const p = normalizeProvider(provider);
  const credential = options.credential == null ? "" : String(options.credential);
  if (p.capabilities.requiresApiKey && !credential) throw providerError("NOT_CONNECTED", "credential not resolved for secretRef=" + (p.secretRef || "<none>"));
  const headers = { accept: "application/json" };
  if (credential) headers.authorization = "Bearer " + credential;
  return {
    url: modelsUrl(p), method: "GET", headers, body: null,
    timeoutMs: Math.min(120000, Math.max(1000, asInt(options.timeoutMs, p.timeoutMs))),
    providerId: p.providerId,
  };
}

/**
 * Sanitized view of a request for evidence / logs. Header VALUES are replaced
 * by a presence marker; the URL is included because it is configuration, not
 * a credential.
 */
export function describeRequest(spec) {
  const headerNames = Object.keys(spec.headers || {}).sort();
  return {
    url: spec.url,
    method: spec.method,
    model: spec.model || null,
    header_names: headerNames,
    authorization_present: headerNames.includes("authorization"),
    timeout_ms: spec.timeoutMs ?? null,
    body_keys: spec.body && typeof spec.body === "object" ? Object.keys(spec.body).sort() : [],
    body_chars: spec.body ? JSON.stringify(spec.body).length : 0,
  };
}

/**
 * Execute a prepared request with a hard timeout. Never throws for network
 * failures: they are reported as data so callers classify them uniformly.
 */
export async function performRequest(spec, deps = {}) {
  const fetchImpl = typeof deps.fetchImpl === "function" ? deps.fetchImpl : fetch;
  const started = Date.now();
  const controller = new AbortController();
  const timeoutMs = Math.min(120000, Math.max(1000, asInt(spec.timeoutMs, 45000)));
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const init = { method: spec.method, headers: spec.headers, signal: controller.signal };
    if (spec.body != null) init.body = JSON.stringify(spec.body);
    const res = await fetchImpl(spec.url, init);
    const rawText = await res.text();
    let parsed = null;
    try { parsed = JSON.parse(rawText); } catch { parsed = null; }
    return {
      httpStatus: res.status,
      ok: !!res.ok,
      body: parsed,
      rawText,
      rawChars: rawText.length,
      elapsedMs: Date.now() - started,
      networkError: null,
      timedOut: false,
    };
  } catch (err) {
    const name = err && err.name ? String(err.name) : "Error";
    const timedOut = name === "AbortError" || name === "TimeoutError";
    return {
      httpStatus: null, ok: false, body: null, rawText: "", rawChars: 0,
      elapsedMs: Date.now() - started,
      networkError: name,
      timedOut,
      rawMessage: redactSecrets(String(err && err.message ? err.message : err)).slice(0, 200),
    };
  } finally {
    clearTimeout(timer);
  }
}

/** Mask anything that looks like a bearer token or vendor key inside text. */
export function redactSecrets(text) {
  if (typeof text !== "string") return "";
  return text
    .replace(/\bBearer\s+[A-Za-z0-9._~+/-]{8,}/gi, "Bearer ***")
    .replace(/\bsk-[A-Za-z0-9._-]{8,}/g, "sk-***")
    .replace(/\bgh[pousr]_[A-Za-z0-9]{8,}/g, "gh***")
    .replace(/\bgithub_pat_[A-Za-z0-9_]{8,}/g, "github_pat_***")
    .replace(/\b[A-Za-z0-9_-]{40,}\b/g, "***");
}

/**
 * Only a masked, non-reversible digest of a credential may leave the process.
 * Returns {present, last4}. Never returns the value itself.
 */
export function describeSecret(value) {
  if (typeof value !== "string" || value.length === 0) return { present: false, last4: null };
  return { present: true, last4: value.length >= 8 ? value.slice(-4) : "****" };
}

function extractProviderMessage(body) {
  if (!body || typeof body !== "object") return "";
  const candidates = [
    body.error && body.error.message,
    body.error && body.error.code,
    body.message,
    body.detail,
    body.msg,
  ];
  for (const c of candidates) {
    if (typeof c === "string" && c.trim()) return redactSecrets(c.trim()).slice(0, 240);
  }
  return "";
}

export function extractContent(body) {
  if (!body || typeof body !== "object") return "";
  const choices = Array.isArray(body.choices) ? body.choices : null;
  if (!choices || choices.length === 0) return "";
  const first = choices[0];
  const msg = first && first.message;
  if (msg && typeof msg.content === "string") return msg.content;
  if (first && typeof first.text === "string") return first.text;
  return "";
}

/**
 * Classify an upstream result into exactly one of the seven error classes, or
 * {ok:true}. Evidence carries the real HTTP status, elapsed time and the
 * provider's own message (redacted, truncated).
 */
export function classifyResponse(provider, httpStatus, body, elapsedMs, extra = {}) {
  const p = provider ? normalizeProvider(provider) : null;
  const timeoutMs = p ? p.timeoutMs : 45000;
  const ms = Number.isFinite(Number(elapsedMs)) ? Number(elapsedMs) : null;
  const evidence = {
    http_status: httpStatus == null ? null : Number(httpStatus),
    elapsed_ms: ms,
    timeout_ms: timeoutMs,
    provider_id: p ? p.providerId : null,
    provider_type: p ? p.providerType : null,
    error_message: extractProviderMessage(body),
    body_keys: body && typeof body === "object" && !Array.isArray(body) ? Object.keys(body).slice(0, 12) : [],
  };
  if (extra.networkError || extra.timedOut) evidence.network_error = extra.networkError || "AbortError";
  if (extra.rawChars != null) evidence.response_chars = Number(extra.rawChars) || 0;

  const fail = (code) => ({ ok: false, code, retryable: RETRYABLE[code], error: code, evidence });

  if (httpStatus == null) {
    const timedOut = extra.timedOut === true || extra.networkError === "AbortError" || extra.networkError === "TimeoutError";
    if (timedOut) return fail(ERROR_CODES.TIMEOUT);
    if (ms != null && ms >= timeoutMs) return fail(ERROR_CODES.TIMEOUT);
    return fail(ERROR_CODES.NOT_CONNECTED);
  }
  const status = Number(httpStatus);
  if (status >= 200 && status < 300) {
    if (!body || typeof body !== "object") return fail(ERROR_CODES.INVALID_RESPONSE);
    if (body.error) return fail(ERROR_CODES.INVALID_RESPONSE);
    if (!extractContent(body).trim()) return fail(ERROR_CODES.INVALID_RESPONSE);
    return { ok: true, code: null, retryable: false, evidence, content: extractContent(body), usage: body.usage || null, finish_reason: body.choices?.[0]?.finish_reason ?? null };
  }
  if (status === 401 || status === 403) return fail(ERROR_CODES.AUTH_FAILED);
  if (status === 404) return fail(ERROR_CODES.MODEL_UNAVAILABLE);
  if (status === 429) return fail(ERROR_CODES.RATE_LIMITED);
  if (status >= 500) return fail(ERROR_CODES.SERVER_ERROR);
  if (status === 400 || status === 422) {
    const msg = evidence.error_message.toLowerCase();
    if (msg.includes("model")) return fail(ERROR_CODES.MODEL_UNAVAILABLE);
    return fail(ERROR_CODES.INVALID_RESPONSE);
  }
  return fail(ERROR_CODES.INVALID_RESPONSE);
}
