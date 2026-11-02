/**
 * MissionStudio — create a mission and (optionally) walk it to RUNNING.
 *
 * The form is a *request*. Every mutation goes through the token-gated domain
 * API, and the mission's authoritative state is whatever the server returns —
 * the UI never advances a status locally. If the launch handshake is refused,
 * the server's own refusal text is shown verbatim.
 *
 * Launch is preview-first: pressing Launch shows exactly which transitions will
 * be attempted, and only the explicit confirm issues them.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  FolderPlus,
  Rocket,
  Save,
  Send,
} from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../ui/Card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../ui/Dialog";
import { Input, Label, Select, Textarea } from "../ui/Field";
import { EmptyState, InlineError } from "../ui/Feedback";
import { cn } from "../ui/cn";
import { missionTone, toneVariant } from "../app/status";
import {
  AGENT_CHOICES,
  EMPTY_MISSION_FORM,
  LAUNCH_SEQUENCE,
  LAUNCHABLE_AGENTS,
  PERMISSION_CATALOG,
  formatPermissions,
  parsePermissions,
  validateMissionForm,
  type MissionFormValues,
} from "../app/missionForm";
import type {
  DomainApi,
  LaunchProbe,
  LaunchRequestResult,
  Mission,
  Project,
  ProjectRootRejection,
  ProjectScope,
} from "../app/domainApi";
import { projectRootRejectionHelp } from "../app/domainApi";

export interface StudioProps {
  api: DomainApi;
  controlToken: string;
  projects: Project[];
  loadingProjects: boolean;
  projectsError: string | null;
  onCreated: (m: Mission) => void;
  onOpenProjects: () => void;
  onCreateProject: (name: string, rootPath: string) => Promise<Project | null>;
  /** RISK-1 — the last refused project root, when the server refused the PATH
   * rather than the request. Null after a success or a non-path failure. */
  projectRejection?: ProjectRootRejection | null;
  /** RISK-1 — the server's own failure text for a non-path refusal. */
  projectError?: string | null;
}

export function MissionStudio(props: StudioProps) {
  const [v, setV] = useState<MissionFormValues>({ ...EMPTY_MISSION_FORM });
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Mission | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [launchPreview, setLaunchPreview] = useState<Mission | null>(null);
  const [launchLog, setLaunchLog] = useState<
    { step: string; ok: boolean; detail: string }[]
  >([]);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  // Wave 9.2 — the START path's own state, kept separate from the Wave-3 attach
  // path above so the two are never rendered as each other.
  const [startPreview, setStartPreview] = useState<LaunchRequestResult | null>(null);
  const [startResult, setStartResult] = useState<LaunchRequestResult | null>(null);
  const [starting, setStarting] = useState(false);

  // Wave 3 — the real launch probe. Refreshed when the launch preview opens so
  // the dialog shows whether a live agent session actually exists right now,
  // rather than a promise that the launch will work.
  const [probe, setProbe] = useState<LaunchProbe | null>(null);

  const refreshProbe = useCallback(async () => {
    const r = await props.api.launchProbe();
    setProbe(r.ok && r.data ? r.data : null);
  }, [props.api]);

  useEffect(() => {
    // The START panel renders a MEASURED capability, so the probe must load
    // BEFORE the operator can press anything -- not only once the Wave-3 launch
    // dialog opens. Loading it lazily would show "no capability" as if it were
    // "no capability exists", which is the UNKNOWN-rendered-as-fact defect.
    void refreshProbe();
  }, [refreshProbe]);

  useEffect(() => {
    if (launchPreview) void refreshProbe();
  }, [launchPreview, refreshProbe]);

  const { errors, payload } = useMemo(() => validateMissionForm(v), [v]);
  const showErrors = touched;
  const canSubmit = payload !== null && !busy && !!props.controlToken;

  const set = <K extends keyof MissionFormValues>(k: K, val: MissionFormValues[K]) =>
    setV((prev) => ({ ...prev, [k]: val }));

  async function submit() {
    setTouched(true);
    setServerError(null);
    if (!payload) return;
    if (!props.controlToken) {
      setServerError(
        "A control token is required to create a mission. Enter it in the top bar — it is held in memory only.",
      );
      return;
    }
    setBusy(true);
    const r = await props.api.createMission(props.controlToken, payload);
    setBusy(false);
    if (!r.ok || !r.data) {
      setServerError(r.error ?? "mission creation failed");
      return;
    }
    setResult(r.data);
    props.onCreated(r.data);
  }

  /**
   * Launch = ONE authenticated call to the mission runtime, which:
   *   1. discovers a REAL live WorkBuddy session on this host,
   *   2. walks DRAFT -> READY -> STARTING -> RUNNING (stopping at any refusal),
   *   3. creates the AgentRun, binds the Session, and ingests real AEP telemetry.
   *
   * The client does NOT build a launch event and does NOT walk the transitions
   * itself. Wave 3 moved that to the server so the state machine, the adapter
   * contract and the session binding are enforced in one place, and so a client
   * cannot manufacture a "successful" launch with no telemetry behind it.
   *
   * This method shows the server's own step log verbatim — including a refusal.
   */
  async function runLaunch(m: Mission) {
    setBusy(true);
    setLaunchLog([]);
    const log: { step: string; ok: boolean; detail: string }[] = [];
    const r = await props.api.launchMission(props.controlToken, m.mission_id, {
      capture: true,
      cwd: m.working_directory || undefined,
    });
    if (!r.ok || !r.data) {
      log.push({ step: "launch", ok: false, detail: r.error ?? "refused" });
      setLaunchLog(log);
      setBusy(false);
      return;
    }
    const res = r.data;
    for (const s of res.steps) {
      log.push({ step: s.step, ok: s.ok, detail: s.detail });
    }
    if (res.capture_source) {
      const lv = res.capture_source.liveness;
      log.unshift({
        step: "capture",
        ok: lv.state === "live",
        detail: `${res.capture_source.kind} · session ${res.capture_source.session_id} · ${lv.reason}`,
      });
    }
    log.push({
      step: "aep",
      ok: res.ingested,
      detail: res.ingested
        ? `ingested · action=${res.ingest?.action ?? "?"} · session=${res.ingest?.session_status ?? "?"}`
        : (res.error ?? "no telemetry ingested"),
    });
    setLaunchLog(log);
    setBusy(false);
    if (res.ok) {
      const fresh = await props.api.mission(m.mission_id);
      if (fresh.ok && fresh.data) {
        setResult(fresh.data);
        props.onCreated(fresh.data);
      }
    }
    setServerError(res.ok ? null : (res.error ?? "launch did not complete"));
  }

  const launchable = LAUNCHABLE_AGENTS.includes(v.agent);
  const launchBlockedReason = launchable
    ? null
    : `Launch for "${v.agent}" is not enabled in this build (Wave 3 wires real agent launch). The mission will be created as DRAFT only.`;

  /**
   * Wave 9.2 — START MISSION.
   *
   * The Wave-3 "Launch…" path above ATTACHES to a WorkBuddy session that is
   * already running. This one makes a process exist. They answer different
   * questions and neither is presented as the other.
   *
   * The two calls are deliberately separate: `reviewStart` is the server's
   * preview (it spawns nothing and does not spend the single-use directive
   * nonce), and `commitStart` is the only client action that starts work. The
   * button that commits is only reachable from the preview dialog, so the
   * operator always sees the exact argv and the server's own `would_accept`
   * before anything runs.
   */
  const nativeCap = probe?.native_launch ?? null;

  async function reviewStart(m: Mission) {
    setServerError(null);
    setStarting(true);
    const r = await props.api.launchRequest(m.mission_id, props.controlToken);
    setStarting(false);
    if (!r.ok || !r.data) {
      setServerError(r.error ?? "launch preview was refused");
      return;
    }
    setStartResult(null);
    setStartPreview(r.data);
  }

  async function commitStart(m: Mission) {
    setStarting(true);
    const r = await props.api.launchCommit(m.mission_id, props.controlToken);
    setStarting(false);
    setStartPreview(null);
    if (!r.ok || !r.data) {
      setServerError(r.error ?? "launch was refused");
      return;
    }
    setStartResult(r.data);
    // The capability is a MEASUREMENT, so a completed attempt changes it: a run
    // that came back AUTH_REQUIRED must flip it to HOST_ACTIVATION_PENDING
    // immediately, not on the next page load.
    void refreshProbe();
    const fresh = await props.api.mission(m.mission_id);
    if (fresh.ok && fresh.data) {
      setResult(fresh.data);
      props.onCreated(fresh.data);
    }
    // A commit that spawned but could not authenticate is NOT a failure of the
    // request -- it is a fact about the host, and it is rendered in the result
    // panel rather than as an error toast that hides the classification.
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Mission Studio</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Define the goal and the guardrails. Launch is a separate, explicit
              step — creating a mission never starts work.
            </p>
          </div>
          <Badge variant={props.controlToken ? "ok" : "unknown"}>
            {props.controlToken ? "control token set" : "no control token"}
          </Badge>
        </div>

        {serverError && <InlineError message={serverError} />}

        {props.projectsError && (
          <InlineError message={`Projects unavailable — ${props.projectsError}`} />
        )}

        {!props.loadingProjects && props.projects.length === 0 && !props.projectsError ? (
          <Card>
            <CardContent className="pt-4">
              <EmptyState
                icon={FolderPlus}
                title="No projects yet"
                detail="A mission must run under a project. Create the first project, then come back here."
                action={
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => setNewProjectOpen(true)}>
                      <FolderPlus className="h-3.5 w-3.5" />
                      Create project
                    </Button>
                    <Button variant="ghost" size="sm" onClick={props.onOpenProjects}>
                      View projects
                    </Button>
                  </div>
                }
              />
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>Mission definition</CardTitle>
            <CardDescription>
              Fields marked required are refused by the server if empty.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field
              id="msn-title"
              label="Title *"
              error={showErrors ? errors.title : undefined}
              className="md:col-span-2"
            >
              <Input
                id="msn-title"
                value={v.title}
                onChange={(e) => set("title", e.target.value)}
                placeholder="e.g. Audit the ingest path for unhandled envelope shapes"
              />
            </Field>

            <Field
              id="msn-project"
              label="Project *"
              error={showErrors ? errors.projectId : undefined}
            >
              <div className="flex gap-2">
                <Select
                  id="msn-project"
                  value={v.projectId}
                  onChange={(e) => set("projectId", e.target.value)}
                  disabled={props.loadingProjects}
                  className="flex-1"
                >
                  <option value="">
                    {props.loadingProjects ? "Loading projects…" : "Select a project…"}
                  </option>
                  {props.projects.map((p) => (
                    <option key={p.project_id} value={p.project_id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setNewProjectOpen(true)}
                  type="button"
                >
                  <FolderPlus className="h-3.5 w-3.5" />
                  New
                </Button>
              </div>
            </Field>

            <Field id="msn-agent" label="Agent *" error={showErrors ? errors.agent : undefined}>
              <Select
                id="msn-agent"
                value={v.agent}
                onChange={(e) => set("agent", e.target.value)}
              >
                {AGENT_CHOICES.map((a) => (
                  <option key={a} value={a}>
                    {a}
                    {LAUNCHABLE_AGENTS.includes(a) ? " (launch enabled)" : " (draft only)"}
                  </option>
                ))}
              </Select>
            </Field>

            <Field id="msn-goal" label="Goal" className="md:col-span-2">
              <Textarea
                id="msn-goal"
                value={v.goal}
                onChange={(e) => set("goal", e.target.value)}
                placeholder="What outcome must exist when this mission is done?"
              />
            </Field>

            <Field
              id="msn-wd"
              label="Working Directory"
              hint="Absolute path the agent runs in. Empty = the project root."
            >
              <Input
                id="msn-wd"
                value={v.workingDirectory}
                onChange={(e) => set("workingDirectory", e.target.value)}
                placeholder="absolute path, e.g. the folder your code lives in"
                spellCheck={false}
              />
            </Field>

            <Field
              id="msn-budget"
              label="Budget (units)"
              error={showErrors ? errors.budgetUnits : undefined}
              hint="Blank = unbudgeted. Non-numeric is refused."
            >
              <Input
                id="msn-budget"
                value={v.budgetUnits}
                onChange={(e) => set("budgetUnits", e.target.value)}
                inputMode="decimal"
                placeholder="0"
              />
            </Field>

            <Field
              id="msn-ctx"
              label="Context"
              className="md:col-span-2"
              hint="Background the agent needs. Include evidence pointers, not secrets."
            >
              <Textarea
                id="msn-ctx"
                value={v.context}
                onChange={(e) => set("context", e.target.value)}
                placeholder="Relevant files, prior decisions, constraints…"
              />
            </Field>

            <Field
              id="msn-perms"
              label="Permissions (requested)"
              className="md:col-span-2"
              hint="Comma separated. These are requests — policy decides the grant."
            >
              <Input
                id="msn-perms"
                value={v.permissionsText}
                onChange={(e) => set("permissionsText", e.target.value)}
                placeholder="fs.read, test.run"
                spellCheck={false}
              />
              <div className="mt-2 flex flex-wrap gap-1">
                {PERMISSION_CATALOG.map((p) => {
                  const on = parsePermissions(v.permissionsText).includes(p);
                  return (
                    <button
                      key={p}
                      type="button"
                      onClick={() => {
                        const cur = parsePermissions(v.permissionsText);
                        const next = on ? cur.filter((x) => x !== p) : [...cur, p];
                        set("permissionsText", formatPermissions(next));
                      }}
                      className={cn(
                        "rounded-md border px-2 py-0.5 text-[11px] transition-colors",
                        on
                          ? "border-primary/50 bg-primary/15 text-primary"
                          : "border-border text-muted-foreground hover:bg-accent",
                      )}
                    >
                      {p}
                    </button>
                  );
                })}
              </div>
            </Field>

            {payload && payload.permissions.length > 0 ? (
              <div className="md:col-span-2 text-[11px] text-muted-foreground">
                Will request: <code className="text-foreground/80">{payload.permissions.join(", ")}</code>
              </div>
            ) : null}

            <div className="md:col-span-2 flex items-center gap-2 border-t border-border pt-4">
              <Button onClick={() => void submit()} disabled={!canSubmit}>
                <Save className="h-3.5 w-3.5" />
                {busy ? "Creating…" : "Create mission (DRAFT)"}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setV({ ...EMPTY_MISSION_FORM });
                  setTouched(false);
                  setResult(null);
                  setServerError(null);
                  setLaunchLog([]);
                }}
              >
                Reset
              </Button>
              {!props.controlToken ? (
                <span className="text-[11px] text-status-warn">
                  Set the control token in the top bar to enable creation.
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>

        {/* ---- Created mission / launch ---- */}
        {result && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-status-ok" />
                Mission created
              </CardTitle>
              <CardDescription>
                Server-assigned state, not a local guess.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                <KV label="Mission ID" value={result.mission_id} mono />
                <KV label="Project" value={result.project_id} mono />
                <KV label="Agent" value={result.agent} />
                <KVStatus label="Status" status={result.status} />
              </div>
              {launchBlockedReason ? (
                <div className="flex items-start gap-2 rounded-md border border-status-warn/40 bg-status-warn/10 px-3 py-2 text-xs text-status-warn">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{launchBlockedReason}</span>
                </div>
              ) : (
                <Button
                  size="sm"
                  onClick={() => setLaunchPreview(result)}
                  disabled={!props.controlToken || busy}
                >
                  <Rocket className="h-3.5 w-3.5" />
                  Launch…
                </Button>
              )}

              {launchLog.length > 0 && (
                <div className="rounded-md border border-border bg-background/50 p-2 font-mono text-[11px]">
                  {launchLog.map((l, i) => (
                    <div key={i} className={l.ok ? "text-status-ok" : "text-status-danger"}>
                      {l.ok ? "✓" : "✗"} {l.step} — {l.detail}
                    </div>
                  ))}
                </div>
              )}

              {/* ---- Wave 9.2: START a real workload on this host ---- */}
              {!launchBlockedReason && result && (
                <div className="space-y-2 rounded-md border border-border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs font-medium text-foreground">
                      <Rocket className="h-3.5 w-3.5" />
                      Start Mission
                      <span className="font-normal text-muted-foreground">
                        — starts a real workload
                      </span>
                    </div>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => void reviewStart(result)}
                      disabled={!props.controlToken || starting}
                    >
                      Start Mission…
                    </Button>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Not the same as “Launch…” above: that one ATTACHES to a session
                    that is already running, and it starts nothing. This one asks the
                    host to START one, under a directive that is signed, single-use,
                    short-lived and bound to this mission and project root.
                  </p>
                  {nativeCap && (
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      <Badge
                        variant={
                          nativeCap.executability === "LOCAL_SPAWN_READY"
                            ? "ok"
                            : nativeCap.executability === "HOST_ACTIVATION_PENDING"
                              ? "warn"
                              : "unknown"
                        }
                      >
                        {nativeCap.executability}
                      </Badge>
                      <span className="text-muted-foreground">
                        {nativeCap.end_to_end_verified
                          ? "a real run has completed end-to-end on this host"
                          : nativeCap.executability === "HOST_ACTIVATION_PENDING"
                            ? "the host can spawn, but the agent has not authenticated"
                            : "no launch has completed end-to-end here yet"}
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* ---- Wave 9.2: what the START actually did ---- */}
              {startResult && (
                <div className="space-y-2 rounded-md border border-border bg-background/50 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-medium text-foreground">Start Mission result</span>
                    <Badge variant={startResult.classification === "LAUNCHED" ? "ok" : "warn"}>
                      {startResult.classification ?? "unknown"}
                    </Badge>
                    {typeof startResult.pid === "number" && (
                      <span className="font-mono text-muted-foreground">pid {startResult.pid}</span>
                    )}
                  </div>
                  {startResult.classification === "AUTH_REQUIRED" && (
                    <p className="text-[11px] text-status-warn">
                      A process started, but the agent refused to authenticate — so no
                      work was performed and the mission was NOT advanced to RUNNING.
                      {startResult.stream?.auth_error
                        ? ` Reason: ${startResult.stream.auth_error}.`
                        : ""}{" "}
                      An approval has been recorded on this mission.
                    </p>
                  )}
                  {startResult.aep && (
                    <p className="text-[11px] text-muted-foreground">
                      session{" "}
                      {startResult.aep.session_discovered
                        ? startResult.aep.session_id
                        : "not observed"}
                      {" · "}
                      AEP ingest {startResult.aep.ingested ? "yes" : "no"}
                      {startResult.stream
                        ? ` · ${startResult.stream.event_count} stream event(s)`
                        : ""}
                    </p>
                  )}
                  <div className="rounded-md border border-border bg-background/60 p-2 font-mono text-[11px]">
                    {startResult.steps.map((s, i) => (
                      <div key={i} className={s.ok ? "text-status-ok" : "text-status-danger"}>
                        {s.ok ? "✓" : "✗"} {s.step} — {s.detail}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      {/* ---- Wave 9.2 — Start Mission preview (computed by the server) ---- */}
      <Dialog open={startPreview !== null} onOpenChange={(o) => !o && setStartPreview(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Start Mission</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-xs">
            <p className="text-muted-foreground">
              This starts a real workload on this host. The argv below is exactly what
              the server will execute: the goal occupies one slot and is never
              interpreted as a command, and there is no shell involved.
            </p>
            <pre className="max-h-32 overflow-auto rounded-md border border-border bg-background/50 p-2 font-mono text-[11px]">
              {startPreview?.preview.argv.join(" ") ?? ""}
            </pre>
            <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
              <KV label="Would be accepted" value={String(startPreview?.would_accept)} />
              <KV label="Reason" value={startPreview?.would_reason ?? ""} mono />
              <KV label="Shell" value={String(startPreview?.preview.shell)} />
              <KV label="Actuation" value={String(startPreview?.preview.actuation)} />
              <KV label="Capability" value={startPreview?.capability ?? ""} mono />
              <KV
                label="Directive TTL"
                value={`${String(startPreview?.directive?.["ttl_s"] ?? "?")}s`}
                mono
              />
            </div>
            {(startPreview?.preview.problems ?? []).length > 0 && (
              <div className="rounded-md border border-status-danger/40 bg-status-danger/10 p-2">
                {(startPreview?.preview.problems ?? []).map((p) => (
                  <div key={p.code} className="font-mono text-[11px] text-status-danger">
                    {p.code}: {p.detail}
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setStartPreview(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!startPreview?.would_accept || starting}
              onClick={() => {
                if (startPreview && result) void commitStart(result);
              }}
            >
              <Rocket className="h-3.5 w-3.5" />
              {starting ? "Starting…" : "Start Mission"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- Launch preview dialog ---- */}
      <Dialog open={launchPreview !== null} onOpenChange={(o) => !o && setLaunchPreview(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Launch preview</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-xs">
            <p className="text-muted-foreground">
              Launching{" "}
              <code className="text-foreground">{launchPreview?.title}</code> will run this
              exact sequence on the server, against a real agent session:
            </p>
            <ol className="list-inside list-decimal space-y-1 rounded-md border border-border bg-background/50 p-3 font-mono">
              <li>
                discover a <span className="text-status-info">live {launchPreview?.agent ?? "agent"} session</span> on this host
              </li>
              <li>
                DRAFT → <span className="text-status-info">READY</span>
              </li>
              {LAUNCH_SEQUENCE.slice(1).map((s, i) => (
                <li key={s}>
                  {LAUNCH_SEQUENCE[i]} → <span className="text-status-info">{s}</span>
                </li>
              ))}
              <li>
                create <span className="text-status-info">AgentRun</span> + bind the Session
              </li>
              <li>
                ingest real <span className="text-status-info">AEP telemetry</span> (assessment →
                policy → directive → audit)
              </li>
            </ol>
            {probe && !probe.workbuddy.ok && (
              <p className="flex items-start gap-2 rounded-md border border-status-warn/40 bg-status-warn/10 px-3 py-2 text-status-warn">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  No live {launchPreview?.agent} session detected — {probe.workbuddy.detail ?? probe.workbuddy.reason}.
                  Launch will be refused rather than faked. Start the agent, then retry.
                </span>
              </p>
            )}
            {probe && probe.workbuddy.ok && (
              <p className="flex items-start gap-2 rounded-md border border-status-info/40 bg-status-info/10 px-3 py-2 text-status-info">
                <Activity className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  Live session ready: <code>{probe.workbuddy.source?.session_id}</code> (pid{" "}
                  {probe.workbuddy.source?.pid})
                </span>
              </p>
            )}
            <p className="flex items-start gap-2 rounded-md border border-status-ok/40 bg-status-ok/10 px-3 py-2 text-status-ok">
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                Actuation is disabled. Launching a mission records intent, binds a
                Session and ingests telemetry; it does not mount an actuator.
              </span>
            </p>
            {!launchable && (
              <p className="text-status-warn">
                Agent <b>{launchPreview?.agent}</b> cannot be launched in this build.
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setLaunchPreview(null)}>
              Cancel
            </Button>
            <Button
              disabled={busy || !launchable}
              onClick={() => {
                const m = launchPreview;
                setLaunchPreview(null);
                if (m) void runLaunch(m);
              }}
            >
              <Send className="h-3.5 w-3.5" />
              {busy ? "Launching…" : "Confirm launch"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CreateProjectDialog
        open={newProjectOpen}
        onOpenChange={setNewProjectOpen}
        onCreate={props.onCreateProject}
        api={props.api}
        projectError={props.projectError ?? null}
        rejection={props.projectRejection ?? null}
      />
    </div>
  );
}

function Field({
  id,
  label,
  hint,
  error,
  className,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p className="text-[11px] text-status-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

function KV({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-border px-2.5 py-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("truncate text-foreground", mono && "font-mono text-[11px]")}>
        {value}
      </span>
    </div>
  );
}

function KVStatus({ label, status }: { label: string; status: string }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-border px-2.5 py-1.5">
      <span className="text-muted-foreground">{label}</span>
      <Badge variant={toneVariant(missionTone(status))}>{status}</Badge>
    </div>
  );
}

function CreateProjectDialog({
  open,
  onOpenChange,
  onCreate,
  api,
  projectError,
  rejection: rejectionFromServer,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreate: (name: string, rootPath: string) => Promise<Project | null>;
  api: DomainApi;
  /** RISK-1 — the server's own failure text, shown when the failure was NOT a
   * path refusal. Without it a 401/403/timeout renders as a bare
   * "Project not created." with no reason at all. */
  projectError: string | null;
  rejection?: ProjectRootRejection | null;
}) {
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // RISK-1: the server's structured refusal is owned by useDomain and threaded in
  // through the `rejection` prop, so this dialog never holds a second copy that
  // could disagree with the hook's. Displayed separately from `err` so a PATH
  // refusal (which carries a code and a fix) is never rendered as the generic
  // auth/transport message that a token failure deserves.
  const rejection = rejectionFromServer ?? null;
  // The boundary, as the server last reported it. `null` means "we have not
  // got an answer yet" and is rendered UNKNOWN -- NOT as an empty list. An empty
  // list is a real, alarming answer (fail-closed: nothing can be created), and
  // rendering it as the same thing as "the read failed" would tell the operator
  // their server is fine when it is not answering.
  const [scope, setScope] = useState<ProjectScope | null>(null);

  // Read the boundary through the ONE client that knows the API base.
  //
  // NOT a bare `fetch("/api/v1/domain/home")`: the shipped topology is
  // cross-origin (the SPA is served by its own ASGI process that does NOT proxy
  // /api/v1, and the API lives on a different loopback port), so a
  // page-relative URL asks the static file server for an API route, gets the SPA
  // shell back with 200, and fails to parse -- leaving this list permanently
  // empty. `api.home()` carries the same resolved base as every other read in
  // this app, and honours VITE_API_BASE / `same-origin` like the rest of the UI.
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const r = await api.home();
        if (alive && r.ok && r.data) setScope(r.data.project_scope ?? null);
      } catch {
        // Unreachable server: leave `scope` null so the row reads UNKNOWN,
        // which is the truth, rather than a fabricated boundary.
      }
    })();
    return () => {
      alive = false;
    };
  }, [api, open]);

  const allowedRoots = scope?.allowed_project_roots ?? null;
  // The refusal itself carries the authoritative list as of the moment it was
  // issued. Prefer it over a possibly-stale pre-fetch: if the operator widened
  // the boundary and retried, the refusal is the newer truth.
  const rootsToShow = rejection?.allowed_project_roots?.length
    ? rejection.allowed_project_roots
    : allowedRoots;
  // Plain-language guidance for THIS code, or null for a code this build has
  // never heard of -- in which case the server's own detail is shown alone
  // rather than being dressed up with advice meant for a different problem.
  const rejectionHelp = rejection ? projectRootRejectionHelp(rejection.code) : null;


  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create project</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="prj-name">Name *</Label>
            <Input
              id="prj-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. JEV Command Center"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="prj-root">Root path</Label>
            <Input
              id="prj-root"
              value={rootPath}
              onChange={(e) => setRootPath(e.target.value)}
              placeholder="full absolute path of an existing directory"
              spellCheck={false}
            />
            {/* RISK-1 spec 10 — the allowed roots, next to the input that is
                refused without them. The placeholder deliberately names no drive
                letter: this project has been broken by a hardcoded drive four
                times, and a placeholder naming one is how the fifth starts -- the
                drive an operator copies is a drive that does not exist on their
                machine, or worse, one that does. The roots come from the server,
                never from a value invented here.

                THREE states, not two. "Not loaded / server silent" must not look
                like "loaded and empty", because they call for opposite
                reactions: one is an outage to report, the other is a
                misconfiguration to fix. */}
            <p className="text-[11px] text-muted-foreground">
              {rootsToShow === null
                ? "Project roots: UNKNOWN — the server has not reported a boundary yet."
                : rootsToShow.length === 0
                  ? "No project roots are configured on the server, so every project root will be refused. An operator must set launcher.project_scope_base (config) or JCC_ALLOWED_PROJECT_ROOTS (environment)."
                  : `Must be an absolute path inside: ${rootsToShow.join("  ·  ")}`}
            </p>
          </div>
          {err && <InlineError message={err} />}
          {/* RISK-1 — the server's reason code, when the refusal was about the
              path. PROJECT_ROOT_MISSING ("create it") and
              PROJECT_ROOT_OUT_OF_SCOPE ("you may not have it") are opposite
              instructions; showing one message for both is how an operator ends
              up creating the very directory the guard just declined to expose.

              All four codes get their own plain-language title and fix, because
              the code alone is not an instruction an operator can act on. The
              server's own `detail` is kept verbatim underneath as the
              audit-grade statement -- it names the resolved path, which is often
              not what was typed, and that difference is worth seeing. */}
          {rejection && (
            <div
              className="rounded-md border border-status-danger/40 bg-status-danger/5 p-2 text-[11px]"
              role="alert"
            >
              <div className="font-semibold text-foreground">
                {rejectionHelp?.title ?? "Project root refused"}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                {rejection.code}
              </div>
              {rejectionHelp && (
                <p className="mt-1 text-foreground">{rejectionHelp.fix}</p>
              )}
              {rejection.raw ? (
                <p className="mt-1 text-muted-foreground">
                  You entered: <span className="font-mono">{rejection.raw}</span>
                </p>
              ) : null}
              <p className="mt-1 text-muted-foreground">{rejection.detail}</p>
              {rejection.allowed_project_roots.length > 0 ? (
                <p className="mt-1 text-muted-foreground">
                  Allowed roots:{" "}
                  <span className="font-mono">
                    {rejection.allowed_project_roots.join("  ·  ")}
                  </span>
                </p>
              ) : (
                <p className="mt-1 text-muted-foreground">
                  The server named no allowed roots with this refusal, which means
                  its allow-list is empty — nothing can be created until an
                  operator configures one.
                </p>
              )}
            </div>
          )}
          {/* A failure that is NOT a path refusal still has to say why. This is the
              server's own text, verbatim; the two panels are mutually exclusive by
              construction (a rejection always sets projectError too), and this one
              renders only when there is no code to explain. */}
          {!rejection && projectError ? (
            <p className="rounded-md border border-status-danger/40 bg-status-danger/5 p-2 text-[11px] text-foreground">
              {projectError}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={busy || !name.trim()}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              const p = await onCreate(name.trim(), rootPath.trim());
              setBusy(false);
              if (!p) {
                // RISK-1: distinguish a PATH refusal from everything else.
                // "Check the control token" is actively wrong advice for a
                // PROJECT_ROOT_MISSING -- the token was fine, and the operator
                // would go looking for a credential problem that does not exist
                // instead of creating the directory they were told about.
                //
                // No message here may promise a reason panel "below": `rejection`
                // is a PROP, so within this handler it still holds the value from
                // before this attempt, and the new one only arrives on the next
                // render. A message that says "shown below" and then shows
                // nothing is worse than no message. The server's own text is
                // rendered separately and unconditionally.
                setErr("Project not created.");
                return;
              }
              setName("");
              setRootPath("");
              onOpenChange(false);
            }}
          >
            {busy ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
