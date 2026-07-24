from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"memories": {}, "notes": []})

    def _read(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {"memories": {}, "notes": []}
            data.setdefault("memories", {})
            data.setdefault("notes", [])
            return data

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)

    def remember(self, key: str, value: str) -> None:
        data = self._read()
        data["memories"][key.strip().lower()] = {
            "value": value.strip(),
            "updated_at": utc_now(),
        }
        self._write(data)

    def recall(self, key: str) -> str | None:
        item = self._read()["memories"].get(key.strip().lower())
        return item["value"] if item else None

    def forget(self, key: str) -> bool:
        data = self._read()
        removed = data["memories"].pop(key.strip().lower(), None) is not None
        if removed:
            self._write(data)
        return removed

    def add_note(self, text: str) -> dict[str, str]:
        data = self._read()
        note = {"text": text.strip(), "created_at": utc_now()}
        data["notes"].append(note)
        data["notes"] = data["notes"][-200:]
        self._write(data)
        return note

    def snapshot(self) -> dict[str, Any]:
        return self._read()
