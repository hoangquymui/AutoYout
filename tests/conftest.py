"""Shared test fixtures: headless Qt and an ffmpeg availability probe."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _ffmpeg_works() -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "-version"], capture_output=True, timeout=15,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        return proc.returncode == 0
    except Exception:
        return False


FFMPEG_AVAILABLE = _ffmpeg_works()

requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg không có trong PATH"
)


@pytest.fixture
def ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"
