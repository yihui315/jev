// Pointer-driven resizable split panes for the Trading Cockpit.
// Pure CSS + pointer events — no dependency. Layout is persisted to
// localStorage. The clamps guarantee neither pane can collapse to zero, so
// chart containers always keep a non-zero height and no pane overflows.
//
// P0 layout fix: the vertical split is now a PIXEL min-height for the upper
// pane (not a percentage of a fixed-height track). This lets the workspace
// grow with its content (a long, naturally-scrolling page) instead of locking
// it to a fixed height that clipped the order ticket. The horizontal split is
// still a percentage of the (definite) track width.
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

const STORAGE_KEY = "jev.cockpit.layout.v2";
const DEFAULTS = { hSplit: 62, vSplit: 460 };
// Left pane width % — right pane keeps >= 28%.
const H_MIN = 40, H_MAX = 72;
// Upper pane MIN height in px — taller content still expands freely (never
// clipped); this only sets a floor so dragging the vertical splitter has an
// effect and the K-line (320px) always has room.
const V_MIN = 360, V_MAX = 1000;

interface Layout { hSplit: number; vSplit: number; }

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

function load(): Layout {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const p = JSON.parse(raw) as Partial<Layout>;
    return {
      hSplit: clamp(Number(p.hSplit) || DEFAULTS.hSplit, H_MIN, H_MAX),
      vSplit: clamp(Number(p.vSplit) || DEFAULTS.vSplit, V_MIN, V_MAX),
    };
  } catch {
    return DEFAULTS;
  }
}

export interface ResizablePanels {
  /** left pane width, percent of the horizontal track */
  hSplit: number;
  /** upper pane MIN height in px (content may grow beyond it) */
  vSplit: number;
  /** attach to the horizontal (left/right) split container */
  hContainerRef: RefObject<HTMLDivElement | null>;
  /** attach to the vertical (upper/lower) split container */
  vContainerRef: RefObject<HTMLDivElement | null>;
  onHDragStart: (e: ReactPointerEvent) => void;
  onVDragStart: (e: ReactPointerEvent) => void;
  reset: () => void;
}

export function useResizablePanels(): ResizablePanels {
  const [layout, setLayout] = useState<Layout>(load);
  const hRef = useRef<HTMLDivElement | null>(null);
  const vRef = useRef<HTMLDivElement | null>(null);
  const dragging = useRef<null | "h" | "v">(null);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout)); } catch { /* storage disabled */ }
  }, [layout]);

  const startDrag = useCallback((axis: "h" | "v") => (e: ReactPointerEvent) => {
    e.preventDefault();
    dragging.current = axis;
    const startPos = axis === "h" ? e.clientX : e.clientY;
    const startVal = axis === "h" ? layout.hSplit : layout.vSplit;
    const containerW = hRef.current?.clientWidth ?? 0;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);

    const onMove = (ev: PointerEvent) => {
      if (dragging.current !== axis) return;
      const delta = axis === "h" ? ev.clientX - startPos : ev.clientY - startPos;
      setLayout((prev) => axis === "h"
        // width: percent of the (definite) track width
        ? { ...prev, hSplit: clamp(startVal + (containerW ? (delta / containerW) * 100 : 0), H_MIN, H_MAX) }
        // height: direct pixel delta (track height is content-driven, not fixed)
        : { ...prev, vSplit: clamp(startVal + delta, V_MIN, V_MAX) });
    };
    const onUp = () => {
      dragging.current = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }, [layout.hSplit, layout.vSplit]);

  return {
    hSplit: layout.hSplit,
    vSplit: layout.vSplit,
    hContainerRef: hRef,
    vContainerRef: vRef,
    onHDragStart: startDrag("h"),
    onVDragStart: startDrag("v"),
    reset: () => setLayout(DEFAULTS),
  };
}
