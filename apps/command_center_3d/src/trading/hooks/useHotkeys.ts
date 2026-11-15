// Global keyboard shortcuts for the Trading Cockpit.
//
// HARD RULE (compliance): a shortcut NEVER places, cancels, confirms or prices
// an order. Order actions are high-risk and are only triggered by an explicit
// human confirmation button in the order form. This module only drives
// navigation and view state (page / symbol / interval / help).
import { useEffect, useRef, useState } from "react";

export interface HotkeyHandlers {
  /** 1..7 → sidebar page number (1-based, matches NAV.num). */
  onNavigate?: (num: number) => void;
  onSymbol?: (sym: "BTC" | "ETH") => void;
  /** Cycle the active K-line interval: +1 = next, -1 = previous. */
  onIntervalCycle?: (dir: 1 | -1) => void;
  /** Jump straight to a named interval (e.g. "1h", "4h", "1d", "1w"). */
  onIntervalSet?: (interval: string) => void;
}

export interface HotkeyState {
  helpOpen: boolean;
  setHelpOpen: (v: boolean) => void;
  toggleHelp: () => void;
}

// Digits 1..7 are reserved for page navigation, so interval shortcuts use
// LETTERS only — this is the "avoid digit collision" rule from the spec.
// 1h/4h/1d/1w have direct letter keys; 1m/5m/15m are reachable via the i cycle.
const INTERVAL_DIRECT: Record<string, string> = { h: "1h", f: "4h", d: "1d", w: "1w" };

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
    t.isContentEditable || t.closest("[data-hotkey-ignore]") !== null;
}

export function useHotkeys(handlers: HotkeyHandlers, enabled = true): HotkeyState {
  const [helpOpen, setHelpOpen] = useState(false);
  // Keep the latest handlers without re-binding the listener on every render.
  const ref = useRef<HotkeyHandlers>(handlers);
  ref.current = handlers;
  const helpRef = useRef(false);
  helpRef.current = helpOpen;

  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      const k = e.key;

      // Esc always closes overlays.
      if (k === "Escape") {
        if (helpRef.current) setHelpOpen(false);
        return;
      }
      if (k === "?") {
        e.preventDefault();
        setHelpOpen((v) => !v);
        return;
      }
      // 1..7 → pages (digits never map to intervals).
      if (/^[1-7]$/.test(k)) {
        e.preventDefault();
        ref.current.onNavigate?.(Number(k));
        return;
      }
      const lower = k.toLowerCase();
      if (lower === "b") { e.preventDefault(); ref.current.onSymbol?.("BTC"); return; }
      if (lower === "e") { e.preventDefault(); ref.current.onSymbol?.("ETH"); return; }
      // Interval: "I" (Shift+i) = previous, "i" = next, letters = direct jump.
      if (k === "I") { e.preventDefault(); ref.current.onIntervalCycle?.(-1); return; }
      if (lower === "i") { e.preventDefault(); ref.current.onIntervalCycle?.(1); return; }
      if (!e.shiftKey && INTERVAL_DIRECT[lower]) {
        e.preventDefault();
        ref.current.onIntervalSet?.(INTERVAL_DIRECT[lower]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);

  return {
    helpOpen,
    setHelpOpen,
    toggleHelp: () => setHelpOpen((v) => !v),
  };
}

/** Human-readable table rendered by the help overlay. */
export const HOTKEY_HELP: { keys: string; desc: string }[] = [
  { keys: "1 … 7", desc: "切换页面（7 = 交易驾驶舱）" },
  { keys: "B / E", desc: "切换标的 BTC / ETH" },
  { keys: "I / Shift+I", desc: "循环切换 K 线周期（下一 / 上一）" },
  { keys: "H / F / D / W", desc: "直接跳到 1h / 4h / 1d / 1w 周期" },
  { keys: "?", desc: "显示 / 隐藏快捷键帮助" },
  { keys: "Esc", desc: "关闭帮助 / 弹层" },
  { keys: "（安全）", desc: "快捷键永不触发下单或撤单，必须人工确认" },
];
