from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .agent import JarvisAgent
from .audit import AuditLog
from .capabilities import capability_payload
from .config import ConfigStore
from .memory import MemoryStore
from .reminders import ReminderStore, RoutineStore
from .windows import WindowsController
from .intelligence import IntelligenceBridge


if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    DATA_DIR = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "JARVIS"
        / "data"
    )
else:
    ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
VERSION = "2.1.0"


class JarvisApp:
    def __init__(self):
        self.config = ConfigStore(DATA_DIR / "config.json")
        self.memory = MemoryStore(DATA_DIR / "memory.json")
        self.reminders = ReminderStore(DATA_DIR / "reminders.json")
        self.routines = RoutineStore(DATA_DIR / "routines.json")
        self.audit = AuditLog(DATA_DIR / "audit.jsonl")
        self.controller = WindowsController(DATA_DIR)
        loaded = self.config.load()
        self.intelligence = IntelligenceBridge(
            enabled=loaded.intelligence_enabled,
            timeout_seconds=loaded.intelligence_timeout_seconds,
            context_path=DATA_DIR / "conversation_context.json",
        )
        self.agent = JarvisAgent(
            self.controller,
            self.memory,
            self.audit,
            self.config,
            self.intelligence,
            self.reminders,
            self.routines,
        )
        self._reminder_thread = threading.Thread(
            target=self._deliver_reminders,
            name="jarvis-reminders",
            daemon=True,
        )
        self._reminder_thread.start()

    def _deliver_reminders(self) -> None:
        while True:
            try:
                for reminder in self.reminders.due():
                    try:
                        self.controller.notify("JARVIS reminder", reminder["text"])
                    except Exception as exc:
                        self.reminders.mark_delivery_failed(reminder["id"])
                        self.audit.write(
                            "reminder_delivery_failed",
                            {"id": reminder["id"], "error": str(exc)[:300]},
                        )
                        continue
                    self.reminders.mark_delivered(reminder["id"])
                    self.audit.write("reminder_delivered", {"id": reminder["id"]})
            except Exception as exc:
                self.audit.write("reminder_error", {"error": str(exc)})
            time.sleep(2)


APP = JarvisApp()


class JarvisHandler(BaseHTTPRequestHandler):
    server_version = "JARVIS/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        APP.audit.write("http", {"message": fmt % args})

    def _json(self, payload: object, status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if length > 64_000:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be an object.")
        return value

    def _trusted(self) -> bool:
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host not in {"127.0.0.1", "localhost", "[::1]"}:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"status": "online", "version": VERSION})
            return
        if path == "/api/status":
            self._json(
                {"system": APP.controller.status(), "intelligence": APP.intelligence.status()}
            )
            return
        if path == "/api/history":
            self._json({"history": APP.audit.recent(40)})
            return
        if path == "/api/memory":
            self._json({"memory": APP.memory.snapshot()})
            return
        if path == "/api/reminders":
            self._json(
                {
                    "reminders": APP.reminders.list(),
                    "history": APP.reminders.history(8),
                }
            )
            return
        if path == "/api/routines":
            self._json({"routines": APP.routines.list()})
            return
        if path == "/api/capabilities":
            config = APP.config.load()
            self._json(
                {
                    "capabilities": capability_payload(
                        shell_enabled=config.allow_shell,
                        intelligence_available=APP.intelligence.status()["available"],
                    )
                }
            )
            return
        if path == "/api/config":
            config = APP.config.load()
            self._json(
                {
                    "config": {
                        "assistant_name": config.assistant_name,
                        "user_name": config.user_name,
                        "speak_responses": config.speak_responses,
                        "allow_shell": config.allow_shell,
                        "allow_power_actions": config.allow_power_actions,
                        "intelligence_enabled": config.intelligence_enabled,
                    }
                }
            )
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        if not self._trusted():
            self._json({"error": "Local requests only."}, HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/command":
                text = body.get("text", "")
                if not isinstance(text, str) or len(text) > 4000:
                    raise ValueError("Command must be text under 4,000 characters.")
                self._json(APP.agent.handle_with_intelligence(text).to_dict())
                return
            if path == "/api/confirm":
                token = body.get("token", "")
                if not isinstance(token, str):
                    raise ValueError("Invalid confirmation token.")
                self._json(APP.agent.confirm(token).to_dict())
                return
            if path == "/api/cancel":
                token = body.get("token", "")
                if not isinstance(token, str):
                    raise ValueError("Invalid confirmation token.")
                self._json(APP.agent.cancel(token).to_dict())
                return
            if path == "/api/config":
                changes = body.get("changes", {})
                if not isinstance(changes, dict):
                    raise ValueError("Settings changes must be an object.")
                config = APP.config.update(changes)
                APP.intelligence.enabled = config.intelligence_enabled
                APP.intelligence.timeout_seconds = config.intelligence_timeout_seconds
                APP.intelligence.free_router.timeout_seconds = config.intelligence_timeout_seconds
                self._json(
                    {
                        "status": "saved",
                        "config": {
                            "assistant_name": config.assistant_name,
                            "user_name": config.user_name,
                            "speak_responses": config.speak_responses,
                            "allow_shell": config.allow_shell,
                            "allow_power_actions": config.allow_power_actions,
                            "intelligence_enabled": config.intelligence_enabled,
                        },
                    }
                )
                return
            if path == "/api/context/clear":
                APP.intelligence.clear_context()
                APP.audit.write("context_cleared", {})
                self._json({"status": "cleared", "message": "Conversation context cleared."})
                return
            if path == "/api/reminders/dismiss":
                reminder_id = body.get("id", "")
                if not isinstance(reminder_id, str) or not reminder_id:
                    raise ValueError("A reminder id is required.")
                dismissed = APP.reminders.dismiss(reminder_id)
                self._json({"status": "dismissed" if dismissed else "not_found"})
                return
            if path == "/api/reminders/snooze":
                reminder_id = body.get("id", "")
                minutes = body.get("minutes", 10)
                if not isinstance(reminder_id, str) or not reminder_id:
                    raise ValueError("A reminder id is required.")
                if not isinstance(minutes, int):
                    raise ValueError("Snooze minutes must be a whole number.")
                reminder = APP.reminders.snooze(reminder_id, minutes)
                self._json(
                    {
                        "status": "snoozed" if reminder else "not_found",
                        "reminder": reminder,
                    }
                )
                return
            self._json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            APP.audit.write("server_error", {"error": str(exc), "path": path})
            self._json({"error": "JARVIS hit an internal error."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            candidate = STATIC_DIR / "index.html"
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local JARVIS assistant.")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    config = APP.config.load()
    port = args.port or config.port
    server = ThreadingHTTPServer(("127.0.0.1", port), JarvisHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"JARVIS is online at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser and config.open_browser_on_start:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJARVIS offline.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
