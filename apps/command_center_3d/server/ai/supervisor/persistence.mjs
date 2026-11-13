// JEV V2.0 Phase E/F Supervisor — local append-only JSONL ledger archive.
//
// PURPOSE: durable, local-only record keeping for the Supervisor's continuous
// runs (Phase E Shadow / Phase F Paper). All data is written to the local
// filesystem as newline-delimited JSON (JSONL). There is NO network, NO
// external storage, and NO provider interaction anywhere in this module.
//
// Design:
//   * appendRecord() writes one JSON object per line, stamped with an ISO
//     timestamp and the UTC day key, into `archive-YYYY-MM-DD.jsonl`.
//   * Daily rotation is implicit: each UTC day gets its own file. rotateDaily()
//     lists the distinct day keys present. readArchive() replays everything
//     across all known day files in chronological (day-sorted) order.

import fs from "node:fs";
import path from "node:path";

/** UTC day key, e.g. "2026-11-13". */
export function dayKey(ts = new Date()) {
  const d = ts instanceof Date ? ts : new Date(ts);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function resolveDir(dir) {
  return path.resolve(dir || ".");
}

const FILE_RE = /^archive-(\d{4}-\d{2}-\d{2})\.jsonl$/;

/**
 * Append a single record to the day's ledger file.
 * @param {string} dir  base ledger directory (created if missing)
 * @param {object} record  arbitrary JSON-serializable object
 * @returns {string} the exact JSON line written
 */
export function appendRecord(dir, record) {
  const base = resolveDir(dir);
  fs.mkdirSync(base, { recursive: true });
  const ts = new Date();
  const line = JSON.stringify({ ...record, _ts: ts.toISOString(), _day: dayKey(ts) });
  fs.appendFileSync(path.join(base, `archive-${dayKey(ts)}.jsonl`), line + "\n", "utf8");
  return line;
}

/**
 * List the distinct UTC day keys for which an archive file exists.
 * @param {string} dir
 * @returns {string[]} sorted ascending
 */
export function rotateDaily(dir) {
  const base = resolveDir(dir);
  if (!fs.existsSync(base)) return [];
  const days = new Set();
  for (const name of fs.readdirSync(base)) {
    const m = FILE_RE.exec(name);
    if (m) days.add(m[1]);
  }
  return [...days].sort();
}

/**
 * Replay every archived record across all day files, in chronological order.
 * Malformed lines are skipped (never throws on a single bad line).
 * @param {string} dir
 * @returns {object[]}
 */
export function readArchive(dir) {
  const base = resolveDir(dir);
  const records = [];
  for (const d of rotateDaily(base)) {
    const p = path.join(base, `archive-${d}.jsonl`);
    if (!fs.existsSync(p)) continue;
    const text = fs.readFileSync(p, "utf8");
    for (const raw of text.split("\n")) {
      const s = raw.trim();
      if (!s) continue;
      try {
        records.push(JSON.parse(s));
      } catch {
        // skip malformed / partial line
      }
    }
  }
  return records;
}
