// JEV Trading Desk V1.0 — app shell + hash router with refresh restore.
// Six pages all consume the single /api/jev adapter (see api/client.ts).
import { useEffect, useState, useCallback, type FormEvent } from "react";
import { Sidebar, TopBar, AuditDrawer, NAV, type PageId } from "./components/Shell";
import { AICommandCenter } from "./pages/AICommandCenter";
import { TradingWorkbench } from "./pages/TradingWorkbench";
import { MarketUniverse3D } from "./pages/MarketUniverse3D";
import { StrategyArena } from "./pages/StrategyArena";
import { PaperAccount } from "./pages/PaperAccount";
import { AIExpertTeam } from "./pages/AIExpertTeam";
import { getPreviewCreds, setPreviewCreds, clearPreviewCreds } from "./api/previewAuth";

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

  // ---- Preview access gate -------------------------------------------------
  const [creds, setCreds] = useState<{ user: string } | null>(() => getPreviewCreds());
  const [showLogin, setShowLogin] = useState<boolean>(() => !getPreviewCreds());
  const doLogin = (user: string, pass: string) => {
    setPreviewCreds({ user, pass });
    setCreds({ user });
    setShowLogin(false);
    // Reload so every page's useJevQuery re-runs with the new credentials.
    window.location.reload();
  };
  const doLogout = () => {
    clearPreviewCreds();
    setCreds(null);
    setShowLogin(true);
  };

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
        {page === "command" && <AICommandCenter topbar={topbar} />}
        {page === "workbench" && <TradingWorkbench topbar={topbar} />}
        {page === "universe" && <MarketUniverse3D topbar={topbar} />}
        {page === "strategy" && <StrategyArena topbar={topbar} />}
        {page === "account" && <PaperAccount topbar={topbar} />}
        {page === "team" && <AIExpertTeam topbar={topbar} />}
      </main>
      {auditOpen && <AuditDrawer onClose={() => setAuditOpen(false)} />}
      {showLogin ? (
        <PreviewLoginModal onLogin={doLogin} defaultUser={creds?.user ?? ""} />
      ) : (
        <button
          className="jev-auth-btn"
          onClick={() => setShowLogin(true)}
          title={creds?.user ? `已登录：${creds.user}（点击切换）` : "预览登录"}
        >
          {creds?.user ? `预览登录 · ${creds.user}` : "预览登录"}
        </button>
      )}
    </div>
  );
}

// Lightweight preview-credentials modal. Credentials are stored only in
// sessionStorage and sent only to the same-origin staging Function.
function PreviewLoginModal({ onLogin, defaultUser }: { onLogin: (u: string, p: string) => void; defaultUser: string }) {
  const [user, setUser] = useState(defaultUser);
  const [pass, setPass] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!user || !pass) return;
    onLogin(user.trim(), pass);
  };
  return (
    <div className="jev-backdrop" style={{ background: "rgba(8,12,20,0.92)", zIndex: 50 }}>
      <form className="jev-login" onSubmit={submit}>
        <h2>JEV 预览访问</h2>
        <p className="jev-login-note">
          此 Staging 站点受 Basic 预览认证保护。请输入分配给你的预览账号与密码。
          凭据仅保存在当前浏览器会话（sessionStorage），仅发送至同源 Cloudflare 函数。
        </p>
        <label>
          预览账号
          <input value={user} onChange={(e) => setUser(e.target.value)} placeholder="preview user" autoFocus />
        </label>
        <label>
          预览密码
          <input type="password" value={pass} onChange={(e) => setPass(e.target.value)} placeholder="preview password" />
        </label>
        <button type="submit" disabled={!user || !pass}>登录</button>
      </form>
    </div>
  );
}
