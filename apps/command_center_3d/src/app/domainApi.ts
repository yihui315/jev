/**
 * domainApi — the ONLY client that talks to the v1.3 domain surface.
 *
 * Every view reads through here instead of calling `fetch` directly. That gives
 * one place to (a) attach the in-memory control token to mutating calls,
 * (b) keep the token out of URLs / localStorage / logs, and (c) return a
 * structured result so a component can render an honest failure rather than an
 * empty list.
 *
 * HARD RULES enforced here:
 *  - The control token is passed per-call from memory. It is never persisted,
 *    never put in a query string, never logged.
 *  - A non-2xx response is returned as `{ok:false}` with the server's own
 *    detail text intact — never swallowed into a fake empty success.
 *  - Read helpers never throw; they return `ok:false` + `error`.
 */

export interface ApiResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
  error: string | null;
}

function extractDetail(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail?: unknown }).detail;
    if (typeof d === "string") return d;
    if (d != null) return JSON.stringify(d);
  }
  if (typeof body === "string" && body.trim()) return body.slice(0, 400);
  return `HTTP ${status}`;
}

async function request<T>(
  base: string,
  method: "GET" | "POST",
  path: string,
  opts: { body?: unknown; token?: string | null; timeoutMs?: number } = {},
): Promise<ApiResult<T>> {
  const { body, token, timeoutMs = 15000 } = opts;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    // Token travels in a header only. Never in the URL, never persisted.
    if (token) headers["X-JCC-Token"] = token;
    const res = await fetch(base + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctrl.signal,
    });
    let parsed: unknown = null;
    const text = await res.text();
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    if (!res.ok) {
      return { ok: false, status: res.status, data: null, error: extractDetail(parsed, res.status) };
    }
    return { ok: true, status: res.status, data: parsed as T, error: null };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    const friendly = msg.includes("abort") ? `request timed out after ${timeoutMs}ms` : msg;
    return { ok: false, status: 0, data: null, error: friendly };
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Wire types (mirror packages/jcc_core/domain.py models)
// ---------------------------------------------------------------------------

export interface Project {
  project_id: string;
  name: string;
  root_path: string;
  description: string;
  created_at: number;
  updated_at: number;
  archived: boolean;
}

export interface Mission {
  mission_id: string;
  project_id: string;
  title: string;
  goal: string;
  agent: string;
  connection_ref: string | null;
  working_directory: string;
  context: string;
  permissions: string[];
  budget_units: number;
  status: string;
  attention_kind: string | null;
  attention_reason: string | null;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  session_id: string | null;
}

export interface ProjectDetail extends Project {
  missions: Mission[];
}

export interface AgentRun {
  run_id: string;
  mission_id: string;
  agent: string;
  connection_ref: string | null;
  session_id: string | null;
  status: string;
  launched_at: number | null;
  ended_at: number | null;
  exit_reason: string | null;
  created_at: number;
}

export interface Artifact {
  artifact_id: string;
  mission_id: string;
  run_id: string | null;
  session_id: string | null;
  kind: string;
  name: string;
  uri: string;
  sha256: string | null;
  bytes: number;
  status: string;
  provenance: Record<string, unknown>;
  created_at: number;
}

export interface DomainCapability {
  capability_id: string;
  connection_id: string;
  kind: string;
  detail: string;
  evidence_ref: string | null;
  measured_at: number | null;
  created_at: number;
  /**
   * Wave 7 — how this capability was established. `MEASURED` (a probe observed
   * it; `evidence_ref` present) vs `ASSERTED` (a human recorded it). Returned by
   * the server on the Wave 7 routes; optional here so older payloads still type.
   */
  provenance?: "MEASURED" | "ASSERTED";
}

export interface DomainConnection {
  connection_id: string;
  agent: string;
  display_name: string;
  connection_type: string;
  endpoint: string;
  secret_env_var: string | null;
  health: string;
  last_probe_at: number | null;
  last_event_at: number | null;
  last_session_id: string | null;
  detail: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  /** Server-computed: only true when a CONTROL_VERIFIED capability exists. */
  control_verified: boolean;
  capabilities: DomainCapability[];
}

export interface AttentionItem {
  mission_id: string;
  title: string;
  project_id: string;
  kind: string | null;
  reason: string | null;
  updated_at: number;
}

/**
 * Wave 7 — what a verification probe OBSERVED.
 *
 * `kind` is an observation, never a caller input, and v1.3 keeps actuation off,
 * so it can never be `CONTROL_VERIFIED`. `reason` explains an `UNKNOWN`.
 */
export interface ProbeObservation {
  kind: string;
  detail: string;
  evidence: Record<string, unknown>;
  evidence_ref: string | null;
  reason: string | null;
  measured_at: number;
}

/** Wave 7 — the server's answer to `POST /connections/{cid}/verify`. */
export interface ConnectionVerification {
  connection_id: string;
  agent: string;
  observed: ProbeObservation;
  health: string;
  control_verified: boolean;
  capabilities: DomainCapability[];
}

/**
 * Wave 4 — a control action proposed for one inbox item.
 *
 * `policy_permitted` and `executability` answer DIFFERENT questions and are never
 * merged: an action can be allowed by policy yet not work on this host right now.
 * `executability === "UNKNOWN"` means the prerequisite could not be determined --
 * the UI renders it neutral, never green.
 */
export interface AttentionAction {
  action: string;
  label: string;
  risk: "low" | "high";
  policy_permitted: boolean;
  executability: "EXECUTABLE" | "BLOCKED" | "UNKNOWN";
  blocked_reason: string | null;
  detail: string;
}

export interface AttentionResolution {
  mission_id: string;
  kind: string | null;
  actions: AttentionAction[];
}

export interface AttentionResolutionsDoc {
  schema: string;
  actuation_enabled: boolean;
  items: AttentionResolution[];
}

/** Wave 4 — the server's answer to a preview or an apply. */
export interface ResolveResult {
  ok: boolean;
  mission_id: string;
  action: string;
  dry_run: boolean;
  applied: boolean;
  policy_permitted?: boolean;
  executability?: string;
  blocked_reason?: string | null;
  detail?: string;
  plan?: Array<{ target: string; id?: string; ids?: string[]; change: string }>;
  result?: Record<string, unknown>;
}

/** Wave 3 — one launch step's outcome (the server names the step it died on). */
export interface LaunchStep {
  step: string;
  ok: boolean;
  detail: string;
}

/** Wave 3 — a launch result. `ok=false` is a legitimate negative, not an error. */
export interface LaunchResult {
  mission_id: string;
  agent: string;
  steps: LaunchStep[];
  run_id: string | null;
  session_id: string | null;
  ingested: boolean;
  ok: boolean;
  error?: string;
  ingest?: { action?: string; session_status?: string; acted?: boolean } | null;
  capture_source?: {
    kind: string; file: string | null; session_id: string | null;
    pid: number | null; version: string | null;
    liveness: LaunchLiveness;
  } | null;
}

/** Wave 3 — three-state liveness. NEVER render `stale`/`dead` as green. */
export interface LaunchLiveness {
  state: "live" | "stale" | "dead";
  reason: string;
  pid_alive: boolean;
  heartbeat_age_ms: number | null;
  fresh: boolean;
}

export interface LaunchProbe {
  schema: string;
  launchable_agents: string[];
  workbuddy: {
    ok: boolean;
    reason?: string | null;
    detail?: string | null;
    session_count: number;
    observed: Array<{
      sessionId: string; pid: number; cwd: string; version: string;
      state: string; reason: string;
    }>;
    source?: LaunchResult["capture_source"];
  };
  actuation: false;
}

export interface MissionWorkload {
  mission_id: string;
  mission_status: string;
  agent: string;
  run_count: number;
  runs: AgentRun[];
  session_ids: string[];
  sessions: Array<Record<string, unknown> & { session_id: string; known: boolean }>;
  live_session_count: number;
  actuation: false;
  observed_at: number;
}

export interface HomeSummary {
  schema: string;
  active_projects: number;
  running_missions: number;
  needs_attention: number;
  completed_missions: number;
  recent_deliverables: number;
  /** Wave 5 — honest split. `verified` is the headline; `produced` is everything
   * the agent emitted but a human has not yet accepted (incl. rejected). */
  deliverables: DeliverableCounts;
  attention_items: AttentionItem[];
  actuation_enabled: boolean;
  localhost_only: boolean;
}

export interface DeliverableCounts {
  produced: number;
  verified: number;
  rejected: number;
  total: number;
}

/** Wave 5 — a deliverable row from the server-side view (artifact + mission title). */
export interface Deliverable extends Artifact {
  mission_title: string;
}

/** One row of `mission_events`, as returned by GET …/missions/{id}/timeline. */
export interface TimelineEntry {
  seq: number;
  /** Unix seconds (server writes `time.time()`), not milliseconds. */
  timestamp: number;
  from_status: string | null;
  to_status: string;
  reason: string;
}

export interface ApprovalRecord {
  approval_id: string;
  mission_id: string;
  kind: string;
  reason: string;
  state: string;
  requested_by: string;
  decided_by: string | null;
  decided_at: number | null;
  payload: Record<string, unknown>;
  created_at: number;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export class DomainApi {
  constructor(private base: string) {}

  setBase(base: string): void {
    this.base = base;
  }

  // --- reads (open, token-free) ---
  home(): Promise<ApiResult<HomeSummary>> {
    return request<HomeSummary>(this.base, "GET", "/api/v1/domain/home");
  }
  projects(includeArchived = false): Promise<ApiResult<Project[]>> {
    const q = includeArchived ? "?include_archived=true" : "";
    return request<Project[]>(this.base, "GET", `/api/v1/domain/projects${q}`);
  }
  project(pid: string): Promise<ApiResult<ProjectDetail>> {
    return request<ProjectDetail>(this.base, "GET", `/api/v1/domain/projects/${encodeURIComponent(pid)}`);
  }
  missions(filter: { projectId?: string; status?: string; limit?: number } = {}):
    Promise<ApiResult<Mission[]>> {
    const p = new URLSearchParams();
    if (filter.projectId) p.set("project_id", filter.projectId);
    if (filter.status) p.set("status_filter", filter.status);
    if (filter.limit) p.set("limit", String(filter.limit));
    const q = p.toString();
    return request<Mission[]>(this.base, "GET", `/api/v1/domain/missions${q ? "?" + q : ""}`);
  }
  mission(mid: string): Promise<ApiResult<Mission>> {
    return request<Mission>(this.base, "GET", `/api/v1/domain/missions/${encodeURIComponent(mid)}`);
  }
  missionTimeline(mid: string): Promise<ApiResult<TimelineEntry[]>> {
    return request<TimelineEntry[]>(
      this.base, "GET", `/api/v1/domain/missions/${encodeURIComponent(mid)}/timeline`);
  }
  missionRuns(mid: string): Promise<ApiResult<AgentRun[]>> {
    return request<AgentRun[]>(
      this.base, "GET", `/api/v1/domain/missions/${encodeURIComponent(mid)}/runs`);
  }
  missionArtifacts(mid: string): Promise<ApiResult<Artifact[]>> {
    return request<Artifact[]>(
      this.base, "GET", `/api/v1/domain/missions/${encodeURIComponent(mid)}/artifacts`);
  }
  /** Wave 5: the server-side deliverable view (no client fan-out; carries the
   * mission title and supports a status filter). */
  deliverables(filter: { status?: string; missionId?: string; limit?: number } = {}):
    Promise<ApiResult<Deliverable[]>> {
    const p = new URLSearchParams();
    if (filter.status) p.set("status", filter.status);
    if (filter.missionId) p.set("mission_id", filter.missionId);
    if (filter.limit != null) p.set("limit", String(filter.limit));
    const q = p.toString();
    return request<Deliverable[]>(
      this.base, "GET", `/api/v1/domain/deliverables${q ? "?" + q : ""}`);
  }
  /** Wave 5: one deliverable by identity. */
  deliverable(aid: string): Promise<ApiResult<Artifact>> {
    return request<Artifact>(
      this.base, "GET", `/api/v1/domain/artifacts/${encodeURIComponent(aid)}`);
  }
  connections(): Promise<ApiResult<DomainConnection[]>> {
    return request<DomainConnection[]>(this.base, "GET", "/api/v1/domain/connections");
  }
  /**
   * Wave 7: one domain connection with its capabilities and, for each, HOW it was
   * established (`provenance`: MEASURED vs ASSERTED). Open read.
   */
  connection(cid: string): Promise<ApiResult<DomainConnection>> {
    return request<DomainConnection>(
      this.base, "GET", `/api/v1/domain/connections/${encodeURIComponent(cid)}`);
  }
  needsAttention(): Promise<ApiResult<AttentionItem[]>> {
    return request<AttentionItem[]>(this.base, "GET", "/api/v1/domain/needs-attention");
  }
  /** Wave 4: the inbox with a per-item control resolution (policy vs executable). */
  attentionResolutions(): Promise<ApiResult<AttentionResolutionsDoc>> {
    return request<AttentionResolutionsDoc>(
      this.base, "GET", "/api/v1/domain/needs-attention/resolutions");
  }
  /**
   * Wave 4 — PREVIEW-FIRST resolve. `dry_run` defaults to true and the server
   * mutates nothing; the caller must send `dry_run:false` to apply. Preview and
   * apply share one server code path, so the preview cannot disagree with the
   * effect.
   */
  resolveAttention(
    token: string,
    mid: string,
    body: { action: string; dry_run?: boolean; state?: string; decided_by?: string; to_status?: string },
  ): Promise<ApiResult<ResolveResult>> {
    return request<ResolveResult>(
      this.base, "POST", `/api/v1/domain/needs-attention/${encodeURIComponent(mid)}/resolve`,
      { body, token });
  }
  /** Wave 3: is there a live WorkBuddy this host can actually launch against? */
  launchProbe(): Promise<ApiResult<LaunchProbe>> {
    return request<LaunchProbe>(this.base, "GET", "/api/v1/domain/launch/probe");
  }
  /** Wave 3: the REAL telemetry a mission's runs produced (from the session store). */
  missionWorkload(mid: string): Promise<ApiResult<MissionWorkload>> {
    return request<MissionWorkload>(
      this.base, "GET", `/api/v1/domain/missions/${encodeURIComponent(mid)}/workload`);
  }

  // --- writes (token-gated) ---
  // NOTE: an approval payload is passed through verbatim; the server owns
  // the decision. `decideApproval` only records a human's verdict.
  createProject(token: string, body: { name: string; root_path?: string; description?: string }) {
    return request<Project>(this.base, "POST", "/api/v1/domain/projects", { body, token });
  }
  createMission(
    token: string,
    body: {
      project_id: string;
      title: string;
      goal?: string;
      agent?: string;
      working_directory?: string;
      context?: string;
      permissions?: string[];
      budget_units?: number;
      connection_ref?: string | null;
    },
  ) {
    return request<Mission>(this.base, "POST", "/api/v1/domain/missions", { body, token });
  }
  transitionMission(
    token: string,
    mid: string,
    body: { to: string; reason?: string; attention_kind?: string; attention_reason?: string },
  ) {
    return request<Mission>(
      this.base, "POST", `/api/v1/domain/missions/${encodeURIComponent(mid)}/transition`,
      { body, token });
  }
  createRun(token: string, mid: string, body: { agent?: string; connection_ref?: string | null }) {
    return request<AgentRun>(
      this.base, "POST", `/api/v1/domain/missions/${encodeURIComponent(mid)}/runs`,
      { body, token });
  }
  /**
   * Wave 3 — launch a mission against a REAL agent session.
   *
   * `capture` defaults to true: the server discovers a live WorkBuddy on this
   * host and uses its real session record as the launch event. The client never
   * supplies a fabricated event -- there is no code path in this client that
   * builds one.
   */
  launchMission(
    token: string,
    mid: string,
    body: { capture?: boolean; cwd?: string; event?: Record<string, unknown> } = {},
  ) {
    return request<LaunchResult>(
      this.base, "POST", `/api/v1/domain/missions/${encodeURIComponent(mid)}/launch`,
      { body, token });
  }
  addArtifact(token: string, mid: string, body: Record<string, unknown>) {
    return request<Artifact>(
      this.base, "POST", `/api/v1/domain/missions/${encodeURIComponent(mid)}/artifacts`,
      { body, token });
  }
  /** Wave 5: record a human verdict on a deliverable. `status` must be VERIFIED
   * or REJECTED, and `decided_by` must name the human. A verdict is final. */
  verifyDeliverable(token: string, aid: string,
                    body: { status: "VERIFIED" | "REJECTED"; decided_by: string; note?: string }) {
    return request<Artifact>(
      this.base, "POST", `/api/v1/domain/artifacts/${encodeURIComponent(aid)}/verify`,
      { body, token });
  }
  upsertConnection(token: string, body: Record<string, unknown>) {
    return request<DomainConnection>(
      this.base, "POST", "/api/v1/domain/connections", { body, token });
  }
  setCapability(token: string, cid: string, body: { kind: string; detail?: string; evidence_ref?: string | null }) {
    return request<DomainCapability>(
      this.base, "POST",
      `/api/v1/domain/connections/${encodeURIComponent(cid)}/capability`, { body, token });
  }
  /**
   * Wave 7 — VERIFY a connection by running a REAL read-only probe server-side.
   *
   * The body selects *which* probe runs; it can never state the outcome. Any
   * `kind` sent here is ignored by the server — the recorded capability is what
   * the probe OBSERVED. In v1.3 that ceiling is `ADAPTER_CONTRACT_LIVE`, because
   * actuation is off and no control path is exercised.
   */
  verifyConnection(token: string, cid: string, body: { probe?: string } = {}) {
    return request<ConnectionVerification>(
      this.base, "POST",
      `/api/v1/domain/connections/${encodeURIComponent(cid)}/verify`, { body, token });
  }
  createApproval(token: string, mid: string, body: Record<string, unknown>) {
    return request<ApprovalRecord>(
      this.base, "POST", `/api/v1/domain/missions/${encodeURIComponent(mid)}/approvals`,
      { body, token });
  }
  decideApproval(token: string, aid: string, body: { state: string; decided_by: string }) {
    return request<ApprovalRecord>(
      this.base, "POST", `/api/v1/domain/approvals/${encodeURIComponent(aid)}/decide`,
      { body, token });
  }
}

/**
 * Formatting helpers shared by the domain views.
 *
 * Kept here (not in a component) so every surface renders a timestamp the same
 * way and so the "no data" case is handled in one place.
 */
export function fmtTime(ts: number | null | undefined): string {
  if (!ts || !Number.isFinite(ts)) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export function fmtRelative(ts: number | null | undefined, now = Date.now()): string {
  if (!ts || !Number.isFinite(ts)) return "—";
  const secs = Math.floor(now / 1000 - ts);
  if (secs < 0) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}
