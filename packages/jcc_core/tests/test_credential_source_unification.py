"""One credential source: a key saved in the settings must WORK everywhere.

THE BUG THIS LOCKS DOWN
------------------------
`/api/v1/domain/jev/select-executor` and `/jev/health` built their client with
``os.environ.get("JEV_API_KEY")`` instead of going through the registry. The
result was two truths at once:

    设置 → 模型与 API      TypeSafe Jev = 已连接（DPAPI 里有 key）
    首页 → JEV 自动选择     JEV_API_KEY is not set; the vendor model did not decide

The settings page was RIGHT. The mission path was reading a different source.
An operator's reasonable conclusion is that the product is broken, when in fact
the key was saved correctly and one route never looked for it.

The environment variable remains a legitimate BOOTSTRAP fallback inside
SecretStore — that is what lets a CI job inject a key. It may not be a second,
independent source of truth for the mission runtime.
"""
from __future__ import annotations

import pytest

from jcc_core.providers.jev_api import (
    STATE_REAL_AUTH_MISSING,
    STATE_REAL_READY,
    JevAuthError,
    JevProvider,
    decide_executor,
)

SECRET = "jev-test-secret-abcdefghijklmnopqrstuvwxyz0123"
EXECUTORS = ["workbuddy"]


def _task():
    return dict(
        task="read-only check", project_root="D:\\jev", task_type="general",
        available_executors=EXECUTORS,
    )


class _Answers:
    """
    A stand-in for a real client that records whether decide() was reached.

    It returns a genuine ``SystemOneResponse`` with real ``Answer`` objects
    rather than a pre-built decision, so the whole parse path runs — a stub that
    returned an ``ExecutorDecision`` would bypass exactly the code under test.
    """

    decided = False
    has_credential = True

    def decide(self, state, questions, model=None):
        type(self).decided = True
        from jcc_core.providers.jev_api import (
            ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse,
        )
        opts = list(questions["executor_choice"]["criteria"])
        return SystemOneResponse(
            model="jev-latest",
            answers={
                "executor_choice": ChoiceAnswer(
                    choice=opts[0], confidence=0.8, probabilities={opts[0]: 0.8}
                ),
                "safe_to_execute": NoulAnswer(noul=True, noul_probability=0.9),
                "risk_level": ScoreAnswer(
                    score=0.1, confidence=0.7, legend={"low": [0.0, 0.33]},
                    probabilities={"low": 0.9},
                ),
            },
            usage={"input_tokens": 10, "output_tokens": 5},
            latency_ms=900,
        )

    def health(self):
        return {"state": STATE_REAL_READY, "credential_env_name": "JEV_API_KEY"}


# --------------------------------------------------------------------------- #
# A credential in the store is enough
# --------------------------------------------------------------------------- #


class TestSecretStoreCredentialIsUsed:
    def test_env_unset_but_credential_present_still_decides(self, monkeypatch):
        """
        The exact production shape: no environment variable, a key in the store.
        Before the fix this returned "JEV_API_KEY is not set" — the source of
        truth was the variable, not the store.
        """
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        client = JevProvider(api_key=SECRET)
        assert client.has_credential

        _Answers.decided = False
        d = decide_executor(_Answers(), **_task())
        assert _Answers.decided is True
        assert d.executor == "workbuddy"
        assert d.source == "jev"
        # A real decision carries no fallback reason, so this is belt-and-braces
        # against the old wording leaking in from anywhere.
        assert "JEV_API_KEY is not set" not in (d.reason or "")

    def test_the_old_wording_is_gone_entirely(self):
        """
        The message named an environment variable, so it read as a diagnosis
        even when the variable was irrelevant. A missing credential is now
        reported as the provider's own state.
        """
        class NoCred:
            has_credential = False

            def health(self):
                return {"state": STATE_REAL_AUTH_MISSING}

        d = decide_executor(NoCred(), **_task())
        assert "JEV_API_KEY is not set" not in d.reason
        assert "REAL_AUTH_MISSING" in d.reason
        assert d.executor == ""          # fail-closed: no substituted executor
        assert d.needs_confirmation is True

    def test_env_unset_and_store_empty_is_auth_missing(self, monkeypatch):
        """The genuine missing-credential case, named by STATE not by variable."""
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        client = JevProvider(api_key=None)
        assert client.has_credential is False
        assert client.health()["state"] == STATE_REAL_AUTH_MISSING

    def test_env_still_works_as_a_bootstrap_fallback(self, monkeypatch, tmp_path):
        """
        The environment is a legitimate BOOTSTRAP path — that is what lets CI
        inject a key. This must keep working: the fix removed a second source of
        TRUTH, not a feature.
        """
        monkeypatch.setenv("JEV_API_KEY", "from-ci")
        from jcc_core.secret_store import SecretStore
        store = SecretStore(str(tmp_path / "c.json"))
        assert store.get_secret("typesafe_jev") == "from-ci"

    def test_env_is_read_first_and_that_is_deliberate(self, monkeypatch, tmp_path):
        """
        Documented, not merely tolerated: SecretStore prefers the ENVIRONMENT
        over the store, so an operator's live shell beats a stale page. Asserted
        so a future change to store-first is a deliberate edit rather than an
        accident.
        """
        monkeypatch.setenv("JEV_API_KEY", "from-env")
        from jcc_core.secret_store import SecretStore
        store = SecretStore(str(tmp_path / "c.json"))
        store.set_secret("typesafe_jev", "from-store")
        assert store.get_secret("typesafe_jev") == "from-env"


# --------------------------------------------------------------------------- #
# The same source for every provider, not just Jev
# --------------------------------------------------------------------------- #


class TestNoProviderHasItsOwnEnvPath:
    """
    The Jev bug was a duplicated credential read. Every generation provider goes
    through the registry, and the registry goes through SecretStore, so the same
    class of failure cannot recur for MiniMax, Qwen or DeepSeek.
    """

    @pytest.mark.parametrize("pid,env_name", [
        ("typesafe_jev", "JEV_API_KEY"),
        ("minimax", "MINIMAX_API_KEY"),
        ("qwen_coding_plan_cn", "QWEN_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
    ])
    def test_registry_resolves_each_provider_through_the_store(self, monkeypatch, tmp_path, pid, env_name):
        monkeypatch.delenv(env_name, raising=False)
        from jcc_core.secret_store import SecretStore

        store = SecretStore(str(tmp_path / "c.json"))
        store.set_secret(pid, SECRET)
        reg = _registry(store)
        if pid == "typesafe_jev":
            assert reg.jev().has_credential
        else:
            assert reg.get(pid) is not None

    def test_no_server_route_constructs_a_jev_client_from_the_environment(self):
        """
        Structural, so a future route cannot quietly reintroduce the second
        source: no non-test code may read an *_API_KEY variable directly.

        Comments are excluded on purpose — the fix left a comment NAMING the old
        read, and a naive scan would flag its own explanation.
        """
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        offenders = []
        # Scan CODE only. `inspect.getsource` is not used because these are
        # module-level functions inside a factory, and the reliable signal is
        # simply "is this line a comment" — the only lines that can legitimately
        # name the old read are the ones explaining why it was removed.
        pattern = re.compile(r"""environ(?:\.get\(|\[)\s*["'][A-Z_]*_API_KEY["']""")
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if pattern.search(line):
                    offenders.append(f"{path.name}:{i}")
        assert not offenders, "credential read straight from the environment: " + ", ".join(offenders)


def _registry(store):
    from jcc_core.provider_registry import ProviderRegistry
    return ProviderRegistry(store)
