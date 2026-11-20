"""Application settings (non-secret only) and endpoint validation."""

from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

from platformdirs import user_config_dir, user_log_dir

from . import APP_NAME
from .fsutil import atomic_write_text, restrict_permissions

log = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_GATEWAY_URL = "https://llm-gateway.example.com/v1"

DEFAULT_GUIDELINES = """\
- Audience: engineers and technical stakeholders.
- Use clear, concise, active voice. Prefer short sentences.
- Use the template's headings, section order and numbering exactly.
- Use tables for parameters, configuration options and comparisons.
- Put commands, code, file paths and identifiers in code formatting.
- Define acronyms on first use.
- Use Mermaid fenced blocks (```mermaid) for system, architecture, flow and sequence diagrams when useful.
- Never invent diagram nodes or relationships not present in the source.
"""


def config_dir() -> Path:
    p = Path(user_config_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    restrict_permissions(p, 0o700)
    return p


def log_dir() -> Path:
    p = Path(user_log_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    restrict_permissions(p, 0o700)
    return p


@dataclass
class Settings:
    """Persisted settings. Secrets (API keys) are NEVER stored here - see secrets_store.py."""

    provider: str = "ollama"  # "ollama" | "openai_compat"
    base_url: str = DEFAULT_OLLAMA_URL
    model: str = ""
    temperature: float = 0.2
    timeout_s: int = 600
    max_input_chars: int = 200_000
    max_output_chars: int = 500_000
    ollama_num_ctx: int = 16384
    verify_tls: bool = True
    use_os_trust_store: bool = True
    ca_bundle: str = ""
    allow_insecure_remote_http: bool = False
    use_system_proxy: bool = True
    guidelines: str = DEFAULT_GUIDELINES
    header_text: str = ""
    footer_text: str = ""
    logo_path: str = ""
    last_source_dir: str = ""
    last_template: str = ""

    @staticmethod
    def path() -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        p = cls.path()
        if not p.exists():
            return s
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("settings root is not an object")
        except (OSError, ValueError) as e:
            log.warning("Ignoring unreadable settings file: %s", e)
            return s
        # Strict schema: only known keys with the expected type are accepted.
        for f in fields(cls):
            if f.name not in data:
                continue
            v, default = data[f.name], getattr(s, f.name)
            if isinstance(default, bool):
                ok = isinstance(v, bool)
            elif isinstance(default, float):
                ok = isinstance(v, (int, float)) and not isinstance(v, bool)
                v = float(v) if ok else v
            elif isinstance(default, int):
                ok = isinstance(v, int) and not isinstance(v, bool)
            else:
                ok = isinstance(v, type(default))
            if ok:
                setattr(s, f.name, v)
        if s.provider not in ("ollama", "openai_compat"):
            s.provider = "ollama"
        return s

    def save(self) -> None:
        atomic_write_text(self.path(), json.dumps(asdict(self), indent=2), mode=0o600)


def is_loopback_host(host: str) -> bool:
    if host.lower() in ("localhost", "localhost."):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def validate_base_url(url: str, allow_insecure_remote_http: bool = False) -> str:
    """Validate an LLM endpoint URL. Plain HTTP is only allowed to loopback unless explicitly enabled."""
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("Endpoint URL must start with http:// or https://")
    if not p.hostname:
        raise ValueError("Endpoint URL has no host")
    if p.username or p.password:
        raise ValueError("Do not put credentials in the URL; use the API key field (stored in the OS keychain)")
    if p.query or p.fragment:
        raise ValueError("Endpoint URL must not contain a query string or fragment")
    if p.scheme == "http" and not is_loopback_host(p.hostname) and not allow_insecure_remote_http:
        raise ValueError(
            "Plain HTTP is only allowed for localhost. Use HTTPS for remote gateways "
            "(or explicitly allow insecure HTTP in Settings, not recommended)."
        )
    return url.rstrip("/")
