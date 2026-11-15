/**
 * verify-minimax-keys.mjs — cheap re-check of which candidate key actually
 * reaches MiniMax M3. Uses curl-backed fetch through proxy 10808.
 * Prints only HTTP status + last4 of the key used; never full secrets.
 */
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..", "..");

function loadDotEnv(p) {
  const out = {}; let raw = "";
  try { raw = readFileSync(p, "utf8"); } catch { return out; }
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim(); if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("="); if (eq === -1) continue;
    let v = t.slice(eq + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[t.slice(0, eq).trim()] = v;
  }
  return out;
}
const env = loadDotEnv(join(ROOT, "config", ".env"));
const keys = {
  MINIMAX_API_KEY: env.MINIMAX_API_KEY,
  DASHSCOPE_API_KEY: env.DASHSCOPE_API_KEY, // original "sk-sp-" candidate
};

function curlFetch(proxy = "127.0.0.1:10808") {
  return async function (url, init = {}) {
    const dir = tmpdir();
    const bodyFile = join(dir, "vmk-" + Math.random().toString(36).slice(2));
    const args = ["-s", "-S", "--connect-timeout", "12", "--max-time", "40", "-o", bodyFile, "-w", "%{http_code}", "-X", "POST"];
    for (const [k, v] of Object.entries(init.headers || {})) args.push("-H", `${k}: ${v}`);
    if (init.body != null) args.push("--data-binary", String(init.body));
    args.push(url);
    const e = { ...process.env, http_proxy: proxy, https_proxy: proxy, HTTP_PROXY: proxy, HTTPS_PROXY: proxy };
    const r = spawnSync("curl", args, { encoding: "utf8", env: e, maxBuffer: 32 * 1024 * 1024 });
    const status = parseInt((r.stdout || "").trim(), 10);
    let raw = ""; try { raw = readFileSync(bodyFile, "utf8"); } catch {}
    if (Number.isNaN(status)) return { status: 0, json: async () => null, raw };
    return { status, json: async () => { try { return JSON.parse(raw); } catch { return null; } }, raw };
  };
}
const fetchImpl = curlFetch();

async function testOne(label, key) {
  const last4 = key ? key.slice(-4) : "(none)";
  if (!key) { console.log(`${label}: no key`); return; }
  const body = JSON.stringify({
    model: "MiniMax-M3",
    messages: [{ role: "user", content: 'Reply with valid JSON only: {"ok":true}' }],
    max_tokens: 40, temperature: 0,
  });
  const res = await fetchImpl("https://api.minimaxi.com/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
    body,
  });
  const api = await res.json();
  const hasChoices = Array.isArray(api?.choices) && api.choices.length > 0;
  console.log(`${label} (last4=${last4}): HTTP ${res.status} | choices=${hasChoices} | err=${api?.error?.message || "n/a"}`);
}

await testOne("MINIMAX_API_KEY", keys.MINIMAX_API_KEY);
await testOne("DASHSCOPE_API_KEY(sk-sp-)", keys.DASHSCOPE_API_KEY);
