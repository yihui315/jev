/**
 * SceneStore — the SINGLE source of truth for the Visual Command Center.
 *
 * Discipline (v1.2 architecture rule): the 3D world, the graph view, the table
 * view, the inspector, the timeline and the filters ALL read from this one
 * store. There is no second copy of "the agents" or "the sessions" inside a
 * component. A scene delta (from GET /api/v1/scene in Wave 3, or an SSE event
 * in Wave 4) is applied here ONCE and every view re-renders from the same state.
 *
 * Transport (read-only, no write/actuation path):
 *  - `load()`            fetches GET /api/v1/scene (the jcc-scene/1 projection).
 *                        Falls back to the v1.1 bridge endpoints if that route
 *                        is absent (older control plane), so the world still
 *                        renders against a real server.
 *  - `connectStream()`  opens GET /api/v1/events/stream (SSE). Every typed
 *                        event becomes a pulse on its agent + a timeline entry.
 *                        On disconnect it falls back to polling /api/v1/scene.
 */
import {
  EMPTY_SCENE,
  mapScene,
  type ConnectionState,
  type SceneEvent,
  type SceneState,
  type ViewMode,
} from "./sceneMap";

const EVENT_TYPES = [
  "session.created",
  "session.updated",
  "assessment.created",
  "directive.created",
  "directive.acted",
  "audit.appended",
  "provider.changed",
  "budget.changed",
  "shadow.changed",
  "agent.changed",
] as const;

type Listener = () => void;

export class SceneStore {
  private state: SceneState = { ...EMPTY_SCENE };
  private listeners = new Set<Listener>();
  private es: EventSource | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private apiBase = "http://127.0.0.1:4779";

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

  // --- UI actions (still one store) --------------------------------------
  selectSession(id: string | null): void {
    this.set({ selectedSessionId: id });
  }
  setAgentFilter(agent: string | null): void {
    this.set({ agentFilter: agent });
  }
  setStatusFilter(status: string | null): void {
    this.set({ statusFilter: status });
  }
  setView(view: ViewMode): void {
    this.set({ view });
  }

  // --- live event application (Wave 4 SSE) -------------------------------
  private agentOfSession(sid: string | undefined): string | undefined {
    if (!sid) return undefined;
    return this.state.sessions.find((s) => s.id === sid)?.agent;
  }

  private pulseAgent(agentId: string | undefined): void {
    if (!agentId) return;
    this.set({ pulses: { ...this.state.pulses, [agentId]: Date.now() } });
  }

  private pushEvent(ev: SceneEvent): void {
    const events = [ev, ...this.state.events].slice(0, 200);
    this.set({ events });
  }

  /** Apply one typed SSE event. Never throws; bad payloads are skipped. */
  applyEvent(type: string, data: Record<string, unknown>, eventId: string): void {
    const ev: SceneEvent = { eventId, type, ts: Date.now(), data };
    this.pushEvent(ev);
    const agent =
      (typeof data.agent === "string" ? data.agent : undefined) ??
      this.agentOfSession(typeof data.session_id === "string" ? data.session_id : undefined);
    if (
      type.startsWith("session.") ||
      type === "assessment.created" ||
      type === "agent.changed"
    ) {
      this.pulseAgent(agent);
    }
  }

  // --- full load (Wave 3 projection) ------------------------------------
  async load(base: string): Promise<void> {
    this.apiBase = base;
    try {
      const res = await fetch(`${base}/api/v1/scene`);
      if (res.ok) {
        const doc = await res.json();
        const mapped = mapScene(doc);
        this.set({ ...mapped, connection: this.state.connection === "OFFLINE" ? "LIVE" : this.state.connection });
        return;
      }
    } catch {
      /* fall through to bridge */
    }
    await this.loadBridge(base);
  }

  /** Resilience path for a control plane without /api/v1/scene (v1.1). */
  private async loadBridge(base: string): Promise<void> {
    try {
      const [health, sessions, decisions] = await Promise.all([
        fetch(`${base}/api/health`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${base}/api/sessions`).then((r) => (r.ok ? r.json() : [])),
        fetch(`${base}/api/decisions`).then((r) => (r.ok ? r.json() : [])),
      ]);
      const doc = {
        schema_version: "bridge/0",
        system: { source: "LIVE", data: health ?? {} },
        agents: {
          source: "DERIVED",
          data: { agents: deriveAgentsFromSessions(sessions as Array<Record<string, unknown>>) },
        },
        sessions: { source: "LIVE", data: sessions },
        directives: { source: "LIVE", data: decisions },
      };
      const mapped = mapScene(doc);
      this.set({ ...mapped, connection: this.state.connection === "OFFLINE" ? "LIVE" : this.state.connection });
    } catch (e) {
      this.set({
        loaded: true,
        error: e instanceof Error ? e.message : String(e),
        connection: "OFFLINE",
      });
    }
  }

  // --- SSE connection (Wave 4) ------------------------------------------
  connectStream(base: string): void {
    this.apiBase = base;
    this.set({ connection: "CONNECTING" });
    try {
      const es = new EventSource(`${base}/api/v1/events/stream`);
      this.es = es;
      es.onopen = () => this.set({ connection: "LIVE" });
      for (const type of EVENT_TYPES) {
        es.addEventListener(type, (ev: Event) => {
          try {
            const data = (ev as MessageEvent).data;
            const frame = JSON.parse(data) as { event_id?: string; data?: Record<string, unknown> };
            this.applyEvent(type, frame.data ?? {}, frame.event_id ?? `${type}:${Date.now()}`);
          } catch {
            /* ignore malformed frame */
          }
        });
      }
      es.onerror = () => {
        // EventSource retries with backoff on its own; if it cannot recover we
        // switch to polling so the world never freezes on OFFLINE.
        this.set({ connection: "RECONNECTING" });
        this.startPolling(base);
      };
    } catch {
      this.set({ connection: "OFFLINE" });
      this.startPolling(base);
    }
  }

  private startPolling(base: string): void {
    if (this.pollTimer !== null) return;
    this.set({ connection: "POLLING" });
    this.pollTimer = setInterval(() => {
      void this.load(base);
    }, 5000);
  }

  disconnect(): void {
    if (this.es) {
      this.es.close();
      this.es = null;
    }
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    this.set({ connection: "OFFLINE" });
  }

  get currentBase(): string {
    return this.apiBase;
  }
}

/** Minimal mirror of server `_agents_from_sessions` for the bridge fallback. */
function deriveAgentsFromSessions(
  sessions: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const byAgent = new Map<string, Record<string, unknown>>();
  for (const s of sessions) {
    const agent = String(s.agent ?? "unknown");
    const bucket =
      byAgent.get(agent) ??
      {
        agent,
        session_count: 0,
        statuses: {} as Record<string, number>,
        session_ids: [] as string[],
        latest_risk_band: "unknown",
      };
    bucket.session_count = Number(bucket.session_count) + 1;
    const st = String(s.status ?? "unknown");
    const statuses = bucket.statuses as Record<string, number>;
    statuses[st] = (statuses[st] ?? 0) + 1;
    (bucket.session_ids as string[]).push(String(s.session_id ?? ""));
    const band = String((s.current_assessment as Record<string, unknown> | undefined)?.band ?? "unknown");
    if (band === "critical" || band === "high") bucket.latest_risk_band = band;
    byAgent.set(agent, bucket);
  }
  return [...byAgent.values()];
}

export const sceneStore = new SceneStore();
export type { ConnectionState, SceneEvent, SceneState, ViewMode };
