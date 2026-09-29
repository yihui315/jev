/**
 * domainWorld — the projection that turns the v1.3 domain into 3D geometry.
 *
 * THE RULE THIS MODULE ENFORCES
 * -----------------------------
 * The 3D plane is NOT a source of truth. It renders what the domain says and
 * nothing else: no decorative node, no placeholder mission, no "demo" cluster.
 * With an empty domain the world is empty. This module is the single place that
 * decides that, and it is pure (no React, no fetch) so the rule is unit-testable
 * and cannot drift from the domain vocabulary.
 *
 * Two consequences made explicit here:
 *   * a mission whose project is unknown is DROPPED and COUNTED as an orphan —
 *     it is never silently attached to some project to make the map look fuller;
 *   * position is a deterministic function of ids, so the map does not shuffle
 *     when the same data is refreshed.
 */
import type { Deliverable, Mission, Project } from "../app/domainApi";
import { missionTone, type StatusTone } from "../app/status";

export interface WorldMission {
  missionId: string;
  title: string;
  projectId: string;
  status: string;
  tone: StatusTone;
  attentionKind: string | null;
  hasDeliverables: boolean;
  /** Stable angle around the project ring, in radians (deterministic). */
  angle: number;
  /** Stable radius from the project centre. */
  radius: number;
}

export interface WorldProject {
  projectId: string;
  name: string;
  missions: WorldMission[];
  counts: { total: number; running: number; attention: number; done: number };
  /** Stable ring position for the project itself. */
  position: [number, number, number];
}

export interface DomainWorld {
  projects: WorldProject[];
  totalMissions: number;
  totalDeliverables: number;
  /** Missions dropped because their project was not in the project list. */
  orphanMissions: number;
  /** True iff there are no projects to render (an honest empty world). */
  empty: boolean;
}

const RING_RADIUS = 6.5;
const MISSION_RADIUS_MIN = 1.4;
const MISSION_RADIUS_SPAN = 1.3;
const RING_Y = 0.6;

/** Stable 32-bit hash of a string (FNV-1a-ish). Deterministic across reloads. */
function hash(s: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

/** Stable unit float in [0,1) from an id. */
function unit(id: string): number {
  return (hash(id) % 100000) / 100000;
}

/**
 * Deterministic position on a ring for the Nth item, distributed by a stable
 * fraction derived from its id so two projects never land on the same angle
 * (modulo a full circle) and the layout is identical across reloads.
 */
function ringPosition(id: string, index: number, total: number): [number, number, number] {
  const base = total > 0 ? (index / total) * Math.PI * 2 : 0;
  // Small id-derived jitter keeps the ring organic without breaking stability.
  const jitter = (unit(id) - 0.5) * 0.35;
  const a = base + jitter;
  return [Math.cos(a) * RING_RADIUS, RING_Y, Math.sin(a) * RING_RADIUS];
}

const RUNNING_STATUSES = new Set(["STARTING", "RUNNING", "VERIFYING"]);
const DONE_STATUSES = new Set(["COMPLETED"]);

export function buildDomainWorld(
  projects: Project[],
  missions: Mission[],
  deliverables: Deliverable[],
): DomainWorld {
  const deliverablesByMission = new Set<string>();
  for (const d of deliverables) {
    if (d.mission_id) deliverablesByMission.add(d.mission_id);
  }

  const byProject = new Map<string, Mission[]>();
  for (const m of projects) byProject.set(m.project_id, []);

  let orphanMissions = 0;
  for (const m of missions) {
    const bucket = byProject.get(m.project_id);
    if (!bucket) {
      // The project this mission claims is not in the list. Do not invent one.
      orphanMissions += 1;
      continue;
    }
    bucket.push(m);
  }

  const worldProjects: WorldProject[] = [];
  projects.forEach((p, pi) => {
    const ms = byProject.get(p.project_id) ?? [];
    const total = ms.length;
    const worldMissions: WorldMission[] = ms
      // deterministic order by id so the ring is stable regardless of API order
      .slice()
      .sort((a, b) => (a.mission_id < b.mission_id ? -1 : a.mission_id > b.mission_id ? 1 : 0))
      .map((m, mi) => {
        const t = total > 0 ? mi / total : 0;
        return {
          missionId: m.mission_id,
          title: m.title,
          projectId: m.project_id,
          status: m.status,
          tone: missionTone(m.status),
          attentionKind: m.attention_kind ?? null,
          hasDeliverables: deliverablesByMission.has(m.mission_id),
          angle: t * Math.PI * 2 + unit(m.mission_id) * 0.2,
          radius: MISSION_RADIUS_MIN + unit(m.mission_id) * MISSION_RADIUS_SPAN,
        };
      });
    worldProjects.push({
      projectId: p.project_id,
      name: p.name,
      missions: worldMissions,
      counts: {
        total,
        running: ms.filter((m) => RUNNING_STATUSES.has(m.status)).length,
        attention: ms.filter((m) => m.status === "NEEDS_ATTENTION").length,
        done: ms.filter((m) => DONE_STATUSES.has(m.status)).length,
      },
      position: ringPosition(p.project_id, pi, projects.length),
    });
  });

  return {
    projects: worldProjects,
    totalMissions: missions.length,
    totalDeliverables: deliverables.length,
    orphanMissions,
    empty: projects.length === 0,
  };
}

/** The 3D world's mission counts, for an overlay header. Pure. */
export function worldCounts(w: DomainWorld): {
  projects: number; missions: number; running: number; attention: number; done: number;
} {
  let running = 0, attention = 0, done = 0, missions = 0;
  for (const p of w.projects) {
    running += p.counts.running;
    attention += p.counts.attention;
    done += p.counts.done;
    missions += p.counts.total;
  }
  return { projects: w.projects.length, missions, running, attention, done };
}
