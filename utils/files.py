"""File-system helpers shared by the AutoYout modules."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional

_NUM_RE = re.compile(r"(\d+)")
_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def natural_key(text: str) -> list:
    """Sort key that keeps ``1.txt < 2.txt < 10.txt``.

    The numeric runs are compared as integers, text runs case-insensitively.
    """
    parts = _NUM_RE.split(str(text))
    key: list = []
    for index, part in enumerate(parts):
        if index % 2:  # numeric run
            key.append((0, int(part), part))
        else:
            key.append((1, part.casefold(), part))
    return key


def natural_sorted(paths: Iterable[os.PathLike | str], by_name: bool = True) -> List[Path]:
    items = [Path(p) for p in paths]
    return sorted(items, key=lambda p: natural_key(p.name if by_name else str(p)))


def safe_filename(name: str, fallback: str = "output") -> str:
    """Make a string safe to use as a Windows file name (keeps Vietnamese)."""
    cleaned = _INVALID_WIN_CHARS.sub("_", str(name or "")).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:150] or fallback


def unique_path(path: os.PathLike | str) -> Path:
    """Return a non-existing path by appending `` (n)`` before the suffix."""
    path = Path(path)
    if not path.exists():
        return path
    for i in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Không tìm được tên file trống cho {path}")


def read_text_smart(path: os.PathLike | str) -> str:
    """Read a Vietnamese text file, tolerating BOM and common legacy encodings."""
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1258", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_json_atomic(path: os.PathLike | str, payload: Any) -> None:
    """Write JSON atomically with retry on Windows sharing violations (WinError 32)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())

        # Windows can temporarily lock files during antivirus scanning,
        # QFileSystemModel, or background readers (WinError 32 / WinError 5).
        # Retry with progressive backoff.
        last_err: Optional[Exception] = None
        for attempt in range(15):
            try:
                os.replace(tmp_name, path)
                last_err = None
                break
            except OSError as exc:
                last_err = exc
                time.sleep(0.04 * (attempt + 1))

        if last_err is not None:
            # Fallback: attempt direct overwrite if replace remains locked
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
            except Exception:
                raise last_err
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def load_json(path: os.PathLike | str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def ensure_dir(path: os.PathLike | str | None) -> Optional[Path]:
    if not path:
        return None
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def open_in_explorer(path: os.PathLike | str) -> None:
    p = Path(path)
    if p.is_file():
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        os.startfile(str(p))  # type: ignore[attr-defined]
