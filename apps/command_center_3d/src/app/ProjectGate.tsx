/**
 * ProjectGate — the friendly wall in front of a refused project root.
 *
 * WHAT IT REPLACES
 * ---------------
 * A raw `{"code":"PROJECT_ROOT_OUT_OF_SCOPE","allowed_project_roots":[...]}`
 * body rendered as page text. That is a server contract, not a sentence, and a
 * first-time user cannot act on it: it does not say what is wrong, what the
 * allowed set is, or what to press next.
 *
 * So the refusal is translated ONCE, here, into a card with four things a person
 * can act on: what happened, what you currently have, what is allowed, and two
 * buttons. The raw JSON survives — but only behind 「技术详情」, for the person who
 * is actually debugging.
 *
 * NO PATH REWRITE
 * ---------------
 * The 「选择 D:\jev」 button navigates to a root the BACKEND published
 * (`allowed[0]`), and 「重新选择项目」 opens the real picker. Neither edits a
 * drive letter: a `F:` → `D:` substitution would run work against a directory the
 * user never chose while showing them the old one, which is a correctness and
 * trust bug, not a convenience.
 */
import { ChevronDown, FolderOpen, ShieldAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { cn } from "../ui/cn";
import type { Project } from "./domainApi";
import {
  VERDICT_HEADLINE,
  VERDICT_TITLE,
  suggestedRoot,
  type RootVerdict,
} from "./projectRoot";

/** The backend's own error body, kept verbatim for the details disclosure. */
export interface ServerRefusal {
  code: string;
  detail: string;
  allowedRoots: string[];
}

export function ProjectGate({
  verdict,
  currentRoot,
  allowedRoots,
  refusal,
  onUseSuggested,
  onReselect,
  className,
}: {
  verdict: RootVerdict;
  /** The project root the user currently has, shown as 「当前：」. */
  currentRoot: string | null;
  /** The roots the backend authorised, shown as 「允许：」. */
  allowedRoots: string[];
  /** The server's raw refusal, shown only inside the details disclosure. */
  refusal?: ServerRefusal | null;
  /** Adopt the backend's first authorised root. Never a rewritten path. */
  onUseSuggested: (root: string) => void;
  /** Reopen the project picker. */
  onReselect: () => void;
  className?: string;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const suggestion = suggestedRoot(allowedRoots);
  const code = verdict.ok ? "ROOT_MISSING" : verdict.code;

  return (
    <section
      className={cn("rounded-lg border border-status-warn/30 bg-status-warn/5 p-5", className)}
      role="alert"
      aria-label={VERDICT_TITLE[code]}
    >
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-status-warn" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="t-section text-foreground">{VERDICT_TITLE[code]}</h3>
          {/* The one plain sentence. No codes, no JSON, no stack-shaped text. */}
          <p className="mt-1 t-body text-foreground">{VERDICT_HEADLINE[code]}</p>
          {!verdict.ok ? (
            <p className="mt-1 t-meta text-muted-foreground">{verdict.reason}</p>
          ) : null}

          <dl className="mt-4 space-y-1.5">
            <Row label="当前" value={currentRoot ?? "未选择项目"} invalid={code === "ROOT_OUT_OF_SCOPE"} />
            <Row label="允许" value={allowedRoots.length > 0 ? allowedRoots.join("、") : "后端未配置允许范围"} />
          </dl>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {suggestion ? (
              <button
                type="button"
                onClick={() => onUseSuggested(suggestion)}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-[14px] font-medium text-primary-foreground transition-colors hover:brightness-110"
              >
                <FolderOpen className="h-4 w-4" aria-hidden="true" />
                选择 {suggestion}
              </button>
            ) : null}
            <button
              type="button"
              onClick={onReselect}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-[14px] text-foreground transition-colors hover:bg-accent"
            >
              重新选择项目
            </button>
          </div>

          {/* The raw contract is still reachable — for the operator debugging it,
              never as the primary message. */}
          {refusal ? (
            <div className="mt-4">
              <button
                type="button"
                onClick={() => setShowDetail((s) => !s)}
                aria-expanded={showDetail}
                className="inline-flex items-center gap-1.5 t-meta text-muted-foreground transition-colors hover:text-foreground"
              >
                <ChevronDown
                  className={cn("h-3.5 w-3.5 transition-transform", showDetail && "rotate-180")}
                  aria-hidden="true"
                />
                技术详情
              </button>
              {showDetail ? (
                <pre className="mt-2 max-h-48 overflow-auto rounded-md border border-border bg-background/60 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
{JSON.stringify(
  {
    code: refusal.code,
    detail: refusal.detail,
    allowed_project_roots: refusal.allowedRoots,
  },
  null,
  2,
)}
                </pre>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function Row({
  label,
  value,
  invalid,
}: {
  label: string;
  value: string;
  invalid?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="shrink-0 t-meta text-muted-foreground">{label}：</dt>
      <dd
        className={cn(
          "min-w-0 flex-1 break-all font-mono text-[12px]",
          invalid ? "text-status-danger" : "text-foreground",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

/**
 * A one-line marker for a project that is present but unusable.
 *
 * Shown IN the picker, so a stale project is visibly expired rather than
 * silently offered and then refused after the user has written their task.
 */
export function ExpiredProjectRow({ name, reason }: { name: string; reason: string }) {
  return (
    <li className="flex items-start gap-2.5 rounded-md px-3 py-2 opacity-70">
      <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-warn" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[14px] text-muted-foreground line-through">{name}</p>
        <p className="t-meta text-status-warn">项目位置已失效 · {reason}</p>
      </div>
    </li>
  );
}

/** The composer's inline warning for a refused root, above the CTA. */
export function ProjectRootNotice({
  root,
  allowedRoots,
  onReselect,
}: {
  root: string;
  allowedRoots: string[];
  onReselect: () => void;
}) {
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-warn/30 bg-status-warn/5 px-4 py-2.5"
      role="status"
    >
      <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-status-warn" aria-hidden="true" />
      <span className="min-w-0 flex-1 text-[13px] text-foreground">
        项目位置已失效
        <span className="ml-1.5 font-mono text-[12px] text-muted-foreground">{root}</span>
        <span className="ml-1.5 text-muted-foreground">
          不在允许范围（{allowedRoots.join("、") || "未配置"}）内，请重新选择。
        </span>
      </span>
      <button
        type="button"
        onClick={onReselect}
        className="shrink-0 rounded-md border border-border px-2.5 py-1.5 t-meta text-foreground transition-colors hover:bg-accent"
      >
        重新选择
      </button>
    </div>
  );
}

/** A project whose root is inside the boundary, for the picker's usable list. */
export function UsableProjectRow({
  project,
  selected,
  onSelect,
}: {
  project: Project;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        role="option"
        aria-selected={selected}
        onClick={onSelect}
        className={cn(
          "flex w-full flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left transition-colors",
          selected ? "bg-primary-soft" : "hover:bg-accent",
        )}
      >
        <span className="w-full truncate text-[14px] text-foreground">{project.name}</span>
        <span className="w-full truncate font-mono text-[11px] text-muted-foreground">
          {project.root_path}
        </span>
      </button>
    </li>
  );
}
