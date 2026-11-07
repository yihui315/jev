// Minimal inline SVG icons (no external icon dependency). Each is a 16x16 stroke
// icon so the terminal stays crisp and dependency-light.
import type { CSSProperties, ReactElement } from "react";

interface P { style?: CSSProperties; }
export type IconComp = (p?: P) => ReactElement;

const base = (children: ReactElement, p?: P): ReactElement => (
  <svg className="ic" width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    style={{ flex: "0 0 16px", ...(p?.style || {}) }} aria-hidden>
    {children}
  </svg>
);

export const IconCommand: IconComp = (p) => base(
  <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /></>, p);
export const IconChart: IconComp = (p) => base(<><path d="M3 3v18h18" /><path d="M7 14l4-4 3 3 5-6" /></>, p);
export const IconGlobe: IconComp = (p) => base(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18" /></>, p);
export const IconFlask: IconComp = (p) => base(<><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3" /><path d="M8 15h8" /></>, p);
export const IconWallet: IconComp = (p) => base(<><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M16 12h3M3 9h14" /></>, p);
export const IconTeam: IconComp = (p) => base(<><circle cx="9" cy="8" r="3" /><path d="M3 20a6 6 0 0 1 12 0" /><circle cx="17" cy="9" r="2.5" /><path d="M16 20a5 5 0 0 1 5-5" /></>, p);
export const IconShield: IconComp = (p) => base(<><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" /></>, p);
export const IconBolt: IconComp = (p) => base(<><path d="M13 2L4 14h7l-1 8 9-12h-7z" /></>, p);
export const IconBook: IconComp = (p) => base(<><path d="M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z" /><path d="M20 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z" /></>, p);
