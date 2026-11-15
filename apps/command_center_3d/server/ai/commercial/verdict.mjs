// commercial/verdict.mjs
//
// Phase G (commercial) — GO / CONDITIONAL GO / NO-GO verdict engine.
//
// The commercial verdict is the FINAL gate. Code completion of B/C/D and a
// green test suite are NOT sufficient. This module enforces the proof-fundamental
// gates programmatically so a GO cannot be faked.
//
// Hard rule: if the data is not REAL, the verdict is NO-GO (refused). No
// synthetic/mock/fixture trading or AI cost may ever feed a commercial verdict.

export const COMMERCIAL_GATES = Object.freeze({
  VALID_SAMPLES_MIN: 100,
  OUT_OF_SAMPLE_MIN: 30,
});

/**
 * @typedef {Object} VerdictInput
 * @property {string}  [dataSource]            MUST be "REAL"
 * @property {number}  [validSamples]          in+out-of-sample valid measured samples
 * @property {number}  [outOfSampleSamples]    distinct out-of-sample measured samples
 * @property {boolean} [aiCostMeasured]        real AI cost accounting present
 * @property {boolean} [riskControlAutomated]  automated risk gate active in the run
 * @property {boolean} [independentAuditSigned] independent auditor signed off
 */

/**
 * Evaluate the commercial verdict.
 * @param {VerdictInput} input
 * @returns {{verdict:"GO"|"CONDITIONAL GO"|"NO-GO", reasons:string[], gateDetail:Object, computable:boolean}}
 */
export function evaluateCommercialVerdict(input) {
  const {
    dataSource = "UNKNOWN",
    validSamples = 0,
    outOfSampleSamples = 0,
    aiCostMeasured = false,
    riskControlAutomated = false,
    independentAuditSigned = false,
  } = input || {};

  const reasons = [];
  const gateDetail = {};

  // Hard rule: no verdict on non-real data.
  if (dataSource !== "REAL") {
    return {
      verdict: "NO-GO",
      reasons: [
        `DATA_NOT_REAL: commercial verdict refused on dataSource=${JSON.stringify(
          dataSource,
        )} (must be 'REAL' with measured trading + AI cost). ` +
          `Synthetic/mock data is NEVER a commercial result.`,
      ],
      gateDetail: { dataSource },
      computable: false,
    };
  }

  // --- Proof-fundamental gates (missing any => NO-GO) ---
  gateDetail.validSamples =
    typeof validSamples === "number" &&
    validSamples >= COMMERCIAL_GATES.VALID_SAMPLES_MIN;
  if (!gateDetail.validSamples) {
    reasons.push(
      `INSUFFICIENT_VALID_SAMPLES: ${validSamples} < ${COMMERCIAL_GATES.VALID_SAMPLES_MIN}`,
    );
  }

  gateDetail.outOfSampleSamples =
    typeof outOfSampleSamples === "number" &&
    outOfSampleSamples >= COMMERCIAL_GATES.OUT_OF_SAMPLE_MIN;
  if (!gateDetail.outOfSampleSamples) {
    reasons.push(
      `INSUFFICIENT_OUT_OF_SAMPLE: ${outOfSampleSamples} < ${COMMERCIAL_GATES.OUT_OF_SAMPLE_MIN}`,
    );
  }

  gateDetail.aiCostMeasured = aiCostMeasured === true;
  if (!gateDetail.aiCostMeasured) {
    reasons.push("AI_COST_NOT_MEASURED: real AI cost accounting required");
  }

  gateDetail.riskControlAutomated = riskControlAutomated === true;
  if (!gateDetail.riskControlAutomated) {
    reasons.push("RISK_CONTROL_NOT_AUTOMATED: automated risk gate required");
  }

  const proofFundamentalsMet =
    gateDetail.validSamples &&
    gateDetail.outOfSampleSamples &&
    gateDetail.aiCostMeasured &&
    gateDetail.riskControlAutomated;

  if (!proofFundamentalsMet) {
    return { verdict: "NO-GO", reasons, gateDetail, computable: true };
  }

  // --- All proof-fundamentals met; only independent audit remains ---
  gateDetail.independentAuditSigned = independentAuditSigned === true;
  if (!gateDetail.independentAuditSigned) {
    return {
      verdict: "CONDITIONAL GO",
      reasons: [
        "INDEPENDENT_AUDIT_PENDING: all evidence gates met, awaiting independent auditor sign-off",
      ],
      gateDetail,
      computable: true,
    };
  }

  return { verdict: "GO", reasons: [], gateDetail, computable: true };
}

/** Convenience: is a verdict a go of any kind? */
export function isGo(verdict) {
  return verdict === "GO" || verdict === "CONDITIONAL GO";
}
