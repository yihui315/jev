/**
 * DeliverablesView — artifacts produced by missions, with an honest lifecycle.
 *
 * Wave 2 listed artifacts client-side by fanning out over missions (capped at 50,
 * silently dropping the rest) and had no way to *accept* anything: every artifact
 * stayed PRODUCED forever, so "deliverable" and "raw output" were the same thing.
 *
 * Wave 5 fixes both. The view reads the SERVER-SIDE deliverable view (joined to
 * mission, filterable by status) so nothing is silently capped, and it exposes the
 * human act the product is really about:
 *
 *   PRODUCED  — the agent emitted it (a fact about the machine). Neutral.
 *   VERIFIED  — a human looked and accepted it (a fact about judgement). Green.
 *   REJECTED  — a human looked and refused it. Red.
 *   SUPERSEDED— replaced by a later artifact. Grey.
 *
 * Four states stay four states: an unverified artifact is never rendered as
 * accepted. Accept/Reject are disabled with a stated reason when there is no
 * control token, rather than shown as a dead green button.
 */
import { useCallback, useEffect, useState } from "react";
import { Boxes, CheckCircle2, FileCode2, FileText, Image as ImageIcon, Package, XCircle } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { EmptyState, InlineError, ScopeNote, Skeleton } from "../ui/Feedback";
import { artifactTone, toneVariant } from "../app/status";
import { truncationNoteFor } from "../app/truncation";
import {
  DOMAIN_LIST_LIMIT,
  fmtBytes,
  fmtRelative,
  type Deliverable,
  type DomainApi,
} from "../app/domainApi";

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

type StatusFilter = "all" | "PRODUCED" | "VERIFIED";

const FINAL = new Set(["VERIFIED", "REJECTED", "SUPERSEDED"]);

export function DeliverablesView({
  api,
  controlToken,
  totalDeliverables,
  onRefresh,
}: {
  api: DomainApi;
  controlToken: string;
  /**
   * Wave 9.3 — the UNCAPPED deliverable count from the domain totals read, or
   * `null` when that read failed. This view fetches its own bounded list; the
   * total is what tells it whether the list is the whole answer or only the
   * newest slice of it.
   */
  totalDeliverables: number | null;
  onRefresh: () => void;
}) {
  const [rows, setRows] = useState<Deliverable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await api.deliverables(
      filter === "all"
        ? { limit: DOMAIN_LIST_LIMIT }
        : { status: filter, limit: DOMAIN_LIST_LIMIT });
    if (r.ok && r.data) {
      setRows(r.data);
      setError(null);
    } else {
      setRows(null);
      setError(r.error || "deliverables unavailable");
    }
    setLoading(false);
  }, [api, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(a: Deliverable, status: "VERIFIED" | "REJECTED") {
    setBusyId(a.artifact_id);
    setNotice(null);
    const r = await api.verifyDeliverable(controlToken, a.artifact_id,
      { status, decided_by: "operator" });
    setBusyId(null);
    if (r.ok && r.data) {
      setNotice(`${status === "VERIFIED" ? "Accepted" : "Rejected"} “${a.name}”.`);
      onRefresh();
      void load();
    } else {
      setError(r.error || "the verdict could not be recorded");
    }
  }

  const known = rows !== null;
  const total = rows?.length ?? 0;
  // Only meaningful on the unfiltered view: with a status filter active,
  // `rows.length` counts that status, not the domain.
  const scopeNote = filter === "all"
    ? truncationNoteFor(total, totalDeliverables, DOMAIN_LIST_LIMIT,
                        { singular: "deliverable", plural: "deliverables" })
    : null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Deliverables</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {loading && !known
                ? "Loading…"
                : known
                  ? scopeNote
                    ? `${total} of ${(totalDeliverables ?? 0)} deliverables${filter === "all" ? "" : ` · ${filter.toLowerCase()}`}`
                    : `${total} deliverable${total === 1 ? "" : "s"}${filter === "all" ? "" : ` · ${filter.toLowerCase()}`}`
                  : "Count unavailable"}
            </p>
            <ScopeNote note={scopeNote} className="mt-2" />
          </div>
          <div className="flex items-center gap-2">
            <div className="inline-flex overflow-hidden rounded-md border border-border">
              {(["all", "PRODUCED", "VERIFIED"] as StatusFilter[]).map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`px-3 py-1 text-xs ${filter === f
                    ? "bg-primary text-primary-foreground"
                    : "bg-card text-muted-foreground hover:text-foreground"}`}
                >
                  {f === "all" ? "All" : f === "PRODUCED" ? "Awaiting review" : "Accepted"}
                </button>
              ))}
            </div>
            <Button variant="outline" size="sm" onClick={() => { onRefresh(); void load(); }}>
              Refresh
            </Button>
          </div>
        </div>

        {error && <InlineError message={error} />}
        {notice && (
          <div className="rounded-md border border-border bg-card/60 px-3 py-2 text-xs text-muted-foreground">
            {notice}
          </div>
        )}

        {loading && !known ? (
          <div className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : !known ? (
          <EmptyState
            icon={Boxes}
            title="Deliverables unavailable"
            detail="The domain API did not answer, so the deliverable list is UNKNOWN. It is not being shown as empty."
            action={<Button variant="outline" size="sm" onClick={() => void load()}>Retry</Button>}
          />
        ) : total === 0 ? (
          <EmptyState
            icon={Boxes}
            title={filter === "VERIFIED" ? "Nothing accepted yet" : "No deliverables yet"}
            detail="Artifacts appear here once a mission produces something and the artifact is registered with its provenance."
          />
        ) : (
          <div className="space-y-2">
            {rows?.map((a) => {
              const Icon = KIND_ICON[a.kind] ?? Package;
              const isFinal = FINAL.has(a.status);
              const noToken = !controlToken;
              return (
                <div
                  key={a.artifact_id}
                  className="flex items-start gap-3 rounded-lg border border-border bg-card px-4 py-3"
                  data-testid={`deliverable-${a.artifact_id}`}
                >
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground">{a.name}</span>
                      <Badge variant="outline">{a.kind}</Badge>
                      <Badge variant={toneVariant(artifactTone(a.status))}>{a.status}</Badge>
                      {a.status === "PRODUCED" && (
                        <span className="text-[10px] text-muted-foreground">
                          awaiting a human verdict
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      from <span className="text-foreground/80">{a.mission_title || "unknown mission"}</span>
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
                      {typeof a.provenance?.verified_by === "string"
                        ? ` · accepted by ${a.provenance.verified_by}` : ""}
                    </div>
                    {a.uri && (
                      <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                        {a.uri}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2">
                    <span className="text-[10px] text-muted-foreground">
                      {fmtRelative(a.created_at)}
                    </span>
                    {!isFinal && (
                      <div className="flex items-center gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={noToken || busyId === a.artifact_id}
                          title={noToken ? "A control token is required to record a verdict" : "Accept this deliverable"}
                          onClick={() => void decide(a, "VERIFIED")}
                        >
                          <CheckCircle2 className="mr-1 h-3 w-3" /> Accept
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={noToken || busyId === a.artifact_id}
                          title={noToken ? "A control token is required to record a verdict" : "Reject this deliverable"}
                          onClick={() => void decide(a, "REJECTED")}
                        >
                          <XCircle className="mr-1 h-3 w-3" /> Reject
                        </Button>
                      </div>
                    )}
                    {!isFinal && noToken && (
                      <span className="text-[10px] text-muted-foreground">
                        add a control token to accept
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div className="flex items-center gap-2 rounded-md border border-border bg-card/50 px-3 py-2 text-[11px] text-muted-foreground">
          <Boxes className="h-3.5 w-3.5" />
          PRODUCED means the agent emitted it; VERIFIED means a human accepted it.
          A verdict is recorded with who decided and is final.
        </div>
      </div>
    </div>
  );
}
