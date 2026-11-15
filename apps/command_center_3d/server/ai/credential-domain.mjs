// F-05: 独立凭据域（credential domain isolation）。
//
// 根因：原 resolveSecret 从全局 env 扁平读取 secretRef，AI provider 凭据与
// （未来）交易侧凭据之间没有强制边界；任一域解析失败/受损都可能牵连另一方。
//
// 本模块把凭据划分为互不相交的「域」：
//   - AI_PROVIDER：模型 provider 凭据（既有扁平 env 键，如 OPENAI_API_KEY）。
//   - TRADING：交易侧凭据（TRADING_ 前缀；当前 fail-closed，无真实接线）。
//
// 关键不变量：resolveDomainSecret 只读取「本域允许」的凭据名，绝不跨域回退到
// 全局 env；请求一个不属于本域的名称时返回 crossDomainBlocked:true，绝不返回
// 他域的值。因此「模型调用失败」在结构上无法读取/污染交易侧凭据。
//
// 诚实声明：历史 MiniMax 凭据的「实际存储位置」本仓无法核实，显式记为 UNVERIFIED，
// 禁止臆造具体路径。

export const CREDENTIAL_DOMAINS = Object.freeze({
  AI_PROVIDER: "AI_PROVIDER",
  TRADING: "TRADING",
});

// AI provider 域允许解析的凭据名（既有扁平 env 键）。新增 AI provider 必须在此登记，
// 否则视为越界（crossDomainBlocked）。
export const AI_PROVIDER_SECRET_NAMES = Object.freeze(new Set([
  "OPENAI_API_KEY",
  "MINIMAX_API_KEY",
  "DASHSCOPE_API_KEY",
  "DEEPSEEK_API_KEY",
  "OPENROUTER_API_KEY",
  "OLLAMA_API_KEY",
]));

// 交易域凭据名必须以该前缀起头（与 AI_PROVIDER 扁平键互不相交）。
export const TRADING_SECRET_PREFIX = "TRADING_";

// 历史 MiniMax 凭据实际存储位置：本仓无法核实 —— 显式 UNVERIFIED，禁止臆造。
export const MINIMAX_CREDENTIAL_LOCATION = "UNVERIFIED";

const DOMAIN_ALLOW = {
  [CREDENTIAL_DOMAINS.AI_PROVIDER]: (name) => AI_PROVIDER_SECRET_NAMES.has(name),
  [CREDENTIAL_DOMAINS.TRADING]: (name) => typeof name === "string" && name.startsWith(TRADING_SECRET_PREFIX),
};

export function isKnownDomain(domain) {
  return Object.prototype.hasOwnProperty.call(DOMAIN_ALLOW, domain);
}

/**
 * 域限定凭据解析：只读取本域允许的凭据名，绝不跨域读取。
 * @returns {{present:boolean, value:string, domain:string, name:string, crossDomainBlocked:boolean, error:string|null}}
 */
export function resolveDomainSecret(domain, name, env = process.env) {
  if (!isKnownDomain(domain)) {
    return { present: false, value: "", domain, name: String(name ?? ""), crossDomainBlocked: false, error: "UNKNOWN_DOMAIN" };
  }
  const allow = DOMAIN_ALLOW[domain];
  const key = String(name ?? "");
  if (!allow(key)) {
    // 名不属于本域 => 拒绝跨域读取，绝不回退到全局 env / 他域值
    return { present: false, value: "", domain, name: key, crossDomainBlocked: true, error: "NAME_NOT_IN_DOMAIN" };
  }
  const raw = env && env[key];
  const value = typeof raw === "string" ? raw : "";
  return { present: value.length > 0, value, domain, name: key, crossDomainBlocked: false, error: null };
}

/**
 * 整域加载：异常被隔离在本域 entry 内，绝不读取/返回另一域凭据。
 * 即便某个名解析抛错，也只影响该 entry，不波及他域。
 */
export function loadDomainSecrets(domain, names, env = process.env) {
  if (!isKnownDomain(domain)) throw new Error("UNKNOWN_DOMAIN:" + String(domain));
  const out = {};
  for (const n of names) {
    try {
      out[n] = resolveDomainSecret(domain, n, env);
    } catch (e) {
      out[n] = { present: false, value: "", domain, name: n, crossDomainBlocked: false, error: "RESOLVE_ERROR:" + (e && e.message ? e.message : String(e)) };
    }
  }
  return Object.freeze(out);
}

/** 交易域便捷解析：只读取 TRADING_ 前缀凭据，绝不接触 AI_PROVIDER 域。 */
export function resolveTradingSecret(name, env = process.env) {
  return resolveDomainSecret(CREDENTIAL_DOMAINS.TRADING, name, env);
}
