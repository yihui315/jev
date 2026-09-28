/**
 * DeliverablesView — artifacts produced by missions, with provenance.
 *
 * Artifacts hang off missions, so the view loads missions first and then fans
 * out to each mission's artifact list. The fan-out is bounded (the first N
 * missions) and every failure is reported per-mission rather than silently
 * dropping that mission's deliverables.
 */
import { useCallback, useEffect, useState } from "react";
import { Boxes, FileCode2, FileText, Image as ImageIcon, Package } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { artifactTone, toneVariant } from "../app/status";
import {
  fmtBytes,
  fmtRelative,
  type Artifact,
  type DomainApi,
  type Mission,
} from "../app/domainApi";

const MAX_MISSIONS_SCANNED = 50;

const KIND_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  code: FileCode2,
  patch: FileCode2,
  document: FileText,
  report: FileText,
  test_result: FileText,
  screenshot: ImageIcon,
  build: Package,
  dataset: Package,
};

interface ArtifactRow {
  artifact: Artifact;
  missionTitle: string;
}

export function DeliverablesView({
  api,
  missions,
  missionsLoading,
  missionsError,
  onRefresh,
}: {
  api: DomainApi;
  missions: Mission[];
  missionsLoading: boolean;
  missionsError: string | null;
  onRefresh: () => void;
}) {
  const [rows, setRows] = useState<ArtifactRow[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (missions.length === 0) {
      setRows([]);
      return;
    }
    setLoading(true);
    setErrors([]);
    const scanned = missions.slice(0, MAX_MISSIONS_SCANNED);
    const results = await Promise.all(
      scanned.map(async (m) => ({ m, r: await api.missionArtifacts(m.mission_id) })),
    );
    const out: ArtifactRow[] = [];
    const errs: string[] = [];
    for (const { m, r } of results) {
      if (!r.ok) {
        errs.push(`${m.title}: ${r.error ?? "artifacts unavailable"}`);
        continue;
      }
      for (const a of r.data ?? []) out.push({ artifact: a, missionTitle: m.title });
    }
    out.sort((x, y) => y.artifact.created_at - x.artifact.created_at);
    setRows(out);
    setErrors(errs);
    setLoading(false);
  }, [api, missions]);

  useEffect(() => {
    void load();
  }, [load]);

  const total = rows?.length ?? 0;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Deliverables</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {loading
                ? "Scanning missions…"
                : `${total} artifact${total === 1 ? "" : "s"} across the ${Math.min(missions.length, MAX_MISSIONS_SCANNED)} most recent missions`}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              onRefresh();
              void load();
            }}
          >
            Refresh
          </Button>
        </div>

        {missionsError && <InlineError message={`Missions unavailable — ${missionsError}`} />}
        {errors.length > 0 && (
          <InlineError
            message={`${errors.length} mission(s) could not be read: ${errors.slice(0, 3).join("; ")}${errors.length > 3 ? " …" : ""}`}
          />
        )}

        {missionsLoading || loading ? (
          <div className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : total === 0 && errors.length === 0 ? (
          <EmptyState
            icon={Boxes}
            title="No deliverables yet"
            detail="Artifacts appear here once a mission produces something and the artifact is registered with its provenance."
          />
        ) : (
          <div className="space-y-2">
            {rows?.map(({ artifact: a, missionTitle }) => {
              const Icon = KIND_ICON[a.kind] ?? Package;
              return (
                <div
                  key={a.artifact_id}
                  className="flex items-start gap-3 rounded-lg border border-border bg-card px-4 py-3"
                >
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground">{a.name}</span>
                      <Badge variant="outline">{a.kind}</Badge>
                      <Badge variant={toneVariant(artifactTone(a.status))}>{a.status}</Badge>
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      from <span className="text-foreground/80">{missionTitle}</span>
                      {a.sha256 ? (
                        <>
                          {" · "}
                          <code title={a.sha256} className="font-mono">
                            sha256:{a.sha256.slice(0, 12)}…
                          </code>
                        </>
                      ) : (
                        " · no hash recorded"
                      )}
                      {a.bytes > 0 ? ` · ${fmtBytes(a.bytes)}` : ""}
                    </div>
                    {a.uri && (
                      <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                        {a.uri}
                      </div>
                    )}
                  </div>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {fmtRelative(a.created_at)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
