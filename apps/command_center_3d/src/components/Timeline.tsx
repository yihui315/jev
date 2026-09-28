import type { SceneState } from "../store/sceneMap";

function ago(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.round(s / 60)}m`;
}

const TYPE_COLOR: Record<string, string> = {
  "session.created": "#7ee2a0",
  "session.updated": "#60a5fa",
  "assessment.created": "#f87171",
  "directive.created": "#c084fc",
  "directive.acted": "#a78bfa",
  "audit.appended": "#9fb3c8",
  "provider.changed": "#fbbf24",
  "budget.changed": "#fcd34d",
  "shadow.changed": "#f472b6",
  "agent.changed": "#22d3ee",
};

/** Live event feed (newest first) driven by the SSE stream. */
export function Timeline({ scene }: { scene: SceneState }) {
  return (
    <div style={{ padding: 8, overflow: "auto", height: "100%" }}>
      <h3 style={{ fontSize: 12, margin: "0 0 6px", color: "#8aa0b4" }}>
        Live events ({scene.events.length})
      </h3>
      {scene.events.length === 0 && (
        <p style={{ fontSize: 11, color: "#8aa0b4" }}>no events yet — connect to /api/v1/events/stream</p>
      )}
      {scene.events.map((e) => (
        <div
          key={e.eventId}
          style={{
            display: "flex",
            gap: 6,
            alignItems: "center",
            fontSize: 11,
            padding: "2px 0",
            borderBottom: "1px solid #1a1f27",
          }}
        >
          <span style={{ color: TYPE_COLOR[e.type] ?? "#9fb3c8", minWidth: 96 }}>{e.type}</span>
          <span style={{ color: "#6b7c8f", minWidth: 28 }}>{ago(e.ts)}</span>
          <span style={{ color: "#5b6b7d", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {String((e.data.agent as string) || (e.data.session_id as string) || "")}
          </span>
        </div>
      ))}
    </div>
  );
}
