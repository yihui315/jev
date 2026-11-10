/**
 * test-candidate-keys.mjs — empirical matrix: each stored candidate key vs each
 * reachable provider. Discipline: secret values never printed (last4 only);
 * real outbound calls through proxy 10808; no funds, no writes.
 */
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { providerRegistryFromEnv, testProvider } from "../server/ai/runtime.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..", "..", "..");

function loadDotEnv(path) {
  const out = {};
  let raw = "";
  try { raw = readFileSync(path, "utf8"); } catch { return out; }
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("=");
    if (eq === -1) continue;
    let v = t.slice(eq + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[t.slice(0, eq).trim()] = v;
  }
  return out;
}

function curlFetch(proxy = "127.0.0.1:10808") {
  return async function (url, init = {}) {
    const dir = mkdtempSync(join(tmpdir(), "ck-"));
    const bodyFile = join(dir, "b");
    const args = ["-s", "-S", "--connect-timeout", "12", "--max-time", "40", "-o", bodyFile, "-w", "%{http_code}", "-X", (init.method || "POST").toUpperCase()];
    for (const [k, v] of Object.entries(init.headers || {})) args.push("-H", `${k}: ${v}`);
    if (init.body != null) args.push("--data-binary", String(init.body));
    args.push(url);
    const env = { ...process.env, http_proxy: proxy, https_proxy: proxy, HTTP_PROXY: proxy, HTTPS_PROXY: proxy };
    const r = spawnSync("curl", args, { encoding: "utf8", env, maxBuffer: 32 * 1024 * 1024 });
    const status = parseInt((r.stdout || "").trim(), 10);
    let raw = "";
    try { raw = readFileSync(bodyFile, "utf8"); } catch {}
    if (Number.isNaN(status)) return { status: 0, text: async () => raw, json: async () => null, __err: r.stderr || r.error?.message };
    return { status, text: async () => raw, json: async () => { try { return JSON.parse(raw); } catch { return null; } } };
  };
}

const fetchImpl = curlFetch();
const fileEnv = loadDotEnv(join(ROOT, "config", ".env"));

// Candidate keys to test (by their stored name).
const CANDIDATES = {
  "sk-cp-(active qwen)": fileEnv.DASHSCOPE_API_KEY,
  "sk-sp-(backup)": fileEnv.DASHSCOPE_API_KEY_BACKUP,
};

// Providers whose endpoint is reachable through the proxy.
const TARGETS = [
  { id: "minimax", secretRef: "MINIMAX_API_KEY" },
  { id: "qwen", secretRef: "DASHSCOPE_API_KEY" },
  { id: "deepseek", secretRef: "DEEPSEEK_API_KEY" },
  { id: "openrouter", secretRef: "OPENROUTER_API_KEY" },
];

async function main() {
  console.log("=== Candidate Key × Provider Matrix (real calls, proxy 10808) ===\n");
  for (const [cname, cval] of Object.entries(CANDIDATES)) {
    if (!cval) { console.log(`[skip] ${cname}: empty`); continue; }
    console.log(`--- candidate ${cname}  (last4=${cval.slice(-4)}, len=${cval.length}) ---`);
    for (const t of TARGETS) {
      const env = { ...process.env, ...fileEnv, [t.secretRef]: cval };
      const res = await testProvider({ env, providerId: t.id, fetchImpl });
      console.log(`    [${res.status}] ${t.id.padEnd(10)} code=${res.code || "-"} http=${res.evidence?.http_status ?? "-"} ${res.evidence?.reason ? "reason=" + String(res.evidence.reason).slice(0, 70) : ""}`);
    }
    console.log("");
  }
  console.log("Interpretation: CONNECTED = key is valid for that provider. FAILED/AUTH_FAILED 401 = wrong provider or dead key. BLOCKED = not attempted.");
}

main().catch((e) => { console.error("matrix error:", e.message); process.exit(2); });
