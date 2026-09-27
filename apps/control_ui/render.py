"""HTML rendering for the 2D Control UI.

Pure functions: each takes a model dict from :mod:`control_ui.model` and returns
an HTML string. All dynamic text is escaped (``html.escape``); the UI never
renders a secret and never invents a value -- unreachable endpoints and missing
sections are shown explicitly.
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Tuple

CSS = """
:root{color-scheme:dark}
body{font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:#0e1116;color:#d6dde6;margin:0}
header{background:#161b22;border-bottom:1px solid #2a313c;padding:10px 16px;
display:flex;flex-wrap:wrap;gap:8px;align-items:center}
header .title{font-weight:700;margin-right:12px;color:#e6edf3}
nav a{color:#9fb0c0;text-decoration:none;padding:4px 8px;border-radius:6px;
border:1px solid transparent}
nav a:hover{background:#1f2630}
nav a.active{background:#22304a;border-color:#3a5a8c;color:#e6edf3}
main{padding:16px;max-width:1100px;margin:0 auto}
h1{font-size:18px;margin:0 0 12px}
h2{font-size:15px;margin:18px 0 8px;color:#aac4e0;border-bottom:1px solid #2a313c;
padding-bottom:4px}
h3{font-size:13px;margin:12px 0 4px;color:#8aa0b4}
table{border-collapse:collapse;width:100%;margin:6px 0 12px}
th,td{border:1px solid #2a313c;padding:5px 8px;text-align:left;vertical-align:top}
th{background:#161b22;color:#aac4e0}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;
font-weight:700}
.badge.demo{background:#5a3a00;color:#ffd17a;border:1px solid #8a5a00}
.badge.ok{background:#0f3d1f;color:#7ee2a0;border:1px solid #1c6b3a}
.badge.warn{background:#5a3a00;color:#ffd17a;border:1px solid #8a5a00}
.badge.err{background:#5a1414;color:#ff9a9a;border:1px solid #8a2020}
.badge.mut{background:#1f2630;color:#9fb0c0}
.kv{display:grid;grid-template-columns:minmax(160px,220px) 1fr;gap:2px 12px;
margin:4px 0}
.kv div:nth-child(odd){color:#8aa0b4}
.unreachable{background:#3a1414;border:1px solid #8a2020;color:#ff9a9a;
padding:10px;border-radius:8px}
.note{color:#8aa0b4;font-style:italic;font-size:13px}
.section-empty{color:#8aa0b4;font-style:italic}
footer{color:#6b7785;font-size:12px;padding:16px;border-top:1px solid #2a313c;
margin-top:24px}
"""

NAV: List[Tuple[str, Optional[str], str]] = [
    ("Dashboard", "/", "dashboard"),
    ("Sessions", "/sessions", "sessions"),
    ("Session Detail", None, "session_detail"),
    ("Decision Center", "/decisions", "decisions"),
    ("Security", "/security", "security"),
    ("Settings", "/settings", "settings"),
    ("Audit", "/audit", "audit"),
    ("System Health", "/health", "health"),
    ("v1.1 Activation", "/v1.1", "v1_1"),
]


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _badge(kind: str, text: str) -> str:
    return f'<span class="badge {esc(kind)}">{esc(text)}</span>'


def _kv(pairs: List[Tuple[str, Any]]) -> str:
    rows = "".join(f"<div>{esc(k)}</div><div>{esc(v)}</div>" for k, v in pairs)
    return f'<div class="kv">{rows}</div>'


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    if not rows:
        return f'<table><tr>{head}</tr></table><div class="section-empty">no rows</div>'
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows
    )
    return f'<table><tr>{head}</tr>{body}</table>'


def _unreachable(model_err: Dict[str, Any]) -> str:
    return (
        '<div class="unreachable"><strong>API UNREACHABLE</strong><br>'
        f"{esc(model_err.get('detail') or model_err.get('error'))}</div>"
    )


def page(title: str, active: str, body: str, first_sid: Optional[str] = None) -> str:
    nav_html = ""
    for label, href, key in NAV:
        if href is None:
            href = f"/sessions/{esc(first_sid)}" if first_sid else "/sessions"
        cls = "active" if key == active else ""
        nav_html += f'<a class="{cls}" href="{href}">{esc(label)}</a>'
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{esc(title)} — JCC Control UI</title><style>{CSS}</style></head>"
        f"<body><header><span class='title'>JCC CONTROL UI</span>"
        f"<nav>{nav_html}</nav></header><main>"
        f"<h1>{esc(title)}</h1>{body}"
        f"<footer>Read-only projection of the loopback control plane. "
        f"No control token is held or rendered by this UI.</footer>"
        f"</main></body></html>"
    )


# --------------------------------------------------------------------------- #
def render_dashboard(m: Dict[str, Any]) -> str:
    health = m["health"]
    if not health["ok"]:
        body = _unreachable(health)
        return page("Dashboard", "dashboard", body)

    h = health["data"]
    agents = m["agents"]
    agent_rows: List[List[Any]] = []
    for name in ("workbuddy", "claude", "codex", "hermes"):
        cap = agents.get(name) or {}
        status = cap.get("status", "UNKNOWN")
        kind = "ok" if str(status).upper() == "CONNECTED" else "warn"
        agent_rows.append([
            esc(name), _badge(kind, status), esc(cap.get("version", "")),
            esc(cap.get("aep", "")), esc(cap.get("verified", "")),
        ])

    risk = m["risk_by_band"]
    risk_rows: List[List[Any]] = [
        [esc(band), esc(count)] for band, count in sorted(risk.items())
    ] or [["(no assessed sessions)", ""]]

    demo_banner = (
        _badge("demo", f"DEMO x{m['demo_count']}") if m["demo_count"] else ""
    )

    body = f"""
    <p>{_badge('ok' if h.get('status')=='ok' else 'warn', 'CORE: '+esc(h.get('status')))}
       {_badge('mut', 'DB: '+( 'WAL' if h.get('wal') else 'non-WAL'))}
       {_badge('mut', 'JEV: '+esc(h.get('jev_provider')))}
       {_badge('mut', 'POLICY: '+esc(h.get('policy_version')))}
       {_badge('mut', 'LOCALHOST-ONLY' if h.get('localhost_only') else 'NOT-LOCALHOST')}
       {demo_banner}</p>

    <h2>Core / Database / Provider</h2>
    {_kv([
        ('Core status', h.get('status')),
        ('Database (WAL)', 'WAL' if h.get('wal') else 'non-WAL'),
        ('Jev provider', h.get('jev_provider')),
        ('Policy version', h.get('policy_version')),
        ('Localhost only', h.get('localhost_only')),
    ])}

    <h2>Agent capabilities (from config)</h2>
    {_table(['agent','status','version','aep','verified'], agent_rows)}

    <h2>Sessions</h2>
    <p>Sessions in store: <strong>{esc(m['session_count'])}</strong>
       &nbsp; problem-state sessions (escalated/quarantine/await/paused):
       <strong>{esc(m['problems'])}</strong></p>

    <h2>Risk (by band, derived from session assessments)</h2>
    {_table(['band','count'], risk_rows)}

    <h2>Budget (configured ceilings — config/jcc.yaml p3.shadow.budget)</h2>
    {_kv([(k, v) for k, v in sorted(m['budget_limits'].items())])}
    <p class="note">Live per-session budget usage is NOT exposed by any open
    endpoint; only the configured ceilings are shown (reported, not fabricated).</p>

    <h2>Errors</h2>
    <p>Problem-state sessions: <strong>{esc(m['problems'])}</strong>
       <span class="note">(derived from real session statuses)</span></p>
    """
    return page("Dashboard", "dashboard", body)


def render_sessions(m: Dict[str, Any]) -> str:
    sessions = m["sessions"]
    if not sessions["ok"]:
        return page("Sessions", "sessions", _unreachable(sessions))
    rows: List[List[Any]] = []
    first_sid = None
    for s in m["rows"]:
        if first_sid is None:
            first_sid = s.get("session_id")
        demo = _badge("demo", "DEMO") if s.get("_demo") else ""
        rows.append([
            f'<a href="/sessions/{esc(s.get("session_id"))}">{esc(s.get("session_id"))}</a>',
            esc(s.get("agent")),
            esc(s.get("project")),
            esc(s.get("status")),
            esc(s.get("event_count")),
            esc(s.get("evidence_count")),
            demo,
        ])
    body = _table(
        ["session_id", "agent", "project", "status", "events", "evidence", "flag"],
        rows,
    )
    return page("Sessions", "sessions", body, first_sid=first_sid)


def render_session_detail(m: Dict[str, Any], session_id: str) -> str:
    session = m["session"]
    if not session["ok"]:
        body = _unreachable(session) if session.get("error") == "API UNREACHABLE" else (
            f'<div class="unreachable">Session not found: {esc(session_id)} '
            f'(HTTP {esc(session.get("status"))})</div>'
        )
        return page(f"Session {session_id}", "session_detail", body)

    s = session["data"]
    assess = s.get("current_assessment") or {}
    directive = s.get("current_directive") or {}
    band = assess.get("band")
    band_kind = (
        "ok" if band in ("low", "medium") else "warn" if band == "high" else "err"
    )
    demo = _badge("demo", "DEMO") if m["is_demo"] else ""

    timeline_rows = [
        [esc(a.get("seq")), esc(a.get("timestamp")),
         esc(a.get("event_id")), esc(a.get("assessment")),
         esc(a.get("directive"))]
        for a in m["audit_rows"]
    ]

    decisions = m["decisions"]
    dec_rows: List[List[Any]] = []
    if decisions["ok"]:
        for d in decisions["data"]:
            dec_rows.append([
                esc(d.get("directive_id")), esc(d.get("action")),
                esc(d.get("policy_version")), esc(d.get("evidence_hash")),
                _badge("ok" if not d.get("acted") else "err",
                       "inert" if not d.get("acted") else "acted"),
            ])

    body = f"""
    <p>{_badge('mut', 'agent: '+esc(s.get('agent')))}
       {_badge('mut', 'status: '+esc(s.get('status')))}
       {_badge('mut', 'project: '+esc(s.get('project')))} {demo}</p>

    <h2>Timeline (audit entries for this session)</h2>
    {_table(['seq','timestamp','event_id','assessment','directive'], timeline_rows)}

    <h2>Assessment (live)</h2>
    {_kv([
        ('band', _badge(band_kind, band) if band else 'n/a'),
        ('risk_score', assess.get('risk_score')),
        ('provider_verified', assess.get('provider_verified')),
        ('blocker_kind', assess.get('blocker_kind')),
        ('needs_verify', assess.get('needs_verify')),
    ])}

    <h2>Policy (config)</h2>
    {_kv([
        ('version', m['policy'].get('version')),
        ('priority', ' > '.join(m['policy'].get('priority') or [])),
        ('live thresholds', m['policy'].get('thresholds_live')),
    ])}

    <h2>Directive (live)</h2>
    {_kv([
        ('action', directive.get('action')),
        ('policy_version', directive.get('policy_version')),
        ('evidence_hash', directive.get('evidence_hash')),
        ('acted', directive.get('acted')),
        ('actuation_reason', directive.get('actuation_reason')),
    ])}
    {_table(['directive_id','action','policy_version','evidence_hash','execution'],
            dec_rows)}

    <h2>Evidence</h2>
    <p>evidence_count: <strong>{esc(s.get('evidence_count'))}</strong>
       &nbsp; directive.evidence_hash:
       <strong>{esc(directive.get('evidence_hash') or 'none')}</strong></p>
    <p class="note">No open endpoint exposes evidence detail; the count is the
    real value from session state (reported, not fabricated).</p>

    <h2>Budget</h2>
    {_kv([(k, v) for k, v in sorted(m['budget_limits'].items())])}
    <p class="note">Configured ceilings only. Live per-session budget usage is
    NOT exposed by any open endpoint (reported, not fabricated).</p>

    <h2>Capability (config)</h2>
    {_kv([(k, v) for k, v in sorted((m['capability'] or {}).items())])}

    <h2>git-test</h2>
    <p class="note">No git-test endpoint exists in the control plane. Nothing to
    wire — reported, not fabricated (a screen section with no real source must
    say so rather than invent a value).</p>
    """
    return page(f"Session {session_id}", "session_detail", body,
                first_sid=session_id)


def render_decisions(m: Dict[str, Any]) -> str:
    decisions = m["decisions"]
    verify = m["verify"]
    verify_txt = ""
    if verify["ok"]:
        v = verify["data"]
        vk = "ok" if v.get("intact") else "err"
        verify_txt = _badge(vk, f"audit chain intact={v.get('intact')} entries={v.get('entries')}")
    dec_rows: List[List[Any]] = []
    chosen = None
    if decisions["ok"]:
        data = decisions["data"]
        if data:
            chosen = data[-1].get("action")
        for d in data:
            dec_rows.append([
                esc(d.get("directive_id")), esc(d.get("session_id")),
                esc(d.get("action")), esc(d.get("policy_version")),
                esc(d.get("evidence_hash")), _badge("ok" if not d.get("acted") else "err",
                                                  "inert" if not d.get("acted") else "acted"),
            ])
    priority = m["policy"].get("priority") or []
    rejected = [a for a in priority if a != chosen]
    demo = _badge("demo", "DEMO") if m.get("is_demo") else ""
    body = f"""
    <p>{verify_txt} {demo}</p>
    <h2>Chosen action</h2>
    <p>{_badge('ok', esc(chosen) if chosen else 'none')}</p>
    <h2>Rejected alternatives (policy priority order)</h2>
    <p>{', '.join(esc(a) for a in rejected) or '(none)'}</p>
    <h2>Evidence hash</h2>
    <p>{esc(decisions['data'][-1].get('evidence_hash') if decisions['ok'] and decisions['data'] else 'n/a')}</p>
    <h2>Assessment / threshold</h2>
    {_kv([
        ('policy_version', m['policy'].get('version')),
        ('live thresholds', m['policy'].get('thresholds_live')),
    ])}
    <h2>Directive verification (audit/verify)</h2>
    <p>{esc(verify.get('data') if verify['ok'] else verify.get('error'))}</p>
    <h2>Execution result</h2>
    <p>All directives are <strong>inert</strong> (acted=false, executable=false,
       actuation_reason=shadow_mode) — P0-P2 actuation is disabled.</p>
    <h2>Directives</h2>
    {_table(['directive_id','session_id','action','policy_version','evidence_hash','exec'],
            dec_rows)}
    """
    return page("Decision Center", "decisions", body)


def render_security(m: Dict[str, Any]) -> str:
    health = m["health"]
    if not health["ok"]:
        return page("Security", "security", _unreachable(health))
    h = health["data"]
    open_eps = m["open_endpoints"] or []
    probe = m["probe_status"]
    if m["probe_error"]:
        probe_txt = _badge("err", "API UNREACHABLE")
    elif probe in (401, 403):
        probe_txt = _badge("ok", f"REFUSED (HTTP {probe})")
    else:
        probe_txt = _badge("err", f"UNEXPECTED (HTTP {probe})")

    body = f"""
    <h2>Auth state</h2>
    {_kv([
        ('Control token', 'required for mutating endpoints (fail-closed)'),
        ('Control token env', h.get('localhost_only') and 'see Settings' or ''),
        ('Localhost only', h.get('localhost_only')),
    ])}
    <h2>Declared open (read-only) endpoints</h2>
    <p>{', '.join(esc(e) for e in open_eps) or '(none declared)'}</p>
    <h2>Refusal without token</h2>
    <p>Anonymous POST /api/assess &rarr; {probe_txt}</p>
    <p class="note">This UI never holds or sends the control token. The probe
    above is exactly the request an anonymous caller makes; a 2xx would be a
    critical failure of the fail-closed gate.</p>
    """
    return page("Security", "security", body)


def render_settings(m: Dict[str, Any]) -> str:
    srv = m["server"]
    jev = m["jev"]
    act = m["actuation"]
    pol = m["policy"]
    body = f"""
    <h2>Server (non-secret)</h2>
    {_kv([
        ('host', srv.get('host')),
        ('port', srv.get('port')),
        ('cors_origins', ', '.join(srv.get('cors_origins') or [])),
    ])}
    <h2>Control token</h2>
    <p>Env-var NAME: <strong>{esc(m['control_token_env'])}</strong>
       <span class="note">(value never stored in config; read at runtime, fail-closed)</span></p>
    <h2>Jev provider</h2>
    {_kv([
        ('provider', jev.get('provider')),
        ('model', jev.get('model')),
        ('base_url', jev.get('base_url')),
        ('feature_flags', jev.get('feature_flags')),
    ])}
    <h2>Actuation (P0-P2 disabled)</h2>
    {_kv([
        ('enabled', act.get('enabled')),
        ('allowed_actions', ', '.join(act.get('allowed_actions') or []) or '(none)'),
    ])}
    <h2>Policy</h2>
    {_kv([
        ('version', pol.get('version')),
        ('priority', ' > '.join(pol.get('priority') or [])),
        ('thresholds (live p3.shadow)', pol.get('thresholds_live')),
        ('thresholds (retired p1)', pol.get('thresholds_retired')),
    ])}
    <h2>Budget limits (p3.shadow.budget)</h2>
    {_kv([(k, v) for k, v in sorted(m['budget_limits'].items())])}
    <h2>Agent capabilities</h2>
    {_kv([(k, v) for k, v in sorted(m['agents'].items(), key=lambda kv: str(kv[0]))])}
    """
    return page("Settings", "settings", body)


def render_audit(m: Dict[str, Any]) -> str:
    audit = m["audit"]
    verify = m["verify"]
    if not audit["ok"]:
        return page("Audit", "audit", _unreachable(audit))
    rows: List[List[Any]] = []
    demo_banner = ""
    for a in audit["data"]:
        sid = str(a.get("session_id") or "")
        is_demo = sid.lower().startswith("demo-") or str(a.get("project") or "").upper() == "DEMO"
        if is_demo and not demo_banner:
            demo_banner = _badge("demo", "DEMO")
        rows.append([
            esc(a.get("seq")), esc(a.get("event_id")), esc(sid),
            esc(a.get("agent")), esc(a.get("assessment")), esc(a.get("directive")),
            esc(a.get("evidence_hash")), esc(a.get("entry_hash")),
            _badge("demo", "DEMO") if is_demo else "",
        ])
    verify_txt = ""
    if verify["ok"]:
        v = verify["data"]
        vk = "ok" if v.get("intact") else "err"
        verify_txt = _badge(vk, f"audit chain intact={v.get('intact')} entries={v.get('entries')}")
    body = f"""
    <p>{verify_txt} {demo_banner}</p>
    <h2>Audit tape (hash-chained, newest first)</h2>
    {_table(['seq','event_id','session_id','agent','assessment','directive',
             'evidence_hash','entry_hash','flag'], rows)}
    """
    return page("Audit", "audit", body)


def render_health(m: Dict[str, Any]) -> str:
    health = m["health"]
    if not health["ok"]:
        return page("System Health", "health", _unreachable(health))
    h = health["data"]
    body = f"""
    <h2>Health payload (/api/health)</h2>
    {_kv([
        ('status', h.get('status')),
        ('jev_provider', h.get('jev_provider')),
        ('actuation_enabled', h.get('actuation_enabled')),
        ('policy_version', h.get('policy_version')),
        ('localhost_only', h.get('localhost_only')),
        ('wal', h.get('wal')),
    ])}
    <h2>Declared open GET endpoints</h2>
    <p>{', '.join(esc(e) for e in (h.get('open_get_endpoints') or []))}</p>
    """
    return page("System Health", "health", body)


def render_notfound(path: str) -> str:
    body = f'<div class="unreachable">No such screen: {esc(path)}</div>'
    return page("Not found", "dashboard", body)


# --------------------------------------------------------------------------- #
# §20 -- the v1.1 activation screen
# --------------------------------------------------------------------------- #
#: A panel that could not be sourced renders its reason, NOT the page-level
#: marker the other screens use. The v1.0 screens are wholly API-backed, so
#: "this page could not reach the control plane" is a correct verdict for them;
#: five of the seven v1.1 panels are artifact-backed and remain true when the API
#: is down, so a page-wide unreachable verdict here would be false. Stating the
#: failure per panel is the only version of this that is true at both extremes.
def _panel_failure(name: str, source: Any, reason: Any, detail: Any) -> str:
    return (
        f'<div class="unreachable"><strong>PANEL NOT SOURCED</strong> '
        f'({esc(name)})<br>reason: <code>{esc(reason)}</code><br>'
        f"source: {esc(source)}<br>detail: {esc(detail)}</div>"
    )


def _panel_header(panel: Dict[str, Any]) -> str:
    if panel.get("available"):
        return _badge("ok", "sourced")
    return _badge("warn", "not sourced")


def _panel_source(panel: Dict[str, Any]) -> str:
    return f'<p class="note">source: {esc(panel.get("source"))}</p>'


def _rows_table(pairs: List[Tuple[str, Any]]) -> str:
    return _table(["field", "value"], [[k, v] for k, v in pairs])


def _render_provider(p: Dict[str, Any]) -> str:
    d = p["data"]
    badge_kind = "ok" if d.get("badge") in ("REAL", "REAL_UNVERIFIED") else "warn"
    out = [_panel_source(p), f"<p>{_badge(badge_kind, d.get('badge'))}</p>"]
    out.append(_rows_table([
        ("provider_mode (live)", d.get("provider_mode")),
        ("provider_requested_mode", d.get("provider_requested_mode")),
        ("provider_is_real", d.get("provider_is_real")),
        ("provider_verified", d.get("provider_verified")),
        ("provider_activation_state", d.get("provider_activation_state")),
        ("jev_provider", d.get("jev_provider")),
        ("policy_version", d.get("policy_version")),
    ]))
    out.append("<h3>Reconciliation against the §7 recorded verdict</h3>")
    recorded_rows = [
        ("artifact", d.get("verified_artifact")),
        ("recorded real_jev", d.get("recorded_real_jev")),
        ("recorded provider_mode", d.get("recorded_provider_mode")),
        ("recorded provider_verified", _tri(d.get("recorded_provider_verified"))),
    ]
    if d.get("recorded_reason"):
        # Only present when the artifact could not be used: an empty row would
        # read as "no problem", which is the opposite of what it means.
        recorded_rows += [
            ("artifact unreadable", d.get("recorded_reason")),
            ("artifact detail", d.get("recorded_detail")),
        ]
    out.append(_rows_table(recorded_rows))
    out.append(f'<p class="note">{esc(d.get("recorded_note"))}</p>')
    if d.get("safety_divergence"):
        out.append(
            f'<div class="unreachable">DIVERGENCE: {esc(d.get("safety_divergence"))}</div>')
    else:
        out.append('<p class="note">No safety divergence: the live provider is not '
                   "claiming to be real against an unverified interface card. A "
                   "difference in mode alone is not raised, because the live "
                   "process and the recorded emission may be different "
                   "configurations.</p>")
    return "".join(out)


def _render_capability_matrix(p: Dict[str, Any]) -> str:
    d = p["data"]
    rows = [[
        r.get("agent"), r.get("adapter_state"), r.get("verified"), r.get("degraded"),
        r.get("unsupported"), r.get("unverified"),
    ] for r in d.get("rows") or []]
    out = [_panel_source(p)]
    out.append(_top3(d.get("vocabulary")))
    out.append(_table(
        ["agent", "adapter_state", "VERIFIED", "DEGRADED", "UNSUPPORTED", "UNVERIFIED"],
        rows,
    ))
    out.append(_rows_table([
        ("agents", d.get("agent_count")),
        ("verified capabilities (total)", d.get("verified_total")),
        ("agents with VERIFIED telemetry", d.get("telemetry_verified")),
        ("actuable capabilities", d.get("actuable_capability_count")),
        ("actuable list", _join(d.get("actuable")) or "(none)"),
        ("section", d.get("section")),
        ("schema", d.get("schema")),
        ("probe script", d.get("probe_script")),
        ("probe measurement", d.get("probe_measurement")),
        ("evidence dir", d.get("evidence_dir")),
        ("repo head at emission", d.get("repo_head_recorded")),
    ]))
    return "".join(out)


def _render_shadow_stats(p: Dict[str, Any]) -> str:
    d = p["data"]
    return _panel_source(p) + _rows_table([
        ("status", d.get("status")),
        ("satisfied", _tri(d.get("satisfied"))),
        ("observed_decisions", d.get("observed_decisions")),
        ("observed_span_s", d.get("observed_span_s")),
        ("actuated_observations", d.get("actuated_observations")),
        ("chain_intact", _tri(d.get("chain_intact"))),
        ("defects", _join(d.get("defects")) or "(none)"),
        ("thresholds", _dict_text(d.get("thresholds"))),
    ])


def _render_actuation(p: Dict[str, Any]) -> str:
    d = p["data"]
    out = [_panel_source(p)]
    out.append(_rows_table([
        ("effective_level", d.get("effective_level")),
        ("configured_level", d.get("configured_level")),
        ("granted_ceiling", d.get("granted_ceiling")),
        ("gate status", d.get("gate_status")),
        ("gate satisfied", _tri(d.get("gate_satisfied"))),
        ("actuation_enabled (gate)", _tri(d.get("actuation_enabled_gate"))),
        ("actuation_enabled (config/live)", _tri(d.get("actuation_enabled_configured"))),
        ("defects", _join(d.get("defects")) or "(none)"),
        ("shadow tape present", _tri(d.get("db_present"))),
    ]))
    if d.get("mismatch"):
        out.append(f'<div class="unreachable">MISMATCH: {esc(d.get("mismatch"))}</div>')
    else:
        out.append('<p class="note">No mismatch between the config switch and the '
                   "gate. Where one of the two is unknown it is shown as blank "
                   "above, not assumed to agree.</p>")
    ladder = [[
        r.get("level"), r.get("rank"), r.get("gate_runs"), r.get("capability_count"),
        _join(r.get("permits")) or "(none)",
    ] for r in d.get("ladder") or []]
    out.append("<h3>Actuation ladder (emitted from the enforcing table)</h3>")
    out.append(_table(["level", "rank", "gate_runs", "capabilities", "permits"], ladder))
    if d.get("note"):
        out.append(f'<p class="note">{esc(d.get("note"))}</p>')
    return "".join(out)


def _render_pending_activation(p: Dict[str, Any]) -> str:
    d = p["data"]
    rows = [[
        i.get("item"), i.get("state"), i.get("why"), _join(i.get("defects")) or "(none)",
    ] for i in d.get("items") or []]
    out = [_panel_source(p)]
    out.append(f"<p>Still inert: <strong>{esc(d.get('count'))}</strong> "
               f"(gate status: {esc(d.get('headline_status'))})</p>")
    out.append(_table(["item", "state", "why", "blocking evidence"], rows))
    out.append(f'<p class="note">provider item source: {esc(d.get("provider_artifact"))}'
               + (f' (unreadable: {esc(d.get("provider_artifact_reason"))})'
                  if d.get("provider_artifact_reason") else "")
               + "</p>")
    return "".join(out)


def _render_budget(p: Dict[str, Any]) -> str:
    d = p["data"]
    out = [_panel_source(p)]
    out.append("<h3>Configured limits</h3>")
    out.append(_kv([(k, v) for k, v in sorted((d.get("limits") or {}).items())]))
    out.append(_rows_table([
        ("limit_count", d.get("limit_count")),
        ("cost provenance vocabulary", _join(d.get("provenance_values"))),
        ("scorer_tokens is a waived dimension", d.get("scorer_tokens_waived")),
        ("USAGE available", d.get("usage_available")),
        ("USAGE reason", d.get("usage_reason")),
    ]))
    out.append(f'<p class="note">{esc(d.get("usage_detail"))}</p>')
    return "".join(out)


def _render_directive(p: Dict[str, Any]) -> str:
    d = p["data"]
    latest = d.get("latest") or {}
    out = [_panel_source(p)]
    out.append(_rows_table([
        ("scope", d.get("scope")),
        ("directives", d.get("total")),
        ("signed", d.get("signed")),
        ("unsigned", d.get("unsigned")),
        ("acted", d.get("acted")),
        ("signed flag disagrees with signature blob",
         d.get("signed_flag_but_no_signature_or_reverse")),
        ("origins", _dict_text(d.get("origins"))),
        ("sessions covered", d.get("session_count")),
    ]))
    if latest:
        out.append("<h3>Latest directive</h3>")
        out.append(_rows_table([
            ("directive_id", latest.get("directive_id")),
            ("session_id", latest.get("session_id")),
            ("timestamp", latest.get("timestamp")),
            ("action", latest.get("action")),
            ("target_actuator", latest.get("target_actuator")),
            ("origin", latest.get("origin")),
            ("policy_version", latest.get("policy_version")),
            ("signed", _tri(latest.get("signed"))),
            ("acted", _tri(latest.get("acted"))),
            ("actuation_reason", latest.get("actuation_reason")),
        ]))
        out.append('<p class="note">The endpoint returns no verification verdict and '
                   "no executable flag, so this panel does not show one; the "
                   "columns it does return are listed under "
                   "&quot;fields returned&quot; below.</p>")
        out.append(f'<p class="note">fields returned: '
                   f'{esc(_join(d.get("fields_returned")))}</p>')
    else:
        out.append('<p class="section-empty">no directives returned by the endpoint</p>')
    return "".join(out)


#: Panel name -> renderer. Keyed by the name the data layer emits, so a panel
#: added to ``PANEL_NAMES`` without a renderer is caught by the test rather than
#: silently not drawn.
_PANEL_RENDERERS = {
    "provider": _render_provider,
    "capability_matrix": _render_capability_matrix,
    "shadow_stats": _render_shadow_stats,
    "actuation": _render_actuation,
    "pending_activation": _render_pending_activation,
    "budget": _render_budget,
    "directive": _render_directive,
}

#: Human headings, in the same order as the data layer's ``PANEL_NAMES``.
PANEL_HEADINGS = {
    "provider": "Provider (four-state badge)",
    "capability_matrix": "Capability Matrix (frozen §10 record)",
    "shadow_stats": "Shadow Stats (release-time readings)",
    "actuation": "Actuation Level (ladder + gate)",
    "pending_activation": "Pending Activation",
    "budget": "Budget (limits + cost provenance)",
    "directive": "Directive",
}


def _join(items: Any) -> str:
    return ", ".join(str(i) for i in (items or []))


def _tri(value: Any) -> str:
    """Render a three-state value. ``None`` means UNKNOWN and must say so.

    ``esc(None)`` is the empty string, which on this page would read as "the
    field is empty" -- indistinguishable from "the artifact does not carry it".
    The distinction is the whole point of ``chain_intact=None`` (the tape is
    absent, so the chain cannot be judged either way), so it is spelled out.
    """
    if value is None:
        return "(unknown)"
    if value is True:
        return "True"
    if value is False:
        return "False"
    return str(value)


def _dict_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "" if value is None else str(value)
    return ", ".join(f"{k}={value[k]}" for k in sorted(value))


def _top3(items: Any) -> str:
    joined = _join(items)
    if not joined:
        return ""
    return f'<p class="note">status vocabulary: {esc(joined)}</p>'


def render_v1_1(m: Dict[str, Any]) -> str:
    """The §20 screen. One block per panel; an unavailable panel says why.

    There is no fallback value and no placeholder anywhere on this page: a panel
    is either rendered from its declared source or rendered as a named failure
    with the reason the source could not be used. That is the same rule the v1.0
    screens follow for missing sections, applied to every panel instead of only
    to the ones somebody remembered to handle.
    """
    panels = m["panels"]
    sourced = _badge("mut", "panels sourced {}/{}".format(
        m["available_count"], len(m["order"])))
    scope_badge = _badge("mut", "scope: " + (m.get("session_id") or "all sessions"))
    parts: List[str] = [
        f"<p>{sourced} {scope_badge}</p>",
        '<p class="note">Every panel names its source. Panels backed by a frozen '
        "release artifact are marked release-time; the API-backed panels are "
        "live. Nothing on this page is invented and nothing is read from the "
        "database the running service owns.</p>",
    ]
    for name in m["order"]:
        panel = panels[name]
        heading = PANEL_HEADINGS.get(name, name)
        parts.append(f"<h2>{esc(heading)} {_panel_header(panel)}</h2>")
        if panel.get("available"):
            renderer = _PANEL_RENDERERS.get(name)
            parts.append(renderer(panel) if renderer
                         else f'<p class="section-empty">no renderer for {esc(name)}</p>')
        else:
            parts.append(_panel_failure(name, panel.get("source"),
                                        panel.get("reason"), panel.get("detail")))
    return page("v1.1 Activation", "v1_1", "".join(parts))

