/**
 * SettingsView — seven sections, one question each.
 *
 * THE STRUCTURE RULE
 * ------------------
 * A settings page fails when everything is on it. So the surface is split into
 * 通用 / 执行代理 / 模型 / 连接 / 安全 / 系统 / 高级, and the one a person
 * actually needs is at the top. The expert controls — the control token, the
 * Simple/Expert switch, the API base — live in 高级, which is the last section
 * and is labelled as such.
 *
 * THE TOKEN IS NOT A NORMAL-MODE FIELD
 * ------------------------------------
 * The control token authorises every mutating call, so it is rendered here in
 * 高级 and in Expert Mode only. It is held in memory for the lifetime of the tab
 * and is never written to localStorage, a cookie, the URL or a log. In Simple
 * Mode the 高级 section explains that the token is unset rather than showing an
 * empty password box that looks like something is missing.
 *
 * AGENT STATUS HAS THREE WORDS, NOT TWELVE
 * -----------------------------------------
 * 可用 / 需要登录 / 离线 is the whole vocabulary in 普通 mode. The server's
 * twelve-state health string is still read, but it is mapped here to one of those
 * three, and an unrecognised health maps to 未知 rather than to 可用.
 */
import { useEffect, useState } from "react";
import { PageBody, PageHeader } from "../components/AppShell";
import { LoadingState } from "../components/States";
import { ProviderCenter } from "./ProviderCenter";
import { describeTokenState, type TokenState } from "../lib/authorizedFetch";
import { cn } from "../ui/cn";
import type { DomainApi, DomainConnection } from "./domainApi";

/** The seven sections, in display order. */
const SECTIONS = [
  { id: "general", label: "通用" },
  { id: "agents", label: "执行代理" },
  // Renamed from 「模型」: this section is where credentials are entered, so the
  // label has to say so. "模型" sounded like a preference list.
  { id: "models", label: "模型与 API" },
  { id: "connections", label: "连接" },
  { id: "security", label: "安全" },
  { id: "system", label: "系统" },
  { id: "advanced", label: "高级" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

/** The three words an agent status is allowed to use in the normal product. */
type AgentState = "ready" | "needs-login" | "offline" | "unknown";

const AGENT_STATE_TEXT: Record<AgentState, string> = {
  ready: "可用",
  "needs-login": "需要登录",
  offline: "离线",
  unknown: "状态未知",
};

const AGENT_STATE_TONE: Record<AgentState, string> = {
  ready: "text-status-ok",
  "needs-login": "text-status-warn",
  offline: "text-status-danger",
  // Unknown is grey. It is never allowed to read as ready.
  unknown: "text-status-unknown",
};

/**
 * Map the backend's health string to one of four display states.
 *
 * Pure and total: a health value this build has never seen becomes `unknown`,
 * which is rendered in grey. Collapsing an unknown health into `ready` is how a
 * dead agent ends up showing a green dot.
 */
export function agentStateFor(health: string): AgentState {
  switch (health) {
    case "CONNECTED":
      return "ready";
    case "DEGRADED":
    case "UNVERIFIED":
      return "needs-login";
    case "DISCONNECTED":
    case "UNAVAILABLE":
      return "offline";
    case "UNKNOWN":
      return "unknown";
    default:
      return "unknown";
  }
}

export function SettingsView({
  api,
  controlToken,
  onControlToken,
  theme,
  onTheme,
  expert,
  onExpert,
  base,
  onOpenToken,
  tokenState,
  onVerifyToken,
}: {
  api: DomainApi;
  controlToken: string;
  onControlToken: (v: string) => void;
  theme: "dark" | "light";
  onTheme: (t: "dark" | "light") => void;
  expert: boolean;
  onExpert: (v: boolean) => void;
  base: string;
  /** Sends the operator to the credential field when an API write is refused. */
  onOpenToken: () => void;
  /** Server-measured authorization state. Never inferred from a non-empty input. */
  tokenState: TokenState;
  /** Runs a real authenticated round trip and reports what the server said. */
  onVerifyToken: () => Promise<void>;
}) {
  const tokenStatus = describeTokenState(tokenState);
  const [section, setSection] = useState<SectionId>("general");
  const [connections, setConnections] = useState<DomainConnection[] | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [showToken, setShowToken] = useState(false);

  // The agent list is only read when the agent section is actually opened.
  useEffect(() => {
    if (section !== "agents" && section !== "connections") return;
    let alive = true;
    void api.connections().then((r) => {
      if (!alive) return;
      if (r.ok && r.data) {
        setConnections(r.data);
        setConnError(null);
      } else {
        setConnections(null);
        setConnError(r.error ?? "无法读取连接状态");
      }
    });
    return () => {
      alive = false;
    };
  }, [api, section]);

  return (
    <PageBody>
      <PageHeader title="设置" description="调整执行代理、连接与产品偏好。" />

      <div className="grid gap-8 lg:grid-cols-[180px_1fr]">
        {/* Section nav. On a phone it becomes a horizontal row instead of a
            column, so the page is still usable at 375px. */}
        <nav aria-label="设置分组" className="lg:sticky lg:top-0 lg:self-start">
          <ul className="flex flex-wrap gap-1 lg:flex-col">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => setSection(s.id)}
                  aria-current={section === s.id ? "true" : undefined}
                  className={cn(
                    "rounded-md px-3 py-2 text-left text-[14px] transition-colors",
                    section === s.id
                      ? "bg-primary-soft font-medium text-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  {s.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0">
          {section === "general" ? (
            <Group title="通用" hint="外观与显示偏好。">
              <Field label="主题">
                <div className="flex gap-2">
                  {(["dark", "light"] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => onTheme(t)}
                      aria-pressed={theme === t}
                      className={cn(
                        "rounded-md border px-3 py-1.5 text-[13px] transition-colors",
                        theme === t
                          ? "border-primary bg-primary-soft text-foreground"
                          : "border-border text-muted-foreground hover:bg-accent",
                      )}
                    >
                      {t === "dark" ? "深色" : "浅色"}
                    </button>
                  ))}
                </div>
              </Field>
              <Field label="界面模式">
                <p className="t-body text-muted-foreground">
                  {expert
                    ? "当前为专家模式：导航中包含 3D 可视化、审计、系统诊断等高级视图。"
                    : "当前为普通模式：导航中只有首页、任务、项目、设置四项。"}
                </p>
                <p className="mt-1 t-meta text-muted-foreground">
                  切换入口在下方「高级」分组。
                </p>
              </Field>
            </Group>
          ) : null}

          {section === "agents" ? (
            <Group title="执行代理" hint="选择由哪个代理执行任务，及其可用状态。">
              {connError ? (
                <p className="t-body text-destructive" role="alert">
                  无法读取代理状态：{connError}
                </p>
              ) : connections === null ? (
                <LoadingState label="正在读取代理状态…" />
              ) : connections.length === 0 ? (
                <p className="t-body text-muted-foreground">
                  后端没有注册任何执行代理。启动任务前请先在服务端配置。
                </p>
              ) : (
                <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border">
                  {connections.map((c) => {
                    const state = agentStateFor(c.health);
                    return (
                      <li key={c.connection_id} className="flex items-center gap-3 bg-card px-4 py-3">
                        <div className="min-w-0 flex-1">
                          <p className="text-[14px] text-foreground">
                            {c.display_name || c.agent}
                          </p>
                          {expert ? (
                            <p className="truncate font-mono text-[11px] text-muted-foreground">
                              {c.endpoint || c.connection_type}
                            </p>
                          ) : null}
                        </div>
                        <span className={cn("shrink-0 t-meta", AGENT_STATE_TONE[state])}>
                          {AGENT_STATE_TEXT[state]}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Group>
          ) : null}

          {section === "models" ? (
            <Group
              title="模型与 API"
              hint="连接 JEV 使用的模型服务。JEV 自动选择会从这里已连接的 Provider 中挑选执行模型与验证模型。"
            >
              <ProviderCenter controlToken={controlToken} onTokenMissing={onOpenToken} />
            </Group>
          ) : null}

          {section === "connections" ? (
            <Group title="连接" hint="执行代理与后端之间的连接状态。">
              {connError ? (
                <p className="t-body text-destructive" role="alert">
                  {connError}
                </p>
              ) : connections === null ? (
                <LoadingState label="正在读取连接…" />
              ) : connections.length === 0 ? (
                <p className="t-body text-muted-foreground">后端没有注册任何连接。</p>
              ) : (
                <ul className="space-y-2">
                  {connections.map((c) => {
                    const state = agentStateFor(c.health);
                    return (
                      <li key={c.connection_id} className="rounded-lg border border-border bg-card p-4">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-[14px] text-foreground">
                            {c.display_name || c.agent}
                          </p>
                          <span className={cn("t-meta", AGENT_STATE_TONE[state])}>
                            {AGENT_STATE_TEXT[state]}
                          </span>
                        </div>
                        {expert ? (
                          <dl className="mt-2 space-y-1 t-meta">
                            <Row label="类型" value={c.connection_type} />
                            <Row label="端点" value={c.endpoint || "未配置"} mono />
                            <Row label="已验证控制" value={c.control_verified ? "是" : "否"} />
                          </dl>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </Group>
          ) : null}

          {section === "security" ? (
            <Group title="安全" hint="控制令牌与本地访问策略。">
              <p className="t-body text-muted-foreground">
                JCC 仅在本机运行，控制令牌用于授权所有写操作（创建任务、批准操作等）。
              </p>
              <p className="mt-2 t-body">
                当前状态：
                <span className={controlToken ? "text-status-ok" : "text-status-warn"}>
                  {controlToken ? "已提供令牌" : "未提供令牌（无法创建任务）"}
                </span>
              </p>

              {/* The token is a CREDENTIAL, so it is entered here — not only under
                  高级. It used to live exclusively in the advanced section, which
                  left the security page able to say 「未提供令牌（无法创建任务）」
                  while offering no way to fix it: a dead end for anyone who went
                  where the label pointed. Both sections write the SAME state, so
                  there is one token in memory and not two. */}
              <div className="mt-4 max-w-md">
                <label htmlFor="token-security" className="mb-1 block t-meta text-muted-foreground">
                  控制令牌
                </label>
                <input
                  id="token-security"
                  type={showToken ? "text" : "password"}
                  value={controlToken}
                  onChange={(e) => onControlToken(e.target.value)}
                  placeholder="粘贴控制令牌"
                  autoComplete="off"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-[13px] text-foreground outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring"
                />
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void onVerifyToken()}
                    disabled={!controlToken || tokenState === "verifying"}
                    className="rounded-md border border-border px-2.5 py-1 t-meta text-foreground transition-colors hover:bg-accent disabled:opacity-50"
                  >
                    {tokenState === "verifying" ? "验证中…" : "验证授权"}
                  </button>
                  <button
                    type="button"
                    onClick={() => onControlToken("")}
                    className="rounded-md border border-border px-2.5 py-1 t-meta text-foreground transition-colors hover:bg-accent"
                  >
                    清除
                  </button>
                </div>
              </div>

              <p className="mt-1.5 t-meta text-muted-foreground">
                令牌不会保存在浏览器中，关闭页面后需要重新提供。
              </p>
            </Group>
          ) : null}

          {section === "system" ? (
            <Group title="系统" hint="后端服务与运行环境。">
              <dl className="space-y-1">
                <Row label="API 地址" value={base} mono />
                <Row label="访问范围" value="仅本机（localhost）" />
              </dl>
              <p className="mt-2 t-meta text-muted-foreground">
                若需要查看运行状态、门禁与会话明细，请切换到专家模式后打开「系统诊断」。
              </p>
            </Group>
          ) : null}

          {section === "advanced" ? (
            <Group title="高级" hint="界面模式与控制令牌。">
              <Field label="界面模式">
                <div className="flex gap-2">
                  {([
                    { id: false, label: "普通模式" },
                    { id: true, label: "专家模式" },
                  ] as const).map((m) => (
                    <button
                      key={String(m.id)}
                      type="button"
                      onClick={() => onExpert(m.id)}
                      aria-pressed={expert === m.id}
                      className={cn(
                        "rounded-md border px-3 py-1.5 text-[13px] transition-colors",
                        expert === m.id
                          ? "border-primary bg-primary-soft text-foreground"
                          : "border-border text-muted-foreground hover:bg-accent",
                      )}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 t-meta text-muted-foreground">
                  专家模式会在导航中增加：任务编辑器、执行可视化、引擎世界、关系图、数据表、连接、审计、系统诊断。
                </p>
              </Field>

              <Field label="控制令牌">
                {/*
                  NOT behind the expert gate.
                  Every write in the product — provider credentials, probe, model
                  routing, project and mission creation, approve/reject — needs
                  this token. Hiding the one input that unlocks all of them
                  behind a mode whose only toggle lives in the command palette
                  made the writes unreachable by any discoverable path.
                */}
                <>
                    {/*
                      Two lines, because they are two different claims.
                      「已提供」 is a fact about this browser tab. 「已验证」 is a
                      fact about the SERVER, measured by a real authenticated
                      round trip — a non-empty input box proves neither, and
                      showing one green "已设置" for both is how an operator ends
                      up hunting a server problem that is really a typo.
                    */}
                    <div className="space-y-0.5">
                      <p
                        className={cn(
                          "t-meta",
                          controlToken ? "text-foreground" : "text-muted-foreground",
                        )}
                      >
                        {tokenStatus.browser}
                      </p>
                      <p
                        className={cn(
                          "t-meta",
                          tokenStatus.tone === "ok"
                            ? "text-status-ok"
                            : tokenStatus.tone === "danger"
                              ? "text-status-danger"
                              : tokenStatus.tone === "warn"
                                ? "text-status-warn"
                                : "text-muted-foreground",
                        )}
                      >
                        {tokenStatus.server}
                      </p>
                    </div>
                    <div className="mt-2 flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void onVerifyToken()}
                        disabled={!controlToken || tokenState === "verifying"}
                        className={cn(
                          "rounded-md border border-border px-2.5 py-1 t-meta text-foreground transition-colors hover:bg-accent",
                          (!controlToken || tokenState === "verifying") &&
                            "cursor-not-allowed opacity-50",
                        )}
                      >
                        {tokenState === "verifying" ? "验证中…" : "验证授权"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowToken((s) => !s)}
                        className="rounded-md border border-border px-2.5 py-1 t-meta text-foreground transition-colors hover:bg-accent"
                      >
                        {showToken ? "隐藏" : "显示"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onControlToken("")}
                        className="rounded-md border border-border px-2.5 py-1 t-meta text-foreground transition-colors hover:bg-accent"
                      >
                        清除
                      </button>
                    </div>
                    <label htmlFor="token" className="sr-only">
                      控制令牌
                    </label>
                    <input
                      id="token"
                      type={showToken ? "text" : "password"}
                      value={controlToken}
                      onChange={(e) => onControlToken(e.target.value)}
                      placeholder="粘贴控制令牌"
                      autoComplete="off"
                      className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-[13px] text-foreground outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring"
                    />
                    <p className="t-meta text-muted-foreground">
                      服务端从环境变量 <code className="font-mono">JCC_CONTROL_TOKEN</code> 读取。
                      令牌只保存在当前标签页内存中，不写入 localStorage、Cookie 或 URL。
                    </p>
                </>
              </Field>
            </Group>
          ) : null}
        </div>
      </div>
    </PageBody>
  );
}

function Group({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-label={title}>
      <h2 className="t-section text-foreground">{title}</h2>
      {hint ? <p className="mt-1 t-meta text-muted-foreground">{hint}</p> : null}
      <div className="mt-4 space-y-5">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 t-meta font-medium text-foreground">{label}</p>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 t-meta text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 truncate text-right t-meta text-foreground", mono && "font-mono")}>
        {value}
      </dd>
    </div>
  );
}
