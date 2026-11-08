const COOKIE_NAME = "nr_paper_session";
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;
const MAX_BODY_BYTES = 180_000;
const MAX_EVENT_BYTES = 150_000;
const MAX_EVENTS_PER_SESSION = 10_000;
const ALLOWED_EVENTS = new Set([
  "paper.account.snapshot"
]);
const TRUSTED_EVENTS = new Set([
  "agent.run.started",
  "agent.run.completed",
  "agent.run.failed",
  "strategy.gate.passed",
  "strategy.gate.rejected",
  "risk.decision",
  "paper.server.order.created",
  "paper.server.order.filled",
  "paper.server.order.rejected",
  "paper.server.order.cancelled"
]);

export function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store, max-age=0",
      "x-content-type-options": "nosniff",
      ...headers
    }
  });
}

export function isSameOrigin(request) {
  const origin = request.headers.get("Origin");
  return !origin || origin === new URL(request.url).origin;
}

function base64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function fromBase64url(value) {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, char => char.charCodeAt(0));
}

async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

async function signPayload(payload, secret) {
  const encoded = base64url(new TextEncoder().encode(JSON.stringify(payload)));
  const signature = await crypto.subtle.sign("HMAC", await hmacKey(secret), new TextEncoder().encode(encoded));
  return encoded + "." + base64url(new Uint8Array(signature));
}

async function verifyToken(token, secret, now = Date.now()) {
  if (!token || typeof token !== "string" || token.length > 2048) return null;
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(fromBase64url(parts[0])));
  } catch (_) { return null; }
  if (!payload || payload.v !== 1 || typeof payload.sid !== "string" || !/^[0-9a-f-]{36}$/i.test(payload.sid)) return null;
  if (!Number.isFinite(payload.exp) || payload.exp <= Math.floor(now / 1000)) return null;
  if (!Number.isFinite(payload.iat) || payload.iat > Math.floor(now / 1000) + 60) return null;
  try {
    const valid = await crypto.subtle.verify("HMAC", await hmacKey(secret), fromBase64url(parts[1]), new TextEncoder().encode(parts[0]));
    return valid ? payload : null;
  } catch (_) { return null; }
}

function cookieValue(request) {
  const cookie = request.headers.get("Cookie") || "";
  const entry = cookie.split(";").map(part => part.trim()).find(part => part.startsWith(COOKIE_NAME + "="));
  return entry ? decodeURIComponent(entry.slice(COOKIE_NAME.length + 1)) : "";
}

function cookieHeader(token, maxAge) {
  return COOKIE_NAME + "=" + encodeURIComponent(token) + "; Path=/api/; Max-Age=" + maxAge + "; HttpOnly; Secure; SameSite=Strict";
}

export function requireConfig(env) {
  if (!env || !env.PAPER_DB || typeof env.PAPER_DB.prepare !== "function") {
    return { error: json({ ok: false, code: "PAPER_LEDGER_NOT_CONFIGURED", error: "服务端模拟账本尚未配置 D1 数据库" }, 503) };
  }
  if (typeof env.PAPER_LEDGER_SECRET !== "string" || env.PAPER_LEDGER_SECRET.length < 32) {
    return { error: json({ ok: false, code: "PAPER_LEDGER_SECRET_MISSING", error: "服务端模拟账本签名密钥未配置（至少 32 个字符）" }, 503) };
  }
  return {};
}

export async function readSession(request, env) {
  const token = cookieValue(request);
  const session = await verifyToken(token, env.PAPER_LEDGER_SECRET);
  if (!session) return { error: json({ ok: false, error: "模拟账本会话无效或已过期，请重新初始化" }, 401) };
  const row = await env.PAPER_DB.prepare(
    "SELECT session_id, expires_at, revoked_at FROM paper_sessions WHERE session_id = ?"
  ).bind(session.sid).first();
  if (!row || row.revoked_at || Date.parse(row.expires_at) <= Date.now()) {
    return { error: json({ ok: false, error: "模拟账本会话不存在、已撤销或已过期" }, 401) };
  }
  return { session };
}

export async function handlePaperSession(request, env) {
  if (request.method !== "POST") return json({ ok: false, error: "仅支持 POST" }, 405, { allow: "POST" });
  if (!isSameOrigin(request)) return json({ ok: false, error: "跨站请求已拒绝" }, 403);
  const config = requireConfig(env);
  if (config.error) return config.error;

  const existing = await readSession(request, env);
  if (existing.session) {
    return json({ ok: true, session: "active", expiresIn: SESSION_TTL_SECONDS });
  }

  const sid = crypto.randomUUID();
  const now = new Date();
  const expires = new Date(now.getTime() + SESSION_TTL_SECONDS * 1000);
  await env.PAPER_DB.prepare(
    "INSERT INTO paper_sessions (session_id, created_at, expires_at) VALUES (?, ?, ?)"
  ).bind(sid, now.toISOString(), expires.toISOString()).run();

  const token = await signPayload({
    v: 1,
    sid,
    iat: Math.floor(now.getTime() / 1000),
    exp: Math.floor(expires.getTime() / 1000)
  }, env.PAPER_LEDGER_SECRET);

  return json(
    { ok: true, session: "created", expiresAt: expires.toISOString(), expiresIn: SESSION_TTL_SECONDS },
    200,
    { "set-cookie": cookieHeader(token, SESSION_TTL_SECONDS) }
  );
}

async function parseBody(request) {
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return { error: json({ ok: false, error: "请求内容过大" }, 413) };
  try { return { body: JSON.parse(raw) }; }
  catch (_) { return { error: json({ ok: false, error: "JSON 请求体无效" }, 400) }; }
}

export async function appendTrustedEvent(request, env, event) {
  try {
    const config = requireConfig(env);
    if (config.error || !event || !TRUSTED_EVENTS.has(event.eventType)) return false;
    const auth = await readSession(request, env);
    if (auth.error) return false;
    if (typeof event.eventId !== "string" || !/^[a-zA-Z0-9._:-]{8,100}$/.test(event.eventId)) return false;
    const payloadJson = JSON.stringify(event.payload == null ? {} : event.payload);
    if (new TextEncoder().encode(payloadJson).byteLength > MAX_EVENT_BYTES) return false;
    await env.PAPER_DB.prepare(
      "INSERT OR IGNORE INTO paper_events (session_id, event_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)"
    ).bind(auth.session.sid, event.eventId, event.eventType, payloadJson, new Date().toISOString()).run();
    return true;
  } catch (_) {
    return false;
  }
}

export async function handlePaperLedger(request, env) {
  if (!["GET", "POST"].includes(request.method)) return json({ ok: false, error: "仅支持 GET / POST" }, 405, { allow: "GET, POST" });
  if (!isSameOrigin(request)) return json({ ok: false, error: "跨站请求已拒绝" }, 403);
  const config = requireConfig(env);
  if (config.error) return config.error;
  const auth = await readSession(request, env);
  if (auth.error) return auth.error;
  const sessionId = auth.session.sid;

  if (request.method === "GET") {
    const url = new URL(request.url);
    const after = Math.max(0, Math.floor(Number(url.searchParams.get("after") || 0)));
    const limit = Math.min(100, Math.max(1, Math.floor(Number(url.searchParams.get("limit") || 50))));
    if (!Number.isFinite(after)) return json({ ok: false, error: "after 游标无效" }, 400);
    const latestOnly = url.searchParams.get("latest") === "1";
    const eventType = url.searchParams.get("eventType") || "";
    const query = latestOnly
      ? (eventType
        ? "SELECT seq, event_id, event_type, payload_json, created_at FROM paper_events WHERE session_id = ? AND event_type = ? ORDER BY seq DESC LIMIT ?"
        : "SELECT seq, event_id, event_type, payload_json, created_at FROM paper_events WHERE session_id = ? ORDER BY seq DESC LIMIT ?")
      : "SELECT seq, event_id, event_type, payload_json, created_at FROM paper_events WHERE session_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?";
    const statement = latestOnly
      ? (eventType ? env.PAPER_DB.prepare(query).bind(sessionId, eventType, limit) : env.PAPER_DB.prepare(query).bind(sessionId, limit))
      : env.PAPER_DB.prepare(query).bind(sessionId, after, limit);
    const result = await statement.all();
    const rows = latestOnly ? (result.results || []).slice().reverse() : (result.results || []);
    const events = rows.map(row => {
      let payload = null;
      try { payload = JSON.parse(row.payload_json); } catch (_) {}
      return { seq: row.seq, eventId: row.event_id, eventType: row.event_type, payload, createdAt: row.created_at };
    });
    return json({ ok: true, events, nextAfter: events.length ? events[events.length - 1].seq : after });
  }

  const parsed = await parseBody(request);
  if (parsed.error) return parsed.error;
  const body = parsed.body || {};
  const incoming = Array.isArray(body.events) ? body.events : [body];
  if (!incoming.length || incoming.length > 20) return json({ ok: false, error: "每次必须提交 1–20 个事件" }, 400);

  const countRow = await env.PAPER_DB.prepare(
    "SELECT COUNT(*) AS count FROM paper_events WHERE session_id = ?"
  ).bind(sessionId).first();
  const existingCount = Number(countRow && countRow.count || 0);
  if (existingCount >= MAX_EVENTS_PER_SESSION) {
    return json({ ok: false, code: "PAPER_LEDGER_QUOTA", error: "当前模拟账本事件数量已达上限，请先导出记录或开启新会话" }, 429);
  }

  const accepted = [];
  for (const event of incoming) {
    if (!event || typeof event !== "object" || typeof event.eventId !== "string" || !/^[a-zA-Z0-9._:-]{8,100}$/.test(event.eventId)) {
      return json({ ok: false, error: "事件 ID 格式无效" }, 400);
    }
    if (!ALLOWED_EVENTS.has(event.eventType)) return json({ ok: false, error: "不允许的事件类型" }, 400);
    const payloadJson = JSON.stringify(event.payload == null ? {} : event.payload);
    if (new TextEncoder().encode(payloadJson).byteLength > MAX_EVENT_BYTES) {
      return json({ ok: false, error: "单个事件超过 150 KB 限制" }, 413);
    }
    const createdAt = new Date().toISOString();
    await env.PAPER_DB.prepare(
      "INSERT OR IGNORE INTO paper_events (session_id, event_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)"
    ).bind(sessionId, event.eventId, event.eventType, payloadJson, createdAt).run();
    const saved = await env.PAPER_DB.prepare(
      "SELECT seq, event_id, event_type, created_at FROM paper_events WHERE session_id = ? AND event_id = ?"
    ).bind(sessionId, event.eventId).first();
    if (saved) accepted.push({ seq: saved.seq, eventId: saved.event_id, eventType: saved.event_type, createdAt: saved.created_at });
  }
  return json({ ok: true, accepted, duplicateSafe: true });
}

export const __test = { verifyToken, signPayload, cookieValue, ALLOWED_EVENTS, TRUSTED_EVENTS };
