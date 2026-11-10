// T7 - redaction: no credential may appear in code, schema, status, evidence or
// persisted rows.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { AI_DIR } from "./lib/fixtures.mjs";
import { classifyResponse } from "../provider-adapter.mjs";
import { listProviders } from "../runtime.mjs";
import { makeDb, seedApprovedDecision } from "./lib/fixtures.mjs";
import { assert, assertEqual } from "./lib/harness.mjs";

export const id = "t7_redaction";
export const title = "Secret redaction: code, schema columns, status projection, persisted rows";

const SECRET_PATTERNS = [
  /ghp_[A-Za-z0-9]{20,}/,
  /github_pat_[A-Za-z0-9_]{20,}/,
  /\bsk-[A-Za-z0-9]{16,}/,
  /Bearer\s+[A-Za-z0-9._~+/=-]{24,}/,
];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walk(full));
    else if (/\.(mjs|sql)$/.test(name)) out.push(full);
  }
  return out;
}

export async function run(suite) {
  await suite.check("no credential-shaped string in server/ai sources or migrations", () => {
    const files = walk(AI_DIR);
    const hits = [];
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      for (const re of SECRET_PATTERNS) {
        const m = re.exec(text);
        if (m) hits.push(f.replace(/\\/g, "/") + " :: " + m[0]);
      }
    }
    assertEqual(hits.length, 0, "secret-shaped strings: " + JSON.stringify(hits));
    return "scanned " + files.length + " files";
  });

  await suite.check("ai_decisions / ai_market_contexts have no secret-bearing column", () => {
    const sql = readFileSync(join(AI_DIR, "migrations", "0006_ai_decisions.sql"), "utf8");
    const columns = [];
    let inTable = false;
    for (const line of sql.split(/\r?\n/)) {
      if (/^CREATE TABLE/.test(line)) inTable = true;
      else if (inTable && /^\);/.test(line)) inTable = false;
      else if (inTable) {
        const m = /^\s{2}([a-z_]+)\s+[A-Z]/.exec(line);
        if (m) columns.push(m[1]);
      }
    }
    assert(columns.length >= 25, "expected to parse all columns, got " + columns.length);
    const bad = columns.filter((c) => /secret|authorization|api_?key|password|token/i.test(c));
    assertEqual(bad.length, 0, "forbidden columns: " + JSON.stringify(bad));
    return "columns=" + columns.length;
  });

  await suite.check("provider status never returns a credential value", () => {
    const key = "sk-" + "unit-test-value-" + "abcdefghijklmnop";
    const status = listProviders({ OPENAI_API_KEY: key, JEV_AI_OPENAI_MODEL: "gpt-4o-mini" });
    const text = JSON.stringify(status);
    assert(!text.includes(key), "credential leaked into provider status");
    const row = status.providers.find((p) => p.providerId === "openai");
    assertEqual(row.credential_present, true);
    assertEqual(row.configured, true);
    assertEqual(row.last4.length, 4);
    assertEqual(status.verdict, "UNVERIFIED");
    return "last4 only";
  });

  await suite.check("provider error messages echoing a key are redacted", () => {
    const key = "sk-" + "abcdefghijklmnopqrstuvwxyz" + "0123456789";
    const provider = { providerId: "openai", providerType: "openai", baseUrl: "https://api.openai.com/v1", modelId: "gpt-4o-mini", secretRef: "OPENAI_API_KEY", timeoutMs: 1000, maxTokens: 8, enabled: true };
    const r = classifyResponse(provider, 401, { error: { message: "invalid key " + key + " rejected" } }, 50, {});
    assertEqual(r.code, "AUTH_FAILED");
    assert(!r.evidence.error_message.includes(key), "key not redacted: " + r.evidence.error_message);
    assert(/sk-\*\*\*/.test(r.evidence.error_message), r.evidence.error_message);
    return r.evidence.error_message;
  });

  await suite.check("persisted decision row contains no header or credential material", async () => {
    const db = makeDb();
    const decisionId = await seedApprovedDecision(db, { decisionId: "dec-redact-0001" });
    const row = await db.prepare("SELECT * FROM ai_decisions WHERE decision_id = ?").bind(decisionId).first();
    const text = JSON.stringify(row);
    for (const re of SECRET_PATTERNS) assert(!re.test(text), "secret-shaped value in row: " + re);
    assert(!/authorization/i.test(text), "header field leaked into row");
    assert(!/bearer /i.test(text), "bearer fragment leaked into row");
    return "row columns=" + Object.keys(row).length;
  });
}
