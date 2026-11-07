// V4.6 — D1-compatible runtime adapter over Node 22 `node:sqlite`.
//
// WHY: the VERIFIED server-authoritative engine (paper-server.mjs, 1106 lines)
// expects a Cloudflare D1 binding (env.PAPER_DB) with prepare/bind/first/all/run
// /batch. Reusing the engine verbatim requires a local binding, NOT a rewrite.
// This adapter is a TRANSPORT SHIM ONLY: it contains no matching, no accounting,
// no order logic, no event semantics. All state transitions come from the
// upstream engine. The schema comes verbatim from the upstream migrations.
//
// HARD RULES:
//   * no second account / ledger / credential / market-data system
//   * the SQLite file is the runtime instance of the SAME authoritative schema
//   * batch() is atomic (transaction), matching D1 batch semantics
//   * BigInt is normalised at the binding boundary (D1 rejects BigInt)
import { DatabaseSync } from "node:sqlite";
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

const MIGRATIONS = [
  resolve(process.env.JCC_NR_TRUSTED || "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted",
    "migrations/0001_paper_ledger.sql"),
  resolve(process.env.JCC_NR_TRUSTED || "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted",
    "migrations/0002_server_paper_engine.sql"),
  resolve(process.env.JCC_NR_TRUSTED || "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted",
    "migrations/0003_fixed_point.sql"),
];

function bindable(v) {
  if (typeof v === "bigint") return Number(v);   // D1 does not accept BigInt
  if (v === undefined) return null;
  if (v === null || typeof v === "string" || typeof v === "number" || typeof v === "boolean") return v;
  return String(v);
}

class D1Statement {
  constructor(db, sql) {
    this._db = db;
    this.sql = sql;
    this._binds = [];
  }
  bind(...args) {
    const s = new D1Statement(this._db, this.sql);
    s._binds = args.map(bindable);
    return s;
  }
  first() {
    const stmt = this._db.prepare(this.sql);
    const row = stmt.get(...this._binds);
    return row === undefined ? null : { ...row };
  }
  all() {
    const stmt = this._db.prepare(this.sql);
    const rows = stmt.all(...this._binds).map(r => ({ ...r }));
    return { results: rows, success: true, meta: { rows_read: rows.length } };
  }
  run() {
    const stmt = this._db.prepare(this.sql);
    const info = stmt.run(...this._binds);
    return { success: true, meta: { changes: Number(info.changes), last_row_id: Number(info.lastInsertRowid) } };
  }
  raw() { return this._binds.slice(); }
}

export class D1Adapter {
  constructor(dbPath) {
    mkdirSync(dirname(dbPath), { recursive: true });
    this.path = dbPath;
    this.db = new DatabaseSync(dbPath);
    this.db.exec("PRAGMA foreign_keys = ON;");
    this.db.exec("PRAGMA journal_mode = WAL;");
    this.migrations_applied = [];
  }

  applyMigrations() {
    for (const p of MIGRATIONS) {
      if (!existsSync(p)) throw new Error("MIGRATION_MISSING: " + p);
      const sql = readFileSync(p, "utf8");
      this.db.exec(sql);
      this.migrations_applied.push(p);
    }
    return this.migrations_applied.slice();
  }

  prepare(sql) { return new D1Statement(this.db, sql); }

  // D1 batch() is atomic. Results are returned in statement order; the engine
  // reads results[0].meta.changes to detect an optimistic-lock conflict.
  async batch(statements) {
    const out = [];
    this.db.exec("BEGIN IMMEDIATE");
    try {
      for (const s of statements) out.push(s.run());
      this.db.exec("COMMIT");
    } catch (err) {
      this.db.exec("ROLLBACK");
      throw err;
    }
    return out;
  }

  // Read-only projection for the independent audit. Never a second ledger.
  readOnlyEvents(limit = 500) {
    const rows = this.db.prepare(
      "SELECT seq, session_id, event_id, command_id, event_type, payload_json, created_at " +
      "FROM paper_server_events ORDER BY seq ASC LIMIT ?"
    ).all(limit).map(r => ({ ...r }));
    return rows;
  }

  readOnlyCounts() {
    const one = sql => Number(this.db.prepare(sql).get()?.n || 0);
    return {
      sessions: one("SELECT COUNT(*) AS n FROM paper_sessions"),
      accounts: one("SELECT COUNT(*) AS n FROM paper_accounts"),
      orders: one("SELECT COUNT(*) AS n FROM paper_orders"),
      fills: one("SELECT COUNT(*) AS n FROM paper_fills"),
      server_events: one("SELECT COUNT(*) AS n FROM paper_server_events"),
    };
  }

  close() { this.db.close(); }
}
