from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus

from .audit import AuditLog
from .config import ConfigStore
from .memory import MemoryStore
from .reminders import ReminderStore, RoutineStore
from .windows import WindowsController
from .intelligence import IntelligenceBridge
from .app_interaction import AppInteractor
from .web_research import WebResearcher


@dataclass
class AgentResponse:
    status: str
    display: str
    speech: str | None = None
    action: str | None = None
    requires_confirmation: bool = False
    confirmation_token: str | None = None
    confirmation_title: str | None = None
    confirmation_detail: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["speech"] = self.speech or self.display
        return payload


@dataclass
class PendingAction:
    action: str
    detail: str
    audit_detail: str
    expires_at: float
    callback: Callable[[], AgentResponse]


class JarvisAgent:
    def __init__(
        self,
        controller: WindowsController,
        memory: MemoryStore,
        audit: AuditLog,
        config: ConfigStore,
        intelligence: IntelligenceBridge | None = None,
        reminders: ReminderStore | None = None,
        routines: RoutineStore | None = None,
        app_interactor: AppInteractor | None = None,
        researcher: WebResearcher | None = None,
    ):
        self.controller = controller
        self.memory = memory
        self.audit = audit
        self.config = config
        self.intelligence = intelligence
        self.reminders = reminders
        self.routines = routines
        self.app_interactor = app_interactor or AppInteractor(controller)
        data_dir = Path(getattr(controller, "data_dir", Path.cwd() / "data"))
        self.researcher = researcher or WebResearcher(data_dir, intelligence)
        self._pending: dict[str, PendingAction] = {}
        self._pending_lock = threading.RLock()

    def handle(self, text: str) -> AgentResponse:
        raw = " ".join(text.strip().split())
        if not raw:
            return AgentResponse("error", "I need a command first.")
        self.audit.write("command_received", {"command": raw[:500]})
        try:
            response = self._route(raw)
        except Exception as exc:
            response = AgentResponse("error", f"I couldn't complete that: {exc}")
        self.audit.write(
            "command_result",
            {
                "command": raw[:500],
                "status": response.status,
                "action": response.action,
            },
        )
        return response

    def handle_with_intelligence(self, text: str) -> AgentResponse:
        """Use deterministic controls first, then the intelligence planner."""
        if text.strip().lower() in {
            "clear conversation",
            "clear context",
            "forget this conversation",
            "start a new conversation",
        }:
            if self.intelligence:
                self.intelligence.clear_context()
            return AgentResponse(
                "success",
                "Conversation context cleared.",
                action="context_clear",
            )
        local = self.handle(text)
        if local.status != "unknown" or not self.intelligence:
            if self.intelligence and local.status != "unknown":
                self.intelligence.record_turn(text, local.display)
            return local
        try:
            plan = self.intelligence.plan(text)
        except Exception as exc:
            status = self.intelligence.status()
            code = status.get("diagnostic_code") or self.intelligence._diagnostic_code(str(exc))
            self.audit.write(
                "intelligence_error",
                {"code": code, "detail": str(exc)[:500]},
            )
            local.display = f"JARVIS intelligence is temporarily unavailable. Error code: {code}."
            local.data["intelligence"] = {
                "available": False,
                "diagnostic_code": code,
            }
            return local
        if plan.kind == "chat" or not plan.commands:
            return AgentResponse(
                "success",
                plan.reply or "JARVIS completed the request.",
                action="intelligence_chat",
                data={"engine": "JARVIS Intelligence"},
            )
        results: list[dict[str, Any]] = []
        for command in plan.commands:
            result = self.handle(command)
            results.append({"command": command, "result": result.to_dict()})
            if result.requires_confirmation:
                if plan.reply:
                    result.data["intelligence_reply"] = plan.reply
                result.data["sequence"] = results
                return result
            if result.status not in {"success", "not_found"}:
                return AgentResponse(
                    result.status,
                    result.display,
                    action="intelligence_action",
                    data={"engine": "JARVIS Intelligence", "sequence": results},
                )
        summary = plan.reply or "Done."
        return AgentResponse(
            "success",
            summary,
            action="intelligence_actions",
            data={"engine": "JARVIS Intelligence", "sequence": results},
        )

    def confirm(self, token: str) -> AgentResponse:
        with self._pending_lock:
            pending = self._pending.pop(token, None)
        if not pending:
            return AgentResponse("error", "That confirmation is missing or has already been used.")
        if pending.expires_at < time.time():
            return AgentResponse("error", "That confirmation expired. Please ask again.")
        self.audit.write(
            "action_confirmed",
            {"action": pending.action, "detail": pending.audit_detail},
        )
        try:
            return pending.callback()
        except Exception as exc:
            return AgentResponse("error", f"The confirmed action failed: {exc}", action=pending.action)

    def cancel(self, token: str) -> AgentResponse:
        with self._pending_lock:
            pending = self._pending.pop(token, None)
        if pending:
            self.audit.write("action_cancelled", {"action": pending.action})
        return AgentResponse("cancelled", "Cancelled. Nothing was changed.")

    def _confirmation(
        self,
        action: str,
        title: str,
        detail: str,
        callback: Callable[[], AgentResponse],
        audit_detail: str | None = None,
    ) -> AgentResponse:
        token = secrets.token_urlsafe(24)
        with self._pending_lock:
            now = time.time()
            self._pending = {
                key: value for key, value in self._pending.items() if value.expires_at > now
            }
            safe_detail = audit_detail or detail
            self._pending[token] = PendingAction(
                action, detail, safe_detail, now + 120, callback
            )
        self.audit.write(
            "confirmation_requested",
            {"action": action, "detail": audit_detail or detail},
        )
        return AgentResponse(
            "confirmation_required",
            f"Please confirm: {detail}",
            action=action,
            requires_confirmation=True,
            confirmation_token=token,
            confirmation_title=title,
            confirmation_detail=detail,
        )

    def _route(self, raw: str) -> AgentResponse:
        spoken = re.sub(
            r"^(hey\s+)?(jarvis|computer)[,:\s]+", "", raw, flags=re.I
        ).strip()
        command = spoken.lower()

        if command in {"help", "what can you do", "capabilities", "commands"}:
            return AgentResponse(
                "success",
                "I can open apps and sites, search the web, control media and volume, "
                "discover installed apps, manage windows, search files, use the clipboard "
                "with confirmation, inspect processes, control display and media, take "
                "screenshots, schedule reminders, run routines, manage notes and memories, "
                "transfer text between open apps with confirmation, research the public web "
                "into a sourced Word document, report system status, and perform confirmed "
                "power or shell actions.",
                action="help",
                data={"suggestions": self.suggestions()},
            )

        if command in {"status", "system status", "computer status", "how is my computer"}:
            status = self.controller.status()
            battery = (
                f", battery {status['battery_percent']} percent"
                if status["battery_percent"] is not None
                else ""
            )
            return AgentResponse(
                "success",
                f"Memory is at {status['memory_percent']}%, disk is at "
                f"{status['disk_percent']}%{battery}.",
                action="system_status",
                data={"system": status},
            )

        if command in {"time", "what time is it", "current time"}:
            now = datetime.now()
            readable = now.strftime("%I:%M %p").lstrip("0")
            return AgentResponse("success", f"It is {readable}.", action="time")

        if command in {"date", "what is the date", "today's date", "what day is it"}:
            now = datetime.now()
            return AgentResponse("success", f"Today is {now:%A, %B %d, %Y}.", action="date")

        transfer_match = (
            re.match(
                r"(?:go to\s+)?(.+?)[,\s]+copy (?:the )?latest "
                r"(?:msg|message)(?:\s+from\s+there)?\s+and\s+paste "
                r"(?:it\s+)?(?:in|into|to)\s+(.+)$",
                spoken,
                re.I,
            )
            or re.match(
                r"(?:copy|transfer)\s+(?:the\s+)?latest\s+(?:msg|message)\s+"
                r"from\s+(.+?)\s+(?:and\s+paste\s+(?:it\s+)?|to\s+)"
                r"(?:in|into|to)?\s*(.+)$",
                spoken,
                re.I,
            )
        )
        if transfer_match:
            source = transfer_match.group(1).strip(" ,.")
            destination = transfer_match.group(2).strip(" ,.")
            return self._confirmation(
                "read_app_text",
                f"Read private text from {source}?",
                f"Inspect visible accessibility text in {source} to find the latest message. "
                f"Nothing will be pasted into {destination} yet.",
                lambda: self._prepare_app_transfer(source, destination),
                audit_detail=f"Read visible text from {source} for an app-to-app transfer.",
            )

        research_match = re.match(
            r"(?:research(?: the web)?(?: for)?|search the web for)\s+(.+?)"
            r"(?:\s+and\s+(?:put|save|write).*(?:doc|document))?$",
            spoken,
            re.I,
        )
        if research_match and (
            command.startswith("research")
            or re.search(r"\b(?:doc|document)\b", command)
        ):
            query = research_match.group(1).strip()
            result = self.researcher.research(query)
            return AgentResponse(
                "success",
                f"Research complete. I reviewed {len(result['sources'])} public sources "
                f"and saved a Word document to {result['path']}.",
                action="web_research",
                data=result,
            )

        match = re.match(
            r"(?:play|search(?: youtube)? for)\s+(.+?)(?:\s+on\s+youtube)?$",
            spoken,
            re.I,
        )
        if match and (
            "youtube" in command
            or command.startswith("play ")
            and command not in {"play music", "play pause"}
        ):
            query = match.group(1).strip()
            message = self.controller.youtube_search(query)
            return AgentResponse("success", message, action="youtube_search")

        match = re.match(
            r"(?:find|search for|locate)\s+(?:a\s+)?file(?:\s+(?:named|called))?\s+(.+)",
            spoken,
            re.I,
        )
        if match:
            query = match.group(1).strip()
            files = self.controller.find_files(query)
            message = (
                f"I found {len(files)} matching files."
                if files
                else f"I couldn't find a file matching {query} in your user folders."
            )
            return AgentResponse(
                "success" if files else "not_found",
                message,
                action="file_search",
                data={"files": files, "query": query},
            )

        match = re.match(r"(?:search(?: the web)? for|google)\s+(.+)", spoken, re.I)
        if match:
            query = match.group(1).strip()
            message = self.controller.web_search(query)
            return AgentResponse("success", message, action="web_search")

        match = re.match(r"(?:weather|forecast)(?:\s+in|\s+for)?\s*(.*)", spoken, re.I)
        if match:
            location = match.group(1).strip() or "my location"
            message = self.controller.web_search(f"weather {location}")
            return AgentResponse("success", message, action="weather")

        if command in {"news", "latest news", "today's news", "brief me on the news"}:
            message = self.controller.open_url("https://news.google.com/")
            return AgentResponse("success", message, action="news")

        match = re.match(r"(?:directions to|map|maps)\s+(.+)", spoken, re.I)
        if match:
            destination = match.group(1).strip()
            url = "https://www.google.com/maps/search/" + quote_plus(destination)
            message = self.controller.open_url(url)
            return AgentResponse("success", message, action="maps")

        match = re.match(r"(?:go to|open website|open site)\s+(.+)", command, re.I)
        if match:
            target = raw[-len(match.group(1)) :]
            message = self.controller.open_url(target)
            return AgentResponse("success", message, action="open_url")

        # "File Explorer" is an application name, not a request to open a
        # relative path named "explorer". The planner commonly emits this exact
        # canonical phrase, so route it before the generic file/folder form.
        if command in {"open file explorer", "launch file explorer", "start file explorer"}:
            message = self.controller.open_app("file explorer")
            return AgentResponse("success", message, action="open_app")

        match = re.match(r"open\s+(?:file|folder)\s+(.+)", command, re.I)
        if match:
            path = raw[-len(match.group(1)) :]
            message = self.controller.open_path(path)
            return AgentResponse("success", message, action="open_path")

        match = re.match(r"(?:open|launch|start)\s+(.+)", command, re.I)
        if match:
            target = match.group(1).strip()
            original_target = raw[-len(match.group(1)) :]
            if "." in target and " " not in target:
                message = self.controller.open_url(original_target)
                return AgentResponse("success", message, action="open_url")
            message = self.controller.open_app(target)
            return AgentResponse("success", message, action="open_app")

        if command in {"volume up", "turn it up", "increase volume"}:
            return AgentResponse(
                "success", self.controller.press_media_key("volume_up", 2), action="volume_up"
            )
        if command in {"volume down", "turn it down", "decrease volume"}:
            return AgentResponse(
                "success", self.controller.press_media_key("volume_down", 2), action="volume_down"
            )
        if command in {"mute", "unmute", "mute volume"}:
            return AgentResponse(
                "success", self.controller.press_media_key("volume_mute"), action="volume_mute"
            )
        if command in {"play", "pause", "play music", "pause music", "play pause"}:
            return AgentResponse(
                "success",
                self.controller.press_media_key("media_play_pause"),
                action="media_play_pause",
            )
        if command in {"next track", "next song", "skip"}:
            return AgentResponse(
                "success", self.controller.press_media_key("media_next"), action="media_next"
            )
        if command in {"previous track", "previous song", "go back"}:
            return AgentResponse(
                "success",
                self.controller.press_media_key("media_previous"),
                action="media_previous",
            )

        if command in {
            "what window is active",
            "active window",
            "current window",
            "what am i looking at",
        }:
            window = self.controller.foreground_window()
            return AgentResponse(
                "success",
                f"The active window is {window['title']}.",
                action="active_window",
                data={"window": window},
            )

        if command in {"list windows", "show windows", "what windows are open"}:
            windows = self.controller.list_windows()
            return AgentResponse(
                "success",
                f"There are {len(windows)} visible windows.",
                action="window_list",
                data={"windows": windows},
            )

        focus_match = re.match(r"(?:switch|focus|go)\s+to\s+(?:the\s+)?(.+?)(?:\s+window)?$", command, re.I)
        if focus_match:
            target = focus_match.group(1).strip()
            return AgentResponse(
                "success",
                self.controller.focus_window(target),
                action="focus_window",
            )

        window_match = re.fullmatch(
            r"(minimize|maximize|restore|close)(?:\s+(?:the\s+)?(?:current\s+)?window)?",
            command,
        )
        if window_match:
            action = window_match.group(1)
            if action == "close":
                title = self.controller.foreground_window()["title"]
                return self._confirmation(
                    "close_window",
                    "Close the active window?",
                    f"Ask {title} to close. Unsaved work may be lost.",
                    lambda: AgentResponse(
                        "success",
                        self.controller.window_action("close"),
                        action="close_window",
                    ),
                )
            return AgentResponse(
                "success",
                self.controller.window_action(action),
                action=f"{action}_window",
            )

        if command in {"read clipboard", "what is on my clipboard", "show clipboard"}:
            return self._confirmation(
                "read_clipboard",
                "Read the clipboard?",
                "Clipboard contents can contain passwords or other sensitive information.",
                lambda: self._clipboard_response(),
            )

        match = re.match(r"(?:copy text|set clipboard(?: to)?)\s+(.+)", raw, re.I)
        if match:
            text = match.group(1)
            return AgentResponse(
                "success",
                self.controller.clipboard_write(text),
                action="clipboard_write",
            )

        if command in {"list processes", "show processes", "what is running"}:
            processes = self.controller.list_processes()
            return AgentResponse(
                "success",
                f"Here are the {len(processes)} highest-memory processes.",
                action="process_list",
                data={"processes": processes},
            )

        match = re.match(r"(?:close|kill|stop)\s+process\s+(.+)", command, re.I)
        if match:
            process = match.group(1).strip()
            return self._confirmation(
                "terminate_process",
                "Terminate this process?",
                f"Close every running instance of {process}. Unsaved work may be lost.",
                lambda: AgentResponse(
                    "success",
                    self.controller.terminate_process(process),
                    action="terminate_process",
                ),
            )

        match = re.match(r"(?:set\s+)?brightness(?:\s+to)?\s+(\d{1,3})(?:\s*%)?", command)
        if match:
            percent = int(match.group(1))
            if not 0 <= percent <= 100:
                return AgentResponse("error", "Brightness must be from 0 to 100.")
            return AgentResponse(
                "success",
                self.controller.set_brightness(percent),
                action="brightness",
            )

        match = re.match(r"(?:notify me|show notification|notification)\s+(.+)", raw, re.I)
        if match:
            text = match.group(1).strip()
            return AgentResponse(
                "success",
                self.controller.notify("JARVIS", text),
                action="notification",
            )

        match = re.match(r"(?:press|shortcut)\s+(.+)", command, re.I)
        if match:
            shortcut = match.group(1).strip()
            message = self.controller.press_hotkey(shortcut)
            return AgentResponse("success", message, action="hotkey")

        match = re.match(r"type\s+(.+)", command, re.I)
        if match:
            typed_text = raw[-len(match.group(1)) :]
            preview = typed_text if len(typed_text) <= 100 else typed_text[:97] + "..."
            return self._confirmation(
                "type_text",
                "Type into the focused window?",
                f'Type “{preview}” wherever the cursor is currently focused.',
                lambda: AgentResponse(
                    "success",
                    self.controller.type_text(typed_text),
                    action="type_text",
                ),
            )

        if command in {"screenshot", "take a screenshot", "capture screen"}:
            path = self.controller.take_screenshot()
            return AgentResponse(
                "success",
                f"Screenshot saved to {path}.",
                action="screenshot",
                data={"path": path},
            )

        if command in {"lock", "lock computer", "lock my computer", "lock pc"}:
            return AgentResponse("success", self.controller.lock(), action="lock")

        power_match = re.fullmatch(
            r"(?:please\s+)?(shutdown|shut down|restart|reboot|sleep)(?:\s+(?:computer|pc|my computer))?",
            command,
        )
        if power_match:
            action = power_match.group(1)
            action = {"shut down": "shutdown", "reboot": "restart"}.get(action, action)
            if not self.config.load().allow_power_actions:
                return AgentResponse("blocked", "Power actions are disabled in Settings.", action=action)
            return self._confirmation(
                action,
                f"Confirm {action}?",
                f"This will {action} your computer immediately.",
                lambda: AgentResponse(
                    "success", self.controller.power(action), action=action
                ),
            )

        match = re.match(r"(?:remember|remember that)\s+(.+?)\s+(?:is|=)\s+(.+)", raw, re.I)
        if match:
            key, value = match.group(1).strip(), match.group(2).strip()
            self.memory.remember(key, value)
            return AgentResponse("success", f"I'll remember that {key} is {value}.", action="remember")

        match = re.match(r"(?:take a note|note|write this down)[:\s]+(.+)", raw, re.I)
        if match:
            note = self.memory.add_note(match.group(1))
            return AgentResponse(
                "success", "Note saved locally.", action="note", data={"note": note}
            )

        if command in {"what do you remember", "show memory", "show memories", "memory"}:
            snapshot = self.memory.snapshot()
            count = len(snapshot["memories"])
            notes = len(snapshot["notes"])
            return AgentResponse(
                "success",
                f"I have {count} saved memories and {notes} notes.",
                action="memory_list",
                data={"memory": snapshot},
            )

        match = re.match(r"(?:recall|what do you know about|what is)\s+(.+)", command, re.I)
        if match:
            key = match.group(1).rstrip("?").strip()
            value = self.memory.recall(key)
            if value is None:
                return AgentResponse("not_found", f"I don't have a memory for {key}.", action="recall")
            return AgentResponse("success", f"{key} is {value}.", action="recall")

        match = re.match(r"forget\s+(.+)", command, re.I)
        if match:
            key = match.group(1).strip()
            return self._confirmation(
                "forget_memory",
                "Forget this memory?",
                f"Remove the saved memory for “{key}”.",
                lambda: AgentResponse(
                    "success",
                    f"Forgot {key}." if self.memory.forget(key) else f"No memory named {key} was found.",
                    action="forget_memory",
                ),
            )

        reminder_match = re.match(
            r"remind me in\s+(\d+)\s*(seconds?|minutes?|hours?|days?)\s+(?:to|that)\s+(.+)",
            raw,
            re.I,
        )
        if reminder_match and self.reminders:
            amount = int(reminder_match.group(1))
            unit = reminder_match.group(2).lower()
            text = reminder_match.group(3).strip()
            seconds = amount * (
                1
                if unit.startswith("second")
                else 60
                if unit.startswith("minute")
                else 3600
                if unit.startswith("hour")
                else 86400
            )
            if seconds > 365 * 86400:
                return AgentResponse("error", "Reminders can be scheduled up to one year ahead.")
            item = self.reminders.add(text, datetime.now().astimezone() + timedelta(seconds=seconds))
            return AgentResponse(
                "success",
                f"I'll remind you in {amount} {unit} to {text}.",
                action="reminder_create",
                data={"reminder": item},
            )

        absolute_reminder_match = re.match(
            r"remind me\s+(?:(today|tomorrow)\s+)?at\s+"
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+(?:to|that)\s+(.+)",
            raw,
            re.I,
        )
        if absolute_reminder_match and self.reminders:
            day_word, hour_raw, minute_raw, meridiem, text = absolute_reminder_match.groups()
            hour = int(hour_raw)
            minute = int(minute_raw or 0)
            if minute > 59 or hour > (12 if meridiem else 23) or hour < 1:
                return AgentResponse("error", "That reminder time is not valid.")
            if meridiem:
                hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
            now = datetime.now().astimezone()
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if day_word and day_word.lower() == "tomorrow":
                due += timedelta(days=1)
            elif not day_word and due <= now:
                due += timedelta(days=1)
            elif day_word and day_word.lower() == "today" and due <= now:
                return AgentResponse("error", "That time has already passed today.")
            item = self.reminders.add(text.strip(), due)
            return AgentResponse(
                "success",
                f"I'll remind you {due.strftime('%A at %I:%M %p').replace(' 0', ' ')} "
                f"to {text.strip()}.",
                action="reminder_create",
                data={"reminder": item},
            )

        if command in {"reminders", "show reminders", "list reminders"} and self.reminders:
            items = self.reminders.list()
            return AgentResponse(
                "success",
                f"You have {len(items)} active reminders.",
                action="reminder_list",
                data={"reminders": items},
            )

        routine_create = re.match(r"create routine\s+(.+?)\s*:\s*(.+)", raw, re.I)
        if routine_create and self.routines:
            name = routine_create.group(1).strip()
            commands = [item.strip() for item in re.split(r"\s*[|;]\s*", routine_create.group(2))]
            invalid = [item for item in commands if not self._safe_routine_command(item)]
            if invalid:
                return AgentResponse(
                    "blocked",
                    "Routines may only contain safe app, web, media, display, notification, "
                    f"and status commands. Not allowed: {invalid[0]}",
                    action="routine_create",
                )
            routine = self.routines.save(name, commands)
            return AgentResponse(
                "success",
                f"Saved routine {name} with {len(commands)} steps.",
                action="routine_create",
                data={"routine": routine},
            )

        routine_run = re.match(r"run routine\s+(.+)", command, re.I)
        if routine_run and self.routines:
            return self._run_routine(routine_run.group(1).strip())

        if command in {"routines", "show routines", "list routines"} and self.routines:
            routines = self.routines.list()
            return AgentResponse(
                "success",
                f"You have {len(routines)} saved routines.",
                action="routine_list",
                data={"routines": routines},
            )

        if command in {"morning briefing", "brief me", "daily briefing"}:
            status = self.controller.status()
            reminder_count = len(self.reminders.list()) if self.reminders else 0
            now = datetime.now()
            battery = (
                f"{status['battery_percent']}%"
                if status["battery_percent"] is not None
                else "not reported"
            )
            return AgentResponse(
                "success",
                f"Good morning. It is {now.strftime('%I:%M %p').lstrip('0')}. "
                f"Memory load is {status['memory_percent']}%, battery is {battery}, "
                f"and you have {reminder_count} active reminders.",
                action="briefing",
                data={"system": status, "reminders": self.reminders.list() if self.reminders else []},
            )

        timer_match = re.match(
            r"(?:set\s+)?(?:a\s+)?timer(?:\s+for)?\s+(\d+)\s*(seconds?|minutes?|hours?)",
            command,
        )
        if timer_match:
            amount = int(timer_match.group(1))
            unit = timer_match.group(2)
            multiplier = 1 if unit.startswith("second") else 60 if unit.startswith("minute") else 3600
            seconds = amount * multiplier
            if not 1 <= seconds <= 86400:
                return AgentResponse("error", "Timers must be between 1 second and 24 hours.")
            return AgentResponse(
                "success",
                f"Timer set for {amount} {unit}.",
                action="timer",
                data={"timer_seconds": seconds, "timer_label": f"{amount} {unit}"},
            )

        match = re.match(r"(?:run|execute|shell)\s+(.+)", raw, re.I)
        if match:
            shell_command = match.group(1).strip()
            if not self.config.load().allow_shell:
                return AgentResponse(
                    "blocked",
                    "Direct shell commands are disabled. Enable Advanced shell control in Settings first.",
                    action="shell",
                )
            preview = shell_command if len(shell_command) <= 160 else shell_command[:157] + "..."
            return self._confirmation(
                "shell",
                "Run a PowerShell command?",
                preview,
                lambda: self._run_shell(shell_command),
            )

        return AgentResponse(
            "unknown",
            "I didn't map that command yet. Say “what can you do” to see the current controls.",
            action="unknown",
            data={"suggestions": self.suggestions()},
        )

    def _run_shell(self, command: str) -> AgentResponse:
        result = self.controller.run_shell(command)
        if result["exit_code"] == 0:
            display = result["stdout"] or "Command completed successfully."
            return AgentResponse("success", display, action="shell", data={"shell": result})
        display = result["stderr"] or f"Command exited with code {result['exit_code']}."
        return AgentResponse("error", display, action="shell", data={"shell": result})

    def _clipboard_response(self) -> AgentResponse:
        text = self.controller.clipboard_read()
        display = text if text else "The clipboard does not contain text."
        return AgentResponse(
            "success",
            display,
            action="clipboard_read",
            data={"clipboard": text},
        )

    def _prepare_app_transfer(
        self, source: str, destination: str
    ) -> AgentResponse:
        latest = self.app_interactor.latest_text(source)
        text = latest["text"]
        preview = text if len(text) <= 240 else text[:237] + "..."
        return self._confirmation(
            "paste_app_text",
            f"Paste into {destination}?",
            f'Paste “{preview}” into {destination}. It will be inserted only, not sent.',
            lambda: AgentResponse(
                "success",
                self.app_interactor.paste_into_app(destination, text),
                action="paste_app_text",
                data={
                    "source_app": source,
                    "destination_app": destination,
                    "character_count": len(text),
                    "sent": False,
                },
            ),
            audit_detail=(
                f"Paste {len(text)} characters from {source} into {destination}; do not send."
            ),
        )

    @staticmethod
    def _safe_routine_command(command: str) -> bool:
        lowered = command.lower().strip()
        allowed = (
            "open ",
            "launch ",
            "start ",
            "search ",
            "google ",
            "volume ",
            "play ",
            "pause",
            "next track",
            "previous track",
            "press ",
            "take a screenshot",
            "system status",
            "set brightness",
            "brightness",
            "notify me ",
        )
        return lowered.startswith(allowed)

    def _run_routine(self, name: str) -> AgentResponse:
        routine = self.routines.get(name) if self.routines else None
        if not routine:
            return AgentResponse("not_found", f"I don't have a routine named {name}.")
        results = []
        for command in routine["commands"]:
            response = self._route(command)
            results.append({"command": command, "result": response.to_dict()})
            if response.status not in {"success", "not_found"}:
                return AgentResponse(
                    response.status,
                    f"Routine stopped at “{command}”: {response.display}",
                    action="routine_run",
                    data={"routine": routine, "sequence": results},
                )
        return AgentResponse(
            "success",
            f"Routine {routine['name']} completed.",
            action="routine_run",
            data={"routine": routine, "sequence": results},
        )

    @staticmethod
    def suggestions() -> list[str]:
        return [
            "Open calculator",
            "System status",
            "Search for today's weather",
            "Take a screenshot",
            "What window is active",
            "Show reminders",
            "Research the web for Windows AI assistants and save it to a document",
            "Transfer the latest message from Discord to Claude",
            "Morning briefing",
            "Volume down",
            "Set a timer for 5 minutes",
            "Remember favorite editor is VS Code",
            "What do you remember",
        ]
