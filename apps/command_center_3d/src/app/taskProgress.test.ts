import { describe, it, expect } from "vitest";
import {
  STAGES,
  STAGE_LABEL,
  activeStage,
  buildStageViews,
  isRunning,
  isTerminal,
  needsHuman,
  stageFor,
  TASK_STATE_LABEL,
  taskStateFor,
} from "./taskProgress";

/**
 * The five-stage progress model is the layer a person reads instead of the
 * backend's ten-state machine. These tests pin the two properties that matter:
 *
 *  1. Every backend status has EXACTLY one stage. A status with no stage must
 *     return null (→ 状态未知), never a guess.
 *  2. A stage is never shown as achieved when the system could not say. This is
 *     the same honesty rule the rest of the product follows, expressed on a rail
 *     a user reads at a glance.
 */
describe("stage mapping", () => {
  it("exposes exactly the five user-facing stages, in order", () => {
    expect([...STAGES]).toEqual(["understand", "plan", "execute", "verify", "done"]);
    // Every stage has a plain-language label; none may be empty.
    for (const s of STAGES) {
      expect(STAGE_LABEL[s].length).toBeGreaterThan(0);
    }
  });

  it("maps the whole backend lifecycle onto a stage", () => {
    expect(stageFor("DRAFT")).toBe("understand");
    expect(stageFor("READY")).toBe("plan");
    expect(stageFor("STARTING")).toBe("execute");
    expect(stageFor("RUNNING")).toBe("execute");
    expect(stageFor("VERIFYING")).toBe("verify");
    expect(stageFor("COMPLETED")).toBe("done");
    // Blocked, paused and failed all stay inside 执行任务 — the work is what
    // stopped, not a later stage.
    expect(stageFor("NEEDS_ATTENTION")).toBe("execute");
    expect(stageFor("PAUSED")).toBe("execute");
    expect(stageFor("FAILED")).toBe("execute");
    expect(stageFor("CANCELLED")).toBe("execute");
  });

  it("returns null for a status it cannot explain, rather than guessing", () => {
    // The load-bearing case: an unknown status must NOT render as "executing",
    // because that would tell a user their task is running when nobody said so.
    expect(stageFor("SOMETHING_NEW")).toBeNull();
    expect(stageFor("")).toBeNull();
    expect(stageFor(null)).toBeNull();
    expect(stageFor(undefined)).toBeNull();
    expect(activeStage("SOMETHING_NEW")).toBeNull();
  });

  it("classifies terminal and running states", () => {
    expect(isTerminal("COMPLETED")).toBe(true);
    expect(isTerminal("FAILED")).toBe(true);
    expect(isTerminal("CANCELLED")).toBe(true);
    expect(isTerminal("RUNNING")).toBe(false);

    expect(isRunning("RUNNING")).toBe(true);
    expect(isRunning("STARTING")).toBe(true);
    expect(isRunning("VERIFYING")).toBe(true);
    expect(isRunning("COMPLETED")).toBe(false);
  });

  it("raises an approval only for a real attention signal", () => {
    expect(needsHuman("NEEDS_ATTENTION", null)).toBe(true);
    expect(needsHuman("RUNNING", "APPROVAL_REQUIRED")).toBe(true);
    // A mission carrying no attention kind is not blocked.
    expect(needsHuman("RUNNING", null)).toBe(false);
    expect(needsHuman("RUNNING", "")).toBe(false);
  });
});

describe("stage rail", () => {
  it("marks earlier stages done and later stages pending", () => {
    const views = buildStageViews("RUNNING");
    expect(views.map((v) => v.state)).toEqual([
      "done",
      "done",
      "active",
      "pending",
      "pending",
    ]);
    // The source status is carried on the active row only — it is the one an
    // operator might want, and Simple Mode never shows it anyway.
    expect(views.filter((v) => v.sourceStatus !== null)).toHaveLength(1);
    expect(views[2].sourceStatus).toBe("RUNNING");
  });

  it("marks the final stage active on completion", () => {
    const views = buildStageViews("COMPLETED");
    expect(views.map((v) => v.state)).toEqual([
      "done",
      "done",
      "done",
      "done",
      "active",
    ]);
  });

  it("marks the active stage as an ERROR on failure, never as done", () => {
    // The load-bearing assertion: a failed task must not show 完成 anywhere.
    const views = buildStageViews("FAILED");
    expect(views[2].state).toBe("error");
    expect(views.filter((v) => v.state === "done")).toHaveLength(2);
    expect(views.some((v) => v.state === "active")).toBe(false);

    const cancelled = buildStageViews("CANCELLED");
    expect(cancelled[2].state).toBe("error");
  });

  it("shows nothing achieved when the status is unknown", () => {
    // The whole rail is pending: we cannot say any stage was reached, so we
    // must not draw three ticks.
    for (const status of [null, undefined, "", "MYSTERY"]) {
      const views = buildStageViews(status);
      expect(views.every((v) => v.state === "pending")).toBe(true);
      expect(views.some((v) => v.state === "done")).toBe(false);
    }
  });

  it("always returns exactly five rows", () => {
    for (const status of ["DRAFT", "RUNNING", "COMPLETED", "FAILED", "NONSENSE"]) {
      expect(buildStageViews(status)).toHaveLength(5);
    }
  });
});

describe("stage ownership — JEV is the orchestrator", () => {
  it("gives four of five stages to JEV and only 执行 to the executor", () => {
    // The product-role correction as an assertion. If a stage's owner ever flips,
    // this fails — which is the point: an executor must never be credited with
    // orchestration, and JEV must never be hidden from the rail.
    const views = buildStageViews("RUNNING", "WorkBuddy");
    expect(views.map((v) => v.owner)).toEqual([
      "jev",
      "jev",
      "executor",
      "jev",
      "jev",
    ]);
  });

  it("names the real executor on the delegated stage only", () => {
    const views = buildStageViews("RUNNING", "Codex");
    expect(views[2].ownerLabel).toBe("Codex");
    for (const i of [0, 1, 3, 4]) {
      expect(views[i].ownerLabel).toBe("JEV");
    }
  });

  it("says 执行器, not a guessed name, when the mission records no agent", () => {
    // Honesty: a mission with no agent has nobody doing the work, so the row must
    // not claim WorkBuddy did.
    for (const agent of [null, undefined, "", "   "]) {
      const views = buildStageViews("RUNNING", agent);
      expect(views[2].ownerLabel).toBe("执行器");
      expect(views[0].ownerLabel).toBe("JEV");
    }
  });

  it("uses the executor name verbatim, without normalising it", () => {
    expect(buildStageViews("RUNNING", "workbuddy")[2].ownerLabel).toBe("workbuddy");
  });

  it("still credits JEV on a failed run, because JEV did the planning", () => {
    const views = buildStageViews("FAILED", "WorkBuddy");
    expect(views[0].ownerLabel).toBe("JEV");
    expect(views[0].state).toBe("done");
    expect(views[2].ownerLabel).toBe("WorkBuddy");
    expect(views[2].state).toBe("error");
  });
});

describe("task state", () => {
  it("uses the four product words, with unknown kept distinct", () => {
    expect(taskStateFor("RUNNING")).toBe("running");
    expect(taskStateFor("STARTING")).toBe("running");
    expect(taskStateFor("NEEDS_ATTENTION")).toBe("waiting");
    expect(taskStateFor("PAUSED")).toBe("waiting");
    expect(taskStateFor("COMPLETED")).toBe("completed");
    expect(taskStateFor("FAILED")).toBe("failed");
    expect(taskStateFor("CANCELLED")).toBe("failed");
    // A status nobody can explain is neither running nor done.
    expect(taskStateFor("MYSTERY")).toBe("unknown");
    expect(taskStateFor(null)).toBe("unknown");
  });

  it("labels every state, and never shows a raw status to a user", () => {
    for (const s of ["running", "waiting", "completed", "failed", "unknown"] as const) {
      expect(TASK_STATE_LABEL[s].length).toBeGreaterThan(0);
    }
    expect(TASK_STATE_LABEL.unknown).toBe("状态未知");
  });
});
