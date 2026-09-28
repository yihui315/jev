import type { SceneState } from "../store/sceneMap";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "2px 0" }}>
      <span style={{ color: "#8aa0b4" }}>{k}</span>
      <span style={{ color: "#d6dde6" }}>{v}</span>
    </div>
  );
}

function SourceBadge({ source }: { source: string }) {
  const color =
    source === "LIVE" ? "#7ee2a0" : source === "RELEASE" ? "#60a5fa" : source === "DERIVED" ? "#c084fc" : "#fbbf24";
  return (
    <span style={{ color, fontSize: 11, border: `1px solid ${color}`, borderRadius: 4, padding: "0 4px" }}>
      {source}
    </span>
  );
}

/** Detail panel for the selected session / agent, or the CORE summary. */
export function Inspector({ scene }: { scene: SceneState }) {
  const sel = scene.selectedSessionId
    ? scene.sessions.find((s) => s.id === scene.selectedSessionId)
    : null;

  if (sel) {
    return (
      <div style={{ padding: 8, fontSize: 12 }}>
        <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>Session {sel.id}</h3>
        <Row k="agent" v={sel.agent} />
        <Row k="project" v={sel.project} />
        <Row k="status" v={sel.status} />
        <Row k="risk band" v={<span style={{ color: sel.riskBand === "high" ? "#f87171" : "#9fb3c8" }}>{sel.riskBand}</span>} />
        <Row k="source" v={<SourceBadge source={sel.source} />} />
        <p style={{ color: "#8aa0b4", marginTop: 8 }}>
          Decisions for this session:{" "}
          {scene.decisions.filter((d) => d.sessionId === sel.id).length || 0}
        </p>
      </div>
    );
  }

  if (scene.agentFilter) {
    const a = scene.agents.find((x) => x.id === scene.agentFilter);
    if (a) {
      return (
        <div style={{ padding: 8, fontSize: 12 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>Agent {a.id}</h3>
          <Row k="sessions" v={a.sessionCount} />
          <Row k="latest risk" v={a.latestRiskBand} />
          <Row k="statuses" v={<span style={{ fontSize: 11 }}>{Object.entries(a.statuses).map(([k, v]) => `${k}:${v}`).join(", ") || "—"}</span>} />
          <Row k="source" v={<SourceBadge source={a.source} />} />
        </div>
      );
    }
  }

  const core = scene.core;
  return (
    <div style={{ padding: 8, fontSize: 12 }}>
      <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>CORE (system)</h3>
      {core ? (
        <>
          <Row k="provider" v={`${core.providerName} / ${core.providerMode}`} />
          <Row k="is real" v={core.providerIsReal ? "yes" : "NO (mock)"} />
          <Row k="verified" v={String(core.providerVerified)} />
          <Row k="actuation" v={core.actuationEnabled ? "enabled" : "disabled"} />
          <Row k="policy" v={core.policyVersion} />
          <Row k="wal" v={String(core.wal)} />
          <Row k="green protected" v={String(core.greenProtected)} />
          <Row k="source" v={<SourceBadge source={core.source} />} />
        </>
      ) : (
        <p style={{ color: "#fbbf24" }}>core: UNKNOWN</p>
      )}
      {scene.risks && (
        <p style={{ color: "#8aa0b4", marginTop: 8 }}>
          risks: {scene.risks.highRiskSessionCount} high · {scene.risks.escalationCount} escalations
        </p>
      )}
      {scene.budget && (
        <p style={{ color: "#8aa0b4" }}>
          budget: {scene.budget.costUnitsUsed} units · {scene.budget.directiveCount} directives
          {scene.budget.costFullyKnown ? "" : " · UNCOSTABLE"}
        </p>
      )}
    </div>
  );
}
