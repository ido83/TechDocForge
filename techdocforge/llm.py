"""LLM clients for Ollama (native API) and OpenAI-compatible gateways (LiteLLM, vLLM, Azure proxy, etc.)."""

from __future__ import annotations

import json
import logging
import ssl
import threading
from dataclasses import dataclass, field
from typing import Iterator

import httpx

from . import __version__

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


@dataclass
class LLMConfig:
    provider: str  # "ollama" | "openai_compat"
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)  # never shown in repr/logs
    temperature: float = 0.2
    timeout_s: int = 600
    num_ctx: int = 16384
    verify: bool | ssl.SSLContext = True
    trust_env: bool = True


def build_ssl(verify_tls: bool, use_os_trust_store: bool, ca_bundle: str) -> bool | ssl.SSLContext:
    """Build TLS verification config. Uses the OS trust store (corporate CAs) when available."""
    if not verify_tls:
        return False
    if ca_bundle:
        ctx = ssl.create_default_context(cafile=ca_bundle)
    elif use_os_trust_store:
        try:
            import truststore

            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:  # noqa: BLE001
            ctx = ssl.create_default_context()
    else:
        ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _headers(cfg: LLMConfig) -> dict[str, str]:
    h = {"Accept": "application/json", "User-Agent": f"TechDocForge/{__version__}"}
    if cfg.api_key:
        h["Authorization"] = f"Bearer {cfg.api_key}"
    return h


def _client(cfg: LLMConfig) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=15.0, read=float(cfg.timeout_s), write=60.0, pool=15.0),
        verify=cfg.verify,
        trust_env=cfg.trust_env,
        follow_redirects=False,  # never forward the bearer token to a redirect target
        headers=_headers(cfg),
    )


def _raise_for_status(r: httpx.Response) -> None:
    if r.is_redirect:
        raise LLMError(f"Server responded with a redirect ({r.status_code}); refusing to follow. Check the URL.")
    if r.status_code in (401, 403):
        raise LLMError(f"Authentication failed ({r.status_code}). Check the API key.")
    if r.status_code >= 400:
        try:
            r.read()
            detail = r.text[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise LLMError(f"HTTP {r.status_code} from LLM endpoint. {detail}".strip())


def _wrap_transport_errors(fn):
    def inner(*a, **kw):
        try:
            return (yield from fn(*a, **kw))
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot connect to endpoint: {e}") from None
        except httpx.TimeoutException:
            raise LLMError("The request timed out. Increase the timeout in Settings or use a faster model.") from None
        except httpx.HTTPError as e:
            raise LLMError(f"Network error: {type(e).__name__}: {e}") from None
        except json.JSONDecodeError:
            raise LLMError("Endpoint returned malformed data.") from None

    return inner


def list_models(cfg: LLMConfig) -> list[str]:
    try:
        with _client(cfg) as c:
            if cfg.provider == "ollama":
                r = c.get(f"{cfg.base_url}/api/tags")
                _raise_for_status(r)
                names = [m.get("name", "") for m in r.json().get("models", [])]
            else:
                r = c.get(f"{cfg.base_url}/models")
                _raise_for_status(r)
                names = [m.get("id", "") for m in r.json().get("data", [])]
    except httpx.ConnectError as e:
        raise LLMError(f"Cannot connect to endpoint: {e}") from None
    except httpx.HTTPError as e:
        raise LLMError(f"Network error: {type(e).__name__}: {e}") from None
    except (json.JSONDecodeError, AttributeError):
        raise LLMError("Endpoint returned an unexpected model list format.") from None
    return sorted(n for n in names if isinstance(n, str) and n)


@_wrap_transport_errors
def stream_chat(cfg: LLMConfig, messages: list[dict], cancel: threading.Event) -> Iterator[str]:
    """Yield text chunks as they arrive. Stops promptly when `cancel` is set."""
    if not cfg.model:
        raise LLMError("No model selected.")
    with _client(cfg) as c:
        if cfg.provider == "ollama":
            payload = {
                "model": cfg.model,
                "messages": messages,
                "stream": True,
                "think": False,
                "options": {
                    "temperature": cfg.temperature,
                    "num_ctx": cfg.num_ctx,
                    "num_predict": 8192,
                },
            }
            with c.stream("POST", f"{cfg.base_url}/api/chat", json=payload) as r:
                _raise_for_status(r)
                for line in r.iter_lines():
                    if cancel.is_set():
                        return
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if "error" in obj:
                        raise LLMError(f"Ollama error: {str(obj['error'])[:300]}")
                    piece = (obj.get("message") or {}).get("content") or ""
                    if piece:
                        yield piece
                    if obj.get("done"):
                        if obj.get("done_reason") == "length":
                            raise LLMError("The model reached its output limit. The document may be incomplete.")
                        return
        else:
            payload = {"model": cfg.model, "messages": messages, "stream": True, "temperature": cfg.temperature}
            with c.stream("POST", f"{cfg.base_url}/chat/completions", json=payload) as r:
                _raise_for_status(r)
                for line in r.iter_lines():
                    if cancel.is_set():
                        return
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    obj = json.loads(data)
                    if "error" in obj:
                        raise LLMError(f"Gateway error: {str(obj['error'])[:300]}")
                    choices = obj.get("choices") or []
                    if choices:
                        choice = choices[0]
                        piece = (choice.get("delta") or {}).get("content") or ""
                        if piece:
                            yield piece
                        finish_reason = choice.get("finish_reason")
                        if finish_reason == "length":
                            raise LLMError("The model reached its output limit. The document may be incomplete.")
                        if finish_reason:
                            return
