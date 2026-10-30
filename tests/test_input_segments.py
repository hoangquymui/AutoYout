"""Transcript helpers: cue plumbing and URL validation (no Whisper, no network)."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import core.input_pipeline as pipeline
from core.subtitles import Cue, cues_from_dicts, cues_to_dicts


def test_transcribe_segments_signature() -> None:
    assert callable(pipeline.transcribe_segments)
    params = inspect.signature(pipeline.transcribe_segments).parameters
    for name in ("media_path", "language", "model_size", "progress_cb", "cancel_cb"):
        assert name in params


def test_transcribe_segments_missing_media_raises(tmp_path: Path) -> None:
    with pytest.raises(pipeline.InputError):
        pipeline.transcribe_segments(tmp_path / "missing.mp4", "vi")


def test_cues_roundtrip_dicts() -> None:
    cues = cues_from_dicts([
        {"start": 0.0, "end": 1.5, "text": "Xin chào"},
        {"start": 1.5, "end": 3.0, "text": "Tạm biệt"},
    ])
    assert [cue.text for cue in cues] == ["Xin chào", "Tạm biệt"]
    assert cues[1].index == 2
    assert cues_to_dicts(cues)[0] == {"start": 0.0, "end": 1.5, "text": "Xin chào"}


def test_cue_from_dict_swaps_reversed_range() -> None:
    cue = Cue.from_dict({"start": 5, "end": 2, "text": "x"})
    assert cue.start == 2 and cue.end == 5


def test_get_youtube_transcript_rejects_bad_url(tmp_path: Path) -> None:
    with pytest.raises(pipeline.InputError):
        pipeline.get_youtube_transcript("not-a-url", tmp_path)


def test_fetch_youtube_info_rejects_bad_url() -> None:
    with pytest.raises(pipeline.InputError):
        pipeline.fetch_youtube_info("ftp://example.com")
