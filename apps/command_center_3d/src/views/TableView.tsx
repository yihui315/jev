import type { SceneState } from "../store/sceneMap";
import { sceneStore } from "../store/SceneStore";

export function TableView({ scene }: { scene: SceneState }) {
  const rows = scene.sessions.filter((s) => {
    if (scene.agentFilter && s.agent !== scene.agentFilter) return false;
    if (scene.statusFilter && s.status !== scene.statusFilter) return false;
    return true;
  });

  return (
    <div style={{ padding: 8, overflow: "auto", height: "100%" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ color: "#8aa0b4", textAlign: "left" }}>
            <th style={th}>session</th>
            <th style={th}>agent</th>
            <th style={th}>project</th>
            <th style={th}>status</th>
            <th style={th}>risk</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr
              key={s.id}
              onClick={() => sceneStore.selectSession(s.id)}
              style={{
                cursor: "pointer",
                background: scene.selectedSessionId === s.id ? "#16241c" : "transparent",
                color: "#d6dde6",
              }}
            >
              <td style={td}>{s.id}</td>
              <td style={td}>{s.agent}</td>
              <td style={td}>{s.project}</td>
              <td style={td}>{s.status}</td>
              <td style={{ ...td, color: s.riskBand === "high" ? "#f87171" : "#9fb3c8" }}>
                {s.riskBand}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td style={td} colSpan={5} color="#8aa0b4">
                no sessions match the current filter
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

const th: React.CSSProperties = { padding: "4px 8px", borderBottom: "1px solid #2a313c" };
const td: React.CSSProperties = { padding: "3px 8px", borderBottom: "1px solid #1a1f27" };
