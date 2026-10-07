# -*- coding: utf-8 -*-
"""Real (non-mocked) acceptance of the WorkBuddy daemon adapter.

This test talks to the ACTUAL authenticated WorkBuddy daemon running on this
host via the official WBIPC transport. It asserts the real, observable state:

  * the daemon is alive and the local IPC ticket proves mutual authenticity
    -> WORKBUDDY_DAEMON = reachable + authenticated (real).
  * the daemon exposes NO external agent-run capability
    -> the structural P0 wall is real, not assumed.
  * run_workload() fails closed (DaemonAgentRunUnsupported)
    -> we never fall back to an unauthenticated isolated serve.

No account credential is read or copied. The WBIPC ticket is a local IPC grant.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jcc_core.workbuddy_daemon import (  # noqa: E402
    WorkBuddyDaemonAdapter, DaemonAgentRunUnsupported, DaemonNotAvailable,
    discovery_scheme, select_production_executor,
)


@pytest.fixture
def adapter():
    cfg = os.environ.get("WORKBUDDY_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".workbuddy")
    return WorkBuddyDaemonAdapter(config_dir=cfg)


def test_discovery_endpoint_present(adapter):
    # The WBIPC discovery file exists (local named-pipe + ticket).
    scheme = discovery_scheme()
    assert scheme is not None, "WBIPC endpoint not discoverable"
    assert "pipe" in scheme or "unix" in scheme or "://" in scheme, scheme


def test_daemon_alive_and_authenticated(adapter):
    st = adapter.probe()
    # The handshake succeeded against the live daemon on this host.
    assert st.alive is True, st.as_dict()
    assert st.authenticated is True, st.as_dict()
    # Exactly the two skill pipes are exposed to external clients.
    assert "wb.request" in st.pipes, st.pipes
    assert "wb.entitlement" in st.pipes, st.pipes


def test_no_external_agent_run_capability(adapter):
    st = adapter.probe()
    # Structural finding: the agent-run pipes are internal (Electron ipcMain)
    # and are NOT exposed to external WBIPC clients.
    assert st.agent_run_capability is None, st.as_dict()
    assert "wb.buddyTask" not in st.pipes, st.pipes
    assert "wb.AgentAppRuntime" not in st.pipes, st.pipes


def test_run_workload_fails_closed(adapter):
    with pytest.raises(DaemonAgentRunUnsupported):
        adapter.run_workload(
            task="Return exactly: JEV_WORKBUDDY_NATIVE_OK",
            mission_id="mission-probe", jcc_run_id="run-probe",
            correlation_id="corr-probe", project_root="D:/jev",
        )


def test_production_executor_selection():
    ex = select_production_executor()
    assert isinstance(ex, WorkBuddyDaemonAdapter)
