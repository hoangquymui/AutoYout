"""ffprobe helpers: durations, media metadata and YouTube timestamps."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _popen(cmd: Sequence[str], timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def resolve_tool(value: str | os.PathLike | None, default_name: str) -> str:
    """Resolve a configured executable, checking explicit paths, project bin, then ``PATH``."""
    if value:
        raw = str(value).strip().strip('"')
        if raw and raw != default_name:
            p = Path(raw)
            if p.is_file():
                return str(p)
            found = shutil.which(raw)
            if found:
                return found

    # Check project-local bin directory (e.g. <project>/bin/ffmpeg.exe)
    project_bin = Path(__file__).resolve().parent.parent / "bin"
    for ext in (".exe", "", ".cmd", ".bat"):
        candidate = project_bin / f"{default_name}{ext}"
        if candidate.is_file():
            return str(candidate)

    found = shutil.which(default_name)
    return found or default_name


def ffmpeg_available(ffmpeg: str = "ffmpeg") -> bool:
    try:
        return _popen([ffmpeg, "-version"], timeout=10).returncode == 0
    except Exception:
        return False


def ffprobe_available(ffprobe: str = "ffprobe") -> bool:
    try:
        return _popen([ffprobe, "-version"], timeout=10).returncode == 0
    except Exception:
        return False


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    try:
        proc = _popen([ffmpeg, "-version"], timeout=10)
        first = (proc.stdout or "").splitlines()
        return first[0].strip() if first else "unknown"
    except Exception as exc:
        return f"unavailable ({exc})"


def detect_encoders(ffmpeg: str = "ffmpeg") -> set[str]:
    """Names of every encoder the configured ffmpeg build advertises."""
    encoders: set[str] = set()
    try:
        proc = _popen([ffmpeg, "-hide_banner", "-encoders"], timeout=20)
        for line in (proc.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            flags = parts[0]
            if len(flags) >= 6 and ("V" in flags[0:2] or "A" in flags[0:2] or "." in flags[0:2]):
                encoders.add(parts[1])
    except Exception:
        return {"libx264"}
    return encoders or {"libx264"}


def detect_hwaccels(ffmpeg: str = "ffmpeg") -> set[str]:
    try:
        proc = _popen([ffmpeg, "-hide_banner", "-hwaccels"], timeout=20)
        return {line.strip() for line in (proc.stdout or "").splitlines()[1:] if line.strip()}
    except Exception:
        return set()


def probe(path: str | os.PathLike, ffprobe: str = "ffprobe", timeout: float = 60.0) -> Dict[str, Any]:
    """Return ffprobe's JSON for a file; empty dict when it cannot be read."""
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = _popen(cmd, timeout=timeout)
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout or "{}")
    except ValueError:
        return {}


def probe_duration(path: str | os.PathLike, ffprobe: str = "ffprobe") -> float:
    """Duration in seconds, using ffprobe then soundfile as a fallback."""
    info = probe(path, ffprobe)
    try:
        value = float(info.get("format", {}).get("duration", 0.0))
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    for stream in info.get("streams", []) or []:
        try:
            value = float(stream.get("duration", 0.0))
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    try:
        import soundfile as sf

        with sf.SoundFile(str(path)) as handle:
            if handle.samplerate:
                return float(len(handle)) / float(handle.samplerate)
    except Exception:
        pass
    return 0.0


def probe_streams(path: str | os.PathLike, ffprobe: str = "ffprobe") -> Tuple[List[dict], List[dict]]:
    info = probe(path, ffprobe)
    streams = info.get("streams", []) or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    return video, audio


def format_timestamp(seconds: float) -> str:
    """YouTube style ``M:SS`` or ``H:MM:SS`` (rounded down)."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def build_timestamps(entries: Iterable[Tuple[float, str]], clamp_zero: bool = True) -> str:
    """Render ``[(start_seconds, label), ...]`` as YouTube chapter lines."""
    lines: List[str] = []
    seen: set[str] = set()
    for start, label in entries:
        stamp = format_timestamp(start)
        clean = re.sub(r"\s+", " ", str(label or "")).strip() or "Chương"
        if clamp_zero and start <= 0:
            stamp = "0:00"
        line = f"{stamp} {clean}"
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def is_probably_audio(path: str | os.PathLike) -> bool:
    video, audio = probe_streams(path)
    return bool(audio) and not bool(video)
