"""Join rendered cue MP3s into the item MP3 + SRT (real ffmpeg)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import requires_ffmpeg
from core.dubbing import CueJob, DubbingEngine, save_cues
from core.project_manager import ProjectManager
from core.settings import load_settings
from utils.media_probe import probe_duration


def _tone(ffmpeg: str, target: Path, seconds: float) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "libmp3lame",
         "-b:a", "192k", str(target)],
        check=True, creationflags=0x08000000,
    )
    return target


@pytest.fixture
def project(tmp_path: Path, ffmpeg: str) -> tuple[ProjectManager, object, list[CueJob]]:
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "tap.txt").write_text("Lồng tiếng thử.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("tap")
    assert item is not None
    manager.update_item("tap", dub_mode="srt")

    first = _tone(ffmpeg, tmp_path / "src" / "a.mp3", 1.0)
    second = _tone(ffmpeg, tmp_path / "src" / "b.mp3", 2.0)
    third = _tone(ffmpeg, tmp_path / "src" / "c.mp3", 1.0)
    cues = [
        CueJob(1, 0.0, 1.0, "Một", out=str(first)),
        CueJob(2, 2.0, 4.0, "Hai", out=str(second)),
        CueJob(3, 5.0, 6.0, "Ba", out=str(third)),
    ]
    save_cues(manager, "tap", cues)
    return manager, item, cues


@requires_ffmpeg
def test_join_timeline_keeps_original_slots(project) -> None:
    manager, item, cues = project
    engine = DubbingEngine(load_settings())

    mp3, srt, duration = engine.join_item(manager, item, cues, mode="timeline")

    assert mp3 == Path(item.audio_path)
    assert mp3.is_file() and mp3.stat().st_size > 1000
    assert srt.is_file()
    assert duration == pytest.approx(6.0, abs=0.4)
    text = srt.read_text(encoding="utf-8")
    assert "00:00:05,000 --> 00:00:06,000" in text


@requires_ffmpeg
def test_join_natural_uses_real_durations_and_gap(project) -> None:
    manager, item, cues = project
    engine = DubbingEngine(load_settings())

    mp3, srt, duration = engine.join_item(manager, item, cues, mode="natural", gap=0.2)

    assert mp3.is_file()
    assert duration == pytest.approx(4.4, abs=0.5)
    assert probe_duration(mp3) > 3.0
    lines = srt.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "1"
    assert "00:00:00,000" in lines[1]


@requires_ffmpeg
def test_join_without_rendered_cues_raises(project) -> None:
    from core.dubbing import DubbingError

    manager, item, _cues = project
    empty = [CueJob(1, 0.0, 1.0, "Chưa render")]
    engine = DubbingEngine(load_settings())

    with pytest.raises(DubbingError):
        engine.join_item(manager, item, empty, mode="timeline")
