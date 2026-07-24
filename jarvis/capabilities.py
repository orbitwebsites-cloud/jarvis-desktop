from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Risk = Literal["read", "safe", "confirm", "disabled"]


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    category: str
    description: str
    risk: Risk = "safe"
    examples: tuple[str, ...] = ()
    requires: str | None = None

    def to_dict(self) -> dict:
        value = asdict(self)
        value["examples"] = list(self.examples)
        return value


CAPABILITIES = (
    Capability("apps", "App launcher", "Computer", "Open allowlisted or discovered Start Menu apps.", examples=("Open calculator", "Launch Spotify")),
    Capability("web", "Web navigation", "Web", "Open safe HTTP(S) sites and launch browser searches.", examples=("Open github.com", "Search for local weather")),
    Capability("files", "File finder", "Computer", "Search file names inside the user profile without reading contents.", "read", ("Find file quarterly report",)),
    Capability("windows", "Window controls", "Computer", "Inspect, list, focus, minimize, maximize, switch, or confirm-close Windows.", examples=("List windows", "Switch to Spotify", "Minimize window")),
    Capability("clipboard", "Clipboard", "Computer", "Set clipboard text or read it after confirmation.", "confirm", ("Copy text hello", "Read clipboard")),
    Capability("typing", "Dictation and typing", "Computer", "Type literal text into the focused application after confirmation.", "confirm", ("Type meeting starts at nine",)),
    Capability("media", "Media and volume", "Media", "Control playback, tracks, mute, and volume.", examples=("Volume down", "Next track")),
    Capability("youtube", "YouTube search", "Media", "Open a requested video or music search.", examples=("Play synthwave on YouTube",)),
    Capability("display", "Display controls", "Computer", "Read or set supported monitor brightness.", examples=("Set brightness to 60",)),
    Capability("screenshot", "Screen capture", "Vision", "Capture all monitors to the Pictures/JARVIS folder.", examples=("Take a screenshot",)),
    Capability("processes", "Process monitor", "Computer", "List processes and confirm before terminating one.", "confirm", ("List processes", "Close process notepad.exe")),
    Capability("telemetry", "System telemetry", "Computer", "Memory, disk, battery, host and uptime information.", "read", ("System status",)),
    Capability("notifications", "Windows notifications", "Productivity", "Show a local Windows notification.", examples=("Notify me stand up and stretch",)),
    Capability("memory", "Long-term memory", "Memory", "Save and recall preferences, facts and notes locally.", examples=("Remember editor is VS Code",)),
    Capability("reminders", "Persistent reminders", "Productivity", "Schedule reminders that survive dashboard refreshes.", examples=("Remind me in 20 minutes to stretch",)),
    Capability("app_workflows", "Cross-app workflows", "Automation", "Read visible text from one open app and stage it in another through two confirmations. JARVIS never auto-sends.", "confirm", ("Transfer the latest message from Discord to Claude",)),
    Capability("research", "Web research documents", "Web", "Open an isolated browser, review bounded public sources, close it, and create a sourced Word document.", "read", ("Research Windows AI assistants and save it to a document",)),
    Capability("routines", "Reusable routines", "Automation", "Save and run sequences of safe JARVIS commands.", examples=("Create routine work: open terminal | open github.com",)),
    Capability("power", "Power controls", "Computer", "Lock, sleep, restart or shut down with policy checks.", "confirm", ("Restart my computer",)),
    Capability("shell", "PowerShell", "Advanced", "Run explicit PowerShell only when enabled and confirmed.", "disabled", ("Run Get-Process",), "Enable Advanced shell control"),
    Capability("intelligence", "JARVIS Intelligence", "Intelligence", "Reasoning, planning, persistent conversation context and external toolsets.", "read", ("Explain this error",), "Working intelligence provider"),
    Capability("voice", "Voice interface", "Voice", "Browser/native WebView speech recognition and spoken replies.", examples=("Tap the core and speak",)),
)


def capability_payload(shell_enabled: bool, intelligence_available: bool) -> list[dict]:
    payload = []
    for item in CAPABILITIES:
        data = item.to_dict()
        if item.id == "shell":
            data["available"] = shell_enabled
            data["risk"] = "confirm" if shell_enabled else "disabled"
        elif item.id == "intelligence":
            data["available"] = intelligence_available
        else:
            data["available"] = True
        payload.append(data)
    return payload
