import { useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import type { Mesh, MeshStandardMaterial } from "three";
import type { SceneState, SessionNode } from "../store/sceneMap";
import { sceneStore } from "../store/SceneStore";
import {
  agentClusterPosition,
  satellitePosition,
} from "../store/positions";

const PULSE_MS = 1300;

function riskColor(band: string): string {
  if (band === "critical" || band === "high") return "#f87171";
  if (band === "medium") return "#facc15";
  if (band === "low") return "#4ade80";
  return "#7aa2d4";
}

function agentColor(band: string): string {
  if (band === "critical" || band === "high") return "#f87171";
  if (band === "medium") return "#facc15";
  if (band === "low") return "#4ade80";
  return "#60a5fa";
}

/** Expanding, fading halo shown when an agent received a live event recently. */
function PulseHalo({ pulseAt, color }: { pulseAt: number; color: string }) {
  const ref = useRef<Mesh>(null);
  const matRef = useRef<MeshStandardMaterial>(null);
  useFrame(() => {
    const age = Date.now() - pulseAt;
    if (age > PULSE_MS || age < 0) {
      if (ref.current) ref.current.visible = false;
      return;
    }
    const t = age / PULSE_MS; // 0..1
    if (ref.current) {
      ref.current.visible = true;
      const s = 1 + t * 1.8;
      ref.current.scale.set(s, s, s);
    }
    if (matRef.current) {
      matRef.current.opacity = (1 - t) * 0.6;
    }
  });
  return (
    <mesh ref={ref} visible={false}>
      <sphereGeometry args={[0.75, 24, 24]} />
      <meshStandardMaterial
        ref={matRef}
        color={color}
        transparent
        opacity={0.5}
        emissive={color}
        emissiveIntensity={0.8}
      />
    </mesh>
  );
}

function CoreNode({ scene }: { scene: SceneState }) {
  const core = scene.core;
  const real = core?.providerIsReal ?? false;
  const color = real ? "#7ee2a0" : "#fbbf24";
  return (
    <group>
      <mesh>
        <icosahedronGeometry args={[0.9, 1]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} />
      </mesh>
      <PulseHalo pulseAt={0} color={color} />
    </group>
  );
}

function AgentCluster({
  agentId,
  latestRiskBand,
  sessionCount,
  pulseAt,
}: {
  agentId: string;
  latestRiskBand: string;
  sessionCount: number;
  pulseAt: number;
}) {
  const pos = agentClusterPosition(agentId);
  const color = agentColor(latestRiskBand);
  return (
    <group position={pos}>
      <mesh
        onClick={(e) => {
          e.stopPropagation();
          sceneStore.setAgentFilter(sceneStore.getSnapshot().agentFilter === agentId ? null : agentId);
        }}
      >
        <sphereGeometry args={[0.6, 32, 32]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.25} />
      </mesh>
      <PulseHalo pulseAt={pulseAt} color={color} />
      <mesh position={[0, 1.0, 0]}>
        <coneGeometry args={[0.12, 0.4, 8]} />
        <meshStandardMaterial color={sessionCount > 0 ? "#cbd5e1" : "#475569"} />
      </mesh>
    </group>
  );
}

function SessionSatellite({ session, selected }: { session: SessionNode; selected: boolean }) {
  const idx = Math.max(0, session.agent ? hashIndex(session.id) : 0);
  return (
    <mesh
      position={satellitePosition(session.agent, idx, 8)}
      onClick={(e) => {
        e.stopPropagation();
        sceneStore.selectSession(session.id);
      }}
      scale={selected ? 1.6 : 1}
    >
      <sphereGeometry args={[0.16, 16, 16]} />
      <meshStandardMaterial
        color={riskColor(session.riskBand)}
        emissive={selected ? "#ffffff" : "#000000"}
        emissiveIntensity={selected ? 0.6 : 0}
      />
    </mesh>
  );
}

/** Stable per-session index for satellite placement (deterministic). */
function hashIndex(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 8;
}

export function CommandCenter3D({ scene }: { scene: SceneState }) {
  const agents = scene.agents;
  const sessionsByAgent = new Map<string, SessionNode[]>();
  for (const s of scene.sessions) {
    const list = sessionsByAgent.get(s.agent) ?? [];
    list.push(s);
    sessionsByAgent.set(s.agent, list);
  }

  return (
    <Canvas camera={{ position: [0, 5, 10], fov: 50 }}>
      <color attach="background" args={["#0e1116"]} />
      <ambientLight intensity={0.6} />
      <pointLight position={[10, 10, 10]} />
      <gridHelper args={[20, 20, "#2a313c", "#1a1f27"]} />

      <CoreNode scene={scene} />

      {agents.map((a) => (
        <AgentCluster
          key={a.id}
          agentId={a.id}
          latestRiskBand={a.latestRiskBand}
          sessionCount={a.sessionCount}
          pulseAt={scene.pulses[a.id] ?? 0}
        />
      ))}

      {agents.flatMap((a) => {
        const sats = sessionsByAgent.get(a.id) ?? [];
        return sats.map((s) => (
          <SessionSatellite
            key={s.id}
            session={s}
            selected={scene.selectedSessionId === s.id}
          />
        ));
      })}
    </Canvas>
  );
}
