from __future__ import annotations

import ctypes
import csv
import getpass
import io
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any


IS_WINDOWS = os.name == "nt"


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


class WindowsController:
    APP_ALIASES = {
        "notepad": ["notepad.exe"],
        "notes": ["notepad.exe"],
        "calculator": ["calc.exe"],
        "calc": ["calc.exe"],
        "file explorer": ["explorer.exe"],
        "explorer": ["explorer.exe"],
        "task manager": ["taskmgr.exe"],
        "terminal": ["wt.exe"],
        "windows terminal": ["wt.exe"],
        "command prompt": ["cmd.exe"],
        "cmd": ["cmd.exe"],
        "powershell": ["powershell.exe"],
        "paint": ["mspaint.exe"],
        "snipping tool": ["snippingtool.exe"],
        "control panel": ["control.exe"],
        "browser": ["__url__", "https://www.google.com"],
        "default browser": ["__url__", "https://www.google.com"],
        "settings": ["__uri__", "ms-settings:"],
        "bluetooth settings": ["__uri__", "ms-settings:bluetooth"],
        "wifi settings": ["__uri__", "ms-settings:network-wifi"],
        "display settings": ["__uri__", "ms-settings:display"],
        "sound settings": ["__uri__", "ms-settings:sound"],
        "camera": ["__uri__", "microsoft.windows.camera:"],
        "microsoft store": ["__uri__", "ms-windows-store:"],
        "store": ["__uri__", "ms-windows-store:"],
    }

    VK = {
        "volume_up": 0xAF,
        "volume_down": 0xAE,
        "volume_mute": 0xAD,
        "media_play_pause": 0xB3,
        "media_next": 0xB0,
        "media_previous": 0xB1,
    }

    HOTKEYS = {
        "copy": ("ctrl", "c"),
        "paste": ("ctrl", "v"),
        "cut": ("ctrl", "x"),
        "undo": ("ctrl", "z"),
        "redo": ("ctrl", "y"),
        "save": ("ctrl", "s"),
        "select all": ("ctrl", "a"),
        "find": ("ctrl", "f"),
        "new tab": ("ctrl", "t"),
        "close tab": ("ctrl", "w"),
        "switch window": ("alt", "tab"),
        "show desktop": ("win", "d"),
        "lock": ("win", "l"),
    }

    KEY_CODES = {
        "ctrl": 0x11,
        "alt": 0x12,
        "shift": 0x10,
        "win": 0x5B,
        "tab": 0x09,
        "enter": 0x0D,
        "escape": 0x1B,
        "space": 0x20,
        **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},
    }

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._app_cache: dict[str, Path] | None = None

    def status(self) -> dict[str, Any]:
        root = Path(os.environ.get("SystemDrive", "C:") + "\\")
        total_disk, used_disk, _ = shutil.disk_usage(root)
        memory = self._memory_status()
        battery = self._battery_status()
        uptime_seconds = (
            int(ctypes.windll.kernel32.GetTickCount64() / 1000)
            if IS_WINDOWS
            else int(time.monotonic())
        )
        return {
            "hostname": socket.gethostname(),
            "user": getpass.getuser(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu": platform.processor() or "Unknown processor",
            "memory_percent": memory["percent"],
            "memory_used_gb": memory["used_gb"],
            "memory_total_gb": memory["total_gb"],
            "disk_percent": round(used_disk / total_disk * 100),
            "disk_used_gb": round(used_disk / 1024**3, 1),
            "disk_total_gb": round(total_disk / 1024**3, 1),
            "battery_percent": battery["percent"],
            "plugged_in": battery["plugged_in"],
            "uptime_seconds": uptime_seconds,
            "time": datetime.now().isoformat(),
        }

    def _memory_status(self) -> dict[str, Any]:
        if not IS_WINDOWS:
            return {"percent": 0, "used_gb": 0, "total_gb": 0}
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        used = stat.ullTotalPhys - stat.ullAvailPhys
        return {
            "percent": int(stat.dwMemoryLoad),
            "used_gb": round(used / 1024**3, 1),
            "total_gb": round(stat.ullTotalPhys / 1024**3, 1),
        }

    def _battery_status(self) -> dict[str, Any]:
        if not IS_WINDOWS:
            return {"percent": None, "plugged_in": None}
        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return {"percent": None, "plugged_in": None}
        percent = None if status.BatteryLifePercent == 255 else int(status.BatteryLifePercent)
        return {"percent": percent, "plugged_in": status.ACLineStatus == 1}

    def open_app(self, name: str) -> str:
        normalized = " ".join(name.lower().strip().split())
        command = self.APP_ALIASES.get(normalized)
        if not command:
            shortcut = self._find_start_menu_app(normalized)
            if shortcut:
                os.startfile(str(shortcut))
                return f"Opening {shortcut.stem}."
            raise ValueError(
                f"I couldn't find “{name}” in the safe aliases or Windows Start Menu."
            )
        if command[0] == "__uri__":
            os.startfile(command[1])
        elif command[0] == "__url__":
            webbrowser.open(command[1])
        else:
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError as exc:
                raise ValueError(f"{name.title()} is not installed or available on PATH.") from exc
        return f"Opening {name}."

    def _find_start_menu_app(self, normalized: str) -> Path | None:
        if self._app_cache is None:
            roots = [
                Path(os.environ.get("ProgramData", "C:\\ProgramData"))
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs",
                Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs",
            ]
            cache: dict[str, Path] = {}
            for root in roots:
                if not root.exists():
                    continue
                for pattern in ("*.lnk", "*.url"):
                    for shortcut in root.rglob(pattern):
                        cache.setdefault(shortcut.stem.lower(), shortcut)
                        if len(cache) >= 5000:
                            break
            self._app_cache = cache
        exact = self._app_cache.get(normalized)
        if exact:
            return exact
        matches = [
            (len(label), path)
            for label, path in self._app_cache.items()
            if normalized in label or label in normalized
        ]
        return min(matches, default=(0, None), key=lambda item: item[0])[1]

    def open_path(self, raw_path: str) -> str:
        cleaned = raw_path.strip().strip('"')
        known_folders = {
            "home": Path.home(),
            "user folder": Path.home(),
            "desktop": Path.home() / "Desktop",
            "documents": Path.home() / "Documents",
            "downloads": Path.home() / "Downloads",
            "pictures": Path.home() / "Pictures",
            "music": Path.home() / "Music",
            "videos": Path.home() / "Videos",
        }
        expanded = known_folders.get(cleaned.lower())
        if expanded is None:
            expanded = Path(os.path.expandvars(os.path.expanduser(cleaned)))
            if not expanded.is_absolute():
                expanded = Path.home() / expanded
        expanded = expanded.resolve()
        if not expanded.exists():
            raise ValueError(f"I couldn't find {expanded}.")
        os.startfile(str(expanded))
        return f"Opening {expanded.name or str(expanded)}."

    def open_url(self, target: str) -> str:
        target = target.strip()
        if not urllib.parse.urlparse(target).scheme:
            target = "https://" + target
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("That doesn't look like a safe web address.")
        webbrowser.open(target)
        return f"Opening {parsed.netloc}."

    def web_search(self, query: str) -> str:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
        return f"Searching the web for {query}."

    def youtube_search(self, query: str) -> str:
        webbrowser.open(
            "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
        )
        return f"Searching YouTube for {query}."

    def press_media_key(self, key: str, repeats: int = 1) -> str:
        if not IS_WINDOWS or key not in self.VK:
            raise ValueError("That media control is not available.")
        for _ in range(max(1, min(repeats, 10))):
            ctypes.windll.user32.keybd_event(self.VK[key], 0, 0, 0)
            ctypes.windll.user32.keybd_event(self.VK[key], 0, 2, 0)
        label = key.replace("_", " ")
        return f"{label.title()} activated."

    def press_hotkey(self, name: str) -> str:
        combo = self.HOTKEYS.get(name.lower().strip())
        if not combo:
            raise ValueError("That shortcut is not in the safe shortcut list.")
        codes = [self.KEY_CODES[key] for key in combo]
        for code in codes:
            ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        for code in reversed(codes):
            ctypes.windll.user32.keybd_event(code, 0, 2, 0)
        return f"{name.title()} shortcut sent."

    def type_text(self, text: str) -> str:
        if not IS_WINDOWS:
            raise ValueError("Typing automation is only available on Windows.")
        utf16 = text.encode("utf-16-le")
        units = [int.from_bytes(utf16[i : i + 2], "little") for i in range(0, len(utf16), 2)]
        inputs = (INPUT * (len(units) * 2))()
        for index, code in enumerate(units):
            inputs[index * 2] = INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0004, 0, 0))
            inputs[index * 2 + 1] = INPUT(type=1, ki=KEYBDINPUT(0, code, 0x0006, 0, 0))
        sent = ctypes.windll.user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise RuntimeError("Windows did not accept the full typing sequence.")
        return f"Typed {len(text)} characters into the focused window."

    def take_screenshot(self) -> str:
        pictures = Path.home() / "Pictures" / "JARVIS"
        pictures.mkdir(parents=True, exist_ok=True)
        output = pictures / f"jarvis-{datetime.now():%Y%m%d-%H%M%S}.png"
        escaped = str(output).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($i);"
            "$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size);"
            f"$i.Save('{escaped}',[System.Drawing.Imaging.ImageFormat]::Png);"
            "$g.Dispose();$i.Dispose()"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if completed.returncode != 0:
            raise RuntimeError("Windows could not capture the screen.")
        return str(output)

    def foreground_window(self) -> dict[str, Any]:
        if not IS_WINDOWS:
            return {"title": "", "pid": None}
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, len(buffer))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return {"title": buffer.value or "Untitled window", "pid": int(pid.value)}

    def list_windows(self, limit: int = 30) -> list[dict[str, Any]]:
        if not IS_WINDOWS:
            return []
        user32 = ctypes.windll.user32
        windows: list[dict[str, Any]] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(handle, _extra):
            if len(windows) >= max(1, min(limit, 100)) or not user32.IsWindowVisible(handle):
                return True
            length = user32.GetWindowTextLengthW(handle)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            title = buffer.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            windows.append({"title": title, "pid": int(pid.value)})
            return True

        user32.EnumWindows(collect, 0)
        return windows

    def focus_window(self, query: str) -> str:
        if not IS_WINDOWS:
            raise ValueError("Window controls are only available on Windows.")
        needle = " ".join(query.lower().split())
        if not needle:
            raise ValueError("Tell me which window to focus.")
        user32 = ctypes.windll.user32
        matches: list[tuple[int, int, str, int]] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(handle, _extra):
            if not user32.IsWindowVisible(handle):
                return True
            length = user32.GetWindowTextLengthW(handle)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            title = buffer.value.strip()
            lowered = title.lower()
            if needle in lowered:
                matches.append((0 if lowered == needle else 1, len(title), title, handle))
            return True

        user32.EnumWindows(collect, 0)
        if not matches:
            raise ValueError(f"I couldn't find an open window matching “{query}”.")
        _, _, title, handle = min(matches, key=lambda item: item[:2])
        user32.ShowWindow(handle, 9)
        if not user32.SetForegroundWindow(handle):
            raise RuntimeError(f"Windows blocked focus switching to {title}.")
        return f"Switched to {title}."

    def window_action(self, action: str) -> str:
        if not IS_WINDOWS:
            raise ValueError("Window controls are only available on Windows.")
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        title = self.foreground_window()["title"]
        show_commands = {"minimize": 6, "maximize": 3, "restore": 9}
        if action in show_commands:
            user32.ShowWindow(handle, show_commands[action])
        elif action == "close":
            user32.PostMessageW(handle, 0x0010, 0, 0)
        else:
            raise ValueError("Unknown window action.")
        return f"{action.title()} sent to {title}."

    def clipboard_read(self) -> str:
        if not IS_WINDOWS:
            raise ValueError("Clipboard access is only available on Windows.")
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        if not user32.OpenClipboard(None):
            raise RuntimeError("The clipboard is busy.")
        try:
            handle = user32.GetClipboardData(13)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def clipboard_write(self, text: str) -> str:
        if not IS_WINDOWS:
            raise ValueError("Clipboard access is only available on Windows.")
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        encoded = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(0x0002, len(encoded))
        if not handle:
            raise RuntimeError("Windows could not allocate clipboard memory.")
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, encoded, len(encoded))
        kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            raise RuntimeError("The clipboard is busy.")
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(13, handle):
                kernel32.GlobalFree(handle)
                raise RuntimeError("Windows could not update the clipboard.")
        finally:
            user32.CloseClipboard()
        return f"Copied {len(text)} characters to the clipboard."

    def find_files(self, query: str, limit: int = 40) -> list[str]:
        needle = query.strip().lower()
        if not needle:
            return []
        skip = {
            "appdata",
            "node_modules",
            ".git",
            ".venv",
            "__pycache__",
            "windowsapps",
        }
        matches: list[str] = []
        scanned = 0
        for root, dirs, files in os.walk(Path.home()):
            dirs[:] = [
                name
                for name in dirs
                if name.lower() not in skip and not name.startswith(".")
            ]
            for filename in files:
                scanned += 1
                if needle in filename.lower():
                    matches.append(str(Path(root) / filename))
                    if len(matches) >= limit:
                        return matches
                if scanned >= 80_000:
                    return matches
        return matches

    def list_processes(self, limit: int = 30) -> list[dict[str, Any]]:
        completed = subprocess.run(
            ["tasklist.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            raise RuntimeError("Windows could not list processes.")
        rows = []
        for row in csv.reader(io.StringIO(completed.stdout)):
            if len(row) < 5:
                continue
            memory_text = row[4].replace(",", "").replace(" K", "").strip()
            memory_kb = int(memory_text) if memory_text.isdigit() else 0
            rows.append(
                {
                    "name": row[0],
                    "pid": int(row[1]),
                    "session": row[2],
                    "memory_mb": round(memory_kb / 1024, 1),
                }
            )
        rows.sort(key=lambda item: item["memory_mb"], reverse=True)
        return rows[: max(1, min(limit, 100))]

    def terminate_process(self, process: str) -> str:
        target = process.strip()
        if not re.fullmatch(r"[\w .()_-]{1,120}(?:\.exe)?", target, re.I):
            raise ValueError("That process name is not valid.")
        completed = subprocess.run(
            ["taskkill.exe", "/IM", target, "/T"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or f"Windows could not close {target}.")
        return f"Closed {target}."

    def set_brightness(self, percent: int) -> str:
        value = max(0, min(100, int(percent)))
        script = (
            "$m=Get-CimInstance -Namespace root/WMI "
            "-ClassName WmiMonitorBrightnessMethods -ErrorAction Stop;"
            f"$m | Invoke-CimMethod -MethodName WmiSetBrightness "
            f"-Arguments @{{Timeout=1;Brightness={value}}} | Out-Null"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            raise RuntimeError("This display does not expose software brightness control.")
        return f"Brightness set to {value}%."

    def notify(self, title: str, message: str) -> str:
        safe_title = title[:80].replace("'", "''")
        safe_message = message[:240].replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;"
            "$n.Visible=$true;"
            f"$n.ShowBalloonTip(4000,'{safe_title}','{safe_message}',"
            "[System.Windows.Forms.ToolTipIcon]::Info);"
            "Start-Sleep -Seconds 4;$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "Notification sent."

    def lock(self) -> str:
        ctypes.windll.user32.LockWorkStation()
        return "Locking the computer."

    def power(self, action: str) -> str:
        commands = {
            "shutdown": ["shutdown.exe", "/s", "/t", "0"],
            "restart": ["shutdown.exe", "/r", "/t", "0"],
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        }
        if action not in commands:
            raise ValueError("Unknown power action.")
        subprocess.Popen(commands[action])
        return f"Starting {action}."

    def run_shell(self, command: str) -> dict[str, Any]:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = completed.stdout.strip()[-8000:]
        stderr = completed.stderr.strip()[-4000:]
        return {
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
