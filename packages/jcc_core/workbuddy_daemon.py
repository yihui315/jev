# -*- coding: utf-8 -*-
"""WorkBuddyDaemonAdapter — production execution path through the ALREADY
authenticated WorkBuddy daemon.

Design constraints (from release acceptance spec):
  * JEV must consume the daemon's *already-authenticated* capability. It must
    NOT copy the account token / Authorization secret / DB login / daemon
    in-memory credential into JCC.
  * The daemon is reached via the official WBIPC transport (a local named-pipe
    with a capability *ticket* handshake). The ticket is a per-session local
    IPC grant, not the user's account password — using it is exactly the
    supported "consume the host capability" path.
  * If the authenticated daemon exposes an agent-run capability, use it.
    Otherwise FAIL CLOSED — never fall back to an unauthenticated isolated
    serve, never scan ambient sessions.

Proven facts this module is built on (verified live against the running
WorkBuddy daemon on this host):
  * WBIPC `connect()` succeeds: the daemon is alive and the local ticket proves
    mutual authenticity. -> WORKBUDDY_DAEMON = reachable + authenticated.
  * The skill WBIPC channel exposes exactly two pipes regardless of client
    identity: `wb.entitlement` and `wb.request`. `wb.request` exposes only
    `http.fetch`. The agent-run pipes (`wb.buddyTask`, `wb.AgentAppRuntime`,
    `wb.conversations`, ...) are INTERNAL (Electron ipcMain) and are NOT
    exposed to external WBIPC clients.
  * Therefore: the daemon is reachable + authenticated, but no supported,
    credential-free external surface lets JEV spawn a model-backed native
    workload. `run_workload()` therefore raises `DaemonAgentRunUnsupported`
    (fail-closed) until WorkBuddy exposes such a capability to first-party
    components through an official, token-based interface.

This module is intentionally stdlib-only (like the vendored wbipc client) so it
can run inside the JCC server process without new dependencies.
"""

import os
import sys
import json
import time
import threading


# --- discovery of the official wbipc client (vendored inside the host) --------
# The host ships `workbuddy_ipc` as a stdlib-only module alongside a builtin
# plugin. We import it from that known location; this is the host's own client,
# not a reimplementation, so the handshake/encryption stay byte-identical.
_WBIPC_CANDIDATES = [
    # plugin path (current layout)
    r"D:/WorkBuddy/resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/builtin-plugins/tencent-docs-plugin/skills/tencent-saas-docs",
    # allow override via env (test harness / alternative layout)
    os.environ.get("JCC_WBIPC_PATH", ""),
]


def _load_wbipc():
    for base in _WBIPC_CANDIDATES:
        if not base or not os.path.isdir(base):
            continue
        if base not in sys.path:
            sys.path.insert(0, base)
        try:
            import workbuddy_ipc  # type: ignore
            return workbuddy_ipc
        except Exception:
            continue
    return None


# Pipes / methods that would constitute a model-backed agent-run capability if
# the daemon ever exposes them to external callers. Checked dynamically — if the
# daemon later grants one, this adapter picks it up with zero code changes.
_AGENT_RUN_PIPE_CANDIDATES = (
    "wb.buddyTask", "wb.AgentAppRuntime", "wb.conversations", "wb.conversationWindow",
)
_AGENT_RUN_METHOD_CANDIDATES = (
    "invoke", "run", "create", "execute", "submit", "wb:invoke", "spawn",
)


class DaemonNotAvailable(Exception):
    """Raised when no authenticated WorkBuddy daemon channel exists."""


class DaemonAgentRunUnsupported(Exception):
    """Raised when the daemon is authenticated but exposes NO supported
    external agent-run capability. This is the fail-closed production state:
    we will NOT fall back to an unauthenticated isolated serve."""


def discovery_scheme():
    """Return the WBIPC endpoint scheme (masked) or None. Does not read/print
    the ticket."""
    wb = _load_wbipc()
    if wb is None:
        return None
    try:
        p = wb.discovery_path()
    except Exception:
        return None
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
        ep = d.get("endpoint", "")
        return (ep[:28] + "...") if len(ep) > 28 else ep
    except Exception:
        return "<unreadable>"


class DaemonStatus(object):
    def __init__(self):
        self.alive = False
        self.authenticated = False
        self.endpoint_scheme = None
        self.pipes = []                 # names only — never secrets
        self.agent_run_capability = None  # "pipe/method" if supported, else None

    def as_dict(self):
        return {
            "alive": self.alive,
            "authenticated": self.authenticated,
            "endpoint_scheme": self.endpoint_scheme,
            "pipe_count": len(self.pipes),
            "agent_run_capability": self.agent_run_capability,
        }


class WorkBuddyDaemonAdapter(object):
    """Single production adapter for talking to the authenticated daemon.

    Lifecycle is: probe() -> (if capability) run_workload(). The adapter keeps a
    short-lived WBIPC connection per probe; long-lived workload streaming would
    reuse a held connection once an agent-run capability exists.
    """

    def __init__(self, config_dir=None, client_id="jcc-native", client_kind="jcc"):
        self._config_dir = config_dir or os.environ.get("WORKBUDDY_CONFIG_DIR") \
            or os.path.join(os.path.expanduser("~"), ".workbuddy")
        self._client_id = client_id
        self._client_kind = client_kind
        self._wb = _load_wbipc()
        self._lock = threading.Lock()

    # --- capability probe ---------------------------------------------------

    def probe(self):
        """Prove the daemon is alive + authenticated and detect any agent-run
        capability. Returns DaemonStatus. Never raises for 'unsupported' — that
        is a normal, reportable state."""
        st = DaemonStatus()
        st.endpoint_scheme = discovery_scheme()
        if self._wb is None:
            return st
        try:
            with self._wb.connect(
                client_id=self._client_id, client_kind=self._client_kind,
                client_version="1.0",
            ) as wb:
                st.alive = True
                pipes = wb.list_pipes()
                st.pipes = list(pipes)
                st.authenticated = True  # mutual ticket proof succeeded
                # detect agent-run capability if the daemon ever exposes one
                for p in pipes:
                    if p in _AGENT_RUN_PIPE_CANDIDATES:
                        try:
                            pc = wb.get_pipe(p)
                            for m in pc.methods:
                                if m in _AGENT_RUN_METHOD_CANDIDATES:
                                    st.agent_run_capability = "%s/%s" % (p, m)
                                    break
                        except Exception:
                            pass
                    if st.agent_run_capability:
                        break
        except self._wb.NotWorkBuddyEnv:
            st.alive = False
            st.authenticated = False
        except Exception:
            st.alive = False
            st.authenticated = False
        return st

    # --- workload execution (fail-closed until capability exists) -----------

    def run_workload(self, task, mission_id, jcc_run_id, correlation_id,
                    project_root, model=None, timeout_s=600):
        """Create a correlated native workload on the authenticated daemon.

        Returns a dict with real ids (workbuddy_workload_id, workbuddy_session_id)
        once an agent-run capability exists. Until then: fail closed.
        """
        st = self.probe()
        if not st.alive or not st.authenticated:
            raise DaemonNotAvailable("no authenticated WorkBuddy daemon channel")
        if not st.agent_run_capability:
            raise DaemonAgentRunUnsupported(
                "authenticated daemon exposes no external agent-run capability "
                "(only wb.request/http.fetch + wb.entitlement). Cannot spawn a "
                "model-backed native workload without copying daemon credentials, "
                "which is forbidden."
            )
        # --- reached only if the daemon grants an agent-run capability -------
        # (not currently reachable on this host; kept for when WorkBuddy exposes
        #  a first-party token-based task interface.)
        pipe, method = st.agent_run_capability.split("/", 1)
        with self._wb.connect(client_id=self._client_id, client_kind=self._client_kind,
                              client_version="1.0") as wb:
            pc = wb.get_pipe(pipe)
            resp = pc.invoke(method, {
                "task": task,
                "correlationId": correlation_id,
                "missionId": mission_id,
                "jccRunId": jcc_run_id,
                "cwd": project_root,
                "model": model,
            }, timeout=timeout_s)
            return {
                "workbuddy_workload_id": resp.get("workloadId") or resp.get("runId"),
                "workbuddy_session_id": resp.get("sessionId"),
                "daemon_pipe": pipe,
                "daemon_method": method,
            }


# --- production executor selection (single routing decision) ----------------
def select_production_executor(config_dir=None):
    """Return the adapter that should own native execution.

    Rule (release spec): if an authenticated WorkBuddy daemon is available, use
    WorkBuddyDaemonAdapter. It then fails closed at workload creation if no
    agent-run capability is exposed. We do NOT fall back to an unauthenticated
    isolated serve, and we do NOT scan ambient sessions.
    """
    return WorkBuddyDaemonAdapter(config_dir=config_dir)
