import type { SceneState } from "../store/sceneMap";

/**
 * Decision Explanation — why the policy produced what it produced for the
 * selected session. Pure read-out of LIVE assessment/directive facts; it never
 * invents a rationale. Where a fact is missing it says "unknown", not "ok".
 */
export function DecisionExplanation({ scene }: { scene: SceneState }) {
  const sel = scene.selectedSessionId
    ? scene.sessions.find((s) => s.id === scene.selectedSessionId)
    : null;
  if (!sel) {
    return <p style={{ fontSize: 11, color: "#8aa0b4", padding: 8 }}>select a session to see its decision</p>;
  }
  const decisions = scene.decisions.filter((d) => d.sessionId === sel.id);
  const reasons: string[] = [];
  if (sel.riskBand === "high" || sel.riskBand === "critical") {
    reasons.push(`risk band ${sel.riskBand} → policy escalates oversight (VERIFY, requires_human)`);
  } else if (sel.riskBand === "medium") {
    reasons.push("risk band medium → standard review path");
  } else if (sel.riskBand === "low") {
    reasons.push("risk band low → no extra oversight");
  } else {
    reasons.push(`risk band ${sel.riskBand} (unknown)`);
  }
  if (decisions.some((d) => d.action === "ESCALATE")) reasons.push("a directive ESCALATE was issued");
  if (decisions.some((d) => d.action === "RETRY")) reasons.push("a directive RETRY was issued");

  return (
    <div style={{ padding: 8, fontSize: 12 }}>
      <h3 style={{ fontSize: 12, margin: "0 0 6px", color: "#8aa0b4" }}>Decision · {sel.id}</h3>
      <ul style={{ paddingLeft: 16, margin: 0, color: "#d6dde6" }}>
        {reasons.map((r, i) => (
          <li key={i} style={{ marginBottom: 4 }}>{r}</li>
        ))}
      </ul>
      <p style={{ fontSize: 11, color: "#9fb3c8", marginTop: 6 }}>
        directives: {decisions.length || 0}
        {decisions.map((d) => ` · ${d.action}(signed=${d.signed}, acted=${d.acted})`).join("")}
      </p>
      <p style={{ fontSize: 11, color: "#6b7c8f" }}>
        policy is a DIRECTIVE, not an execution: actuation is disabled in v1.2, so no machine
        acts on these outcomes without a human.
      </p>
    </div>
  );
}
