/**
 * projectRoot — is this project path actually usable?
 *
 * THE PROBLEM THIS SOLVES
 * -----------------------
 * The backend's real boundary is `allowed_project_roots`, read from
 * `GET /api/v1/domain/home` → `project_scope.allowed_project_roots` (on this
 * host: `["D:\jev"]`). Projects recorded earlier can point OUTSIDE it — a
 * workspace that was `F:\jev` before the boundary moved is the exact case. A
 * project that cannot be used must be refused BY THE FRONTEND, before the user
 * spends effort on it and before the server returns a raw
 * `PROJECT_ROOT_OUT_OF_SCOPE` JSON body.
 *
 * WHY THERE IS NO `F:` → `D:` REWRITE ANYWHERE IN THIS FILE
 * -------------------------------------------------------
 * A string substitution would be a security bypass wearing a convenience
 * disguise. If the recorded root is out of scope, the ONLY correct outcomes are
 * "use a root the backend authorised" or "refuse and ask" — silently rewriting
 * the path would run the user's task against a directory they never chose, and
 * would do it while showing them the OLD path in the UI. So there is no rewrite
 * helper, no drive-letter mapping, and no fallback substitution here. The
 * resolver below only ever returns a root the backend itself published.
 *
 * THE RULE, STATED ONCE
 * ---------------------
 * A root is usable when the backend authorised it AND it is not a relative
 * fragment. An UNAVAILABLE boundary is not a pass: it means "we could not find
 * out", which must render as requiring re-selection rather than as allowed.
 */
import type { Project, ProjectScope } from "./domainApi";

/** Why a project cannot be used, in the user's language. */
export type RootVerdict =
  | { ok: true; root: string }
  | { ok: false; code: VerdictCode; reason: string };

export type VerdictCode =
  | "ROOT_OUT_OF_SCOPE"
  | "ROOT_MISSING"
  | "BOUNDARY_UNKNOWN"
  | "ROOT_NOT_ABSOLUTE";

/** How a path compares on Windows, and everywhere else. */
function normalize(p: string): string {
  let s = p.trim();
  if (!s) return "";
  // Collapse a trailing separator so `D:\jev\` and `D:\jev` are one root.
  // Length > 1 so a drive root such as `D:\` keeps its separator.
  while (s.length > 1 && (s.endsWith("\\") || s.endsWith("/"))) s = s.slice(0, -1);
  return s.toLowerCase();
}

/** Is `root` equal to, or nested under, one of `allowed`? */
export function isWithinAllowedRoots(root: string, allowed: string[]): boolean {
  const target = normalize(root);
  if (!target) return false;
  return allowed.some((a) => {
    const base = normalize(a);
    if (!base) return false;
    if (target === base) return true;
    // `D:\jev` must accept `D:\jev\apps\ui` but NOT `D:\jevold` — hence the
    // separator check. A plain `startsWith` would let a sibling directory whose
    // name merely begins with the allowed root's name through the gate.
    return target.startsWith(base.endsWith("\\") || base.endsWith("/") ? base : `${base}\\`) ||
      target.startsWith(`${base}/`);
  });
}

/** Is this a full path from a drive or UNC root, rather than a fragment? */
export function isAbsolutePath(p: string): boolean {
  const s = p.trim();
  if (!s) return false;
  return /^[a-zA-Z]:[\\/]/.test(s) || /^\\\\/.test(s);
}

/**
 * The roots the backend actually authorised.
 *
 * `null` when the boundary could not be read. `null` and `[]` are different and
 * both matter: `[]` means the server published an empty allow-list (it refuses
 * everything, fail-closed), `null` means we never got the answer. Neither is a
 * pass.
 */
export function allowedRootsFrom(scope: ProjectScope | null | undefined): string[] | null {
  if (!scope || !Array.isArray(scope.allowed_project_roots)) return null;
  return scope.allowed_project_roots.map(String).filter((s) => s.trim().length > 0);
}

/**
 * Judge one project against the backend's boundary.
 *
 * The check is on the project's RESOLVED `root_path` — the value the server
 * stored — and never on the raw text the user once typed. Checking the raw text
 * would let `F:\jev\..\jev` read as out of scope while resolving to something
 * inside it, i.e. a false refusal.
 */
export function verifyProjectRoot(
  project: Pick<Project, "root_path"> | null,
  allowed: string[] | null,
): RootVerdict {
  if (!project || !project.root_path || !project.root_path.trim()) {
    return { ok: false, code: "ROOT_MISSING", reason: "该项目没有记录目录位置。" };
  }
  if (allowed === null) {
    // We do not know the boundary, so we cannot certify this root. Rendering
    // this as "allowed" would be a guess; rendering it as "refused" would be a
    // lie about a server that may well permit it. It asks, which is the only
    // honest third option.
    return {
      ok: false,
      code: "BOUNDARY_UNKNOWN",
      reason: "无法读取后端允许的工作区范围，暂时不能确认该项目目录可用。",
    };
  }
  if (!isAbsolutePath(project.root_path)) {
    return {
      ok: false,
      code: "ROOT_NOT_ABSOLUTE",
      reason: "该项目记录的不是完整路径。",
    };
  }
  if (!isWithinAllowedRoots(project.root_path, allowed)) {
    return {
      ok: false,
      code: "ROOT_OUT_OF_SCOPE",
      reason: "该项目目录不在 JEV 允许的工作区范围内。",
    };
  }
  return { ok: true, root: project.root_path };
}

export const VERDICT_TITLE: Record<VerdictCode, string> = {
  ROOT_OUT_OF_SCOPE: "无法开始任务",
  ROOT_MISSING: "无法开始任务",
  BOUNDARY_UNKNOWN: "无法确认工作区范围",
  ROOT_NOT_ABSOLUTE: "无法开始任务",
};

/** The single sentence shown as the headline on the error card. */
export const VERDICT_HEADLINE: Record<VerdictCode, string> = {
  ROOT_OUT_OF_SCOPE: "当前项目目录不在 JEV 允许的工作区范围内。",
  ROOT_MISSING: "当前项目没有可用的目录，请重新选择。",
  BOUNDARY_UNKNOWN: "无法读取后端的允许范围，暂时不能提交任务。",
  ROOT_NOT_ABSOLUTE: "当前项目目录不是完整路径，请重新选择。",
};

/**
 * The root to offer as the one-click fix.
 *
 * It is ALWAYS a root the backend published — taken from `allowed[0]` as
 * returned. There is deliberately no hardcoded `D:\jev` fallback: if the server
 * ever moves its boundary, this must follow it, and a hardcoded path here would
 * become exactly the `F:` → `D:` rewrite in disguise.
 */
export function suggestedRoot(allowed: string[] | null): string | null {
  if (!allowed || allowed.length === 0) return null;
  return allowed[0];
}

/**
 * The projects that can actually be started, plus the ones that cannot.
 *
 * `usable` is what the composer may target; `blocked` is what the picker must
 * label as expired rather than offer silently.
 */
export function partitionProjects(
  projects: Project[],
  allowed: string[] | null,
): { usable: Project[]; blocked: Array<{ project: Project; verdict: RootVerdict }> } {
  const usable: Project[] = [];
  const blocked: Array<{ project: Project; verdict: RootVerdict }> = [];
  for (const p of projects) {
    const v = verifyProjectRoot(p, allowed);
    if (v.ok) usable.push(p);
    else blocked.push({ project: p, verdict: v });
  }
  return { usable, blocked };
}

/** Turn a server `PROJECT_ROOT_*` code into the same verdict vocabulary. */
export function verdictFromServerCode(code: string): VerdictCode | null {
  switch (code) {
    case "PROJECT_ROOT_OUT_OF_SCOPE":
      return "ROOT_OUT_OF_SCOPE";
    case "PROJECT_ROOT_MISSING":
      return "ROOT_MISSING";
    case "PROJECT_ROOT_NOT_ABSOLUTE":
      return "ROOT_NOT_ABSOLUTE";
    default:
      // `NO_PROJECT_ROOTS_CONFIGURED` and any future code: still a boundary
      // problem from the user's point of view, and the card must not fall back
      // to showing raw JSON.
      return "ROOT_OUT_OF_SCOPE";
  }
}
