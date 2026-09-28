import { describe, it, expect } from "vitest";
import { mapScene, EMPTY_SCENE } from "./sceneMap";

/** A realistic jcc-scene/1 document (mirrors server.build_scene output). */
function fixture() {
  return {
    schema_version: "jcc-scene/1",
    generated_at: 1234.5,
    source_legend: {},
    projection_only: true,
    system: {
      source: "LIVE",
      data: {
        provider_name: "mock",
        provider_mode: "MOCK",
        provider_verified: false,
        provider_is_real: false,
        actuation_enabled: false,
        policy_version: "p3.0.0",
        wal: true,
        localhost_only: true,
        build: "jcc-v1.2-visual",
        projection_only: true,
        green_protected: true,
      },
    },
    agents: {
      source: "DERIVED",
      data: {
        agents: [
          { agent: "workbuddy", session_count: 2, statuses: { active: 2 }, session_ids: ["s1", "s2"], latest_risk_band: "high" },
          { agent: "claude", session_count: 1, statuses: { active: 1 }, session_ids: ["s3"], latest_risk_band: "medium" },
        ],
        distinct_agent_count: 2,
      },
    },
    sessions: {
      source: "LIVE",
      data: [
        { session_id: "s1", agent: "workbuddy", project: "p", status: "active", current_assessment: { band: "high", risk_score: 0.9, escalate: true } },
        { session_id: "s2", agent: "workbuddy", project: "p", status: "active", current_assessment: { band: "low" } },
        { session_id: "s3", agent: "claude", project: "p", status: "active", current_assessment: { band: "medium" } },
      ],
    },
    assessments: { source: "LIVE", data: [] },
    directives: {
      source: "LIVE",
      data: [
        { directive_id: "d1", action: "VERIFY", session_id: "s1", acted: false, signed: false },
      ],
    },
    risks: {
      source: "DERIVED",
      data: { band_distribution: { high: 1, low: 1, medium: 1 }, escalation_count: 1, high_risk_session_count: 1, high_risk_session_ids: ["s1"] },
    },
    budget: {
      source: "DERIVED",
      data: { directive_count: 1, escalations: 0, retries: 0, cost_units_used: 5.0, uncostable_actions: [], cost_fully_known: true },
    },
    capabilities: { source: "RELEASE", data: {} },
    shadow: { source: "UNKNOWN", data: null, reason: "not wired" },
    audit_summary: { source: "LIVE", data: { window_entries: 3, chain_intact: true, chain_entries: 3, first_seq: 1, last_seq: 3, truncated: false } },
  };
}

describe("mapScene", () => {
  it("maps a full LIVE scene into the store shape", () => {
    const s = mapScene(fixture());
    expect(s.schemaVersion).toBe("jcc-scene/1");
    expect(s.core?.providerIsReal).toBe(false);
    expect(s.core?.actuationEnabled).toBe(false);
    expect(s.agents).toHaveLength(2);
    expect(s.agents[0].id).toBe("workbuddy");
    expect(s.agents[0].sessionCount).toBe(2);
    expect(s.agents[0].latestRiskBand).toBe("high");
    expect(s.sessions).toHaveLength(3);
    expect(s.sessions[0].riskBand).toBe("high");
    expect(s.sessions[0].source).toBe("LIVE");
    expect(s.decisions).toHaveLength(1);
    expect(s.decisions[0].action).toBe("VERIFY");
    expect(s.risks?.bandDistribution).toEqual({ high: 1, low: 1, medium: 1 });
    expect(s.budget?.costUnitsUsed).toBe(5.0);
    expect(s.budget?.costFullyKnown).toBe(true);
    expect(s.capabilitiesSource).toBe("RELEASE");
    expect(s.shadowSource).toBe("UNKNOWN");
    expect(s.auditChainIntact).toBe(true);
    expect(s.loaded).toBe(true);
    expect(s.error).toBeNull();
  });

  it("anti-fabrication: an UNKNOWN system degrades to core=null (never coerced)", () => {
    const doc: any = fixture();
    doc.system = { source: "UNKNOWN", data: null, reason: "x" };
    const s = mapScene(doc);
    expect(s.core).toBeNull();
    expect(s.shadowSource).toBe("UNKNOWN");
  });

  it("never throws on a malformed / partial payload", () => {
    expect(() => mapScene(null)).not.toThrow();
    expect(() => mapScene({})).not.toThrow();
    // A successful (even empty) map yields a loaded-but-empty state, never a crash.
    const s = mapScene({});
    expect(s.loaded).toBe(true);
    expect(s.error).toBeNull();
    expect(s.agents).toEqual([]);
    expect(s.core).toBeNull();
  });
});
