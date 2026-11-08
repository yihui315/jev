// Preview access gate for the JEV Cloud Staging trading desk.
//
// The staging Function enforces an HTTP Basic preview gate
// (JEV_PREVIEW_USER / JEV_PREVIEW_PASSWORD). The browser keeps these
// credentials in sessionStorage and attaches them to every /api/jev request.
// They are never written to logs and are only ever sent to the same-origin
// Cloudflare Function that enforces the gate.
//
// This is intentionally minimal: it does NOT implement the session cookie flow
// (the server-authoritative engine sets one, but we also send Basic on every
// call for robustness). It exists only so the six staging pages are usable
// behind the preview gate.

const KEY = "jev_preview_creds_v1";

export interface PreviewCreds {
  user: string;
  pass: string;
}

export function getPreviewCreds(): PreviewCreds | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as PreviewCreds;
    if (c && typeof c.user === "string" && typeof c.pass === "string" && c.pass.length > 0) {
      return c;
    }
  } catch {
    /* ignore malformed storage */
  }
  return null;
}

export function setPreviewCreds(c: PreviewCreds): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ user: c.user, pass: c.pass }));
  } catch {
    /* ignore storage failure */
  }
}

export function clearPreviewCreds(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export function authHeader(): string | null {
  const c = getPreviewCreds();
  if (!c || !c.user || !c.pass) return null;
  // Credentials are ASCII (preview user + hex secret); btoa is safe here.
  return "Basic " + btoa(`${c.user}:${c.pass}`);
}
