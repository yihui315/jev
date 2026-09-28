import { useEffect } from "react";
import { useSyncExternalStore } from "react";
import { sceneStore } from "./store/SceneStore";
import { CommandCenter3D } from "./scene/CommandCenter3D";
import { TableView } from "./views/TableView";
import { GraphView } from "./views/GraphView";
import { Inspector } from "./components/Inspector";
import { Timeline } from "./components/Timeline";
import { Header } from "./components/Header";

// Read-only API base. Defaults to the v1.2 control plane (4779 bridge / v1.2
// scene+SSE). Override with VITE_API_BASE. Never points at GREEN (4778).
const API_BASE: string =
  (import.meta as unknown as { env?: { VITE_API_BASE?: string } }).env?.VITE_API_BASE ||
  "http://127.0.0.1:4779";

export function App() {
  const scene = useSyncExternalStore(sceneStore.subscribe, sceneStore.getSnapshot);

  useEffect(() => {
    void sceneStore.load(API_BASE);
    sceneStore.connectStream(API_BASE);
    return () => sceneStore.disconnect();
  }, []);

  const main =
    scene.view === "table" ? (
      <TableView scene={scene} />
    ) : scene.view === "graph" ? (
      <GraphView scene={scene} />
    ) : (
      <CommandCenter3D scene={scene} />
    );

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
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <Header scene={scene} apiBase={API_BASE} />
        <div style={{ flex: 1, minHeight: 0, position: "relative" }}>{main}</div>
      </div>
      <aside
        style={{
          width: 340,
          display: "flex",
          flexDirection: "column",
          borderLeft: "1px solid #2a313c",
          overflow: "hidden",
        }}
      >
        <div style={{ flex: "0 0 auto", borderBottom: "1px solid #2a313c" }}>
          <Inspector scene={scene} />
        </div>
        <div style={{ flex: 1, minHeight: 0, borderBottom: "1px solid #2a313c" }}>
          <Timeline scene={scene} />
        </div>
      </aside>
    </div>
  );
}
