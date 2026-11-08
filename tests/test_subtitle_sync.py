"""Tests verifying subtitle synchronization between audio and video rendering."""
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from core.ffmpeg_renderer import FFmpegRenderer, VideoConfig
from core.project_manager import ProjectItem, ProjectManager
from core.subtitles import Cue, write_srt, parse_srt


def test_split_subtitle_text_weights_by_character_count():
    # Long sentence should get more duration than short sentence
    text = "Chào bạn. Đây là một câu rất dài có nhiều nội dung cần được đọc trong nhiều giây hơn rất nhiều so với câu ngắn đầu tiên."
    events = FFmpegRenderer._split_subtitle_text(text, start=0.0, end=10.0)
    assert len(events) == 2
    short_dur = events[0][1] - events[0][0]
    long_dur = events[1][1] - events[1][0]
    # Long sentence must have significantly longer display duration than short sentence
    assert long_dur > short_dur * 2.5
    assert abs(events[0][0] - 0.0) < 1e-4
    assert abs(events[-1][1] - 10.0) < 1e-4


def test_srt_events_auto_loaded_when_subtitle_enabled(tmp_path: Path):
    manager = ProjectManager(tmp_path)
    audio_path = tmp_path / "audio" / "test_video.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"dummy mp3 data")

    item = ProjectItem(
        base="test_video",
        text_file=None,
        json_file=None,
        text="Đoạn 1. Đoạn 2.",
        text_hash="",
        metadata={},
        audio_path=audio_path,
    )

    srt_path = audio_path.with_suffix(".srt")
    write_srt([
        Cue(1, 0.5, 3.2, "Đoạn 1 chính xác"),
        Cue(2, 3.5, 7.8, "Đoạn 2 chính xác"),
    ], srt_path)

    renderer = FFmpegRenderer({})
    cfg = VideoConfig(subtitle_enabled=True, subtitle_srt=False)
    segments = [(item, 0.0, 8.0)]

    events = renderer._srt_events(manager, [item], cfg, segments)
    assert len(events) == 2
    assert events[0] == (0.5, 3.2, "Đoạn 1 chính xác")
    assert events[1] == (3.5, 7.8, "Đoạn 2 chính xác")
