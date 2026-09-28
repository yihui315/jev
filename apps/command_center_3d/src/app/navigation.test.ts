import { describe, it, expect } from "vitest";
import {
  NAV_ITEMS,
  NAV_GROUPS,
  VIEW_IDS,
  coerceView,
  isLegacyView,
  navItem,
  navItemsInGroup,
  navLabel,
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

  it("groups only contain declared items", () => {
    const grouped = NAV_GROUPS.flatMap((g) => navItemsInGroup(g).map((n) => n.id));
    expect(grouped.sort()).toEqual([...VIEW_IDS].sort());
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
      expect(navLabel(v)).toBe(navItem(v)?.label);
    }
  });

  it("shows the inspector rail only for session-oriented views", () => {
    expect(showsInspectorRail("world")).toBe(true);
    expect(showsInspectorRail("graph")).toBe(true);
    expect(showsInspectorRail("table")).toBe(true);
    // Product views own their full width.
    expect(showsInspectorRail("home")).toBe(false);
    expect(showsInspectorRail("studio")).toBe(false);
    expect(showsInspectorRail("connections")).toBe(false);
    expect(showsInspectorRail("deliverables")).toBe(false);
  });
});
