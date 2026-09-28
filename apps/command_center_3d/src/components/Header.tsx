import type { ConnectionState, SceneState, ViewMode } from "../store/sceneMap";
import { sceneStore } from "../store/SceneStore";

const CONN_COLOR: Record<ConnectionState, string> = {
  CONNECTING: "#fbbf24",
  LIVE: "#7ee2a0",
  RECONNECTING: "#fbbf24",
  POLLING: "#60a5fa",
  OFFLINE: "#f87171",
};

const VIEWS: ViewMode[] = ["world", "graph", "table"];

export function Header({ scene, apiBase }: { scene: SceneState; apiBase: string }) {
  const statuses = Array.from(new Set(scene.sessions.map((s) => s.status))).sort();
  return (
    <div style={{ borderBottom: "1px solid #2a313c", padding: "8px 10px", fontSize: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <strong style={{ fontSize: 14 }}>JCC Visual Command Center</strong>
        <span style={{ color: CONN_COLOR[scene.connection], fontSize: 11 }}>
          ● {scene.connection}
        </span>
      </div>
      <div style={{ color: "#6b7c8f", fontSize: 11, marginTop: 2 }}>API: {apiBase}</div>

      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        {VIEWS.map((v) => (
          <button
            key={v}
            onClick={() => sceneStore.setView(v)}
            style={{
              background: scene.view === v ? "#1f2a36" : "transparent",
              color: scene.view === v ? "#7ee2a0" : "#9fb3c8",
              border: "1px solid #2a313c",
              borderRadius: 4,
              padding: "2px 8px",
              cursor: "pointer",
              font: "inherit",
              textTransform: "capitalize",
            }}
          >
            {v}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
        {scene.agents.map((a) => (
          <button
            key={a.id}
            onClick={() => sceneStore.setAgentFilter(scene.agentFilter === a.id ? null : a.id)}
            style={{
              background: scene.agentFilter === a.id ? "#1f2a36" : "transparent",
              color: scene.agentFilter === a.id ? "#7ee2a0" : "#9fb3c8",
              border: "1px solid #2a313c",
              borderRadius: 4,
              padding: "1px 6px",
              cursor: "pointer",
              font: "inherit",
              fontSize: 11,
            }}
          >
            {a.id}
          </button>
        ))}
        {scene.agentFilter && (
          <button
            onClick={() => sceneStore.setAgentFilter(null)}
            style={{ background: "transparent", color: "#f87171", border: "none", cursor: "pointer", font: "inherit", fontSize: 11 }}
          >
            clear
          </button>
        )}
      </div>

      {statuses.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
          {statuses.map((st) => (
            <button
              key={st}
              onClick={() => sceneStore.setStatusFilter(scene.statusFilter === st ? null : st)}
              style={{
                background: scene.statusFilter === st ? "#1f2a36" : "transparent",
                color: scene.statusFilter === st ? "#7ee2a0" : "#9fb3c8",
                border: "1px solid #2a313c",
                borderRadius: 4,
                padding: "1px 6px",
                cursor: "pointer",
                font: "inherit",
                fontSize: 11,
              }}
            >
              {st}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
