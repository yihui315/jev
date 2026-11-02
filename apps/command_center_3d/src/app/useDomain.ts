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
  DOMAIN_LIST_LIMIT,
  DomainApi,
  asProjectRootRejection,
  type AttentionItem,
  type Deliverable,
  type DomainCounts,
  type HomeSummary,
  type Mission,
  type Project,
  type ProjectRootRejection,
} from "./domainApi";
import { classifyTruncation, type TruncationState } from "./truncation";

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

  /**
   * Wave 9.3 — uncapped domain totals, or `null` when the counts read failed.
   *
   * `null` is the important case: it means "we could not find out how many there
   * are", which must never be rendered as "this is all of them". The views use
   * `missionsTruncation` / `deliverablesTruncation` rather than reading these
   * numbers directly, so the classification rule lives in one place.
   */
  counts: DomainCounts | null;
  countsError: string | null;
  /** Is the missions list a partial view of the domain? */
  missionsTruncation: TruncationState;
  /** Is the deliverables list a partial view of the domain? */
  deliverablesTruncation: TruncationState;
  /** The ceiling the lists above were requested with. */
  listCeiling: number;

  refresh: () => Promise<void>;
  reloadProjects: () => Promise<void>;
  /** Create a project. Returns the project, or null on failure.
   *
   * RISK-1: on failure the REASON is left in `projectError` rather than
   * discarded. The server answers a rejected project root with one of FOUR
   * specific codes -- `PROJECT_ROOT_OUT_OF_SCOPE` ("you may not have this"),
   * `PROJECT_ROOT_MISSING` ("go create it"), `PROJECT_ROOT_NOT_ABSOLUTE` and
   * `PROJECT_ROOT_NOT_A_DIRECTORY` -- and those are not variants of one
   * message. Collapsing them into a bare `null` is how an operator ends up
   * retrying a refused traversal forever, or creating the very directory the
   * guard declined. */
  createProject: (name: string, rootPath: string) => Promise<Project | null>;
  /** The last project-creation failure, already parsed when it was a path
   * rejection. Null after a success. */
  projectError: string | null;
  /** The parsed form of `projectError` when the server refused a project root. */
  projectRejection: ProjectRootRejection | null;
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

  // RISK-1 -- the last refused project root, kept so the UI can name the allowed
  // roots next to the refusal. Separate from `projectsError` (a failure to READ
  // the list) because the two mean different things to the operator.
  const [projectError, setProjectError] = useState<string | null>(null);
  const projectRejection = useMemo(
    () => asProjectRootRejection(projectError),
    [projectError],
  );

  const [missions, setMissions] = useState<Mission[]>([]);
  const [missionsError, setMissionsError] = useState<string | null>(null);
  const [missionsLoading, setMissionsLoading] = useState(true);

  const [deliverables, setDeliverables] = useState<Deliverable[]>([]);
  const [deliverablesError, setDeliverablesError] = useState<string | null>(null);

  const [attention, setAttention] = useState<AttentionItem[] | null>(null);
  const [attentionError, setAttentionError] = useState<string | null>(null);
  const [attentionLoading, setAttentionLoading] = useState(true);

  const [counts, setCounts] = useState<DomainCounts | null>(null);
  const [countsError, setCountsError] = useState<string | null>(null);

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
    const [h, p, m, a, dd, ct] = await Promise.all([
      api.home(),
      api.projects(),
      api.missions({ limit: DOMAIN_LIST_LIMIT }),
      api.needsAttention(),
      api.deliverables({ limit: DOMAIN_LIST_LIMIT }),
      api.counts(),
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

    if (ct.ok && ct.data) {
      setCounts(ct.data);
      setCountsError(null);
    } else {
      // NOT setCounts(null-as-zeros): `null` already means "we could not find
      // out", and the truncation classifier turns that into "this list may be
      // incomplete" rather than into a comfortable "that's all of them".
      setCountsError(ct.error ?? "domain totals unavailable");
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
      if (!token) {
        // RISK-1: an absent token is a refusal too, and it must SAY so. Returning
        // a bare null here made the dialog claim "if the path was refused, the
        // reason is shown below" and then show nothing, because the one failure
        // that is definitely not a path problem was the one that stayed silent.
        setProjectError(
          "No control token is set, so the project was never submitted. " +
            "This is a missing credential, not a problem with the path.",
        );
        return null;
      }
      const r = await api.createProject(token, { name, root_path: rootPath });
      if (!r.ok || !r.data) {
        // RISK-1: keep the server's reason. Swallowing it here would leave the
        // operator with a form that simply refuses to submit and no idea which
        // of four very different problems they have.
        setProjectError(r.error ?? "project could not be created");
        return null;
      }
      setProjectError(null);
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

  // The classification is computed once, here, so no view can invent its own
  // rule for what "partial" means.
  const missionsTruncation = useMemo(
    () => classifyTruncation(missions.length, counts ? counts.missions : null,
                             DOMAIN_LIST_LIMIT),
    [missions.length, counts],
  );
  const deliverablesTruncation = useMemo(
    () => classifyTruncation(deliverables.length,
                             counts ? counts.deliverables : null,
                             DOMAIN_LIST_LIMIT),
    [deliverables.length, counts],
  );

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
    counts,
    countsError,
    missionsTruncation,
    deliverablesTruncation,
    listCeiling: DOMAIN_LIST_LIMIT,
    refresh,
    reloadProjects,
    createProject,
    projectError,
    projectRejection,
  };
}
