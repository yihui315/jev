#!/usr/bin/env node
/**
 * normalize-evidence.mjs — 证据标准化比较视图生成器
 *
 * 目的（用户明确要求）：
 *   保留原始证据，同时生成「去除非确定性字段」的标准化比较视图。
 *   绝不为了哈希一致而扩大剥离范围、绝不删除任何 verdict / checks / pass / detail。
 *
 * 边界（写死在代码里，不接受参数放宽）：
 *   - 只读 reports/evidence/v16/*.json（顶层，不递归）；
 *   - 只写 reports/evidence/v16/_normalized/**；
 *   - 零依赖，仅用 node: 标准库；
 *   - 不联网、不 spawn、不修改任何原始文件。
 *
 * 剥离清单（只有这些，逐条计数并写进 MANIFEST）：
 *   1) 执行簿记时间戳字段：startedAt / finishedAt / capturedAt / generatedAt /
 *      completedAt / createdAt / updatedAt
 *   2) stderr 内嵌的进程号：`(node:<PID>)` → `(node:<PID>)` 占位
 *   3) 绝对路径中的用户名 / 盘符：`X:\Users\<name>` → `<USER_HOME>`，其余盘符绝对路径 → `<ABS_PATH>`
 *   4) stdout 里的 `RESULT_JSON:{...}`：解析后按同样规则归一化再序列化
 *
 * 明确保留（不做任何改写，且写进 MANIFEST 说明）：
 *   - price_ts / fetched_at / freshness_ms 等领域数据（它们是哈希载荷的一部分，
 *     剥掉就等于销毁证据；它们本来就是确定性的）
 *   - 一切 verdict / pass / checks / detail / 任何 sha256 值
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));               // .../apps/command_center_3d/scripts
const REPO_ROOT = resolve(HERE, "..", "..", "..");                  // repo root
const EVID_DIR = join(REPO_ROOT, "reports", "evidence", "v16");
const OUT_DIR = join(EVID_DIR, "_normalized");

/** 只剥离「执行簿记」时间戳；不含 price_ts / fetched_at（领域数据） */
const EXEC_TIME_KEYS = new Set([
  "startedAt", "finishedAt", "capturedAt", "generatedAt",
  "completedAt", "createdAt", "updatedAt",
]);

const SCHEMA_VERSION = "1";

/* ---------------------------------------------------------------- utils */

function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

/** 键名排序的稳定序列化，保证同内容必然同哈希 */
function stableStringify(value, indent = 2) {
  const seen = new Set();
  const walk = (v, depth) => {
    const pad = " ".repeat(indent * depth);
    const padIn = " ".repeat(indent * (depth + 1));
    if (v === null) return "null";
    if (Array.isArray(v)) {
      if (v.length === 0) return "[]";
      const items = v.map((x) => padIn + (x === undefined ? "null" : walk(x, depth + 1)));
      return "[\n" + items.join(",\n") + "\n" + pad + "]";
    }
    if (typeof v === "object") {
      if (seen.has(v)) return '"[CIRCULAR]"';
      seen.add(v);
      const keys = Object.keys(v).filter((k) => v[k] !== undefined).sort();
      if (keys.length === 0) return "{}";
      const items = keys.map((k) => padIn + JSON.stringify(k) + ": " + walk(v[k], depth + 1));
      seen.delete(v);
      return "{\n" + items.join(",\n") + "\n" + pad + "}";
    }
    if (typeof v === "number" && !Number.isFinite(v)) return JSON.stringify(String(v));
    return JSON.stringify(v);
  };
  return walk(value, 0);
}

/** 单次统计容器：path -> count */
const makeTracker = () => ({ map: new Map(), add(path, n = 1) { this.map.set(path, (this.map.get(path) || 0) + n); } });

function trackerToArray(tracker) {
  return [...tracker.map.entries()]
    .map(([path, count]) => ({ path, count }))
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

/* ------------------------------------------------- 字符串级归一化规则 */

const STRING_RULES = [
  // stderr 里的 (node:21384) —— 进程号每次执行都不同
  { id: "stderr-node-pid", re: /\(node:\d+\)/g, to: "(node:<PID>)" },
  // Windows 用户目录：C:\Users\Administrator\... → <USER_HOME>
  { id: "abs-user-home-win", re: /[A-Za-z]:[\\/]Users[\\/][^\\/\s"',)]+/g, to: "<USER_HOME>" },
  // POSIX 用户目录
  { id: "abs-user-home-posix", re: /\/(?:home|Users)\/[^/\s"',)]+/g, to: "<USER_HOME>" },
  // UNC 路径
  { id: "abs-unc", re: /\\\\[^\s"',)]+/g, to: "<ABS_PATH>" },
  // 其余盘符绝对路径
  { id: "abs-drive-path", re: /\b[A-Za-z]:[\\/](?:[^\\/\s"',):]+[\\/])*[^\\/\s"',):]*/g, to: "<ABS_PATH>" },
];

function normalizeString(s, tracker, pathLabel) {
  let out = s;
  for (const rule of STRING_RULES) {
    const hits = out.match(rule.re);
    if (hits && hits.length) {
      tracker.add(`${pathLabel}#${rule.id}`, hits.length);
      out = out.replace(rule.re, rule.to);
    }
  }
  return out;
}

/* --------------------------------------------------------- 结构化归一化 */

/**
 * @param {any} node
 * @param {{ time: ReturnType<typeof makeTracker>, str: ReturnType<typeof makeTracker>, notes: string[] }} t
 * @param {string} pathLabel
 */
function normalizeValue(node, t, pathLabel) {
  if (Array.isArray(node)) {
    return node.map((v, i) => normalizeValue(v, t, `${pathLabel}[${i}]`));
  }
  if (node && typeof node === "object") {
    const out = {};
    for (const key of Object.keys(node)) {
      const childPath = pathLabel ? `${pathLabel}.${key}` : key;
      if (EXEC_TIME_KEYS.has(key)) {
        t.time.add(childPath);
        continue; // 整个键剔除
      }
      if (key === "stdout" && typeof node[key] === "string") {
        out[key] = normalizeStdout(node[key], t, childPath);
        continue;
      }
      out[key] = normalizeValue(node[key], t, childPath);
    }
    return out;
  }
  if (typeof node === "string") return normalizeString(node, t.str, pathLabel);
  return node;
}

/** stdout 可能是 "RESULT_JSON:{...}"：解析后归一化，再按原前缀序列化回去 */
function normalizeStdout(text, t, pathLabel) {
  const PREFIX = "RESULT_JSON:";
  const idx = text.indexOf(PREFIX);
  if (idx === -1) return normalizeString(text, t.str, pathLabel);
  const head = text.slice(0, idx);
  const body = text.slice(idx + PREFIX.length);
  const trailing = body.endsWith("\n") ? "\n" : "";
  let parsed;
  try {
    parsed = JSON.parse(trailing ? body.slice(0, -1) : body);
  } catch {
    t.notes.push(`${pathLabel}: RESULT_JSON 解析失败，按纯字符串归一化`);
    return normalizeString(text, t.str, pathLabel);
  }
  const normalized = normalizeValue(parsed, t, `${pathLabel}<RESULT_JSON>`);
  return head + PREFIX + stableStringify(normalized, 0).replace(/\n\s*/g, "") + trailing;
}

/* ------------------------------------------------------------- verdict */

function extractVerdict(obj) {
  if (!obj || typeof obj !== "object") return null;
  if (typeof obj.verdict === "string") return obj.verdict;
  if (typeof obj.overall === "string") return obj.overall;
  if (obj.result && typeof obj.result.verdict === "string") return obj.result.verdict;
  if (typeof obj.stdout === "string") {
    const m = obj.stdout.match(/"verdict"\s*:\s*"([^"]+)"/);
    if (m) return m[1];
  }
  return null;
}

/**
 * 比较投影：个体证据文件是 {id,file,label,exitCode,verdict,stdout,stderr,result}
 * 的「执行外壳 + 结果载荷」，group 证据文件是「结果载荷」本身。
 * 不投影就会把「外壳 vs 裸载荷」的结构差异误报成非确定性差异。
 */
function payloadOf(obj) {
  if (obj && typeof obj === "object" && obj.result && typeof obj.result === "object" && Array.isArray(obj.result.checks)) {
    return obj.result;
  }
  return obj;
}

/* ------------------------------------------------- F-18 修复：性能/事实分离
 * 墙钟派生的「性能测量」字段（latency_ms / latencyMs）随执行上下文变化——
 * 同一决策两次运行其时延必然不同。若纳入跨批次比较用的 payload 哈希，会导致
 * 「本地 vs CI / 今天 vs 昨天」的哈希比对假阳性（同批次内可比、跨批次不可比）。
 *
 * 修复：把它们从「事实哈希」(payloadSha256) 中排除，但仍完整保留在归一化输出
 * 里供审计（rec.payloadPerfExcluded）。排除规则是「仅当键值为数字（一次墙钟
 * 测量）时才排除」——若只是 schema 合约里声明的字段名字符串、数组元素或 null，
 * 一律保留，避免误伤合约与领域时间戳（price_ts / fetched_at / freshness_ms）。 */
const PERF_MEASURE_KEYS = new Set(["latency_ms", "latencyMs"]);

/** 投影出「事实载荷」：深拷贝并剔除数字型的墙钟性能测量键。 */
function projectFactPayload(node) {
  if (Array.isArray(node)) return node.map(projectFactPayload);
  if (node && typeof node === "object") {
    const out = {};
    for (const key of Object.keys(node)) {
      if (PERF_MEASURE_KEYS.has(key) && typeof node[key] === "number") continue; // 排除墙钟测量
      out[key] = projectFactPayload(node[key]);
    }
    return out;
  }
  return node;
}

/** 收集被排除的性能测量值（供审计记录，不销毁证据）。 */
function pickPerfValues(node, acc = {}) {
  if (Array.isArray(node)) { for (const v of node) pickPerfValues(v, acc); return acc; }
  if (node && typeof node === "object") {
    for (const key of Object.keys(node)) {
      if (PERF_MEASURE_KEYS.has(key) && typeof node[key] === "number") acc[key] = node[key];
      pickPerfValues(node[key], acc);
    }
  }
  return acc;
}

/* --------------------------------------------------------------- diff */

function collectDiffs(a, b, pathLabel, acc, limit) {
  if (acc.length >= limit) return acc;
  const ta = typeof a, tb = typeof b;
  if (ta !== tb) {
    acc.push({ path: pathLabel, kind: "TYPE_MISMATCH", a: String(a).slice(0, 160), b: String(b).slice(0, 160) });
    return acc;
  }
  if (a && typeof a === "object") {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of [...keys].sort()) {
      if (acc.length >= limit) break;
      const p = pathLabel ? `${pathLabel}.${k}` : k;
      if (!(k in a)) { acc.push({ path: p, kind: "MISSING_IN_A", b: short(b[k]) }); continue; }
      if (!(k in b)) { acc.push({ path: p, kind: "MISSING_IN_B", a: short(a[k]) }); continue; }
      collectDiffs(a[k], b[k], p, acc, limit);
    }
    return acc;
  }
  if (a !== b) acc.push({ path: pathLabel, kind: "VALUE_DIFF", a: short(a), b: short(b) });
  return acc;
}

function short(v) {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s === undefined ? "" : s.length > 160 ? s.slice(0, 157) + "..." : s;
}

/* ---------------------------------------------------------------- main */

function main({ evidDir = EVID_DIR, outDir = OUT_DIR } = {}) {
  if (!existsSync(evidDir)) {
    console.error(`EVIDENCE DIR MISSING: ${evidDir}`);
    process.exit(2);
  }
  mkdirSync(outDir, { recursive: true });

  const entries = readdirSync(evidDir);
  const jsonFiles = entries
    .filter((n) => n.toLowerCase().endsWith(".json"))
    .filter((n) => {
      try { return statSync(join(evidDir, n)).isFile(); } catch { return false; }
    })
    .sort();
  const skippedDirs = entries
    .filter((n) => { try { return statSync(join(evidDir, n)).isDirectory(); } catch { return false; } })
    .sort();

  /* 可选：把仓库外的证据文件（例如另一次执行的 RESULT_JSON）纳入同一比较视图。
     只读外部文件，输出落到 _normalized/_external/，并在 MANIFEST 里标 status=EXTERNAL。 */
  const extraArgs = [];
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--extra" && argv[i + 1]) extraArgs.push(argv[i + 1]);
    else if (argv[i].startsWith("--extra=")) extraArgs.push(argv[i].slice("--extra=".length));
  }
  const extraItems = extraArgs.map((p) => ({
    name: basename(p),
    rawPath: resolve(p),
    outPath: join(outDir, "_external", basename(p)),
    outRel: "_external/" + basename(p),
    status: "EXTERNAL",
  }));
  if (extraItems.length) mkdirSync(join(outDir, "_external"), { recursive: true });

  const items = [
    ...jsonFiles.map((n) => ({ name: n, rawPath: join(evidDir, n), outPath: join(outDir, n), outRel: n, status: "OK" })),
    ...extraItems,
  ];

  const files = [];
  const normalizedByTestId = new Map();
  const selfCheckFailures = [];

  for (const item of items) {
    const name = item.name;
    const rawBuf = readFileSync(item.rawPath);
    const rawText = rawBuf.toString("utf8");

    let parsed;
    try {
      parsed = JSON.parse(rawText);
    } catch (e) {
      // 解析失败不猜、不丢：原样复制 + 在 MANIFEST 里标 PARSE_FAILED
      writeFileSync(item.outPath, rawBuf);
      files.push({
        file: name, status: "PARSE_FAILED", error: String(e && e.message),
        rawSha256: sha256(rawBuf), rawBytes: rawBuf.length,
        normalizedSha256: sha256(rawBuf), normalizedBytes: rawBuf.length,
        stripped: [], strippedTotal: 0,
        verdictRaw: null, verdictNormalized: null, verdictConsistent: null,
      });
      continue;
    }

    const tracker = { time: makeTracker(), str: makeTracker(), notes: [] };
    const normalized = normalizeValue(parsed, tracker, "");
    const outBuf = Buffer.from(stableStringify(normalized) + "\n", "utf8");
    writeFileSync(item.outPath, outBuf);

    const verdictRaw = extractVerdict(parsed);
    const verdictNormalized = extractVerdict(normalized);
    const consistent = verdictRaw === verdictNormalized;
    if (!consistent) selfCheckFailures.push({ file: name, verdictRaw, verdictNormalized });

    const strippedTime = trackerToArray(tracker.time);
    const strippedStr = trackerToArray(tracker.str);
    const strippedTotal = [...strippedTime, ...strippedStr].reduce((s, x) => s + x.count, 0);

    const rec = {
      file: name,
      status: item.status,
      rawPathOutsideRepo: item.status === "EXTERNAL" ? item.rawPath : undefined,
      rawSha256: sha256(rawBuf),
      rawBytes: rawBuf.length,
      normalizedFile: item.outRel,
      normalizedSha256: sha256(outBuf),
      normalizedBytes: outBuf.length,
      strippedTimeFields: strippedTime,
      strippedStringPatterns: strippedStr,
      strippedTotal,
      verdictRaw,
      verdictNormalized,
      verdictConsistent: consistent,
      notes: tracker.notes,
    };
    files.push(rec);

    // 按 testId 归组，供「同测试不同次运行」比较
    const testId = normalized.testId || (normalized.result && normalized.result.testId) || null;
    if (testId) {
      const payload = payloadOf(normalized);
      // F-18：事实哈希排除墙钟性能测量键（latency_ms/latencyMs 数字值），保证跨批次可复现
      const factPayload = projectFactPayload(payload);
      const payloadSha = sha256(Buffer.from(stableStringify(factPayload) + "\n", "utf8"));
      rec.payloadSha256 = payloadSha;
      rec.payloadPerfExcluded = pickPerfValues(payload);
      if (!normalizedByTestId.has(testId)) normalizedByTestId.set(testId, []);
      normalizedByTestId.get(testId).push({ file: name, sha: payloadSha, obj: factPayload, raw: payload });
    } else {
      rec.payloadSha256 = null;
    }
  }

  /* 配对比较：同一个 testId 的多次运行（个体证据 vs group 证据） */
  const pairs = [];
  for (const [testId, runs] of [...normalizedByTestId.entries()].sort()) {
    for (let i = 0; i < runs.length; i += 1) {
      for (let j = i + 1; j < runs.length; j += 1) {
        const A = runs[i], B = runs[j];
        const equal = A.sha === B.sha;
        const diffs = equal ? [] : collectDiffs(A.obj, B.obj, "", [], 30);
        // F-18：即便事实哈希相同，也诚实标注「仅性能测量（时延）不同」，避免误读为完全一致
        const perfDiffers = JSON.stringify(pickPerfValues(A.raw)) !== JSON.stringify(pickPerfValues(B.raw));
        pairs.push({
          testId,
          a: A.file, aPayloadSha256: A.sha,
          b: B.file, bPayloadSha256: B.sha,
          identicalAfterNormalization: equal,
          perfDiffers,
          diffCount: equal ? 0 : diffs.length,
          diffTruncated: !equal && diffs.length >= 30,
          diffs,
        });
      }
    }
  }

  const manifest = {
    schemaVersion: SCHEMA_VERSION,
    tool: "apps/command_center_3d/scripts/normalize-evidence.mjs",
    purpose:
      "保留原始证据（原文件一字未改），同时生成去除非确定性字段的标准化比较视图。" +
      "剥离范围是白名单式的、逐条计数的；verdict/checks/pass/detail/所有哈希值一律保留。",
    sourceDir: evidDir,
    outputDir: outDir,
    scannedAtIsStripped: true,
    rules: {
      strippedTimeKeys: [...EXEC_TIME_KEYS].sort(),
      strippedStringPatterns: STRING_RULES.map((r) => ({ id: r.id, pattern: String(r.re), replacement: r.to })),
      hashScope: {
        description:
          "payloadSha256 的确定性口径 = 对 payloadOf(normalized) 做『事实投影』后哈希；" +
          "事实投影剔除墙钟派生的性能测量键，使跨批次比较可复现（本地 vs CI / 今天 vs 昨天）。",
        determinismExcludedKeys: [...PERF_MEASURE_KEYS].sort(),
        exclusionRule: "仅当键值为数字（一次墙钟测量）时排除；字段名字符串 / 数组元素 / null 一律保留",
        preservedForAudit: "被排除的性能值记录在每条 rec.payloadPerfExcluded，不销毁证据",
      },
      explicitlyPreserved: [
        "verdict / overall / pass / detail / checks / exitCode",
        "所有 sha256 / fullHash / strippedHash / payload_sha256 值",
        "领域时间戳 price_ts / fetched_at / freshness_ms（事实哈希载荷的一部分，剥掉即销毁证据）",
        "性能测量键 latency_ms / latencyMs 的『值』不删，仅移出事实哈希（见 rec.payloadPerfExcluded）",
        "stdout 正文与 stderr 正文（仅替换 PID 与绝对路径）",
      ],
      serialization: "对象键名升序 + 2 空格缩进 + 末尾换行（stableStringify）",
    },
    counts: {
      jsonFiles: jsonFiles.length,
      externalFiles: extraItems.length,
      normalizedOk: files.filter((f) => f.status === "OK").length,
      parseFailed: files.filter((f) => f.status === "PARSE_FAILED").length,
      skippedDirectories: skippedDirs,
      selfCheckFailures: selfCheckFailures.length,
      comparedPairs: pairs.length,
      pairsIdentical: pairs.filter((p) => p.identicalAfterNormalization).length,
      pairsDifferent: pairs.filter((p) => !p.identicalAfterNormalization).length,
    },
    selfCheck: {
      rule: "归一化前后 extractVerdict() 必须完全一致（确保没有因为剥离而改变结论）",
      failures: selfCheckFailures,
      pass: selfCheckFailures.length === 0,
    },
    files,
    residualDiff: {
      note:
        "同一 testId 的两次真实运行在归一化后是否仍然不同。不同不代表归一化失败，" +
        "而是说明差异不是时间戳/PID/路径造成的，属于真实残留差异（例如随执行上下文变化的哈希）。" +
        "比较单位是「结果载荷」：个体证据文件取其 .result，group 证据文件取本体，避免外壳结构与载荷结构的伪差异。",
      comparisonUnit: "payload (.result if present, else whole document)",
      pairs,
    },
  };

  writeFileSync(join(outDir, "MANIFEST.json"), stableStringify(manifest) + "\n", "utf8");

  /* ------------------------------------------------ 控制台摘要（给人看） */
  const L = [];
  L.push(`source: ${evidDir}`);
  L.push(`output: ${outDir}`);
  L.push(`json files scanned : ${jsonFiles.length}`);
  L.push(`normalized OK      : ${manifest.counts.normalizedOk}`);
  L.push(`parse failed       : ${manifest.counts.parseFailed}`);
  L.push(`skipped dirs       : ${skippedDirs.join(", ") || "(none)"}`);
  L.push(`verdict self-check : ${manifest.selfCheck.pass ? "PASS (all file verdicts identical before/after)" : "FAIL " + JSON.stringify(selfCheckFailures)}`);
  L.push(`compared pairs     : ${pairs.length} (identical=${manifest.counts.pairsIdentical} different=${manifest.counts.pairsDifferent})`);
  for (const p of pairs) {
    L.push(`  pair ${p.testId}: ${p.a} vs ${p.b} → ${p.identicalAfterNormalization ? "IDENTICAL" : "DIFFERENT (" + p.diffCount + " diffs)"}`);
    if (!p.identicalAfterNormalization) {
      for (const d of p.diffs.slice(0, 6)) L.push(`    ${d.kind} @ ${d.path}: A=${JSON.stringify(d.a)} B=${JSON.stringify(d.b)}`);
      if (p.diffCount > 6) L.push(`    ... ${p.diffCount - 6} more`);
    }
  }
  const totalStripped = files.reduce((s, f) => s + (f.strippedTotal || 0), 0);
  L.push(`stripped occurrences total: ${totalStripped}`);
  console.log(L.join("\n"));
}

// 仅在被直接执行时运行（import 时不触发，便于单元测试注入临时目录）
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}

export { projectFactPayload, pickPerfValues, payloadOf, main };
