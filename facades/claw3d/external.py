"""Capability probe for the EXTERNAL Claw3D product (S-07).

Same honesty rule as the Session Center probe: the facade must still be real
when the 3D product it visualises is not installed — that state is OPTIONAL
VISUAL INTEGRATION, not a failure, and a probe is the only honest way to say so.

* it BINDS NOTHING and starts no process;
* a URL may only come from ``JCC_CLAW3D_URL`` / ``JCC_SESSION_CENTER_URL`` and
  must be loopback, otherwise it is refused (localhost-only rule);
* every failure is recorded as a reason, so an unavailable product reports WHY.

The result is never written into a payload at projection time — the projected
payload carries the value frozen in ``contract.json`` so "same input state
projects byte-identical output" holds unconditionally. Consumers read the live
result through :func:`probe_external_product`.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

PRODUCT = "JCC Claw3D"
ENV_URL_VARS = ("JCC_CLAW3D_URL", "JCC_SESSION_CENTER_URL")
#: Executable names a Windows install of Claw3D would put on PATH.
CANDIDATE_EXES = ("claw3d.exe", "claw3d")
#: Substrings matched against installed-product display names.
DISPLAY_NAME_HINTS = ("claw3d",)
DEFAULT_TIMEOUT_S = 1.0
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _path_dirs() -> List[str]:
    raw = os.environ.get("PATH", "")
    return [p for p in raw.split(os.pathsep) if p]


def _candidate_exes_found() -> List[str]:
    found: List[str] = []
    for name in CANDIDATE_EXES:
        for folder in _path_dirs():
            try:
                if os.path.exists(os.path.join(folder, name)):
                    found.append(os.path.join(folder, name))
                    break
            except OSError:
                continue
    return found


def _installed_display_names() -> List[str]:
    """Installed-product names from the uninstall hive (best effort, read-only)."""
    names: List[str] = []
    if os.name != "nt":
        return names
    try:  # pragma: no cover - Windows-only branch, skipped on other hosts
        import winreg

        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for i in range(0, winreg.QueryInfoKey(key)[1]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                                name, _ = winreg.QueryValueEx(sub, "DisplayName")
                                if isinstance(name, str):
                                    names.append(name)
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        return []
    return [n for n in names if any(h in n.lower() for h in DISPLAY_NAME_HINTS)]


def _probe_url(url: str, timeout: float, reasons: List[str]) -> bool:
    """One loopback GET with a hard timeout. Any error is an unavailable product."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        reasons.append(f"endpoint {url!r} is not parseable: {exc}")
        return False
    if parts.scheme not in ("http", "https") or parts.hostname not in LOOPBACK_HOSTS:
        reasons.append(
            f"endpoint {url!r} refused: only loopback endpoints are probed "
            "(localhost-only rule; a facade may not talk to a remote host)"
        )
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            resp.read(64)
            return int(resp.status) < 400
    except urllib.error.HTTPError as exc:
        return exc.code < 400
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"endpoint {url!r} did not answer within {timeout}s "
                       f"({type(exc).__name__}: {exc})")
        return False


def probe_external_product(timeout: float = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """Return what this machine can actually confirm about the product."""
    reasons: List[str] = []
    endpoint: str = ""

    for var in ENV_URL_VARS:
        url = (os.environ.get(var) or "").strip()
        if not url:
            continue
        endpoint = url
        if _probe_url(url, timeout, reasons):
            return {
                "available": True,
                "product": PRODUCT,
                "endpoint": url,
                "source": f"env:{var}",
                "reasons": reasons or [f"endpoint {url} answered"],
            }
        break

    exes = _candidate_exes_found()
    if exes:
        reasons.append(f"candidate executable(s) present: {exes}")
    installed = _installed_display_names()
    if installed:
        reasons.append(f"matching installed product(s): {installed}")

    if not endpoint and not exes and not installed:
        reasons.append(
            "no loopback endpoint configured, no candidate executable on PATH, "
            "no matching uninstall entry -> product not installed here"
        )
    return {
        "available": False,
        "product": PRODUCT,
        "endpoint": endpoint,
        "source": "probe",
        "reasons": reasons,
    }
