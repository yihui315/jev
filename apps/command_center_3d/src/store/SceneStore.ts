/**
 * SceneStore — the SINGLE source of truth for the Visual Command Center.
 *
 * Discipline (v1.2 architecture rule): the 3D world, the table view, the control
 * bar, the inspector and the timeline ALL read from this one store. There is no
 * second copy of "the agents" or "the sessions" living inside a component. A
 * scene delta (from the /api/v1/scene projection in Wave 3, or an SSE event in
 * Wave 4) is applied here ONCE and every view re-renders from the same state.
 *
 * The store is transport-agnostic: `load()` currently reads the read-only open
 * endpoints of the running control plane as a bridge; Wave 3 switches the fetch
 * target to GET /api/v1/scene (a projection, not a new truth store) and Wave 4
 * feeds `applyDelta()` from the SSE stream. No write/actuation path exists here.
 */

export interface AgentNode {
  id: string;
  adapterState: string;
  aep: boolean;
  verified: boolean;
}

export interface SessionNode {
  id: string;
  agent: string;
  project: string;
  status: string;
  riskBand?: string;
}

export interface DecisionNode {
  directive_id: string;
  action: string;
  session_id: string;
  acted: boolean;
  signed: boolean;
}

export interface SceneState {
  agents: AgentNode[];
  sessions: SessionNode[];
  decisions: DecisionNode[];
  health: Record<string, unknown> | null;
  loaded: boolean;
  error: string | null;
  /** UI-only state (filtering / selection / operator mode) — still one store. */
  selectedSessionId: string | null;
  agentFilter: string | null;
}

const KNOWN_AGENTS = ["workbuddy", "claude", "codex", "hermes"];

type Listener = () => void;

export class SceneStore {
  private state: SceneState = {
    agents: [],
    sessions: [],
    decisions: [],
    health: null,
    loaded: false,
    error: null,
    selectedSessionId: null,
    agentFilter: null,
  };
  private listeners = new Set<Listener>();

  /** getSnapshot must return a stable reference between renders (useSyncExternalStore). */
  getSnapshot = (): SceneState => this.state;

  subscribe = (cb: Listener): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private set(next: Partial<SceneState>): void {
    this.state = { ...this.state, ...next };
    this.listeners.forEach((l) => l());
  }

  selectSession(id: string | null): void {
    this.set({ selectedSessionId: id });
  }

  setAgentFilter(agent: string | null): void {
    this.set({ agentFilter: agent });
  }

  /** Bridge loader — read-only open endpoints. Replaced by /api/v1/scene in Wave 3. */
  async load(base: string): Promise<void> {
    try {
      const [health, sessions, decisions] = await Promise.all([
        fetch(`${base}/api/health`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${base}/api/sessions`).then((r) => (r.ok ? r.json() : [])),
        fetch(`${base}/api/decisions`).then((r) => (r.ok ? r.json() : [])),
      ]);

      const agentIds = new Set<string>(KNOWN_AGENTS);
      (sessions as Array<{ agent?: string }>).forEach((s) => {
        if (s.agent) agentIds.add(s.agent);
      });

      const agents: AgentNode[] = [...agentIds].map((id) => ({
        id,
        adapterState: "?",
        aep: false,
        verified: false,
      }));

      this.set({
        health: (health as Record<string, unknown>) ?? null,
        agents,
        sessions: (sessions as Array<Record<string, unknown>>).map((s) => ({
          id: String(s.session_id ?? ""),
          agent: String(s.agent ?? ""),
          project: String(s.project ?? ""),
          status: String(s.status ?? ""),
          riskBand:
            (s.current_assessment as { band?: string } | undefined)?.band ?? undefined,
        })),
        decisions: (decisions as Array<Record<string, unknown>>).map((d) => ({
          directive_id: String(d.directive_id ?? ""),
          action: String(d.action ?? ""),
          session_id: String(d.session_id ?? ""),
          acted: Boolean(d.acted),
          signed: Boolean(d.signed),
        })),
        loaded: true,
        error: null,
      });
    } catch (e) {
      this.set({
        loaded: true,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }

  /** Apply a scene delta (Wave 4 SSE). Merges, never replaces the whole tree. */
  applyDelta(delta: Partial<SceneState>): void {
    this.set(delta);
  }
}

export const sceneStore = new SceneStore();
