/**
 * PreviewPanel — the running app, embedded.
 *
 * THE FALLBACK IS THE FEATURE
 * ---------------------------
 * A preview that cannot load must not render as a blank white rectangle, which
 * looks like a broken product. So every failure mode here has its own named
 * state: 「后端未提供预览地址」 is a fact about the backend, 「预览暂不可用」 is a
 * fact about this attempt, and both are different from a silent empty frame.
 *
 * The frame is sandboxed (`sandbox` without `allow-same-origin`) so a previewed
 * page cannot reach this app's storage, and it is `lazy` so a heavy preview does
 * not compete with the page it lives on.
 */
import { useState } from "react";
import { ExternalLink, Maximize2, RefreshCw } from "lucide-react";
import { cn } from "../ui/cn";
import { EmptyState } from "../components/States";

export function PreviewPanel({
  url,
  reason,
  className,
}: {
  /** The dev-server URL, or null when the backend published none. */
  url: string | null;
  /** Why there is no preview, when `url` is null. */
  reason?: string | null;
  className?: string;
}) {
  // Bumped to force a reload; a key change remounts the iframe cleanly, which
  // is more reliable than reaching into a cross-document frame.
  const [reloadKey, setReloadKey] = useState(0);
  const [failed, setFailed] = useState(false);

  if (!url) {
    return (
      <section className={cn("flex flex-col", className)} aria-label="实时预览">
        <Header
          url={null}
          onReload={() => setReloadKey((k) => k + 1)}
          onOpen={() => {}}
          disabled
        />
        <EmptyState
          title="预览暂不可用"
          hint={reason || "后端没有为这个任务提供开发服务器地址。"}
        />
      </section>
    );
  }

  return (
    <section className={cn("flex flex-col", className)} aria-label="实时预览">
      <Header
        url={url}
        onReload={() => {
          setFailed(false);
          setReloadKey((k) => k + 1);
        }}
        onOpen={() => window.open(url, "_blank", "noopener,noreferrer")}
        disabled={false}
      />
      {failed ? (
        <EmptyState
          title="预览暂不可用"
          hint={`无法加载 ${url}。请确认开发服务器正在运行，然后重试。`}
          action={
            <button
              type="button"
              onClick={() => {
                setFailed(false);
                setReloadKey((k) => k + 1);
              }}
              className="rounded-md border border-border px-3 py-1.5 t-meta text-foreground transition-colors hover:bg-accent"
            >
              重试
            </button>
          }
        />
      ) : (
        <iframe
          key={reloadKey}
          src={url}
          title="任务实时预览"
          className="h-[560px] w-full rounded-lg border border-border bg-card"
          loading="lazy"
          referrerPolicy="no-referrer"
          // No allow-same-origin: the previewed document gets no access to this
          // origin's storage or DOM.
          sandbox="allow-scripts allow-forms allow-popups"
          onError={() => setFailed(true)}
        />
      )}
    </section>
  );
}

function Header({
  url,
  onReload,
  onOpen,
  disabled,
}: {
  url: string | null;
  onReload: () => void;
  onOpen: () => void;
  disabled: boolean;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h3 className="t-section text-foreground">实时预览</h3>
      <div className="flex items-center gap-1.5">
        {url ? (
          <span className="mr-1 hidden max-w-[280px] truncate font-mono text-[11px] text-muted-foreground sm:inline">
            {url}
          </span>
        ) : null}
        <IconBtn onClick={onReload} label="刷新预览" disabled={disabled}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
        </IconBtn>
        <IconBtn onClick={onOpen} label="在新窗口打开" disabled={!url}>
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </IconBtn>
        {url ? (
          <IconBtn
            onClick={() => {
              const el = document.querySelector<HTMLElement>("iframe[title='任务实时预览']");
              if (el?.requestFullscreen) void el.requestFullscreen().catch(() => undefined);
            }}
            label="全屏预览"
            disabled={false}
          >
            <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
          </IconBtn>
        ) : null}
      </div>
    </div>
  );
}

function IconBtn({
  onClick,
  label,
  disabled,
  children,
}: {
  onClick: () => void;
  label: string;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  );
}
