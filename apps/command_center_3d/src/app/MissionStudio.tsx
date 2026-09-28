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
import { useMemo, useState } from "react";
import {
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
import type { DomainApi, Mission, Project } from "../app/domainApi";

export interface StudioProps {
  api: DomainApi;
  controlToken: string;
  projects: Project[];
  loadingProjects: boolean;
  projectsError: string | null;
  onCreated: (m: Mission) => void;
  onOpenProjects: () => void;
  onCreateProject: (name: string, rootPath: string) => Promise<Project | null>;
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
   * Launch = the declared handshake, executed step by step against the server.
   * Each transition is a separate authenticated call and each is logged. If one
   * is refused we STOP — we do not continue on a broken chain and we do not
   * pretend the later steps happened.
   */
  async function runLaunch(m: Mission) {
    setBusy(true);
    setLaunchLog([]);
    const log: { step: string; ok: boolean; detail: string }[] = [];
    let current = m;
    for (const step of LAUNCH_SEQUENCE) {
      const r = await props.api.transitionMission(props.controlToken, current.mission_id, {
        to: step,
        reason: "launched from Mission Studio",
      });
      if (!r.ok || !r.data) {
        log.push({ step, ok: false, detail: r.error ?? "refused" });
        setLaunchLog([...log]);
        setBusy(false);
        return;
      }
      current = r.data;
      log.push({ step, ok: true, detail: `now ${current.status}` });
      setLaunchLog([...log]);
    }
    // The mission is RUNNING; open the run record so a Session can attach.
    const run = await props.api.createRun(props.controlToken, current.mission_id, {
      agent: current.agent,
      connection_ref: current.connection_ref,
    });
    log.push({
      step: "create-run",
      ok: run.ok,
      detail: run.ok && run.data ? run.data.run_id : (run.error ?? "failed"),
    });
    setLaunchLog([...log]);
    setBusy(false);
    setResult(current);
    props.onCreated(current);
  }

  const launchable = LAUNCHABLE_AGENTS.includes(v.agent);
  const launchBlockedReason = launchable
    ? null
    : `Launch for "${v.agent}" is not enabled in this build (Wave 3 wires real agent launch). The mission will be created as DRAFT only.`;

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
                placeholder="F:\\jev"
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
            </CardContent>
          </Card>
        )}
      </div>

      {/* ---- Launch preview dialog ---- */}
      <Dialog open={launchPreview !== null} onOpenChange={(o) => !o && setLaunchPreview(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Launch preview</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-xs">
            <p className="text-muted-foreground">
              Launching{" "}
              <code className="text-foreground">{launchPreview?.title}</code> will apply this
              exact transition sequence against the server:
            </p>
            <ol className="list-inside list-decimal space-y-1 rounded-md border border-border bg-background/50 p-3 font-mono">
              <li>
                DRAFT → <span className="text-status-info">READY</span>
              </li>
              {LAUNCH_SEQUENCE.slice(1).map((s, i) => (
                <li key={s}>
                  {LAUNCH_SEQUENCE[i]} → <span className="text-status-info">{s}</span>
                </li>
              ))}
              <li>
                create <span className="text-status-info">AgentRun</span> (Session attaches at Wave 3)
              </li>
            </ol>
            <p className="flex items-start gap-2 rounded-md border border-status-ok/40 bg-status-ok/10 px-3 py-2 text-status-ok">
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                Actuation is disabled. Launching a mission records intent and state;
                it does not mount an actuator.
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
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreate: (name: string, rootPath: string) => Promise<Project | null>;
}) {
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
              placeholder="F:\\jev"
              spellCheck={false}
            />
          </div>
          {err && <InlineError message={err} />}
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
                setErr("Project creation failed. Check the control token.");
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
