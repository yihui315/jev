/**
 * TaskComposer — the one thing a new user is asked to do.
 *
 * THE PRODUCT RULE
 * ----------------
 * Three inputs and one button: describe the task, pick a project, press 开始执行.
 * That is the whole interaction. The four backend steps behind the button
 * (create → ready → start → launch) are performed by `startTask()` and reported
 * as a running sequence, never as four things to understand.
 *
 * The quick templates fill the input but NEVER auto-run: an agent that starts
 * writing to someone's disk the moment they glance at a suggestion is a hazard,
 * not a convenience.
 *
 * HONESTY
 * -------
 * Every failure path names what actually failed — a missing credential, a
 * refused path, an unreachable server, or an agent that would not authenticate.
 * The button is disabled only when the task genuinely cannot be submitted (no
 * goal, or no project), and it explains which one is missing.
 */
import { useEffect, useRef, useState } from "react";
import { authorizedFetch } from "../lib/authorizedFetch";
import {
  ArrowRight,
  ChevronDown,
  Cpu,
  FolderOpen,
  Loader2,
  Paperclip,
  Sparkles,
} from "lucide-react";
import type { Project } from "./domainApi";
import { verifyProjectRoot } from "./projectRoot";
import { ExpiredProjectRow, UsableProjectRow } from "./ProjectGate";
import { cn } from "../ui/cn";

/** The four suggestions offered under the input. Fill only — never auto-run. */
export const QUICK_TASKS: readonly string[] = [
  "检查项目并修复所有问题",
  "运行测试并生成报告",
  "优化当前前端",
  "部署到生产环境",
];

/**
 * The four executors, described as what they DO rather than as product names.
 *
 * They are components of JEV, not alternatives to it, so the copy never puts one
 * of them where the product's own name belongs. The user picks an EXECUTION
 * METHOD; JEV remains the orchestrator either way.
 */
export const AGENT_OPTIONS: readonly { id: string; label: string; hint: string }[] = [
  { id: "workbuddy", label: "WorkBuddy", hint: "通用执行，适合多数任务" },
  { id: "codex", label: "Codex", hint: "代码重构与实现" },
  { id: "claude", label: "Claude", hint: "分析与文档写作" },
  { id: "hermes", label: "Hermes", hint: "备用执行通道" },
];

/**
 * The default execution method.
 *
 * `auto` is not an executor — it is JEV choosing one. It is the default because a
 * first-time user does not know which executor suits their task, and offering
 * four names up front implies they must.
 */
export const AUTO_EXECUTOR = "auto";

/** The label for the auto mode, shown in the collapsed control. */
export const AUTO_LABEL = "JEV 自动选择";

/**
 * How the auto mode must actually be resolved.
 *
 * NAMING: `JEV` is this product (the orchestrator). `Jev` is TypeSafe's System
 * One model. Only the VENDOR model may choose an executor in `auto` mode.
 *
 * There is deliberately NO local mapping from `auto` to a concrete executor. An
 * earlier version of this file returned `{ id: "workbuddy" }` for `auto`, which
 * meant the label 「JEV 自动选择」 was a UI fiction over a hardcoded default: the
 * user was told the system had chosen when nothing had chosen anything.
 *
 * Resolution is now a SERVER round trip — see `askJevForExecutor`. When the
 * vendor model cannot decide, this returns null and the caller must surface that
 * fact rather than substituting an executor of its own.
 */
export interface ExecutorRequest {
  task: string;
  project_root: string;
  task_type: string;
  available_executors: string[];
  executor_health: Record<string, string>;
  risk_context?: Record<string, unknown>;
}

/** The server's answer. `source` is what keeps the UI honest. */
export interface ExecutorDecision {
  executor: string;
  /** `"jev"` only when the vendor model actually decided. */
  source: "jev" | "fallback" | "undecided";
  confidence: number;
  safe_to_execute: boolean | null;
  risk_score: number | null;
  needs_confirmation: boolean;
  reason: string | null;
  request_id: string | null;
  /**
   * The stable id a human approval binds to.
   *
   * NOT `request_id`: the vendor's `x-request-id` header is often absent, and a
   * binding keyed on a missing value would silently collapse every decision onto
   * the same key — so an approval for one task would appear to authorise
   * another. This one is derived from the decision's own content.
   */
  jev_decision_id: string;
  latency_ms: number;
  provider_state: string;
}

export interface ApiResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
  error: string | null;
}

/**
 * Ask the SERVER which executor to run.
 *
 * The browser never decides. This is a thin transport to
 * `POST /api/v1/domain/jev/select-executor`, which is the only place
 * `decide_executor()` is called. Every field is echoed back so the UI can show
 * what actually happened.
 *
 * Returns null when the request itself failed — the caller then says "JEV could
 * not choose", which is different from "JEV chose workbuddy".
 */
export async function askJevForExecutor(
  apiBase: string,
  controlToken: string,
  req: ExecutorRequest,
): Promise<ExecutorDecision | null> {
  if (!controlToken) {
    // A missing credential is a REFUSAL, and it must be reported as one.
    return {
      executor: "",
      source: "undecided",
      confidence: 0,
      safe_to_execute: null,
      risk_score: null,
      needs_confirmation: true,
      reason: "未提供控制令牌，Jev 未能参与执行器选择。",
      request_id: null,
      jev_decision_id: "",
      latency_ms: 0,
      provider_state: "REAL_AUTH_MISSING",
    };
  }
  try {
    // The shared transport, so this route carries the credential the same way
    // every other write does. A hand-built header here is how two callers end up
    // disagreeing about what "authenticated" means.
    const res = await authorizedFetch<Record<string, unknown>>(
      controlToken,
      "/api/v1/domain/jev/select-executor",
      { method: "POST", body: req, apiBase },
    );
    const body = res.ok ? (res.data as Record<string, unknown> | null) : null;
    if (!res.ok || !body) {
      return {
        executor: "",
        source: "undecided",
        confidence: 0,
        safe_to_execute: null,
        risk_score: null,
        needs_confirmation: true,
        reason: res.authFailed
          ? "控制令牌未被服务端接受（401/403），执行器未选择。"
          : `选择执行器失败（HTTP ${res.status}）`,
        request_id: null,
        jev_decision_id: "",
        latency_ms: 0,
        provider_state: "REAL_UNAVAILABLE",
      };
    }
    return body as unknown as ExecutorDecision;
  } catch {
    return {
      executor: "",
      source: "undecided",
      confidence: 0,
      safe_to_execute: null,
      risk_score: null,
      needs_confirmation: true,
      reason: "无法连接后端，Jev 未能参与执行器选择。",
      request_id: null,
      jev_decision_id: "",
      latency_ms: 0,
      provider_state: "REAL_UNAVAILABLE",
    };
  }
}

export interface ComposerSubmitState {
  /** True while the four backend steps are running. */
  busy: boolean;
  /** The step currently executing, for the running sequence display. */
  activeStep: string | null;
  error: string | null;
  /** True when the failure is "no control token", which needs a different fix. */
  missingCredential: boolean;
}

const IDLE: ComposerSubmitState = {
  busy: false,
  activeStep: null,
  error: null,
  missingCredential: false,
};

export function TaskComposer({
  projects,
  /** Projects the backend boundary allows, in scope order. */
  usableProjects,
  /** Projects whose recorded root is outside the boundary. Never offered. */
  blockedProjects,
  allowedRoots,
  selectedProject,
  onSelectProject,
  executor,
  onSelectExecutor,
  onSubmit,
  state = IDLE,
  /** Renders the credential prompt under the button. */
  onOpenSettings,
  className,
}: {
  projects: Project[];
  usableProjects: Project[];
  blockedProjects: Array<{ project: Project; reason: string }>;
  allowedRoots: string[];
  selectedProject: Project | null;
  onSelectProject: (p: Project) => void;
  /** `auto` (JEV decides) or a concrete executor id. */
  executor: string;
  onSelectExecutor: (a: string) => void;
  onSubmit: (goal: string) => void;
  state?: ComposerSubmitState;
  onOpenSettings?: () => void;
  className?: string;
}) {
  const [goal, setGoal] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Grow with the content up to a ceiling, then scroll. A composer that only
  // shows one line hides the second half of the instruction the user typed.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [goal]);

  const trimmed = goal.trim();

  /**
   * Submitting is refused while the selected project is out of scope.
   *
   * This is the front-line half of the boundary: the backend would also refuse,
   * but it would do so AFTER the user wrote their whole task, and it would answer
   * with a JSON body. Refusing here costs one sentence and prevents the wasted
   * effort.
   */
  const selectedVerdict = verifyProjectRoot(selectedProject, allowedRoots);
  const rootBlocked = !selectedVerdict.ok;

  const canSubmit = trimmed.length > 0 && !!selectedProject && !state.busy && !rootBlocked;

  // The reasons the button is disabled, named rather than left to be guessed.
  const blockedReason = !trimmed
    ? "请先描述你想完成的任务"
    : !selectedProject
      ? "请先选择一个项目"
      : rootBlocked
        ? "当前项目目录已失效，请重新选择"
        : null;

  const submit = () => {
    if (!canSubmit) return;
    onSubmit(trimmed);
  };

  return (
    <div className={cn("w-full", className)}>
      {/* The input surface. One card, one field, one action. */}
      <div className="rounded-xl border border-border bg-card shadow-card transition-colors focus-within:border-border-strong">
        <label htmlFor="task-goal" className="sr-only">
          描述你的任务
        </label>
        <textarea
          id="task-goal"
          ref={inputRef}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => {
            // ⌘/Ctrl + Enter submits: the keyboard path to the only CTA.
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
          rows={3}
          disabled={state.busy}
          placeholder={"描述任务，例如：\n“检查这个网站并修复所有问题，然后部署。”"}
          className="w-full resize-none bg-transparent px-5 pt-5 pb-3 text-[15px] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/70 disabled:opacity-60"
        />

        <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-3">
          <ProjectPicker
            usable={usableProjects}
            blocked={blockedProjects}
            selected={selectedProject}
            open={pickerOpen}
            setOpen={setPickerOpen}
            onSelect={(p) => {
              onSelectProject(p);
              setPickerOpen(false);
            }}
            disabled={state.busy}
          />

          <ExecutorPicker
            value={executor}
            onSelect={onSelectExecutor}
            disabled={state.busy}
          />

          <button
            type="button"
            disabled
            title="附件上传尚未在当前后端开放"
            className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-md px-2.5 py-2 t-meta text-muted-foreground/50"
          >
            <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
            附件
          </button>

          <div className="ml-auto flex items-center gap-3">
            <span className="hidden t-meta text-muted-foreground sm:inline">
              {blockedReason ?? "⌘ + Enter"}
            </span>
            <button
              type="button"
              onClick={submit}
              disabled={!canSubmit}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-[14px] font-medium transition-all",
                canSubmit
                  ? "bg-primary text-primary-foreground hover:brightness-110 active:scale-[0.98]"
                  : "cursor-not-allowed bg-muted text-muted-foreground",
              )}
            >
              {state.busy ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : null}
              {state.busy ? "正在启动…" : "开始执行"}
              {!state.busy ? <ArrowRight className="h-4 w-4" aria-hidden="true" /> : null}
            </button>
          </div>
        </div>
      </div>

      {/* The running sequence: four backend steps shown as ONE continuous
          process, which is the whole point of the simplification. */}
      {state.busy ? (
        <p className="mt-3 flex items-center gap-2 t-meta text-muted-foreground" aria-live="polite">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          {state.activeStep ?? "正在提交任务…"}
        </p>
      ) : null}

      {state.error ? (
        <div
          className="mt-3 flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3"
          role="alert"
        >
          <div className="min-w-0 flex-1">
            <p className="t-body text-foreground">{state.error}</p>
            {state.missingCredential && onOpenSettings ? (
              <button
                type="button"
                onClick={onOpenSettings}
                className="mt-2 rounded-md border border-border px-2.5 py-1.5 t-meta text-foreground transition-colors hover:bg-accent"
              >
                前往设置
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Quick templates. Fill the field; never run. */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 t-meta text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          快捷任务
        </span>
        {QUICK_TASKS.map((t) => (
          <button
            key={t}
            type="button"
            disabled={state.busy}
            onClick={() => {
              setGoal(t);
              inputRef.current?.focus();
            }}
            className="rounded-full border border-border bg-card px-3 py-1.5 t-meta text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground disabled:opacity-50"
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The project picker, split by the backend's boundary.
 *
 * `usable` rows are selectable. `blocked` rows are shown, greyed and struck
 * through, labelled 项目位置已失效 — deliberately VISIBLE rather than filtered
 * out, because a project that silently disappears from the list reads as "it
 * never existed", and the user needs to know their workspace boundary moved.
 * They are not clickable: there is no action to take on them from here.
 */
function ProjectPicker({
  usable,
  blocked,
  selected,
  open,
  setOpen,
  onSelect,
  disabled,
}: {
  usable: Project[];
  blocked: Array<{ project: Project; reason: string }>;
  selected: Project | null;
  open: boolean;
  setOpen: (v: boolean) => void;
  onSelect: (p: Project) => void;
  disabled: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  if (usable.length === 0 && blocked.length === 0) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-2 t-meta text-muted-foreground">
        <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
        未选择项目
      </span>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="inline-flex max-w-[260px] items-center gap-1.5 rounded-md border border-border px-2.5 py-2 t-meta text-foreground transition-colors hover:bg-accent disabled:opacity-50"
      >
        <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="truncate">{selected ? selected.name : "选择项目"}</span>
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            role="listbox"
            className="absolute bottom-full left-0 z-50 mb-2 max-h-80 w-[340px] overflow-y-auto rounded-lg border border-border bg-popover p-1 shadow-pop"
          >
            {usable.length > 0 ? (
              usable.map((p) => (
                <UsableProjectRow
                  key={p.project_id}
                  project={p}
                  selected={selected?.project_id === p.project_id}
                  onSelect={() => onSelect(p)}
                />
              ))
            ) : (
              <p className="px-3 py-2 t-meta text-muted-foreground">
                当前没有可用的项目目录。
              </p>
            )}
            {blocked.length > 0 ? (
              <>
                <div className="my-1 border-t border-border" />
                <p className="px-3 pb-1 pt-0.5 text-[11px] text-muted-foreground/70">
                  目录已失效，需重新选择
                </p>
                {blocked.map(({ project, reason }) => (
                  <ExpiredProjectRow key={project.project_id} name={project.name} reason={reason} />
                ))}
              </>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

/**
 * The execution-method control.
 *
 * COLLAPSED, it reads 「JEV 自动选择（推荐）」 — the product is presented as the
 * orchestrator that decides, and no executor name appears at all. Expanding it
 * reveals the four executors as what they are: interchangeable components JEV can
 * drive, not four products the user is choosing between.
 *
 * The auto row carries a 「推荐」 badge so the recommended path is visible rather
 * than merely first.
 */
function ExecutorPicker({
  value,
  onSelect,
  disabled,
}: {
  value: string;
  onSelect: (a: string) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const isAuto = value === AUTO_EXECUTOR;
  const current = isAuto ? null : (AGENT_OPTIONS.find((a) => a.id === value) ?? null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label="执行方式"
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-2 t-meta text-foreground transition-colors hover:bg-accent disabled:opacity-50"
      >
        <Cpu className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        {isAuto ? AUTO_LABEL : current?.label ?? value}
        <ChevronDown
          className={cn("h-3 w-3 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            role="listbox"
            aria-label="选择执行方式"
            className="absolute bottom-full left-0 z-50 mb-2 w-[280px] rounded-lg border border-border bg-popover p-1 shadow-pop"
          >
            {/* JEV's own decision, listed first and marked recommended. */}
            <button
              type="button"
              role="option"
              aria-selected={isAuto}
              onClick={() => {
                onSelect(AUTO_EXECUTOR);
                setOpen(false);
              }}
              className={cn(
                "flex w-full flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left transition-colors",
                isAuto ? "bg-primary-soft" : "hover:bg-accent",
              )}
            >
              <span className="flex w-full items-center gap-2">
                <span className="text-[14px] text-foreground">{AUTO_LABEL}</span>
                <span className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] text-primary">
                  推荐
                </span>
              </span>
              <span className="text-[11px] text-muted-foreground">
                由 JEV 根据任务类型挑选执行器
              </span>
            </button>

            <div className="my-1 border-t border-border" />
            <p className="px-3 pb-1 text-[11px] text-muted-foreground/70">高级：指定执行器</p>
            {AGENT_OPTIONS.map((a) => (
              <button
                key={a.id}
                type="button"
                role="option"
                aria-selected={a.id === current?.id}
                onClick={() => {
                  onSelect(a.id);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left transition-colors",
                  a.id === current?.id ? "bg-primary-soft" : "hover:bg-accent",
                )}
              >
                <span className="text-[14px] text-foreground">{a.label}</span>
                <span className="text-[11px] text-muted-foreground">{a.hint}</span>
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
