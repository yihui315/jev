// T1 - decision contract: legal shapes accepted, illegal shapes rejected.

import { validateDecision, decisionSchemaDocument, DECISION_FIELDS, DECISION_SCHEMA_VERSION, canonicalize } from "../decision-schema.mjs";
import { validHold, validBuy } from "./lib/fixtures.mjs";
import { assert, assertEqual } from "./lib/harness.mjs";

export const id = "t1_decision_schema";
export const title = "Decision schema: whitelist, types, ranges, HOLD/BUY/SELL shape";

export async function run(suite) {
  await suite.check("valid HOLD is accepted and normalized", () => {
    const r = validateDecision(validHold());
    assert(r.valid, "expected valid: " + JSON.stringify(r.errors));
    assertEqual(r.decision.action, "HOLD");
    assertEqual(r.decision.side, null);
    assertEqual(r.decision.quantity, null);
    return "errors=0";
  });

  await suite.check("valid BUY MARKET is accepted", () => {
    const r = validateDecision(validBuy());
    assert(r.valid, JSON.stringify(r.errors));
    assertEqual(r.decision.side, "BUY");
    assertEqual(r.decision.orderType, "MARKET");
    assertEqual(r.decision.quantity, 0.001);
    return "quantity=0.001";
  });

  await suite.check("valid SELL LIMIT is accepted", () => {
    const r = validateDecision(validBuy({ action: "SELL", side: "SELL", orderType: "LIMIT", quantity: 0.002, limitPrice: 61000 }));
    assert(r.valid, JSON.stringify(r.errors));
    assertEqual(r.decision.limitPrice, 61000);
    return "limitPrice=61000";
  });

  await suite.check("unknown extra field is rejected", () => {
    const r = validateDecision({ ...validBuy(), alphaSignal: 1 });
    assert(!r.valid, "expected rejection");
    assert(r.errors.some((e) => e.code === "DECISION_UNKNOWN_FIELD" && e.field === "alphaSignal"), JSON.stringify(r.errors));
    assertEqual(r.decision, null);
    return "DECISION_UNKNOWN_FIELD";
  });

  await suite.check("HOLD carrying quantity/limitPrice is rejected", () => {
    const r = validateDecision(validHold({ quantity: 0.001, limitPrice: 60000 }));
    assert(!r.valid, "expected rejection");
    const codes = r.errors.map((e) => e.code);
    assert(codes.includes("DECISION_HOLD_SHAPE"), JSON.stringify(r.errors));
    assertEqual(codes.filter((c) => c === "DECISION_HOLD_SHAPE").length >= 1, true);
    return "DECISION_HOLD_SHAPE x" + codes.filter((c) => c === "DECISION_HOLD_SHAPE").length;
  });

  await suite.check("BUY without quantity is rejected", () => {
    const r = validateDecision(validBuy({ quantity: null }));
    assert(!r.valid, "expected rejection");
    assert(r.errors.some((e) => e.field === "quantity"), JSON.stringify(r.errors));
    return r.errors.map((e) => e.code).join(",");
  });

  await suite.check("BUY with zero/negative quantity is rejected", () => {
    assert(!validateDecision(validBuy({ quantity: 0 })).valid, "zero must fail");
    assert(!validateDecision(validBuy({ quantity: -1 })).valid, "negative must fail");
    assert(!validateDecision(validBuy({ quantity: "0.001" })).valid, "string must not be coerced");
    return "0 / -1 / string rejected";
  });

  await suite.check("confidence outside [0,1] is rejected", () => {
    assert(!validateDecision(validHold({ confidence: 1.5 })).valid, "1.5 must fail");
    assert(!validateDecision(validHold({ confidence: -0.1 })).valid, "-0.1 must fail");
    assert(!validateDecision(validHold({ confidence: "0.5" })).valid, "string must fail");
    return "1.5 / -0.1 / string rejected";
  });

  await suite.check("enum violations are rejected", () => {
    const r1 = validateDecision(validHold({ action: "LONG" }));
    assert(!r1.valid && r1.errors.some((e) => e.code === "DECISION_ENUM" && e.field === "action"), JSON.stringify(r1.errors));
    const r2 = validateDecision(validBuy({ orderType: "STOP" }));
    assert(!r2.valid && r2.errors.some((e) => e.code === "DECISION_ENUM" && e.field === "orderType"), JSON.stringify(r2.errors));
    const r3 = validateDecision(validBuy({ side: "SELL" }));
    assert(!r3.valid && r3.errors.some((e) => e.code === "DECISION_EXEC_SHAPE"), JSON.stringify(r3.errors));
    return "action/orderType/side enums enforced";
  });

  await suite.check("missing required field is rejected", () => {
    const d = validBuy();
    delete d.marketContextId;
    const r = validateDecision(d);
    assert(!r.valid && r.errors.some((e) => e.code === "DECISION_MISSING_FIELD" && e.field === "marketContextId"), JSON.stringify(r.errors));
    return "DECISION_MISSING_FIELD";
  });

  await suite.check("LIMIT without limitPrice is rejected", () => {
    const r = validateDecision(validBuy({ orderType: "LIMIT", limitPrice: null }));
    assert(!r.valid && r.errors.some((e) => e.field === "limitPrice"), JSON.stringify(r.errors));
    return "limitPrice required for LIMIT";
  });

  await suite.check("schema document matches the validator field set", () => {
    const doc = decisionSchemaDocument();
    assertEqual(doc.additionalProperties, false);
    assertEqual(doc.$id, DECISION_SCHEMA_VERSION);
    const docKeys = Object.keys(doc.properties).sort();
    const fieldKeys = DECISION_FIELDS.slice().sort();
    assertEqual(JSON.stringify(docKeys), JSON.stringify(fieldKeys));
    assertEqual(JSON.stringify(doc.required.slice().sort()), JSON.stringify(fieldKeys));
    return "fields=" + docKeys.length;
  });

  await suite.check("canonicalize is key-order independent", () => {
    assertEqual(canonicalize({ b: 1, a: [2, { d: 4, c: 3 }] }), canonicalize({ a: [2, { c: 3, d: 4 }], b: 1 }));
    return "stable";
  });
}
