"""Renderer additions: random video pool, SRT export and language fonts."""
from __future__ import annotations

import re
from pathlib import Path

from core.ffmpeg_renderer import FFmpegRenderer, VideoConfig
from core.project_manager import ProjectManager
from core.settings import load_settings


def _renderer() -> FFmpegRenderer:
    return FFmpegRenderer(load_settings())


def _manager(tmp_path: Path) -> ProjectManager:
    inputs = tmp_path / "input"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "tap_01.txt").write_text("Nội dung phần một.", encoding="utf-8")
    (inputs / "bai_zh.txt").write_text("你好，世界。", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()
    return manager


def _cfg(**overrides) -> VideoConfig:
    raw = {"visual": "video_pool"}
    raw.update(overrides)
    return VideoConfig.from_settings({"video": raw})


def test_video_pool_config_roundtrip() -> None:
    cfg = _cfg(video_pool_dir="D:/clips", video_pool_count=3, write_srt=True)
    assert cfg.visual == "video_pool"
    assert cfg.video_pool_dir == "D:/clips"
    assert cfg.video_pool_count == 3
    assert cfg.write_srt is True
    assert cfg.errors() == []


def test_video_pool_picks_random_subset(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    pool = manager.assets_dir / "video_pool"
    pool.mkdir(parents=True, exist_ok=True)
    for index in range(1, 5):
        (pool / f"clip_{index}.mp4").write_bytes(b"0")
    (pool / "note.txt").write_text("không phải video", encoding="utf-8")

    renderer = _renderer()
    chosen = renderer._video_pool_sources(manager, _cfg(video_pool_count=2))
    assert len(chosen) == 2
    assert all(path.suffix == ".mp4" for path in chosen)

    everything = renderer._video_pool_sources(manager, _cfg(video_pool_count=0))
    assert len(everything) == 4

    capped = renderer._video_pool_sources(manager, _cfg(video_pool_count=99))
    assert len(capped) == 4


def test_video_pool_empty_folder(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    (manager.assets_dir / "video_pool").mkdir(parents=True, exist_ok=True)
    assert _renderer()._video_pool_sources(manager, _cfg()) == []


def test_srt_timestamp_format() -> None:
    assert FFmpegRenderer._srt_timestamp(0) == "00:00:00,000"
    assert FFmpegRenderer._srt_timestamp(3661.5) == "01:01:01,500"
    assert FFmpegRenderer._srt_timestamp(59.9999) == "00:01:00,000"


def test_write_srt_blocks(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    item = manager.get_item("tap_01")
    segments = [(item, 0.0, 4.2)]
    target = tmp_path / "out" / "video.srt"
    assert _renderer()._write_srt(segments, 4.2, target) is True
    content = target.read_text(encoding="utf-8")
    assert re.match(
        r"1\n00:00:00,000 --> 00:00:04,200\nNội dung phần một\.\n", content)


def test_write_srt_empty_returns_false(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    item = manager.get_item("tap_01")
    item.text = ""
    item.metadata = {}
    target = tmp_path / "empty.srt"
    assert _renderer()._write_srt([(item, 0.0, 1.0)], 1.0, target) is False
    assert not target.exists()


def test_segments_language_and_cjk_font(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    renderer = _renderer()
    zh_item = manager.get_item("bai_zh")
    vi_item = manager.get_item("tap_01")
    assert renderer._segments_language([(zh_item, 0.0, 2.0)]) == "zh"
    assert renderer._segments_language([(vi_item, 0.0, 2.0)]) == "vi"
    assert renderer._segments_language([]) == "vi"

    temp = tmp_path / "ass"
    temp.mkdir()
    assert renderer._write_subtitles(
        [(zh_item, 0.0, 4.0)], 4.0, temp, _cfg(), language="zh") is True
    ass = (temp / "subs.ass").read_text(encoding="utf-8")
    assert "Microsoft YaHei" in ass
    assert "你好" in ass
