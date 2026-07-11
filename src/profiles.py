import json
import os
import sys
from typing import Optional

import updater


class LlamaProfilesManager:
    def __init__(self) -> None:
        self.profiles: list[dict] = []
        self.load()

    def load(self) -> None:
        if os.path.exists(updater.PROFILES_FILE):
            try:
                with open(updater.PROFILES_FILE, "r") as f:
                    self.profiles = json.load(f)
            except Exception as e:
                print(f"Error loading profiles: {e}", file=sys.stderr)

        # Ensure there is always at least one profile
        if not self.profiles:
            self.profiles = [
                {
                    "name": "Default",
                    "env_vars": "",
                    "args": "--port 8080 --host 127.0.0.1",
                }
            ]
            self.save()

    def save(self) -> None:
        try:
            os.makedirs(updater.CONFIG_DIR, exist_ok=True)
            with open(updater.PROFILES_FILE, "w") as f:
                json.dump(self.profiles, f, indent=4)
        except Exception as e:
            print(f"Error saving profiles: {e}", file=sys.stderr)

    def get_profile(self, name: str) -> Optional[dict]:
        for p in self.profiles:
            if p["name"] == name:
                return p
        return None
