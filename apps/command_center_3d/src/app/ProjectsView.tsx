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
import { fmtRelative, type DomainApi, type Mission, type Project, type ProjectDetail } from "../app/domainApi";

export function ProjectsView({
  api,
  projects,
  loading,
  error,
  onNewMission,
  onRefresh,
}: {
  api: DomainApi;
  projects: Project[];
  loading: boolean;
  error: string | null;
  onNewMission: () => void;
  onRefresh: () => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

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
