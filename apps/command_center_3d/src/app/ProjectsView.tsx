/**
 * ProjectsView — the project list plus the missions under each project.
 *
 * Read-only. Expanding a project fetches its detail (which carries its missions)
 * from the server; the view never derives a mission list from a cached one.
 */
import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FolderKanban,
  FolderPlus,
  Rocket,
} from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/Card";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { cn } from "../ui/cn";
import { missionTone, toneVariant } from "../app/status";
import { isWithinAllowedRoots } from "../app/projectRoot";
import { fmtRelative, type DomainApi, type Mission, type Project, type ProjectDetail } from "../app/domainApi";

export function ProjectsView({
  api,
  projects,
  loading,
  error,
  onNewMission,
  onRefresh,
  allowedRoots,
  onCreateProject,
}: {
  api: DomainApi;
  projects: Project[];
  loading: boolean;
  error: string | null;
  onNewMission: () => void;
  onRefresh: () => void;
  /**
   * The roots the backend authorised. The create form only ever offers one of
   * these — a project cannot be created outside the boundary, so the input is
   * not a free-text field but a choice among roots the server published.
   */
  allowedRoots: string[];
  /** Create a project at an authorised root. Returns the project or null. */
  onCreateProject: (name: string, rootPath: string) => Promise<Project | null>;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // --- create-project form (boundary-constrained) ---
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  // Pre-filled from the home page's 「选择 <root>」 when it hands one over.
  const [newRoot, setNewRoot] = useState<string>(() => {
    try {
      const handed = sessionStorage.getItem("jev.project.createRoot");
      if (handed) {
        sessionStorage.removeItem("jev.project.createRoot");
        return handed;
      }
    } catch {
      /* storage unavailable */
    }
    return "";
  });
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  /**
   * Create at the chosen root.
   *
   * The root is validated against `allowedRoots` HERE as well, not left to the
   * server. That is deliberate: the server's 422 is a JSON contract, and the
   * user should not have to read one to learn they typed a path the boundary
   * rejects. The server still validates — this only moves the first, cheap
   * rejection to where the sentence can be written.
   */
  async function submitCreate() {
    const root = newRoot.trim();
    if (!root || !newName.trim()) return;
    if (!allowedRoots.some((a) => isWithinAllowedRoots(root, [a]))) {
      setCreateError("只能在后端允许的工作区范围内创建项目。");
      return;
    }
    setCreateBusy(true);
    setCreateError(null);
    const created = await onCreateProject(newName.trim(), root);
    setCreateBusy(false);
    if (!created) {
      setCreateError("创建失败，请检查控制令牌与目录是否存在。");
      return;
    }
    setCreating(false);
    setNewName("");
  }

  async function toggle(p: Project) {
    if (openId === p.project_id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(p.project_id);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    const r = await api.project(p.project_id);
    setDetailLoading(false);
    if (!r.ok || !r.data) {
      setDetailError(r.error ?? "project detail unavailable");
      return;
    }
    setDetail(r.data);
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Projects</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {loading ? "Loading…" : `${projects.length} project${projects.length === 1 ? "" : "s"}`}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onRefresh}>
              Refresh
            </Button>
            <Button size="sm" onClick={onNewMission}>
              <Rocket className="h-3.5 w-3.5" />
              New Mission
            </Button>
          </div>
        </div>

        {error && <InlineError message={error} />}

        {/* --- create at an authorised root -------------------------------
            This is the way OUT when every recorded project is stale. The root
            input is a CHOICE among backend-published roots, not free text, so a
            project cannot be created outside the boundary by construction. */}
        {allowedRoots.length > 0 ? (
          <div className="rounded-lg border border-border bg-card p-4">
            {creating ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="t-section text-foreground">新建项目</h3>
                  <button
                    type="button"
                    onClick={() => setCreating(false)}
                    className="rounded-md border border-border px-2.5 py-1 t-meta text-foreground transition-colors hover:bg-accent"
                  >
                    取消
                  </button>
                </div>
                <div className="flex flex-wrap gap-2">
                  <div className="min-w-[200px] flex-1">
                    <label htmlFor="np-name" className="mb-1 block t-meta text-muted-foreground">
                      项目名称
                    </label>
                    <input
                      id="np-name"
                      type="text"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder="例如：JEV"
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-[14px] text-foreground outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring"
                    />
                  </div>
                  <div className="min-w-[200px] flex-1">
                    <label htmlFor="np-root" className="mb-1 block t-meta text-muted-foreground">
                      项目目录（仅限允许范围）
                    </label>
                    <select
                      id="np-root"
                      value={newRoot}
                      onChange={(e) => setNewRoot(e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-[13px] text-foreground outline-none focus-visible:border-ring"
                    >
                      <option value="">请选择目录</option>
                      {allowedRoots.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {createError ? (
                  <p className="t-body text-destructive" role="alert">
                    {createError}
                  </p>
                ) : null}
                <button
                  type="button"
                  onClick={() => void submitCreate()}
                  disabled={createBusy || !newName.trim() || !newRoot.trim()}
                  className={
                    createBusy || !newName.trim() || !newRoot.trim()
                      ? "cursor-not-allowed rounded-md bg-muted px-4 py-2 text-[14px] text-muted-foreground"
                      : "rounded-md bg-primary px-4 py-2 text-[14px] font-medium text-primary-foreground transition-colors hover:brightness-110"
                  }
                >
                  {createBusy ? "正在创建…" : "创建项目"}
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="t-section text-foreground">新建项目</h3>
                  <p className="mt-0.5 t-meta text-muted-foreground">
                    允许的工作区范围：{allowedRoots.join("、")}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setCreating(true);
                    setNewRoot(allowedRoots[0] ?? "");
                    setCreateError(null);
                  }}
                  className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-[14px] text-foreground transition-colors hover:bg-accent"
                >
                  <FolderPlus className="h-4 w-4" aria-hidden="true" />
                  新建项目
                </button>
              </div>
            )}
          </div>
        ) : null}

        {loading && projects.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : projects.length === 0 && !error ? (
          <EmptyState
            icon={FolderPlus}
            title="No projects yet"
            detail="Create a project in Mission Studio — every mission runs under one."
            action={
              <Button size="sm" onClick={onNewMission}>
                Open Mission Studio
              </Button>
            }
          />
        ) : (
          <div className="space-y-2">
            {projects.map((p) => {
              const open = openId === p.project_id;
              return (
                <Card key={p.project_id}>
                  <button
                    onClick={() => void toggle(p)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left"
                    aria-expanded={open}
                  >
                    {open ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <FolderKanban className="h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-foreground">
                        {p.name}
                      </div>
                      <div className="truncate text-[11px] text-muted-foreground">
                        {p.root_path || "no root path"} · {p.description || "no description"}
                      </div>
                    </div>
                    <span className="shrink-0 text-[10px] text-muted-foreground">
                      {fmtRelative(p.updated_at)}
                    </span>
                  </button>
                  {open && (
                    <CardContent className="border-t border-border pt-3">
                      {detailLoading ? (
                        <Skeleton className="h-12 w-full" />
                      ) : detailError ? (
                        <InlineError message={detailError} />
                      ) : detail && detail.missions.length > 0 ? (
                        <ul className="divide-y divide-border">
                          {detail.missions.map((m) => (
                            <MissionRow key={m.mission_id} mission={m} />
                          ))}
                        </ul>
                      ) : (
                        <div className="text-xs text-muted-foreground">
                          No missions under this project yet.
                        </div>
                      )}
                    </CardContent>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export function MissionRow({ mission }: { mission: Mission }) {
  return (
    <li className="flex items-center gap-3 py-2">
      <Badge variant={toneVariant(missionTone(mission.status))}>{mission.status}</Badge>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-foreground">{mission.title}</div>
        <div className="truncate text-[11px] text-muted-foreground">
          {mission.agent} · {mission.goal || "no goal recorded"}
        </div>
      </div>
      <span className="shrink-0 text-[10px] text-muted-foreground">
        {fmtRelative(mission.updated_at)}
      </span>
    </li>
  );
}
