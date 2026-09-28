/**
 * sceneMap — the pure transform from the `jcc-scene/1` projection (Wave 3) to
 * the Visual Command Center's SceneState.
 *
 * This module is the ONLY place that knows the wire shape of the projection.
 * Keeping it pure (no React, no fetch, no globals) makes it unit-testable and
 * keeps the store thin. Anti-fabrication discipline is inherited from the
 * server: a section whose `source` is UNKNOWN carries `data: null`, and we
 * surface that as an explicit "unknown" in the UI rather than coercing it to
 * 0 / false / [] / healthy.
 */

export type Source = "LIVE" | "RELEASE" | "DERIVED" | "UNKNOWN";

export interface CoreNode {
  providerName: string;
  providerMode: string;
  providerVerified: boolean;
  providerIsReal: boolean;
  actuationEnabled: boolean;
  policyVersion: string;
  wal: boolean;
  greenProtected: boolean;
  source: Source;
}

export interface AgentNode {
  id: string;
  sessionCount: number;
  statuses: Record<string, number>;
  sessionIds: string[];
  latestRiskBand: string;
  source: Source;
}

export interface SessionNode {
  id: string;
  agent: string;
  project: string;
  status: string;
  riskBand: string;
  source: Source;
}

export interface DecisionNode {
  directiveId: string;
  action: string;
  sessionId: string;
  acted: boolean;
  signed: boolean;
  source: Source;
}

export interface RiskSummary {
  bandDistribution: Record<string, number>;
  escalationCount: number;
  highRiskSessionCount: number;
  highRiskSessionIds: string[];
  source: Source;
}

export interface BudgetSummary {
  directiveCount: number;
  escalations: number;
  retries: number;
  costUnitsUsed: number;
  uncostableActions: string[];
  costFullyKnown: boolean;
  source: Source;
}

export interface SceneEvent {
  eventId: string;
  type: string;
  ts: number;
  data: Record<string, unknown>;
}

export type ViewMode = "world" | "graph" | "table";
export type ConnectionState = "CONNECTING" | "LIVE" | "RECONNECTING" | "POLLING" | "OFFLINE";

export interface AgentCapability {
  status: string;
  reason: string | null;
  reportedStatus?: string;
}

export interface AgentCap {
  adapterSelfStatus?: string;
  adapterState?: string;
  capabilities: Record<string, AgentCapability>;
}

export interface CapabilityMatrix {
  actuationCapabilities: string[];
  actuationSummary?: { actuable: string[]; actuableCapabilityCount: number; note?: string };
  agents: Record<string, AgentCap>;
}

export interface SceneState {
  /** Meta */
  schemaVersion: string | null;
  generatedAt: number | null;
  /** Sections (already source-tagged) */
  core: CoreNode | null;
  agents: AgentNode[];
  sessions: SessionNode[];
  decisions: DecisionNode[];
  risks: RiskSummary | null;
  budget: BudgetSummary | null;
  capabilities: CapabilityMatrix | null;
  capabilitiesSource: Source | null;
  shadowSource: Source | null;
  auditSource: Source | null;
  auditChainIntact: boolean | null;
  /** Live event timeline (newest first). */
  events: SceneEvent[];
  /** agentId -> last pulse timestamp (ms) for halo animation. */
  pulses: Record<string, number>;
  /** Transport / UI-only state — still one store. */
  connection: ConnectionState;
  selectedSessionId: string | null;
  agentFilter: string | null;
  statusFilter: string | null;
  view: ViewMode;
  /** Operator console (Wave 6) — memory-only, never persisted. */
  operatorMode: "view" | "operate";
  controlToken: string;
  pendingAction: { agent: string; action: string } | null;
  lastControlResult: { ok: boolean; status: number; detail: string } | null;
  loaded: boolean;
  error: string | null;
}

export const EMPTY_SCENE: SceneState = {
  schemaVersion: null,
  generatedAt: null,
  core: null,
  agents: [],
  sessions: [],
  decisions: [],
  risks: null,
  budget: null,
  capabilities: null,
  capabilitiesSource: null,
  shadowSource: null,
  auditSource: null,
  auditChainIntact: null,
  events: [],
  pulses: {},
  connection: "CONNECTING",
  selectedSessionId: null,
  agentFilter: null,
  statusFilter: null,
  view: "world",
  operatorMode: "view",
  controlToken: "",
  pendingAction: null,
  lastControlResult: null,
  loaded: false,
  error: null,
};

interface Section {
  source: Source;
  data: unknown;
  reason?: string;
}

function asSection(v: unknown): Section | null {
  if (!v || typeof v !== "object") return null;
  const s = v as Record<string, unknown>;
  return {
    source: (s.source as Source) ?? "UNKNOWN",
    data: s.data,
    reason: typeof s.reason === "string" ? s.reason : undefined,
  };
}

/** Map a raw session row from the projection to a SessionNode. */
function mapSession(raw: Record<string, unknown>): SessionNode {
  const assessment = (raw.current_assessment as Record<string, unknown>) ?? {};
  return {
    id: String(raw.session_id ?? ""),
    agent: String(raw.agent ?? "unknown"),
    project: String(raw.project ?? ""),
    status: String(raw.status ?? "unknown"),
    riskBand: String(assessment.band ?? "unknown"),
    source: "LIVE",
  };
}

/** Map a directive row from the projection to a DecisionNode. */
function mapDecision(raw: Record<string, unknown>): DecisionNode {
  return {
    directiveId: String(raw.directive_id ?? ""),
    action: String(raw.action ?? "UNKNOWN"),
    sessionId: String(raw.session_id ?? ""),
    acted: Boolean(raw.acted),
    signed: Boolean(raw.signed),
    source: "LIVE",
  };
}

/**
 * Transform the `jcc-scene/1` document into SceneState.
 *
 * Every section is read independently; a missing/UNKNOWN section degrades that
 * one slice only. The function never throws on malformed input (the server
 * already guarantees that, but we defend here too so a bad payload can never
 * blank the whole world).
 */
export function mapScene(doc: unknown): SceneState {
  const d = (doc ?? {}) as Record<string, unknown>;
  const out: SceneState = { ...EMPTY_SCENE };

  out.schemaVersion = typeof d.schema_version === "string" ? d.schema_version : null;
  out.generatedAt = typeof d.generated_at === "number" ? d.generated_at : null;

  // --- system -> core -----------------------------------------------------
  const system = asSection(d.system);
  if (system && system.source !== "UNKNOWN" && system.data && typeof system.data === "object") {
    const s = system.data as Record<string, unknown>;
    out.core = {
      providerName: String(s.provider_name ?? "UNKNOWN"),
      providerMode: String(s.provider_mode ?? "UNKNOWN"),
      providerVerified: Boolean(s.provider_verified),
      providerIsReal: Boolean(s.provider_is_real),
      actuationEnabled: Boolean(s.actuation_enabled),
      policyVersion: String(s.policy_version ?? "UNKNOWN"),
      wal: Boolean(s.wal),
      greenProtected: Boolean(s.green_protected),
      source: system.source,
    };
  }

  // --- agents -> cluster summaries ---------------------------------------
  const agents = asSection(d.agents);
  if (agents && agents.source !== "UNKNOWN" && agents.data && typeof agents.data === "object") {
    const ad = agents.data as Record<string, unknown>;
    const list = Array.isArray(ad.agents) ? (ad.agents as Array<Record<string, unknown>>) : [];
    out.agents = list.map((a) => ({
      id: String(a.agent ?? "unknown"),
      sessionCount: Number(a.session_count ?? 0),
      statuses: (a.statuses as Record<string, number>) ?? {},
      sessionIds: Array.isArray(a.session_ids) ? (a.session_ids as string[]) : [],
      latestRiskBand: String(a.latest_risk_band ?? "unknown"),
      source: agents.source,
    }));
  }

  // --- sessions -----------------------------------------------------------
  const sessions = asSection(d.sessions);
  if (sessions && sessions.source !== "UNKNOWN" && Array.isArray(sessions.data)) {
    out.sessions = (sessions.data as Array<Record<string, unknown>>).map(mapSession);
  }

  // --- directives -> decisions --------------------------------------------
  const directives = asSection(d.directives);
  if (directives && directives.source !== "UNKNOWN" && Array.isArray(directives.data)) {
    out.decisions = (directives.data as Array<Record<string, unknown>>).map(mapDecision);
  }

  // --- risks --------------------------------------------------------------
  const risks = asSection(d.risks);
  if (risks && risks.source !== "UNKNOWN" && risks.data && typeof risks.data === "object") {
    const r = risks.data as Record<string, unknown>;
    out.risks = {
      bandDistribution: (r.band_distribution as Record<string, number>) ?? {},
      escalationCount: Number(r.escalation_count ?? 0),
      highRiskSessionCount: Number(r.high_risk_session_count ?? 0),
      highRiskSessionIds: Array.isArray(r.high_risk_session_ids)
        ? (r.high_risk_session_ids as string[])
        : [],
      source: risks.source,
    };
  }

  // --- budget -------------------------------------------------------------
  const budget = asSection(d.budget);
  if (budget && budget.source !== "UNKNOWN" && budget.data && typeof budget.data === "object") {
    const b = budget.data as Record<string, unknown>;
    out.budget = {
      directiveCount: Number(b.directive_count ?? 0),
      escalations: Number(b.escalations ?? 0),
      retries: Number(b.retries ?? 0),
      costUnitsUsed: Number(b.cost_units_used ?? 0),
      uncostableActions: Array.isArray(b.uncostable_actions)
        ? (b.uncostable_actions as string[])
        : [],
      costFullyKnown: Boolean(b.cost_fully_known),
      source: budget.source,
    };
  }

  // --- capabilities (RELEASE matrix: per-agent actuation gating) ----------
  const cap = asSection(d.capabilities);
  out.capabilitiesSource = cap?.source ?? null;
  if (cap && cap.source !== "UNKNOWN" && cap.data && typeof cap.data === "object") {
    const c = cap.data as Record<string, unknown>;
    out.capabilities = {
      actuationCapabilities: Array.isArray(c.actuation_capabilities)
        ? (c.actuation_capabilities as string[])
        : [],
      actuationSummary: (c.actuation_summary as CapabilityMatrix["actuationSummary"]) ?? undefined,
      agents: (c.agents as Record<string, AgentCap>) ?? {},
    };
  }

  // --- shadow / audit (source only for the badges) -----------------------
  out.shadowSource = asSection(d.shadow)?.source ?? null;
  out.shadowSource = asSection(d.shadow)?.source ?? null;
  const audit = asSection(d.audit_summary);
  out.auditSource = audit?.source ?? null;
  if (audit && audit.data && typeof audit.data === "object") {
    out.auditChainIntact = Boolean((audit.data as Record<string, unknown>).chain_intact);
  }

  out.loaded = true;
  out.error = null;
  return out;
}
