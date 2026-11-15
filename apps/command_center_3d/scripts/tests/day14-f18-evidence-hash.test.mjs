#!/usr/bin/env node
/**
 * F-18 回归测试：证据 payload 哈希跨批次可复现
 *
 * 核心命题：墙钟派生的性能测量（latency_ms / latencyMs 数字值）不得进入
 * 「事实哈希」(payloadSha256)，否则同一决策两次运行（时延不同）会跨批次假阳性。
 *
 * 证明方式（真实跑 normalize-evidence.mjs 的 main()，读其 MANIFEST 与归一化输出）：
 *  1) 正向：相同事实、不同 latency 的两批证据 → payloadSha256 相同（跨批次可复现）
 *  2) 负向/变异：事实不同（即便 latency 相同）→ payloadSha256 不同（真实差异仍被抓住）
 *  3) 不误伤：schema 合约里作为「字段名字符串」出现的 latency_ms 仍保留在归一化输出
 *  4) 审计保留：被排除的 latency 数值仍记录在 rec.payloadPerfExcluded
 *  5) 诚实标注：相同事实但时延不同 → 配对 identicalAfterNormalization=true 且 perfDiffers=true
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { main, payloadOf } from "../normalize-evidence.mjs";

function sha256Hex(s) { return createHash("sha256").update(s).digest("hex"); }

function makeEvidence({ id, latency, priceTs, hasResult = true }) {
  const doc = {
    id, file: id + ".json", label: "F-18 cross-batch",
    exitCode: 0, verdict: "PASS",
    stdout: 'RESULT_JSON:{"testId":"T-X","verdict":"PASS","checks":[]}',
    stderr: "(node:12345) ok",
    capturedAt: "2026-01-01T00:00:02Z",
    node: "(node:12345)",
  };
  if (hasResult) {
    doc.result = {
      testId: "T-X", verdict: "PASS",
      startedAt: "2026-01-01T00:00:00Z", finishedAt: "2026-01-01T00:00:01Z",
      meta: { latency_ms: latency, price_ts: priceTs, fetched_at_utc: "2026-01-01T00:00:00Z" },
      checks: [{ name: "c1", pass: true }],
      extra: {},
    };
  }
  return doc;
}

function runOn(tmp, files) {
  const evid = join(tmp, "evid");
  const out = join(tmp, "norm");
  mkdirSync(evid, { recursive: true });
  for (const [name, obj] of files) writeFileSync(join(evid, name), JSON.stringify(obj) + "\n");
  main({ evidDir: evid, outDir: out });
  return { evid, out, manifest: JSON.parse(readFileSync(join(out, "MANIFEST.json"), "utf8")) };
}

test("F-18 positive: identical facts, different latency => same payloadSha256 (cross-batch reproducible)", () => {
  const tmp = mkdtempSync(join(tmpdir(), "f18-pos-"));
  try {
    const { manifest, out } = runOn(tmp, [
      ["batch1.json", makeEvidence({ id: "batch1", latency: 120, priceTs: 1700000000000 })],
      ["batch2.json", makeEvidence({ id: "batch2", latency: 455, priceTs: 1700000000000 })],
    ]);
    const f1 = manifest.files.find((f) => f.file === "batch1.json");
    const f2 = manifest.files.find((f) => f.file === "batch2.json");
    assert.ok(f1.payloadSha256, "batch1 payloadSha256 computed");
    assert.equal(f1.payloadSha256, f2.payloadSha256,
      "same facts but different wall-clock latency must hash identically (cross-batch reproducible)");

    // 反证：若把 latency 留在哈希里，两批的原始 payload 哈希必然不同
    const raw1 = sha256Hex(JSON.stringify(payloadOf(makeEvidence({ id: "x", latency: 120, priceTs: 1700000000000 }))) + "\n");
    const raw2 = sha256Hex(JSON.stringify(payloadOf(makeEvidence({ id: "x", latency: 455, priceTs: 1700000000000 }))) + "\n");
    assert.notEqual(raw1, raw2, "sanity: raw payload (with latency) hashes DO differ — fix is what removes latency");

    // 审计保留：归一化输出仍含 latency_ms 数值（仅移出哈希，不删）
    const norm1 = readFileSync(join(out, "batch1.json"), "utf8");
    assert.match(norm1, /"latency_ms":\s*120/, "normalized output retains latency_ms value for audit");

    // 配对诚实标注
    const pair = manifest.residualDiff.pairs.find((p) => p.testId === "T-X");
    assert.ok(pair, "pair exists for T-X");
    assert.equal(pair.identicalAfterNormalization, true, "same facts => identical");
    assert.equal(pair.perfDiffers, true, "but honest: latency differed");
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("F-18 negative/mutation: differing fact (same latency) => different payloadSha256 (real diffs still caught)", () => {
  const tmp = mkdtempSync(join(tmpdir(), "f18-neg-"));
  try {
    const { manifest } = runOn(tmp, [
      ["batch1.json", makeEvidence({ id: "batch1", latency: 120, priceTs: 1700000000000 })],
      ["batch3.json", makeEvidence({ id: "batch3", latency: 120, priceTs: 1700000000001 })], // 仅 fact 不同
    ]);
    const f1 = manifest.files.find((f) => f.file === "batch1.json");
    const f3 = manifest.files.find((f) => f.file === "batch3.json");
    assert.notEqual(f1.payloadSha256, f3.payloadSha256,
      "a real fact difference (price_ts) must still break the hash");
    const pair = manifest.residualDiff.pairs.find((p) => p.testId === "T-X");
    assert.equal(pair.identicalAfterNormalization, false, "real diff => not identical");
    assert.equal(pair.perfDiffers, false, "latency was identical, so no perf diff flagged");
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("F-18 no over-strip: schema-contract 'latency_ms' as a field-name string is preserved in normalized output", () => {
  const tmp = mkdtempSync(join(tmpdir(), "f18-schema-"));
  try {
    const contract = {
      id: "contract", file: "contract.json", label: "schema",
      exitCode: 0, verdict: "PASS", stdout: "", stderr: "",
      result: { verdict: "PASS", checks: [], fields: ["latency_ms", "price_ts", "fetched_at"], schemaVersion: 7 },
      capturedAt: "2026-01-01T00:00:00Z",
    };
    const { out, manifest } = runOn(tmp, [["contract.json", contract]]);
    const norm = readFileSync(join(out, "contract.json"), "utf8");
    assert.match(norm, /"latency_ms"/, "field-name string 'latency_ms' must survive normalization (not stripped as a perf key)");
    // 该文件无 result.checks 数组 → payloadOf 退回整文档，但 testId 缺失 → payloadSha256 null（不报错）
    const fc = manifest.files.find((f) => f.file === "contract.json");
    assert.equal(fc.payloadSha256, null, "no testId => payloadSha256 null (expected), and no crash");
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});

test("F-18 audit record: excluded latency value is captured in rec.payloadPerfExcluded", () => {
  const tmp = mkdtempSync(join(tmpdir(), "f18-audit-"));
  try {
    const { manifest } = runOn(tmp, [
      ["batch1.json", makeEvidence({ id: "batch1", latency: 777, priceTs: 1700000000000 })],
    ]);
    const f1 = manifest.files.find((f) => f.file === "batch1.json");
    assert.deepEqual(f1.payloadPerfExcluded, { latency_ms: 777 }, "numeric latency must be recorded for audit");
  } finally { rmSync(tmp, { recursive: true, force: true }); }
});
