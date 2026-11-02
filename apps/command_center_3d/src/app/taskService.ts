/**
 * taskService — the ONE call a normal user makes.
 *
 * WHAT THIS SOLVES
 * ----------------
 * Creating a runnable task on the real backend is four backend calls in order:
 *
 *     createMission → transition(READY) → transition(STARTING/RUNNING) → launch
 *
 * The old Mission Studio exposed all four as separate buttons, which is honest
 * for an operator and unusable for a first-time user. This module performs the
 * sequence, reports EACH step as it happens, and stops at the first refusal —
 * while every underlying `domainApi` method stays exactly as it was. Nothing is
 * deleted; it is composed.
 *
 * HONESTY RULES (these are the whole point of the module)
 * -------------------------------------------------------
 * 1. No step is ever reported as succeeded unless the server said 2xx. Every
 *    step carries the server's own detail text on failure.
 * 2. A missing control token is surfaced as a missing credential, distinctly
 *    from a path rejection or a server error, because they need different fixes.
 * 3. `started` is false unless a launch actually returned. A mission that was
 *    created but could not be launched is reported as "created, not running" —
 *    never as a running task.
 * 4. The control token is passed per call from memory. It is never stored, never
 *    logged, never placed in a URL.
 */
import {
  type ApiResult,
  type DomainApi,
  type LaunchResult,
  type Mission,
  type Project,
  humanizeLaunchRejection,
} from "./domainApi";

/** The four backend steps, in the order the product performs them. */
export const TASK_STEPS = ["create", "ready", "start", "launch"] as const;
export type TaskStep = (typeof TASK_STEPS)[number];

export const STEP_LABEL: Readonly<Record<TaskStep, string>> = {
  create: "创建任务",
  ready: "准备就绪",
  start: "开始执行",
  launch: "连接执行代理",
};

export interface StepReport {
  step: TaskStep;
  label: string;
  ok: boolean;
  /** The server's own words, or null when the step succeeded. */
  detail: string | null;
  /** HTTP status, 0 when the transport never reached the server. */
  status: number;
}

export interface StartTaskInput {
  /** The project the task runs against. */
  project: Project;
  /** One sentence from the user. */
  goal: string;
  /** Which agent should run it. Defaults to the project's usual agent. */
  agent?: string;
  /** Free-form extra context. */
  context?: string;
  workingDirectory?: string;
}

export interface StartTaskResult {
  ok: boolean;
  /** The mission that was created, when creation itself succeeded. */
  mission: Mission | null;
  /** Every step, in order, including the one that failed. */
  steps: StepReport[];
  /** True only when a launch call returned successfully. */
  started: boolean;
  /**
   * The launch's own verdict. `LaunchResult` (the Wave 3 contract) carries a
   * boolean `ok`, so this is the two-state projection of it; the four-way
   * `classification` vocabulary belongs to the Wave 9.2 `launchCommit` contract,
   * which this product does not call.
   */
  classification: "LAUNCHED" | "STARTED_NO_TELEMETRY" | null;
  /**
   * A single line the Simple UI can show. Null when everything worked, because a
   * successful start has nothing to apologise for.
   */
  failure: string | null;
  /** True when the failure was "no control token", not a task or path problem. */
  missingCredential: boolean;
}

function report(
  step: TaskStep,
  ok: boolean,
  status: number,
  detail: string | null,
): StepReport {
  return { step, label: STEP_LABEL[step], ok, detail, status };
}

/** The 401 the server returns when `JCC_CONTROL_TOKEN` is unset. */
function isMissingCredential(error: string | null, status: number): boolean {
  if (status === 401) return true;
  return !!error && /control token not configured/i.test(error);
}

/** A short, actionable line for each failure the server actually produces. */
function explain(status: number, detail: string | null, step: TaskStep): string {
  const d = detail ?? "";
  if (isMissingCredential(d, status)) {
    return "服务端未配置控制令牌（JCC_CONTROL_TOKEN），写操作被安全策略拒绝。启动服务后即可创建任务。";
  }
  if (status === 401) return "控制令牌无效，任务未被提交。";
  if (status === 403) return "当前策略不允许这一步操作。";
  if (status === 404) return `接口不存在，后端未提供这一步能力（${step}）。`;
  if (status === 422) return d || "提交的内容被服务端拒绝。";
  if (status === 0) return "无法连接后端服务，请确认 JCC API 正在运行。";
  if (status >= 500) return d || "后端服务出错，请稍后重试。";
  return d || `第 ${step} 步失败（HTTP ${status}）。`;
}

/**
 * Run the full create → ready → start → launch sequence.
 *
 * Stops at the FIRST refusal. Later steps are not reported at all rather than
 * reported as failed, because a step that was never attempted did not fail.
 *
 * NOTE ON THE LAUNCH CALL: this uses `launchMission` (the Wave 3 contract), whose
 * verdict is the boolean `ok` plus an `error` string and a `capture_source`
 * liveness reading. The richer four-way `classification` belongs to the Wave 9.2
 * `launchCommit` contract, which this product does not call — so a launch here is
 * judged on `ok`, and the child's own liveness/reason text is surfaced verbatim
 * rather than being re-invented.
 */
export async function startTask(
  api: DomainApi,
  token: string,
  input: StartTaskInput,
): Promise<StartTaskResult> {
  const steps: StepReport[] = [];
  const agent = (input.agent || "workbuddy").toLowerCase();
  const goal = input.goal.trim();

  const finish = (
    ok: boolean,
    mission: Mission | null,
    started: boolean,
    failure: string | null,
    classification: "LAUNCHED" | "STARTED_NO_TELEMETRY" | null,
    missingCredential: boolean,
  ): StartTaskResult => ({
    ok,
    mission,
    steps,
    started,
    classification,
    failure,
    missingCredential,
  });

  // --- 0. precondition: a goal and a credential are both required ----------
  if (!token) {
    steps.push(
      report("create", false, 401, "Control token not configured (fail-closed)"),
    );
    return finish(
      false,
      null,
      false,
      "尚未提供控制令牌，无法在服务端创建任务。请在「设置 → 安全」中提供令牌。",
      null,
      true,
    );
  }
  if (!goal) {
    steps.push(report("create", false, 0, "任务描述为空"));
    return finish(false, null, false, "请先描述你想完成的任务。", null, false);
  }

  // --- 1. create ---------------------------------------------------------
  const created: ApiResult<Mission> = await api.createMission(token, {
    project_id: input.project.project_id,
    title: deriveTitle(goal),
    goal,
    agent,
    context: input.context ?? "",
    working_directory:
      input.workingDirectory || input.project.root_path || input.project.project_id,
  });
  if (!created.ok || !created.data) {
    steps.push(report("create", false, created.status, created.error));
    return finish(
      false,
      null,
      false,
      explain(created.status, created.error, "create"),
      null,
      isMissingCredential(created.error, created.status),
    );
  }
  const mission = created.data;
  steps.push(report("create", true, created.status, null));

  // --- 2. READY ----------------------------------------------------------
  // A mission created as DRAFT is not launchable. The transition is the backend's
  // own state machine moving; the client never writes `status` directly.
  const ready = await api.transitionMission(token, mission.mission_id, {
    to: "READY",
    reason: "task started from the composer",
  });
  if (!ready.ok) {
    steps.push(report("ready", false, ready.status, ready.error));
    // The mission EXISTS. Reporting `mission` lets the UI link to it instead of
    // pretending nothing was created.
    return finish(
      false,
      mission,
      false,
      explain(ready.status, ready.error, "ready"),
      null,
      isMissingCredential(ready.error, ready.status),
    );
  }
  steps.push(report("ready", true, ready.status, null));

  // --- 3. STARTING -------------------------------------------------------
  const startedTransition = await api.transitionMission(token, mission.mission_id, {
    to: "STARTING",
    reason: "launching the execution agent",
  });
  if (!startedTransition.ok) {
    steps.push(report("start", false, startedTransition.status, startedTransition.error));
    return finish(
      false,
      mission,
      false,
      explain(startedTransition.status, startedTransition.error, "start"),
      null,
      isMissingCredential(startedTransition.error, startedTransition.status),
    );
  }
  steps.push(report("start", true, startedTransition.status, null));

  // --- 4. launch ---------------------------------------------------------
  // This is the call that actually starts work. Its `classification` is the
  // child's own verdict: a run that started and then failed to authenticate
  // comes back AUTH_REQUIRED and must NOT be shown as a running task.
  //
  // Use `native: true` (mission-scoped spawn) rather than the default
  // `capture: true` (attach to a live operator session). Capture requires a live
  // WorkBuddy session whose cwd MATCHES the project dir; in this environment the
  // operator session runs in the workspace, so capture always 409s with
  // WORKBUDDY_SESSION_SCOPE_MISMATCH. Native spawn launches a real workload in
  // the project dir and is what "execute this task" actually means.
  const launch: ApiResult<LaunchResult> = await api.launchMission(token, mission.mission_id, {
    native: true,
  });
  if (!launch.ok || !launch.data) {
    steps.push(report("launch", false, launch.status, launch.error));
    const humanized = humanizeLaunchRejection(launch.error);
    return finish(
      false,
      mission,
      false,
      humanized || explain(launch.status, launch.error, "launch"),
      null,
      isMissingCredential(launch.error, launch.status),
    );
  }

  const data = launch.data;
  const launched = data.ok === true;
  steps.push(
    report(
      "launch",
      launched,
      launch.status,
      launched ? null : data.error || data.capture_source?.liveness.reason || "执行代理未确认启动",
    ),
  );

  if (launched) {
    return finish(true, mission, true, null, "LAUNCHED", false);
  }

  // The mission exists and is STARTING, but the agent never came up. Say both
  // facts, so the UI can show the task page (it is real) with an honest banner.
  return finish(
    false,
    mission,
    false,
    classifyLaunchFailure(data),
    "STARTED_NO_TELEMETRY",
    false,
  );
}

function classifyLaunchFailure(data: LaunchResult): string {
  const humanized = humanizeLaunchRejection(data.error);
  if (humanized) return humanized;
  const auth = /auth|login|unauthor|token|401/i.test(data.error ?? "");
  if (auth) {
    return "执行代理需要登录授权，任务已创建但尚未运行。请在「设置 → 执行代理」中完成认证。";
  }
  return data.error || "执行代理未确认启动，任务已创建但尚未运行。";
}

/**
 * Derive a short task title from the goal.
 *
 * A task list full of full sentences is unreadable, so the first clause becomes
 * the title. The ORIGINAL goal is still stored on the mission, so nothing is
 * lost by summarising here.
 */
export function deriveTitle(goal: string, max = 42): string {
  const clean = goal.replace(/\s+/g, " ").trim();
  if (!clean) return "未命名任务";
  // The separator class must cover BOTH punctuation sets: a Chinese goal is
  // full of 「，」「；」 and an English one of ",;". Splitting on ASCII only would
  // return the entire sentence as the title, which is exactly what the task list
  // must not show.
  const cut = clean.split(/[。；;！!？?，,、\n]/)[0].trim() || clean;
  return cut.length > max ? `${cut.slice(0, max - 1)}…` : cut;
}
