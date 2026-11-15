// JEV V1.6 Track B - the standard AI decision contract (owner ruling shape).
//
// A decision is the ONLY object an AI layer may hand to the risk gate. It is
// validated by a hand-written strict validator (no dependency): unknown fields
// are rejected, types are not coerced, and semantic consistency
// (HOLD vs BUY/SELL) is enforced. The JSON-Schema-shaped document below is a
// documentation artefact with the same field set - the runtime always uses
// validateDecision(), never a third-party validator.
//
// SECURITY NOTES
//   * `confidence` is a MODEL SELF-REPORT. It is carried for audit only and is
//     never read by risk-gate.mjs. A 0.99 confidence cannot turn a rejection
//     into an approval.
//   * `data_mode` (mock|real) is NOT a decision field: it is assigned by the
//     server at persistence time so a model can never claim to be real.

export const DECISION_SCHEMA_VERSION = "jev.ai.decision/v1";

export const ACTIONS = Object.freeze(["HOLD", "BUY", "SELL"]);
export const SIDES = Object.freeze(["BUY", "SELL"]);
export const ORDER_TYPES = Object.freeze(["MARKET", "LIMIT"]);

export const DECISION_FIELDS = Object.freeze([
  "schemaVersion", "strategyId", "symbol", "action", "side", "orderType",
  "quantity", "limitPrice", "confidence", "reason", "riskFlags",
  "marketContextId", "validForSeconds",
]);

const ID_RE = /^[A-Za-z0-9._:-]{3,100}$/;
const SYMBOL_RE = /^[A-Z0-9]{5,20}$/;
const MAX_REASON = 2000;
const MAX_FLAGS = 12;
const MAX_FLAG_CHARS = 200;
const MAX_VALID_FOR_SECONDS = 3600;

function fail(code, field, detail) {
  return { code, field, detail };
}

function isPlainObject(v) {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/**
 * Strict validator. Returns { valid, errors[], decision } where `decision` is
 * a freshly normalized copy when valid (or null when not).
 */
export function validateDecision(input) {
  const errors = [];
  if (!isPlainObject(input)) {
    return { valid: false, errors: [fail("DECISION_NOT_OBJECT", "$", "decision must be a plain object")], decision: null };
  }

  for (const key of Object.keys(input)) {
    if (!DECISION_FIELDS.includes(key)) errors.push(fail("DECISION_UNKNOWN_FIELD", key, "field is not in the decision whitelist"));
  }
  for (const key of DECISION_FIELDS) {
    if (!(key in input)) errors.push(fail("DECISION_MISSING_FIELD", key, "required field is missing"));
  }
  if (errors.length) return { valid: false, errors, decision: null };

  if (input.schemaVersion !== DECISION_SCHEMA_VERSION) {
    errors.push(fail("DECISION_ENUM", "schemaVersion", "must equal " + DECISION_SCHEMA_VERSION));
  }

  const strategyId = input.strategyId;
  if (typeof strategyId !== "string" || !ID_RE.test(strategyId)) errors.push(fail("DECISION_PATTERN", "strategyId", "must match " + String(ID_RE)));

  const symbol = typeof input.symbol === "string" ? input.symbol.toUpperCase() : input.symbol;
  if (typeof symbol !== "string" || !SYMBOL_RE.test(symbol)) errors.push(fail("DECISION_PATTERN", "symbol", "must be uppercase A-Z0-9, 5..20 chars"));

  const action = typeof input.action === "string" ? input.action.toUpperCase() : input.action;
  if (!ACTIONS.includes(action)) errors.push(fail("DECISION_ENUM", "action", "must be one of " + ACTIONS.join("|")));

  if (typeof input.confidence !== "number" || !Number.isFinite(input.confidence)) {
    errors.push(fail("DECISION_TYPE", "confidence", "must be a finite number"));
  } else if (input.confidence < 0 || input.confidence > 1) {
    errors.push(fail("DECISION_RANGE", "confidence", "must be within [0,1]"));
  }

  if (typeof input.reason !== "string" || input.reason.trim().length === 0) {
    errors.push(fail("DECISION_TYPE", "reason", "must be a non-empty string"));
  } else if (input.reason.length > MAX_REASON) {
    errors.push(fail("DECISION_RANGE", "reason", "must be <= " + MAX_REASON + " chars"));
  }

  if (!Array.isArray(input.riskFlags)) {
    errors.push(fail("DECISION_TYPE", "riskFlags", "must be an array of strings"));
  } else if (input.riskFlags.length > MAX_FLAGS) {
    errors.push(fail("DECISION_RANGE", "riskFlags", "must contain <= " + MAX_FLAGS + " items"));
  } else {
    input.riskFlags.forEach((flag, i) => {
      if (typeof flag !== "string" || flag.length === 0 || flag.length > MAX_FLAG_CHARS) {
        errors.push(fail("DECISION_TYPE", "riskFlags[" + i + "]", "each flag must be a 1.." + MAX_FLAG_CHARS + " char string"));
      }
    });
  }

  if (typeof input.marketContextId !== "string" || !ID_RE.test(input.marketContextId)) {
    errors.push(fail("DECISION_PATTERN", "marketContextId", "must match " + String(ID_RE)));
  }

  if (!Number.isInteger(input.validForSeconds)) {
    errors.push(fail("DECISION_TYPE", "validForSeconds", "must be an integer"));
  } else if (input.validForSeconds < 1 || input.validForSeconds > MAX_VALID_FOR_SECONDS) {
    errors.push(fail("DECISION_RANGE", "validForSeconds", "must be within 1.." + MAX_VALID_FOR_SECONDS));
  }

  // Side / orderType / quantity / limitPrice shape depends on the action.
  if (action === "HOLD") {
    if (input.side !== null) errors.push(fail("DECISION_HOLD_SHAPE", "side", "must be null when action=HOLD"));
    if (input.orderType !== null) errors.push(fail("DECISION_HOLD_SHAPE", "orderType", "must be null when action=HOLD"));
    if (input.quantity !== null) errors.push(fail("DECISION_HOLD_SHAPE", "quantity", "must be null when action=HOLD"));
    if (input.limitPrice !== null) errors.push(fail("DECISION_HOLD_SHAPE", "limitPrice", "must be null when action=HOLD"));
  } else if (action === "BUY" || action === "SELL") {
    const side = typeof input.side === "string" ? input.side.toUpperCase() : input.side;
    if (!SIDES.includes(side)) {
      errors.push(fail("DECISION_ENUM", "side", "must be BUY|SELL for an executable action"));
    } else if (side !== action) {
      errors.push(fail("DECISION_EXEC_SHAPE", "side", "must equal action"));
    }
    const orderType = typeof input.orderType === "string" ? input.orderType.toUpperCase() : input.orderType;
    if (!ORDER_TYPES.includes(orderType)) {
      errors.push(fail("DECISION_ENUM", "orderType", "must be MARKET|LIMIT for an executable action"));
    }
    if (typeof input.quantity !== "number" || !Number.isFinite(input.quantity) || input.quantity <= 0) {
      errors.push(fail("DECISION_RANGE", "quantity", "must be a finite number > 0 for an executable action"));
    }
    if (orderType === "LIMIT") {
      if (typeof input.limitPrice !== "number" || !Number.isFinite(input.limitPrice) || input.limitPrice <= 0) {
        errors.push(fail("DECISION_RANGE", "limitPrice", "LIMIT requires a finite limitPrice > 0"));
      }
    } else if (orderType === "MARKET") {
      if (input.limitPrice !== null) errors.push(fail("DECISION_EXEC_SHAPE", "limitPrice", "must be null for MARKET"));
    }
  }

  if (errors.length) return { valid: false, errors, decision: null };

  return {
    valid: true,
    errors: [],
    decision: {
      schemaVersion: DECISION_SCHEMA_VERSION,
      strategyId,
      symbol,
      action,
      side: input.side === null ? null : String(input.side).toUpperCase(),
      orderType: input.orderType === null ? null : String(input.orderType).toUpperCase(),
      quantity: input.quantity,
      limitPrice: input.limitPrice,
      confidence: input.confidence,
      reason: input.reason.trim(),
      riskFlags: input.riskFlags.slice(),
      marketContextId: input.marketContextId,
      validForSeconds: input.validForSeconds,
    },
  };
}

/** Stable canonical JSON (sorted keys) for hashing a decision or its input. */
export function canonicalize(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
  const keys = Object.keys(value).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalize(value[k])).join(",") + "}";
}

/** JSON-Schema-shaped DOCUMENTATION object, equivalent to validateDecision(). */
export function decisionSchemaDocument() {
  const num = { type: "number" };
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    $id: DECISION_SCHEMA_VERSION,
    title: "JEV AI decision",
    type: "object",
    additionalProperties: false,
    required: DECISION_FIELDS.slice(),
    properties: {
      schemaVersion: { const: DECISION_SCHEMA_VERSION },
      strategyId: { type: "string", pattern: ID_RE.source },
      symbol: { type: "string", pattern: SYMBOL_RE.source },
      action: { enum: ACTIONS.slice() },
      side: { enum: [...SIDES, null] },
      orderType: { enum: [...ORDER_TYPES, null] },
      quantity: { anyOf: [num, { type: "null" }] },
      limitPrice: { anyOf: [num, { type: "null" }] },
      confidence: { type: "number", minimum: 0, maximum: 1 },
      reason: { type: "string", minLength: 1, maxLength: MAX_REASON },
      riskFlags: { type: "array", maxItems: MAX_FLAGS, items: { type: "string", minLength: 1, maxLength: MAX_FLAG_CHARS } },
      marketContextId: { type: "string", pattern: ID_RE.source },
      validForSeconds: { type: "integer", minimum: 1, maximum: MAX_VALID_FOR_SECONDS },
    },
    allOf: [
      {
        if: { properties: { action: { const: "HOLD" } } },
        then: {
          properties: {
            side: { type: "null" }, orderType: { type: "null" },
            quantity: { type: "null" }, limitPrice: { type: "null" },
          },
        },
      },
      {
        if: { properties: { action: { enum: ["BUY", "SELL"] } } },
        then: { properties: { quantity: { type: "number", exclusiveMinimum: 0 } } },
      },
    ],
  };
}
