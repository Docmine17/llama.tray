import json
import os
import sys
from typing import Any

import updater


class LlamaConfig:
    def __init__(self) -> None:
        self.defaults: dict[str, Any] = {
            "current_version": "",
            "backend": "vulkan",
            "terminal_integration": False,
            "current_profile": "Default",
            "autostart": "Disabled",
        }
        self.data: dict[str, Any] = self.defaults.copy()
        self.migration_needed: Any = None
        self.load()

    def load(self) -> None:
        if os.path.exists(updater.CONFIG_FILE):
            try:
                with open(updater.CONFIG_FILE, "r") as f:
                    file_data = json.load(f)
                    self.data.update(file_data)

                    if "env_vars" in file_data or "args" in file_data:
                        self.migration_needed = {
                            "env_vars": file_data.get("env_vars", ""),
                            "args": file_data.get(
                                "args", "--port 8080 --host 127.0.0.1"
                            ),
                        }
                        if "env_vars" in self.data:
                            del self.data["env_vars"]
                        if "args" in self.data:
                            del self.data["args"]
                        self.save()
            except Exception as e:
                print(f"Error loading config: {e}", file=sys.stderr)

    def save(self) -> None:
        try:
            os.makedirs(updater.CONFIG_DIR, exist_ok=True)
            with open(updater.CONFIG_FILE, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}", file=sys.stderr)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def set_bulk(self, updates: dict[str, Any]) -> None:
        """Update multiple keys and save only once."""
        self.data.update(updates)
        self.save()
