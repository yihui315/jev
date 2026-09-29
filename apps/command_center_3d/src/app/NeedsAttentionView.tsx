/**
 * NeedsAttentionView — the unified inbox, as a PREVIEW-FIRST control surface.
 *
 * Wave 2 shipped a read-only inbox and said so out loud. Wave 4 makes it
 * actionable without ever lying about what an action will do. Two rules govern
 * every control here:
 *
 *  1. **POLICY PERMITTED and CURRENTLY EXECUTABLE are shown SEPARATELY.** An
 *     action can be allowed by policy yet not work on this host right now (e.g.
 *     "resume" is a legal action, but no live session exists to resume). Merging
 *     them into one green button is the defect this screen exists to avoid.
 *  2. **A click NEVER mutates.** Clicking an action opens a preview that shows
 *     exactly what the server says would change (a `dry_run` request). The
 *     confirm button is a SECOND, deliberate action.
 *
 * `UNKNOWN` (the prerequisite could not be determined) renders neutral — never
 * green. "We could not check" is not "it is fine".
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, CheckCircle2, Inbox, Loader2, ShieldCheck, ShieldQuestion,
} from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../ui/Dialog";
import { attentionLabel, attentionTone, toneVariant } from "../app/status";
import {
  fmtRelative,
  type ApiResult,
  type AttentionItem,
  type AttentionResolution,
  type AttentionResolutionsDoc,
  type DomainApi,
  type ResolveResult,
} from "../app/domainApi";

/** Copy for the closed blocked-reason vocabulary (never free text). */
function blockedCopy(reason: string | null | undefined): string {
  switch (reason) {
    case "NO_LIVE_SESSION": return "no live session to act on";
    case "NO_PENDING_APPROVAL": return "no pending approval to decide";
    case "CONNECTION_NOT_CONTROL_VERIFIED": return "connection is not control-verified";
    case "CONNECTION_UNKNOWN": return "connection state unknown";
    case "ACTUATION_DISABLED": return "actuation is disabled in this build";
    case "ALREADY_RESOLVED": return "already resolved";
    case "NOT_IN_ATTENTION": return "no longer needs attention";
    default: return reason ? reason.toLowerCase().replace(/_/g, " ") : "";
  }
}

function ExecutabilityChip({ a }: { a: AttentionResolution["actions"][number] }) {
  if (a.executability === "EXECUTABLE") {
    return (
      <Badge variant="ok" title="The prerequisites hold on this host right now">
        <ShieldCheck className="mr-1 h-3 w-3" /> executable
      </Badge>
    );
  }
  if (a.executability === "UNKNOWN") {
    // Neutral, never green: the prerequisite could not be determined.
    return (
      <Badge variant="unknown" title="The prerequisite could not be determined">
        <ShieldQuestion className="mr-1 h-3 w-3" /> unknown
      </Badge>
    );
  }
  return (
    <Badge variant="unknown" title={a.detail || "blocked"}>
      blocked: {blockedCopy(a.blocked_reason)}
    </Badge>
  );
}

export function NeedsAttentionView({
  api,
  controlToken,
  items,
  loading,
  error,
  onRefresh,
  onGotoMissions,
}: {
  api: DomainApi;
  controlToken: string;
  items: AttentionItem[] | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onGotoMissions: () => void;
}) {
  const known = items !== null;
  const [res, setRes] = useState<AttentionResolutionsDoc | null>(null);
  const [resError, setResError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ mid: string; action: string } | null>(null);
  const [previewDoc, setPreviewDoc] = useState<ResolveResult | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const loadResolutions = useCallback(async () => {
    const r: ApiResult<AttentionResolutionsDoc> = await api.attentionResolutions();
    if (r.ok && r.data) {
      setRes(r.data);
      setResError(null);
    } else {
      setRes(null);
      setResError(r.error || "resolution unavailable");
    }
  }, [api]);

  useEffect(() => { void loadResolutions(); }, [loadResolutions]);

  const byId = useMemo(() => {
    const m = new Map<string, AttentionResolution>();
    for (const it of res?.items ?? []) m.set(it.mission_id, it);
    return m;
  }, [res]);

  const refreshAll = useCallback(() => {
    onRefresh();
    void loadResolutions();
  }, [onRefresh, loadResolutions]);

  async function openPreview(mid: string, action: string) {
    setPreview({ mid, action });
    setPreviewDoc(null);
    setPreviewErr(null);
    setBusy(true);
    const r = await api.resolveAttention(controlToken, mid, { action, dry_run: true });
    setBusy(false);
    if (r.ok && r.data) setPreviewDoc(r.data);
    else setPreviewErr(r.error || "preview failed");
  }

  async function applyPreview() {
    if (!preview) return;
    setBusy(true);
    const body: Record<string, unknown> = { action: preview.action, dry_run: false };
    if (preview.action === "decide_approval") {
      body.state = "APPROVED";
      body.decided_by = "operator";
    }
    const r = await api.resolveAttention(controlToken, preview.mid,
      body as { action: string; dry_run: boolean });
    setBusy(false);
    if (r.ok && r.data) {
      if (r.data.applied) {
        setNotice(`Applied “${preview.action}”. The item was resolved.`);
        setPreview(null);
        refreshAll();
      } else {
        setPreviewErr(r.data.detail || r.data.blocked_reason
          || "The server refused to apply this action.");
        setPreviewDoc(r.data);
      }
    } else {
      setPreviewErr(r.error || "apply failed");
    }
  }

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
          <Button variant="outline" size="sm" onClick={refreshAll}>
            Refresh
          </Button>
        </div>

        {error && <InlineError message={error} />}
        {resError && (
          <InlineError message={`Control resolution unavailable (${resError}). Actions are hidden rather than guessed.`} />
        )}
        {notice && (
          <div className="rounded-md border border-border bg-card/60 px-3 py-2 text-xs text-muted-foreground">
            {notice}
          </div>
        )}

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
            action={<Button variant="outline" size="sm" onClick={refreshAll}>Retry</Button>}
          />
        ) : items.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="Inbox zero"
            detail="No mission is blocked, escalated, stuck, over budget or awaiting an approval."
          />
        ) : (
          <div className="space-y-2">
            {items.map((it) => {
              const r = byId.get(it.mission_id);
              return (
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

                  {r && r.actions.length > 0 ? (
                    <div className="mt-3 space-y-2 rounded-md border border-border bg-background/40 p-2">
                      {r.actions.map((a) => (
                        <div key={a.action} className="flex flex-wrap items-center gap-2">
                          <Badge variant="info" title="Allowed by the deterministic policy">
                            <ShieldCheck className="mr-1 h-3 w-3" /> policy permitted
                          </Badge>
                          <ExecutabilityChip a={a} />
                          {a.risk === "high" && (
                            <Badge variant="warn" title="This re-enters work">
                              high risk
                            </Badge>
                          )}
                          <Button
                            variant="outline"
                            size="sm"
                            className="ml-auto"
                            disabled={busy}
                            onClick={() => void openPreview(it.mission_id, a.action)}
                          >
                            {a.executability === "EXECUTABLE" ? "Preview & resolve" : "Preview"}
                          </Button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-3 text-[11px] text-muted-foreground">
                      No control action is proposed for this item.
                    </p>
                  )}

                  <div className="mt-2 flex items-center gap-3 text-[10px] text-muted-foreground">
                    <span className="font-mono">{it.mission_id}</span>
                    <span className="font-mono">{it.project_id}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div className="flex items-center gap-2 rounded-md border border-border bg-card/50 px-3 py-2 text-[11px] text-muted-foreground">
          <Inbox className="h-3.5 w-3.5" />
          Resolving an item is preview-first: nothing changes until you confirm in
          the preview. Policy-permitted and currently-executable are shown separately.
          <Button variant="ghost" size="sm" className="ml-auto" onClick={onGotoMissions}>
            Open missions
          </Button>
        </div>
      </div>

      <Dialog open={preview !== null} onOpenChange={(o) => { if (!o) setPreview(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Preview resolution</DialogTitle>
            <p className="text-xs text-muted-foreground">
              This is what the server says it WOULD change. Nothing has changed yet.
            </p>
          </DialogHeader>

          {busy && !previewDoc ? (
            <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Previewing…
            </div>
          ) : previewErr ? (
            <InlineError message={previewErr} />
          ) : previewDoc ? (
            <div className="space-y-3 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-muted-foreground">action</span>
                <span className="font-mono">{previewDoc.action}</span>
                {previewDoc.executability && (
                  <Badge variant={previewDoc.executability === "EXECUTABLE" ? "ok" : "unknown"}>
                    {previewDoc.executability.toLowerCase()}
                  </Badge>
                )}
              </div>
              {previewDoc.plan && previewDoc.plan.length > 0 ? (
                <ul className="space-y-1">
                  {previewDoc.plan.map((p, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-muted-foreground" />
                      <span>
                        <span className="font-mono text-[10px] text-muted-foreground">{p.target}</span>
                        {p.id ? <span className="ml-1 font-mono text-[10px]">{p.id}</span> : null}
                        {p.ids && p.ids.length > 0 ? (
                          <span className="ml-1 font-mono text-[10px]">{p.ids.join(", ")}</span>
                        ) : null}
                        <br />
                        {p.change}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground">
                  {previewDoc.detail || "The server reported no plan for this action."}
                </p>
              )}
              {previewDoc.detail ? (
                <p className="text-[11px] text-muted-foreground">{previewDoc.detail}</p>
              ) : null}
            </div>
          ) : null}

          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setPreview(null)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || !previewDoc || previewErr !== null
                || previewDoc.executability !== "EXECUTABLE"}
              onClick={() => void applyPreview()}
            >
              {preview?.action === "resume" ? "Confirm — re-enter work" : "Confirm & apply"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
