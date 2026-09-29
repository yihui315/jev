/**
 * OperationalWorld — the v1.3 operational plane.
 *
 * Where CommandCenter3D renders the ENGINE's telemetry (agent spheres / session
 * satellites), this scene renders the WORK: each Project is a ring in space, its
 * Missions orbit it coloured by lifecycle state, and a mission that has produced
 * a deliverable carries a small satellite.
 *
 * The plane is NOT a source of truth. It is a pure projection of the domain reads
 * (see store/domainWorld.ts). Clicking a mission navigates to it; nothing here
 * ever writes.
 */
import { useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import type { Group, Mesh, MeshStandardMaterial } from "three";
import type { DomainWorld, WorldMission, WorldProject } from "../store/domainWorld";
import { missionStatusColor } from "../app/status";

const ATTENTION_PULSE_MS = 1600;

function MissionBody({
  mission,
  onSelect,
}: {
  mission: WorldMission;
  onSelect: (m: WorldMission) => void;
}) {
  const bodyRef = useRef<Mesh>(null);
  const haloRef = useRef<Mesh>(null);
  const haloMat = useRef<MeshStandardMaterial>(null);
  const satRef = useRef<Mesh>(null);
  const color = missionStatusColor(mission.status);
  const isAttention = mission.status === "NEEDS_ATTENTION";
  const isTerminal =
    mission.status === "COMPLETED" ||
    mission.status === "FAILED" ||
    mission.status === "CANCELLED";

  // All motion is derived from the STABLE angle/radius on the mission, so every
  // frame puts the body in the same place for the same data (no drift).
  useFrame(() => {
    const x = Math.cos(mission.angle) * mission.radius;
    const z = Math.sin(mission.angle) * mission.radius;
    if (bodyRef.current) {
      bodyRef.current.position.x = x;
      bodyRef.current.position.z = z;
    }
    if (satRef.current) {
      satRef.current.position.x = x;
      satRef.current.position.z = z;
      satRef.current.rotation.y += 0.02;
    }
    if (isAttention && haloRef.current && haloMat.current) {
      const t = performance.now();
      const phase = (t % ATTENTION_PULSE_MS) / ATTENTION_PULSE_MS;
      const s = 1 + phase * 1.6;
      haloRef.current.position.x = x;
      haloRef.current.position.z = z;
      haloRef.current.scale.set(s, s, s);
      haloMat.current.opacity = (1 - phase) * 0.55;
      haloRef.current.visible = true;
    }
  });

  // Attention pulses; terminal missions are dimmed so a settled world reads calm.
  const emissiveIntensity = isTerminal ? 0.12 : isAttention ? 0.5 : 0.3;

  return (
    <group>
      <mesh
        ref={bodyRef}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(mission);
        }}
        scale={isTerminal ? 0.8 : 1}
      >
        <sphereGeometry args={[0.32, 24, 24]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={emissiveIntensity} />
      </mesh>
      {isAttention && (
        <mesh ref={haloRef} visible={false}>
          <sphereGeometry args={[0.42, 20, 20]} />
          <meshStandardMaterial
            ref={haloMat}
            color={color}
            transparent
            opacity={0.4}
            emissive={color}
            emissiveIntensity={0.7}
          />
        </mesh>
      )}
      {mission.hasDeliverables && !isTerminal && (
        <mesh ref={satRef} position={[0, 0.3, 0]}>
          <octahedronGeometry args={[0.1, 0]} />
          <meshStandardMaterial color="#a78bfa" emissive="#a78bfa" emissiveIntensity={0.5} />
        </mesh>
      )}
    </group>
  );
}

function ProjectRing({
  project,
  onSelect,
}: {
  project: WorldProject;
  onSelect: (m: WorldMission) => void;
}) {
  const ref = useRef<Group>(null);
  useFrame(() => {
    if (ref.current) ref.current.rotation.y += 0.0006;
  });
  const attention = project.counts.attention > 0;
  return (
    <group position={project.position} ref={ref}>
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <torusGeometry args={[2.0, 0.06, 12, 48]} />
        <meshStandardMaterial
          color={attention ? "#facc15" : "#334155"}
          emissive={attention ? "#facc15" : "#000000"}
          emissiveIntensity={attention ? 0.4 : 0}
        />
      </mesh>
      <mesh>
        <dodecahedronGeometry args={[0.42, 0]} />
        <meshStandardMaterial color="#7aa2d4" emissive="#7aa2d4" emissiveIntensity={0.25} />
      </mesh>
      {project.missions.map((m) => (
        <MissionBody key={m.missionId} mission={m} onSelect={onSelect} />
      ))}
    </group>
  );
}

/** The non-WebGL overlay: counts + hovered/selected mission detail. */
export function OperationalWorldOverlay({
  world,
  hovered,
  onGotoMissions,
}: {
  world: DomainWorld;
  hovered: WorldMission | null;
  onGotoMissions: (missionId: string) => void;
}) {
  if (world.empty) {
    return null;
  }
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex items-end justify-between gap-3 p-4">
      <div className="pointer-events-auto rounded-md border border-border bg-card/85 px-3 py-2 text-xs text-muted-foreground backdrop-blur">
        {hovered ? (
          <span>
            <span className="font-medium text-foreground">{hovered.title}</span>
            {" · "}
            <span className="font-mono">{hovered.status}</span>
            {" · "}
            <button
              className="ml-1 text-primary underline"
              onClick={() => onGotoMissions(hovered.missionId)}
            >
              open
            </button>
          </span>
        ) : (
          "Hover a mission to inspect · click to open"
        )}
      </div>
    </div>
  );
}

export function OperationalWorld({
  world,
  onSelectMission,
  onHoverMission,
}: {
  world: DomainWorld;
  onSelectMission: (missionId: string) => void;
  onHoverMission: (m: WorldMission | null) => void;
}) {
  return (
    <Canvas
      camera={{ position: [0, 9, 14], fov: 50 }}
      onPointerMissed={() => onHoverMission(null)}
    >
      <color attach="background" args={["#0b0f14"]} />
      <ambientLight intensity={0.65} />
      <pointLight position={[12, 14, 12]} intensity={1.1} />
      <gridHelper args={[30, 30, "#22303f", "#141a22"]} />
      <group>
        {world.projects.map((p) => (
          <ProjectRing
            key={p.projectId}
            project={p}
            onSelect={(m) => { onHoverMission(m); onSelectMission(m.missionId); }}
          />
        ))}
      </group>
    </Canvas>
  );
}
