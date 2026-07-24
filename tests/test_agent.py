from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis.agent import JarvisAgent
from jarvis.audit import AuditLog
from jarvis.config import ConfigStore
from jarvis.memory import MemoryStore
from jarvis.reminders import ReminderStore, RoutineStore


class FakeController:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"memory_percent": 41, "disk_percent": 52, "battery_percent": 80}

    def open_app(self, name):
        self.calls.append(("open_app", name))
        return f"Opening {name}."

    def open_url(self, target):
        self.calls.append(("open_url", target))
        return f"Opening {target}."

    def open_path(self, target):
        self.calls.append(("open_path", target))
        return f"Opening {target}."

    def web_search(self, query):
        self.calls.append(("search", query))
        return f"Searching for {query}."

    def youtube_search(self, query):
        self.calls.append(("youtube", query))
        return f"Searching YouTube for {query}."

    def press_media_key(self, key, repeats=1):
        self.calls.append(("media", key, repeats))
        return "Media key sent."

    def press_hotkey(self, name):
        self.calls.append(("hotkey", name))
        return "Shortcut sent."

    def type_text(self, text):
        self.calls.append(("type", text))
        return "Typed."

    def take_screenshot(self):
        self.calls.append(("screenshot",))
        return "C:\\Pictures\\shot.png"

    def foreground_window(self):
        return {"title": "Test Window", "pid": 123}

    def list_windows(self):
        return [
            {"title": "JARVIS", "pid": 123},
            {"title": "Spotify", "pid": 456},
        ]

    def focus_window(self, title):
        self.calls.append(("focus_window", title))
        return f"Switched to {title}."

    def window_action(self, action):
        self.calls.append(("window", action))
        return f"{action.title()} sent."

    def clipboard_read(self):
        self.calls.append(("clipboard_read",))
        return "secret clipboard text"

    def clipboard_write(self, text):
        self.calls.append(("clipboard_write", text))
        return "Copied."

    def find_files(self, query):
        self.calls.append(("find_files", query))
        return [f"C:\\Users\\test\\{query}.txt"]

    def list_processes(self):
        return [{"name": "test.exe", "pid": 1, "session": "Console", "memory_mb": 12}]

    def terminate_process(self, name):
        self.calls.append(("terminate", name))
        return f"Closed {name}."

    def set_brightness(self, percent):
        self.calls.append(("brightness", percent))
        return f"Brightness set to {percent}%."

    def notify(self, title, message):
        self.calls.append(("notify", title, message))
        return "Notification sent."

    def lock(self):
        self.calls.append(("lock",))
        return "Locked."

    def power(self, action):
        self.calls.append(("power", action))
        return f"Starting {action}."

    def run_shell(self, command):
        self.calls.append(("shell", command))
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}


class FailingIntelligence:
    def plan(self, _text):
        raise RuntimeError("Backend timed out after 45s with provider details")

    def status(self):
        return {"available": False, "diagnostic_code": "JARVIS-E202"}

    @staticmethod
    def _diagnostic_code(_detail):
        return "JARVIS-E202"

class FakeAppInteractor:
    def __init__(self):
        self.calls = []
        self.private_text = "meet me at the library"

    def latest_text(self, app):
        self.calls.append(("read", app))
        return {"app": app, "window_title": app, "text": self.private_text}

    def ensure_window(self, app):
        self.calls.append(("ensure", app))
        return {"app": app, "window_title": app}

    def paste_into_app(self, app, text):
        self.calls.append(("paste", app, text))
        return f"Pasted into {app}. It was not sent."


class FakeResearcher:
    def __init__(self):
        self.calls = []

    def research(self, query):
        self.calls.append(query)
        return {
            "query": query,
            "summary": "A sourced summary.",
            "path": r"C:\Documents\JARVIS Research\result.docx",
            "sources": [{"title": "Source", "url": "https://example.com"}],
        }


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.controller = FakeController()
        self.app_interactor = FakeAppInteractor()
        self.researcher = FakeResearcher()
        self.config = ConfigStore(root / "config.json")
        self.audit_path = root / "audit.jsonl"
        self.agent = JarvisAgent(
            self.controller,
            MemoryStore(root / "memory.json"),
            AuditLog(self.audit_path),
            self.config,
            reminders=ReminderStore(root / "reminders.json"),
            routines=RoutineStore(root / "routines.json"),
            app_interactor=self.app_interactor,
            researcher=self.researcher,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_opens_allowlisted_app(self):
        result = self.agent.handle("Hey Jarvis, open calculator")
        self.assertEqual(result.status, "success")
        self.assertIn(("open_app", "calculator"), self.controller.calls)

    def test_open_file_explorer_is_an_app_not_a_relative_file(self):
        result = self.agent.handle("open file explorer")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.action, "open_app")
        self.assertIn(("open_app", "file explorer"), self.controller.calls)
        self.assertFalse(any(call[0] == "open_path" for call in self.controller.calls))

    def test_power_action_requires_one_time_confirmation(self):
        request = self.agent.handle("restart my computer")
        self.assertTrue(request.requires_confirmation)
        self.assertEqual(self.controller.calls, [])
        confirmed = self.agent.confirm(request.confirmation_token)
        self.assertEqual(confirmed.status, "success")
        self.assertIn(("power", "restart"), self.controller.calls)
        reused = self.agent.confirm(request.confirmation_token)
        self.assertEqual(reused.status, "error")

    def test_typing_requires_confirmation(self):
        request = self.agent.handle("type hello world")
        self.assertTrue(request.requires_confirmation)
        self.assertNotIn(("type", "hello world"), self.controller.calls)
        self.agent.confirm(request.confirmation_token)
        self.assertIn(("type", "hello world"), self.controller.calls)

    def test_memory_round_trip(self):
        saved = self.agent.handle("remember favorite editor is VS Code")
        recalled = self.agent.handle("recall favorite editor")
        self.assertEqual(saved.status, "success")
        self.assertIn("VS Code", recalled.display)

    def test_shell_is_disabled_by_default(self):
        result = self.agent.handle("run Get-Process")
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.requires_confirmation)

    def test_timer_is_structured(self):
        result = self.agent.handle("set a timer for 2 minutes")
        self.assertEqual(result.data["timer_seconds"], 120)

    def test_unknown_command_does_not_execute(self):
        result = self.agent.handle("quantum-clean the entire motherboard")
        self.assertEqual(result.status, "unknown")
        self.assertEqual(self.controller.calls, [])

    def test_intelligence_errors_show_only_jarvis_diagnostic_code(self):
        self.agent.intelligence = FailingIntelligence()
        result = self.agent.handle_with_intelligence("quantum-clean the entire motherboard")
        self.assertEqual(
            result.display,
            "JARVIS intelligence is temporarily unavailable. Error code: JARVIS-E202.",
        )
        self.assertNotIn("backend", result.display.lower())
        self.assertNotIn("provider", result.display)

    def test_youtube_query_does_not_keep_suffix(self):
        result = self.agent.handle("play synthwave mix on YouTube")
        self.assertEqual(result.action, "youtube_search")
        self.assertIn(("youtube", "synthwave mix"), self.controller.calls)

    def test_explicit_file_search_beats_web_search(self):
        result = self.agent.handle("search for a file named roadmap")
        self.assertEqual(result.action, "file_search")
        self.assertIn(("find_files", "roadmap"), self.controller.calls)

    def test_clipboard_read_requires_confirmation(self):
        request = self.agent.handle("read clipboard")
        self.assertTrue(request.requires_confirmation)
        self.assertNotIn(("clipboard_read",), self.controller.calls)
        result = self.agent.confirm(request.confirmation_token)
        self.assertEqual(result.data["clipboard"], "secret clipboard text")

    def test_close_window_requires_confirmation(self):
        request = self.agent.handle("close window")
        self.assertTrue(request.requires_confirmation)
        self.agent.cancel(request.confirmation_token)
        self.assertNotIn(("window", "close"), self.controller.calls)

    def test_lists_and_focuses_visible_windows(self):
        listed = self.agent.handle("list windows")
        self.assertEqual(listed.status, "success")
        self.assertEqual(len(listed.data["windows"]), 2)
        focused = self.agent.handle("switch to Spotify")
        self.assertEqual(focused.status, "success")
        self.assertIn(("focus_window", "spotify"), self.controller.calls)

    def test_persistent_reminder(self):
        result = self.agent.handle("remind me in 5 minutes to stretch")
        self.assertEqual(result.status, "success")
        listed = self.agent.handle("show reminders")
        self.assertEqual(len(listed.data["reminders"]), 1)
        self.assertEqual(listed.data["reminders"][0]["text"], "stretch")

    def test_absolute_reminder(self):
        result = self.agent.handle("remind me tomorrow at 5:30 pm to call mom")
        self.assertEqual(result.status, "success")
        due = datetime.fromisoformat(result.data["reminder"]["due_at"]).astimezone()
        self.assertEqual((due.hour, due.minute), (17, 30))

    def test_delivered_reminder_leaves_active_list_and_can_be_snoozed(self):
        store = self.agent.reminders
        item = store.add("test", datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(len(store.due()), 1)
        self.assertTrue(store.mark_delivered(item["id"]))
        self.assertEqual(store.list(), [])
        snoozed = store.snooze(item["id"], 10)
        self.assertIsNotNone(snoozed)
        self.assertEqual(len(store.list()), 1)

    def test_failed_reminder_delivery_is_kept_for_retry(self):
        store = self.agent.reminders
        item = store.add("retry me", datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertTrue(store.mark_delivery_failed(item["id"]))
        active = store.list()
        self.assertEqual(len(active), 1)
        self.assertFalse(active[0]["delivered"])
        self.assertIn("next_attempt_at", active[0])
        self.assertEqual(store.due(), [])

    def test_cross_app_transfer_uses_two_confirmations_and_never_sends(self):
        first = self.agent.handle(
            "go to Discord copy the latest msg and paste it into Claude"
        )
        self.assertTrue(first.requires_confirmation)
        self.assertEqual(self.app_interactor.calls, [])
        second = self.agent.confirm(first.confirmation_token)
        self.assertTrue(second.requires_confirmation)
        # The destination is opened up front, before the paste confirmation, so
        # the confirmed paste never has to launch it and wait mid-open.
        self.assertEqual(
            self.app_interactor.calls, [("read", "Discord"), ("ensure", "Claude")]
        )
        final = self.agent.confirm(second.confirmation_token)
        self.assertEqual(final.status, "success")
        self.assertFalse(final.data["sent"])
        self.assertIn(
            ("paste", "Claude", self.app_interactor.private_text),
            self.app_interactor.calls,
        )
        audit_text = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn(self.app_interactor.private_text, audit_text)

    def test_web_research_creates_structured_result(self):
        result = self.agent.handle(
            "research the web for Windows AI assistants and save it to a document"
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.action, "web_research")
        self.assertEqual(self.researcher.calls, ["Windows AI assistants"])
        self.assertTrue(result.data["path"].endswith(".docx"))

    def test_safe_routine_runs_and_unsafe_routine_is_blocked(self):
        created = self.agent.handle(
            "create routine focus: open calculator | volume down | notify me focus started"
        )
        self.assertEqual(created.status, "success")
        ran = self.agent.handle("run routine focus")
        self.assertEqual(ran.status, "success")
        self.assertIn(("open_app", "calculator"), self.controller.calls)
        blocked = self.agent.handle("create routine bad: run Remove-Item C:\\")
        self.assertEqual(blocked.status, "blocked")


if __name__ == "__main__":
    unittest.main()
