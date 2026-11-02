import { describe, it, expect } from "vitest";
import { buildDomainWorld, worldCounts } from "./domainWorld";
import type { Deliverable, Mission, Project } from "../app/domainApi";

/**
 * Wave 6 — the operational world is a PURE projection of the domain.
 *
 * The load-bearing property: the 3D plane is not a source of truth. It shows
 * what the domain says and nothing else — an empty domain is an empty world, a
 * mission whose project is unknown is counted (not silently placed), and the
 * same data always produces the same layout.
 */

function proj(id: string, name = id): Project {
  return {
    project_id: id, name, root_path: "", description: "",
    created_at: 0, updated_at: 0, archived: false,
  };
}

function mission(id: string, projectId: string, status = "RUNNING"): Mission {
  return {
    mission_id: id, project_id: projectId, title: `M ${id}`, goal: "", agent: "workbuddy",
    connection_ref: null, working_directory: "", context: "", permissions: [], budget_units: 0,
    status, attention_kind: status === "NEEDS_ATTENTION" ? "AGENT_STUCK" : null,
    attention_reason: null, created_at: 0, updated_at: 0, started_at: null, finished_at: null,
    session_id: null,
  };
}

function deliverable(id: string, missionId: string): Deliverable {
  return {
    artifact_id: id, mission_id: missionId, run_id: null, session_id: null,
    kind: "file", name: id, uri: "", sha256: null, bytes: 0, status: "PRODUCED",
    provenance: {}, created_at: 0, mission_title: "",
  };
}

describe("buildDomainWorld", () => {
  it("renders nothing for an empty domain (no decorative nodes)", () => {
    const w = buildDomainWorld([], [], []);
    expect(w.empty).toBe(true);
    expect(w.projects).toEqual([]);
    expect(w.totalMissions).toBe(0);
    expect(w.orphanMissions).toBe(0);
  });

  it("attaches missions to their project and derives counts", () => {
    const p = proj("prj_1", "Alpha");
    const w = buildDomainWorld(
      [p],
      [
        mission("m1", "prj_1", "RUNNING"),
        mission("m2", "prj_1", "NEEDS_ATTENTION"),
        mission("m3", "prj_1", "COMPLETED"),
      ],
      [],
    );
    expect(w.empty).toBe(false);
    expect(w.projects).toHaveLength(1);
    expect(w.projects[0].missions).toHaveLength(3);
    expect(w.projects[0].counts).toEqual({ total: 3, running: 1, attention: 1, done: 1 });
  });

  it("counts an orphan mission instead of silently placing it", () => {
    const p = proj("prj_1");
    const w = buildDomainWorld([p], [mission("m1", "prj_1"), mission("mX", "prj_ghost")], []);
    expect(w.orphanMissions).toBe(1);
    expect(w.projects[0].missions.map((m) => m.missionId)).toEqual(["m1"]);
  });

  it("marks a mission as carrying deliverables only when one references it", () => {
    const w = buildDomainWorld(
      [proj("prj_1")],
      [mission("m1", "prj_1"), mission("m2", "prj_1")],
      [deliverable("a1", "m1")],
    );
    const byId = new Map(w.projects[0].missions.map((m) => [m.missionId, m]));
    expect(byId.get("m1")!.hasDeliverables).toBe(true);
    expect(byId.get("m2")!.hasDeliverables).toBe(false);
  });

  it("is deterministic: identical input yields identical positions", () => {
    const projects = [proj("prj_1"), proj("prj_2")];
    const missions = [mission("m1", "prj_1"), mission("m2", "prj_2"), mission("m3", "prj_1")];
    const a = buildDomainWorld(projects, missions, []);
    const b = buildDomainWorld(projects, missions, []);
    expect(JSON.stringify(a)).toEqual(JSON.stringify(b));
  });

  it("orders missions by id regardless of API order (stable ring)", () => {
    const p = proj("prj_1");
    const a = buildDomainWorld([p], [mission("m3", "prj_1"), mission("m1", "prj_1")], []);
    const b = buildDomainWorld([p], [mission("m1", "prj_1"), mission("m3", "prj_1")], []);
    expect(a.projects[0].missions.map((m) => m.missionId))
      .toEqual(b.projects[0].missions.map((m) => m.missionId));
  });

  it("keeps projects in the order given (stable ring)", () => {
    const w = buildDomainWorld([proj("p_b"), proj("p_a")], [], []);
    expect(w.projects.map((p) => p.projectId)).toEqual(["p_b", "p_a"]);
  });
});

describe("worldCounts", () => {
  it("aggregates across projects", () => {
    const w = buildDomainWorld(
      [proj("p1"), proj("p2")],
      [
        mission("a", "p1", "RUNNING"),
        mission("b", "p1", "NEEDS_ATTENTION"),
        mission("c", "p2", "COMPLETED"),
        mission("d", "p2", "RUNNING"),
      ],
      [],
    );
    expect(worldCounts(w)).toEqual({
      projects: 2, missions: 4, running: 2, attention: 1, done: 1,
    });
  });
});
