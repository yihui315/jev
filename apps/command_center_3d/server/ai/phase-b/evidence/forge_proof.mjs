// FORGE B — targeted G-1 / G-2 invariants proof (MOCK-ONLY, local).
// Run: node apps/command_center_3d/server/ai/phase-b/evidence/forge_proof.mjs
import {
  AuthorizationLedger, D1BackedGateState, LedgerReplayError, D1SharedStore,
} from "../ledger.mjs";
import {
  safeCoreEnforceWithD1,
} from "../integration.mjs";
import {
  SharedIdentityRevocationStore,
} from "../identity.mjs";
import { GATE_CODES } from "../../human-publish-gate.mjs";

let allPass = true;
function check(name, cond) {
  console.log((cond ? "PASS" : "FAIL") + " - " + name);
  if (!cond) allPass = false;
}

// ---- G-1: cross-instance replay / no double-spend ----
console.log("=== G-1 PROOF ===");

// (a) Two gate instances share ONE ledger; consuming the SAME nonce twice must
// throw LedgerReplayError on the second call.
{
  const store = new D1SharedStore();
  const ledger = new AuthorizationLedger(store);
  const stateA = new D1BackedGateState(ledger);
  const stateB = new D1BackedGateState(ledger);
  stateA.consumedNonces.add("nonce-X"); // first consume (instance A)
  let threw = false;
  try {
    stateB.consumedNonces.add("nonce-X"); // same nonce on instance B
  } catch (e) {
    threw = e instanceof LedgerReplayError;
  }
  check("G-1 cross-instance: 2nd add of same nonce throws LedgerReplayError", threw);
}

// (b) safeCoreEnforceWithD1 wraps the REPLAY throw as { ok:false, code:GATE_CODES.REPLAY }
{
  const store = new D1SharedStore();
  const ledger = new AuthorizationLedger(store);
  // Mock core enforcement that consumes a nonce via the D1-backed adapter.
  const coreEnforce = (a) => {
    a.state.consumedNonces.add("nonce-Y");
    return { ok: true };
  };
  const first = await safeCoreEnforceWithD1({ coreEnforce, coreEnforceArgs: {}, ledger, audit: null });
  const second = await safeCoreEnforceWithD1({ coreEnforce, coreEnforceArgs: {}, ledger, audit: null });
  check("G-1 safeCoreEnforceWithD1: first consume ok", first.ok === true);
  check(
    "G-1 safeCoreEnforceWithD1: 2nd returns ok:false + code:GATE_CODES.REPLAY",
    second.ok === false && second.code === GATE_CODES.REPLAY,
  );
}

// (c) 200-way parallel DISTINCT-nonce consumption on ONE shared ledger.
{
  const store = new D1SharedStore();
  const ledger = new AuthorizationLedger(store);
  const results = await Promise.all(
    Array.from({ length: 200 }, (_, i) =>
      ledger.consumeNonce({
        nonce: "n" + i, grantId: "g" + i, actorHash: "a", strategyId: "s", nowMs: Date.now(),
      })),
  );
  const okCount = results.filter((r) => r.ok === true).length;
  const collisions = results.filter((r) => r.ok === false).length;
  const ledgerCount = store.snapshot().nonces.length;
  check("G-1 200-way: all 200 consumed ok", okCount === 200);
  check("G-1 200-way: 0 collisions", collisions === 0);
  check("G-1 200-way: ledger count == 200", ledgerCount === 200);
}

// ---- G-2: shared revocation across instances ----
console.log("=== G-2 PROOF ===");
{
  const shared = new Map();
  const storeA = new SharedIdentityRevocationStore(shared);
  const storeB = new SharedIdentityRevocationStore(shared);
  storeA.revokeSub("u1");
  check("G-2 storeB sees u1 revoked (true)", storeB.isRevoked({ sub: "u1" }) === true);
  check("G-2 storeB sees u2 not revoked (false)", storeB.isRevoked({ sub: "u2" }) === false);
}

console.log(allPass ? "OVERALL: PASS" : "OVERALL: FAIL");
process.exit(allPass ? 0 : 1);
