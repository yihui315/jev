/**
 * Local D1-shaped shim backed by node:sqlite (in-memory). NO cloud D1, NO
 * network, NO connection to jev-paper-staging / Night Researcher.
 *
 * Implements the D1 surface the collector uses:
 *   db.prepare(sql).bind(...args).first() / .all() / .run() / db.batch([...])
 *
 * A write spy records every INSERT/UPDATE/DELETE/REPLACE target table so T5 can
 * assert the collector performed ZERO writes to the frozen ledger tables.
 */
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

const WRITE_RE = /\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)/i;

export class D1Shim {
  constructor(schemaSql) {
    this.db = new DatabaseSync(":memory:");
    this.db.exec("PRAGMA foreign_keys = ON;");
    this.db.exec(schemaSql);
    this.sqlLog = [];       // every executed SQL
    this.writeLog = [];     // { sql, table }
    this.phase = "setup";
  }

  static fromSchemaFile(path) {
    return new D1Shim(readFileSync(path, "utf8"));
  }

  setPhase(name) { this.phase = name; }
  clearWriteLog() { this.writeLog = []; }

  _record(sql) {
    this.sqlLog.push(sql);
    const m = WRITE_RE.exec(sql);
    if (m) this.writeLog.push({ phase: this.phase, table: m[1].toLowerCase(), sql });
  }

  prepare(sql) { return new D1Statement(this, sql); }

  batch(statements) {
    for (const s of statements) s._execute();
    return Promise.resolve({ success: true });
  }

  /** Escape hatch for test assertions (plain SELECT). */
  queryAll(sql, ...args) { return this.db.prepare(sql).all(...args); }
}

class D1Statement {
  constructor(shim, sql) {
    this.shim = shim;
    this.sql = sql;
    this.bound = [];
  }
  bind(...args) { this.bound = args; return this; }

  _execute() {
    this.shim._record(this.sql);
    return this.shim.db.prepare(this.sql);
  }

  async first(column) {
    const stmt = this._execute();
    const row = stmt.get(...this.bound);
    if (row == null) return null;
    if (column != null) return row[column] ?? null;
    return row;
  }

  async all() {
    const stmt = this._execute();
    const results = stmt.all(...this.bound);
    return { results, success: true };
  }

  async run() {
    const stmt = this._execute();
    const info = stmt.run(...this.bound);
    return { success: true, meta: { changes: Number(info && info.changes) || 0 } };
  }
}
