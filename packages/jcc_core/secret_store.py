"""SecretStore — provider credentials on disk, encrypted with Windows DPAPI.

WHY THIS EXISTS
---------------
The operator should not have to edit Windows environment variables to connect a
model. So a credential arrives over HTTP from a settings page, and from that
moment it has exactly one legitimate resting place: a process that must not
leak it. This module is that place.

THE THREEFOLD BOUNDARY
----------------------
1. **At rest** — only ciphertext. A DPAPI blob under the current Windows user's
   key. A different Windows account cannot decrypt it, which is the property we
   want: the file is useless to anyone who lifts the disk.
2. **In flight** — the API never returns a secret. ``describe()`` yields
   ``configured`` and ``last4``; there is no method that hands a full key to a
   caller over HTTP, because no such method exists.
3. **In process** — plaintext lives in a memory cache, and the cache is the only
   place a provider reads it from. It is never formatted into a log line, never
   put in an exception message, and never serialised into an audit record.

DPAPI, NOT A HOME-GROWN CIPHER
-------------------------------
``CryptProtectData`` with ``CRYPTPROTECT_LOCAL_MACHINE`` deliberately NOT set is
scoped to ``CurrentUser``: the blob is bound to the caller's Windows credentials.
That is the whole reason for using the platform primitive instead of, say, an
AES key stored next to the ciphertext — which would be encryption of exactly the
thing an attacker who reads the file also gets.

READING ORDER
-------------
Environment first, then the store. An operator who exports ``MINIMAX_API_KEY`` in
a shell still wins, which is what a CI job needs; the store is the fallback that
makes the GUI path work.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
from ctypes import wintypes
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# DPAPI
# --------------------------------------------------------------------------- #

#: ``CryptProtectData`` flags. ``CRYPTPROTECT_UI_FORBIDDEN`` (0x1) suppresses the
#: interactive prompt, so a background service can never block on a dialog.
#: ``LOCAL_MACHINE`` is deliberately ABSENT -> the blob is CurrentUser-scoped.
CRYPTPROTECT_UI_FORBIDDEN = 0x1

#: Flags for ``CryptUnprotectData``.
CRYPTPROTECT_LOCAL_MACHINE = 0x4

class _DataBlob(ctypes.Structure):
    """The DATA_BLOB marshalling struct both DPAPI calls require.

    ``ctypes.wintypes`` has no DATA_BLOB, so it is declared here. ``pbData`` is a
    pointer to bytes; the buffer it points at must outlive the DPAPI call, which
    is why :func:`_to_blob` hands the buffer back to the caller.
    """

    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


class SecretStoreError(Exception):
    """Anything that went wrong protecting or unprotecting a secret."""


def _to_blob(data: bytes) -> tuple:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
    # The buffer must outlive the DPAPI call, so it is returned alongside.
    return blob, buf


def _from_blob(blob) -> bytes:
    if not blob.cbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def dpapi_protect(plaintext: bytes, *, entropy: bytes = b"") -> bytes:
    """Encrypt with DPAPI under the current Windows user.

    ``entropy`` is an *optional* additional secret input. It is NOT used to store
    the key — it is not stored anywhere, so a lost entropy value would make the
    data unrecoverable. It exists for callers that want domain separation, and
    defaults to empty so the common case needs no extra state.
    """
    if not isinstance(plaintext, (bytes, bytearray)):
        raise SecretStoreError("plaintext must be bytes")
    data_in, keep_in = _to_blob(bytes(plaintext))
    ent_in, keep_ent = _to_blob(entropy) if entropy else (None, None)
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data_in),
        None,                       # description: not shown, and not needed
        ctypes.byref(ent_in) if ent_in else None,
        None, None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise SecretStoreError(f"CryptProtectData failed (winerr {ctypes.GetLastError()})")
    try:
        return _from_blob(out)
    finally:
        if out.pbData:
            ctypes.windll.kernel32.LocalFree(out.pbData)


def dpapi_unprotect(ciphertext: bytes, *, entropy: bytes = b"") -> bytes:
    """Decrypt a blob produced by :func:`dpapi_protect`.

    Raises :class:`SecretStoreError` when the caller is not the Windows user who
    encrypted it. That failure is the security property, not a bug to work
    around, so it is never converted into an empty string.
    """
    if not isinstance(ciphertext, (bytes, bytearray)):
        raise SecretStoreError("ciphertext must be bytes")
    data_in, keep_in = _to_blob(bytes(ciphertext))
    ent_in, keep_ent = _to_blob(entropy) if entropy else (None, None)
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        None,
        ctypes.byref(ent_in) if ent_in else None,
        None, None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise SecretStoreError(
            "CryptUnprotectData failed: the stored secret belongs to a different "
            "Windows user, or the file was modified"
        )
    try:
        return _from_blob(out)
    finally:
        if out.pbData:
            ctypes.windll.kernel32.LocalFree(out.pbData)


#: Reported instead of a real failure when DPAPI is not usable (non-Windows).
class DpapiUnavailable(SecretStoreError):
    """DPAPI could not be loaded — the platform does not provide it."""


def dpapi_available() -> bool:
    try:
        return hasattr(ctypes.windll, "crypt32")
    except Exception:  # pragma: no cover - platform dependent
        return False


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

#: Provider id -> environment variable that can also supply the key.
ENV_FOR_PROVIDER: Dict[str, str] = {
    "typesafe_jev": "JEV_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "qwen_coding_plan_cn": "QWEN_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "custom": "CUSTOM_API_KEY",
}


class SecretStore:
    """Encrypted provider credentials with an in-process plaintext cache.

    Thread-safe: the settings page, the mission poller and the router can all
    reach for a key at once, and a torn read of the JSON file would look like a
    corrupt credential.
    """

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.RLock()
        # provider id -> plaintext. The ONLY place a full key exists in memory.
        self._cache: Dict[str, str] = {}
        self._load_cache()

    # -- disk ------------------------------------------------------------- #

    def _read_raw(self) -> Dict[str, str]:
        """Return the on-disk map of provider id -> base64 ciphertext."""
        if not os.path.exists(self._path):
            return {}
        with open(self._path, "r", encoding="utf-8") as fh:
            try:
                doc = json.load(fh)
            except ValueError:
                # A corrupt file is treated as empty rather than crashing the
                # service: the operator can re-enter keys. Silently rewriting it
                # is avoided so the damage is visible on disk.
                return {}
        if not isinstance(doc, dict):
            return {}
        return {k: v for k, v in doc.items() if isinstance(v, str)}

    def _write_raw(self, data: Dict[str, str]) -> None:
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # Written via a temp file + replace so a crash mid-write cannot leave a
        # half-written credential file that un-decrypts to garbage.
        tmp = f"{self._path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self._path)
        # Best-effort ACL tightening. The ciphertext is already DPAPI-bound, so
        # this is defence in depth rather than the control itself.
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def _load_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            for provider, b64 in self._read_raw().items():
                try:
                    self._cache[provider] = dpapi_unprotect(base64.b64decode(b64)).decode("utf-8")
                except Exception:
                    # A blob we cannot open is not a usable credential, and it is
                    # reported as absent by has_secret(). It is left on disk so
                    # the operator can see it exists and re-enter it.
                    continue

    # -- public API ------------------------------------------------------- #

    def set_secret(self, provider: str, secret: str) -> None:
        """Encrypt and persist one credential.

        Rejects an empty value rather than storing one: an empty "credential"
        would satisfy a presence check and then fail at request time, which is a
        worse failure than "not configured".
        """
        provider = str(provider).strip()
        secret = str(secret or "")
        if not provider:
            raise SecretStoreError("provider id is required")
        if not secret.strip():
            raise SecretStoreError("secret is required")
        with self._lock:
            blob = base64.b64encode(dpapi_protect(secret.encode("utf-8"))).decode("ascii")
            data = self._read_raw()
            data[provider] = blob
            self._write_raw(data)
            self._cache[provider] = secret

    def get_secret(self, provider: str) -> Optional[str]:
        """The plaintext, or None.

        Lookup order is store-then-environment for the GUI case, reversed to
        environment-first for the CI case: an operator who exported a variable
        in their shell expects it to win over whatever they clicked in a page last
        week, and a stale page should not silently override a live secret.
        """
        provider = str(provider).strip()
        env_name = ENV_FOR_PROVIDER.get(provider)
        if env_name:
            from_env = os.environ.get(env_name)
            if from_env and from_env.strip():
                return from_env.strip()
        with self._lock:
            return self._cache.get(provider)

    def has_secret(self, provider: str) -> bool:
        return bool(self.get_secret(provider))

    def delete_secret(self, provider: str) -> bool:
        """Remove one credential. Returns True when something was removed."""
        provider = str(provider).strip()
        with self._lock:
            data = self._read_raw()
            existed = data.pop(provider, None) is not None
            if existed:
                self._write_raw(data)
            self._cache.pop(provider, None)
            return existed

    def describe(self, provider: str) -> Dict[str, object]:
        """Everything the API may return about a credential.

        Deliberately has no code path that can emit the secret: a boolean and the
        last four characters are the whole vocabulary. `last4` exists so a person
        can tell two keys apart in a list; four characters cannot authenticate
        anything.
        """
        secret = self.get_secret(provider)
        env_name = ENV_FOR_PROVIDER.get(provider, "")
        return {
            "provider": provider,
            "configured": bool(secret),
            "last4": (secret[-4:] if secret and len(secret) >= 4 else None),
            "source": (
                ("environment" if env_name and os.environ.get(env_name) else "store")
                if secret else None
            ),
            "env_name": env_name or None,
        }

    def describe_all(self) -> List[Dict[str, object]]:
        return [self.describe(p) for p in sorted(ENV_FOR_PROVIDER)]

    def rotate(self, provider: str, secret: str) -> None:
        """Alias for set_secret, named for what the settings page calls it."""
        self.set_secret(provider, secret)
