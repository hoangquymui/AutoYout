"""Default/user settings loading with a recursive merge."""
from __future__ import annotations

import copy
import os
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


def normalize_h2mtts_path(value: str | os.PathLike | None) -> str:
    """Normalize H2MTTS path to a valid path on the current machine.

    1. Empty value -> returns default_h2mtts_path().
    2. Relative path -> resolves against current ROOT.
    3. Legacy developer path (containing 'mui/f375') -> if it doesn't match ROOT or doesn't exist,
       falls back to default_h2mtts_path() so other machines never keep hardcoded 'D:/mui/f375'.
    4. Non-existent absolute path from another machine/drive -> falls back to default_h2mtts_path().
    5. Existing valid directory -> returns its resolved POSIX path.
    """
    raw = str(value or "").strip()
    if not raw:
        return default_h2mtts_path()

    p = Path(raw)
    if not p.is_absolute():
        resolved = (ROOT / p).resolve()
        return str(resolved.as_posix())

    norm_lower = raw.replace("\\", "/").lower()
    root_norm = str(ROOT).replace("\\", "/").lower()

    # Legacy developer external paths or paths from another machine
    if norm_lower == "d:/mui/f375/tts/h2mtts":
        return default_h2mtts_path()
    if "mui/f375" in norm_lower and "mui/f375" not in root_norm:
        return default_h2mtts_path()

    # Absolute path that doesn't exist on this machine
    if not p.exists():
        return default_h2mtts_path()

    return str(p.resolve().as_posix())


def default_piper_path() -> str:
    """Return default PiperTTS directory path inside this project."""
    if (ROOT / "tts" / "PiperTTS").is_dir():
        return str((ROOT / "tts" / "PiperTTS").as_posix())
    return str((ROOT / "PiperTTS").as_posix())


def normalize_piper_path(value: str | os.PathLike | None) -> str:
    """Normalize PiperTTS path to a valid path on the current machine."""
    raw = str(value or "").strip()
    if not raw:
        return default_piper_path()
    p = Path(raw)
    if not p.is_absolute():
        resolved = (ROOT / p).resolve()
        return str(resolved.as_posix())
    norm_lower = raw.replace("\\", "/").lower()
    root_norm = str(ROOT).replace("\\", "/").lower()
    if "mui/f375" in norm_lower and "mui/f375" not in root_norm:
        return default_piper_path()
    if not p.exists():
        return default_piper_path()
    return str(p.resolve().as_posix())


def default_f5tts_path() -> str:
    """Return default F5-TTS / E2-TTS directory path inside this project."""
    if (ROOT / "tts" / "F5TTS").is_dir():
        return str((ROOT / "tts" / "F5TTS").as_posix())
    return str((ROOT / "F5TTS").as_posix())


def normalize_f5tts_path(value: str | os.PathLike | None) -> str:
    """Normalize F5-TTS / E2-TTS path to a valid path on the current machine."""
    raw = str(value or "").strip()
    if not raw:
        return default_f5tts_path()
    p = Path(raw)
    if not p.is_absolute():
        resolved = (ROOT / p).resolve()
        return str(resolved.as_posix())
    norm_lower = raw.replace("\\", "/").lower()
    root_norm = str(ROOT).replace("\\", "/").lower()
    if "mui/f375" in norm_lower and "mui/f375" not in root_norm:
        return default_f5tts_path()
    if not p.exists():
        return default_f5tts_path()
    return str(p.resolve().as_posix())


def default_xtts_path() -> str:
    """Return default Coqui XTTS-v2 directory path inside this project."""
    if (ROOT / "tts" / "XTTSv2").is_dir():
        return str((ROOT / "tts" / "XTTSv2").as_posix())
    return str((ROOT / "XTTSv2").as_posix())


def normalize_xtts_path(value: str | os.PathLike | None) -> str:
    """Normalize Coqui XTTS-v2 path to a valid path on the current machine."""
    raw = str(value or "").strip()
    if not raw:
        return default_xtts_path()
    p = Path(raw)
    if not p.is_absolute():
        resolved = (ROOT / p).resolve()
        return str(resolved.as_posix())
    norm_lower = raw.replace("\\", "/").lower()
    root_norm = str(ROOT).replace("\\", "/").lower()
    if "mui/f375" in norm_lower and "mui/f375" not in root_norm:
        return default_xtts_path()
    if not p.exists():
        return default_xtts_path()
    return str(p.resolve().as_posix())


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

    # Normalize h2mtts, piper, f5tts, and xtts paths so they always work on the current machine
    h2 = str(merged["paths"].get("h2mtts") or "").strip()
    merged["paths"]["h2mtts"] = normalize_h2mtts_path(h2)
    piper_raw = str(merged["paths"].get("piper") or "").strip()
    merged["paths"]["piper"] = normalize_piper_path(piper_raw)
    f5_raw = str(merged["paths"].get("f5tts") or "").strip()
    merged["paths"]["f5tts"] = normalize_f5tts_path(f5_raw)
    xtts_raw = str(merged["paths"].get("xtts") or "").strip()
    merged["paths"]["xtts"] = normalize_xtts_path(xtts_raw)

    # Normalize ffmpeg / ffprobe if they point to an invalid absolute path
    for tool_key, default_tool in [("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe")]:
        tool_val = str(merged["paths"].get(tool_key) or "").strip()
        if tool_val and tool_val != default_tool:
            p = Path(tool_val)
            if p.is_absolute() and not p.exists():
                merged["paths"][tool_key] = default_tool

    # Reset last_project if from another machine and doesn't exist
    last_proj = str(merged.get("project", {}).get("last_project") or "").strip()
    if last_proj and Path(last_proj).is_absolute() and not Path(last_proj).exists():
        merged["project"]["last_project"] = ""

    return merged


def save_settings(settings: Dict[str, Any]) -> None:
    settings_to_save = copy.deepcopy(settings)
    paths = settings_to_save.get("paths")
    if isinstance(paths, dict):
        h2 = str(paths.get("h2mtts") or "").strip()
        if h2:
            try:
                h2_p = Path(h2).resolve()
                root_p = ROOT.resolve()
                if h2_p == (root_p / "H2MTTS").resolve():
                    paths["h2mtts"] = "H2MTTS"
                elif h2_p == (root_p / "tts" / "H2MTTS").resolve():
                    paths["h2mtts"] = "tts/H2MTTS"
            except Exception:
                pass
        for tool_key in ("ffmpeg", "ffprobe"):
            tool_val = str(paths.get(tool_key) or "").strip()
            if tool_val:
                try:
                    t_p = Path(tool_val).resolve()
                    root_p = ROOT.resolve()
                    if (
                        t_p == (root_p / "bin" / f"{tool_key}.exe").resolve()
                        or t_p == (root_p / f"{tool_key}.exe").resolve()
                    ):
                        paths[tool_key] = tool_key
                except Exception:
                    pass
    write_json_atomic(USER_SETTINGS_PATH, settings_to_save)


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
