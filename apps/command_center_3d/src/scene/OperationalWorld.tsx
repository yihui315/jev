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
 *
 * WHY THIS FILE IS INSTANCED RATHER THAN A LIST OF <mesh> ELEMENTS
 * ---------------------------------------------------------------
 * Measured (leg B8_W10_BROWSER_PERF, 2026-09-30): the previous shape issued
 * ~1.05 draw calls PER MISSION -- 1054 draw calls/frame at 1000 missions, one
 * <mesh> per body plus one per halo plus one per deliverable satellite plus one
 * per project ring and core. The cost therefore grew with the DOMAIN, which is
 * the one thing the world is supposed to scale to, and the frame time grew with
 * it (3.2 FPS at 1000 objects in real Chrome).
 *
 * The fix is ARCHITECTURAL, not a GPU upgrade: the same nodes are drawn with a
 * fixed number of draw calls, because identical geometry differs only by
 * per-instance transform and colour. Concretely:
 *
 *   mission bodies (active)   -> 1 InstancedMesh   (instanceColor = status colour)
 *   mission bodies (terminal) -> 1 InstancedMesh   (dimmed emissive)
 *   attention halos           -> 1 InstancedMesh   (per-instance pulse scale)
 *   deliverable satellites    -> 1 InstancedMesh
 *   project rings             -> 1 InstancedMesh
 *   project cores             -> 1 InstancedMesh
 *
 * so the draw-call count is a property of the SCENE DESIGN (~6 + helpers) instead
 * of a property of how much work the operator has. Triangle count still scales
 * with the domain, which is honest and expected; what no longer scales is the
 * per-object CPU submission cost that dominated the old measurement.
 *
 * LOD is applied to the sphere detail by node count. This is a deliberate,
 * documented trade: at 1000 nodes the bodies are ~17 px wide on a 1280x720
 * viewport, so the tessellation of the previous shape bought nothing visible and
 * cost 1152 triangles each.
 *
 * Two properties of the old file are preserved exactly, because the Wave 9.3
 * world contract depends on them:
 *   * position is still a pure function of the mission's stable angle/radius and
 *     its project's stable ring position -- refreshing the same data re-renders
 *     the same layout, and nothing here randomises;
 *   * clicking/hovering a mission still resolves to the SAME mission object, so
 *     World / Graph / Table continue to name one entity.
 */
import { useLayoutEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, type ThreeEvent } from "@react-three/fiber";
import * as THREE from "three";
import type { DomainWorld, WorldMission } from "../store/domainWorld";
import { missionStatusColor } from "../app/status";

const ATTENTION_PULSE_MS = 1600;

/** Terminal lifecycles are dimmed so a settled world reads calm. */
function isTerminal(status: string): boolean {
  return status === "COMPLETED" || status === "FAILED" || status === "CANCELLED";
}

/**
 * Sphere tessellation by node count. Values are (widthSegments, heightSegments);
 * triangles per body = width * height * 2. Chosen from the measured angular size
 * of a body on the operator's viewport, not from taste.
 */
function bodyDetail(n: number): [number, number] {
  if (n <= 200) return [16, 12]; // 384 tris
  if (n <= 600) return [10, 8]; // 160 tris
  return [8, 6]; // 96 tris
}

interface Placed {
  mission: WorldMission;
  x: number;
  z: number;
  terminal: boolean;
  attention: boolean;
  satellite: boolean;
  color: THREE.Color;
}

/** Flatten the projection into placed instances. Pure; no randomness. */
function placeAll(world: DomainWorld): Placed[] {
  const out: Placed[] = [];
  for (const project of world.projects) {
    const [px, , pz] = project.position;
    for (const mission of project.missions) {
      const terminal = isTerminal(mission.status);
      out.push({
        mission,
        x: px + Math.cos(mission.angle) * mission.radius,
        z: pz + Math.sin(mission.angle) * mission.radius,
        terminal,
        attention: mission.status === "NEEDS_ATTENTION",
        satellite: mission.hasDeliverables && !terminal,
        color: new THREE.Color(missionStatusColor(mission.status)),
      });
    }
  }
  return out;
}

/** A stable body radius so the click target and the visual agree. */
const BODY_RADIUS = 0.32;

interface BodyLayerProps {
  items: Placed[];
  detail: [number, number];
  emissiveIntensity: number;
  scale: number;
  onPick: (m: WorldMission) => void;
  onHover: (m: WorldMission | null) => void;
  testId?: string;
}

/**
 * One InstancedMesh for a whole class of mission bodies.
 *
 * Matrices and colours are written in a layout effect keyed on the instance
 * list, so a data refresh rewrites the same buffers instead of remounting
 * geometry; the render loop itself allocates nothing.
 */
function BodyLayer({
  items,
  detail,
  emissiveIntensity,
  scale,
  onPick,
  onHover,
}: BodyLayerProps) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const count = items.length;

  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh || count === 0) return;
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const pos = new THREE.Vector3();
    const scl = new THREE.Vector3(scale, scale, scale);
    for (let i = 0; i < count; i++) {
      const it = items[i];
      pos.set(it.x, 0, it.z);
      m.compose(pos, q, scl);
      mesh.setMatrixAt(i, m);
      mesh.setColorAt(i, it.color);
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    // Instance bounds are not derived from the geometry (which is a unit
    // sphere), so culling against it would hide every instance except one.
    mesh.computeBoundingSphere();
  }, [items, count, scale]);

  if (count === 0) return null;
  return (
    <instancedMesh
      ref={ref}
      args={[undefined, undefined, count]}
      frustumCulled={false}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        e.stopPropagation();
        const i = e.instanceId;
        if (i != null && items[i]) onPick(items[i].mission);
      }}
      onPointerMove={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        const i = e.instanceId;
        onHover(i != null && items[i] ? items[i].mission : null);
      }}
    >
      <sphereGeometry args={[BODY_RADIUS, detail[0], detail[1]]} />
      <meshStandardMaterial emissiveIntensity={emissiveIntensity} />
    </instancedMesh>
  );
}

/** attention halos — one draw call, per-instance pulsing scale. */
function HaloLayer({ items }: { items: Placed[] }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const count = items.length;
  const m = useMemo(() => new THREE.Matrix4(), []);
  const q = useMemo(() => new THREE.Quaternion(), []);
  const pos = useMemo(() => new THREE.Vector3(), []);
  const scl = useMemo(() => new THREE.Vector3(), []);

  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh || count === 0) return;
    for (let i = 0; i < count; i++) {
      const it = items[i];
      pos.set(it.x, 0, it.z);
      scl.set(1, 1, 1);
      m.compose(pos, q, scl);
      mesh.setMatrixAt(i, m);
      mesh.setColorAt(i, it.color);
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [items, count, m, q, pos, scl]);

  useFrame(() => {
    const mesh = ref.current;
    if (!mesh || count === 0) return;
    const phase = (performance.now() % ATTENTION_PULSE_MS) / ATTENTION_PULSE_MS;
    const s = 1 + phase * 1.6;
    for (let i = 0; i < count; i++) {
      const it = items[i];
      pos.set(it.x, 0, it.z);
      scl.set(s, s, s);
      m.compose(pos, q, scl);
      mesh.setMatrixAt(i, m);
    }
    mesh.instanceMatrix.needsUpdate = true;
  });

  if (count === 0) return null;
  return (
    <instancedMesh
      ref={ref}
      args={[undefined, undefined, count]}
      frustumCulled={false}
      renderOrder={1}
    >
      <sphereGeometry args={[0.42, 12, 8]} />
      <meshStandardMaterial transparent opacity={0.28} emissiveIntensity={0.7} />
    </instancedMesh>
  );
}

/** deliverable satellites — one draw call, slow shared spin. */
function SatelliteLayer({ items }: { items: Placed[] }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const count = items.length;
  const m = useMemo(() => new THREE.Matrix4(), []);
  const q = useMemo(() => new THREE.Quaternion(), []);
  const pos = useMemo(() => new THREE.Vector3(), []);
  const scl = useMemo(() => new THREE.Vector3(1, 1, 1), []);
  const spin = useRef(0);

  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh || count === 0) return;
    for (let i = 0; i < count; i++) {
      const it = items[i];
      pos.set(it.x, 0.3, it.z);
      m.compose(pos, q, scl);
      mesh.setMatrixAt(i, m);
    }
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [items, count, m, q, pos, scl]);

  useFrame((_, delta) => {
    const mesh = ref.current;
    if (!mesh || count === 0) return;
    spin.current += delta * 0.9;
    q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), spin.current);
    for (let i = 0; i < count; i++) {
      const it = items[i];
      pos.set(it.x, 0.3, it.z);
      m.compose(pos, q, scl);
      mesh.setMatrixAt(i, m);
    }
    mesh.instanceMatrix.needsUpdate = true;
  });

  if (count === 0) return null;
  return (
    <instancedMesh
      ref={ref}
      args={[undefined, undefined, count]}
      frustumCulled={false}
      renderOrder={2}
    >
      <octahedronGeometry args={[0.1, 0]} />
      <meshStandardMaterial color="#a78bfa" emissive="#a78bfa" emissiveIntensity={0.5} />
    </instancedMesh>
  );
}

/** project rings + cores — two draw calls for every project. */
function ProjectLayer({ world }: { world: DomainWorld }) {
  const ringRef = useRef<THREE.InstancedMesh>(null);
  const coreRef = useRef<THREE.InstancedMesh>(null);
  const projects = world.projects;
  const count = projects.length;
  const attentionColor = useMemo(() => new THREE.Color("#facc15"), []);
  const idleColor = useMemo(() => new THREE.Color("#334155"), []);
  const coreColor = useMemo(() => new THREE.Color("#7aa2d4"), []);

  useLayoutEffect(() => {
    const m = new THREE.Matrix4();
    const pos = new THREE.Vector3();
    const scl = new THREE.Vector3(1, 1, 1);
    // A torus in the XZ plane: the same -90deg X rotation the single-mesh shape
    // used, expressed once as a quaternion for every instance.
    const ringQ = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(1, 0, 0), -Math.PI / 2);
    const idQ = new THREE.Quaternion();
    const rings = ringRef.current;
    const cores = coreRef.current;
    if (rings && count) {
      for (let i = 0; i < count; i++) {
        const p = projects[i];
        pos.set(p.position[0], p.position[1], p.position[2]);
        m.compose(pos, ringQ, scl);
        rings.setMatrixAt(i, m);
        rings.setColorAt(i, p.counts.attention > 0 ? attentionColor : idleColor);
      }
      rings.instanceMatrix.needsUpdate = true;
      if (rings.instanceColor) rings.instanceColor.needsUpdate = true;
      rings.computeBoundingSphere();
    }
    if (cores && count) {
      for (let i = 0; i < count; i++) {
        const p = projects[i];
        pos.set(p.position[0], p.position[1], p.position[2]);
        m.compose(pos, idQ, scl);
        cores.setMatrixAt(i, m);
        cores.setColorAt(i, coreColor);
      }
      cores.instanceMatrix.needsUpdate = true;
      if (cores.instanceColor) cores.instanceColor.needsUpdate = true;
      cores.computeBoundingSphere();
    }
  }, [projects, count, attentionColor, idleColor, coreColor]);

  if (count === 0) return null;
  return (
    <group>
      <instancedMesh
        ref={ringRef}
        args={[undefined, undefined, count]}
        frustumCulled={false}
      >
        <torusGeometry args={[2.0, 0.06, 8, 40]} />
        <meshStandardMaterial emissiveIntensity={0.4} />
      </instancedMesh>
      <instancedMesh
        ref={coreRef}
        args={[undefined, undefined, count]}
        frustumCulled={false}
      >
        <dodecahedronGeometry args={[0.42, 0]} />
        <meshStandardMaterial emissiveIntensity={0.25} />
      </instancedMesh>
    </group>
  );
}

/** The non-WebGL overlay: counts + hovered/selected mission detail. */
export function OperationalWorldOverlay({
  world,
  hovered,
  scopeNote,
  onGotoMissions,
}: {
  world: DomainWorld;
  hovered: WorldMission | null;
  /**
   * Wave 9.3 — the plane is a projection of a BOUNDED list read, so it can show
   * fewer missions than the domain holds. A map that silently omits nodes is
   * worse than no map: it looks complete. The note is `null` when the
   * projection is the whole domain.
   */
  scopeNote: string | null;
  onGotoMissions: (missionId: string) => void;
}) {
  if (world.empty) {
    return null;
  }
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex items-end justify-between gap-3 p-4">
      <div className="flex max-w-[60%] flex-col gap-2">
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
        {scopeNote ? (
          <div className="pointer-events-auto rounded-md border border-border bg-card/85 px-3 py-2 text-[11px] text-muted-foreground backdrop-blur">
            {scopeNote}
          </div>
        ) : null}
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
  const placed = useMemo(() => placeAll(world), [world]);
  const active = useMemo(() => placed.filter((p) => !p.terminal), [placed]);
  const terminal = useMemo(() => placed.filter((p) => p.terminal), [placed]);
  const halos = useMemo(() => placed.filter((p) => p.attention), [placed]);
  const satellites = useMemo(() => placed.filter((p) => p.satellite), [placed]);
  const detail = useMemo(() => bodyDetail(placed.length), [placed.length]);

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
        <ProjectLayer world={world} />
        <BodyLayer
          items={active}
          detail={detail}
          emissiveIntensity={0.3}
          scale={1}
          onPick={(m) => onSelectMission(m.missionId)}
          onHover={onHoverMission}
        />
        <BodyLayer
          items={terminal}
          detail={detail}
          emissiveIntensity={0.12}
          scale={0.8}
          onPick={(m) => onSelectMission(m.missionId)}
          onHover={onHoverMission}
        />
        <HaloLayer items={halos} />
        <SatelliteLayer items={satellites} />
      </group>
    </Canvas>
  );
}
