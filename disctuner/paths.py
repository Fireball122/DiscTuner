"""Where the tools DiscTuner drives live, and the few settings it remembers.

Lookup order for each tool: environment variable, saved setting, then the usual places.
Nothing is hardcoded to one machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "DiscTuner"
PROJECT_DIR = Path(__file__).resolve().parent.parent

ENV_DISCPROBE = "DISCTUNER_DISCPROBE"
ENV_DIS_EXE = "DISCTUNER_DIS_EXE"

DISCPROBE_URL = "https://github.com/Ashley-QwQ/disc-probe"


class ToolNotFound(RuntimeError):
    """A required external tool is missing; the message says how to point DiscTuner at it."""


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_NAME


def _settings_file() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        with open(_settings_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_setting(key: str, value: str) -> None:
    settings = load_settings()
    settings[key] = value
    _settings_file().parent.mkdir(parents=True, exist_ok=True)
    with open(_settings_file(), "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def is_discprobe(path: str | Path) -> bool:
    """True for a disc-probe checkout: the folder that holds the `discprobe` package."""
    return (Path(path) / "discprobe" / "__main__.py").is_file()


def find_discprobe() -> Path:
    """The disc-probe folder: the measurement and burn tooling DiscTuner wraps."""
    home = Path.home()
    candidates = [
        os.environ.get(ENV_DISCPROBE),
        load_settings().get("discprobe_dir"),
        Path.cwd() / "disc-probe",
        PROJECT_DIR.parent / "disc-probe",
        home / "disc-probe",
        home / "Downloads" / "disc-probe",
        home / "Documents" / "disc-probe",
    ]
    for candidate in candidates:
        if candidate and is_discprobe(candidate):
            return Path(candidate)
    raise ToolNotFound(f"disc-probe was not found. Download it from {DISCPROBE_URL} and point DiscTuner at its folder.")


def find_dis_exe() -> Path | None:
    """DiscImageStudio's executable, or None when it has to be chosen by hand."""
    for candidate in (os.environ.get(ENV_DIS_EXE), load_settings().get("dis_exe")):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    searches = [
        (Path.home() / "Downloads", "DiscImageStudio*.exe"),
        (local / "Programs" / "DiscImageStudio", "*.exe"),
    ]
    for folder, pattern in searches:
        if folder.is_dir():
            matches = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            if matches:
                return matches[0]
    return None
