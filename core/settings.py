"""Default/user settings loading with a recursive merge."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from utils.files import load_json, write_json_atomic

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "default_settings.json"
if not DEFAULT_SETTINGS_PATH.is_file() and (ROOT / "_internal" / "config" / "default_settings.json").is_file():
    DEFAULT_SETTINGS_PATH = ROOT / "_internal" / "config" / "default_settings.json"
USER_SETTINGS_PATH = CONFIG_DIR / "user_settings.json"
CREDENTIALS_DIR = CONFIG_DIR / "youtube_credentials"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def default_h2mtts_path() -> str:
    """Return default H2MTTS directory path inside this project."""
    if (ROOT / "tts" / "H2MTTS").is_dir():
        return str((ROOT / "tts" / "H2MTTS").as_posix())
    return str((ROOT / "H2MTTS").as_posix())


def load_settings() -> Dict[str, Any]:
    defaults = load_json(DEFAULT_SETTINGS_PATH, {}) or {}
    user = load_json(USER_SETTINGS_PATH, {}) or {}
    merged = _deep_merge(defaults, user)
    merged.setdefault("paths", {})
    merged.setdefault("project", {})
    merged.setdefault("tts", {})
    merged.setdefault("video", {})
    merged.setdefault("youtube", {})
    merged.setdefault("app", {})

    # Default h2mtts to inside this project if unset or legacy external path
    h2 = str(merged["paths"].get("h2mtts") or "").strip()
    if not h2 or h2.replace("\\", "/").lower() == "d:/mui/f375/tts/h2mtts":
        merged["paths"]["h2mtts"] = default_h2mtts_path()

    return merged


def save_settings(settings: Dict[str, Any]) -> None:
    write_json_atomic(USER_SETTINGS_PATH, settings)


def get_setting(settings: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = settings
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_setting(settings: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = settings
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def resolve_config_path(value: str) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def credentials_dir() -> Path:
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    return CREDENTIALS_DIR
