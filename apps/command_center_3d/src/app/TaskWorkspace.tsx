/**
 * TaskWorkspace — the second most important page in the product.
 *
 * WHAT IT ANSWERS
 * ---------------
 * While a task runs, three questions are live at once, and this page is
 * structured around them rather than around the data model:
 *
 *   现在在做什么   → the progress rail + the current activity
 *   完成了什么     → the live event timeline
 *   产生了什么     → files changed, preview, results
 *
 * THE FIVE TABS
 * -------------
 * 过程 / 文件变更 / 预览 / 结果 / 高级详情. The first four are the task. The
 * fifth is where the engineering detail (mission id, session, policy, the 3D
 * world) went when it stopped being the main event.
 *
 * NOTHING HERE IS INVENTED
 * ------------------------
 * Progress comes from the server's mission status through `taskProgress`. The
 * timeline comes from real events. Files come from real artifacts. The preview
 * URL comes from the backend or the panel says it has none. An empty state is
 * always labelled as an empty state, never padded with illustrative content.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
  FileCode2,
  FolderOpen,
  Loader2,
  MonitorPlay,
  Send,
  XCircle,
} from "lucide-react";
import { PageBody, PageHeader } from "../components/AppShell";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { ApprovalCard } from "./ApprovalCard";
import { cn } from "../ui/cn";
import {
  linesFromTimeline,
  useActivityFeed,
  type ActivityLine,
} from "./eventService";
import {
  elapsedLabel,
  formatDuration,
} from "./RecentTaskCard";
import { TaskProgress } from "./TaskProgressView";
import { EventTimeline } from "./EventTimeline";
import { FileChanges, DiffViewer } from "./FileChanges";
import { PreviewPanel } from "./PreviewPanel";
import { DeliverablesPanel } from "./DeliverablesPanel";
import { findPreviewUrl } from "./systemService";
import { TASK_STATE_LABEL, taskStateFor } from "./taskProgress";
import {
  fmtRelative,
  type Artifact,
  type AttentionAction,
  type AttentionItem,
  type AttentionResolutionsDoc,
  type DomainApi,
  type Mission,
  type Project,
  type TimelineEntry,
} from "./domainApi";

/** The five tabs, in order. `expert` marks the one that is not task content. */
const TABS = [
  { id: "process", label: "过程" },
  { id: "files", label: "文件变更" },
  { id: "preview", label: "预览" },
  { id: "result", label: "结果" },
  { id: "expert", label: "高级详情", expert: true },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function TaskWorkspace({
  api,
  apiBase,
  mission,
  project,
  attention,
  controlToken,
  onResolved,
  onBack,
  onContinue,
  onOpenExpert,
  liveMissions,
}: {
  api: DomainApi;
  apiBase: string;
  mission: Mission | null;
  project: Project | null;
  attention: AttentionItem[];
  /** In-memory control token; approvals are mutating calls. Never persisted. */
  controlToken: string;
  /** Called after a decision lands, so the shell can re-read the domain. */
  onResolved: () => void;
  onBack: () => void;
  /** Send a follow-up instruction in the same task thread. */
  onContinue: (text: string) => void;
  /** Jump to an expert surface (visualization, audit…). */
  onOpenExpert: (view: "world" | "audit" | "connections") => void;
  /** The live mission list, so the header reflects a status change instantly. */
  liveMissions: Mission[];
}) {
  const [tab, setTab] = useState<TabId>("process");
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ url: string | null; source: string } | null>(null);
  const [viewing, setViewing] = useState<{ artifact: Artifact; content: string | null; error: string | null } | null>(
    null,
  );
  const [followUp, setFollowUp] = useState("");
  const [followBusy, setFollowBusy] = useState(false);
  // --- approval state, bound to THIS mission only ---
  const [resolutions, setResolutions] = useState<AttentionResolutionsDoc | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  const mid = mission?.mission_id ?? null;

  /**
   * Prefer the mission object the shell already holds, so a status change picked
   * up by the 5s poll is reflected without waiting for this page's own read.
   */
  const live = useMemo(
    () => (mid ? (liveMissions.find((m) => m.mission_id === mid) ?? mission) : mission),
    [liveMissions, mid, mission],
  );

  const loadArtifacts = useCallback(async () => {
    if (!mid) return;
    setArtifactsLoading(true);
    const r = await api.missionArtifacts(mid);
    if (r.ok && r.data) {
      setArtifacts(r.data);
      setArtifactsError(null);
    } else {
      setArtifacts([]);
      setArtifactsError(r.error ?? "无法读取文件列表");
    }
    setArtifactsLoading(false);
  }, [api, mid]);

  // Timeline + artifacts are read once per mission; the live stream supplies
  // everything after that.
  useEffect(() => {
    if (!mid) return;
    let alive = true;
    setTimelineError(null);
    void api.missionTimeline(mid).then((r) => {
      if (!alive) return;
      if (r.ok && r.data) setTimeline(r.data);
      else setTimelineError(r.error ?? "无法读取任务历史");
    });
    void loadArtifacts();
    return () => {
      alive = false;
    };
  }, [api, mid, loadArtifacts]);

  // The preview URL is looked up when the tab is actually opened, not on mount:
  // it is one more request, and most tasks are not web tasks.

  // The server's per-item control resolution, read so the approval card can show
  // the ACTION and its risk rather than a bare "this needs attention".
  useEffect(() => {
    if (!mid) return;
    let alive = true;
    void api.attentionResolutions().then((r) => {
      if (alive && r.ok && r.data) setResolutions(r.data);
    });
    return () => {
      alive = false;
    };
  }, [api, mid, controlToken]);

  /** The action the server proposes for THIS mission, if any. */
  const pendingAction: AttentionAction | null = useMemo(() => {
    if (!resolutions || !mid) return null;
    const entry = resolutions.items.find((i) => i.mission_id === mid);
    return entry?.actions?.[0] ?? null;
  }, [resolutions, mid]);

  /**
   * Apply one approval decision, scoped to this mission.
   *
   * The mission id comes from the item itself, so there is no code path that can
   * approve a task the user is not looking at.
   */
  const resolveApproval = useCallback(
    async (item: AttentionItem, allow: boolean) => {
      const action = pendingAction;
      if (!action) return;
      setApprovalBusy(true);
      setApprovalError(null);
      const r = await api.resolveAttention(controlToken, item.mission_id, {
        action: action.action,
        dry_run: false,
        state: allow ? "APPROVED" : "REJECTED",
        decided_by: "jev-operator",
      });
      setApprovalBusy(false);
      if (!r.ok) {
        setApprovalError(r.error ?? "提交审批失败");
        return;
      }
      // Re-read so the rail reflects the server's new state rather than an
      // optimistic guess.
      onResolved();
    },
    [api, controlToken, pendingAction, onResolved],
  );

  useEffect(() => {
    if (tab !== "preview" || !mid || preview !== null) return;
    let alive = true;
    void findPreviewUrl(api, mid).then((r) => {
      if (alive) setPreview(r);
    });
    return () => {
      alive = false;
    };
  }, [tab, mid, api, preview]);

  const staticLines: ActivityLine[] = useMemo(() => linesFromTimeline(timeline), [timeline]);
  const feed = useActivityFeed(apiBase, mid, staticLines);

  if (!live) {
    return (
      <PageBody>
        <ErrorState
          title="未找到该任务"
          detail="任务可能已被删除，或链接中的标识无效。"
          onRetry={onBack}
        />
      </PageBody>
    );
  }

  const state = taskStateFor(live.status);
  const elapsed = elapsedLabel(live, Date.now());
  const pendingAttention = attention.filter((a) => a.mission_id === live.mission_id);

  return (
    <PageBody>
      <button
        type="button"
        onClick={onBack}
        className="mb-4 inline-flex items-center gap-1.5 t-meta text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        返回任务列表
      </button>

      <PageHeader
        title={live.title}
        description={
          <div className="space-y-1.5">
            {/*
             * THE PRODUCT-ROLE LINE.
             *
             * Without this, a task page headed 「执行代理：WorkBuddy」 reads as
             * though WorkBuddy IS the product and JEV is a shell around it. The
             * truth is the reverse: JEV owns the task, supervises it, and
             * delegates exactly one stage to an executor. So the coordinator is
             * named first and the executor is labelled as a component.
             */}
            <p className="t-meta text-muted-foreground">JEV 正在协调此任务</p>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 t-meta">
              {project ? (
                <span className="inline-flex items-center gap-1.5">
                  <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
                  项目：
                  <span className="font-mono">{project.root_path || project.name}</span>
                </span>
              ) : null}
              <span>
                执行器：
                <span className="text-status-live">
                  {live.agent || "未记录"}
                </span>
              </span>
              {elapsed ? <span>运行时间：{elapsed}</span> : null}
            </div>
          </div>
        }
        actions={
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 t-meta",
              state === "running"
                ? "text-status-live"
                : state === "waiting"
                  ? "text-status-warn"
                  : state === "completed"
                    ? "text-status-ok"
                    : state === "failed"
                      ? "text-status-danger"
                      : "text-status-unknown",
            )}
          >
            {state === "running" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : state === "completed" ? (
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            ) : state === "failed" ? (
              <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
            )}
            {TASK_STATE_LABEL[state]}
          </span>
        }
      />

      {live.goal ? (
        <p className="-mt-4 mb-6 max-w-3xl t-body text-muted-foreground">{live.goal}</p>
      ) : null}

      {/* --- an approval this task is blocked on, bound to THIS mission --- */}
      {pendingAttention.length > 0 ? (
        <div className="mb-6">
          <ApprovalCard
            item={pendingAttention[0]}
            action={pendingAction}
            busy={approvalBusy}
            error={approvalError}
            onAllow={() => void resolveApproval(pendingAttention[0], true)}
            onDeny={() => void resolveApproval(pendingAttention[0], false)}
          />
          {pendingAttention.length > 1 ? (
            <p className="mt-2 t-meta text-muted-foreground">
              这个任务还有 {pendingAttention.length - 1} 项待确认，可在「需要确认」页查看全部。
            </p>
          ) : null}
        </div>
      ) : null}

      {/* --- the five tabs --------------------------------------------- */}
      <div role="tablist" aria-label="任务详情" className="mb-6 flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => setTab(t.id)}
            className={cn(
              "-mb-px border-b-2 px-3.5 py-2.5 text-[14px] transition-colors",
              tab === t.id
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
        className="min-h-[320px] focus-visible:outline-none"
      >
        {tab === "process" ? (
          <div className="grid gap-8 lg:grid-cols-[280px_1fr]">
            <div className="space-y-6">
              <section aria-label="任务进度">
                <h3 className="mb-3 t-section text-foreground">任务进度</h3>
                <TaskProgress status={live.status} executor={live.agent} />
              </section>
              <CurrentActivity
                mission={live}
                lines={feed.lines}
                feedState={feed.state}
              />
            </div>
            <div className="min-w-0">
              {timelineError ? (
                <ErrorState title="无法读取任务历史" detail={timelineError} />
              ) : (
                <EventTimeline lines={feed.lines} state={feed.state} />
              )}
            </div>
          </div>
        ) : null}

        {tab === "files" ? (
          <div className="space-y-4">
            <FileChanges
              artifacts={artifacts}
              loading={artifactsLoading}
              error={artifactsError}
              onOpenArtifact={(artifact) => {
                setViewing({ artifact, content: null, error: null });
                void (async () => {
                  try {
                    const res = await fetch(artifact.uri);
                    if (!res.ok) {
                      setViewing({
                        artifact,
                        content: null,
                        error: `无法读取文件内容（HTTP ${res.status}）。后端可能不提供文件内容下载。`,
                      });
                      return;
                    }
                    const text = await res.text();
                    setViewing({ artifact, content: text, error: null });
                  } catch (e) {
                    setViewing({
                      artifact,
                      content: null,
                      error: e instanceof Error ? e.message : String(e),
                    });
                  }
                })();
              }}
            />
            {viewing ? (
              <DiffViewer
                artifact={viewing.artifact}
                content={viewing.content}
                loading={viewing.content === null && viewing.error === null}
                error={viewing.error}
                onClose={() => setViewing(null)}
              />
            ) : null}
          </div>
        ) : null}

        {tab === "preview" ? (
          preview === null ? (
            <LoadingState label="正在查找预览地址…" />
          ) : (
            <PreviewPanel url={preview.url} reason={preview.source} />
          )
        ) : null}

        {tab === "result" ? (
          <div className="space-y-8">
            <ResultSummary mission={live} artifacts={artifacts} onContinue={onContinue} />
            <DeliverablesPanel artifacts={artifacts} loading={artifactsLoading} error={artifactsError} />
            <FollowUp
              value={followUp}
              onChange={setFollowUp}
              busy={followBusy}
              onSubmit={() => {
                const text = followUp.trim();
                if (!text) return;
                setFollowBusy(true);
                onContinue(text);
                setFollowUp("");
                window.setTimeout(() => setFollowBusy(false), 400);
              }}
            />
          </div>
        ) : null}

        {tab === "expert" ? (
          <ExpertDetail
            mission={live}
            api={api}
            apiBase={apiBase}
            onOpenExpert={onOpenExpert}
          />
        ) : null}
      </div>

      {pendingAttention.length > 0 && tab !== "result" ? (
        <p className="mt-6 rounded-lg border border-status-warn/30 bg-status-warn/5 px-4 py-3 t-body text-status-warn">
          这个任务有 {pendingAttention.length} 项操作等待你的确认。
        </p>
      ) : null}
    </PageBody>
  );
}

/** The "what is it doing right now" block: the current step plus the newest line. */
function CurrentActivity({
  mission,
  lines,
  feedState,
}: {
  mission: Mission;
  lines: ActivityLine[];
  feedState: string;
}) {
  const newest = lines.length > 0 ? [...lines].sort((a, b) => b.at - a.at)[0] : null;
  const running = mission.status === "RUNNING" || mission.status === "STARTING";

  return (
    <section
      className="rounded-lg border border-border bg-card p-4"
      aria-label="当前操作"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="t-section text-foreground">当前操作</h3>
        {running ? (
          <span className="inline-flex items-center gap-1.5 t-meta text-status-live">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            执行中
          </span>
        ) : null}
      </div>
      {newest ? (
        <p className="t-body text-foreground">{newest.text}</p>
      ) : running ? (
        <p className="t-body text-muted-foreground">正在启动执行代理…</p>
      ) : (
        <p className="t-body text-muted-foreground">这个任务当前没有进行中的操作。</p>
      )}
      <p className="mt-2 t-meta text-muted-foreground">
        {mission.agent || "未指定代理"}
        {feedState === "reconnecting" ? " · 事件流重连中" : ""}
      </p>
    </section>
  );
}

/** The completion block: three facts and three actions. */
function ResultSummary({
  mission,
  artifacts,
  onContinue,
}: {
  mission: Mission;
  artifacts: Artifact[];
  onContinue: (text: string) => void;
}) {
  const done = mission.status === "COMPLETED";
  const failed = mission.status === "FAILED" || mission.status === "CANCELLED";
  const elapsed = mission.started_at && mission.finished_at
    ? formatDuration(mission.finished_at - mission.started_at)
    : null;

  if (!done && !failed) {
    return (
      <EmptyState
        title="任务尚未结束"
        hint={`当前状态：${TASK_STATE_LABEL[taskStateFor(mission.status)]}。完成后这里会显示任务摘要与交付结果。`}
      />
    );
  }

  return (
    <section
      className={cn(
        "rounded-lg border p-6",
        done ? "border-status-ok/30 bg-status-ok/5" : "border-destructive/30 bg-destructive/5",
      )}
    >
      <h3 className={cn("t-page", done ? "text-status-ok" : "text-destructive")}>
        {done ? "任务完成 ✓" : mission.status === "CANCELLED" ? "任务已取消" : "任务失败"}
      </h3>
      <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="产出文件" value={String(artifacts.length)} />
        <Stat label="运行时长" value={elapsed ?? "未记录"} />
        <Stat label="执行代理" value={mission.agent || "未指定"} />
        <Stat label="最后更新" value={fmtRelative(mission.updated_at)} />
      </dl>
      <div className="mt-6 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onContinue("")}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-[14px] font-medium text-primary-foreground transition-colors hover:brightness-110"
        >
          <Send className="h-4 w-4" aria-hidden="true" />
          继续修改
        </button>
        <button
          type="button"
          onClick={() => window.open("about:blank", "_blank")}
          className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-[14px] text-foreground transition-colors hover:bg-accent"
        >
          <ExternalLink className="h-4 w-4" aria-hidden="true" />
          打开项目
        </button>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="t-meta text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-[15px] text-foreground">{value}</dd>
    </div>
  );
}

/** The follow-up composer: same task thread, no new task created. */
function FollowUp({
  value,
  onChange,
  busy,
  onSubmit,
}: {
  value: string;
  onChange: (v: string) => void;
  busy: boolean;
  onSubmit: () => void;
}) {
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <label htmlFor="followup" className="mb-2 block t-section text-foreground">
        还想让我做什么？
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <input
          id="followup"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSubmit();
            }
          }}
          placeholder="继续修改这个任务的输出…"
          className="min-w-[240px] flex-1 rounded-md border border-border bg-background px-3 py-2 text-[14px] text-foreground outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring"
        />
        <button
          type="button"
          onClick={onSubmit}
          disabled={!value.trim() || busy}
          className={cn(
            "inline-flex items-center gap-2 rounded-md px-4 py-2 text-[14px] font-medium transition-colors",
            !value.trim() || busy
              ? "cursor-not-allowed bg-muted text-muted-foreground"
              : "bg-primary text-primary-foreground hover:brightness-110",
          )}
        >
          <Send className="h-3.5 w-3.5" aria-hidden="true" />
          发送
        </button>
      </div>
    </section>
  );
}

/** The engineering detail, moved here rather than deleted. */
function ExpertDetail({
  mission,
  api,
  apiBase,
  onOpenExpert,
}: {
  mission: Mission;
  api: DomainApi;
  apiBase: string;
  onOpenExpert: (view: "world" | "audit" | "connections") => void;
}) {
  const [runs, setRuns] = useState<number | null>(null);
  const [sessions, setSessions] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    void api.missionRuns(mission.mission_id).then((r) => {
      if (alive) setRuns(r.ok && r.data ? r.data.length : null);
    });
    void api.missionWorkload(mission.mission_id).then((r) => {
      if (alive) {
        setSessions(r.ok && r.data ? r.data.session_ids.length : null);
      }
    });
    return () => {
      alive = false;
    };
  }, [api, mission.mission_id]);

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-3 t-section text-foreground">任务标识与状态</h3>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 rounded-lg border border-border bg-card p-4 sm:grid-cols-2">
          <Row label="任务 ID" value={mission.mission_id} mono />
          <Row label="任务状态" value={mission.status} mono />
          <Row label="项目 ID" value={mission.project_id} mono />
          <Row label="会话 ID" value={mission.session_id ?? "未关联"} mono />
          <Row label="执行记录" value={runs === null ? "未读取" : String(runs)} />
          <Row label="会话数量" value={sessions === null ? "未读取" : String(sessions)} />
          <Row label="工作目录" value={mission.working_directory || "未指定"} mono />
          <Row label="API 地址" value={apiBase} mono />
        </dl>
      </section>

      <section>
        <h3 className="mb-3 t-section text-foreground">高级视图</h3>
        <div className="flex flex-wrap gap-2">
          <ExpertLink onClick={() => onOpenExpert("world")} icon={MonitorPlay} label="执行可视化（3D）" />
          <ExpertLink onClick={() => onOpenExpert("audit")} icon={FileCode2} label="审计记录" />
          <ExpertLink onClick={() => onOpenExpert("connections")} icon={FolderOpen} label="连接与代理" />
        </div>
      </section>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/50 pb-1.5 last:border-0">
      <dt className="shrink-0 t-meta text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 truncate text-right t-meta text-foreground", mono && "font-mono")}>
        {value}
      </dd>
    </div>
  );
}

function ExpertLink({
  onClick,
  icon: Icon,
  label,
}: {
  onClick: () => void;
  icon: typeof MonitorPlay;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-2 rounded-lg border border-border px-3.5 py-2 text-[14px] text-foreground transition-colors hover:bg-accent"
    >
      <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
      {label}
    </button>
  );
}
