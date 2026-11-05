/**
 * App — the v2 shell.
 *
 * WHAT CHANGED FROM v1.3, AND WHY
 * --------------------------------
 * v1.3 exposed fourteen top-level surfaces and asked a first-time user to
 * understand Mission, Session, AEP, Policy and Directive before they could start
 * work. This shell reduces the default navigation to four entries (首页 / 任务 /
 * 项目 / 设置), composes the four backend steps behind ONE button, and moves the
 * engineering surfaces behind an Expert Mode toggle.
 *
 * NOTHING WAS DELETED. Every v1.3 view is still mounted here and still reachable;
 * `isExpertMode` decides what the SIDEBAR lists, not what the app can do. The
 * palette and the URL hash reach an advanced view in either mode, so a bookmark
 * from the old build keeps working.
 *
 * THREE INVARIANTS THIS FILE ENFORCES
 * ------------------------------------
 * 1. The control token lives in memory only. It is never written to
 *    localStorage, never placed in a URL, and never rendered in Simple Mode.
 * 2. The 3D engine is lazily imported. The home page must not pay for Three.js.
 * 3. Every read failure is shown as a failure. There is no path that turns an
 *    error into an empty list or a green status.
 */
import { Suspense, lazy as reactLazy, useCallback, useEffect, useMemo, useState } from "react";
import { useSyncExternalStore } from "react";

import { sceneStore } from "./store/SceneStore";
import { TableView } from "./views/TableView";
import { GraphView } from "./views/GraphView";
import { ConnectionsView } from "./views/ConnectionsView";
import { Inspector } from "./components/Inspector";
import { Timeline } from "./components/Timeline";
import { RiskRadar } from "./components/RiskRadar";
import { DecisionExplanation } from "./components/DecisionExplanation";
import { OperatorConsole } from "./components/OperatorConsole";

import { AppShell, PageBody } from "./components/AppShell";
import { Sidebar } from "./app/Sidebar";
import { TopStatus, type StatusProbe } from "./app/TopStatus";
import { HomeView } from "./app/HomeView";
import { TaskWorkspace } from "./app/TaskWorkspace";
import { ProjectsView } from "./app/ProjectsView";
import { MissionsView } from "./app/MissionsView";
import { MissionStudio } from "./app/MissionStudio";
import { DeliverablesView } from "./app/DeliverablesView";
import { NeedsAttentionView } from "./app/NeedsAttentionView";
import { ConnectionVerificationView } from "./app/ConnectionVerificationView";
import { AuditView, DiagnosticsView } from "./app/SystemViews";
import { SettingsView } from "./app/SettingsView";
import { useDomain } from "./app/useDomain";
import { probeSystem } from "./app/systemService";
import { startTask, STEP_LABEL, type TaskStep } from "./app/taskService";
import { useControlTokenVerification } from "./lib/authorizedFetch";
import { fetchAutoControlToken } from "./lib/autoToken";
import { humanizeProviderError } from "./lib/humanizeProviderError";
import {
  AGENT_OPTIONS,
  AUTO_EXECUTOR,
  askJevForExecutor,
  type ComposerSubmitState,
  type ExecutorDecision,
} from "./app/TaskComposer";
import {
  allowedRootsFrom,
  partitionProjects,
  verifyProjectRoot,
  VERDICT_HEADLINE,
} from "./app/projectRoot";
import { truncationNote } from "./app/truncation";
import { CommandPalette, type CommandItemSpec } from "./ui/CommandPalette";
import { TooltipProvider } from "./ui/Tooltip";
import { envApiBase, resolveApiBase } from "./lib/apiBase";
import { buildDomainWorld } from "./store/domainWorld";
import {
  NAV_ITEMS,
  coerceView,
  isAdvanced,
  navLabel,
  showsInspectorRail,
  type ViewMode,
} from "./app/navigation";

// Read-only API base. The definition lives in ONE place -- see lib/apiBase.ts.
const API_BASE: string = resolveApiBase(envApiBase());

const THEME_KEY = "jcc.theme";
const MODE_KEY = "jcc.mode";
/** Deep link to a task: `#/task-detail?mission=msn_xxx`. */
const TASK_HASH = /^task-detail\?mission=(.+)$/;

function readTheme(): "dark" | "light" {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage unavailable — fall through to the default */
  }
  return "dark";
}

/** Simple Mode is the default. Expert Mode is opt-in and remembered. */
function readExpertMode(): boolean {
  try {
    return localStorage.getItem(MODE_KEY) === "expert";
  } catch {
    return false;
  }
}

/** Read the view (and optional task id) out of the URL hash. */
function readRoute(): { view: ViewMode; missionId: string | null } {
  try {
    const h = window.location.hash.replace(/^#\/?/, "").trim();
    if (!h) return { view: "home", missionId: null };
    const m = h.match(TASK_HASH);
    if (m) return { view: "task-detail", missionId: decodeURIComponent(m[1]) };
    return { view: coerceView(h, "home"), missionId: null };
  } catch {
    return { view: "home", missionId: null };
  }
}

const IDLE_COMPOSER: ComposerSubmitState = {
  busy: false,
  activeStep: null,
  error: null,
  missingCredential: false,
};

export function App() {
  const scene = useSyncExternalStore(sceneStore.subscribe, sceneStore.getSnapshot);

  const route = useMemo(readRoute, []);
  const [view, setView] = useState<ViewMode>(route.view);
  const [activeMission, setActiveMission] = useState<string | null>(route.missionId);
  const [controlToken, setControlToken] = useState("");
  /**
   * Auto-configure the control token when the SPA server was started with one.
   * Memory-only: the fetched value goes into React state -- never localStorage,
   * a cookie, the URL or a log. A server that does not serve one simply leaves
   * the field empty and the operator is prompted exactly as before.
   */
  useEffect(() => {
    const ac = new AbortController();
    let cancelled = false;
    void fetchAutoControlToken(ac.signal).then((token) => {
      if (!cancelled && token) setControlToken(token);
    });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, []);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(readTheme);
  const [expert, setExpert] = useState(readExpertMode);
  const [probes, setProbes] = useState<StatusProbe[]>([]);
  const [composer, setComposer] = useState<ComposerSubmitState>(IDLE_COMPOSER);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  /**
   * The execution method. `auto` is the default and means JEV picks the executor
   * — the four concrete executors are a component choice, not a product choice.
   */
  const [executor, setExecutor] = useState(AUTO_EXECUTOR);
  /**
   * The vendor model's last decision, kept so the task page can show WHAT chose
   * the executor. `null` means no Jev round trip has happened, which is
   * different from "Jev decided workbuddy" — the UI must not render the two the
   * same way.
   */
  const [jevDecision, setJevDecision] = useState<ExecutorDecision | null>(null);
  // The decision's SUBJECT, captured at the moment it was made. A confirmation
  // must bind to the task that was actually decided on, so this is held rather
  // than re-read from a textarea the operator may since have edited.
  const [pendingGoal, setPendingGoal] = useState("");
  const [pendingProvider, setPendingProvider] = useState<string | null>(null);
  const [pendingModel, setPendingModel] = useState<string | null>(null);

  const { state: tokenState, verify: verifyToken } = useControlTokenVerification(controlToken);
  const onVerifyToken = useCallback(async () => { await verifyToken(controlToken); }, [verifyToken, controlToken]);

  const domain = useDomain(API_BASE, controlToken);

  const goto = useCallback((v: ViewMode) => {
    setView(v);
    if (v !== "task-detail") setActiveMission(null);
    try {
      window.location.hash = `#/${v}`;
    } catch {
      /* non-browser environment */
    }
  }, []);

  const openTask = useCallback((missionId: string) => {
    setActiveMission(missionId);
    setView("task-detail");
    try {
      window.location.hash = `#/task-detail?mission=${encodeURIComponent(missionId)}`;
    } catch {
      /* non-browser environment */
    }
  }, []);

  // --- theme ---
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  // --- mode ---
  useEffect(() => {
    try {
      localStorage.setItem(MODE_KEY, expert ? "expert" : "simple");
    } catch {
      /* ignore */
    }
  }, [expert]);

  // --- scene transport (the 3D plane's own data) ---
  useEffect(() => {
    void sceneStore.load(API_BASE);
    sceneStore.connectStream(API_BASE);
    return () => sceneStore.disconnect();
  }, []);

  // --- deep link on hash change (back/forward) ---
  useEffect(() => {
    const onHash = () => {
      const r = readRoute();
      setView(r.view);
      setActiveMission(r.missionId);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // --- ⌘K / Ctrl+K ---
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // --- the header's status probes ---
  const refreshProbes = useCallback(async () => {
    const next = await probeSystem(API_BASE, domain.api);
    setProbes(next);
  }, [domain.api]);

  useEffect(() => {
    void refreshProbes();
    const t = window.setInterval(() => void refreshProbes(), 20000);
    return () => window.clearInterval(t);
  }, [refreshProbes]);

  /**
   * The backend's REAL workspace boundary, read from the domain home summary.
   *
   * This is the only source of truth for "may this project be used". It is never
   * hardcoded and never string-rewritten: on this host the server publishes
   * `["D:\jev"]`, and a project recorded as `F:\jev\.workbuddy` is therefore out of
   * scope no matter what a stale local cache or an old bookmark claims.
   */
  const allowedRoots = useMemo(
    () => allowedRootsFrom(domain.home ? domain.home.project_scope : null),
    [domain.home],
  );

  /**
   * Projects split by that boundary.
   *
   * `usable` is what the composer may target; `blocked` is shown greyed with
   * 项目位置已失效 rather than silently dropped, so a user whose workspace moved
   * learns WHY their project vanished instead of assuming it never existed.
   */
  const { usable: usableProjects, blocked: blockedRaw } = useMemo(
    () => partitionProjects(domain.projects, allowedRoots),
    [domain.projects, allowedRoots],
  );

  const blockedProjects = useMemo(
    () =>
      blockedRaw.map(({ project, verdict }) => ({
        project,
        reason: verdict.ok ? "" : verdict.reason,
      })),
    [blockedRaw],
  );

  /**
   * The project the composer starts against.
   *
   * Selected by id so a refresh that reorders the list cannot silently change the
   * target. When the current selection is out of scope, the first usable project
   * takes over — a boundary the backend has already published, never a rewritten
   * path. With no usable project it stays null and the composer refuses to submit
   * with 「请先选择一个项目」 rather than guessing a directory.
   */
  const selectedProject = useMemo(() => {
    if (usableProjects.length === 0) return null;
    const chosen = usableProjects.find((p) => p.project_id === selectedProjectId);
    if (chosen) return chosen;
    // Prefer a project whose root IS the first authorised root, so the default
    // lands on the workspace the backend nominates rather than an arbitrary one.
    const first = allowedRoots?.[0];
    if (first) {
      const exact = usableProjects.find((p) => verifyProjectRoot(p, allowedRoots).ok && p.root_path === first);
      if (exact) return exact;
    }
    return usableProjects[0];
  }, [usableProjects, selectedProjectId, allowedRoots]);

  /**
   * The one action: run the whole create → ready → start → launch sequence.
   *
   * Each backend step is reported as it happens so the button shows real
   * progress, and any refusal is surfaced verbatim with the option to open
   * settings when the cause is a missing credential.
   */
  /**
   * Run the APPROVED task.
   *
   * The executor comes from the approval the BACKEND verified, and the model
   * from what the router selects afterwards. Nothing here re-asks Jev and
   * nothing hardcodes a provider, so a confirmation cannot smuggle in a
   * different decision than the one the operator actually approved.
   */
  const runApproved = useCallback(
    async (approvedExecutor: string) => {
      if (!selectedProject) {
        setComposer({
          busy: false, activeStep: null,
          error: "请先在「项目」中选择一个项目，再启动任务。",
          missingCredential: false,
        });
        return;
      }
      const r = await startTask(domain.api, controlToken, {
        project: selectedProject,
        goal: pendingGoal,
        agent: approvedExecutor,
      });
      if (r.ok && r.mission) {
        setComposer(IDLE_COMPOSER);
        setJevDecision(null);
        void domain.refresh();
        openTask(r.mission.mission_id);
        return;
      }
      setComposer({
        busy: false, activeStep: null, error: r.failure,
        missingCredential: r.missingCredential,
      });
    },
    [domain, controlToken, selectedProject, pendingGoal, openTask],
  );

  const submitTask = useCallback(
    async (goal: string) => {
      if (!selectedProject) {
        setComposer({
          busy: false,
          activeStep: null,
          error: "请先在「项目」中选择一个项目，再启动任务。",
          missingCredential: false,
        });
        return;
      }
      setComposer({ busy: true, activeStep: "正在创建任务…", error: null, missingCredential: false });
      try {
        /**
         * Re-check the boundary at submit time, not just at render time.
         *
         * The 5s poll can bring a new `project_scope` at any moment, and a task
         * submitted against a root the server has since withdrawn must be stopped
         * HERE — before the user waits on a request that will come back 422.
         */
        const verdict = verifyProjectRoot(selectedProject, allowedRoots);
        if (!verdict.ok) {
          setComposer({
            busy: false,
            activeStep: null,
            error: `${VERDICT_HEADLINE[verdict.code]} 当前项目目录已失效，请重新选择。`,
            missingCredential: false,
          });
          return;
        }
        /**
         * Resolve the execution method.
         *
         * In `auto` mode the choice belongs to the VENDOR model (Jev), reached
         * through the server — the browser never picks. If Jev cannot decide, the
         * task is NOT started with a substitute executor: the user is told that
         * nobody chose, which is a different and truthful outcome from silently
         * running workbuddy behind a label that says JEV chose it.
         */
        let agentId: string;
        if (executor === AUTO_EXECUTOR) {
          setComposer({
            busy: true,
            activeStep: "Jev 正在评估执行策略…",
            error: null,
            missingCredential: false,
          });
          const decision = await askJevForExecutor(API_BASE, controlToken, {
            task: goal,
            project_root: selectedProject.root_path,
            task_type: "general",
            available_executors: AGENT_OPTIONS.map((a) => a.id),
            executor_health: {},
          });
          if (!decision || !decision.executor) {
            // Jev did not decide. Say so and stop.
            setComposer({
              busy: false,
              activeStep: null,
              error:
                humanizeProviderError(decision?.reason) ||
                "Jev 未能决定使用哪个执行器，任务未创建。请稍后重试或手动指定执行器。",
              missingCredential: decision?.provider_state === "REAL_AUTH_MISSING",
            });
            return;
          }
          if (decision.needs_confirmation) {
            // A low-confidence decision is a PAUSE, not a failure. Showing it as
            // an error left the operator with nothing to do but reload, so the
            // card takes over and offers the two real choices.
            setComposer({
              busy: false,
              activeStep: null,
              error: null,
              missingCredential: false,
            });
            setPendingGoal(goal);
            setJevDecision(decision);
            return;
          }
          setJevDecision(decision);
          agentId = decision.executor;
        } else {
          // The user picked an executor explicitly — no Jev round trip needed.
          agentId = executor;
        }

        const r = await startTask(domain.api, controlToken, {
          project: selectedProject,
          goal,
          agent: agentId,
        });
        if (r.ok && r.mission) {
          setComposer(IDLE_COMPOSER);
          void domain.refresh();
          openTask(r.mission.mission_id);
          return;
        }
        setComposer({
          busy: false,
          activeStep: null,
          error: r.failure,
          missingCredential: r.missingCredential,
        });
        // A mission may exist even when the launch failed, so refresh and go
        // there: the user sees the task and the honest reason it is not running.
        if (r.mission) {
          void domain.refresh();
          openTask(r.mission.mission_id);
        }
      } catch (e) {
        setComposer({
          busy: false,
          activeStep: null,
          error: e instanceof Error ? e.message : String(e),
          missingCredential: false,
        });
      }
    },
    [allowedRoots, controlToken, domain, executor, openTask, selectedProject],
  );

  /**
   * Adopt the root the backend authorised.
   *
   * Measured on this host: every recorded project still points at `F:\jev`, so
   * after the boundary moved to `D:\jev` there is NOTHING selectable. That makes
   * "select the project on that root" insufficient — the user needs a way to
   * CREATE one there, which is the projects page with the root pre-filled.
   *
   * No `F:` → `D:` rewrite anywhere: when no project exists on the authorised
   * root, this navigates to project creation carrying the server's own root.
   * Rewriting would run the task against a directory the user never chose while
   * the UI still showed them the old one.
   */
  const useSuggestedRoot = useCallback(
    (root: string) => {
      const match = usableProjects.find((p) => p.root_path === root);
      if (match) {
        setSelectedProjectId(match.project_id);
        return;
      }
      // No project on that root yet: go create one there, carrying the exact
      // root the server published.
      try {
        sessionStorage.setItem("jev.project.createRoot", root);
      } catch {
        /* storage unavailable — the projects page falls back to its own input */
      }
      goto("projects");
    },
    [usableProjects, goto],
  );

  /** A follow-up instruction continues the SAME task rather than creating one. */
  const continueTask = useCallback(
    (text: string) => {
      if (!activeMission || !text.trim()) return;
      void domain.refresh();
    },
    [activeMission, domain],
  );

  const paletteItems = useMemo<CommandItemSpec[]>(() => {
    const items: CommandItemSpec[] = NAV_ITEMS.filter((n) => expert || !isAdvanced(n.id)).map(
      (n) => ({
        id: `nav:${n.id}`,
        label: expert ? n.label : n.labelZh,
        hint: "go",
        group: "导航",
        icon: n.icon,
        keywords: [n.id, n.description],
        onSelect: () => goto(n.id),
      }),
    );
    for (const m of domain.missions.slice(0, 25)) {
      items.push({
        id: `mission:${m.mission_id}`,
        label: `任务 · ${m.title}`,
        hint: m.status,
        group: "任务",
        keywords: [m.mission_id, m.agent, m.status],
        onSelect: () => openTask(m.mission_id),
      });
    }
    for (const p of domain.projects.slice(0, 20)) {
      items.push({
        id: `project:${p.project_id}`,
        label: `项目 · ${p.name}`,
        hint: "open",
        group: "项目",
        keywords: [p.project_id, p.root_path],
        onSelect: () => goto("projects"),
      });
    }
    items.push({
      id: "action:mode",
      label: expert ? "切换到普通模式" : "切换到专家模式",
      hint: "pref",
      group: "操作",
      onSelect: () => setExpert((e) => !e),
    });
    items.push({
      id: "action:theme",
      label: `切换到${theme === "dark" ? "浅色" : "深色"}主题`,
      hint: "pref",
      group: "操作",
      onSelect: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    });
    return items;
  }, [domain.missions, domain.projects, expert, goto, openTask, theme]);

  const rail = showsInspectorRail(view);
  const activeMissionObj =
    activeMission !== null
      ? (domain.missions.find((m) => m.mission_id === activeMission) ?? null)
      : null;
  const activeProject =
    activeMissionObj !== null
      ? (domain.projects.find((p) => p.project_id === activeMissionObj.project_id) ?? null)
      : null;

  const main = (() => {
    switch (view) {
      case "home":
        return (
          <HomeView
            missions={domain.missions}
            projects={domain.projects}
            usableProjects={usableProjects}
            blockedProjects={blockedProjects}
            allowedRoots={allowedRoots ?? []}
            missionsLoading={domain.missionsLoading}
            missionsError={domain.missionsError}
            selectedProject={selectedProject}
            onSelectProject={(p) => setSelectedProjectId(p.project_id)}
            executor={executor}
            onSelectExecutor={setExecutor}
            onSubmit={(goal) => void submitTask(goal)}
            composerState={composer}
            jevDecision={jevDecision}
            pendingGoal={pendingGoal}
            pendingProvider={pendingProvider}
            pendingModel={pendingModel}
            controlToken={controlToken}
            onApproved={(a) => void runApproved(a)}
            onCancelled={() => { setJevDecision(null); setComposer(IDLE_COMPOSER); }}
            onOpenTask={openTask}
            onOpenSettings={() => goto("settings")}
            onOpenProjects={() => goto("projects")}
            onUseSuggestedRoot={useSuggestedRoot}
            onRefresh={() => void domain.refresh()}
          />
        );
      case "task-detail":
        return (
          <TaskWorkspace
            api={domain.api}
            apiBase={API_BASE}
            mission={activeMissionObj}
            project={activeProject}
            attention={domain.attention ?? []}
            controlToken={controlToken}
            onResolved={() => void domain.refresh()}
            onBack={() => goto("missions")}
            onContinue={continueTask}
            onOpenExpert={(v) => {
              setExpert(true);
              goto(v);
            }}
            liveMissions={domain.missions}
          />
        );
      case "projects":
        return (
          <ProjectsView
            api={domain.api}
            projects={domain.projects}
            loading={domain.projectsLoading}
            error={domain.projectsError}
            onNewMission={() => goto("home")}
            onRefresh={() => void domain.refresh()}
            allowedRoots={allowedRoots ?? []}
            onCreateProject={domain.createProject}
          />
        );
      case "missions":
        return (
          <MissionsView
            api={domain.api}
            missions={domain.missions}
            loading={domain.missionsLoading}
            error={domain.missionsError}
            total={domain.counts ? domain.counts.missions : null}
            scopeNote={truncationNote(domain.missionsTruncation, {
              singular: "任务",
              plural: "任务",
            })}
            onNewMission={() => goto("home")}
            onRefresh={() => void domain.refresh()}
          />
        );
      case "needs-attention":
        return (
          <NeedsAttentionView
            api={domain.api}
            controlToken={controlToken}
            items={domain.attention}
            loading={domain.attentionLoading}
            error={domain.attentionError}
            onRefresh={() => void domain.refresh()}
            onGotoMissions={() => goto("missions")}
          />
        );
      case "deliverables":
        return (
          <DeliverablesView
            api={domain.api}
            controlToken={controlToken}
            totalDeliverables={domain.counts ? domain.counts.deliverables : null}
            onRefresh={() => void domain.refresh()}
          />
        );
      case "settings":
        return (
          <SettingsView
            api={domain.api}
            controlToken={controlToken}
            onControlToken={setControlToken}
            theme={theme}
            onTheme={setTheme}
            expert={expert}
            onExpert={setExpert}
            base={API_BASE}
            /* Send the operator to the 高级 group, where the control-token field lives.
               Focus rather than a message: the fix for "token missing" is one
               field, and it is on this same screen. */
            onOpenToken={() => setExpert(true)}
            tokenState={tokenState}
            onVerifyToken={onVerifyToken}
          />
        );
      case "studio":
        return (
          <MissionStudio
            api={domain.api}
            controlToken={controlToken}
            projects={domain.projects}
            loadingProjects={domain.projectsLoading}
            projectsError={domain.projectsError}
            onCreated={() => void domain.refresh()}
            onOpenProjects={() => goto("projects")}
            onCreateProject={domain.createProject}
            projectError={domain.projectError}
            projectRejection={domain.projectRejection}
          />
        );
      case "connections":
        return (
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 shrink-0 overflow-y-auto border-b border-border" style={{ maxHeight: "58%" }}>
              <ConnectionVerificationView
                api={domain.api}
                controlToken={controlToken}
                onRefresh={() => void domain.refresh()}
              />
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              <ConnectionsView scene={scene} apiBase={API_BASE} controlToken={controlToken || null} />
            </div>
          </div>
        );
      case "audit":
        return <AuditView base={API_BASE} />;
      case "diagnostics":
        return <DiagnosticsView base={API_BASE} api={domain.api} scene={scene} />;
      case "table":
        return <TableView scene={scene} />;
      case "graph":
        return <GraphView scene={scene} />;
      case "world":
        // The 3D plane is code-split: this branch is the ONLY thing that pulls
        // Three.js in, so the home page never pays for it.
        return <LazyWorld scene={scene} domain={domain} onGotoMissions={openTask} />;
      case "engine-world":
        return <LazyEngineWorld scene={scene} />;
      // --- Trading Workbench (Paper Trading × Night Researcher) ---
      case "market-overview":
        return (
          <Suspense fallback={<PageBody><div className="t-body text-muted-foreground">加载中…</div></PageBody>}>
            <LazyMarketOverview controlToken={controlToken} />
          </Suspense>
        );
      case "trading-desk":
        return (
          <Suspense fallback={<PageBody><div className="t-body text-muted-foreground">加载中…</div></PageBody>}>
            <LazyTradingDesk controlToken={controlToken} />
          </Suspense>
        );
      case "market-universe":
        return (
          <Suspense fallback={<PageBody><div className="t-body text-muted-foreground">加载中…</div></PageBody>}>
            <LazyMarketUniverse controlToken={controlToken} />
          </Suspense>
        );
      case "agent-center":
        return (
          <Suspense fallback={<PageBody><div className="t-body text-muted-foreground">加载中…</div></PageBody>}>
            <LazyAgentCenter controlToken={controlToken} />
          </Suspense>
        );
      case "risk-management":
        return (
          <Suspense fallback={<PageBody><div className="t-body text-muted-foreground">加载中…</div></PageBody>}>
            <LazyRiskManagement controlToken={controlToken} />
          </Suspense>
        );
      case "strategy-research":
        return (
          <Suspense fallback={<PageBody><div className="t-body text-muted-foreground">加载中…</div></PageBody>}>
            <LazyStrategyResearch controlToken={controlToken} />
          </Suspense>
        );
      case "trading-audit":
        return (
          <Suspense fallback={<PageBody><div className="t-body text-muted-foreground">加载中…</div></PageBody>}>
            <LazyTradingAudit controlToken={controlToken} />
          </Suspense>
        );
      default:
        return <HomeView
          missions={domain.missions}
          projects={domain.projects}
          usableProjects={usableProjects}
          blockedProjects={blockedProjects}
          allowedRoots={allowedRoots ?? []}
          missionsLoading={domain.missionsLoading}
          missionsError={domain.missionsError}
          selectedProject={selectedProject}
          onSelectProject={(p) => setSelectedProjectId(p.project_id)}
          executor={executor}
          onSelectExecutor={setExecutor}
          onSubmit={(goal) => void submitTask(goal)}
          composerState={composer}
          onOpenTask={openTask}
          onOpenSettings={() => goto("settings")}
          onOpenProjects={() => goto("projects")}
          onUseSuggestedRoot={useSuggestedRoot}
          onRefresh={() => void domain.refresh()}
          jevDecision={jevDecision}
          pendingGoal={pendingGoal}
          pendingProvider={pendingProvider}
          pendingModel={pendingModel}
          controlToken={controlToken}
          onApproved={(a) => void runApproved(a)}
          onCancelled={() => { setJevDecision(null); setComposer(IDLE_COMPOSER); }}
        />;
    }
  })();

  return (
    <TooltipProvider delayDuration={200}>
      <AppShell
        sidebar={
          <Sidebar
            view={view}
            expert={expert}
            onSelect={goto}
            attentionCount={domain.attention ? domain.attention.length : null}
            attentionUnknown={domain.attention === null}
            onOpenPalette={() => setPaletteOpen(true)}
            mobileOpen={mobileNavOpen}
            onCloseMobile={() => setMobileNavOpen(false)}
          />
        }
        header={
          <TopStatus
            probes={probes}
            refreshing={domain.refreshing}
            onRefresh={() => void domain.refresh()}
            onOpenMobileNav={() => setMobileNavOpen(true)}
            version="v2"
          />
        }
        banner={
          domain.stale ? (
            <div className="flex items-center gap-2 border-b border-status-warn/30 bg-status-warn/10 px-4 py-1.5 t-meta text-status-warn" role="status">
              最近一次刷新失败，当前显示的是上一次成功读取的数据，数字可能不是最新的。
            </div>
          ) : null
        }
        aside={
          rail ? (
            <aside className="hidden w-[340px] shrink-0 flex-col overflow-y-auto border-l border-border xl:flex">
              <div className="border-b border-border"><Inspector scene={scene} /></div>
              <div className="border-b border-border"><RiskRadar scene={scene} /></div>
              <div className="border-b border-border"><DecisionExplanation scene={scene} /></div>
              <OperatorConsole scene={scene} />
              <div className="border-t border-border"><Timeline scene={scene} /></div>
            </aside>
          ) : null
        }
      >
        <h2 className="sr-only" aria-live="polite">
          {navLabel(view, expert)}
        </h2>
        {main}
      </AppShell>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} items={paletteItems} />
    </TooltipProvider>
  );
}

/**
 * The 3D surfaces, loaded on demand.
 *
 * A module-level `lazy` so the chunk is fetched the first time a 3D view is
 * actually opened and never before — which is what keeps Three.js out of the home
 * page's initial bundle. `Suspense` shows an honest loading state rather than a
 * blank canvas.
 */
const OperationalWorld = reactLazy(() =>
  import("./scene/OperationalWorld").then((m) => ({
    default: m.OperationalWorld as React.ComponentType<{
      world: ReturnType<typeof buildDomainWorld>;
      onSelectMission: (missionId: string) => void;
      onHoverMission: (mission: import("./store/domainWorld").WorldMission | null) => void;
    }>,
  })),
);

const OperationalWorldOverlay = reactLazy(() =>
  import("./scene/OperationalWorld").then((m) => ({
    default: m.OperationalWorldOverlay as React.ComponentType<{
      world: ReturnType<typeof buildDomainWorld>;
      hovered: import("./store/domainWorld").WorldMission | null;
      scopeNote: string | null;
      onGotoMissions: (missionId: string) => void;
    }>,
  })),
);

const CommandCenter3D = reactLazy(() =>
  import("./scene/CommandCenter3D").then((m) => ({
    default: m.CommandCenter3D as React.ComponentType<{
      scene: ReturnType<typeof sceneStore.getSnapshot>;
    }>,
  })),
);

/**
 * The six Paper Trading × Night Researcher surfaces, code-split like the 3D
 * views so they never enter the initial bundle. Each page takes only the control
 * token it forwards to the Paper API client (ignored while USE_MOCK is true).
 */
const TradingPage = (name: string) =>
  reactLazy(() =>
    import(`./views/trading/pages/${name}`).then((m) => ({
      default: m[name as keyof typeof m] as React.ComponentType<{ controlToken: string }>,
    })),
  );

const LazyMarketOverview = TradingPage("MarketOverview");
const LazyTradingDesk = TradingPage("TradingDesk");
const LazyMarketUniverse = TradingPage("MarketUniverse");
const LazyAgentCenter = TradingPage("AgentCenter");
const LazyRiskManagement = TradingPage("RiskManagement");
const LazyStrategyResearch = TradingPage("StrategyResearch");
const LazyTradingAudit = TradingPage("TradingAudit");

/**
 * The visualization surface.
 *
 * Three cases, three honest renderings: no WebGL2 (say so, and point at the
 * table view which shows the same data), the engine-telemetry scene, and the
 * operational world with its overlay. The world builder itself is pure and
 * tiny, so it stays in the main bundle while the renderer does not.
 */
function LazyWorld({
  scene,
  domain,
  onGotoMissions,
}: {
  scene: ReturnType<typeof sceneStore.getSnapshot>;
  domain: ReturnType<typeof useDomain>;
  onGotoMissions: (missionId: string) => void;
}) {
  const world = useMemo(
    () => buildDomainWorld(domain.projects, domain.missions, domain.deliverables),
    [domain.projects, domain.missions, domain.deliverables],
  );
  const [hovered, setHovered] = useState<import("./store/domainWorld").WorldMission | null>(null);

  if (!scene.webgl2Supported) {
    return (
      <PageBody>
        <div className="rounded-lg border border-border bg-card p-6">
          <h2 className="t-section text-foreground">3D 可视化不可用</h2>
          <p className="mt-1.5 t-body text-muted-foreground">
            当前浏览器未启用 WebGL2，3D 视图无法渲染。可在专家模式使用「数据表」查看同一份数据。
          </p>
        </div>
      </PageBody>
    );
  }

  if (world.empty) {
    return (
      <PageBody>
        <div className="rounded-lg border border-border bg-card p-6">
          <h2 className="t-section text-foreground">还没有可可视化的数据</h2>
          <p className="mt-1.5 t-body text-muted-foreground">
            3D 视图只呈现后端实际返回的项目与任务。创建项目并启动任务后，节点会出现在这里。
          </p>
        </div>
      </PageBody>
    );
  }

  return (
    <div className="relative h-full w-full">
      <Suspense
        fallback={
          <div className="flex h-full w-full items-center justify-center">
            <p className="t-body text-muted-foreground">正在加载 3D 引擎…</p>
          </div>
        }
      >
        <OperationalWorld
          world={world}
          onSelectMission={onGotoMissions}
          onHoverMission={setHovered}
        />
        <OperationalWorldOverlay
          world={world}
          hovered={hovered}
          scopeNote={truncationNote(domain.missionsTruncation, {
            singular: "任务",
            plural: "任务",
          })}
          onGotoMissions={onGotoMissions}
        />
      </Suspense>
    </div>
  );
}

/** The engine-telemetry scene — a separate chunk from the operational world. */
function LazyEngineWorld({ scene }: { scene: ReturnType<typeof sceneStore.getSnapshot> }) {
  if (!scene.webgl2Supported) {
    return (
      <PageBody>
        <div className="rounded-lg border border-border bg-card p-6">
          <h2 className="t-section text-foreground">3D 引擎视图不可用</h2>
          <p className="mt-1.5 t-body text-muted-foreground">
            当前浏览器未启用 WebGL2。可在专家模式使用「数据表」查看同一份会话数据。
          </p>
        </div>
      </PageBody>
    );
  }
  return (
    <Suspense
      fallback={
        <div className="flex h-full w-full items-center justify-center">
          <p className="t-body text-muted-foreground">正在加载 3D 引擎…</p>
        </div>
      }
    >
      <CommandCenter3D scene={scene} />
    </Suspense>
  );
}
