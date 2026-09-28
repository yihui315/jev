/**
 * navigation — the single registry of product surfaces (Wave 2).
 *
 * A view exists in exactly ONE place. The sidebar, the ⌘K palette, the mobile
 * drawer and any deep-link helper all read from `NAV_ITEMS`; none of them keeps
 * its own list. Adding a view means adding one row here, and every entry point
 * gains it at once.
 *
 * This module is pure (no React, no fetch) so it is unit-testable and so the
 * router below can be reasoned about without rendering anything.
 */
import {
  Activity,
  AlertTriangle,
  Boxes,
  FolderKanban,
  Globe2,
  Home,
  LayoutPanelLeft,
  ListChecks,
  Plug,
  Rocket,
  ScrollText,
  Settings,
  Stethoscope,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

/**
 * Product views.
 *
 * The first four (`world`/`graph`/`table`/`connections`) are the v1.2 views and
 * keep their exact ids so existing bookmarks, tests and the SceneStore contract
 * stay valid. The rest are the v1.3 product surfaces.
 */
export type ViewMode =
  | "home"
  | "projects"
  | "missions"
  | "needs-attention"
  | "deliverables"
  | "studio"
  | "world"
  | "graph"
  | "table"
  | "connections"
  | "audit"
  | "diagnostics"
  | "settings";

/** Sidebar grouping. Order inside a group is the display order. */
export type NavGroup = "Work" | "Control" | "System";

export interface NavItem {
  id: ViewMode;
  label: string;
  /** Short description surfaced in tooltips and the palette. */
  description: string;
  group: NavGroup;
  icon: LucideIcon;
  /** False when the view is reached only via a CTA / palette entry. */
  inSidebar: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    id: "home",
    label: "Home",
    description: "Active projects, running missions, what needs you now",
    group: "Work",
    icon: Home,
    inSidebar: true,
  },
  {
    id: "projects",
    label: "Projects",
    description: "Every project and the missions launched under it",
    group: "Work",
    icon: FolderKanban,
    inSidebar: true,
  },
  {
    id: "missions",
    label: "Missions",
    description: "Mission queue with authoritative lifecycle state",
    group: "Work",
    icon: ListChecks,
    inSidebar: true,
  },
  {
    id: "needs-attention",
    label: "Needs Attention",
    description: "Approvals, escalations, stuck agents and blocked work",
    group: "Work",
    icon: AlertTriangle,
    inSidebar: true,
  },
  {
    id: "deliverables",
    label: "Deliverables",
    description: "Artifacts produced by missions, with provenance",
    group: "Work",
    icon: Boxes,
    inSidebar: true,
  },
  {
    id: "studio",
    label: "New Mission",
    description: "Mission Studio — goal, project, agent, budget, launch",
    group: "Work",
    icon: Rocket,
    inSidebar: true,
  },
  {
    id: "world",
    label: "World",
    description: "3D operational view of projects, agents and sessions",
    group: "Control",
    icon: Globe2,
    inSidebar: true,
  },
  {
    id: "graph",
    label: "Graph",
    description: "Agent / session relationship graph",
    group: "Control",
    icon: LayoutPanelLeft,
    inSidebar: true,
  },
  {
    id: "table",
    label: "Table",
    description: "Flat, GPU-free session table (WebGL fallback)",
    group: "Control",
    icon: Activity,
    inSidebar: true,
  },
  {
    id: "connections",
    label: "Connections",
    description: "Agent connections, capability probes and telemetry",
    group: "Control",
    icon: Plug,
    inSidebar: true,
  },
  {
    id: "audit",
    label: "Audit",
    description: "Append-only decision and directive tape",
    group: "System",
    icon: ScrollText,
    inSidebar: true,
  },
  {
    id: "diagnostics",
    label: "Diagnostics",
    description: "Runtime health, gates and honest system status",
    group: "System",
    icon: Stethoscope,
    inSidebar: true,
  },
  {
    id: "settings",
    label: "Settings",
    description: "Control token, theme and local product preferences",
    group: "System",
    icon: Settings,
    inSidebar: true,
  },
] as const;

export const NAV_GROUPS: readonly NavGroup[] = ["Work", "Control", "System"];

const BY_ID: Record<string, NavItem> = Object.fromEntries(
  NAV_ITEMS.map((n) => [n.id, n]),
);

/** The full ordered list of valid view ids. */
export const VIEW_IDS: readonly ViewMode[] = NAV_ITEMS.map((n) => n.id);

export function navItem(id: ViewMode): NavItem | undefined {
  return BY_ID[id];
}

export function navLabel(id: ViewMode): string {
  return BY_ID[id]?.label ?? id;
}

export function navItemsInGroup(group: NavGroup): NavItem[] {
  return NAV_ITEMS.filter((n) => n.group === group);
}

/**
 * Narrow an arbitrary string to a ViewMode.
 *
 * Used when reading a view out of a hash / persisted preference: an unknown
 * value must fall back to a real surface rather than blanking the product.
 */
export function coerceView(v: unknown, fallback: ViewMode = "home"): ViewMode {
  return typeof v === "string" && v in BY_ID ? (v as ViewMode) : fallback;
}

/** True for the four v1.2 views (kept for SceneStore compatibility checks). */
export const LEGACY_VIEWS: readonly ViewMode[] = ["world", "graph", "table", "connections"];

export function isLegacyView(v: ViewMode): boolean {
  return LEGACY_VIEWS.includes(v);
}

/**
 * Whether the right-hand inspector rail applies to a view.
 *
 * The rail shows session/risk/directive detail, which is only meaningful for
 * the session-oriented surfaces. Product views own their full width.
 */
export function showsInspectorRail(v: ViewMode): boolean {
  return v === "world" || v === "graph" || v === "table";
}
