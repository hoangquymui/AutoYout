"""SRT/VTT parsing, cue timeline and atomic SRT writing."""
from __future__ import annotations

from pathlib import Path

from core.subtitles import (
    Cue,
    cues_to_text,
    format_timestamp,
    parse_srt,
    parse_timestamp,
    parse_vtt,
    text_to_cues,
    write_srt,
)


def test_format_and_parse_timestamp() -> None:
    assert format_timestamp(3723.456) == "01:02:03,456"
    assert format_timestamp(0) == "00:00:00,000"
    assert parse_timestamp("01:02:03,456") == 3723.456
    assert parse_timestamp("01:02:03.456") == 3723.456
    assert parse_timestamp("00:01.500") == 1.5
    assert parse_timestamp("12.5") == 12.5


def test_parse_srt_bom_crlf_multiline_and_reversed_end() -> None:
    raw = (
        "\ufeff1\r\n00:00:01,000 --> 00:00:04,500\r\nXin chào\r\ntiếp\r\n\r\n"
        "2\r\n00:00:05,000 --> 00:00:03,000\r\nĐảo ngược\r\n"
    )
    cues = parse_srt(raw)
    assert len(cues) == 2
    assert cues[0].text == "Xin chào tiếp"
    assert cues[0].start == 1.0 and cues[0].end == 4.5
    assert cues[1].start == 3.0 and cues[1].end == 5.0


def test_parse_srt_skips_garbage_blocks() -> None:
    raw = (
        "rác không phải cue\n\n"
        "1\n00:00:01,000 --> 00:00:02,000\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n<i>Nội dung</i>"
    )
    cues = parse_srt(raw)
    assert [cue.text for cue in cues] == ["Nội dung"]


def test_parse_vtt_header_settings_short_stamp_and_duplicates() -> None:
    raw = """WEBVTT
Kind: captions

NOTE điều hướng

00:01.000 --> 00:03.000 align:start position:0%
<v Nam>Xin chào</v>

00:03.000 --> 00:05.000
Xin chào

00:05.000 --> 00:07.000
Tạm biệt
"""
    cues = parse_vtt(raw)
    assert [cue.text for cue in cues] == ["Xin chào", "Tạm biệt"]
    assert cues[0].start == 1.0 and cues[0].end == 5.0
    assert cues[1].start == 5.0 and cues[1].end == 7.0


def test_write_srt_roundtrip(tmp_path: Path) -> None:
    cues = [Cue(1, 0.0, 1.5, "Một"), Cue(2, 2.25, 4.0, "Hai")]
    target = write_srt(cues, tmp_path / "a.srt")
    parsed = parse_srt(target.read_text(encoding="utf-8"))
    assert [cue.text for cue in parsed] == ["Một", "Hai"]
    assert parsed[0].start == 0.0
    assert abs(parsed[0].end - 1.5) < 0.002
    assert abs(parsed[1].start - 2.25) < 0.002


def test_cues_to_text_keeps_lines() -> None:
    cues = [Cue(1, 0, 1, "Một"), Cue(2, 1, 2, "Hai")]
    assert cues_to_text(cues) == "Một\nHai"


def test_text_to_cues_timing_is_monotonic() -> None:
    cues = text_to_cues("Câu một. Câu hai.\n\nĐoạn hai tiếp theo.", total_seconds=12)
    assert len(cues) >= 2
    assert all(cue.start >= 0 and cue.end > cue.start for cue in cues)
    assert all(
        later.start >= earlier.start
        for earlier, later in zip(cues, cues[1:])
    )
    assert abs(cues[-1].end - 12.0) < 0.5


def test_text_to_cues_estimates_without_duration() -> None:
    cues = text_to_cues("Một câu ngắn.")
    assert len(cues) == 1
    assert cues[0].start == 0.0
    assert cues[0].end > 0
