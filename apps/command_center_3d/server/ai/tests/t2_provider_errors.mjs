// T2 - provider adapter: request shape, SSRF guard, and the seven error classes.

import {
  buildRequest, buildModelsRequest, classifyResponse, performRequest, describeRequest,
  assertAllowedUrl, ERROR_CODES,
} from "../provider-adapter.mjs";
import { assert, assertEqual, assertRejects } from "./lib/harness.mjs";

export const id = "t2_provider_errors";
export const title = "Provider adapter: request build, SSRF guard, timeout/error classification";

const OPENAI = {
  providerId: "openai", providerType: "openai", baseUrl: "https://api.openai.com/v1",
  modelId: "gpt-4o-mini", secretRef: "OPENAI_API_KEY", timeoutMs: 45000, maxTokens: 256, enabled: true,
};
const COMPAT = {
  providerId: "deepseek", providerType: "openai-compatible", baseUrl: "https://api.deepseek.com/v1",
  modelId: "deepseek-chat", secretRef: "DEEPSEEK_API_KEY", timeoutMs: 30000, maxTokens: 256, enabled: true,
};
const OLLAMA = {
  providerId: "ollama", providerType: "ollama", baseUrl: "http://127.0.0.1:11434",
  modelId: "llama3.1", secretRef: "OLLAMA_API_KEY", timeoutMs: 20000, maxTokens: 256, enabled: true,
};
const MSG = [{ role: "user", content: "return json" }];
const KEY = "test-key-value-0123456789";

export async function run(suite) {
  await suite.check("openai request targets /v1/chat/completions with bearer header", () => {
    const spec = buildRequest(OPENAI, MSG, { credential: KEY, maxTokens: 8 });
    assertEqual(spec.url, "https://api.openai.com/v1/chat/completions");
    assertEqual(spec.method, "POST");
    assertEqual(spec.headers.authorization, "Bearer " + KEY);
    assertEqual(spec.body.stream, false);
    assertEqual(spec.body.model, "gpt-4o-mini");
    assertEqual(spec.body.max_tokens, 8);
    return spec.url;
  });

  await suite.check("openai-compatible baseUrl is appended, never doubled", () => {
    assertEqual(buildRequest(COMPAT, MSG, { credential: KEY }).url, "https://api.deepseek.com/v1/chat/completions");
    const already = { ...COMPAT, baseUrl: "https://ai.example.com/v1/chat/completions" };
    assertEqual(buildRequest(already, MSG, { credential: KEY }).url, "https://ai.example.com/v1/chat/completions");
    return "no double /v1";
  });

  await suite.check("ollama loopback endpoint needs no key and sends no authorization", () => {
    const spec = buildRequest(OLLAMA, MSG, {});
    assertEqual(spec.url, "http://127.0.0.1:11434/v1/chat/completions");
    assert(!("authorization" in spec.headers), "authorization must be absent for a keyless local Ollama");
    return "no auth header";
  });

  await suite.check("SSRF guard rejects http to a non-loopback host", async () => {
    const err = await assertRejects(() => assertAllowedUrl("http://evil.example.com/v1", { providerType: "ollama" }), "PROVIDER_URL_FORBIDDEN");
    assert(/http is only allowed/.test(err.message), err.message);
    return err.code;
  });

  await suite.check("SSRF guard rejects private https hosts", async () => {
    await assertRejects(() => assertAllowedUrl("https://192.168.1.10/v1", { providerType: "openai" }), "PROVIDER_URL_FORBIDDEN");
    await assertRejects(() => assertAllowedUrl("https://169.254.169.254/latest", { providerType: "openai" }), "PROVIDER_URL_FORBIDDEN");
    return "RFC1918 + metadata host rejected";
  });

  await suite.check("missing credential fails closed before any request", async () => {
    const err = await assertRejects(() => buildRequest(OPENAI, MSG, {}), "NOT_CONNECTED");
    assert(/credential not resolved/.test(err.message), err.message);
    return err.code;
  });

  await suite.check("401/403 classify as AUTH_FAILED", () => {
    for (const status of [401, 403]) {
      const r = classifyResponse(OPENAI, status, { error: { message: "invalid api key" } }, 120, {});
      assertEqual(r.ok, false);
      assertEqual(r.code, ERROR_CODES.AUTH_FAILED);
      assertEqual(r.evidence.http_status, status);
      assertEqual(r.retryable, false);
    }
    return "401 + 403";
  });

  await suite.check("404 classifies as MODEL_UNAVAILABLE with evidence", () => {
    const r = classifyResponse(OPENAI, 404, { error: { message: "model gpt-x not found", code: "model_not_found" } }, 90, {});
    assertEqual(r.code, ERROR_CODES.MODEL_UNAVAILABLE);
    assertEqual(r.evidence.http_status, 404);
    assert(/model/.test(r.evidence.error_message), r.evidence.error_message);
    return r.code;
  });

  await suite.check("400 with a model error classifies as MODEL_UNAVAILABLE, other 400 as INVALID_RESPONSE", () => {
    assertEqual(classifyResponse(COMPAT, 400, { error: { message: "unsupported model: foo" } }, 50, {}).code, ERROR_CODES.MODEL_UNAVAILABLE);
    assertEqual(classifyResponse(COMPAT, 400, { error: { message: "bad temperature" } }, 50, {}).code, ERROR_CODES.INVALID_RESPONSE);
    return "model-vs-shape split";
  });

  await suite.check("429 classifies as RATE_LIMITED and is retryable", () => {
    const r = classifyResponse(OPENAI, 429, { error: { message: "rate limit" } }, 30, {});
    assertEqual(r.code, ERROR_CODES.RATE_LIMITED);
    assertEqual(r.retryable, true);
    return r.code;
  });

  await suite.check("5xx classifies as SERVER_ERROR", () => {
    const r = classifyResponse(OPENAI, 503, { error: { message: "overloaded" } }, 800, {});
    assertEqual(r.code, ERROR_CODES.SERVER_ERROR);
    assertEqual(r.evidence.http_status, 503);
    return r.code;
  });

  await suite.check("200 with non-JSON / empty content is INVALID_RESPONSE", () => {
    assertEqual(classifyResponse(OPENAI, 200, null, 100, {}).code, ERROR_CODES.INVALID_RESPONSE);
    assertEqual(classifyResponse(OPENAI, 200, { choices: [] }, 100, {}).code, ERROR_CODES.INVALID_RESPONSE);
    assertEqual(classifyResponse(OPENAI, 200, { choices: [{ message: { content: "" } }] }, 100, {}).code, ERROR_CODES.INVALID_RESPONSE);
    return "3 malformed shapes";
  });

  await suite.check("successful 200 returns content and is not retryable", () => {
    const body = { choices: [{ message: { content: '{"ok":true}' }, finish_reason: "stop" }], usage: { total_tokens: 3 } };
    const r = classifyResponse(OPENAI, 200, body, 100, {});
    assertEqual(r.ok, true);
    assertEqual(r.content, '{"ok":true}');
    assertEqual(r.retryable, false);
    return "content parsed";
  });

  await suite.check("timeout classifies as TIMEOUT via timeout flag and via elapsed budget", () => {
    assertEqual(classifyResponse(OPENAI, null, null, 45001, { timedOut: true }).code, ERROR_CODES.TIMEOUT);
    assertEqual(classifyResponse(OPENAI, null, null, 45001, {}).code, ERROR_CODES.TIMEOUT);
    return "both paths";
  });

  await suite.check("generic network failure is NOT_CONNECTED, not TIMEOUT", () => {
    const r = classifyResponse(OPENAI, null, null, 120, { networkError: "TypeError" });
    assertEqual(r.code, ERROR_CODES.NOT_CONNECTED);
    assertEqual(r.evidence.network_error, "TypeError");
    return r.code;
  });

  await suite.check("performRequest converts an AbortError into timedOut data", async () => {
    const aborting = () => Promise.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    const res = await performRequest(buildRequest(OPENAI, MSG, { credential: KEY, maxTokens: 8 }), { fetchImpl: aborting });
    assertEqual(res.timedOut, true);
    assertEqual(res.httpStatus, null);
    assertEqual(classifyResponse(OPENAI, res.httpStatus, res.body, res.elapsedMs, { timedOut: res.timedOut }).code, ERROR_CODES.TIMEOUT);
    return "timedOut=true";
  });

  await suite.check("describeRequest redacts the credential (only presence is exposed)", () => {
    const spec = buildRequest(OPENAI, MSG, { credential: KEY });
    const described = describeRequest(spec);
    const text = JSON.stringify(described);
    assert(!text.includes(KEY), "credential leaked into described request");
    assertEqual(described.authorization_present, true);
    assertEqual(described.header_names.includes("authorization"), true);
    return "header value never serialized";
  });

  await suite.check("models discovery URL is /v1/models", () => {
    assertEqual(buildModelsRequest(OPENAI, { credential: KEY }).url, "https://api.openai.com/v1/models");
    return "/v1/models";
  });
}
