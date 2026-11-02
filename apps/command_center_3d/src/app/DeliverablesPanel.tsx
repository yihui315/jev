/**
 * DeliverablesPanel — the task's outputs, on the task's page.
 *
 * Deliverables used to be a top-level menu, which meant the files a task
 * produced were separated from the task that produced them. They are the same
 * object seen from two sides, so they live together now.
 *
 * THE HASH IS REAL BUT NOT DEFAULT
 * -------------------------------
 * A SHA256 is an integrity fact, not a headline. It is available on the row and
 * copyable, and it is never the first thing the eye lands on — the file name and
 * what it is come first.
 */
import { useState } from "react";
import {
  Check,
  Copy,
  FileCode2,
  FileImage,
  FileText,
  FileVideo,
  FolderOpen,
  Globe,
} from "lucide-react";
import { cn } from "../ui/cn";
import { EmptyState } from "../components/States";
import { fmtBytes, fmtTime, type Artifact } from "./domainApi";

/** Pick an icon from the artifact's declared kind / uri extension. */
function iconFor(a: Artifact) {
  const uri = (a.uri ?? "").toLowerCase();
  if (a.kind && /image|图片/i.test(a.kind) || /\.(png|jpg|jpeg|gif|webp|svg)$/.test(uri)) {
    return FileImage;
  }
  if (a.kind && /video|视频/i.test(a.kind) || /\.(mp4|webm|mov|mkv)$/.test(uri)) {
    return FileVideo;
  }
  if (a.kind && /html|页面/i.test(a.kind) || /\.html?$/.test(uri)) return Globe;
  if (a.kind && /markdown|文档/i.test(a.kind) || /\.md$/.test(uri)) return FileText;
  if (/\.(ts|tsx|js|jsx|py|json|css|sh|sql|ya?ml|toml)$/.test(uri)) return FileCode2;
  return FileText;
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setDone(true);
          window.setTimeout(() => setDone(false), 1600);
        } catch {
          // Clipboard access can be denied; there is nothing useful to say, and
          // the value stays visible on screen either way.
          setDone(false);
        }
      }}
      aria-label={label}
      title={label}
      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      {done ? (
        <Check className="h-3.5 w-3.5 text-status-ok" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
    </button>
  );
}

export function DeliverablesPanel({
  artifacts,
  loading,
  error,
  className,
}: {
  artifacts: Artifact[];
  loading: boolean;
  error: string | null;
  className?: string;
}) {
  return (
    <section className={cn("flex flex-col", className)} aria-label="交付结果">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="t-section text-foreground">交付结果</h3>
        {artifacts.length > 0 ? (
          <span className="t-meta text-muted-foreground">{artifacts.length} 个文件</span>
        ) : null}
      </div>

      {error ? (
        <EmptyState title="无法读取交付结果" hint={error} />
      ) : artifacts.length === 0 ? (
        <EmptyState
          title="暂无交付文件"
          hint={loading ? "正在读取…" : "这个任务还没有产出可交付的文件。"}
        />
      ) : (
        <ul className="space-y-2">
          {artifacts.map((a) => {
            const Icon = iconFor(a);
            return (
              <li
                key={a.artifact_id}
                className="rounded-lg border border-border bg-card p-4"
              >
                <div className="flex items-start gap-3">
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[14px] font-medium text-foreground">{a.name}</p>
                    <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 t-meta sm:grid-cols-4">
                      <div>
                        <dt className="inline text-muted-foreground">类型：</dt>
                        <dd className="inline text-foreground">{a.kind || "未标注"}</dd>
                      </div>
                      <div>
                        <dt className="inline text-muted-foreground">大小：</dt>
                        <dd className="inline text-foreground">{fmtBytes(a.bytes)}</dd>
                      </div>
                      <div>
                        <dt className="inline text-muted-foreground">创建：</dt>
                        <dd className="inline text-foreground">{fmtTime(a.created_at)}</dd>
                      </div>
                      <div>
                        <dt className="inline text-muted-foreground">校验：</dt>
                        <dd className="inline">
                          {a.sha256 ? (
                            <span className="inline-flex items-center gap-1">
                              <span className="font-mono text-[11px] text-foreground">
                                {a.sha256.slice(0, 10)}…
                              </span>
                              <CopyButton value={a.sha256} label="复制 SHA256" />
                            </span>
                          ) : (
                            <span className="text-muted-foreground">未记录</span>
                          )}
                        </dd>
                      </div>
                    </dl>
                    <div className="mt-2 flex items-center gap-2">
                      <code className="min-w-0 flex-1 truncate rounded bg-background/60 px-2 py-1 font-mono text-[11px] text-muted-foreground">
                        {a.uri}
                      </code>
                      <CopyButton value={a.uri} label="复制路径" />
                      <a
                        href={a.uri}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 t-meta text-foreground transition-colors hover:bg-accent"
                      >
                        <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
                        打开
                      </a>
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
