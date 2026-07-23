from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Imported explicitly so PyInstaller includes the native desktop backend.
import webview  # noqa: F401

from jarvis import providers


def _crash_log_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "JARVIS" / "data" / "crash.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _record_crash(kind: str, detail: str) -> None:
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        with _crash_log_path().open("a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] {kind}\n{detail[-12000:]}\n")
    except OSError:
        pass


def _main_exception_hook(exc_type, exc_value, exc_traceback) -> None:
    _record_crash("unhandled-main-thread", "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _thread_exception_hook(args) -> None:
    _record_crash(
        f"unhandled-thread:{args.thread.name if args.thread else 'unknown'}",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
    )


sys.excepthook = _main_exception_hook
threading.excepthook = _thread_exception_hook


def _message_text(body: dict) -> str:
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
            raw = exc.read(600).decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                error = parsed.get("error", parsed)
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


def robust_chat(self, messages: list[dict[str, str]], max_tokens: int = 900) -> str:
    """Try alternate models and bounded retries instead of dropping offline."""
    self.last_errors = {}
    configured = [
        provider
        for provider in self.PROVIDERS
        if os.environ.get(provider.key_name, "").strip()
    ]
    if self.last_provider:
        configured.sort(key=lambda item: item.name != self.last_provider)
    deadline = time.monotonic() + min(48, max(18, self.timeout_seconds + 3))

    for provider in configured:
        key = os.environ.get(provider.key_name, "").strip()
        candidates = list(dict.fromkeys(provider.preferred_models))
        if self.last_provider == provider.name and self.last_model in candidates:
            candidates.remove(self.last_model)
            candidates.insert(0, self.last_model)

        for model in candidates:
            if time.monotonic() >= deadline:
                break
            for attempt in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 1:
                    break
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": max(900 if attempt == 0 else 1400, max_tokens),
                    "stream": False,
                }
                if "gpt-oss" in model:
                    payload["reasoning_effort"] = "low"
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 JARVIS/1.1",
                }
                if provider.name == "openrouter":
                    headers.update({"HTTP-Referer": "http://localhost/JARVIS", "X-Title": "JARVIS"})
                request = Request(
                    provider.base_url + "/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                try:
                    with urlopen(request, timeout=min(18, max(3, remaining))) as response:
                        body = json.loads(response.read().decode("utf-8"))
                    content = _message_text(body)
                    if not content:
                        raise RuntimeError("provider returned no usable answer")
                    self.last_provider = provider.name
                    self.last_model = model
                    self.last_errors = {}
                    return content
                except (HTTPError, URLError, TimeoutError, socket.timeout, OSError, ValueError, KeyError, RuntimeError) as exc:
                    self.last_errors[provider.name] = f"{model}: {_provider_error(exc)}"
                    retryable = not isinstance(exc, HTTPError) or exc.code == 429 or exc.code >= 500
                    if attempt == 0 and retryable and time.monotonic() + 0.4 < deadline:
                        time.sleep(0.4)
                        continue
                    break

    if not configured:
        raise RuntimeError("No intelligence provider is configured.")
    detail = ", ".join(f"{name}: {error}" for name, error in self.last_errors.items())
    raise RuntimeError(f"All configured providers failed. {detail}".strip())


providers.FreeModelRouter.chat = robust_chat

from jarvis import intelligence  # noqa: E402

_original_status = intelligence.IntelligenceBridge.status
_original_plan = intelligence.IntelligenceBridge.plan


def reliable_status(self) -> dict:
    result = _original_status(self)
    configured = bool(self.free_router.configured or self.executable or self._configured_base_url())
    result["available"] = bool(self.enabled and configured)
    result["health"] = "degraded" if self._last_error and configured else "ready" if configured else "offline"
    result["connected"] = configured
    return result


def resilient_plan(self, user_text: str):
    try:
        return _original_plan(self, user_text)
    except RuntimeError as primary_error:
        # If a local Ollama/OpenAI-compatible endpoint exists, use it when a
        # cloud free-tier provider is temporarily unavailable.
        try:
            if not self._detect_local_endpoint_error():
                plan = self._plan_via_local_endpoint(user_text)
                if plan is not None:
                    self.record_turn(user_text, plan.raw or plan.reply)
                    self._last_error = None
                    self._last_success = datetime.now(timezone.utc).isoformat()
                    return plan
        except Exception as fallback_error:
            self._last_error = f"{primary_error}; local fallback: {fallback_error}"
        raise primary_error


intelligence.IntelligenceBridge.status = reliable_status
intelligence.IntelligenceBridge.plan = resilient_plan

from jarvis.desktop import main  # noqa: E402


if __name__ == "__main__":
    main()
