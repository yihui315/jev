import type { SceneState } from "../store/sceneMap";

function Bar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, padding: "1px 0" }}>
      <span style={{ width: 58, color: "#8aa0b4" }}>{label}</span>
      <span style={{ flex: 1, height: 8, background: "#1a1f27", borderRadius: 4, overflow: "hidden" }}>
        <span style={{ display: "block", height: "100%", width: `${Math.min(100, value * 12)}%`, background: color }} />
      </span>
      <span style={{ width: 22, textAlign: "right", color: "#d6dde6" }}>{value}</span>
    </div>
  );
}

/** Compact risk + budget radar. Reads only from the shared SceneStore. */
export function RiskRadar({ scene }: { scene: SceneState }) {
  const risks = scene.risks;
  const budget = scene.budget;
  if (!risks && !budget) {
    return <p style={{ fontSize: 11, color: "#8aa0b4", padding: 8 }}>no risk/budget data</p>;
  }
  const bands = risks?.bandDistribution ?? {};
  return (
    <div style={{ padding: 8, fontSize: 12 }}>
      <h3 style={{ fontSize: 12, margin: "0 0 6px", color: "#8aa0b4" }}>Risk Radar</h3>
      {Object.entries(bands)
        .sort((a, b) => b[1] - a[1])
        .map(([band, n]) => (
          <Bar
            key={band}
            label={band}
            value={n}
            color={band === "high" || band === "critical" ? "#f87171" : band === "medium" ? "#facc15" : "#4ade80"}
          />
        ))}
      <div style={{ marginTop: 6, fontSize: 11, color: "#9fb3c8" }}>
        escalations: {risks?.escalationCount ?? 0} · high-risk: {risks?.highRiskSessionCount ?? 0}
      </div>
      {budget && (
        <div style={{ marginTop: 4, fontSize: 11, color: "#9fb3c8" }}>
          budget: {budget.costUnitsUsed} units · {budget.directiveCount} directives
          {budget.costFullyKnown ? "" : " · UNCOSTABLE"}
        </div>
      )}
    </div>
  );
}
