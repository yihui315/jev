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
    // Token travels in headers only. Never in the URL, never persisted.
    //
    // Both forms are sent because the control plane accepts either and a proxy
    // in front of the port may read only one. Constructed by the SAME helper the
    // rest of the app uses, so a change to the header contract is a one-line
    // edit rather than a hunt through six call sites.
    Object.assign(headers, authHeaders(token ?? ""));
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
  /** RISK-1 — what the operator TYPED, echoed for display only.
   *
   * `root_path` is the CANONICAL value the server resolved and stored; this is
   * the raw submission. Keeping both means the UI can show "you typed X, we
   * stored Y" instead of silently rewriting the operator's input, which is the
   * behaviour that makes a canonicalisation bug invisible. Absent on older
   * servers. */
  root_path_raw?: string;
  root_path_canonical?: string;
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
  /**
   * Wave 9.2 — a DIFFERENT question from the rest of this payload.
   *
   * `workbuddy` above answers "is there a live session I could ATTACH to".
   * `native_launch` answers "can this host actually START a workload". They are
   * separate readings and are never merged; the second is a measurement and
   * reports UNKNOWN when it has not been taken.
   */
  native_launch: LaunchCapability;
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

/** Wave 9.2 — what this host can ACTUALLY do with a launch. Measured, not asserted. */
export interface LaunchCapability {
  schema: string;
  capability: string;
  agent: string;
  template_id: string | null;
  capability_version: string;
  spawn_ready: boolean;
  spawn_problem: string | null;
  resolved_head_len: number;
  signing_available: boolean;
  /** UNKNOWN is never green: an unmeasured capability is not a success. */
  executability: "LOCAL_SPAWN_READY" | "HOST_ACTIVATION_PENDING" | "UNKNOWN";
  end_to_end_verified: boolean;
  last_measured: {
    classification: string | null;
    session_discovered?: boolean;
    auth_error?: string | null;
    event_types?: string[];
    measured_at?: number;
    template_id?: string;
    args_hash?: string | null;
  } | null;
  autonomous_actuation: false;
  note: string;
}

/**
 * Wave 9.2 — the launch control plane's answer, for BOTH the preview call and
 * the commit call.
 *
 * `would_accept` is the ONE field the UI reads to decide whether Start Mission
 * is enabled, and it is composed server-side from the nine checks AND the
 * command buildability. `would_reason` names whichever half failed, so the UI
 * never has to explain a refusal it inferred itself.
 */
export interface LaunchRequestResult {
  schema: string;
  mission_id: string;
  project_id: string;
  committed: boolean;
  capability: string;
  capability_version: string;
  policy_version: string;
  directive: Record<string, unknown>;
  preview: {
    schema: string;
    launchable: boolean;
    problems: Array<{ code: string; detail: string }>;
    argv: string[];
    argv_goal_slot: number | null;
    project_root_bound: boolean;
    args_hash: string | null;
    shell: false;
    actuation: false;
    note: string;
  };
  checks_ok: boolean;
  command_buildable: boolean;
  preview_problems: string[];
  would_accept: boolean;
  would_reason: string;
  steps: LaunchStep[];
  actuation: false;
  elapsed_s: number;
  note?: string;
  // --- commit-only fields ---
  spawned?: boolean;
  pid?: number | null;
  classification?: "REFUSED" | "AUTH_REQUIRED" | "LAUNCHED" | "STARTED_NO_TELEMETRY";
  reason?: string | null;
  stream?: {
    event_count: number;
    event_types: string[];
    unparseable_lines: number;
    auth_error: string | null;
    exited: boolean;
    exit_code: number | null;
    stdout_file: string | null;
    job_object: string;
  };
  aep?: {
    session_discovered: boolean;
    session_id: string | null;
    ingested: boolean;
    classification: string | null;
    error?: string;
  };
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
  /** RISK-1 — the project-root boundary, as the server states it.
   *
   * Optional so a build against an older server renders "UNKNOWN" rather than
   * crashing: the UI must never invent a boundary it was not told (the same
   * rule `actuation_enabled` follows). */
  project_scope?: ProjectScope;
}

/** RISK-1 — what a project root is allowed to be, and why one was refused.
 *
 * `allowed_project_roots` is the whole point of shipping this: an operator who
 * cannot see the boundary cannot fix a rejection, and a UI that renders a
 * refusal without naming the allowed set teaches the operator to retry blindly. */
export interface ProjectScope {
  schema: string;
  allowed_project_roots: string[];
  reason_codes: string[];
  fail_closed: boolean;
  stored_value: string;
  note?: string;
}

/** RISK-1 — a refused project root, parsed out of the 422 detail.
 *
 * The server returns `{code, detail, field, raw, allowed_project_roots}` for a
 * rejected path. `code` is what the UI must branch on: `PROJECT_ROOT_MISSING`
 * ("create it") and `PROJECT_ROOT_OUT_OF_SCOPE` ("you may not have it") are
 * opposite instructions, and collapsing them into one generic error is how a
 * refused traversal ends up looking like a typo to the person fixing it. */
export interface ProjectRootRejection {
  code: string;
  detail: string;
  field: string;
  raw: string;
  allowed_project_roots: string[];
}

/** Parse a write failure into a `ProjectRootRejection`, or null if it is not one.
 *
 * Returns null for every other failure (401, 403, network, 500) so the caller
 * keeps its existing handling instead of mislabelling an auth failure as a
 * path problem.
 *
 * THE SHAPE IS NOT GUESSED. `request()` does not hand this function the raw
 * response body: `extractDetail()` has already unwrapped it, and for a
 * non-string `detail` it returns `JSON.stringify(detail)` -- i.e. the code sits
 * at the TOP level of this string, not under a second `detail` key. Both
 * spellings are accepted so the parser is correct whether it is handed the
 * unwrapped string (the production path) or a whole body (a fixture, a future
 * caller that logs envelopes). Accepting only the nested form is a parser that
 * returns `null` for every real refusal: the UI then silently falls back to a
 * generic error and the operator is back to guessing.
 */
export function asProjectRootRejection(error: string | null): ProjectRootRejection | null {
  if (!error) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(error);
  } catch {
    // Not JSON — a plain-string detail from some other endpoint.
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const top = parsed as Partial<ProjectRootRejection>;
  const nested = (top as { detail?: unknown }).detail;
  // Prefer the unwrapped top level; fall back to a nested `detail` object.
  const d =
    typeof top.code === "string" && typeof top.detail === "string"
      ? top
      : nested && typeof nested === "object"
        ? (nested as Partial<ProjectRootRejection>)
        : null;
  if (!d || typeof d.code !== "string" || typeof d.detail !== "string") return null;
  return {
    code: d.code,
    detail: d.detail,
    field: typeof d.field === "string" && d.field ? d.field : "root_path",
    raw: typeof d.raw === "string" ? d.raw : "",
    allowed_project_roots: Array.isArray(d.allowed_project_roots)
      ? d.allowed_project_roots.map(String)
      : [],
  };
}

/**
 * Wave-launch — a refused REAL-agent launch, parsed out of the 409 detail.
 *
 * The launch route refuses with a structured `{code, detail, ...}` body (see
 * `workbuddy_launch.py` and `server.py`). `code` is what the UI must branch on:
 * `WORKBUDDY_SESSION_SCOPE_MISMATCH` ("no live session in the project dir") and
 * `WORKBUDDY_NATIVE_NOT_STARTED` ("spawn was attempted but did not come up") are
 * opposite instructions, and collapsing them into one generic error is how an
 * operator ends up re-reading the same raw JSON and learns nothing.
 *
 * Returns null for every other failure (401, 403, network, 500, a plain-string
 * detail) so the caller keeps its existing `explain()` handling instead of
 * mislabelling an auth failure as a launch-scope problem.
 */
export function humanizeLaunchRejection(error: string | null | undefined): string | null {
  if (!error) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(error);
  } catch {
    return null; // a plain-string detail from some other endpoint
  }
  if (!parsed || typeof parsed !== "object") return null;
  const top = parsed as Record<string, unknown>;
  const code = typeof top.code === "string" ? top.code : "";
  if (!code) return null;
  const detail = typeof top.detail === "string" ? top.detail : "";

  if (code === "WORKBUDDY_SESSION_SCOPE_MISMATCH") {
    const req =
      typeof top.requested_cwd === "string" && top.requested_cwd
        ? top.requested_cwd
        : "";
    const got =
      typeof top.captured_cwd === "string" && top.captured_cwd
        ? top.captured_cwd
        : "";
    const scopeNote = got ? `当前活会话作用域为 ${got}。` : "";
    return (
      `没有作用域为 ${req || "该项目目录"} 的活 WorkBuddy 会话。${scopeNote}` +
      `「挂接模式」(capture) 拒绝把启动误绑到会话从未运行过的目录。` +
      `改走「原生启动」(native) 即可在 ${req || "该项目目录"} 拉起一个 mission 级工作负载；` +
      `或在该目录下运行 WorkBuddy 后重试挂接。`
    );
  }
  if (code === "WORKBUDDY_NATIVE_NOT_STARTED") {
    return `原生启动已尝试但未成功拉起工作负载：${detail || "未见进程启动"}。请检查 WorkBuddy 可执行文件与主机激活状态后重试。`;
  }
  if (code === "no_live_workbuddy_session") {
    return "未发现任何活 WorkBuddy 会话，无法挂接。请先启动 WorkBuddy，或在「原生启动」模式下重试。";
  }
  if (code === "no_workbuddy_session") {
    return "会话目录下没有任何 WorkBuddy 会话记录。请先启动 WorkBuddy，或在「原生启动」模式下重试。";
  }
  return null; // fall through to the generic explain()
}

/** RISK-1 — the four refusals, each with the instruction the operator needs.
 *
 * Kept as DATA with one entry per code, because these are four different
 * problems: two say "not there, create it", one says "you may not have it",
 * one says "a file is not a working directory". An operator who is told only
 * "invalid path" retries; an operator who is told which of the four it is can
 * finish. The prose is the UI's own wording -- the server's `detail` is shown
 * beneath it verbatim as the audit-grade statement, never replaced by it.
 *
 * `fix` is the action. `blame` is the honest answer to "whose fault is this",
 * because two of the four are a configuration problem the operator cannot fix
 * from this screen and saying otherwise sends them hunting the wrong thing. */
export const PROJECT_ROOT_REJECTION_HELP: Readonly<
  Record<string, { title: string; fix: string; blame: "operator" | "server" }>
> = {
  PROJECT_ROOT_OUT_OF_SCOPE: {
    title: "Outside the allowed project roots",
    fix: "Pick a directory that sits inside one of the allowed roots listed above. Paths that reach elsewhere by .. or through a link are refused even when the target exists.",
    blame: "operator",
  },
  PROJECT_ROOT_MISSING: {
    title: "Inside an allowed root, but nothing is there",
    fix: "Create the directory first, or give the full path of a directory that already exists.",
    blame: "operator",
  },
  PROJECT_ROOT_NOT_ABSOLUTE: {
    title: "Not a full path from a drive or UNC root",
    fix: "Enter the complete absolute path (for example D:\\workspace\\my-project). A relative fragment is refused rather than resolved against wherever the server happens to be running.",
    blame: "operator",
  },
  PROJECT_ROOT_NOT_A_DIRECTORY: {
    title: "That path is a file, not a directory",
    fix: "Give the folder that contains your work, not a document inside it.",
    blame: "operator",
  },
  /** An empty allow-list refuses everything. Not a code the server emits -- it
   *  is what the refusal MEANS when `fail_closed` has nothing configured, and
   *  the operator's only fix is a server-side config change. */
  NO_PROJECT_ROOTS_CONFIGURED: {
    title: "No project roots are configured on the server",
    fix: "Every project root is refused until an operator sets launcher.project_scope_base (config) or JCC_ALLOWED_PROJECT_ROOTS (environment). This cannot be fixed from this screen.",
    blame: "server",
  },
};

/** The human-readable guidance for a refusal code, or null if we do not know it.
 *
 * An unknown code returns null rather than a generic string, so a future server
 * reason shows its own text instead of being mislabelled with today's advice. */
export function projectRootRejectionHelp(
  code: string,
): { title: string; fix: string; blame: "operator" | "server" } | null {
  return PROJECT_ROOT_REJECTION_HELP[code] ?? null;
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

/**
 * Wave 9.3 — uncapped domain totals (`GET /api/v1/domain/counts`).
 *
 * Present so the UI can tell a bounded list from a complete one. A count that
 * could not be read must render as UNKNOWN, never as zero — the same rule the
 * attention badge already follows.
 */
export interface DomainCounts {
  schema: string;
  projects: number;
  archived_projects: number;
  missions: number;
  deliverables: number;
}

/**
 * The ceiling the UI asks the bounded list reads for. It is deliberately NOT
 * the server's default (200): asking for the server default made the client's
 * own bound invisible, which is the defect Wave 9.3 fixes. It is also
 * deliberately not "no limit" — an unbounded read is its own defect, and the
 * `counts()` read above is how the UI knows when even this ceiling was hit.
 */
export const DOMAIN_LIST_LIMIT = 1000;

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
  /**
   * Wave 9.3: the UNCAPPED domain totals.
   *
   * The list reads below are bounded (`DOMAIN_LIST_LIMIT`), which is correct —
   * an unbounded read is its own defect. What was wrong is that the UI had no
   * way to tell a truncated list from a complete one, so a workspace with 500
   * missions rendered 200 of them and looked identical to a workspace with 200.
   * This is the read that makes the bound visible.
   */
  counts(): Promise<ApiResult<DomainCounts>> {
    return request<DomainCounts>(this.base, "GET", "/api/v1/domain/counts");
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
  /**
   * Wave 9.2 — PREVIEW a real launch.
   *
   * Spawns nothing and does NOT spend the single-use directive nonce, so it is
   * safe to call on render and again immediately before committing. The server
   * composes `would_accept` from the nine checks AND the command buildability;
   * this client never re-derives that answer.
   */
  launchRequest(mid: string, token: string, body: { model?: string } = {}) {
    return request<LaunchRequestResult>(
      this.base, "POST",
      `/api/v1/domain/missions/${encodeURIComponent(mid)}/launch-request`,
      { body, token });
  }
  /**
   * Wave 9.2 — COMMIT a launch. The only call in this client that starts a
   * process on the host.
   *
   * `classification` in the response is the child's OWN verdict, so a run that
   * started and then failed to authenticate comes back as `AUTH_REQUIRED` and
   * must be rendered as a host-activation blocker -- never as a running mission.
   */
  launchCommit(mid: string, token: string, body: { model?: string } = {}) {
    return request<LaunchRequestResult>(
      this.base, "POST",
      `/api/v1/domain/missions/${encodeURIComponent(mid)}/launch-commit`,
      { body, token });
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
    body: {
      capture?: boolean;
      native?: boolean;
      cwd?: string;
      event?: Record<string, unknown>;
    } = {},
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
import { authHeaders } from "../lib/authorizedFetch";
