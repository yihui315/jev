/**
 * navigation — the single registry of product surfaces.
 *
 * A view exists in exactly ONE place. The sidebar, the command palette, the
 * mobile drawer and the expert panel all read from `NAV_ITEMS`; none of them
 * keeps its own list.
 *
 * THE INFORMATION ARCHITECTURE (v2)
 * ---------------------------------
 * A first-time user sees FOUR entries: 首页 / 任务 / 项目 / 设置. Everything the
 * engineering console used to expose as a top-level item (World, Graph, Table,
 * Connections, Audit, Diagnostics, Studio) is still here, still reachable, and
 * still fully functional — it is just marked `advanced`, so Simple Mode hides it
 * and Expert Mode reveals it.
 *
 * Nothing is deleted. `mode: "simple" | "expert"` decides VISIBILITY, never
 * capability: switching to Simple cannot orphan a feature, and switching to
 * Expert restores every surface that existed before.
 *
 * Pure module (no React, no fetch) so it is unit-testable.
 */
import {
  Activity,
  AlertTriangle,
  Boxes,
  BrainCircuit,
  CandlestickChart,
  FileCode2,
  FlaskConical,
  FolderKanban,
  Globe2,
  Home,
  LayoutPanelLeft,
  LineChart,
  ListChecks,
  Orbit,
  Plug,
  Rocket,
  ScrollText,
  Settings,
  ShieldAlert,
  Stethoscope,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

/**
 * Every surface the product can show.
 *
 * The first four ids are the v1.2/v1.3 views and keep their exact ids so existing
 * bookmarks and the SceneStore contract stay valid.
 */
export type ViewMode =
  // --- the four primary surfaces ---
  | "home"
  | "missions"
  | "projects"
  | "settings"
  // --- primary-adjacent (reachable by CTA, listed in simple mode when relevant) ---
  | "task-detail"
  | "deliverables"
  | "needs-attention"
  | "studio"
  // --- advanced: Expert Mode only, all fully functional ---
  | "world"
  | "engine-world"
  | "graph"
  | "table"
  | "connections"
  | "audit"
  | "diagnostics"
  // --- trading workbench (Paper Trading × Night Researcher) — Expert Mode, Work group ---
  | "market-overview"
  | "trading-desk"
  | "market-universe"
  | "agent-center"
  | "risk-management"
  | "strategy-research"
  | "trading-audit";

/** Sidebar grouping. Order inside a group is the display order. */
export type NavGroup = "Main" | "Work" | "System";

/** Whether a surface is visible in Simple Mode. */
export type NavVisibility = "primary" | "advanced";

export interface NavItem {
  id: ViewMode;
  label: string;
  /** Chinese label for the Simplified primary surface. */
  labelZh: string;
  description: string;
  group: NavGroup;
  icon: LucideIcon;
  /** `primary` shows in Simple Mode; `advanced` needs Expert Mode. */
  visibility: NavVisibility;
  /** False when the view is reached only via a CTA / palette entry. */
  inSidebar: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  // ---------------------------------------------------------------------
  // The four primary entries. This is the whole product to a new user.
  // ---------------------------------------------------------------------
  {
    id: "home",
    label: "Home",
    labelZh: "首页",
    description: "今天想完成什么",
    group: "Main",
    icon: Home,
    visibility: "primary",
    inSidebar: true,
  },
  {
    id: "missions",
    label: "Tasks",
    labelZh: "任务",
    description: "所有任务与它们的运行状态",
    group: "Main",
    icon: ListChecks,
    visibility: "primary",
    inSidebar: true,
  },
  {
    id: "projects",
    label: "Projects",
    labelZh: "项目",
    description: "项目目录、最近任务与运行环境",
    group: "Main",
    icon: FolderKanban,
    visibility: "primary",
    inSidebar: true,
  },
  {
    id: "settings",
    label: "Settings",
    labelZh: "设置",
    description: "执行代理、模型、连接、安全与系统",
    group: "Main",
    icon: Settings,
    visibility: "primary",
    inSidebar: true,
  },

  // ---------------------------------------------------------------------
  // Secondary. Reached by CTA from a task, listed in the sidebar as a
  // subordinate entry rather than a peer of the four primaries.
  // ---------------------------------------------------------------------
  {
    id: "task-detail",
    label: "Task Detail",
    labelZh: "任务详情",
    description: "单个任务的进度、文件变更、预览与结果",
    group: "Work",
    icon: FileCode2,
    visibility: "primary",
    inSidebar: false,
  },
  {
    id: "needs-attention",
    label: "Needs Attention",
    labelZh: "需要确认",
    description: "等待你批准的操作",
    group: "Work",
    icon: AlertTriangle,
    visibility: "primary",
    inSidebar: false,
  },
  {
    id: "deliverables",
    label: "Deliverables",
    labelZh: "交付结果",
    description: "任务产出的文件与结果",
    group: "Work",
    icon: Boxes,
    visibility: "primary",
    inSidebar: false,
  },
  {
    id: "studio",
    label: "Mission Studio",
    labelZh: "任务编辑器",
    description: "逐步创建任务：目标、项目、代理、预算",
    group: "Work",
    icon: Rocket,
    visibility: "advanced",
    inSidebar: true,
  },

  // ---------------------------------------------------------------------
  // Advanced. Expert Mode only. Every one of these shipped in v1.2/v1.3 and
  // keeps its full behaviour; Simple Mode merely declines to list them.
  // ---------------------------------------------------------------------
  {
    id: "world",
    label: "World",
    labelZh: "执行可视化",
    description: "3D operational map of projects, missions and deliverables",
    group: "System",
    icon: Globe2,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "engine-world",
    label: "Engine World",
    labelZh: "引擎世界",
    description: "3D engine supervision: agents, sessions and live telemetry",
    group: "System",
    icon: Orbit,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "graph",
    label: "Graph",
    labelZh: "关系图",
    description: "Agent / session relationship graph",
    group: "System",
    icon: LayoutPanelLeft,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "table",
    label: "Table",
    labelZh: "数据表",
    description: "Flat, GPU-free session table (WebGL fallback)",
    group: "System",
    icon: Activity,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "connections",
    label: "Connections",
    labelZh: "连接",
    description: "Agent connections, capability probes and telemetry",
    group: "System",
    icon: Plug,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "audit",
    label: "Audit",
    labelZh: "审计",
    description: "Append-only decision and directive tape",
    group: "System",
    icon: ScrollText,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "diagnostics",
    label: "Diagnostics",
    labelZh: "系统诊断",
    description: "Runtime health, gates and honest system status",
    group: "System",
    icon: Stethoscope,
    visibility: "advanced",
    inSidebar: true,
  },

  // ---------------------------------------------------------------------
  // Trading Workbench — Paper Trading × Night Researcher.
  // Expert Mode only, grouped under Work. Reuses the existing Provider Center
  // concept and the Paper API; never a second provider / API-key system.
  // V4.0 six-page taxonomy (01–06) + the supplementary audit trail.
  // ---------------------------------------------------------------------
  {
    id: "market-overview",
    label: "AI Trading Command Center",
    labelZh: "01 · AI 交易指挥中心",
    description: "行情总览、账户资产与策略状态（机构级仪表盘）",
    group: "Work",
    icon: LineChart,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "trading-desk",
    label: "Pro Trading Desk",
    labelZh: "02 · 专业交易工作站",
    description: "K 线、订单簿、撮合下单（需人工确认）",
    group: "Work",
    icon: CandlestickChart,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "market-universe",
    label: "3D Market Universe",
    labelZh: "03 · 3D 市场宇宙",
    description: "资产相关性网络（实时 K 线计算，无假资金流）",
    group: "Work",
    icon: Boxes,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "strategy-research",
    label: "Strategy Arena",
    labelZh: "04 · 策略竞技场",
    description: "回测 / 样本外 / 收益与策略版本（OOS 门禁）",
    group: "Work",
    icon: FlaskConical,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "risk-management",
    label: "Paper Account",
    labelZh: "05 · 模拟交易账户",
    description: "持仓、敞口、最大回撤与风险拒绝（服务端数据）",
    group: "Work",
    icon: ShieldAlert,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "agent-center",
    label: "AI Expert Team",
    labelZh: "06 · AI 专家团队",
    description: "六角色（SPOTTER/PRIOR/EDGE/KELLY/TAKER/CLOSER）运行记录",
    group: "Work",
    icon: BrainCircuit,
    visibility: "advanced",
    inSidebar: true,
  },
  {
    id: "trading-audit",
    label: "Trading Audit",
    labelZh: "交易审计",
    description: "订单、成交、账本与事件回放（服务端账本一致）",
    group: "Work",
    icon: ScrollText,
    visibility: "advanced",
    inSidebar: true,
  },
] as const;

export const NAV_GROUPS: readonly NavGroup[] = ["Main", "Work", "System"];

const BY_ID: Record<string, NavItem> = Object.fromEntries(
  NAV_ITEMS.map((n) => [n.id, n]),
);

/** The full ordered list of valid view ids. */
export const VIEW_IDS: readonly ViewMode[] = NAV_ITEMS.map((n) => n.id);

/** The four entries that make up the default product navigation. */
export const PRIMARY_VIEWS: readonly ViewMode[] = NAV_ITEMS.filter(
  (n) => n.visibility === "primary" && n.inSidebar,
).map((n) => n.id);

export function navItem(id: ViewMode): NavItem | undefined {
  return BY_ID[id];
}

/**
 * The label for a view in the current mode.
 *
 * Simple Mode speaks Chinese plain language; Expert Mode keeps the engineering
 * names, because that is the audience reading it.
 */
export function navLabel(id: ViewMode, expert = false): string {
  const item = BY_ID[id];
  if (!item) return id;
  return expert ? item.label : item.labelZh;
}

export function navItemsInGroup(group: NavGroup, expert = false): NavItem[] {
  return NAV_ITEMS.filter(
    (n) => n.group === group && (expert || n.visibility === "primary"),
  );
}

/**
 * Narrow an arbitrary string to a ViewMode.
 *
 * Used when reading a view out of a hash: an unknown value must fall back to a
 * real surface rather than blanking the product.
 */
export function coerceView(v: unknown, fallback: ViewMode = "home"): ViewMode {
  return typeof v === "string" && v in BY_ID ? (v as ViewMode) : fallback;
}

/** True for the four v1.2 views (kept for SceneStore compatibility checks). */
export const LEGACY_VIEWS: readonly ViewMode[] = ["world", "graph", "table", "connections"];

export function isLegacyView(v: ViewMode): boolean {
  return LEGACY_VIEWS.includes(v);
}

/** True when a surface is Expert-Mode-only. */
export function isAdvanced(v: ViewMode): boolean {
  return BY_ID[v]?.visibility === "advanced";
}

/** The views that need Three.js. Used to keep the 3D engine off the home page. */
export const THREE_D_VIEWS: readonly ViewMode[] = ["world", "engine-world"];

export function needs3D(v: ViewMode): boolean {
  return THREE_D_VIEWS.includes(v);
}

/**
 * Whether the right-hand inspector rail applies to a view.
 *
 * The rail shows session/risk/directive detail, which is only meaningful for the
 * session-oriented surfaces. The v1.3 operational `world` owns its full width.
 */
export function showsInspectorRail(v: ViewMode): boolean {
  return v === "engine-world" || v === "graph" || v === "table";
}
