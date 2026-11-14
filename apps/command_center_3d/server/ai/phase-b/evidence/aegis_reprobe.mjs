// AEGIS independent re-probe (NOT forge_proof.mjs).
// Re-verifies G-1 / G-2 repairs and fail-closed negative controls against the
// REAL modules, with fresh mocks. MOCK-ONLY. No network / no real IdP / no D1.

import { createHmac, randomBytes } from "node:crypto";
import { GATE_CODES, AppendOnlyAuditLog } from "../../human-publish-gate.mjs";
import {
  AuthorizationLedger, D1BackedGateState, LedgerReplayError, D1SharedStore,
} from "../ledger.mjs";
import {
  SharedIdentityRevocationStore,
} from "../identity.mjs";
import {
  ROLES, ROLE_BOUND_SCHEMA, issueRoleBoundGrant, mintActorToken,
} from "../roles.mjs";
import {
  phaseBEnforce, safeCoreEnforceWithD1,
} from "../integration.mjs";

// ---- tiny harness ----
let pass = 0, fail = 0;
function check(name, cond, detail = "") {
  if (cond) { pass++; console.log(`PASS  ${name}${detail ? " :: " + detail : ""}`); }
  else { fail++; console.log(`FAIL  ${name}${detail ? " :: " + detail : ""}`); }
}

const IDP = "idp-secret-min-16-bytes-long";
const RB_SECRET = "rb-secret-min-16-bytes-long";
const nowMs = Date.now();
const hex64 = () => randomBytes(32).toString("hex");

// Reimplemented (independent) rolebound signer so we can mint a self-approval
// grant with a VALID signature but approverId===executorId (issuance path
// refuses this, so we sign it ourselves to test the enforce-time separation
// check genuinely fires on the production path).
const SIGNED_FIELDS = ["grantId","schema","action","strategyId","strategyVersion","contentHash","nonce","approverId","executorId"];
function enc(f){const t=typeof f;let tag,p;if(t==="string"){tag="s";p=f;}else if(t==="number"){if(Number.isNaN(f)){tag="x";p="nan";}else if(!Number.isFinite(f)){tag="i";p=f<0?"neg-inf":"pos-inf";}else if(Object.is(f,-0)){tag="n";p="neg-zero";}else{tag="n";p=String(f);}}else if(t==="boolean"){tag="b";p=f?"1":"0";}else if(t==="bigint"){tag="B";p=f.toString();}else if(f===null){tag="z";p="null";}else if(f===undefined){tag="u";p="undefined";}else{tag="Y";p=Object.prototype.toString.call(f);}const tok=tag+":"+p;return tok.length+":"+tok;}
function canon(g){return [g.schema,g.grantId,g.action,g.strategyId,g.strategyVersion,g.contentHash,g.nonce,g.approverId,g.executorId,g.issuedAtMs,g.expiresAtMs].map(enc).join("|");}
function signGrant(g,secret){return createHmac("sha256",String(secret)).update(canon(g),"utf8").digest("hex");}

function makeExecutorToken(sub, roles=[ROLES.EXECUTOR]) {
  return mintActorToken({ sub, roles, idpSecret: IDP, nowMs });
}
// Approver token (MFA + APPROVER role) used to mint valid grants via issuance.
function makeApproverToken(sub) {
  return mintActorToken({ sub, roles: [ROLES.APPROVER], idpSecret: IDP, nowMs });
}

// =========================================================
// G-1a: two D1BackedGateState sharing ONE ledger; same nonce twice -> throw
// =========================================================
{
  const ledger = new AuthorizationLedger(new D1SharedStore());
  const s1 = new D1BackedGateState(ledger);
  const s2 = new D1BackedGateState(ledger); // different adapter instance, SAME shared store
  s1.consumedNonces.add("n1"); // first consume (cross-instance A)
  let threw = false, isReplayErr = false;
  try {
    s2.consumedNonces.add("n1"); // second consume (cross-instance B) -> must throw
  } catch (e) {
    threw = true;
    isReplayErr = e instanceof LedgerReplayError;
  }
  check("G-1a", threw && isReplayErr,
    `threw=${threw} isLedgerReplayError=${isReplayErr}`);
}

// =========================================================
// G-1b: safeCoreEnforceWithD1 must NOT mask a returned REPLAY denial,
// AND the thrown-LedgerReplayError path must still yield ok:false REPLAY.
// =========================================================
{
  // (i) core returns a REPLAY denial -> wrapper must surface ok:false (not true)
  async function coreReturnsReplay() {
    return { ok: false, code: GATE_CODES.REPLAY, detail: "core-replay" };
  }
  const ledger1 = new AuthorizationLedger(new D1SharedStore());
  const r1 = await safeCoreEnforceWithD1({ coreEnforce: coreReturnsReplay, ledger: ledger1 });
  check("G-1b(returned-denial)",
    r1.ok === false && r1.code === GATE_CODES.REPLAY,
    `ok=${r1.ok} code=${r1.code}`);

  // (ii) core THROWS LedgerReplayError (cross-instance TOCTOU) -> ok:false REPLAY
  const ledger2 = new AuthorizationLedger(new D1SharedStore());
  const pre = new D1BackedGateState(ledger2);
  pre.consumedNonces.add("dup"); // consume once so re-add throws
  async function coreThrows(args) {
    args.state.consumedNonces.add("dup"); // -> throws LedgerReplayError
    return { ok: true };
  }
  const r2 = await safeCoreEnforceWithD1({ coreEnforce: coreThrows, ledger: ledger2 });
  check("G-1b(thrown-replay)",
    r2.ok === false && r2.code === GATE_CODES.REPLAY,
    `ok=${r2.ok} code=${r2.code}`);
}

// =========================================================
// G-1c: 200 parallel DISTINCT-nonce consumes on one shared ledger.
// =========================================================
{
  const ledger = new AuthorizationLedger(new D1SharedStore());
  const results = await Promise.all(
    Array.from({ length: 200 }, (_, i) =>
      ledger.consumeNonce({
        nonce: "n" + i, grantId: "g" + i, actorHash: "a", strategyId: "s", nowMs,
      }))
  );
  const allOk = results.every((r) => r.ok === true);
  const replays = results.filter((r) => r.code === GATE_CODES.REPLAY).length;
  const size = ledger.store.snapshot().nonces.length;
  check("G-1c", allOk && replays === 0 && size === 200,
    `allOk=${allOk} replays=${replays} ledgerSize=${size}`);
}

// =========================================================
// G-2: two SharedIdentityRevocationStore sharing ONE Map; revoke visible cross-instance.
// =========================================================
{
  const sharedMap = new Map();
  const storeA = new SharedIdentityRevocationStore(sharedMap);
  const storeB = new SharedIdentityRevocationStore(sharedMap);
  storeA.revokeSub("u1");
  check("G-2",
    storeB.isRevoked({ sub: "u1" }) === true && storeB.isRevoked({ sub: "u2" }) === false,
    `b(u1)=${storeB.isRevoked({ sub: "u1" })} b(u2)=${storeB.isRevoked({ sub: "u2" })}`);
}

// =========================================================
// NEGATIVE CONTROLS — production-intended phaseBEnforce path (fail-closed).
// =========================================================

// Helper: build a full valid grant (approver != executor) via issuance path.
async function buildValidGrant(approverSub, executorSub, contentHash) {
  const { verifyIdToken } = await import("../identity.mjs");
  const approverTok = makeApproverToken(approverSub);
  const claims = verifyIdToken(approverTok, { issuerSecret: IDP, nowMs }).claims;
  const issued = issueRoleBoundGrant({
    approverClaims: claims,
    action: "TRADE", strategyId: "S1", strategyVersion: "v1",
    contentHash, executorId: executorSub, ttlMs: 300000, nowMs, secret: RB_SECRET, nonce: hex64(),
  });
  if (!issued.ok) throw new Error("grant issue failed: " + issued.code);
  return issued.grant;
}

// MFA bypass: executor token WITHOUT mfa in amr -> DENY.
{
  const ledger = new AuthorizationLedger(new D1SharedStore());
  const audit = new AppendOnlyAuditLog();
  const worm = { anchor() {} }; // not reached on deny
  const contentHash = hex64();
  // executor token with amr=[] (no mfa) but EXECUTOR role
  const noMfaTok = mintActorToken({ sub: "exec1", roles: [ROLES.EXECUTOR], idpSecret: IDP, nowMs, amr: [] });
  const grant = await buildValidGrant("appr1", "exec1", contentHash);
  const r = phaseBEnforce({
    executorToken: noMfaTok, idpSecret: IDP, grant,
    action: "TRADE", strategyId: "S1", strategyVersion: "v1", contentHash,
    nowMs, secret: RB_SECRET, ledger, audit, worm, riskState: { drawdownPct: 1 },
  });
  check("NC-MFA-bypass", r.ok === false,
    `ok=${r.ok} code=${r.code}`);
}

// Role self-approval: grant with approverId===executorId (validly signed) -> DENY.
{
  const ledger = new AuthorizationLedger(new D1SharedStore());
  const audit = new AppendOnlyAuditLog();
  const worm = { anchor() {} };
  const selfId = "selfuser";
  const contentHash = hex64();
  const grant = {
    schema: ROLE_BOUND_SCHEMA, grantId: "rbg-self", action: "TRADE",
    strategyId: "S1", strategyVersion: "v1", contentHash, nonce: "n-self",
    approverId: selfId, executorId: selfId, issuedAtMs: nowMs, expiresAtMs: nowMs + 300000,
  };
  grant.signature = signGrant(grant, RB_SECRET); // valid signature, but separation violated
  const execTok = makeExecutorToken(selfId);
  const r = phaseBEnforce({
    executorToken: execTok, idpSecret: IDP, grant,
    action: "TRADE", strategyId: "S1", strategyVersion: "v1", contentHash,
    nowMs, secret: RB_SECRET, ledger, audit, worm, riskState: { drawdownPct: 1 },
  });
  check("NC-role-self-approval", r.ok === false,
    `ok=${r.ok} code=${r.code}`);
}

// Risk override: riskState breached but otherwise-valid grant -> DENY (never overridden).
{
  const ledger = new AuthorizationLedger(new D1SharedStore());
  const audit = new AppendOnlyAuditLog();
  const worm = { anchor() {} };
  const contentHash = hex64();
  const grant = await buildValidGrant("appr2", "exec2", contentHash);
  const execTok = makeExecutorToken("exec2");
  const r = phaseBEnforce({
    executorToken: execTok, idpSecret: IDP, grant,
    action: "TRADE", strategyId: "S1", strategyVersion: "v1", contentHash,
    nowMs, secret: RB_SECRET, ledger, audit, worm,
    riskState: { drawdownPct: 99 }, // breach maxDrawdownPct (25)
  });
  check("NC-risk-override", r.ok === false,
    `ok=${r.ok} code=${r.code}`);
}

// F-19 ordering: WORM anchor tip is the LAST audit entry (PHASEB_ALLOWED),
// i.e. anchored AFTER the final allowed append.
{
  const ledger = new AuthorizationLedger(new D1SharedStore());
  const audit = new AppendOnlyAuditLog();
  const anchored = [];
  const worm = { anchor(tip, t) { anchored.push({ tip, t }); } };
  const contentHash = hex64();
  const grant = await buildValidGrant("appr3", "exec3", contentHash);
  const execTok = makeExecutorToken("exec3");
  const beforeLen = audit.entries().length;
  const r = phaseBEnforce({
    executorToken: execTok, idpSecret: IDP, grant,
    action: "TRADE", strategyId: "S1", strategyVersion: "v1", contentHash,
    nowMs, secret: RB_SECRET, ledger, audit, worm, riskState: { drawdownPct: 1 },
  });
  const entries = audit.entries();
  const last = entries[entries.length - 1];
  const tipMatches = anchored.length === 1 && anchored[0].tip === last.hash;
  const lastIsAllowed = last && last.record && last.record.type === "PHASEB_ALLOWED";
  const appendedThenAnchored = entries.length === beforeLen + 1 && tipMatches;
  check("NC-F19-ordering",
    r.ok === true && lastIsAllowed && appendedThenAnchored,
    `allowed=${r.ok} lastRecordType=${last && last.record && last.record.type} anchored=${anchored.length} tipMatch=${tipMatches}`);
}

console.log(`\nAEGIS_REPROBE pass=${pass} fail=${fail}`);
process.exit(fail === 0 ? 0 : 1);
