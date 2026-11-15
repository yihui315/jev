/**
 * P1 正式数据层 — 共享底层原语：append-only hash chain（内部模块）。
 *
 * 语义参考 day14-runner.mjs 的 makeJournal（只读参考，不复制、不 import）：
 *   - 每条落盘记录带 _prev（上一条 _hash，首条为 "GENESIS"）+ _hash（sha256）。
 *   - 启动时全量校验链：逐条 _prev === 上一条 tip、重算 _hash 防内容篡改。
 *   - 恢复 tip，支持断点续写 / 重启续接。
 *
 * 硬约束：
 *   - 仅 Node stdlib（node:crypto / node:fs / node:path）。零网络、零付费模型。
 *   - 所有外部输入依赖注入：readImpl / writeImpl / existsImpl / mkdirImpl / hashImpl。
 *     模块加载时不发起任何 IO、不触网。默认实现直接走 node:fs（本地）。
 *   - writeImpl 为「追加一行文本」语义（append-only JSONL），不做覆盖/删除。
 */
import { createHash } from "node:crypto";
import { readFileSync, appendFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export const GENESIS = "GENESIS";

/** 默认 sha256：字符串 -> 64 位 hex。可注入覆盖。 */
export function defaultHashImpl(str) {
  return createHash("sha256").update(String(str)).digest("hex");
}

/** 默认读：文件不存在返回 null（不抛），存在返回 utf8 文本。 */
export function defaultReadImpl(p) {
  return existsSync(p) ? readFileSync(p, "utf8") : null;
}

/** 默认写：追加文本（append-only，绝不覆盖）。 */
export function defaultWriteImpl(p, text) {
  appendFileSync(p, text);
}

export function defaultExistsImpl(p) {
  return existsSync(p);
}

export function defaultMkdirImpl(d) {
  mkdirSync(d, { recursive: true });
}

export const RESERVED_KEYS = Object.freeze(["_prev", "_hash"]);

/**
 * 全量读取并校验既有链。任何断链 / 篡改 / 解析失败都 throw（fail-closed）。
 * 返回 { tip, count, bodies }；bodies 为已落盘记录体（不含 _prev/_hash）。
 */
function loadAndVerify({ path, readImpl, existsImpl, hashImpl }) {
  let tip = GENESIS;
  let count = 0;
  const bodies = [];
  if (!existsImpl(path)) return { tip, count, bodies };
  const raw = readImpl(path);
  if (raw == null) return { tip, count, bodies };
  const lines = String(raw).split("\n").filter((l) => l.trim().length > 0);
  for (let i = 0; i < lines.length; i++) {
    let rec;
    try {
      rec = JSON.parse(lines[i]);
    } catch (e) {
      throw new Error(`[append-only-log] ${path} line ${i} not JSON: ${e.message}`);
    }
    if (!rec || typeof rec !== "object" || Array.isArray(rec))
      throw new Error(`[append-only-log] ${path} line ${i} not an object (tamper)`);
    const { _prev, _hash, ...body } = rec;
    if (typeof _prev !== "string" || typeof _hash !== "string")
      throw new Error(`[append-only-log] ${path} line ${i} missing _prev/_hash (tamper)`);
    if (_prev !== tip)
      throw new Error(`[append-only-log] ${path} CHAIN BROKEN at line ${i}: _prev !== previous tip`);
    const recomputed = hashImpl(_prev + "|" + JSON.stringify(body));
    if (recomputed !== _hash)
      throw new Error(`[append-only-log] ${path} HASH TAMPER at line ${i}: content hash mismatch`);
    tip = _hash;
    count++;
    bodies.push(body);
  }
  return { tip, count, bodies };
}

/**
 * 创建 append-only log。构造即全量校验链 + 恢复 tip（重启续接天然生效）。
 */
export function createAppendOnlyLog(opts = {}) {
  const {
    path,
    readImpl = defaultReadImpl,
    writeImpl = defaultWriteImpl,
    existsImpl = defaultExistsImpl,
    mkdirImpl = defaultMkdirImpl,
    hashImpl = defaultHashImpl,
  } = opts;
  if (typeof path !== "string" || path.length === 0)
    throw new Error("[append-only-log] path required");
  for (const [k, fn] of Object.entries({ readImpl, writeImpl, existsImpl, mkdirImpl, hashImpl })) {
    if (typeof fn !== "function") throw new Error(`[append-only-log] ${k} must be a function`);
  }

  let state = loadAndVerify({ path, readImpl, existsImpl, hashImpl });

  return {
    path,
    get tip() { return state.tip; },
    get count() { return state.count; },
    /** 已落盘记录体（不含 _prev/_hash），供上层做域校验 / 回放。 */
    readAll() { return state.bodies.slice(); },

    /** 重新从注入的 IO 读取并全量校验链；断链/篡改即 throw。 */
    verify() {
      state = loadAndVerify({ path, readImpl, existsImpl, hashImpl });
      return { ok: true, count: state.count, tip: state.tip };
    },

    /** 追加一条记录（体）。返回含 _prev/_hash 的完整落盘记录。 */
    append(body) {
      if (!body || typeof body !== "object" || Array.isArray(body))
        throw new Error("[append-only-log] append expects a record object");
      for (const k of RESERVED_KEYS) {
        if (Object.prototype.hasOwnProperty.call(body, k))
          throw new Error(`[append-only-log] body must not contain reserved key ${k}`);
      }
      const bodyStr = JSON.stringify(body);
      const _prev = state.tip;
      const _hash = hashImpl(_prev + "|" + bodyStr);
      mkdirImpl(dirname(path));
      writeImpl(path, JSON.stringify({ ...body, _prev, _hash }) + "\n");
      state.tip = _hash;
      state.count++;
      state.bodies.push(body);
      return { ...body, _prev, _hash };
    },
  };
}
