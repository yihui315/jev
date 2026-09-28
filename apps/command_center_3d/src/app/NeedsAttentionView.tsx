/**
 * NeedsAttentionView — the unified inbox (Wave 2 surface; Wave 4 deepens the
 * control actions).
 *
 * The list is exactly what the server reports as NEEDS_ATTENTION. Each row
 * carries the reason, so a human can act without opening three other screens.
 *
 * Wave 2 is deliberately read-only here: resolving an item requires either a
 * mission transition or an approval decision, both of which belong to Wave 4's
 * preview-first control flow. A button that appeared to "fix" an item but only
 * changed local state would be a lie, so there is no such button.
 */
import { AlertTriangle, CheckCircle2, Inbox } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { attentionLabel, attentionTone, toneVariant } from "../app/status";
import { fmtRelative, type AttentionItem } from "../app/domainApi";

export function NeedsAttentionView({
  items,
  loading,
  error,
  onRefresh,
  onGotoMissions,
}: {
  items: AttentionItem[] | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onGotoMissions: () => void;
}) {
  const known = items !== null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Needs Attention</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {loading && !known
                ? "Loading…"
                : known
                  ? `${items.length} item${items.length === 1 ? "" : "s"} waiting on a human`
                  : "Count unavailable"}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={onRefresh}>
            Refresh
          </Button>
        </div>

        {error && <InlineError message={error} />}

        {loading && !known ? (
          <div className="space-y-2">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : !known ? (
          <EmptyState
            icon={AlertTriangle}
            title="Inbox unavailable"
            detail="The domain API did not answer, so the inbox state is UNKNOWN. It is not being shown as empty."
            action={
              <Button variant="outline" size="sm" onClick={onRefresh}>
                Retry
              </Button>
            }
          />
        ) : items.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="Inbox zero"
            detail="No mission is blocked, escalated, stuck, over budget or awaiting an approval."
          />
        ) : (
          <div className="space-y-2">
            {items.map((it) => (
              <div
                key={it.mission_id}
                className="rounded-lg border border-border bg-card p-4"
                data-testid={`attention-${it.mission_id}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={toneVariant(attentionTone(it.kind ?? ""))}>
                    {attentionLabel(it.kind ?? "UNKNOWN")}
                  </Badge>
                  <span className="truncate text-sm font-medium text-foreground">{it.title}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                    {fmtRelative(it.updated_at)}
                  </span>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  {it.reason || "No reason recorded by the server."}
                </p>
                <div className="mt-2 flex items-center gap-3 text-[10px] text-muted-foreground">
                  <span className="font-mono">{it.mission_id}</span>
                  <span className="font-mono">{it.project_id}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2 rounded-md border border-border bg-card/50 px-3 py-2 text-[11px] text-muted-foreground">
          <Inbox className="h-3.5 w-3.5" />
          Resolving an item requires a mission transition or an approval decision —
          the preview-first control flow arrives in Wave 4. Nothing on this screen
          mutates state.
          <Button variant="ghost" size="sm" className="ml-auto" onClick={onGotoMissions}>
            Open missions
          </Button>
        </div>
      </div>
    </div>
  );
}
