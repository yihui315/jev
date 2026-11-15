// JEV V1.6 Track B - six-role vocabulary mapping.
//
// THE PROBLEM: three vocabularies coexist.
//   1. V1.6 requirement roles (owner-facing): Market Analyst / Strategy
//      Researcher / Risk Officer / Execution Planner / Performance Analyst /
//      Supervisor.
//   2. Code roles in the frozen orchestrator: SPOTTER / PRIOR / EDGE / KELLY /
//      TAKER / CLOSER (agent-orchestrator.mjs AI_ROLES + the two deterministic
//      gates KELLY and TAKER).
//   3. An older order-side vocabulary (SPOTTER/ANALYST/STRATEGIST/RISK/
//      EXECUTOR/CLOSER) recorded as `naming_discrepancy` in the gateway.
//
// THE RULE HERE: this table is a DRAFT for the owner to ratify. Code identifiers
// are NOT renamed. `decisionStatus` marks every row as owner-decided draft, and
// `codeChanged` is false for all rows: no frozen file was renamed or edited.

export const ROLE_MAPPING_STATUS = "OWNER-DECIDED DRAFT (pending owner sign-off)";
export const CODE_ROLES = Object.freeze(["SPOTTER", "PRIOR", "EDGE", "KELLY", "TAKER", "CLOSER"]);

export const ROLE_MAPPING = Object.freeze([
  {
    order: 1,
    requirementRole: "Market Analyst",
    codeRole: "SPOTTER",
    kind: "llm",
    semantics: "Discover tradable opportunities and produce the market observation that seeds the run.",
    confidence: "HIGH",
    codeChanged: false,
  },
  {
    order: 2,
    requirementRole: "Strategy Researcher",
    codeRole: "PRIOR",
    kind: "llm",
    semantics: "Turn observations into a regime/prior hypothesis, separating fact from inference.",
    confidence: "MEDIUM",
    codeChanged: false,
  },
  {
    order: 3,
    requirementRole: "Risk Officer",
    codeRole: "EDGE",
    kind: "llm-and-server",
    semantics: "Challenge the hypothesis (counter-evidence) at the LLM layer; the BINDING risk decision is the deterministic server gate in risk-gate.mjs, never an LLM.",
    confidence: "MEDIUM",
    codeChanged: false,
  },
  {
    order: 4,
    requirementRole: "Execution Planner",
    codeRole: "TAKER",
    kind: "deterministic",
    semantics: "Build the proposal-only execution plan. Position sizing is KELLY (also deterministic, currently fail-closed/BLOCKED without server-verified OOS samples).",
    confidence: "HIGH",
    codeChanged: false,
  },
  {
    order: 5,
    requirementRole: "Performance Analyst",
    codeRole: "CLOSER",
    kind: "llm",
    semantics: "Post-hoc review, exit/invalidations, failure modes and next validation step.",
    confidence: "MEDIUM",
    codeChanged: false,
  },
  {
    order: 6,
    requirementRole: "Supervisor",
    codeRole: null,
    kind: "server-and-human",
    semantics: "No code role exists for this. Supervision is the server risk gate plus human owner sign-off; nothing in the AI layer can approve its own execution.",
    confidence: "NONE_IN_CODE",
    codeChanged: false,
  },
]);

export function roleMappingDocument() {
  return {
    status: ROLE_MAPPING_STATUS,
    codeRoles: CODE_ROLES.slice(),
    renamePerformed: false,
    rows: ROLE_MAPPING.map((r) => ({ ...r })),
    note: "Ratification of this table is an OWNER decision. Until then, code identifiers stay as they are and the V1.6 names are display-layer only.",
  };
}
