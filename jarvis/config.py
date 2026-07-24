from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass
class JarvisConfig:
    assistant_name: str = "JARVIS"
    user_name: str = "Boss"
    speak_responses: bool = True
    allow_shell: bool = False
    allow_power_actions: bool = True
    intelligence_enabled: bool = True
    intelligence_timeout_seconds: int = 45
    open_browser_on_start: bool = True
    port: int = 8765


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(JarvisConfig())

    def load(self) -> JarvisConfig:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            # Migrate settings written by builds before the JARVIS rebrand.
            if "intelligence_enabled" not in raw and "hermes_enabled" in raw:
                raw["intelligence_enabled"] = raw["hermes_enabled"]
            if "intelligence_timeout_seconds" not in raw and "hermes_timeout_seconds" in raw:
                raw["intelligence_timeout_seconds"] = raw["hermes_timeout_seconds"]
            allowed = JarvisConfig.__dataclass_fields__.keys()
            clean = {key: value for key, value in raw.items() if key in allowed}
            return JarvisConfig(**clean)

    def update(self, changes: dict[str, Any]) -> JarvisConfig:
        current = self.load()
        allowed = set(JarvisConfig.__dataclass_fields__) - {"port"}
        for key, value in changes.items():
            if key in allowed and isinstance(value, type(getattr(current, key))):
                setattr(current, key, value)
        self.save(current)
        return current

    def save(self, config: JarvisConfig) -> None:
        with self._lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
            temp.replace(self.path)
