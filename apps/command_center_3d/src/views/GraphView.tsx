import type { SceneState } from "../store/sceneMap";
import { sceneStore } from "../store/SceneStore";
import { agentAngle, CLUSTER_RADIUS } from "../store/positions";

const W = 520;
const H = 420;
const CX = W / 2;
const CY = H / 2;

function agentXY(id: string): [number, number] {
  const a = agentAngle(id);
  return [CX + Math.cos(a) * CLUSTER_RADIUS * 26, CY + Math.sin(a) * CLUSTER_RADIUS * 26];
}

function riskColor(band: string): string {
  if (band === "critical" || band === "high") return "#f87171";
  if (band === "medium") return "#facc15";
  if (band === "low") return "#4ade80";
  return "#7aa2d4";
}

/** 2D node-link projection sharing the SAME SceneStore as the 3D world. */
export function GraphView({ scene }: { scene: SceneState }) {
  const filtered = scene.sessions.filter((s) => {
    if (scene.agentFilter && s.agent !== scene.agentFilter) return false;
    if (scene.statusFilter && s.status !== scene.statusFilter) return false;
    return true;
  });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "100%", background: "#0e1116" }}>
      {/* edges: agent -> session (deterministic positions) */}
      {filtered.map((s) => {
        const [ax, ay] = agentXY(s.agent);
        const [sx, sy] = sessionXY(s.id);
        return (
          <line
            key={`e-${s.id}`}
            x1={ax}
            y1={ay}
            x2={sx}
            y2={sy}
            stroke="#2a313c"
            strokeWidth={1}
          />
        );
      })}

      {/* CORE */}
      <circle cx={CX} cy={CY} r={18} fill={scene.core?.providerIsReal ? "#7ee2a0" : "#fbbf24"} />
      <text x={CX} y={CY + 4} textAnchor="middle" fontSize={9} fill="#0e1116">
        CORE
      </text>

      {/* agents */}
      {scene.agents.map((a) => {
        const [x, y] = agentXY(a.id);
        const color =
          a.latestRiskBand === "high" || a.latestRiskBand === "critical"
            ? "#f87171"
            : a.latestRiskBand === "medium"
              ? "#facc15"
              : "#60a5fa";
        return (
          <g key={a.id}>
            <circle
              cx={x}
              cy={y}
              r={10}
              fill={color}
              style={{ cursor: "pointer" }}
              onClick={() =>
                sceneStore.setAgentFilter(sceneStore.getSnapshot().agentFilter === a.id ? null : a.id)
              }
            />
            <text x={x} y={y - 14} textAnchor="middle" fontSize={10} fill="#d6dde6">
              {a.id} ({a.sessionCount})
            </text>
          </g>
        );
      })}

      {/* sessions */}
      {filtered.map((s) => {
        const [sx, sy] = sessionXY(s.id);
        const sel = scene.selectedSessionId === s.id;
        return (
          <circle
            key={s.id}
            cx={sx}
            cy={sy}
            r={sel ? 6 : 4}
            fill={riskColor(s.riskBand)}
            stroke={sel ? "#fff" : "none"}
            style={{ cursor: "pointer" }}
            onClick={() => sceneStore.selectSession(s.id)}
          />
        );
      })}
    </svg>
  );
}

function hashJitter(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return (h % 1000) / 1000;
}

/** Deterministic session node position in the 2D graph. */
function sessionXY(id: string): [number, number] {
  return [CX + (hashJitter(id) - 0.5) * 380, CY + (hashJitter(id + "y") - 0.5) * 300];
}
