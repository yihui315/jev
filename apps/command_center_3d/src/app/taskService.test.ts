import { describe, it, expect, vi } from "vitest";
import { deriveTitle, startTask, STEP_LABEL, TASK_STEPS } from "./taskService";
import type { ApiResult, DomainApi, Mission, Project } from "./domainApi";

/**
 * `startTask` is the one call a normal user makes, and it hides four backend
 * steps. These tests pin the two things that must never break:
 *
 *  1. It reports a step as successful ONLY when the server said 2xx.
 *  2. It stops at the first refusal, and a failure is named for what it is —
 *     a missing credential is a different problem from a rejected path.
 */

const project: Project = {
  project_id: "prj_1",
  name: "JEV",
  root_path: "D:\\jev",
  description: "",
  created_at: 0,
  updated_at: 0,
  archived: false,
};

function mission(over: Partial<Mission> = {}): Mission {
  return {
    mission_id: "msn_1",
    project_id: "prj_1",
    title: "t",
    goal: "g",
    agent: "workbuddy",
    connection_ref: null,
    working_directory: "D:\\jev",
    context: "",
    permissions: [],
    budget_units: 0,
    status: "DRAFT",
    attention_kind: null,
    attention_reason: null,
    created_at: 0,
    updated_at: 0,
    started_at: null,
    finished_at: null,
    session_id: null,
    ...over,
  };
}

function ok<T>(data: T): ApiResult<T> {
  return { ok: true, status: 200, data, error: null };
}
function bad<T>(status: number, error: string): ApiResult<T> {
  return { ok: false, status, data: null, error };
}

/** A stub API whose four calls are individually programmable. */
function stubApi(parts: {
  create?: ApiResult<Mission>;
  ready?: ApiResult<Mission>;
  start?: ApiResult<Mission>;
  launch?: ApiResult<{ ok: boolean; error?: string }>;
}) {
  const calls: string[] = [];
  const api = {
    createMission: vi.fn(async () => {
      calls.push("create");
      return parts.create ?? ok(mission());
    }),
    transitionMission: vi.fn(async (_t: string, _m: string, body: { to: string }) => {
      calls.push(`transition:${body.to}`);
      if (body.to === "READY") return parts.ready ?? ok(mission({ status: "READY" }));
      return parts.start ?? ok(mission({ status: "STARTING" }));
    }),
    launchMission: vi.fn(async () => {
      calls.push("launch");
      return (
        parts.launch ??
        ok({ ok: true, mission_id: "msn_1", agent: "workbuddy", steps: [], run_id: null, session_id: null, ingested: true } as never)
      );
    }),
  } as unknown as DomainApi;
  return { api, calls };
}

describe("deriveTitle", () => {
  it("takes the first clause and caps the length", () => {
    expect(deriveTitle("检查这个网站并修复所有问题，然后部署。")).toBe("检查这个网站并修复所有问题");
    expect(deriveTitle("  修复   布局  ")).toBe("修复 布局");
    const long = deriveTitle("a".repeat(80));
    expect(long.length).toBe(42);
    expect(long.endsWith("…")).toBe(true);
  });

  it("never returns an empty title", () => {
    expect(deriveTitle("")).toBe("未命名任务");
    expect(deriveTitle("   ")).toBe("未命名任务");
  });
});

describe("startTask", () => {
  it("runs all four backend steps in order and reports success", async () => {
    const { api, calls } = stubApi({});
    const r = await startTask(api, "tok", { project, goal: "检查项目" });
    expect(r.ok).toBe(true);
    expect(r.started).toBe(true);
    expect(r.failure).toBeNull();
    expect(calls).toEqual(["create", "transition:READY", "transition:STARTING", "launch"]);
    expect(r.steps).toHaveLength(TASK_STEPS.length);
    expect(r.steps.every((s) => s.ok)).toBe(true);
  });

  it("refuses without a token and never touches the network", async () => {
    const { api, calls } = stubApi({});
    const r = await startTask(api, "", { project, goal: "检查项目" });
    expect(r.ok).toBe(false);
    expect(r.started).toBe(false);
    // A missing credential is its own diagnosis, and needs its own fix.
    expect(r.missingCredential).toBe(true);
    expect(r.failure).toContain("控制令牌");
    expect(calls).toEqual([]);
  });

  it("refuses an empty goal before submitting anything", async () => {
    const { api, calls } = stubApi({});
    const r = await startTask(api, "tok", { project, goal: "   " });
    expect(r.ok).toBe(false);
    expect(r.failure).toContain("描述");
    expect(calls).toEqual([]);
  });

  it("stops at the first refusal and does not attempt later steps", async () => {
    const { api, calls } = stubApi({ create: bad(422, "root out of scope") });
    const r = await startTask(api, "tok", { project, goal: "检查项目" });
    expect(r.ok).toBe(false);
    // Exactly one step ran, so exactly one step is reported. A step never
    // attempted did not fail and must not be listed as if it had.
    expect(calls).toEqual(["create"]);
    expect(r.steps).toHaveLength(1);
    expect(r.steps[0].step).toBe("create");
    expect(r.mission).toBeNull();
  });

  it("keeps a created mission visible when a later step fails", async () => {
    // The mission EXISTS at this point. Reporting null would make the UI claim
    // nothing was created, which is false.
    const { api } = stubApi({ start: bad(403, "forbidden") });
    const r = await startTask(api, "tok", { project, goal: "检查项目" });
    expect(r.ok).toBe(false);
    expect(r.mission).not.toBeNull();
    expect(r.mission?.mission_id).toBe("msn_1");
    expect(r.steps.filter((s) => s.ok)).toHaveLength(2);
  });

  it("reports a 401 from the server as a missing credential", async () => {
    const { api } = stubApi({
      create: bad(401, "Control token not configured (fail-closed)"),
    });
    const r = await startTask(api, "stale", { project, goal: "检查项目" });
    expect(r.missingCredential).toBe(true);
    expect(r.failure).toContain("JCC_CONTROL_TOKEN");
  });

  it("does not claim a task started when the launch was refused", async () => {
    // A created mission whose agent never came up is NOT a running task.
    const { api } = stubApi({
      launch: ok({ ok: false, error: "agent requires authentication" } as never),
    });
    const r = await startTask(api, "tok", { project, goal: "检查项目" });
    expect(r.ok).toBe(false);
    expect(r.started).toBe(false);
    expect(r.mission).not.toBeNull();
    expect(r.failure).toContain("登录");
  });

  it("gives every step a plain-language label", () => {
    for (const s of TASK_STEPS) {
      expect(STEP_LABEL[s].length).toBeGreaterThan(0);
    }
  });
});
