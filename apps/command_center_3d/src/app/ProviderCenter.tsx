/**
 * ProviderCenter — 设置 → 模型与 API
 *
 * WHAT THIS PAGE IS
 * -----------------
 * The operator connects a model provider here, with a pasted key, instead of
 * editing Windows environment variables. Five cards; the whole advanced surface
 * (base url, model id, timeout, plan type) hides behind 高级设置 so a first-time
 * user sees five status dots and nothing else.
 *
 * THE SECRET RULE, IN ONE PLACE
 * -----------------------------
 * The key is typed into a field, POSTed once to the backend, and then the field
 * is CLEARED. It is never read back: the API returns `configured` and `last4`,
 * and this file has no code path that could display a full key even if the
 * backend sent one. Nothing is written to localStorage, sessionStorage or a
 * cookie — a key in browser storage is a key in a backup.
 *
 * NAMING: `JEV` is this product. `Jev` is TypeSafe's System One model. The cards
 * name the vendor; the copy never calls a provider "JEV".
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Settings2,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { PageBody, PageHeader } from "../components/AppShell";
import { LoadingState } from "../components/States";
import { cn } from "../ui/cn";
import { API_BASE, authorizedFetch, openFetch } from "../lib/authorizedFetch";

/**
 * The seven provider states, in the product's language.
 *
 * DISCOVERY_UNSUPPORTED gets its own wording rather than being folded into
 * "无法连接": a vendor that publishes no model list is a working provider, and
 * telling an operator their MiniMax account is broken sends them to debug a
 * fault that does not exist.
 */
/**
 * Display states, decided by the BACKEND and rendered verbatim.
 *
 * There is deliberately no `DISCOVERY_UNSUPPORTED` entry any more, and that
 * omission is the fix. The wire value used to be mapped here to
 * 「已连接（无模型列表接口）」 — so a provider with a preset model name and NO
 * proven generation showed a green dot and the word 已连接, while a summary line
 * elsewhere mapped the same provider to 「无法连接」. Two tables, one provider,
 * two answers.
 *
 * Now the backend sends one `display_state` and this file only spells it.
 * `CONFIGURED_PENDING` is the state that says: credential present, capability
 * not yet proven.
 */
const STATE_TEXT: Record<string, string> = {
  CONNECTED: "已连接",
  // A remembered pass. Deliberately NOT 已连接: this provider was proven at a
  // moment in the past and has NOT been re-checked now, and the timestamp is
  // shown so the claim can be judged rather than taken on faith.
  STALE_VERIFIED: "上次验证通过",
  CONFIGURED_PENDING: "已配置 · 待验证",
  NOT_CONFIGURED: "需要 API Key",
  AUTH_FAILED: "API Key 无效",
  ENDPOINT_MISMATCH: "端点不匹配（区域或套餐不符）",
  PLAN_MISMATCH: "套餐不匹配",
  RATE_LIMITED: "已限流",
  UNAVAILABLE: "无法连接",
};

const STATE_DOT: Record<string, string> = {
  CONNECTED: "bg-status-ok",
  // Amber, not green: a remembered success is weaker than a live one and the
  // colour has to say so.
  STALE_VERIFIED: "bg-status-warn",
  CONFIGURED_PENDING: "bg-status-warn",
  NOT_CONFIGURED: "bg-muted-foreground/40",
  AUTH_FAILED: "bg-status-danger",
  ENDPOINT_MISMATCH: "bg-status-warn",
  PLAN_MISMATCH: "bg-status-warn",
  RATE_LIMITED: "bg-status-warn",
  UNAVAILABLE: "bg-status-danger",
};

const DISCOVERY_TEXT: Record<string, string> = {
  MODEL_DISCOVERY_SUPPORTED: "支持模型发现",
  MODEL_DISCOVERY_UNSUPPORTED: "不支持模型发现",
  MODEL_DISCOVERY_UNKNOWN: "尚未探测",
};

const REGION_TEXT: Record<string, string> = {
  "cn-beijing": "中国 · 北京",
  global: "全球",
  custom: "自定义",
  // MiniMax reports its own region code, which is not the same vocabulary as
  // the display-region names used by the other providers.
  cn: "中国",
};

/**
 * The three MiniMax dimensions, as INDEPENDENT choices.
 *
 * They used to be one field whose value was a plan name, which meant a China
 * Coding Plan key was sent to the international pay-as-you-go host and came back
 * "invalid api key (2049)" — a valid credential reported as a broken one.
 * Nothing here is derived from another: region decides the host, plan decides what
 * was bought, protocol decides how we speak. Picking one never implies another.
 */
/**
 * The provider-list lifecycle, as a PURE reducer.
 *
 * Extracted so it can be tested without a DOM, and because it encodes the
 * invariant that was violated: `loading` may only be true while a request is
 * genuinely in flight, so it is impossible to reach a state that renders
 * 「正在读取 Provider…」 with no request behind it. The previous design used a
 * null `cards` to mean not-started, loading AND failed, which is how a resolved
 * fetch could leave the page loading forever.
 */
export type LoadStatus = "idle" | "loading" | "success" | "error";
export interface LoadState {
  status: LoadStatus;
  cards: ProviderCard[];
  error: string | null;
}

export const initialLoadState: LoadState = { status: "idle", cards: [], error: null };

export type LoadAction =
  | { type: "begin" }
  | { type: "resolve"; cards: ProviderCard[] }
  | { type: "fail"; error: string };

export function loadReducer(state: LoadState, action: LoadAction): LoadState {
  switch (action.type) {
    case "begin":
      return { status: "loading", cards: state.cards, error: null };
    case "resolve":
      return { status: "success", cards: action.cards, error: null };
    case "fail":
      // `cards` is preserved so a failed refresh does not blank a list the
      // operator is already reading; `error` is ALWAYS set, so no failure can be
      // rendered as a spinner.
      return { status: "error", cards: state.cards, error: action.error };
    default:
      return state;
  }
}

/** True only when a request is outstanding. The invariant, in one place. */
export function isActivelyLoading(s: LoadState): boolean {
  return s.status === "loading";
}

const REGION_OPTIONS = [
  { value: "cn", label: "中国" },
  { value: "global", label: "全球" },
] as const;

const PLAN_OPTIONS = [
  { value: "coding_subscription_cn", label: "Coding Plan（中国）" },
  { value: "coding_subscription_global", label: "Coding Plan（全球）" },
  { value: "pay_as_you_go", label: "按量付费 API" },
] as const;

const PROTOCOL_OPTIONS = [
  { value: "openai_compatible", label: "OpenAI Compatible" },
  { value: "native", label: "Native（原生）" },
] as const;

export interface ProviderCard {
  id: string;
  display_name: string;
  purpose: string;
  region: string;
  state: string;
  /** MODEL_DISCOVERY_SUPPORTED | MODEL_DISCOVERY_UNSUPPORTED | MODEL_DISCOVERY_UNKNOWN */
  discovery?: string;
  /** "decision" | "generation" — decides which probe and which label apply. */
  verification_kind?: string;
  /** True only after a real decision round trip. Never set for generators. */
  decision_verified?: boolean;
  /** DECISION_VERIFIED | GENERATION_VERIFIED | NOT_VERIFIED */
  verification?: string;
  /**
   * The DISPLAY state, decided by the backend and rendered verbatim.
   * CONNECTED | CONFIGURED_PENDING | AUTH_FAILED | ENDPOINT_MISMATCH | ...
   *
   * The frontend must not derive this from `state`: that is what let the summary
   * and the card describe one provider two different ways.
   */
  display_state?: string;
  /** A short honest sentence from the backend, for the shown state. */
  display_state_reason?: string;
  /** PRESET = our own declaration, ACCOUNT = the service listed them. */
  model_source?: string;
  /** LIVE_READY | LAST_VERIFIED_READY | NOT_VERIFIED — which kind of claim this is. */
  verification_source?: string;
  /** Epoch seconds of the last REAL pass, or null. */
  last_verified_at?: number | null;
  last_verified_model?: string | null;
  last_latency_ms?: number | null;
  capabilities?: string[];
  /** True only after a real authenticated completion. The receipt for READY. */
  generation_verified?: boolean;
  /** True when `models` is OUR preset rather than the account's inventory. */
  models_are_preset?: boolean;
  base_url: string;
  default_model: string;
  models: string[];
  latency_ms?: number | null;
  detail?: string | null;
  is_jev: boolean;
  /** Three INDEPENDENT dimensions, reported separately by the backend. */
  plan_type?: string;
  api_protocol?: string;
  plan_types?: string[];
  credential?: { configured: boolean; last4: string | null; source: string | null };
  supports_tools?: boolean;
  supports_streaming?: boolean;
  role?: string;
}

export function ProviderCenter({
  controlToken,
  onTokenMissing,
}: {
  controlToken: string;
  /** Called when a write is attempted without a credential for the API. */
  onTokenMissing: () => void;
}) {
  /**
   * EXPLICIT status, not `cards === null`.
   *
   * The old shape used a null `cards` to mean three different things at once —
   * not started, loading, and failed — so a failure and a request that never
   * fired looked identical, and the UI sat on 「正在读取 Provider…」 forever with
   * no error to show. `loading` is now true only while a request is genuinely in
   * flight, which makes that state impossible to get stuck in. The logic lives in
   * loadReducer so it can be tested without a DOM.
   */
  const [state, dispatch] = useReducer(loadReducer, initialLoadState);
  const { status, cards, error } = state;
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "warn"; text: string } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  /** Bumped by the retry button. A retry re-runs THIS effect — no remount. */
  const [reloadNonce, setReloadNonce] = useState(0);

  const load = useCallback(async (isStale: () => boolean) => {
    dispatch({ type: "begin" });
    try {
      // A server-declared OPEN read, so no token is attached on purpose: putting
      // a credential on a request that does not need one suggests it does.
      const res = await openFetch<{ providers?: ProviderCard[] }>("/api/v1/providers");
      if (isStale()) return;
      if (!res.ok) {
        // A failure MUST land in `error`, never in a permanent loading state.
        dispatch({ type: "fail", error: res.error ?? `HTTP ${res.status}` });
        return;
      }
      dispatch({ type: "resolve", cards: res.data?.providers ?? [] });
    } catch (e) {
      if (isStale()) return;
      // A synchronous throw used to escape entirely, leaving the UI loading
      // forever with no error. The catch is the fix, not a nicety.
      dispatch({ type: "fail", error: e instanceof Error ? e.message : "无法读取 Provider 列表" });
    }
  }, []);

  useEffect(() => {
    // A LOCAL variable, not a ref. A ref survives StrictMode's
    // setup -> cleanup -> setup cycle, so `cancelledRef.current` would still be
    // true on the second setup and the fetch would never run again. React's own
    // docs are explicit about this; the local flag is scoped to one setup.
    let stale = false;
    const isStale = () => stale;

    void load(isStale);

    return () => {
      stale = true;
    };
  }, [load, reloadNonce]);

  const retry = useCallback(() => {
    setReloadNonce((n) => n + 1);
  }, []);

  // NOTE: there is deliberately no local `headers()` helper any more. The
  // previous one threw on an empty token, which turned every write button into a
  // silent no-op: no request left the browser, so the failure was indistinguishable
  // from a broken backend and left nothing in the Network panel to diagnose.
  // Header construction now lives in exactly one place — `authHeaders()` in
  // `lib/authorizedFetch.ts` — so a change cannot be applied to one caller and
  // missed in another.

  const save = useCallback(
    async (pid: string, apiKey: string, extra: Record<string, unknown>) => {
      setBusy(pid);
      setNotice(null);
      const res = await authorizedFetch<{
        credential?: { configured: boolean; last4: string | null };
        probe?: { state: string; detail?: string | null; latency_ms?: number; models?: string[]; provider: string; base_url: string };
        detail?: string;
      }>(controlToken, `/api/v1/providers/${pid}/credentials`, {
        method: "POST",
        body: { api_key: apiKey, ...extra },
      });

      if (res.authFailed) {
        // The credential reached the server and was refused by the control gate.
        // That is a DIFFERENT fact from a provider rejecting its own key, and
        // conflating them sends the operator to fix the wrong thing.
        setNotice({
          kind: "warn",
          text: "控制令牌未被服务端接受（401/403），请求未越过鉴权闸门。请检查「高级 → 控制令牌」。",
        });
        setEditing(null);
        return;
      }
      if (!res.ok) {
        setNotice({
          kind: "warn",
          text: res.error ? `保存失败：${res.error}` : `保存失败（HTTP ${res.status}）`,
        });
        return;
      }

      const p = res.data?.probe;
      // Reported from the PROBE, not from "the POST returned 200". A stored key
      // that cannot authenticate is not a success.
      if (p?.state === "READY") {
        setNotice({
          kind: "ok",
          text: `${p.base_url} 连接成功 · 延迟 ${p.latency_ms ?? "—"} ms · 模型 ${(p.models ?? []).length} 个`,
        });
      } else {
        const card = cards?.find((c) => c.id === p?.provider);
        setNotice({
          kind: "warn",
          text: `已保存并越过鉴权闸门，但 Provider 未就绪：${STATE_TEXT[p?.state ?? ""] ?? "未知"}${
            p?.detail && card ? ` · ${humanDetail({ ...card, detail: p.detail })}` : ""
          }`,
        });
      }
      setEditing(null);
      await load(() => false);
      setBusy(null);
    },
    [controlToken, cards, load],
  );

  const remove = useCallback(
    async (pid: string) => {
      setBusy(pid);
      setNotice(null);
      const res = await authorizedFetch(controlToken, `/api/v1/providers/${pid}/credentials`, {
        method: "DELETE",
      });
      if (res.authFailed) {
        setNotice({ kind: "warn", text: "控制令牌未被服务端接受（401/403），请求未越过鉴权闸门。" });
      } else if (!res.ok) {
        setNotice({ kind: "warn", text: `删除失败：${res.error ?? `HTTP ${res.status}`}` });
      } else {
        setNotice({ kind: "ok", text: "凭据已删除" });
        setEditing(null);
        await load(() => false);
      }
      setBusy(null);
    },
    [controlToken, load],
  );

  const probe = useCallback(
    async (pid: string) => {
      setBusy(pid);
      setNotice(null);
      const res = await authorizedFetch<{
        state: string; latency_ms?: number; models?: string[];
      }>(controlToken, `/api/v1/providers/${pid}/probe`, { method: "POST", body: {} });
      if (res.authFailed) {
        setNotice({ kind: "warn", text: "控制令牌未被服务端接受（401/403），请求未越过鉴权闸门。" });
      } else if (!res.ok) {
        setNotice({ kind: "warn", text: `测试失败：${res.error ?? `HTTP ${res.status}`}` });
      } else {
        setNotice({
          kind: res.data?.state === "READY" ? "ok" : "warn",
          text: `${STATE_TEXT[res.data?.state ?? ""] ?? "未知"}${
            res.data?.latency_ms != null ? ` · ${res.data.latency_ms} ms` : ""
          } · 模型 ${(res.data?.models ?? []).length} 个`,
        });
        await load(() => false);
      }
      setBusy(null);
    },
    [controlToken, load],
  );

  const isCustom = cards?.some((c) => c.id === "custom");

  return (
    <PageBody>
      <PageHeader
        title="模型与 API"
        description="连接 JEV 使用的模型服务。API Key 只保存在本机，由 Windows DPAPI 加密。"
        actions={
          <button
            type="button"
            onClick={retry}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[13px] text-foreground transition-colors hover:bg-accent"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            刷新
          </button>
        }
      />

      {error ? (
        <p className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-[14px] text-destructive" role="alert">
          无法读取 Provider 列表：{error}
        </p>
      ) : null}

      {notice ? (
        <p
          role="status"
          className={cn(
            "mb-4 flex items-start gap-2 rounded-md border px-4 py-3 text-[14px]",
            notice.kind === "ok"
              ? "border-status-ok/30 bg-status-ok/5 text-foreground"
              : "border-status-warn/30 bg-status-warn/5 text-foreground",
          )}
        >
          {notice.kind === "ok" ? (
            <Check className="mt-0.5 h-4 w-4 shrink-0 text-status-ok" aria-hidden="true" />
          ) : (
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-status-warn" aria-hidden="true" />
          )}
          <span className="min-w-0 flex-1 break-words">{notice.text}</span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label="关闭提示"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </p>
      ) : null}

      {!controlToken ? (
        <p className="mb-4 rounded-md border border-status-warn/30 bg-status-warn/5 px-4 py-3 text-[13px] text-muted-foreground">
          保存、测试和删除需要控制令牌。请先在「设置 → 安全」中提供。
        </p>
      ) : null}

      {status === "loading" || status === "idle" ? (
        <LoadingState label="正在读取 Provider…" />
      ) : status === "error" ? (
        /* A failure is a STATE with a way out, not a permanent spinner. The old
           code had no branch here at all, so a failed read left the page
           claiming it was still loading. */
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-5">
          <p className="text-[14px] text-foreground">
            无法读取 Provider 列表：{error ?? "未知错误"}
          </p>
          <button
            type="button"
            onClick={retry}
            className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[13px] text-foreground transition-colors hover:bg-accent"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            重试
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {cards.map((c) => (
            <ProviderRow
              key={c.id}
              card={c}
              busy={busy === c.id}
              canWrite
              expanded={editing === c.id}
              onToggle={() => setEditing(editing === c.id ? null : c.id)}
              onSave={(key, extra) => void save(c.id, key, extra)}
              onDelete={() => void remove(c.id)}
              onProbe={() => void probe(c.id)}
            />
          ))}

          {!isCustom ? (
            <p className="text-[13px] text-muted-foreground">
              需要 GLM、Kimi、OpenRouter 或本地 Ollama？配置「Custom OpenAI Compatible」即可，无需改动 JEV。
            </p>
          ) : null}
        </div>
      )}
    </PageBody>
  );
}

function ProviderRow({
  card,
  busy,
  canWrite = true,
  expanded,
  onToggle,
  onSave,
  onDelete,
  onProbe,
}: {
  card: ProviderCard;
  busy: boolean;
  /** Always true: a write is always ATTEMPTED so the server can refuse it. */
  canWrite?: boolean;
  expanded: boolean;
  onToggle: () => void;
  onSave: (apiKey: string, extra: Record<string, unknown>) => void;
  onDelete: () => void;
  onProbe: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [baseUrl, setBaseUrl] = useState(card.base_url);
  const [model, setModel] = useState(card.default_model);
  // Three independent selections. Changing the region does NOT rewrite the plan,
  // and changing the plan does NOT rewrite the protocol — an implicit link is
  // what sent a CN key to the global host in the first place.
  const [plan, setPlan] = useState(card.plan_type ?? "coding_subscription_cn");
  const [protocol, setProtocol] = useState(card.api_protocol ?? "openai_compatible");
  const [region, setRegion] = useState(card.region ?? "cn");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setBaseUrl(card.base_url);
    setModel(card.default_model);
    setPlan(card.plan_type ?? "coding_subscription_cn");
    setProtocol(card.api_protocol ?? "openai_compatible");
    setRegion(card.region ?? "cn");
  }, [card.base_url, card.default_model, card.plan_type, card.api_protocol, card.region]);

  useEffect(() => {
    if (expanded) inputRef.current?.focus();
  }, [expanded]);

  /**
   * The DISPLAY state, taken verbatim from the backend.
   *
   * The frontend used to map the wire `state` to its own wording, which meant two
   * independent renderers could describe the same provider differently at the
   * same moment — the summary said 「无法连接」 while the card said 「已连接」.
   * Both now read one field that the backend decided, so they cannot disagree.
   *
   * The invariant that matters: 「已连接」 appears only when
   * `generation_verified` (or `decision_verified` for a decision provider) is
   * true. A credential, a readable model list and a preset model name are not
   * evidence that anything can be generated.
   */
  const verified =
    card.verification_kind === "decision"
      ? card.decision_verified === true
      : card.generation_verified === true;
  const state = card.display_state ?? "NOT_CONFIGURED";
  const ok = state === "CONNECTED";
  const configured = card.credential?.configured ?? false;

  const submit = () => {
    if (!apiKey.trim()) return;
    onSave(apiKey.trim(), {
      base_url: baseUrl || undefined,
      model: model || undefined,
      display_name: card.id === "custom" ? baseUrl : undefined,
      // All three sent verbatim, with NOTHING derived from anything else.
      // The previous code collapsed plan and protocol into one field, so a
      // China Coding Plan key was sent to the international host and reported as
      // "invalid api key" — a valid credential reported as broken.
      plan_type: card.id === "minimax" ? plan : undefined,
      api_protocol: card.id === "minimax" ? protocol : undefined,
      region: card.id === "minimax" ? region : undefined,
    });
    // The key is cleared the moment it is handed over. It is never rendered
    // again, because the backend will not return it.
    setApiKey("");
    setShowKey(false);
  };

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <span
          className={cn("h-2 w-2 shrink-0 rounded-full", STATE_DOT[state] ?? "bg-muted-foreground/40")}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[15px] font-medium text-foreground">
              {card.display_name}
            </span>
            <span className="text-[12px] text-muted-foreground">
              {STATE_TEXT[state] ?? "未知"}
            </span>
            {configured ? (
              <span className="font-mono text-[11px] text-muted-foreground/70">
                ····{card.credential?.last4 ?? "----"}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-[12px] text-muted-foreground">{card.purpose}</p>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={onProbe}
            disabled={busy}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-[12px] text-foreground transition-colors hover:bg-accent",
              busy && "cursor-not-allowed opacity-50",
            )}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : null}
            测试连接
          </button>
          {configured ? (
            <button
              type="button"
              onClick={onDelete}
              disabled={busy}
              title="删除凭据"
              aria-label={`删除 ${card.display_name} 的凭据`}
              className={cn(
                "rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive",
                busy && "cursor-not-allowed opacity-50",
              )}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          ) : null}
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1.5 text-[12px] text-foreground transition-colors hover:bg-accent"
          >
            {configured ? (
              <>
                <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                更换
              </>
            ) : (
              <>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                配置
              </>
            )}
            <ChevronDown
              className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")}
              aria-hidden="true"
            />
          </button>
        </div>
      </div>

      {/* The reason comes from the BACKEND, in the same words it chose the state
          with. `humanDetail` used to re-derive it here, which is a second place
          for the card to disagree with the summary — so it is only used as a
          fallback for a server that predates `display_state_reason`. */}
      {(card.display_state_reason || (card.detail && !ok)) ? (
        <p className="border-t border-border px-4 py-2 text-[12px] text-muted-foreground">
          {card.display_state_reason || humanDetail(card)}
        </p>
      ) : null}

      {expanded ? (
        <div className="space-y-3 border-t border-border px-4 py-3">
          <div>
            <label htmlFor={`key-${card.id}`} className="mb-1 block text-[12px] text-muted-foreground">
              API Key
            </label>
            <div className="flex gap-2">
              <input
                id={`key-${card.id}`}
                ref={inputRef}
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={configured ? "输入新 Key 以替换" : "粘贴 API Key"}
                autoComplete="off"
                spellCheck={false}
                className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-[13px] text-foreground outline-none placeholder:font-sans placeholder:text-muted-foreground/70 focus-visible:border-ring"
              />
              <button
                type="button"
                onClick={() => setShowKey((s) => !s)}
                aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
                className="rounded-md border border-border px-2.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                {showKey ? (
                  <EyeOff className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Eye className="h-4 w-4" aria-hidden="true" />
                )}
              </button>
            </div>
            {/* A prefix hint, and only a hint.

                `sk-cp` is MiniMax's subscription / coding-plan key shape, and
                pointing it at the CN endpoint instead of the international
                pay-as-you-go host is the difference between READY and a bogus
                "invalid api key". So the hint appears. It is deliberately NOT a
                verification: a prefix is public, forgeable, and says nothing
                about whether the credential is live or which plan it carries.
                Only the server's own answer can settle that. */}
            {card.id === "minimax" && /^sk-cp/i.test(apiKey.trim()) ? (
              <p className="mt-1 text-[11px] text-status-warn" role="status">
                看起来是 Subscription / Coding Plan Key（sk-cp 前缀）。
                请确认「区域」与「套餐」与你的账号一致 —— 前缀本身不构成认证成功。
              </p>
            ) : null}
            <p className="mt-1 text-[11px] text-muted-foreground/80">
              提交后由本机 DPAPI 加密保存，不会回传、不写入浏览器存储。
            </p>
          </div>

          {/* Advanced settings. Hidden by default so the normal path is one
              field and one button. */}
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground">
              <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
              高级设置
              <ChevronDown
                className="h-3 w-3 transition-transform group-open:rotate-180"
                aria-hidden="true"
              />
            </summary>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <div>
                <label htmlFor={`url-${card.id}`} className="mb-1 block text-[12px] text-muted-foreground">
                  Base URL
                </label>
                <input
                  id={`url-${card.id}`}
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  spellCheck={false}
                  className="w-full rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] text-foreground outline-none focus-visible:border-ring"
                />
              </div>
              <div>
                <label htmlFor={`model-${card.id}`} className="mb-1 block text-[12px] text-muted-foreground">
                  默认模型
                </label>
                <input
                  id={`model-${card.id}`}
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  list={`models-${card.id}`}
                  spellCheck={false}
                  className="w-full rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] text-foreground outline-none focus-visible:border-ring"
                />
                <datalist id={`models-${card.id}`}>
                  {card.models.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
              </div>
              {/* MiniMax gets three selectors, because it has three decisions.
                  They are separate on purpose: changing 区域 does not rewrite
                  套餐, because an implicit link between them is what sent a
                  China key to the global host. */}
              {card.id === "minimax" ? (
                <>
                  <div>
                    <label htmlFor={`region-${card.id}`} className="mb-1 block text-[12px] text-muted-foreground">
                      区域
                    </label>
                    <select
                      id={`region-${card.id}`}
                      value={region}
                      onChange={(e) => setRegion(e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-[12px] text-foreground outline-none focus-visible:border-ring"
                    >
                      {REGION_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label htmlFor={`plan-${card.id}`} className="mb-1 block text-[12px] text-muted-foreground">
                      套餐
                    </label>
                    <select
                      id={`plan-${card.id}`}
                      value={plan}
                      onChange={(e) => setPlan(e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-[12px] text-foreground outline-none focus-visible:border-ring"
                    >
                      {PLAN_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label htmlFor={`proto-${card.id}`} className="mb-1 block text-[12px] text-muted-foreground">
                      协议
                    </label>
                    <select
                      id={`proto-${card.id}`}
                      value={protocol}
                      onChange={(e) => setProtocol(e.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-[12px] text-foreground outline-none focus-visible:border-ring"
                    >
                      {PROTOCOL_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                </>
              ) : null}
            </div>
            <dl className="mt-2 grid gap-x-4 gap-y-1 text-[11px] sm:grid-cols-2">
              <Meta label="区域" value={REGION_TEXT[card.region] ?? card.region} />
              <Meta
                label="模型发现"
                value={DISCOVERY_TEXT[card.discovery ?? ""] ?? card.discovery ?? "尚未探测"}
              />
              {/* The verification row is CAPABILITY-AWARE.
                  Jev decides; it does not write prose. Showing 「真实生成：已验证」
                  on a decision provider would claim a capability it does not
                  have, and hiding the row entirely would hide the evidence that
                  does exist. So the label follows the provider's kind. */}
              {card.verification_kind === "decision" ? (
                <Meta
                  label="决策能力"
                  value={
                    card.decision_verified
                      ? "已验证"
                      : card.state === "REAL_AUTH_MISSING" || card.state === "NOT_CONFIGURED"
                        ? "未验证（无凭据）"
                        : "未验证"
                  }
                />
              ) : (
                <Meta
                  label="真实生成"
                  value={
                    card.generation_verified
                      ? "已验证"
                      : card.state === "NOT_CONFIGURED"
                        ? "未验证（无凭据）"
                        : "未验证"
                  }
                />
              )}
              {card.last_verified_at ? (
                <Meta
                  label="上次验证"
                  value={`${card.last_verified_model ?? "—"} · ${new Date(
                    card.last_verified_at * 1000,
                  ).toLocaleString("zh-CN")}`}
                />
              ) : null}
              <Meta
                label="模型来源"
                value={
                  card.model_source === "PRESET"
                    ? "预设（未经账号确认）"
                    : card.model_source === "ACCOUNT"
                      ? "账号返回"
                      : "—"
                }
              />
              <Meta label="工具调用" value={card.supports_tools ? "支持" : "不支持"} />
              <Meta label="流式" value={card.supports_streaming ? "支持" : "不支持"} />
              <Meta label="可用模型" value={card.models.length > 0 ? card.models.join("、") : "—"} />
            </dl>
          </details>

          <button
            type="button"
            onClick={submit}
            disabled={!apiKey.trim() || busy}
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-4 py-2 text-[14px] font-medium transition-colors",
              !apiKey.trim() || busy
                ? "cursor-not-allowed bg-muted text-muted-foreground"
                : "bg-primary text-primary-foreground hover:brightness-110",
            )}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            保存并测试
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Turn a backend failure into a sentence a person can act on.
 *
 * The raw `detail` is a server contract: it contains state enums
 * (`AUTH_MISSING`, `REAL_AUTH_MISSING`) and an HTTP body. Showing it verbatim
 * means a person staring at `REAL_AUTH_MISSING` has to guess that it means
 * "paste an API key". So the known shapes are recognised and rewritten, and
 * anything unrecognised falls back to a generic line rather than leaking the
 * enum — the full text is still available to an operator through the API.
 */
function humanDetail(card: ProviderCard): string {
  const d = String(card.detail ?? "");
  if (!d) return "";
  if (/NOT_CONFIGURED|未配置 API Key/.test(d)) {
    return "尚未配置 API Key。点击「配置」粘贴 Key 后即可连接。";
  }
  if (/AUTH_FAILED|HTTP 401|HTTP 403|Invalid or missing/.test(d)) {
    return "API Key 无效或已失效，请重新配置。";
  }
  if (/DISCOVERY_UNSUPPORTED|不提供 \/models|不提供模型列表/.test(d)) {
    // Not a fault: the account is fine, the vendor just does not publish a
    // model list. Saying so plainly stops a needless debugging session.
    return "该 Provider 不提供模型列表接口（这不是故障）。真实生成可用时状态为「已连接」。";
  }
  if (/HTTP 429|rate.?limit/i.test(d)) {
    return "请求被限流，请稍后重试。";
  }
  if (/HTTP 5\d\d/.test(d)) {
    return "服务端返回错误，Provider 暂时不可用。请稍后重试。";
  }
  if (/HTTP 404/.test(d)) {
    return "该端点不存在。可在高级设置中确认 Base URL 与模型 ID。";
  }
  if (/尚未验证真实生成/.test(d)) {
    return "凭据有效、模型列表可读。点击「测试连接」完成一次真实生成验证后才能显示为「已连接」。";
  }
  if (/不在可用列表/.test(d)) {
    return `默认模型不可用，请在高级设置中改用可用模型：${
      card.models.slice(0, 3).join("、") || "（未获取到列表）"
    }`;
  }
  if (/transport failure|timed out|Connection|dns/i.test(d)) {
    return "无法连接到服务端，请检查网络或 Base URL。";
  }
  if (/未配置 Base URL/.test(d)) return "请在高级设置中填写 Base URL。";
  return "连接未就绪。点击「测试连接」查看详细原因。";
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 truncate font-mono text-foreground/80">{value}</dd>
    </div>
  );
}
