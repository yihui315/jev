/**
 * HomeView — the product first screen (Wave 2).
 *
 * Five counters + the Needs Attention list, all read from
 * `GET /api/v1/domain/home`. Nothing on this screen is computed client-side:
 * the server owns the counts, and the UI renders "—" when a value is not
 * available rather than inventing a zero.
 *
 * The primary action is New Mission, because launching work is the product's
 * reason to exist.
 */
import {
  Activity,
  AlertTriangle,
  Boxes,
  CheckCircle2,
  FolderKanban,
  PlayCircle,
  Plus,
  RefreshCw,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../ui/Card";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { cn } from "../ui/cn";
import { attentionLabel, attentionTone, toneVariant } from "../app/status";
import { fmtRelative, type AttentionItem, type HomeSummary } from "../app/domainApi";
import type { SceneState } from "../store/sceneMap";

export interface HomeData {
  summary: HomeSummary | null;
  error: string | null;
  loading: boolean;
  lastLoadedAt: number | null;
}

export function HomeView({
  data,
  scene,
  onNewMission,
  onGotoMissions,
  onGotoAttention,
  onGotoDeliverables,
  onGotoProjects,
  onRefresh,
}: {
  data: HomeData;
  scene: SceneState;
  onNewMission: () => void;
  onGotoMissions: () => void;
  onGotoAttention: () => void;
  onGotoDeliverables: () => void;
  onGotoProjects: () => void;
  onRefresh: () => void;
}) {
  const s = data.summary;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-5 p-5">
        {/* ---- Hero / primary CTA ---- */}
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">
              Mission Control
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {s
                ? `${s.active_projects} project${s.active_projects === 1 ? "" : "s"} · ${s.running_missions} running · ${s.needs_attention} needing attention`
                : "Loading workspace state…"}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onRefresh}>
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </Button>
            <Button size="sm" onClick={onNewMission} data-testid="home-new-mission">
              <Plus className="h-3.5 w-3.5" />
              New Mission
            </Button>
          </div>
        </div>

        {data.error && (
          <InlineError message={`Home summary unavailable — ${data.error}`} />
        )}

        {/* ---- Five counters ---- */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <StatCard
            icon={FolderKanban}
            label="Active Projects"
            value={s?.active_projects}
            loading={data.loading}
            onClick={onGotoProjects}
            testId="stat-active-projects"
          />
          <StatCard
            icon={PlayCircle}
            label="Running Missions"
            value={s?.running_missions}
            loading={data.loading}
            tone="live"
            onClick={onGotoMissions}
            testId="stat-running-missions"
          />
          <StatCard
            icon={AlertTriangle}
            label="Needs Attention"
            value={s?.needs_attention}
            loading={data.loading}
            tone={(s?.needs_attention ?? 0) > 0 ? "warn" : undefined}
            onClick={onGotoAttention}
            testId="stat-needs-attention"
          />
          <StatCard
            icon={Boxes}
            label="Accepted deliverables"
            value={s?.deliverables?.verified}
            loading={data.loading}
            onClick={onGotoDeliverables}
            testId="stat-deliverables"
            hint={
              s && s.deliverables
                ? s.deliverables.produced > 0
                  ? `${s.deliverables.produced} awaiting review`
                  : "all reviewed"
                : undefined
            }
          />
          <StatCard
            icon={CheckCircle2}
            label="Completed"
            value={s?.completed_missions}
            loading={data.loading}
            tone="ok"
            onClick={onGotoMissions}
            testId="stat-completed"
          />
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* ---- Needs attention ---- */}
          <Card className="lg:col-span-2">
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-status-warn" />
                  Needs Attention
                </CardTitle>
                <CardDescription>
                  Work the system has stopped on, waiting for a human decision
                </CardDescription>
              </div>
              <Button variant="ghost" size="sm" onClick={onGotoAttention}>
                Open inbox
              </Button>
            </CardHeader>
            <CardContent>
              {data.loading && !s ? (
                <div className="space-y-2">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : !s ? (
                <EmptyState
                  icon={AlertTriangle}
                  title="Attention list unavailable"
                  detail="The domain API did not return a home summary. Nothing is being assumed here — the count is unknown, not zero."
                  action={
                    <Button variant="outline" size="sm" onClick={onRefresh}>
                      Retry
                    </Button>
                  }
                />
              ) : s.attention_items.length === 0 ? (
                <EmptyState
                  icon={CheckCircle2}
                  title="Nothing needs you right now"
                  detail="No mission is blocked, escalated or awaiting an approval."
                />
              ) : (
                <ul className="divide-y divide-border">
                  {s.attention_items.map((it) => (
                    <AttentionRow key={it.mission_id} item={it} onOpen={onGotoAttention} />
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* ---- System health ---- */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-status-info" />
                System Health
              </CardTitle>
              <CardDescription>Live control-plane facts</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2.5 text-xs">
              <HealthRow
                label="Transport"
                value={scene.connection}
                variant={
                  scene.connection === "LIVE"
                    ? "live"
                    : scene.connection === "OFFLINE"
                      ? "danger"
                      : scene.connection === "POLLING"
                        ? "info"
                        : "warn"
                }
              />
              <HealthRow
                label="Provider"
                value={scene.core?.providerName ?? "UNKNOWN"}
                variant={scene.core ? "info" : "unknown"}
                hint={
                  scene.core
                    ? `${scene.core.providerMode}${scene.core.providerVerified ? " · verified" : ""}`
                    : "no scene loaded"
                }
              />
              <HealthRow
                label="Policy"
                value={scene.core?.policyVersion ?? "UNKNOWN"}
                variant={scene.core ? "info" : "unknown"}
              />
              <HealthRow
                label="Audit chain"
                value={
                  scene.auditChainIntact === null
                    ? "UNKNOWN"
                    : scene.auditChainIntact
                      ? "INTACT"
                      : "BROKEN"
                }
                variant={
                  scene.auditChainIntact === null
                    ? "unknown"
                    : scene.auditChainIntact
                      ? "ok"
                      : "danger"
                }
              />
              <HealthRow
                label="Actuation"
                value={s ? (s.actuation_enabled ? "ENABLED" : "DISABLED") : "UNKNOWN"}
                variant={s ? (s.actuation_enabled ? "danger" : "ok") : "unknown"}
                hint="fail-closed build"
              />
              <HealthRow
                label="Scope"
                value={s?.localhost_only ? "LOCALHOST" : "UNKNOWN"}
                variant={s?.localhost_only ? "info" : "unknown"}
              />
              <div className="pt-1 text-[10px] text-muted-foreground">
                Last loaded:{" "}
                {data.lastLoadedAt ? fmtRelative(Math.floor(data.lastLoadedAt / 1000)) : "—"}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  loading,
  tone,
  onClick,
  testId,
  hint,
}: {
  icon: LucideIcon;
  label: string;
  value: number | undefined;
  loading: boolean;
  tone?: NonNullable<React.ComponentProps<typeof Badge>["variant"]>;
  onClick: () => void;
  testId: string;
  hint?: string;
}) {
  const known = value !== undefined && value !== null;
  return (
    <button
      onClick={onClick}
      data-testid={testId}
      className={cn(
        "group rounded-lg border border-border bg-card p-4 text-left transition-colors",
        "hover:border-primary/50 hover:bg-accent/40",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <Icon className="h-4 w-4 text-muted-foreground group-hover:text-primary" />
      </div>
      <div className="mt-2">
        {loading && !known ? (
          <Skeleton className="h-7 w-12" />
        ) : known ? (
          <span
            className={cn(
              "text-2xl font-semibold tabular-nums",
              tone === "warn" ? "text-status-warn" : "text-foreground",
            )}
          >
            {value}
          </span>
        ) : (
          // Unknown is rendered as an explicit dash, never as a zero.
          <span className="text-2xl font-semibold text-status-unknown" title="Not available">
            —
          </span>
        )}
      </div>
      {hint && known ? (
        <div className="mt-0.5 text-[10px] text-muted-foreground">{hint}</div>
      ) : null}
    </button>
  );
}

function AttentionRow({ item, onOpen }: { item: AttentionItem; onOpen: () => void }) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <Badge variant={toneVariant(attentionTone(item.kind ?? ""))}>
        {attentionLabel(item.kind ?? "UNKNOWN")}
      </Badge>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-foreground">{item.title}</div>
        <div className="truncate text-[11px] text-muted-foreground">
          {item.reason || "no reason recorded"}
        </div>
      </div>
      <span className="shrink-0 text-[10px] text-muted-foreground">
        {fmtRelative(item.updated_at)}
      </span>
      <Button variant="ghost" size="sm" onClick={onOpen}>
        Review
      </Button>
    </li>
  );
}

function HealthRow({
  label,
  value,
  variant,
  hint,
}: {
  label: string;
  value: string;
  variant: NonNullable<React.ComponentProps<typeof Badge>["variant"]>;
  hint?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5">
        {hint ? <span className="text-[10px] text-muted-foreground">{hint}</span> : null}
        <Badge variant={variant}>{value}</Badge>
      </span>
    </div>
  );
}
