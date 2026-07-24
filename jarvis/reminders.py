from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ReminderStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"reminders": []})

    def _read(self) -> dict[str, Any]:
        with self._lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = {"reminders": []}
            value.setdefault("reminders", [])
            return value

    def _write(self, value: dict[str, Any]) -> None:
        with self._lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)

    def add(self, text: str, due_at: datetime) -> dict[str, Any]:
        data = self._read()
        item = {
            "id": secrets.token_urlsafe(8),
            "text": text.strip(),
            "due_at": due_at.astimezone(timezone.utc).isoformat(),
            "created_at": now_utc().isoformat(),
            "delivered": False,
            "dismissed": False,
        }
        data["reminders"].append(item)
        data["reminders"] = data["reminders"][-500:]
        self._write(data)
        return item

    def list(self, include_dismissed: bool = False) -> list[dict[str, Any]]:
        items = self._read()["reminders"]
        if not include_dismissed:
            items = [item for item in items if not item.get("dismissed")]
        return sorted(items, key=lambda item: item.get("due_at", ""))

    def due(self, mark_delivered: bool = False) -> list[dict[str, Any]]:
        data = self._read()
        now = now_utc()
        due_items = []
        changed = False
        for item in data["reminders"]:
            if item.get("dismissed") or item.get("delivered"):
                continue
            try:
                due_at = datetime.fromisoformat(item["due_at"])
            except (KeyError, ValueError):
                continue
            if due_at <= now:
                due_items.append(item)
                if mark_delivered:
                    item["delivered"] = True
                    changed = True
        if changed:
            self._write(data)
        return due_items

    def dismiss(self, reminder_id: str) -> bool:
        data = self._read()
        changed = False
        for item in data["reminders"]:
            if item.get("id") == reminder_id:
                item["dismissed"] = True
                changed = True
                break
        if changed:
            self._write(data)
        return changed


class RoutineStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"routines": {}})

    def _read(self) -> dict[str, Any]:
        with self._lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = {"routines": {}}
            value.setdefault("routines", {})
            return value

    def _write(self, value: dict[str, Any]) -> None:
        with self._lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)

    def save(self, name: str, commands: list[str]) -> dict[str, Any]:
        data = self._read()
        key = name.strip().lower()
        item = {
            "name": name.strip(),
            "commands": [command.strip() for command in commands if command.strip()][:12],
            "updated_at": now_utc().isoformat(),
        }
        data["routines"][key] = item
        self._write(data)
        return item

    def get(self, name: str) -> dict[str, Any] | None:
        return self._read()["routines"].get(name.strip().lower())

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._read()["routines"].values(), key=lambda item: item["name"].lower())

    def delete(self, name: str) -> bool:
        data = self._read()
        removed = data["routines"].pop(name.strip().lower(), None) is not None
        if removed:
            self._write(data)
        return removed
