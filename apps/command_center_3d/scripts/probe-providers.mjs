/**
 * probe-providers.mjs — real provider connectivity probe (PHASE 1 of DAY-1).
 *
 * Discipline:
 *  - Secret VALUES are never printed. Only credential presence + last4 (from
 *    runtime.describeSecret) + provider-side evidence (http_status, model,
 *    finish_reason, token usage) are surfaced.
 *  - A missing credential is reported as BLOCKED (SECRET_REF_UNRESOLVED), never
 *    dressed up as an outage or a success.
 *  - Network egress goes through the local proxy 127.0.0.1:10808 (Node global
 *    fetch does not honor http_proxy, so we back fetchImpl with curl, which does).
 *  - No trading, no funds, no writes. Read-only connectivity check.
 *
 * Usage:
 *   node scripts/probe-providers.mjs
 * Reads config/.env (gitignored) for credentials; merges over process.env.
 */
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  providerRegistryFromEnv,
  resolveSecret,
  testProvider,
} from "../server/ai/runtime.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..", "..");
const ENV_FILE = join(REPO_ROOT, "config", ".env");

/** Parse a simple KEY=VALUE .env (no interpolation, # comments, blank lines). */
function loadDotEnv(path) {
  const out = {};
  let raw = "";
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    return out;
  }
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const k = trimmed.slice(0, eq).trim();
    let v = trimmed.slice(eq + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    out[k] = v;
  }
  return out;
}

/**
 * curl-backed fetchImpl. Returns a Response-like { status, text(), json() }.
 * Body is written to a temp file so we can separate it from the HTTP status code
 * cleanly (curl -w "%{http_code}" writes only the code to stdout).
 */
function makeCurlFetch(proxy = "127.0.0.1:10808") {
  return async function curlFetch(url, init = {}) {
    const dir = mkdtempSync(join(tmpdir(), "probe-"));
    const bodyFile = join(dir, "body.txt");
    const args = [
      "-s", "-S",
      "--connect-timeout", "12",
      "--max-time", "40",
      "-o", bodyFile,
      "-w", "%{http_code}",
    ];
    args.push("-X", (init.method || "POST").toUpperCase());
    const headers = init.headers || {};
    for (const [k, v] of Object.entries(headers)) args.push("-H", `${k}: ${v}`);
    if (init.body != null) args.push("--data-binary", String(init.body));
    args.push(url);
    const env = { ...process.env, http_proxy: proxy, https_proxy: proxy, HTTPS_PROXY: proxy, HTTP_PROXY: proxy };
    const r = spawnSync("curl", args, { encoding: "utf8", env, maxBuffer: 32 * 1024 * 1024 });
    const status = parseInt((r.stdout || "").trim(), 10);
    let rawText = "";
    try { rawText = readFileSync(bodyFile, "utf8"); } catch { /* ignore */ }
    if (Number.isNaN(status)) {
      // curl failed entirely (proxy down / DNS / timeout)
      return {
        status: 0,
        text: async () => rawText,
        json: async () => { try { return JSON.parse(rawText); } catch { return null; } },
        __curlError: r.stderr || r.error?.message || "curl exited non-zero",
      };
    }
    return {
      status,
      text: async () => rawText,
      json: async () => { try { return JSON.parse(rawText); } catch { return null; } },
    };
  };
}

function maskEvidence(ev) {
  if (!ev) return ev;
  const { ...rest } = ev;
  // describeRequest may include url/headers/method — keep url host only.
  if (rest.url) {
    try { rest.url = new URL(rest.url).host; } catch { /* leave */ }
  }
  if (rest.headers) rest.headers = Object.fromEntries(
    Object.keys(rest.headers).map((k) => [k, k.toLowerCase().includes("authorization") ? "<redacted>" : (rest.headers[k] ? "<set>" : "<empty>")]),
  );
  return rest;
}

async function main() {
  const fileEnv = loadDotEnv(ENV_FILE);
  const env = { ...process.env, ...fileEnv };
  const fetchImpl = makeCurlFetch();
  const providers = providerRegistryFromEnv(env);

  const rows = [];
  for (const p of providers) {
    const secret = resolveSecret(p, env);
    if (!secret.present) {
      rows.push({
        providerId: p.providerId,
        model: p.modelId,
        credential: "ABSENT",
        status: "BLOCKED",
        code: "SECRET_REF_UNRESOLVED",
        http_status: null,
        detail: "No credential stored for " + p.secretRef + " — nothing sent.",
      });
      continue;
    }
    const res = await testProvider({ env, providerId: p.providerId, fetchImpl });
    rows.push({
      providerId: p.providerId,
      model: res.model || p.modelId,
      credential: "PRESENT(last4=" + (secret.value.slice(-4)) + ")",
      status: res.status,
      code: res.code || null,
      http_status: res.evidence?.http_status ?? null,
      retryable: res.retryable ?? null,
      detail: res.evidence?.reason || (res.evidence ? JSON.stringify(maskEvidence(res.evidence)) : null),
    });
  }

  // Report (no secret values).
  console.log("=== Provider Connectivity Probe ===");
  console.log("env file:", ENV_FILE, fileEnv && Object.keys(fileEnv).length ? "(loaded " + Object.keys(fileEnv).length + " keys)" : "(absent/empty)");
  console.log("");
  for (const r of rows) {
    console.log(`[${r.status}] ${r.providerId}  model=${r.model ?? "?"}  cred=${r.credential}`);
    console.log(`        code=${r.code ?? "-"} http=${r.http_status ?? "-"} retryable=${r.retryable ?? "-"}`);
    if (r.detail) console.log(`        ${String(r.detail).slice(0, 300)}`);
  }
  const connected = rows.filter((r) => r.status === "CONNECTED").length;
  const blocked = rows.filter((r) => r.status === "BLOCKED").length;
  const failed = rows.filter((r) => r.status === "FAILED").length;
  console.log("");
  console.log(`SUMMARY: CONNECTED=${connected} BLOCKED=${blocked} FAILED=${failed} TOTAL=${rows.length}`);
  console.log("verdict: " + (connected > 0 ? "AT_LEAST_ONE_REAL_PROVIDER_CALLABLE" : "NO_REAL_PROVIDER_CALLABLE"));
}

main().catch((e) => {
  console.error("probe error:", e.message);
  process.exit(2);
});
