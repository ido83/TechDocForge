"""API key storage via the OS credential store.

Windows: Credential Manager. Linux: Secret Service (GNOME Keyring / KWallet).
Keys are never written to the settings file or logs. If no keychain backend is
available, keys are kept in memory for the current session only.
Environment variable TECHDOCFORGE_API_KEY overrides (useful for managed deployments).
"""

from __future__ import annotations

import logging
import os

import keyring
import keyring.errors

from . import APP_NAME

log = logging.getLogger(__name__)
ENV_VAR = "TECHDOCFORGE_API_KEY"
_session_keys: dict[str, str] = {}


def _account(base_url: str) -> str:
    return f"api-key:{base_url.rstrip('/')}"


def keyring_available() -> bool:
    try:
        kr = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - any backend init failure means "unavailable"
        return False
    name = type(kr).__module__ + "." + type(kr).__name__
    if "fail" in name or "null" in name.lower():
        return False
    if hasattr(kr, "backends") and not getattr(kr, "backends"):
        return False
    return True


def get_api_key(base_url: str) -> str | None:
    env = os.environ.get(ENV_VAR)
    if env:
        return env.strip()
    acct = _account(base_url)
    if acct in _session_keys:
        return _session_keys[acct]
    if not keyring_available():
        return None
    try:
        return keyring.get_password(APP_NAME, acct)
    except keyring.errors.KeyringError as e:
        log.warning("Keychain read failed: %s", type(e).__name__)
        return None


def set_api_key(base_url: str, key: str) -> bool:
    """Store key. Returns True if persisted to the OS keychain, False if session-only."""
    acct = _account(base_url)
    if keyring_available():
        try:
            keyring.set_password(APP_NAME, acct, key)
            _session_keys.pop(acct, None)
            return True
        except keyring.errors.KeyringError as e:
            log.warning("Keychain write failed: %s", type(e).__name__)
    _session_keys[acct] = key
    return False


def delete_api_key(base_url: str) -> None:
    acct = _account(base_url)
    _session_keys.pop(acct, None)
    if keyring_available():
        try:
            keyring.delete_password(APP_NAME, acct)
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError as e:
            log.warning("Keychain delete failed: %s", type(e).__name__)
