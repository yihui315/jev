// F-05 独立凭据域测试：AI provider 凭据与交易侧凭据必须落在互不相交的域中，
// 任一域解析失败/受损都不能读取或牵连另一域。MiniMax 历史位置本仓记为 UNVERIFIED。

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  CREDENTIAL_DOMAINS, resolveDomainSecret, loadDomainSecrets, resolveTradingSecret,
  AI_PROVIDER_SECRET_NAMES, MINIMAX_CREDENTIAL_LOCATION,
} from "../../server/ai/credential-domain.mjs";
import { providerRegistryFromEnv, listProviders } from "../../server/ai/runtime.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const RUNTIME_AI_SRC = readFileSync(join(HERE, "../../server/ai/runtime.mjs"), "utf8");

test("F-05 正向：AI_PROVIDER 域可解析其登记的凭据名", () => {
  const r = resolveDomainSecret(CREDENTIAL_DOMAINS.AI_PROVIDER, "OPENAI_API_KEY", { OPENAI_API_KEY: "sk-openai-1234" });
  assert.equal(r.present, true, "AI provider secret resolves");
  assert.equal(r.value, "sk-openai-1234");
  assert.equal(r.domain, "AI_PROVIDER");
  assert.equal(r.crossDomainBlocked, false);
});

test("F-05 负向/变异：AI_PROVIDER 域绝不能读取 TRADING_ 凭据（即使 env 中存在），value 绝不泄露", () => {
  const env = { OPENAI_API_KEY: "sk-openai-1234", TRADING_FOO: "t-secret-leak" };
  const r = resolveDomainSecret(CREDENTIAL_DOMAINS.AI_PROVIDER, "TRADING_FOO", env);
  assert.equal(r.crossDomainBlocked, true, "AI domain must block cross-domain name");
  assert.equal(r.present, false);
  assert.equal(r.value, "", "AI resolver must NEVER return a trading secret value");
});

test("F-05：TRADING 域只认 TRADING_ 前缀；不能读取 AI 凭据", () => {
  const env = { TRADING_FOO: "t-xyz", OPENAI_API_KEY: "sk-openai-1234" };
  const ok = resolveDomainSecret(CREDENTIAL_DOMAINS.TRADING, "TRADING_FOO", env);
  assert.equal(ok.present, true, "trading secret resolves under TRADING domain");
  const blocked = resolveDomainSecret(CREDENTIAL_DOMAINS.TRADING, "OPENAI_API_KEY", env);
  assert.equal(blocked.crossDomainBlocked, true, "trading domain cannot read AI name");
  assert.equal(blocked.value, "");
});

test("F-05：未知域被拒绝（UNKNOWN_DOMAIN），不回退到全局 env", () => {
  const r = resolveDomainSecret("BOGUS", "OPENAI_API_KEY", { OPENAI_API_KEY: "x" });
  assert.equal(r.error, "UNKNOWN_DOMAIN");
  assert.equal(r.present, false);
  assert.equal(r.value, "");
});

test("F-05：loadDomainSecrets 异常隔离，且只产出本域 entry（不触及他域）", () => {
  const env = { OPENAI_API_KEY: "sk-1", TRADING_FOO: "t-leak" };
  const out = loadDomainSecrets(CREDENTIAL_DOMAINS.AI_PROVIDER, ["OPENAI_API_KEY", "NOT_IN_DOMAIN"], env);
  assert.ok(Object.isFrozen(out), "result frozen");
  assert.equal(out.OPENAI_API_KEY.present, true);
  assert.equal(out.NOT_IN_DOMAIN.crossDomainBlocked, true, "out-of-domain name is blocked, not leaked");
  assert.equal(out.NOT_IN_DOMAIN.value, "");
  assert.ok(!("TRADING_FOO" in out), "trading secret never appears in AI domain load");
});

test("F-05 集成：provider 目录与 listProviders 均带 domain=AI_PROVIDER；verdict 口径不变", () => {
  const reg = providerRegistryFromEnv({});
  assert.ok(reg.length >= 6, "catalogue has providers");
  assert.ok(reg.every((p) => p.domain === "AI_PROVIDER"), "every provider is in AI_PROVIDER domain");

  const blocked = listProviders({});
  assert.ok(blocked.providers.every((p) => p.domain === "AI_PROVIDER"), "listed providers carry domain");
  assert.equal(blocked.verdict, "BLOCKED", "no secrets => BLOCKED (unchanged semantics)");

  const unverified = listProviders({ OPENAI_API_KEY: "sk-openai-1234" });
  assert.equal(unverified.verdict, "UNVERIFIED", "with a credential => UNVERIFIED (not a connectivity claim)");
  const openai = unverified.providers.find((p) => p.providerId === "openai");
  assert.equal(openai.credential_present, true);
  assert.equal(openai.domain, "AI_PROVIDER");
});

test("F-05 诚实声明：历史 MiniMax 凭据位置记为 UNVERIFIED（禁止臆造）", () => {
  assert.equal(MINIMAX_CREDENTIAL_LOCATION, "UNVERIFIED", "MiniMax storage location is honestly UNVERIFIED");
  assert.ok(AI_PROVIDER_SECRET_NAMES.has("MINIMAX_API_KEY"), "MINIMAX_API_KEY registered under AI_PROVIDER domain");
});

test("F-05 静态：runtime.mjs 已接入域限定解析（import + resolveDomainSecret 使用）", () => {
  assert.ok(/import \{[^}]*resolveDomainSecret[^}]*\} from "\.\/credential-domain\.mjs"/.test(RUNTIME_AI_SRC), "runtime imports resolveDomainSecret from credential-domain");
  assert.ok(/resolveDomainSecret\(domain, ref, env\)/.test(RUNTIME_AI_SRC), "resolveSecret delegates to domain-scoped resolver");
  assert.ok(/\.map\(\(p\) => \(\{ \.\.\.p, domain: CREDENTIAL_DOMAINS\.AI_PROVIDER \}\)\)/.test(RUNTIME_AI_SRC), "catalogue tags each provider with AI_PROVIDER domain");
});
