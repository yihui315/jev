import { describe, it, expect } from "vitest";
import {
  NAV_ITEMS,
  NAV_GROUPS,
  PRIMARY_VIEWS,
  VIEW_IDS,
  coerceView,
  isAdvanced,
  isLegacyView,
  navItem,
  navItemsInGroup,
  navLabel,
  needs3D,
  showsInspectorRail,
} from "./navigation";

/**
 * The navigation registry is the single source of truth for product surfaces.
 * These tests pin the properties that would otherwise drift silently: the v1.3
 * required view list, uniqueness of ids, and the legacy-view contract the
 * SceneStore depends on.
 */

const REQUIRED_V13_VIEWS = [
  "home",
  "projects",
  "missions",
  "needs-attention",
  "connections",
  "deliverables",
  "world",
  "engine-world",
  "audit",
  "diagnostics",
  "settings",
];

describe("navigation registry", () => {
  it("declares every v1.3 product surface", () => {
    for (const v of REQUIRED_V13_VIEWS) {
      expect(VIEW_IDS).toContain(v);
    }
  });

  it("has unique ids and unique labels-per-id", () => {
    const ids = NAV_ITEMS.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("keeps the four v1.2 view ids intact (SceneStore contract)", () => {
    for (const v of ["world", "graph", "table", "connections"] as const) {
      expect(navItem(v)).toBeDefined();
      expect(isLegacyView(v)).toBe(true);
    }
  });

  it("gives every item an icon, a description and a valid group", () => {
    for (const n of NAV_ITEMS) {
      expect(n.icon).toBeTruthy();
      expect(n.description.length).toBeGreaterThan(0);
      expect(NAV_GROUPS).toContain(n.group);
    }
  });

  it("groups only contain declared items (expert mode shows every group)", () => {
    const grouped = NAV_GROUPS.flatMap((g) => navItemsInGroup(g, true).map((n) => n.id));
    expect(grouped.sort()).toEqual([...VIEW_IDS].sort());
  });

  it("exposes exactly four primary sidebar entries, in order", () => {
    // The whole product to a new user: 首页 / 任务 / 项目 / 设置.
    expect(PRIMARY_VIEWS).toEqual(["home", "missions", "projects", "settings"]);
  });

  it("hides advanced surfaces in simple mode but keeps every one in expert mode", () => {
    // Simplicity is a VISIBILITY decision, never a capability one: an advanced
    // view must still exist in the registry, or Expert Mode could not restore it.
    for (const n of NAV_ITEMS) {
      if (n.visibility === "advanced") {
        expect(isAdvanced(n.id)).toBe(true);
        expect(navItemsInGroup(n.group, true).map((x) => x.id)).toContain(n.id);
        expect(navItemsInGroup(n.group, false).map((x) => x.id)).not.toContain(n.id);
      } else {
        expect(isAdvanced(n.id)).toBe(false);
      }
    }
  });

  it("marks only the two 3D views as needing the Three.js engine", () => {
    // The home page must never pull the 3D engine into its bundle.
    expect(needs3D("world")).toBe(true);
    expect(needs3D("engine-world")).toBe(true);
    for (const v of VIEW_IDS) {
      if (v !== "world" && v !== "engine-world") {
        expect(needs3D(v)).toBe(false);
      }
    }
  });

  it("speaks Chinese in simple mode and keeps engineering names in expert mode", () => {
    // The plain-language surface and the engineering surface are the SAME view
    // with two labels; `missions` is the id, 任务 what a user calls it, Tasks
    // what the engineering console calls it.
    expect(navLabel("missions", false)).toBe("任务");
    expect(navLabel("missions", true)).toBe("Tasks");
    expect(navLabel("home", false)).toBe("首页");
    expect(navLabel("projects", false)).toBe("项目");
    expect(navLabel("settings", false)).toBe("设置");
    // Every view must have BOTH labels, so no mode can show a raw id.
    for (const v of VIEW_IDS) {
      expect(navLabel(v, false).length).toBeGreaterThan(0);
      expect(navLabel(v, true).length).toBeGreaterThan(0);
      expect(navLabel(v, false)).not.toBe(v);
    }
  });

  it("coerces an unknown view to the fallback rather than trusting it", () => {
    expect(coerceView("world")).toBe("world");
    expect(coerceView("not-a-view")).toBe("home");
    expect(coerceView("")).toBe("home");
    expect(coerceView(null)).toBe("home");
    expect(coerceView(undefined, "table")).toBe("table");
    expect(coerceView(42)).toBe("home");
  });

  it("labels every view", () => {
    for (const v of VIEW_IDS) {
      expect(navLabel(v, true)).toBe(navItem(v)?.label);
    }
  });

  it("shows the inspector rail only for session-oriented views", () => {
    // Wave 6: `world` is now the OPERATIONAL map (projects/missions/deliverables),
    // which is not session-oriented and owns its full width. The v1.2 engine
    // telemetry scene moved to `engine-world`, which keeps the session rail.
    expect(showsInspectorRail("engine-world")).toBe(true);
    expect(showsInspectorRail("graph")).toBe(true);
    expect(showsInspectorRail("table")).toBe(true);
    expect(showsInspectorRail("world")).toBe(false);
    // Product views own their full width.
    expect(showsInspectorRail("home")).toBe(false);
    expect(showsInspectorRail("studio")).toBe(false);
    expect(showsInspectorRail("connections")).toBe(false);
    expect(showsInspectorRail("deliverables")).toBe(false);
  });
});
