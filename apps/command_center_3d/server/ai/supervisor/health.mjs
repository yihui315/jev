// JEV V2.0 Phase E/F Supervisor — local health check (no network).
//
// PURPOSE: a lightweight, pure-local sanity check used both at startup and by
// the (future) periodic health-check loop. It verifies that the resolved config
// is internally consistent, that LIVE mode is not armed without full external
// authorization, and that the local ledger directory is writable.
//
// It performs NO network calls and NO provider/model calls.

import fs from "node:fs";
import path from "node:path";

const ALLOWED_MODES = ["OFF", "SHADOW", "PAPER", "LIVE"];

const REQUIRED_AUTH_FLAGS = [
  "HAS_REAL_IDP",
  "HAS_REAL_D1",
  "HAS_EXTERNAL_WORM",
  "HAS_PAID_API_BUDGET",
  "HAS_REAL_CI",
];

/**
 * @param {object} config  resolved Supervisor config
 * @param {string} [dir]   ledger directory to probe for writability
 * @returns {{healthy:boolean, issues:string[]}}
 */
export function checkHealth(config, dir) {
  const issues = [];

  // 1. Config presence / sanity.
  if (!config) {
    issues.push("CONFIG_MISSING");
  } else {
    if (!ALLOWED_MODES.includes(config.JEV_MODE)) {
      issues.push("CONFIG_MODE_INVALID");
    }
    if (!(config.SHADOW_DAYS > 0)) issues.push("CONFIG_SHADOW_DAYS_INVALID");
    if (!(config.PAPER_DAYS > 0)) issues.push("CONFIG_PAPER_DAYS_INVALID");
  }

  // 2. LIVE must never be considered healthy without full authorization.
  if (config && config.JEV_MODE === "LIVE") {
    const missing = REQUIRED_AUTH_FLAGS.filter((f) => !config[f]);
    if (missing.length) issues.push("LIVE_WITHOUT_AUTH:" + missing.join(","));
    if (config.ENABLE_REAL_TRADING !== "true" || config.ALLOW_BUY_SELL !== "true") {
      issues.push("LIVE_REAL_TRADING_DISABLED");
    }
  }

  // 3. Ledger directory writability probe (create + write + remove a marker).
  if (dir) {
    try {
      fs.mkdirSync(dir, { recursive: true });
      const probe = path.join(dir, `.health-probe-${process.pid}-${Date.now()}.tmp`);
      fs.writeFileSync(probe, "ok", "utf8");
      fs.unlinkSync(probe);
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      issues.push("LEDGER_DIR_NOT_WRITABLE:" + msg);
    }
  }

  return { healthy: issues.length === 0, issues };
}
