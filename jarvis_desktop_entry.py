from __future__ import annotations

import ctypes
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _data_dir() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "JARVIS"
        / "data"
    )


def _record_crash(kind: str, detail: str) -> None:
    try:
        path = _data_dir() / "crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{datetime.now(timezone.utc).isoformat()}] {kind}\n{detail[-12000:]}\n")
    except OSError:
        pass


def _main_exception_hook(exc_type, exc_value, exc_traceback) -> None:
    _record_crash(
        "unhandled-main-thread",
        "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
    )
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _thread_exception_hook(args) -> None:
    _record_crash(
        f"unhandled-thread:{args.thread.name if args.thread else 'unknown'}",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
    )


def _claim_single_instance() -> object | None:
    if os.name != "nt":
        return object()
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\OrbitWebsites.JARVIS.Desktop")
    if not handle or kernel32.GetLastError() == 183:
        return None
    return handle


sys.excepthook = _main_exception_hook
threading.excepthook = _thread_exception_hook

from jarvis.desktop import main  # noqa: E402


if __name__ == "__main__":
    instance_handle = _claim_single_instance()
    if instance_handle is not None:
        main()
