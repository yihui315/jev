import { cn } from "./cn";

/** Skeleton — an explicit "not loaded yet" placeholder. Never a fake number. */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

/**
 * EmptyState — the honest "nothing here" panel.
 *
 * Every list in the product routes through this instead of rendering a blank
 * area, so "we have no data" is always distinguishable from "the UI broke".
 */
export function EmptyState({
  title,
  detail,
  action,
  icon: Icon,
  className,
}: {
  title: string;
  detail?: string;
  action?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center",
        className,
      )}
    >
      {Icon ? <Icon className="h-6 w-6 text-muted-foreground" /> : null}
      <div className="text-sm font-medium text-foreground">{title}</div>
      {detail ? (
        <div className="max-w-md text-xs text-muted-foreground">{detail}</div>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/**
 * InlineError — an error panel that keeps the failure text verbatim.
 * Truncating an error is how a real failure gets mistaken for a cosmetic one.
 */
export function InlineError({
  message,
  className,
}: {
  message: string;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive",
        className,
      )}
    >
      {message}
    </div>
  );
}

/**
 * ScopeNote — "this list is a partial view", said out loud.
 *
 * Wave 9.3. The bounded list reads make a partial view inevitable at scale; what
 * is NOT inevitable is the user not being told. Rendered only when there is
 * something to disclose (the caller passes the note from
 * `app/truncation.ts`, which returns `null` for the complete case), so the line
 * stays meaningful instead of becoming furniture.
 *
 * Deliberately a quiet, non-alarming style: a bounded view is normal operation,
 * not an error — and dressing it as an error would train the user to dismiss
 * the real ones.
 */
export function ScopeNote({
  note,
  className,
}: {
  note: string | null;
  className?: string;
}) {
  if (!note) return null;
  return (
    <p
      role="status"
      className={cn(
        "rounded-md border border-border bg-muted/40 px-3 py-1.5 text-[11px] text-muted-foreground",
        className,
      )}
    >
      {note}
    </p>
  );
}
