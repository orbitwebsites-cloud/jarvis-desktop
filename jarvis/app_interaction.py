from __future__ import annotations

import re
import time
from typing import Any


class AppInteractor:
    """Guarded Windows UI Automation helpers for attended app workflows."""

    # How patient JARVIS is with a slow app, in one place. A freshly launched
    # app can take several seconds to paint; focus can lag a newly shown window.
    LAUNCH_TIMEOUT = 25.0
    POLL_INTERVAL = 0.5
    FOCUS_ATTEMPTS = 6

    COMMON_CHROME_TEXT = {
        "close",
        "maximize",
        "minimize",
        "restore",
        "settings",
        "help",
        "search",
        "home",
        "inbox",
        "friends",
        "library",
    }

    def __init__(self, controller: Any):
        self.controller = controller
        self._desk: Any = None

    def _desktop(self):
        # Built once and reused; a poll loop otherwise rebuilds this wrapper
        # dozens of times while waiting for an app to appear.
        if self._desk is None:
            try:
                from pywinauto import Desktop
            except ImportError as exc:
                raise RuntimeError("App interaction support is not installed.") from exc
            self._desk = Desktop(backend="uia")
        return self._desk

    def _match_window(self, needle: str):
        matches = []
        for window in self._desktop().windows():
            try:
                title = window.window_text().strip()
                if title and window.is_visible() and needle in title.lower():
                    matches.append(
                        (
                            0 if title.lower() == needle else 1,
                            len(title),
                            window,
                            title,
                        )
                    )
            except Exception:
                continue
        if matches:
            _, _, window, title = min(matches, key=lambda item: item[:2])
            return window, title
        return None

    def _find_window(self, query: str, launch: bool = True):
        needle = " ".join(query.lower().split())
        if not needle:
            raise ValueError("An app name is required.")
        # Fast path: the window is already open (the common case once we open
        # the app ahead of any confirmation), so return without launching.
        found = self._match_window(needle)
        if found:
            return found
        if not launch:
            raise ValueError(f"I couldn't find a visible {query} window.")
        # Launch first, then give the app a generous, separate window to appear.
        # A freshly launched app can take several seconds to paint its window,
        # so the timeout starts counting from the launch, not from the request.
        self.controller.open_app(query)
        deadline = time.monotonic() + self.LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(self.POLL_INTERVAL)
            found = self._match_window(needle)
            if found:
                return found
        raise ValueError(f"I couldn't find or open a visible {query} window.")

    def ensure_window(self, app_name: str) -> None:
        """Open the app (if needed) and wait for its window to appear.

        Called before the paste confirmation so the destination is launched and
        ready ahead of time — the confirmed action then never has to launch an
        app and wait on it, which is what used to time out mid-open.
        """
        self._find_window(app_name)

    @classmethod
    def _useful_text(cls, text: str, app_name: str) -> bool:
        clean = " ".join(text.split())
        if len(clean) < 2 or len(clean) > 4000:
            return False
        lowered = clean.lower()
        if lowered in cls.COMMON_CHROME_TEXT or lowered == app_name.lower():
            return False
        if re.fullmatch(r"\d{1,2}:\d{2}(?:\s*[ap]m)?", lowered):
            return False
        return True

    def read_text_items(self, app_name: str, limit: int = 300) -> dict[str, Any]:
        window, title = self._find_window(app_name)
        items: list[str] = []
        seen: set[str] = set()
        for control_type in ("Text", "Document"):
            try:
                controls = window.descendants(control_type=control_type)
            except Exception:
                controls = []
            for control in controls:
                try:
                    raw_text = control.window_text() or getattr(
                        control.element_info, "name", ""
                    )
                    text = " ".join(raw_text.split())
                    if (
                        text
                        and text not in seen
                        and self._useful_text(text, app_name)
                    ):
                        seen.add(text)
                        items.append(text)
                        if len(items) >= limit:
                            break
                except Exception:
                    continue
            if len(items) >= limit:
                break
        if not items:
            raise RuntimeError(
                f"{title} did not expose readable text through Windows accessibility."
            )
        return {"app": app_name, "window_title": title, "items": items}

    def latest_text(self, app_name: str) -> dict[str, str]:
        snapshot = self.read_text_items(app_name)
        # UI Automation traversal follows the visual tree for Electron apps;
        # the final useful text item is normally the newest visible message.
        text = snapshot["items"][-1]
        return {
            "app": app_name,
            "window_title": snapshot["window_title"],
            "text": text,
        }

    def paste_into_app(self, app_name: str, text: str) -> str:
        # The destination was opened ahead of this confirmed step, so it should
        # already be present; don't relaunch it here.
        window, title = self._find_window(app_name, launch=False)
        # A window can be visible but not yet foreground right after it opens, so
        # retry the focus a few times instead of failing on the first check.
        needle = app_name.lower()
        for attempt in range(self.FOCUS_ATTEMPTS):
            try:
                window.set_focus()
            except Exception:
                pass
            time.sleep(0.25 if attempt == 0 else self.POLL_INTERVAL)
            focused_title = self.controller.foreground_window().get("title", "")
            if needle in focused_title.lower():
                break
        else:
            raise RuntimeError(f"Windows did not focus the intended {app_name} window.")

        candidates = []
        for control_type in ("Edit", "Document"):
            try:
                for control in window.descendants(control_type=control_type):
                    try:
                        if control.is_visible() and control.is_enabled():
                            rect = control.rectangle()
                            candidates.append((rect.bottom, rect.right - rect.left, control))
                    except Exception:
                        continue
            except Exception:
                continue
        if not candidates:
            raise RuntimeError(f"I couldn't find an editable field in {title}.")
        _, _, target = max(candidates, key=lambda item: (item[0], item[1]))
        target.click_input()
        time.sleep(0.2)

        previous_clipboard = self.controller.clipboard_read()
        try:
            self.controller.clipboard_write(text)
            self.controller.press_hotkey("paste")
            time.sleep(0.6)
        finally:
            self.controller.clipboard_write(previous_clipboard)
        return f"Pasted the approved text into {title}. It was not sent."
