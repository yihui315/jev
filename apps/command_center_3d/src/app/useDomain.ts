/**
 * useDomain — the single owner of v1.3 domain data on the client.
 *
 * Mirrors the SceneStore discipline: one hook holds projects / missions /
 * attention / home and the app shell passes slices down. Views never fetch
 * independently, so there is exactly one answer to "how many missions are
 * running" at any moment.
 *
 * Honesty rules implemented here:
 *  - `attention` stays `null` until a successful load; it is never defaulted to
 *    `[]`. `null` renders as UNKNOWN, `[]` renders as "inbox zero". Collapsing
 *    the two is how an outage looks identical to a healthy quiet system.
 *  - Every load records its own error; a failed refresh does NOT wipe the last
 *    good data, but it does set `stale: true` so the UI can say so.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DomainApi,
  type AttentionItem,
  type Deliverable,
  type HomeSummary,
  type Mission,
  type Project,
} from "./domainApi";

const POLL_MS = 5000;

export interface DomainState {
  api: DomainApi;
  home: HomeSummary | null;
  homeError: string | null;
  homeLoading: boolean;
  homeLoadedAt: number | null;

  projects: Project[];
  projectsError: string | null;
  projectsLoading: boolean;

  missions: Mission[];
  missionsError: string | null;
  missionsLoading: boolean;

  /** Wave 6: deliverables feed the operational map (which missions produced). */
  deliverables: Deliverable[];
  deliverablesError: string | null;

  /** `null` = unknown (never loaded / last load failed). `[]` = genuinely none. */
  attention: AttentionItem[] | null;
  attentionError: string | null;
  attentionLoading: boolean;

  refreshing: boolean;
  lastRefreshAt: number | null;
  /** True when the most recent refresh failed but earlier data is still shown. */
  stale: boolean;

  refresh: () => Promise<void>;
  reloadProjects: () => Promise<void>;
  createProject: (name: string, rootPath: string) => Promise<Project | null>;
}

export function useDomain(base: string, controlToken: string): DomainState {
  const api = useMemo(() => new DomainApi(base), [base]);
  const tokenRef = useRef(controlToken);
  tokenRef.current = controlToken;

  const [home, setHome] = useState<HomeSummary | null>(null);
  const [homeError, setHomeError] = useState<string | null>(null);
  const [homeLoading, setHomeLoading] = useState(true);
  const [homeLoadedAt, setHomeLoadedAt] = useState<number | null>(null);

  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(true);

  const [missions, setMissions] = useState<Mission[]>([]);
  const [missionsError, setMissionsError] = useState<string | null>(null);
  const [missionsLoading, setMissionsLoading] = useState(true);

  const [deliverables, setDeliverables] = useState<Deliverable[]>([]);
  const [deliverablesError, setDeliverablesError] = useState<string | null>(null);

  const [attention, setAttention] = useState<AttentionItem[] | null>(null);
  const [attentionError, setAttentionError] = useState<string | null>(null);
  const [attentionLoading, setAttentionLoading] = useState(true);

  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState<number | null>(null);
  const [stale, setStale] = useState(false);

  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const [h, p, m, a, dd] = await Promise.all([
      api.home(),
      api.projects(),
      api.missions({ limit: 200 }),
      api.needsAttention(),
      api.deliverables({ limit: 200 }),
    ]);
    if (!alive.current) return;

    let anyFailure = false;

    if (h.ok && h.data) {
      setHome(h.data);
      setHomeError(null);
      setHomeLoadedAt(Date.now());
    } else {
      setHomeError(h.error ?? "home summary unavailable");
      anyFailure = true;
    }

    if (p.ok && p.data) {
      setProjects(p.data);
      setProjectsError(null);
    } else {
      setProjectsError(p.error ?? "projects unavailable");
      anyFailure = true;
    }

    if (m.ok && m.data) {
      setMissions(m.data);
      setMissionsError(null);
    } else {
      setMissionsError(m.error ?? "missions unavailable");
      anyFailure = true;
    }

    if (a.ok && a.data) {
      setAttention(a.data);
      setAttentionError(null);
    } else {
      // NOTE: we do NOT setAttention([]) here — a failed poll must not look
      // like an empty inbox.
      setAttentionError(a.error ?? "attention list unavailable");
      anyFailure = true;
    }

    if (dd.ok && dd.data) {
      setDeliverables(dd.data);
      setDeliverablesError(null);
    } else {
      setDeliverablesError(dd.error ?? "deliverables unavailable");
      anyFailure = true;
    }

    setHomeLoading(false);
    setProjectsLoading(false);
    setMissionsLoading(false);
    setAttentionLoading(false);
    setRefreshing(false);
    setLastRefreshAt(Date.now());
    setStale(anyFailure);
  }, [api]);

  const reloadProjects = useCallback(async () => {
    setProjectsLoading(true);
    const r = await api.projects();
    if (!alive.current) return;
    if (r.ok && r.data) {
      setProjects(r.data);
      setProjectsError(null);
    } else {
      setProjectsError(r.error ?? "projects unavailable");
    }
    setProjectsLoading(false);
  }, [api]);

  const createProject = useCallback(
    async (name: string, rootPath: string): Promise<Project | null> => {
      const token = tokenRef.current;
      if (!token) return null;
      const r = await api.createProject(token, { name, root_path: rootPath });
      if (!r.ok || !r.data) return null;
      await reloadProjects();
      void refresh();
      return r.data;
    },
    [api, reloadProjects, refresh],
  );

  // Initial load + light polling. Polling is what keeps the attention badge and
  // the mission queue honest without an SSE channel for domain events (Wave 3
  // adds the typed stream for agent work).
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  return {
    api,
    home,
    homeError,
    homeLoading,
    homeLoadedAt,
    projects,
    projectsError,
    projectsLoading,
    missions,
    missionsError,
    missionsLoading,
    deliverables,
    deliverablesError,
    attention,
    attentionError,
    attentionLoading,
    refreshing,
    lastRefreshAt,
    stale,
    refresh,
    reloadProjects,
    createProject,
  };
}
