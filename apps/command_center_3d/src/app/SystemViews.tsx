/**
 * SystemViews — Audit / Diagnostics / Settings.
 *
 * These are the "prove it" surfaces. Each one renders the raw fact it claims
 * from (HTTP status, gate result, token presence) rather than a summary badge
 * that could outlive the fact.
 */
import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, ScrollText, Settings2, Stethoscope, XCircle } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/Card";
import { EmptyState, InlineError, Skeleton } from "../ui/Feedback";
import { Label, Select } from "../ui/Field";
import { cn } from "../ui/cn";
import { fmtRelative, type DomainApi } from "../app/domainApi";
import type { SceneState } from "../store/sceneMap";

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

interface AuditRow {
  directive_id?: string;
  action?: string;
  session_id?: string;
  acted?: boolean;
  signed?: boolean;
  created_at?: number;
  [k: string]: unknown;
}

export function AuditView({ base }: { base: string }) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [verify, setVerify] = useState<{ ok: boolean; body: unknown } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`${base}/api/audit`);
      if (!r.ok) {
        setErr(`GET /api/audit → HTTP ${r.status}`);
        setRows(null);
        return;
      }
      const body = await r.json();
      setRows(Array.isArray(body) ? (body as AuditRow[]) : ((body as { audit?: AuditRow[] }).audit ?? []));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setRows(null);
    } finally {
      setBusy(false);
    }
  }, [base]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runVerify() {
    setBusy(true);
    try {
      const r = await fetch(`${base}/api/audit/verify`);
      setVerify({ ok: r.ok, body: await r.json().catch(() => null) });
    } catch (e) {
      setVerify({ ok: false, body: { error: e instanceof Error ? e.message : String(e) } });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Audit Tape</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Append-only record of every directive the policy layer emitted
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => void runVerify()} disabled={busy}>
              Verify chain
            </Button>
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={busy}>
              <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
              Refresh
            </Button>
          </div>
        </div>

        {verify && (
          <div
            className={cn(
              "flex items-start gap-2 rounded-md border px-3 py-2 text-xs",
              verify.ok
                ? "border-status-ok/40 bg-status-ok/10 text-status-ok"
                : "border-status-danger/40 bg-status-danger/10 text-status-danger",
            )}
          >
            {verify.ok ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            ) : (
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            )}
            <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[11px]">
              {JSON.stringify(verify.body, null, 2)}
            </pre>
          </div>
        )}

        {err && <InlineError message={err} />}

        {rows === null && !err ? (
          <Skeleton className="h-32 w-full" />
        ) : rows && rows.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="Audit tape is empty"
            detail="No directive has been emitted yet. An empty tape is the honest state before any work runs."
          />
        ) : rows ? (
          <Card>
            <CardContent className="pt-4">
              <ul className="divide-y divide-border">
                {rows.map((r, i) => (
                  <li key={String(r.directive_id ?? i)} className="flex items-center gap-2 py-2 text-xs">
                    <Badge variant={r.acted ? "warn" : "unknown"}>
                      {r.acted ? "acted" : "inert"}
                    </Badge>
                    <Badge variant={r.signed ? "ok" : "outline"}>
                      {r.signed ? "signed" : "unsigned"}
                    </Badge>
                    <span className="font-mono text-foreground/90">{String(r.action ?? "?")}</span>
                    <span className="truncate font-mono text-[10px] text-muted-foreground">
                      {String(r.session_id ?? "")}
                    </span>
                    <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                      {r.created_at ? fmtRelative(Number(r.created_at)) : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

interface ProbeResult {
  label: string;
  path: string;
  status: number | null;
  ok: boolean;
  ms: number;
  detail: string;
}

export function DiagnosticsView({ base, api, scene }: { base: string; api: DomainApi; scene: SceneState }) {
  const [probes, setProbes] = useState<ProbeResult[] | null>(null);
  const [running, setRunning] = useState(false);

  const run = useCallback(async () => {
    setRunning(true);
    const endpoints: { label: string; path: string }[] = [
      { label: "Health", path: "/api/health" },
      { label: "Scene projection", path: "/api/v1/scene" },
      { label: "Domain home", path: "/api/v1/domain/home" },
      { label: "Domain projects", path: "/api/v1/domain/projects" },
      { label: "Domain missions", path: "/api/v1/domain/missions" },
      { label: "Domain connections", path: "/api/v1/domain/connections" },
      { label: "Domain needs-attention", path: "/api/v1/domain/needs-attention" },
      { label: "Audit", path: "/api/audit" },
      { label: "Audit verify", path: "/api/audit/verify" },
    ];
    const out: ProbeResult[] = [];
    for (const e of endpoints) {
      const t0 = performance.now();
      try {
        const r = await fetch(base + e.path);
        const ms = performance.now() - t0;
        let detail = `HTTP ${r.status}`;
        try {
          const t = await r.text();
          if (t.length > 0) detail = `HTTP ${r.status} · ${t.length} bytes`;
        } catch {
          /* body unreadable */
        }
        out.push({ label: e.label, path: e.path, status: r.status, ok: r.ok, ms, detail });
      } catch (err) {
        out.push({
          label: e.label,
          path: e.path,
          status: null,
          ok: false,
          ms: performance.now() - t0,
          detail: err instanceof Error ? err.message : String(err),
        });
      }
    }
    setProbes(out);
    setRunning(false);
  }, [base]);

  useEffect(() => {
    void run();
  }, [run]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Diagnostics</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Live endpoint probes against {base}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void run()} disabled={running}>
            <RefreshCw className={cn("h-3.5 w-3.5", running && "animate-spin")} />
            Re-run probes
          </Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Stethoscope className="h-4 w-4 text-status-info" />
              Endpoint health
            </CardTitle>
            <CardDescription>
              Each row is a real request. A failure is shown as a failure — never hidden.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {probes === null ? (
              <Skeleton className="h-24 w-full" />
            ) : (
              <ul className="divide-y divide-border">
                {probes.map((p) => (
                  <li key={p.path} className="flex items-center gap-3 py-2 text-xs">
                    {p.ok ? (
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-status-ok" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5 shrink-0 text-status-danger" />
                    )}
                    <span className="w-44 shrink-0 truncate text-foreground">{p.label}</span>
                    <code className="truncate font-mono text-[10px] text-muted-foreground">
                      {p.path}
                    </code>
                    <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                      {p.detail} · {p.ms.toFixed(0)}ms
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Runtime facts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <Fact label="API base" value={base} mono />
            <Fact label="Transport" value={scene.connection} />
            <Fact label="WebGL2" value={scene.webgl2Supported ? "available" : "unavailable (table fallback active)"} />
            <Fact label="Scene schema" value={scene.schemaVersion ?? "not loaded"} mono />
            <Fact label="Provider" value={scene.core?.providerName ?? "UNKNOWN"} />
            <Fact label="Policy version" value={scene.core?.policyVersion ?? "UNKNOWN"} mono />
            <Fact
              label="Audit chain"
              value={
                scene.auditChainIntact === null
                  ? "UNKNOWN"
                  : scene.auditChainIntact
                    ? "INTACT"
                    : "BROKEN"
              }
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/60 pb-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("truncate text-foreground", mono && "font-mono text-[11px]")}>{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Settings
//
// MOVED to `./app/SettingsView.tsx`. The settings surface was rebuilt as a
// seven-section page (通用 / 执行代理 / 模型 / 连接 / 安全 / 系统 / 高级) so
// that a normal user finds what they need without reading a token field, while
// every professional control stayed reachable one section deeper. Re-exported
// here so existing imports keep resolving.
// ---------------------------------------------------------------------------

export { SettingsView } from "./SettingsView";

