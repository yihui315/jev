import { Canvas } from "@react-three/fiber";
import type { SceneState } from "../store/SceneStore";

const FALLBACK_AGENTS = [
  { id: "workbuddy", adapterState: "?", aep: false, verified: false },
  { id: "claude", adapterState: "?", aep: false, verified: false },
  { id: "codex", adapterState: "?", aep: false, verified: false },
  { id: "hermes", adapterState: "?", aep: false, verified: false },
];

function agentColor(state: string): string {
  if (state === "CONNECTED") return "#4ade80";
  if (state === "DEGRADED") return "#facc15";
  return "#f87171";
}

function AgentNode({
  position,
  color,
}: {
  position: [number, number, number];
  color: string;
}) {
  return (
    <group position={position}>
      <mesh>
        <sphereGeometry args={[0.6, 32, 32]} />
        <meshStandardMaterial color={color} />
      </mesh>
    </group>
  );
}

/**
 * The 3D Session World (Wave 5 seed). Agents are rendered as a ring of nodes;
 * each agent's sessions are small satellites around it. Reads ONLY from SceneStore
 * via props — it never owns state.
 */
export function CommandCenter3D({ scene }: { scene: SceneState }) {
  const agents = scene.agents.length ? scene.agents : FALLBACK_AGENTS;
  const radius = 3;

  const sessionsByAgent = new Map<string, typeof scene.sessions>();
  for (const s of scene.sessions) {
    const list = sessionsByAgent.get(s.agent) ?? [];
    list.push(s);
    sessionsByAgent.set(s.agent, list);
  }

  return (
    <Canvas camera={{ position: [0, 4, 9], fov: 50 }}>
      <color attach="background" args={["#0e1116"]} />
      <ambientLight intensity={0.6} />
      <pointLight position={[10, 10, 10]} />
      <gridHelper args={[20, 20, "#2a313c", "#1a1f27"]} />

      {agents.map((a, i) => {
        const angle = (i / agents.length) * Math.PI * 2;
        const pos: [number, number, number] = [
          Math.cos(angle) * radius,
          0,
          Math.sin(angle) * radius,
        ];
        const color = agentColor(a.adapterState);
        const sats = sessionsByAgent.get(a.id) ?? [];
        return (
          <group key={a.id}>
            <AgentNode position={pos} color={color} />
            {sats.map((s, j) => {
              const sa = (j / Math.max(1, sats.length)) * Math.PI * 2;
              const child: [number, number, number] = [
                pos[0] + Math.cos(sa) * 1.1,
                0.4,
                pos[2] + Math.sin(sa) * 1.1,
              ];
              return (
                <mesh key={s.id} position={child}>
                  <sphereGeometry args={[0.18, 16, 16]} />
                  <meshStandardMaterial
                    color={s.riskBand === "high" ? "#f87171" : "#7aa2d4"}
                  />
                </mesh>
              );
            })}
          </group>
        );
      })}
    </Canvas>
  );
}
