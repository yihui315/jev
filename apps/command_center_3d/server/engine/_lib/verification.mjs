/**
 * JEV V4.3 / §2 - Trusted Verification API validation core.
 *
 * Pure, deterministic and testable. It decides whether a research bundle may
 * produce verified=1 rows and a trusted receipt. It NEVER writes to D1.
 *
 * Two independent gates:
 *   verified=1  - data integrity + identity + pinned versions + thresholds
 *   trusted=1   - ONLY a real GitHub Actions OIDC runner (or an explicitly
 *                 approved service identity). A local E2E HMAC run may write
 *                 verified=1 to exercise the pipeline, but gets trusted=0,
 *                 so evaluateStrategyGate() still refuses release.
 */
import { sha256Hex } from "./service-auth.mjs";

export const SCHEMA = "jev-verification-bundle/v1";
export const MIN_SAMPLE_COUNT = 100;
export const MIN_OOS_TRADES = 30;
export const REQUIRED_FOLDS = 3;
const HEX64 = /^[0-9a-f]{64}$/;
const SHA1 = /^[0-9a-f]{40}$/;

/** D1 strategy_versions.strategy_id <- Python STRATEGIES registry key. */
export const STRATEGY_ID_MAP = Object.freeze({
  FNG_EMA_MR: "fng_ema_mean_reversion",
  BRC20_RSI_MR: "brc20_rsi_mean_reversion"
});

function isIso(value) {
  return typeof value === "string" && value.length >= 20 && Number.isFinite(Date.parse(value));
}

function safeInt(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

/**
 * Verify the bundle seal. `canonical_payload` is emitted by the Python runner
 * and is the AUTHORITATIVE data source: hashing exactly these bytes avoids any
 * Python/JS float-formatting ambiguity. Callers must use `core`, not the
 * outer object's duplicate fields.
 */
export async function parseAndSealBundle(parsed) {
  const errors = [];
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, errors: ["BUNDLE_NOT_OBJECT"], core: null };
  }
  const canonicalPayload = String(parsed.canonical_payload || "");
  const declared = String(parsed.bundle_sha256 || "");
  if (!canonicalPayload) errors.push("CANONICAL_PAYLOAD_MISSING");
  if (!HEX64.test(declared)) errors.push("BUNDLE_SHA256_INVALID");
  if (canonicalPayload && HEX64.test(declared)) {
    const actual = await sha256Hex(canonicalPayload);
    if (actual !== declared) errors.push("BUNDLE_SHA256_MISMATCH");
  }
  let core = null;
  if (canonicalPayload) {
    try { core = JSON.parse(canonicalPayload); }
    catch (_) { errors.push("CANONICAL_PAYLOAD_UNREADABLE"); }
  }
  if (core) {
    // Reject an attacker swapping the outer duplicate fields.
    if (core.schema !== parsed.schema || !Array.isArray(core.runs) ||
        !Array.isArray(parsed.runs) || core.runs.length !== parsed.runs.length) {
      errors.push("BUNDLE_OUTER_FIELDS_MISMATCH");
    }
  }
  return { ok: errors.length === 0, errors, core, canonicalPayload, declared };
}

/**
 * Validate provenance, identity pinning, and every run's result integrity.
 * A client CANNOT self-report a passing gate that its own metrics contradict.
 */
export function validateBundle(core, identity) {
  const errors = [];
  if (!core || core.schema !== SCHEMA) errors.push("SCHEMA_MISMATCH");
  const p = (core && core.provenance) || {};
  if (typeof p.repository !== "string" || !p.repository) errors.push("PROVENANCE_REPOSITORY_MISSING");
  for (const key of ["engine_sha256", "strategy_source_sha256", "runner_source_sha256"]) {
    if (!HEX64.test(String(p[key] || ""))) errors.push("PROVENANCE_HASH_INVALID:" + key);
  }
  if (!isIso(p.generated_at)) errors.push("PROVENANCE_TIMESTAMP_INVALID");
  if (p.engine_mode !== "real-python-backtest-engine") errors.push("ENGINE_MODE_UNTRUSTED");
  if (!["github-actions", "local"].includes(p.runner)) errors.push("PROVENANCE_RUNNER_INVALID");
  if (p.git_commit_sha && !SHA1.test(String(p.git_commit_sha))) errors.push("PROVENANCE_COMMIT_SHA_INVALID");

  if (identity && identity.authMethod === "github-actions-oidc") {
    if (p.runner !== "github-actions") errors.push("OIDC_REQUIRES_GITHUB_RUNNER");
    if (p.repository !== identity.repository) errors.push("OIDC_REPOSITORY_MISMATCH");
    if (p.git_commit_sha !== identity.commitSha) errors.push("OIDC_COMMIT_MISMATCH");
    if (identity.workflowRef && p.workflow_ref !== identity.workflowRef) {
      errors.push("OIDC_WORKFLOW_MISMATCH");
    }
  }

  const runs = Array.isArray(core && core.runs) ? core.runs : [];
  if (!runs.length) errors.push("NO_RUNS");
  runs.forEach((run, index) => {
    const tag = `run[${index}]`;
    if (!run || typeof run !== "object") { errors.push(tag + ":INVALID"); return; }
    if (!/^[A-Za-z0-9._:-]{8,120}$/.test(String(run.run_id || ""))) errors.push(tag + ":RUN_ID_INVALID");
    if (STRATEGY_ID_MAP[String(run.python_strategy_id)] !== String(run.strategy_id)) {
      errors.push(tag + ":STRATEGY_ID_MAPPING_MISMATCH");
    }
    if (!/^[A-Za-z0-9._-]{1,40}$/.test(String(run.strategy_version || ""))) errors.push(tag + ":VERSION_INVALID");
    if (!/^[A-Z0-9]{5,20}$/.test(String(run.symbol || ""))) errors.push(tag + ":SYMBOL_INVALID");
    if (!isIso(run.window_from) || !isIso(run.window_to) ||
        Date.parse(run.window_from) >= Date.parse(run.window_to)) {
      errors.push(tag + ":WINDOW_INVALID");
    }
    if (!HEX64.test(String(run.input_sha256 || ""))) errors.push(tag + ":INPUT_HASH_INVALID");
    if (Number(run.folds) !== REQUIRED_FOLDS) errors.push(tag + ":FOLDS_INVALID");
    if (!Array.isArray(run.data_sources) || !run.data_sources.length) errors.push(tag + ":DATA_SOURCES_MISSING");
    if (!Array.isArray(run.walk_forward) || run.walk_forward.length !== REQUIRED_FOLDS) {
      errors.push(tag + ":WALK_FORWARD_FOLDS_MISSING");
    }
    const m = run.metrics || {};
    const g = run.gates || {};
    if (!safeInt(m.sample_count)) errors.push(tag + ":SAMPLE_COUNT_INVALID");
    if (!safeInt(m.oos_trades)) errors.push(tag + ":OOS_TRADES_INVALID");
    if (typeof m.max_drawdown !== "number" || !Number.isFinite(m.max_drawdown)) {
      errors.push(tag + ":MAX_DRAWDOWN_INVALID");
    }
    if (!m.insample || !m.oos || typeof m.insample !== "object" || typeof m.oos !== "object") {
      errors.push(tag + ":METRICS_MISSING");
    }
    if (![0, 1].includes(Number(g.strategy_pass)) || ![0, 1].includes(Number(g.oos_pass))) {
      errors.push(tag + ":GATE_FLAGS_INVALID");
    }
    if (Number(g.strategy_pass) === 1 && (
      m.sample_count < MIN_SAMPLE_COUNT ||
      Number(g.overfit_suspect) !== 0 ||
      Number(m.oos && m.oos.net) < 0
    )) {
      errors.push(tag + ":SELF_REPORTED_PASS_CONTRADICTS_METRICS");
    }
    if (Number(g.oos_pass) === 1 && (
      m.oos_trades < MIN_OOS_TRADES ||
      (Array.isArray(run.walk_forward) && run.walk_forward.some((f) => f.status !== "VALIDATED")) ||
      (run.grid && run.grid.overfit_flag)
    )) {
      errors.push(tag + ":SELF_REPORTED_OOS_PASS_CONTRADICTS_METRICS");
    }
  });
  return { ok: errors.length === 0, errors, runs };
}

/**
 * Per-run evidence decision. `versionRow` is the authoritative D1
 * strategy_versions row; a missing / unfrozen / hash-mismatched version can
 * never yield verified=1.
 */
export function decideEvidence(run, core, identity, versionRow, validationOk) {
  const provenance = (core && core.provenance) || {};
  const checks = {
    validationOk: validationOk === true,
    identityAuthenticated: identity.authMethod === "github-actions-oidc" || identity.authMethod === "hmac-service",
    strategyVersionExists: !!versionRow,
    strategyVersionFrozen: Number(versionRow && versionRow.frozen) === 1,
    strategyVersionMatches: !!versionRow && versionRow.version === run.strategy_version,
    strategySourceHashMatches: !!versionRow &&
      versionRow.source_sha256 === provenance.strategy_source_sha256,
    strategyPass: Number(run.gates && run.gates.strategy_pass) === 1,
    oosPass: Number(run.gates && run.gates.oos_pass) === 1,
    sampleCountOk: Number(run.metrics && run.metrics.sample_count) >= MIN_SAMPLE_COUNT,
    oosTradesOk: Number(run.metrics && run.metrics.oos_trades) >= MIN_OOS_TRADES,
    foldsOk: Number(run.folds) === REQUIRED_FOLDS
  };
  const verified = Object.values(checks).every(Boolean) ? 1 : 0;
  // A local / HMAC test run can NEVER become a release trust anchor.
  const trusted = identity.authMethod === "github-actions-oidc" && identity.trusted === 1 ? 1 : 0;
  return {
    verified,
    trusted,
    status: verified ? "verified" : "failed",
    authMethod: identity.authMethod,
    checks,
    reasons: Object.entries(checks).filter(([, v]) => !v).map(([k]) => k)
  };
}

export const __test = {
  parseAndSealBundle, validateBundle, decideEvidence,
  SCHEMA, STRATEGY_ID_MAP, MIN_SAMPLE_COUNT, MIN_OOS_TRADES, REQUIRED_FOLDS
};
