// T5 - mock/real isolation. A real decision is impossible without a resolved
// credential, and a mock decision can never be stored or reported as real.

import { listProviders, testProvider, createDecision } from "../runtime.mjs";
import { validBuy, makeContext, makeDb } from "./lib/fixtures.mjs";
import { assert, assertEqual, assertRejects } from "./lib/harness.mjs";

export const id = "t5_data_mode";
export const title = "data_mode isolation: BLOCKED real path, explicit mock path";

const NOW = Date.parse("2026-11-09T12:00:00.000Z");

export async function run(suite) {
  await suite.check("empty env: providers verdict is BLOCKED, nothing claims CONNECTED", () => {
    const p = listProviders({});
    assertEqual(p.verdict, "BLOCKED");
    assertEqual(p.credentials, 0);
    assertEqual(p.verified_connected, 0);
    assert(!p.providers.some((x) => x.status === "CONNECTED"), "no provider may claim CONNECTED");
    assert(p.providers.every((x) => x.connectivity === "UNVERIFIED"), "connectivity must stay UNVERIFIED");
    return "providers=" + p.providers.length;
  });

  await suite.check("real decision without a credential is refused and writes nothing", async () => {
    const db = makeDb();
    const err = await assertRejects(
      () => createDecision({ db, env: {}, strategyId: "strat-real", symbol: "BTCUSDT", providerId: "openai", dataMode: "real", marketContext: makeContext(NOW), nowMs: NOW }),
      "PROVIDER_BLOCKED"
    );
    assert(/No credential/.test(err.message), err.message);
    const count = await db.prepare("SELECT COUNT(*) AS n FROM ai_decisions").first();
    assertEqual(Number(count.n), 0);
    return err.code + " rows=0";
  });

  await suite.check("mock decision is labelled mock in D1 and in the result", async () => {
    const db = makeDb();
    const r = await createDecision({ db, env: {}, strategyId: "strat-mock", symbol: "BTCUSDT", dataMode: "mock", marketContext: makeContext(NOW), nowMs: NOW });
    assertEqual(r.dataMode, "mock");
    assertEqual(r.decision.dataMode, "mock");
    assertEqual(r.decision.providerId, "mock");
    assertEqual(r.decision.modelId, "mock-deterministic-v1");
    const raw = await db.prepare("SELECT data_mode, provider_id FROM ai_decisions WHERE decision_id = ?").bind(r.decision.decisionId).first();
    assertEqual(raw.data_mode, "mock");
    assertEqual(raw.provider_id, "mock");
    return "data_mode=mock";
  });

  await suite.check("mock mode may not borrow a real provider id", async () => {
    const db = makeDb();
    await assertRejects(
      () => createDecision({ db, env: {}, strategyId: "strat-mock", symbol: "BTCUSDT", providerId: "openai", dataMode: "mock", marketContext: makeContext(NOW), nowMs: NOW }),
      "DATA_MODE_MISMATCH"
    );
    return "DATA_MODE_MISMATCH";
  });

  await suite.check("provider test with no credential is BLOCKED and sends no request", async () => {
    let sent = 0;
    const spy = async () => { sent += 1; throw new Error("must not be called"); };
    const r = await testProvider({ env: {}, providerId: "openai", fetchImpl: spy });
    assertEqual(r.status, "BLOCKED");
    assertEqual(r.code, "SECRET_REF_UNRESOLVED");
    assertEqual(sent, 0);
    assertEqual(r.evidence.http_status, null);
    return "BLOCKED, requests=0";
  });

  await suite.check("provider test reports AUTH_FAILED from a real 401, never CONNECTED", async () => {
    const env = { OPENAI_API_KEY: "unit-test-not-a-real-key-abc" };
    const fetch401 = async () => ({ status: 401, ok: false, text: async () => JSON.stringify({ error: { message: "invalid key" } }) });
    const r = await testProvider({ env, providerId: "openai", fetchImpl: fetch401 });
    assertEqual(r.status, "FAILED");
    assertEqual(r.code, "AUTH_FAILED");
    assertEqual(r.evidence.http_status, 401);
    return r.code;
  });

  await suite.check("provider test reports CONNECTED only on a real 200 with content", async () => {
    const env = { OPENAI_API_KEY: "unit-test-not-a-real-key-abc" };
    const fetch200 = async () => ({ status: 200, ok: true, text: async () => JSON.stringify({ choices: [{ message: { content: '{"ok":true}' }, finish_reason: "stop" }], usage: { total_tokens: 2 } }) });
    const r = await testProvider({ env, providerId: "openai", fetchImpl: fetch200 });
    assertEqual(r.status, "CONNECTED");
    assertEqual(r.code, null);
    assertEqual(r.evidence.http_status, 200);
    assert(!("last4" in r), "provider test must not echo credential fragments");
    return "CONNECTED on 200 only";
  });

  await suite.check("real decision path works end-to-end on an injected transport and is stored as real", async () => {
    const db = makeDb();
    const env = { OPENAI_API_KEY: "unit-test-not-a-real-key-abc" };
    const fetchOk = async () => ({
      status: 200, ok: true,
      text: async () => JSON.stringify({ choices: [{ message: { content: JSON.stringify(validBuy({ marketContextId: "mctx-real-0001", quantity: 0.0005 })) } }] }),
    });
    const ctx = makeContext(NOW, { marketContextId: "mctx-real-0001" });
    const r = await createDecision({ db, env, strategyId: "strat-real", symbol: "BTCUSDT", providerId: "openai", dataMode: "real", marketContext: ctx, fetchImpl: fetchOk, nowMs: NOW });
    assertEqual(r.dataMode, "real");
    assertEqual(r.decision.dataMode, "real");
    assertEqual(r.decision.providerId, "openai");
    assertEqual(r.decision.modelId, "gpt-4o-mini");
    assert(r.decision.requestPayloadSha256 && r.decision.requestPayloadSha256.length === 64, "request hash missing");
    assert(r.decision.inputSnapshotSha256.length === 64, "input hash missing");
    assert(r.decision.responsePayloadSha256.length === 64, "response hash missing");
    return "data_mode=real, hashes present";
  });

  await suite.check("real decision surfaces provider timeout as TIMEOUT and writes nothing", async () => {
    const db = makeDb();
    const env = { OPENAI_API_KEY: "unit-test-not-a-real-key-abc" };
    const aborting = async () => { throw Object.assign(new Error("aborted"), { name: "AbortError" }); };
    const err = await assertRejects(
      () => createDecision({ db, env, strategyId: "strat-real", symbol: "BTCUSDT", providerId: "openai", dataMode: "real", marketContext: makeContext(NOW), fetchImpl: aborting, nowMs: NOW }),
      "TIMEOUT"
    );
    const count = await db.prepare("SELECT COUNT(*) AS n FROM ai_decisions").first();
    assertEqual(Number(count.n), 0);
    return err.code + " rows=0";
  });
}
