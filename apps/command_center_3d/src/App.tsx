import { useCallback, useEffect, useMemo, useState } from "react";
import { useSyncExternalStore } from "react";
import { sceneStore } from "./store/SceneStore";
import { CommandCenter3D } from "./scene/CommandCenter3D";
import { TableView } from "./views/TableView";
import { GraphView } from "./views/GraphView";
import { ConnectionsView } from "./views/ConnectionsView";
import { Inspector } from "./components/Inspector";
import { Timeline } from "./components/Timeline";
import { RiskRadar } from "./components/RiskRadar";
import { DecisionExplanation } from "./components/DecisionExplanation";
import { OperatorConsole } from "./components/OperatorConsole";

// v1.3 product shell
import { Sidebar } from "./app/Sidebar";
import { TopBar } from "./app/TopBar";
import { HomeView } from "./app/HomeView";
import { MissionStudio } from "./app/MissionStudio";
import { ProjectsView } from "./app/ProjectsView";
import { MissionsView } from "./app/MissionsView";
import { DeliverablesView } from "./app/DeliverablesView";
import { NeedsAttentionView } from "./app/NeedsAttentionView";
import { AuditView, DiagnosticsView, SettingsView } from "./app/SystemViews";
import { useDomain } from "./app/useDomain";
import { CommandPalette, type CommandItemSpec } from "./ui/CommandPalette";
import { TooltipProvider } from "./ui/Tooltip";
import { Badge } from "./ui/Badge";
import {
  NAV_ITEMS,
  coerceView,
  navLabel,
  showsInspectorRail,
  type ViewMode,
} from "./app/navigation";

// Read-only API base. Defaults to the v1.2/v1.3 control plane (4779). Override
// with VITE_API_BASE. Never points at the protected GREEN instance (4778).
//
// `VITE_API_BASE=same-origin` (or an empty string) collapses to "" so the
// bundle issues relative requests. That is how the product runs when the
// durable runtime serves UI and API from ONE port (Wave 8), and it is what the
// Wave 2 gate relies on — the browser must talk to the gate's own origin.
const RAW_API_BASE: string =
  (import.meta as unknown as { env?: { VITE_API_BASE?: string } }).env?.VITE_API_BASE ?? "";
const API_BASE: string =
  RAW_API_BASE === "same-origin" ? "" : RAW_API_BASE || "http://127.0.0.1:4779";

const THEME_KEY = "jcc.theme";

function readTheme(): "dark" | "light" {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage unavailable — fall through to the default */
  }
  return "dark";
}

export function App() {
  const scene = useSyncExternalStore(sceneStore.subscribe, sceneStore.getSnapshot);

  const [view, setView] = useState<ViewMode>(() => coerceView(readViewFromHash(), "home"));
  const [controlToken, setControlToken] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(readTheme);

  const domain = useDomain(API_BASE, controlToken);

  const goto = useCallback((v: ViewMode) => {
    setView(v);
    // The view lives in the store-free URL hash so a reload lands where the
    // operator left off, without the hash becoming a state source of truth.
    try {
      window.location.hash = `#/${v}`;
    } catch {
      /* non-browser environment */
    }
  }, []);

  // --- theme ---
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  // --- scene transport ---
  useEffect(() => {
    void sceneStore.load(API_BASE);
    sceneStore.connectStream(API_BASE);
    if (!sceneStore.getSnapshot().webgl2Supported) {
      // Wave 8 fallback: without WebGL2 the world has no context. Fall back to
      // the table view only when the operator is actually on the world view.
      setView((cur) => (cur === "world" ? "table" : cur));
    }
    return () => sceneStore.disconnect();
  }, []);

  // --- deep link on hash change (back/forward) ---
  useEffect(() => {
    const onHash = () => setView(coerceView(readViewFromHash(), "home"));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // --- ⌘K / Ctrl+K ---
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const paletteItems = useMemo<CommandItemSpec[]>(() => {
    const items: CommandItemSpec[] = NAV_ITEMS.map((n) => ({
      id: `nav:${n.id}`,
      label: n.label,
      hint: "go",
      group: "Navigate",
      icon: n.icon,
      keywords: [n.id, n.description],
      onSelect: () => goto(n.id),
    }));
    items.push({
      id: "action:new-mission",
      label: "New Mission",
      hint: "action",
      group: "Actions",
      keywords: ["create", "launch", "studio", "mission"],
      onSelect: () => goto("studio"),
    });
    for (const p of domain.projects.slice(0, 20)) {
      items.push({
        id: `project:${p.project_id}`,
        label: `Project · ${p.name}`,
        hint: "open",
        group: "Projects",
        keywords: [p.project_id, p.root_path],
        onSelect: () => goto("projects"),
      });
    }
    for (const m of domain.missions.slice(0, 25)) {
      items.push({
        id: `mission:${m.mission_id}`,
        label: `Mission · ${m.title}`,
        hint: m.status,
        group: "Missions",
        keywords: [m.mission_id, m.agent, m.status],
        onSelect: () => goto("missions"),
      });
    }
    items.push({
      id: "action:theme",
      label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`,
      hint: "pref",
      group: "Actions",
      onSelect: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    });
    return items;
  }, [domain.projects, domain.missions, goto, theme]);

  const rail = showsInspectorRail(view);

  const main = (() => {
    switch (view) {
      case "home":
        return (
          <HomeView
            data={{
              summary: domain.home,
              error: domain.homeError,
              loading: domain.homeLoading,
              lastLoadedAt: domain.homeLoadedAt,
            }}
            scene={scene}
            onNewMission={() => goto("studio")}
            onGotoMissions={() => goto("missions")}
            onGotoAttention={() => goto("needs-attention")}
            onGotoDeliverables={() => goto("deliverables")}
            onGotoProjects={() => goto("projects")}
            onRefresh={() => void domain.refresh()}
          />
        );
      case "projects":
        return (
          <ProjectsView
            api={domain.api}
            projects={domain.projects}
            loading={domain.projectsLoading}
            error={domain.projectsError}
            onNewMission={() => goto("studio")}
            onRefresh={() => void domain.refresh()}
          />
        );
      case "missions":
        return (
          <MissionsView
            api={domain.api}
            missions={domain.missions}
            loading={domain.missionsLoading}
            error={domain.missionsError}
            onNewMission={() => goto("studio")}
            onRefresh={() => void domain.refresh()}
          />
        );
      case "needs-attention":
        return (
          <NeedsAttentionView
            api={domain.api}
            controlToken={controlToken}
            items={domain.attention}
            loading={domain.attentionLoading}
            error={domain.attentionError}
            onRefresh={() => void domain.refresh()}
            onGotoMissions={() => goto("missions")}
          />
        );
      case "deliverables":
        return (
          <DeliverablesView
            api={domain.api}
            controlToken={controlToken}
            onRefresh={() => void domain.refresh()}
          />
        );
      case "studio":
        return (
          <MissionStudio
            api={domain.api}
            controlToken={controlToken}
            projects={domain.projects}
            loadingProjects={domain.projectsLoading}
            projectsError={domain.projectsError}
            onCreated={() => void domain.refresh()}
            onOpenProjects={() => goto("projects")}
            onCreateProject={domain.createProject}
          />
        );
      case "connections":
        return (
          <ConnectionsView scene={scene} apiBase={API_BASE} controlToken={controlToken || null} />
        );
      case "audit":
        return <AuditView base={API_BASE} />;
      case "diagnostics":
        return <DiagnosticsView base={API_BASE} api={domain.api} scene={scene} />;
      case "settings":
        return (
          <SettingsView
            controlToken={controlToken}
            onControlToken={setControlToken}
            theme={theme}
            onTheme={setTheme}
            base={API_BASE}
          />
        );
      case "table":
        return <TableView scene={scene} />;
      case "graph":
        return <GraphView scene={scene} />;
      case "world":
      default:
        return <CommandCenter3D scene={scene} />;
    }
  })();

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
        <Sidebar
          view={view}
          onSelect={goto}
          attentionCount={domain.attention ? domain.attention.length : null}
          attentionUnknown={domain.attention === null}
          onOpenPalette={() => setPaletteOpen(true)}
          mobileOpen={mobileNavOpen}
          onCloseMobile={() => setMobileNavOpen(false)}
        />

        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar
            view={view}
            connection={scene.connection}
            controlToken={controlToken}
            onControlToken={setControlToken}
            onNewMission={() => goto("studio")}
            onRefresh={() => void domain.refresh()}
            refreshing={domain.refreshing}
            onOpenMobileNav={() => setMobileNavOpen(true)}
            actuationEnabled={domain.home ? domain.home.actuation_enabled : null}
          />

          {domain.stale && (
            <div className="flex items-center gap-2 border-b border-status-warn/30 bg-status-warn/10 px-4 py-1.5 text-[11px] text-status-warn">
              <Badge variant="warn">stale</Badge>
              The last refresh failed. Showing the most recent successful data —
              counts here may be out of date.
            </div>
          )}

          <main className="relative min-h-0 flex-1">
            {/* The view is announced to assistive tech on change. */}
            <h2 className="sr-only" aria-live="polite">
              {navLabel(view)}
            </h2>
            {main}
          </main>
        </div>

        {rail && (
          <aside className="hidden w-[340px] shrink-0 flex-col overflow-y-auto border-l border-border xl:flex">
            <div className="border-b border-border">
              <Inspector scene={scene} />
            </div>
            <div className="border-b border-border">
              <RiskRadar scene={scene} />
            </div>
            <div className="border-b border-border">
              <DecisionExplanation scene={scene} />
            </div>
            <OperatorConsole scene={scene} />
            <div className="border-t border-border">
              <Timeline scene={scene} />
            </div>
          </aside>
        )}

        <CommandPalette
          open={paletteOpen}
          onOpenChange={setPaletteOpen}
          items={paletteItems}
        />
      </div>
    </TooltipProvider>
  );
}

/**
 * Read the view from the URL hash (`#/world`).
 *
 * The hash is a convenience, never a source of truth: `coerceView` maps any
 * unrecognised value back to Home, so a hand-edited or stale hash can never
 * blank the product.
 */
function readViewFromHash(): string | null {
  try {
    const h = window.location.hash.replace(/^#\/?/, "").trim();
    return h || null;
  } catch {
    return null;
  }
}
