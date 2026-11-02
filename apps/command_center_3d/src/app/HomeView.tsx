/**
 * HomeView — the simplest page in the product.
 *
 * THE ACCEPTANCE TEST FOR THIS FILE
 * --------------------------------
 * Someone who has never seen JEV opens it and must know, in about ten seconds:
 *
 *   1. what this thing does      → the headline and sub-line
 *   2. how to start              → one input and one button
 *   3. what happened recently    → the task list at the bottom
 *
 * Everything else is deliberately absent. No counters, no policy version, no
 * actuation flag, no 3D, no connection matrix. A user does not need any of it to
 * decide what to do next, and showing it is what made the old Home feel like a
 * dashboard instead of a product.
 *
 * The status indicator in the header is the only chrome, and it is one word wide
 * until clicked.
 */
import { useMemo } from "react";
import { PageBody } from "../components/AppShell";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { RecentTaskCard } from "./RecentTaskCard";
import { ProjectGate } from "./ProjectGate";
import { TaskComposer, type ComposerSubmitState } from "./TaskComposer";
import { ConfirmationCard } from "./ConfirmationCard";
import type { ExecutorDecision } from "./TaskComposer";
import type { Mission, Project } from "./domainApi";

/** How many recent tasks the home page lists. */
const RECENT_LIMIT = 6;

export function HomeView({
  missions,
  projects,
  usableProjects,
  blockedProjects,
  allowedRoots,
  missionsLoading,
  missionsError,
  selectedProject,
  onSelectProject,
  executor,
  onSelectExecutor,
  onSubmit,
  composerState,
  onOpenTask,
  onOpenSettings,
  onOpenProjects,
  onUseSuggestedRoot,
  onRefresh,
  jevDecision,
  pendingGoal,
  pendingProvider,
  pendingModel,
  controlToken,
  onApproved,
  onCancelled,
}: {
  missions: Mission[];
  projects: Project[];
  /** Projects inside the backend's boundary. */
  usableProjects: Project[];
  /** Projects whose recorded root is outside it, with the reason. */
  blockedProjects: Array<{ project: Project; reason: string }>;
  /** The roots the backend authorised. */
  allowedRoots: string[];
  missionsLoading: boolean;
  missionsError: string | null;
  selectedProject: Project | null;
  onSelectProject: (p: Project) => void;
  executor: string;
  onSelectExecutor: (a: string) => void;
  onSubmit: (goal: string) => void;
  composerState: ComposerSubmitState;
  onOpenTask: (missionId: string) => void;
  onOpenSettings: () => void;
  onOpenProjects: () => void;
  /**
   * Adopt a root the BACKEND authorised (always `allowedRoots[0]`).
   *
   * The handler selects the usable project that already sits on that root. If no
   * project does, it goes to the projects page to create one — it never invents
   * a path and never rewrites the expired one.
   */
  onUseSuggestedRoot: (root: string) => void;
  onRefresh: () => void;
  /** Jev's decision, when it needs a human before anything runs. */
  jevDecision: ExecutorDecision | null;
  /** The goal the decision was made about, so the card can bind to it. */
  pendingGoal: string;
  pendingProvider: string | null;
  pendingModel: string | null;
  controlToken: string;
  onApproved: (executor: string) => void;
  onCancelled: () => void;
}) {
  /**
   * The recent list.
   *
   * Sorted by real `updated_at` (the server's clock, not the browser's), and
   * capped. A task that is still running is kept at the top regardless of its
   * timestamp: the thing happening right now is the thing the user came to see.
   */
  const recent = useMemo(() => {
    const running = missions.filter(
      (m) => m.status === "RUNNING" || m.status === "STARTING" || m.status === "VERIFYING",
    );
    const rest = missions
      .filter((m) => m.status !== "RUNNING" && m.status !== "STARTING" && m.status !== "VERIFYING")
      .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
    return [...running, ...rest].slice(0, RECENT_LIMIT);
  }, [missions]);

  return (
    <PageBody>
      {/* --- the one question ------------------------------------------- */}
      <section className="pt-6 lg:pt-12">
        <h1 className="t-hero text-foreground">告诉 JEV 你想完成什么。</h1>
        <p className="mt-3 max-w-2xl t-body text-muted-foreground">
          JEV 会分析任务、制定计划、选择合适的执行器并监督完成。
        </p>
      </section>

      {/* --- the one action -------------------------------------------- */}
      <section className="mt-8 max-w-3xl">
        <TaskComposer
          projects={projects}
          usableProjects={usableProjects}
          blockedProjects={blockedProjects}
          allowedRoots={allowedRoots}
          selectedProject={selectedProject}
          onSelectProject={onSelectProject}
          executor={executor}
          onSelectExecutor={onSelectExecutor}
          onSubmit={onSubmit}
          state={composerState}
          onOpenSettings={onOpenSettings}
        />
      </section>

      {/* The confirmation card, when Jev's decision needs a person.
          Rendered beside the composer rather than in place of it: the goal text
          is still on screen, so the operator confirms the thing they can read. */}
      {jevDecision && jevDecision.needs_confirmation ? (
        <section className="mt-6 max-w-3xl">
          <ConfirmationCard
            decision={jevDecision}
            task={pendingGoal}
            projectRoot={selectedProject?.root_path ?? ""}
            provider={pendingProvider}
            model={pendingModel}
            controlToken={controlToken}
            onApproved={onApproved}
            onCancelled={onCancelled}
          />
        </section>
      ) : null}

      {/* --- an expired project, explained rather than vanished ------------
          A project whose root is outside the backend's boundary is still listed
          by the API, so hiding it would make it look deleted. The card says what
          happened, what the boundary is, and offers the one root the server
          authorised. There is no F: → D: substitution: the button navigates to a
          root the BACKEND published. */}
      {blockedProjects.length > 0 ? (
        <section className="mt-6 max-w-3xl">
          <ProjectGate
            verdict={{
              ok: false,
              code: "ROOT_OUT_OF_SCOPE",
              reason: `${blockedProjects.length} 个项目的目录不在允许范围内。`,
            }}
            currentRoot={blockedProjects[0].project.root_path}
            allowedRoots={allowedRoots}
            onUseSuggested={onUseSuggestedRoot}
            onReselect={onOpenProjects}
          />
        </section>
      ) : null}

      {/* --- what happened recently ------------------------------------ */}
      <section className="mt-14">
        <div className="mb-4 flex items-baseline justify-between gap-3">
          <h2 className="t-section text-foreground">最近任务</h2>
          {recent.length > 0 ? (
            <span className="t-meta text-muted-foreground">
              {missions.length > RECENT_LIMIT
                ? `显示最近 ${RECENT_LIMIT} 个，共 ${missions.length} 个`
                : `共 ${missions.length} 个`}
            </span>
          ) : null}
        </div>

        {missionsLoading && missions.length === 0 ? (
          <LoadingState label="正在读取任务…" />
        ) : missionsError && missions.length === 0 ? (
          <ErrorState title="无法读取任务列表" detail={missionsError} onRetry={onRefresh} />
        ) : recent.length === 0 ? (
          <EmptyState
            title="还没有任务"
            hint="在上方描述你的第一个任务，JEV 会自动完成分析、规划与执行。"
          />
        ) : (
          <ul className="grid gap-2.5 sm:grid-cols-2">
            {recent.map((m) => (
              <li key={m.mission_id}>
                <RecentTaskCard mission={m} onOpen={() => onOpenTask(m.mission_id)} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </PageBody>
  );
}
