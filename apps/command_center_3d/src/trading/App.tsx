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

  const cloudPreview = import.meta.env.VITE_CLOUDFLARE_STATIC_PREVIEW === "1";
  const meta: Record<PageId, string> = cloudPreview ? {
    command: "BLOCKED", workbench: "BLOCKED", universe: "BLOCKED",
    strategy: "NO_DATA", account: "BLOCKED", team: "BLOCKED",
  } : {
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
      {/* Single grid item (col 2) so topbar + content fill the main column as a
          flex column. Without this wrapper each page's fragment (<topbar/> +
          <jev-content/>) becomes TWO separate grid cells, dropping the content
          into the 232px sidebar column and leaving col 2 empty. */}
      <main className="jev-main">
        {cloudPreview && <div role="status" style={{ padding: "10px 16px", background: "#46311a", color: "#ffe2b0", borderBottom: "1px solid #865a22", fontSize: "13px" }}>Cloudflare Preview · UI ONLY · Paper Trading / AI 服务未接通，所有下单请求被服务端阻断。非实盘交易。</div>}
        {page === "command" && <AICommandCenter topbar={topbar} />}
        {page === "workbench" && <TradingWorkbench topbar={topbar} />}
        {page === "universe" && <MarketUniverse3D topbar={topbar} />}
        {page === "strategy" && <StrategyArena topbar={topbar} />}
        {page === "account" && <PaperAccount topbar={topbar} />}
        {page === "team" && <AIExpertTeam topbar={topbar} />}
      </main>
      {auditOpen && <AuditDrawer onClose={() => setAuditOpen(false)} />}
    </div>
  );
}
