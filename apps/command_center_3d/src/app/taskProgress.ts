/**
 * taskProgress — the user-facing five-stage progress model.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * The backend owns a ten-state mission machine (`DRAFT`, `READY`, `STARTING`,
 * `RUNNING`, `NEEDS_ATTENTION`, `PAUSED`, `VERIFYING`, `COMPLETED`, `FAILED`,
 * `CANCELLED`). A person who has never seen JEV should not have to learn any of
 * those words to follow along. So the frontend keeps the machine intact and adds
 * ONE mapping layer on top of it: five plain-language stages.
 *
 * The mapping is deliberately one-directional and total: every backend status
 * maps to exactly one stage, and an unrecognised status maps to `unknown` rather
 * than to a friendly default. A status the product has never heard of is a fact
 * it cannot explain, and rendering it as "执行任务" would be a guess.
 *
 * Pure module: no React, no fetch. Unit-tested directly.
 */
import type { MissionStatus } from "./status";

/** The five stages a person actually recognises, in order. */
export const STAGES = [
  "understand",
  "plan",
  "execute",
  "verify",
  "done",
] as const;

export type Stage = (typeof STAGES)[number];

/** Plain-language labels. Kept here so no two screens word a stage differently. */
export const STAGE_LABEL: Readonly<Record<Stage, string>> = {
  understand: "理解任务",
  plan: "制定计划",
  execute: "执行任务",
  verify: "验证结果",
  done: "完成交付",
};

/** One-line explanation shown under the progress rail on the task page. */
export const STAGE_HINT: Readonly<Record<Stage, string>> = {
  understand: "读取项目，确认要做的事",
  plan: "拆解步骤，安排执行顺序",
  execute: "指挥执行器按计划推进",
  verify: "检查结果是否正确",
  done: "任务已交付",
};

/**
 * WHO is responsible for a stage.
 *
 * This is the product-role correction, expressed as data. Four of the five
 * stages are JEV's own work; only the middle one is delegated. So a rail that
 * said "WorkBuddy · 理解任务" would credit an executor with orchestration it
 * never did, and — more importantly for a first-time user — would make the
 * executor look like the product.
 *
 * `execute` is the only stage whose owner is the executor; the renderer reads
 * the executor's real name from the mission, so the row names WorkBuddy / Codex
 * / Claude / Hermes truthfully rather than hardcoding one.
 */
export const STAGE_OWNER: Readonly<Record<Stage, "jev" | "executor">> = {
  understand: "jev",
  plan: "jev",
  execute: "executor",
  verify: "jev",
  done: "jev",
};

/** How a stage is drawn. `error` is distinct from `pending` on purpose. */
export type StageState = "done" | "active" | "pending" | "error";

/**
 * Backend status -> stage.
 *
 * Exported as a frozen map so the unit test asserts THIS table rather than a
 * copy of it, and so a new backend status cannot be added without a decision
 * here.
 */
const STAGE_OF: Readonly<Record<MissionStatus, Stage>> = {
  DRAFT: "understand",
  READY: "plan",
  STARTING: "execute",
  RUNNING: "execute",
  VERIFYING: "verify",
  COMPLETED: "done",
  // A failure and a cancellation are BOTH terminal, but they are not the same
  // event, and both must read as "this did not finish" rather than "done".
  FAILED: "execute",
  CANCELLED: "execute",
  NEEDS_ATTENTION: "execute",
  PAUSED: "execute",
};

/**
 * Map a backend mission status to one of the five stages.
 *
 * Returns `null` for a status this build does not know. `null` is meaningful:
 * the caller must render "状态未知", not guess a stage, because a wrong stage
 * would tell the user their task is executing when the system cannot say so.
 */
export function stageFor(status: string | null | undefined): Stage | null {
  if (!status) return null;
  return STAGE_OF[status as MissionStatus] ?? null;
}

/** True when the mission has reached a terminal state (either way). */
export function isTerminal(status: string | null | undefined): boolean {
  return status === "COMPLETED" || status === "FAILED" || status === "CANCELLED";
}

/** True when the mission is doing work right now. */
export function isRunning(status: string | null | undefined): boolean {
  return status === "RUNNING" || status === "STARTING" || status === "VERIFYING";
}

/**
 * Whether a mission is blocked on a human decision.
 *
 * This is the ONLY signal the task page uses to raise an approval banner, and it
 * requires BOTH the status and the server's own `attention_kind`. A mission that
 * merely carries an attention kind while running is not blocked; a mission in
 * `NEEDS_ATTENTION` with no kind is a state the server did not explain, so it is
 * reported as needing attention but with no fabricated action attached.
 */
export function needsHuman(status: string | null | undefined, attentionKind: string | null): boolean {
  return status === "NEEDS_ATTENTION" || (attentionKind != null && attentionKind !== "");
}

export interface StageView {
  stage: Stage;
  label: string;
  hint: string;
  state: StageState;
  /** `jev` or `executor` — who is doing this stage. */
  owner: "jev" | "executor";
  /**
   * The display name for this stage's owner: `JEV` or the executor's real name.
   *
   * A `null` executor name renders as 「执行器」 rather than as a guessed one: the
   * mission has not recorded an agent, so the row must not claim WorkBuddy did
   * the work.
   */
  ownerLabel: string;
  /** The backend status that put the task in this stage, for the details tab. */
  sourceStatus: string | null;
}

/**
 * Build the full five-row progress rail for a mission.
 *
 * `sourceStatus` is carried on the ACTIVE row only — it is the one row whose
 * backend state is worth an operator looking at, and it is never shown in the
 * default (Simple) surface anyway; it exists for the expert details tab.
 */
export function buildStageViews(
  status: string | null | undefined,
  executor: string | null | undefined = null,
): StageView[] {
  const current = stageFor(status);
  const currentIdx = current ? STAGES.indexOf(current) : -1;
  const failed = status === "FAILED";
  const cancelled = status === "CANCELLED";
  // With no mapping at all, no stage may claim to be active or done: an
  // unexplained state shows a rail where nothing has been achieved yet, which is
  // the honest rendering.
  const nothingKnown = currentIdx < 0;
  // The executor name is used verbatim when the mission carries one. It is never
  // substituted or defaulted, so an unnamed mission shows the generic 执行器.
  const executorLabel = executor && executor.trim() ? executor.trim() : null;

  return STAGES.map((stage, i) => {
    let state: StageState;
    if (nothingKnown) {
      state = "pending";
    } else if (i < currentIdx) {
      state = "done";
    } else if (i === currentIdx) {
      state = failed || cancelled ? "error" : "active";
    } else {
      state = "pending";
    }
    const owner = STAGE_OWNER[stage];
    return {
      stage,
      label: STAGE_LABEL[stage],
      hint: STAGE_HINT[stage],
      state,
      owner,
      ownerLabel: owner === "jev" ? "JEV" : (executorLabel ?? "执行器"),
      sourceStatus: i === currentIdx ? (status ?? null) : null,
    };
  });
}

/** The single stage a task is currently in, or null when unknown. */
export function activeStage(status: string | null | undefined): Stage | null {
  return stageFor(status);
}

/**
 * The four user-facing task states.
 *
 * The product spec calls for exactly these: 运行中 / 等待确认 / 已完成 / 失败.
 * `cancelled` and the unknown case are folded deliberately — a cancelled task
 * and a failed task are both "did not complete", and an unknown status is
 * rendered as `unknown` so it can never be counted as either.
 */
export type TaskState = "running" | "waiting" | "completed" | "failed" | "unknown";

export const TASK_STATE_LABEL: Readonly<Record<TaskState, string>> = {
  running: "运行中",
  waiting: "等待确认",
  completed: "已完成",
  failed: "失败",
  unknown: "状态未知",
};

export function taskStateFor(status: string | null | undefined): TaskState {
  if (!status) return "unknown";
  switch (status) {
    case "COMPLETED":
      return "completed";
    case "FAILED":
    case "CANCELLED":
      return "failed";
    case "NEEDS_ATTENTION":
      return "waiting";
    case "DRAFT":
    case "READY":
    case "STARTING":
    case "RUNNING":
    case "VERIFYING":
      return "running";
    case "PAUSED":
      return "waiting";
    default:
      return "unknown";
  }
}
