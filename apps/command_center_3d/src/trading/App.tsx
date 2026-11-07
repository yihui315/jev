// JEV Trading Desk V1.0 — app shell + hash router with refresh restore.
// Six pages all consume the single /api/jev adapter (see api/client.ts).
import { useEffect, useState, useCallback } from "react";
import { Sidebar, TopBar, AuditDrawer, NAV, type PageId } from "./components/Shell";
import { AICommandCenter } from "./pages/AICommandCenter";
import { TradingWorkbench } from "./pages/TradingWorkbench";
import { MarketUniverse3D } from "./pages/MarketUniverse3D";
import { StrategyArena } from "./pages/StrategyArena";
import { PaperAccount } from "./pages/PaperAccount";
import { AIExpertTeam } from "./pages/AIExpertTeam";

const ROUTE_RE = /^#\/?([a-z]+)$/;

function readRoute(): PageId {
  const h = window.location.hash || "";
  const m = h.match(ROUTE_RE);
  const id = m ? (m[1] as PageId) : "command";
  return NAV.some((n) => n.id === id) ? id : "command";
}

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export function TradingApp() {
  const [page, setPage] = useState<PageId>(readRoute);
  const [auditOpen, setAuditOpen] = useState(() =>
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("audit") === "open"
  );
  const now = useClock();

  const goto = useCallback((p: PageId) => {
    setPage(p);
    try { window.location.hash = "#/" + p; } catch { /* non-browser */ }
  }, []);

  useEffect(() => {
    const onHash = () => setPage(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const meta: Record<PageId, string> = {
    command: "VERIFIED", workbench: "LIVE", universe: "LIVE",
    strategy: "NO_DATA", account: "LIVE", team: "BLOCKED",
  };
  const title = NAV.find((n) => n.id === page)?.label ?? "";
  const clock = `${now.toISOString().slice(0, 19).replace("T", " ")} UTC`;

  const topbar = (
    <TopBar title={title} metaLabel={meta[page] as never} onOpenAudit={() => setAuditOpen(true)} clock={clock} />
  );

  return (
    <div className="jev-app">
      <Sidebar page={page} onSelect={goto} />
      {page === "command" && <AICommandCenter topbar={topbar} />}
      {page === "workbench" && <TradingWorkbench topbar={topbar} />}
      {page === "universe" && <MarketUniverse3D topbar={topbar} />}
      {page === "strategy" && <StrategyArena topbar={topbar} />}
      {page === "account" && <PaperAccount topbar={topbar} />}
      {page === "team" && <AIExpertTeam topbar={topbar} />}
      {auditOpen && <AuditDrawer onClose={() => setAuditOpen(false)} />}
    </div>
  );
}
