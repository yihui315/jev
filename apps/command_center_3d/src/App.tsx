import { useEffect } from "react";
import { useSyncExternalStore } from "react";
import { sceneStore } from "./store/SceneStore";
import { CommandCenter3D } from "./scene/CommandCenter3D";

// Read-only API base. Defaults to the BLUE v1.1 instance (4779); falls back to
// GREEN (4778) only for read-only open endpoints. Override with VITE_API_BASE.
const API_BASE: string =
  (import.meta as unknown as { env?: { VITE_API_BASE?: string } }).env
    ?.VITE_API_BASE || "http://127.0.0.1:4779";

export function App() {
  const scene = useSyncExternalStore(sceneStore.subscribe, sceneStore.getSnapshot);

  useEffect(() => {
    void sceneStore.load(API_BASE);
  }, []);

  const visibleSessions = scene.agentFilter
    ? scene.sessions.filter((s) => s.agent === scene.agentFilter)
    : scene.sessions;

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        color: "#d6dde6",
        background: "#0e1116",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <CommandCenter3D scene={scene} />
      </div>
      <aside
        style={{
          width: 360,
          overflow: "auto",
          borderLeft: "1px solid #2a313c",
          padding: 12,
        }}
      >
        <h1 style={{ fontSize: 16, margin: "0 0 4px" }}>
          JCC Visual Command Center
        </h1>
        <p style={{ fontSize: 12, color: "#8aa0b4" }}>API: {API_BASE}</p>
        {scene.error && (
          <p style={{ color: "#ff9a9a" }}>error: {scene.error}</p>
        )}
        {!scene.loaded && <p style={{ color: "#8aa0b4" }}>loading…</p>}

        <h2 style={{ fontSize: 13 }}>Agents ({scene.agents.length})</h2>
        <ul style={{ paddingLeft: 16 }}>
          {scene.agents.map((a) => (
            <li key={a.id}>
              <button
                onClick={() => sceneStore.setAgentFilter(scene.agentFilter === a.id ? null : a.id)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: scene.agentFilter === a.id ? "#7ee2a0" : "#d6dde6",
                  cursor: "pointer",
                  font: "inherit",
                }}
              >
                {a.id} — {a.adapterState}
              </button>
            </li>
          ))}
        </ul>

        <h2 style={{ fontSize: 13 }}>
          Sessions ({visibleSessions.length}
          {scene.agentFilter ? ` · ${scene.agentFilter}` : ""})
        </h2>
        <ul style={{ paddingLeft: 16 }}>
          {visibleSessions.slice(0, 60).map((s) => (
            <li
              key={s.id}
              onClick={() => sceneStore.selectSession(s.id)}
              style={{ cursor: "pointer", color: scene.selectedSessionId === s.id ? "#7ee2a0" : "#d6dde6" }}
            >
              {s.id} · {s.agent} · {s.status}
              {s.riskBand ? ` · ${s.riskBand}` : ""}
            </li>
          ))}
        </ul>

        <h2 style={{ fontSize: 13 }}>Decisions ({scene.decisions.length})</h2>
        <ul style={{ paddingLeft: 16 }}>
          {scene.decisions.slice(0, 60).map((d) => (
            <li key={d.directive_id}>
              {d.action} · acted={String(d.acted)} · signed={String(d.signed)}
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
