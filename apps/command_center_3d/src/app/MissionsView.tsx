/**
 * MissionsView — the mission queue with authoritative lifecycle state.
 *
 * Filtering happens client-side over the server's list; the server's
 * `status_filter` is offered too so a large workspace can narrow the response.
 * A mission detail drawer shows the SERVER's timeline (mission_events), which
 * is the only record of how a mission arrived at its current state.
 */
import { useEffect, useState } from "react";
import { ListChecks, Rocket } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/Card";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { cn } from "../ui/cn";
import { MISSION_STATUSES, missionTone, toneVariant } from "../app/status";
import {
  fmtRelative,
  fmtTime,
  type DomainApi,
  type Mission,
  type MissionWorkload,
  type TimelineEntry,
} from "../app/domainApi";

export function MissionsView({
  api,
  missions,
  loading,
  error,
  onNewMission,
  onRefresh,
}: {
  api: DomainApi;
  missions: Mission[];
  loading: boolean;
  error: string | null;
  onNewMission: () => void;
  onRefresh: () => void;
}) {
  const [filter, setFilter] = useState<string | null>(null);
  const [selected, setSelected] = useState<Mission | null>(null);

  const shown = filter ? missions.filter((m) => m.status === filter) : missions;
  const counts = MISSION_STATUSES.map((s) => ({
    s,
    n: missions.filter((m) => m.status === s).length,
  })).filter((c) => c.n > 0);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Missions</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {loading ? "Loading…" : `${missions.length} mission${missions.length === 1 ? "" : "s"} · state is authoritative in the core`}
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

        {/* Status filter chips, derived from what actually exists */}
        {counts.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            <FilterChip active={filter === null} onClick={() => setFilter(null)}>
              All · {missions.length}
            </FilterChip>
            {counts.map(({ s, n }) => (
              <FilterChip
                key={s}
                active={filter === s}
                tone={missionTone(s)}
                onClick={() => setFilter(filter === s ? null : s)}
              >
                {s} · {n}
              </FilterChip>
            ))}
          </div>
        )}

        {loading && missions.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : missions.length === 0 && !error ? (
          <EmptyState
            icon={ListChecks}
            title="No missions yet"
            detail="Launch your first mission from Mission Studio. Nothing is fabricated here — an empty queue stays empty."
            action={
              <Button size="sm" onClick={onNewMission}>
                Open Mission Studio
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-2">
            {shown.map((m) => (
              <button
                key={m.mission_id}
                onClick={() => setSelected(m)}
                className={cn(
                  "flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-left transition-colors",
                  selected?.mission_id === m.mission_id
                    ? "border-primary/60"
                    : "hover:border-primary/40 hover:bg-accent/40",
                )}
              >
                <Badge variant={toneVariant(missionTone(m.status))}>{m.status}</Badge>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">{m.title}</div>
                  <div className="truncate text-[11px] text-muted-foreground">
                    {m.agent} · {m.project_id}
                    {m.attention_kind ? ` · ${m.attention_kind}` : ""}
                  </div>
                </div>
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {fmtRelative(m.updated_at)}
                </span>
              </button>
            ))}
            {shown.length === 0 && (
              <EmptyState
                title={`No mission in ${filter}`}
                detail="Clear the filter to see the whole queue."
                action={
                  <Button variant="outline" size="sm" onClick={() => setFilter(null)}>
                    Clear filter
                  </Button>
                }
              />
            )}
          </div>
        )}
      </div>

      {selected && (
        <MissionDetailDrawer
          api={api}
          mission={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function FilterChip({
  active,
  tone,
  onClick,
  children,
}: {
  active: boolean;
  tone?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
        active ? "border-primary/60 bg-primary/15 text-primary" : "border-border text-muted-foreground hover:bg-accent",
      )}
    >
      {children}
    </button>
  );
}

function MissionDetailDrawer({
  api,
  mission,
  onClose,
}: {
  api: DomainApi;
  mission: Mission;
  onClose: () => void;
}) {
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Wave 3 — the REAL telemetry this mission's runs produced, from the session
  // store. Kept null until a successful read so a failure never renders as
  // "no workload", which would be a false claim that the mission is idle.
  const [workload, setWorkload] = useState<MissionWorkload | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const r = await api.missionTimeline(mission.mission_id);
      if (cancelled) return;
      if (!r.ok) {
        setErr(r.error ?? "timeline unavailable");
        return;
      }
      setTimeline(r.data ?? []);
    })();
    void (async () => {
      const w = await api.missionWorkload(mission.mission_id);
      if (cancelled) return;
      if (w.ok && w.data) setWorkload(w.data);
    })();
    return () => {
      cancelled = true;
    };
  }, [api, mission.mission_id]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto border-l border-border bg-card p-4"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`Mission ${mission.title}`}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-foreground">{mission.title}</h3>
            <div className="mt-1 flex items-center gap-2">
              <Badge variant={toneVariant(missionTone(mission.status))}>{mission.status}</Badge>
              <span className="font-mono text-[10px] text-muted-foreground">{mission.mission_id}</span>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>

        <dl className="mt-4 space-y-2 text-xs">
          <Row label="Goal" value={mission.goal || "—"} />
          <Row label="Agent" value={mission.agent} />
          <Row label="Project" value={mission.project_id} mono />
          <Row label="Working dir" value={mission.working_directory || "—"} mono />
          <Row label="Permissions" value={mission.permissions.length ? mission.permissions.join(", ") : "none requested"} />
          <Row label="Budget" value={mission.budget_units ? String(mission.budget_units) : "unbudgeted"} />
          <Row label="Session" value={mission.session_id ?? "not attached yet"} mono />
          <Row label="Created" value={fmtTime(mission.created_at)} />
        </dl>

        {mission.attention_reason && (
          <div className="mt-3 rounded-md border border-status-warn/40 bg-status-warn/10 px-3 py-2 text-xs text-status-warn">
            {mission.attention_kind}: {mission.attention_reason}
          </div>
        )}

        {/* Wave 3 — LIVE WORKLOAD. Derived from the engine's session store, so a
            mission that claims RUNNING with no telemetry shows zero live
            sessions here rather than a fabricated activity indicator. */}
        <Card className="mt-4" data-testid="mission-workload">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Live workload
              {workload && (
                <Badge variant={workload.live_session_count > 0 ? "live" : "unknown"}>
                  {workload.live_session_count > 0
                    ? `${workload.live_session_count} live session${workload.live_session_count > 1 ? "s" : ""}`
                    : "no live session"}
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {workload === null ? (
              <div className="text-xs text-muted-foreground">
                Workload unknown — the session store has not answered.
              </div>
            ) : (
              <dl className="space-y-2 text-xs">
                <Row label="Agent runs" value={String(workload.run_count)} />
                <Row
                  label="Sessions"
                  value={workload.session_ids.length ? workload.session_ids.join(", ") : "—"}
                  mono
                />
                <Row
                  label="Live telemetry"
                  value={
                    workload.live_session_count > 0
                      ? `${workload.live_session_count} session(s) with real AEP events`
                      : "none — no AEP events under this mission's sessions"
                  }
                />
                <Row
                  label="Actuation"
                  value={workload.actuation === false ? "OFF (fail-closed)" : "unknown"}
                />
              </dl>
            )}
          </CardContent>
        </Card>

        <Card className="mt-4">
          <CardHeader>
            <CardTitle>Timeline</CardTitle>
          </CardHeader>
          <CardContent>
            {err ? (
              <InlineError message={err} />
            ) : timeline === null ? (
              <Skeleton className="h-16 w-full" />
            ) : timeline.length === 0 ? (
              <div className="text-xs text-muted-foreground">No events recorded.</div>
            ) : (
              <ol className="space-y-2">
                {timeline.map((e) => (
                  <li key={e.seq} className="flex gap-2 text-xs">
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                    <div className="min-w-0">
                      <div className="text-foreground">
                        {e.from_status ? `${e.from_status} → ` : ""}
                        <span className="font-medium">{e.to_status}</span>
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        {fmtTime(e.timestamp)} · {e.reason || "no reason"}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/60 pb-1.5">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={cn("truncate text-right text-foreground", mono && "font-mono text-[11px]")}>
        {value}
      </dd>
    </div>
  );
}
