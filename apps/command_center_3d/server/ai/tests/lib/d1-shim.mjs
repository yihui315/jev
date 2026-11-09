// Minimal D1-shaped shim over node:sqlite (in-memory) for AI contract tests.
// Independent of the Track A collector shim: no shared writes, no network.

import { DatabaseSync } from "node:sqlite";

const WRITE_RE = /\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)/i;

export class D1Shim {
  constructor(schemaSql) {
    this.db = new DatabaseSync(":memory:");
    this.db.exec("PRAGMA foreign_keys = ON;");
    if (schemaSql) this.db.exec(schemaSql);
    this.sqlLog = [];
    this.writeLog = [];
  }
  prepare(sql) { return new Statement(this, sql); }
  batch(statements) {
    for (const s of statements) s._exec();
    return Promise.resolve({ success: true });
  }
  exec(sql) { this._record(sql); this.db.exec(sql); }
  _record(sql) {
    this.sqlLog.push(sql);
    const m = WRITE_RE.exec(sql);
    if (m) this.writeLog.push({ table: m[1].toLowerCase(), sql });
  }
  queryAll(sql, ...args) { return this.db.prepare(sql).all(...args); }
}

class Statement {
  constructor(shim, sql) { this.shim = shim; this.sql = sql; this.bound = []; }
  bind(...args) { this.bound = args; return this; }
  _exec() { this.shim._record(this.sql); return this.shim.db.prepare(this.sql); }
  async first(column) {
    const row = this._exec().get(...this.bound);
    if (row == null) return null;
    if (column != null) return row[column] ?? null;
    return row;
  }
  async all() { return { results: this._exec().all(...this.bound), success: true }; }
  async run() {
    const info = this._exec().run(...this.bound);
    return { success: true, meta: { changes: Number(info && info.changes) || 0 } };
  }
}
