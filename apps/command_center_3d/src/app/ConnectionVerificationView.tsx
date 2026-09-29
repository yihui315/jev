/**
 * ConnectionVerificationView — Wave 7: is JCC actually able to work with this agent?
 *
 * The v1.2.1 AI CONNECTION CENTER (rendered below this panel) answers "what does
 * the agent-report think?"; it is built on the legacy `/api/connections` surface.
 * This panel is the v1.3 DOMAIN view, and it answers a different question:
 *
 *     For each domain connection, what does the SERVER say it can do —
 *     and HOW did the server come to believe it?
 *
 * Two things the product refused to fake, and this surface makes visible:
 *
 *  1. **MEASURED vs ASSERTED.** A capability recorded by a probe carries an
 *     `evidence_ref` (a digest of the observation) and renders as `measured`.
 *     One typed in by a human renders as `asserted`. They are never drawn the
 *     same, and a chip is never green without MEASURED provenance.
 *
 *  2. **The honest ceiling.** v1.3 keeps actuation OFF, so a probe NEVER returns
 *     `CONTROL_VERIFIED` — the most it can honestly observe is
 *     `ADAPTER_CONTRACT_LIVE` ("we can see the agent and speak its contract").
 *     The panel states that ceiling rather than showing a green "verified" badge
 *     the product cannot back up.
 *
 * The one action here is READ-ONLY against the host: "Verify now" runs a real
 * probe server-side and writes down what it saw. It is token-gated, and without
 * a token the button is disabled WITH a stated reason — never a dead green
 * control.
 */
import { useCallback, useEffect, useState } from "react";
import { Plug, RefreshCw, ShieldCheck } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { capabilityChipTone, provenanceLabel, toneVariant } from "../app/status";
import {
  fmtRelative,
  type ConnectionVerification,
  type DomainApi,
  type DomainConnection,
} from "../app/domainApi";

export function ConnectionVerificationView({
  api,
  controlToken,
  onRefresh,
}: {
  api: DomainApi;
  controlToken: string;
  onRefresh?: () => void;
}) {
  const [rows, setRows] = useState<DomainConnection[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [observed, setObserved] = useState<Record<string, ConnectionVerification>>({});

  const load = useCallback(async () => {
    setLoading(true);
    const r = await api.connections();
    if (r.ok && r.data) {
      setRows(r.data);
      setError(null);
    } else {
      setRows(null);
      setError(r.error || "connections unavailable");
    }
    setLoading(false);
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  async function verify(c: DomainConnection) {
    setBusy(c.connection_id);
    setError(null);
    const r = await api.verifyConnection(controlToken, c.connection_id, { probe: "auto" });
    setBusy(null);
    if (r.ok && r.data) {
      setObserved((prev) => ({ ...prev, [c.connection_id]: r.data as ConnectionVerification }));
      onRefresh?.();
      void load();
    } else {
      setError(r.error || "the probe could not be run");
    }
  }

  const known = rows !== null;
  const count = rows?.length ?? 0;

  return (
    <div className="mx-auto max-w-5xl space-y-3 p-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-foreground">
            <Plug className="h-4 w-4 text-primary" /> Connections — measured capability
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {loading && !known
              ? "Loading…"
              : known
                ? `${count} domain connection${count === 1 ? "" : "s"} · capability is MEASURED by a probe, never assumed`
                : "Connection state unavailable"}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          <RefreshCw className="mr-1 h-3 w-3" /> Refresh
        </Button>
      </div>

      {error && <InlineError message={error} />}

      {loading && !known ? (
        <Skeleton className="h-16 w-full" />
      ) : !known ? (
        <EmptyState
          icon={Plug}
          title="Connections unavailable"
          detail="The domain API did not answer, so connection capability is UNKNOWN. It is not being shown as empty."
          action={<Button variant="outline" size="sm" onClick={() => void load()}>Retry</Button>}
        />
      ) : count === 0 ? (
        <EmptyState
          icon={Plug}
          title="No domain connections yet"
          detail="Connections appear here once an agent project registers one. Nothing is pre-verified."
        />
      ) : (
        <div className="space-y-2">
          {rows?.map((c) => {
            const obs = observed[c.connection_id];
            const noToken = !controlToken;
            return (
              <div
                key={c.connection_id}
                data-testid={`connection-${c.connection_id}`}
                className="rounded-lg border border-border bg-card px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{c.agent}</span>
                  <Badge variant={toneVariant(capabilityChipTone("UNKNOWN", "ASSERTED"))}>
                    {c.health}
                  </Badge>
                  {c.control_verified ? (
                    <Badge variant="ok">control verified</Badge>
                  ) : (
                    <span className="text-[10px] text-muted-foreground">
                      no verified control path — effectful action stays blocked
                    </span>
                  )}
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {c.last_probe_at ? `probed ${fmtRelative(c.last_probe_at)}` : "never probed"}
                  </span>
                </div>

                {c.capabilities.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {c.capabilities.map((cap) => {
                      const prov = cap.provenance ?? "ASSERTED";
                      return (
                        <Badge
                          key={cap.capability_id}
                          variant={toneVariant(capabilityChipTone(cap.kind, prov))}
                          title={cap.detail || cap.kind}
                          data-testid={`cap-${c.connection_id}-${cap.kind}`}
                        >
                          {cap.kind}
                          <span className="opacity-70">· {provenanceLabel(prov)}</span>
                          {cap.evidence_ref ? (
                            <code className="ml-1 font-mono text-[9px] opacity-70">
                              {cap.evidence_ref.slice(0, 14)}…
                            </code>
                          ) : null}
                        </Badge>
                      );
                    })}
                  </div>
                ) : (
                  <div className="mt-2 text-[11px] text-muted-foreground">
                    No capability recorded. Run a probe to measure one — nothing is assumed.
                  </div>
                )}

                {obs && (
                  <div className="mt-2 rounded-md border border-border bg-background/40 px-3 py-2 text-[11px] text-muted-foreground">
                    <div className="flex flex-wrap items-center gap-2">
                      <ShieldCheck className="h-3.5 w-3.5" />
                      <span className="text-foreground/80">observed</span>
                      <Badge variant={toneVariant(capabilityChipTone(obs.observed.kind, "MEASURED"))}>
                        {obs.observed.kind}
                      </Badge>
                      {obs.observed.reason ? <span>reason: {obs.observed.reason}</span> : null}
                      <span>· health {obs.health}</span>
                    </div>
                    <div className="mt-1">{obs.observed.detail}</div>
                    {obs.observed.evidence_ref ? (
                      <div className="mt-0.5 font-mono text-[10px]">
                        evidence {obs.observed.evidence_ref.slice(0, 24)}…
                      </div>
                    ) : null}
                  </div>
                )}

                <div className="mt-2 flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={noToken || busy === c.connection_id}
                    title={noToken
                      ? "A control token is required to run a verification probe"
                      : "Run a real read-only probe and record what it observes"}
                    onClick={() => void verify(c)}
                  >
                    <ShieldCheck className="mr-1 h-3 w-3" />
                    {busy === c.connection_id ? "Probing…" : "Verify now"}
                  </Button>
                  {noToken && (
                    <span className="text-[10px] text-muted-foreground">
                      add a control token to verify
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-start gap-2 rounded-md border border-border bg-card/50 px-3 py-2 text-[11px] text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          A capability is <b>measured</b>, not asserted. Verification runs a real
          read-only probe and records only what it observes. Because actuation is
          disabled in this build, a probe can never claim a control path — the
          honest ceiling is <code>ADAPTER_CONTRACT_LIVE</code>. Seeing an agent is
          not controlling it.
        </span>
      </div>
    </div>
  );
}
