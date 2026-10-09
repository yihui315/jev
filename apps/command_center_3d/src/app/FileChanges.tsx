/**
 * FileChanges — what the task touched, in the task's own page.
 *
 * WHY THIS TAB IS A FIRST-CITIZEN
 * -------------------------------
 * "What did it actually change?" is the first question anyone asks after an agent
 * runs. Sending them to the Audit screen to find out is the behaviour this tab
 * replaces: the answer belongs next to the task, not in a separate console.
 *
 * WHERE THE DATA COMES FROM — AND WHAT HAPPENS WHEN IT DOES NOT EXIST
 * -------------------------------------------------------------------
 * The v1.3 domain API has no file-diff endpoint. It does expose the mission's
 * artifacts, which carry a real `uri`, `bytes` and `sha256` recorded by the
 * server at production time. So:
 *
 *  - If the backend reports artifacts, they are listed with their real sizes and
 *    types. A `text/*` or source-file artifact opens a viewer over its own
 *    recorded content when the server serves it.
 *  - If the backend reports NO artifacts, this tab says 「暂无文件变更记录」 and
 *    names the reason (the backend publishes no diff endpoint for this build).
 *
 * It NEVER synthesises a file list, a line count, or an addition/deletion
 * summary. A fabricated `+186 -42` is the single most damaging thing this
 * product could display, because the user would act on it.
 */
import { FileText, FolderGit2 } from "lucide-react";
import { cn } from "../ui/cn";
import { EmptyState } from "../components/States";
import { fmtBytes, type Artifact } from "./domainApi";

/** Does this artifact look like a source/text file we can try to render? */
export function isTextual(a: Artifact): boolean {
  const uri = (a.uri ?? "").toLowerCase();
  if (a.kind && /text|code|source|diff|markdown/i.test(a.kind)) return true;
  return /\.(ts|tsx|js|jsx|py|json|md|txt|css|html|ya?ml|toml|sh|sql|csv)$/.test(uri);
}

/** A one-letter status tag, from the server's own artifact status. */
function statusTag(status: string): { text: string; tone: string } {
  switch (status) {
    case "VERIFIED":
      return { text: "已确认", tone: "text-status-ok" };
    case "REJECTED":
      return { text: "已拒绝", tone: "text-status-danger" };
    case "SUPERSEDED":
      return { text: "已被替代", tone: "text-muted-foreground" };
    default:
      return { text: "已产出", tone: "text-status-info" };
  }
}

export function FileChanges({
  artifacts,
  loading,
  error,
  onOpenArtifact,
  className,
}: {
  artifacts: Artifact[];
  loading: boolean;
  error: string | null;
  onOpenArtifact: (a: Artifact) => void;
  className?: string;
}) {
  return (
    <section className={cn("flex flex-col", className)} aria-label="文件变更">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="t-section text-foreground">文件变更</h3>
        {artifacts.length > 0 ? (
          <span className="t-meta text-muted-foreground">
            共 {artifacts.length} 个文件 ·{" "}
            {fmtBytes(artifacts.reduce((n, a) => n + (a.bytes ?? 0), 0))}
          </span>
        ) : null}
      </div>

      {error ? (
        <EmptyState
          title="无法读取文件列表"
          hint={error}
          icon={FolderGit2}
        />
      ) : artifacts.length === 0 ? (
        <EmptyState
          title="暂无文件变更记录"
          hint={
            loading
              ? "正在读取后端记录…"
              : "后端未记录本任务的产出文件。当前版本的 API 没有提供逐行 diff 接口，因此这里不显示任何推算的改动。"
          }
          icon={FolderGit2}
        />
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border">
          {artifacts.map((a) => {
            const tag = statusTag(a.status);
            return (
              <li key={a.artifact_id}>
                <button
                  type="button"
                  onClick={() => onOpenArtifact(a)}
                  className="flex w-full items-center gap-3 bg-card px-4 py-3 text-left transition-colors hover:bg-accent/40"
                >
                  <FileText
                    className="h-4 w-4 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[14px] text-foreground">{a.name}</p>
                    <p className="truncate font-mono text-[11px] text-muted-foreground">
                      {a.uri}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <span className={cn("t-meta", tag.tone)}>{tag.text}</span>
                    <span className="t-meta text-muted-foreground">{fmtBytes(a.bytes)}</span>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/**
 * A viewer for one artifact's content.
 *
 * It renders the text the SERVER gave us. It does not compute a diff, because a
 * diff needs both revisions and the domain API only ever records the produced
 * one. Showing the produced content is useful and true; showing an invented diff
 * would be neither.
 */
export function DiffViewer({
  artifact,
  content,
  loading,
  error,
  onClose,
}: {
  artifact: Artifact | null;
  content: string | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  if (!artifact) return null;
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-[14px] font-medium text-foreground">{artifact.name}</p>
          <p className="truncate font-mono text-[11px] text-muted-foreground">{artifact.uri}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-md border border-border px-2.5 py-1.5 t-meta text-foreground transition-colors hover:bg-accent"
        >
          关闭
        </button>
      </div>
      <div className="max-h-[420px] overflow-auto">
        {loading ? (
          <p className="p-4 t-meta text-muted-foreground">正在读取文件内容…</p>
        ) : error ? (
          <p className="p-4 t-body text-destructive">{error}</p>
        ) : (
          <pre className="whitespace-pre-wrap break-words p-4 font-mono text-[12px] leading-relaxed text-foreground">
            {content}
          </pre>
        )}
      </div>
    </div>
  );
}
