from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _message_text(body: dict[str, Any]) -> str:
    """Normalize OpenAI-compatible responses from different free providers."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if isinstance(content, str) and content.strip():
        return content.strip()
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.lstrip().startswith(("{", "```")):
            return value.strip()
    text = choice.get("text")
    return text.strip() if isinstance(text, str) else ""


def _provider_error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        detail = ""
        try:
            raw = exc.read(800).decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            error = parsed.get("error", parsed) if isinstance(parsed, dict) else parsed
            if isinstance(error, dict):
                detail = str(error.get("message", ""))
            elif isinstance(error, str):
                detail = error
        except (OSError, ValueError):
            pass
        return f"HTTP {exc.code}{': ' + detail[:180] if detail else ''}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "request timed out"
    if isinstance(exc, URLError):
        return f"network error: {str(exc.reason)[:160]}"
    return str(exc)[:180] or exc.__class__.__name__


def load_local_env() -> Path | None:
    """Load JARVIS secrets without overwriting explicitly set environment values."""
    candidates: list[Path] = []
    override = os.environ.get("JARVIS_ENV_FILE")
    if override:
        candidates.append(Path(override))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "JARVIS" / ".env")
    candidates.append(Path.cwd() / ".env")
    if not getattr(sys, "frozen", False):
        candidates.append(Path(__file__).resolve().parent.parent / ".env")

    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key.replace("_", "").isalnum():
                os.environ.setdefault(key, value)
        return path
    return None


@dataclass(frozen=True)
class FreeProvider:
    name: str
    key_name: str
    base_url: str
    preferred_models: tuple[str, ...]
    free_router_only: bool = False


class FreeModelRouter:
    """Fail over across user-supplied free-tier providers.

    OpenRouter is restricted to explicit ``:free`` models or its free router.
    Groq and Cerebras calls use their account-level free developer tiers and
    stop on quota exhaustion instead of switching to a paid model.
    """

    PROVIDERS = (
        FreeProvider(
            "cerebras",
            "CEREBRAS_API_KEY",
            "https://api.cerebras.ai/v1",
            ("gpt-oss-120b", "zai-glm-4.7", "gemma-4-31b"),
        ),
        FreeProvider(
            "groq",
            "GROQ_API_KEY",
            "https://api.groq.com/openai/v1",
            (
                "openai/gpt-oss-120b",
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant",
            ),
        ),
        FreeProvider(
            "cerebras-backup",
            "CEREBRAS_API_KEY1",
            "https://api.cerebras.ai/v1",
            ("gpt-oss-120b", "zai-glm-4.7", "gemma-4-31b"),
        ),
        FreeProvider(
            "openrouter",
            "OPENROUTER_API_KEY",
            "https://openrouter.ai/api/v1",
            (
                "openrouter/free",
                "poolside/laguna-s-2.1:free",
                "poolside/laguna-xs-2.1:free",
                "cohere/north-mini-code:free",
            ),
            free_router_only=True,
        ),
    )

    def __init__(self, timeout_seconds: int = 45):
        self.timeout_seconds = max(8, min(timeout_seconds, 45))
        self.env_path = load_local_env()
        self.last_provider: str | None = None
        self.last_model: str | None = None
        self.last_errors: dict[str, str] = {}
        self._model_cache: dict[str, tuple[float, list[str]]] = {}
        self._connection_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._cooldown_until: dict[str, float] = {}
        self.last_latency_ms: int | None = None

    @property
    def configured(self) -> bool:
        return any(os.environ.get(provider.key_name) for provider in self.PROVIDERS)

    def _available_models(self, provider: FreeProvider, key: str) -> list[str]:
        cached = self._model_cache.get(provider.name)
        now = time.monotonic()
        if cached and now - cached[0] < 600:
            return cached[1]
        request = Request(
            provider.base_url + "/models",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "Mozilla/5.0 JARVIS/1.0",
            },
        )
        with urlopen(request, timeout=min(self.timeout_seconds, 20)) as response:
            body = json.loads(response.read().decode("utf-8"))
        models = [
            item["id"]
            for item in body.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        self._model_cache[provider.name] = (now, models)
        return models

    def _select_model(self, provider: FreeProvider, key: str) -> str:
        available = set(self._available_models(provider, key))
        for model in provider.preferred_models:
            if model in available or model == "openrouter/free":
                if provider.free_router_only and not (
                    model == "openrouter/free" or model.endswith(":free")
                ):
                    continue
                return model
        if provider.free_router_only:
            free = sorted(model for model in available if model.endswith(":free"))
            if free:
                return free[0]
        raise RuntimeError("No approved free model is currently available.")

    def chat(self, messages: list[dict[str, str]], max_tokens: int = 900) -> str:
        """Return quickly, fail over predictably, and avoid repeatedly hitting dead routes."""
        self.last_errors = {}
        configured = [
            provider
            for provider in self.PROVIDERS
            if os.environ.get(provider.key_name, "").strip()
        ]
        if self.last_provider:
            configured.sort(key=lambda provider: provider.name != self.last_provider)
        started = time.monotonic()
        deadline = started + min(28, max(14, self.timeout_seconds))
        skipped: list[FreeProvider] = []

        for provider in configured:
            now = time.monotonic()
            if self._cooldown_until.get(provider.name, 0) > now:
                skipped.append(provider)
                continue
            key = os.environ.get(provider.key_name, "").strip()
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 1:
                    break
                model = self._select_model(provider, key)
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "stream": False,
                }
                if "gpt-oss" in model:
                    payload["reasoning_effort"] = "low"
                request = Request(
                    provider.base_url + "/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        # Cloudflare blocks Python's default urllib signature on
                        # Groq and Cerebras even when the API key is valid.
                        "User-Agent": "Mozilla/5.0 JARVIS/2.0",
                        **(
                            {
                                "HTTP-Referer": "http://localhost/JARVIS",
                                "X-Title": "JARVIS",
                            }
                            if provider.name == "openrouter"
                            else {}
                        ),
                    },
                    method="POST",
                )
                with urlopen(request, timeout=min(16, max(3, remaining))) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = _message_text(body)
                if not content:
                    raise RuntimeError("Provider returned an empty response.")
                self.last_provider = provider.name
                self.last_model = model
                self.last_latency_ms = round((time.monotonic() - started) * 1000)
                self._cooldown_until.pop(provider.name, None)
                self.last_errors = {}
                return content
            except (HTTPError, URLError, TimeoutError, socket.timeout, OSError, ValueError, KeyError, RuntimeError) as exc:
                self.last_errors[provider.name] = _provider_error(exc)
                retryable = not isinstance(exc, HTTPError) or exc.code == 429 or exc.code >= 500
                self._cooldown_until[provider.name] = time.monotonic() + (45 if retryable else 180)

        if skipped and not self.last_errors:
            # If every route is cooling down, allow one immediate recovery probe.
            provider = skipped[0]
            self._cooldown_until.pop(provider.name, None)
            return self.chat(messages, max_tokens=max_tokens)
        if not configured:
            raise RuntimeError("No intelligence provider is configured.")
        detail = ", ".join(f"{name}: {error}" for name, error in self.last_errors.items())
        raise RuntimeError(f"All configured free providers failed. {detail}".strip())

    @staticmethod
    def omniroute_connections(base_url: str | None) -> list[dict[str, Any]]:
        if not base_url:
            return []
        parsed = base_url.rstrip("/")
        if parsed.endswith("/v1"):
            parsed = parsed[:-3]
        try:
            request = Request(parsed + "/api/providers", headers={"Accept": "application/json"})
            with urlopen(request, timeout=4) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            # Newer OmniRoute builds protect management HTTP routes. Its local
            # CLI can still return the same sanitized connection metadata from
            # the encrypted store, so use that read-only path as the fallback.
            executable = shutil.which("omniroute.cmd") or shutil.which("omniroute")
            if not executable:
                return []
            try:
                completed = subprocess.run(
                    [executable, "providers", "list", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                raw = completed.stdout
                start = raw.find("{")
                body = json.loads(raw[start:]) if start >= 0 else {}
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return []
        result = []
        for item in body.get("providers", []):
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "provider": item.get("provider"),
                    "name": item.get("name"),
                    "active": bool(item.get("isActive")),
                    "status": item.get("testStatus"),
                    "source": "omniroute",
                }
            )
        return result

    def connection_summary(self, base_url: str | None) -> list[dict[str, Any]]:
        cached_at, cached_connections = self._connection_cache
        now = time.monotonic()
        if cached_connections and now - cached_at < 60:
            connections = [dict(item) for item in cached_connections]
        else:
            connections = self.omniroute_connections(base_url)
            self._connection_cache = (now, [dict(item) for item in connections])
        for provider in self.PROVIDERS:
            connections.append(
                {
                    "provider": provider.name,
                    "name": "JARVIS env",
                    "active": bool(os.environ.get(provider.key_name)),
                    "status": (
                        "active"
                        if provider.name == self.last_provider
                        else "configured"
                        if os.environ.get(provider.key_name)
                        else "missing"
                    ),
                    "source": "environment",
                    "latency_ms": (
                        self.last_latency_ms
                        if provider.name == self.last_provider
                        else None
                    ),
                }
            )
        return connections
