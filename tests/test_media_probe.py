"""Timestamp formatting and ffprobe helpers."""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

from utils.media_probe import (
    build_timestamps,
    ffmpeg_available,
    format_duration,
    format_timestamp,
    probe_duration,
    resolve_tool,
)


def _write_wav(path: Path, seconds: int = 2, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * rate * seconds)


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(65.9) == "1:05"
    assert format_timestamp(3661) == "1:01:01"


def test_format_duration() -> None:
    assert format_duration(0) == "0s"
    assert format_duration(5.4) == "5s"
    assert format_duration(65) == "1m 05s"
    assert format_duration(3725) == "1h 02m 05s"


def test_build_timestamps_dedupes_and_clamps_zero() -> None:
    text = build_timestamps([(0, "Mở đầu"), (0, "Mở đầu"), (65, "Phần hai")])
    assert text.splitlines() == ["0:00 Mở đầu", "1:05 Phần hai"]
    assert build_timestamps([(12.5, "  ")]) == "0:12 Chương"


def test_probe_duration_reads_wav(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    _write_wav(path, seconds=2)
    assert 1.5 <= probe_duration(path) <= 2.5


def test_resolve_tool_prefers_existing_path(tmp_path: Path) -> None:
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"")
    assert resolve_tool(fake, "ffmpeg") == str(fake)
    assert resolve_tool(str(tmp_path / "missing.exe"), "definitely-missing-tool") in (
        "definitely-missing-tool",
    )


def test_ffmpeg_available_returns_bool() -> None:
    assert isinstance(ffmpeg_available(shutil.which("ffmpeg") or "ffmpeg"), bool)
