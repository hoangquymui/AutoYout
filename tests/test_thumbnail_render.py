"""Thumbnail rendering: text fitting and composited output."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from core.thumbnail import (  # noqa: E402
    _segment_line_highlights,
    autosize_font,
    default_font_path,
    is_cjk,
    render_thumbnail,
    translate_thumbnail_keywords,
    wrap_title_lines,
)


def _background(tmp_path: Path, size=(320, 180)) -> Path:
    path = tmp_path / "bg.png"
    Image.new("RGB", size, (34, 51, 68)).save(path)
    return path


def _draw() -> ImageDraw.ImageDraw:
    return ImageDraw.Draw(Image.new("RGB", (16, 16)))


def test_wrap_line_widths_fit_the_box() -> None:
    draw = _draw()
    font = autosize_font("Tiêu đề rất dài để kiểm tra",
                         600, 300, 90, 40, 1.05)
    lines = wrap_title_lines(
        draw, "Một tiêu đề dài bí ẩn khiến người xem phải bấm vào", font, 600)
    assert len(lines) > 1
    assert all(draw.textlength(line, font=font) <= 600 for line in lines)


def test_autosize_font_stays_within_bounds() -> None:
    font = autosize_font("Ngắn gọn", 600, 300, 96, 60, 1.05)
    assert 60 <= font.size <= 96

    tiny = autosize_font("Tiêu đề cực kỳ dài " * 30, 200, 80, 96, 40, 1.05)
    assert 40 <= tiny.size <= 96


def test_render_writes_scaled_image(tmp_path: Path) -> None:
    out = tmp_path / "thumb.jpg"
    result = render_thumbnail(
        _background(tmp_path), "Bí mật bị che giấu",
        [("BÍ MẬT", "red")], "drama_left", {},
        out, size=(640, 360))

    assert result == out and out.is_file() and out.stat().st_size > 1000
    with Image.open(out) as image:
        assert image.size == (640, 360)
        assert image.format == "JPEG"


@pytest.mark.parametrize("highlights", [[], [("không có trong tiêu đề", "green")]])
def test_render_tolerates_empty_or_unmatched_highlights(
    tmp_path: Path, highlights
) -> None:
    out = tmp_path / "thumb.png"
    render_thumbnail(
        _background(tmp_path), "Tiêu đề thử nghiệm", highlights,
        "top_center", {"green": "#3BFF35", "red": "#FF3B30"},
        out, size=(320, 180))

    assert out.is_file()
    with Image.open(out) as image:
        assert image.size == (320, 180)


def test_is_cjk_detection() -> None:
    assert not is_cjk("Tiêu đề tiếng Việt có dấu")
    assert not is_cjk("English YouTube Title 123")
    assert is_cjk("校门口玩具的惊人真相")
    assert is_cjk("学校前の伝説の玩具の正体")
    assert is_cjk("학교 앞 장난감의 진실")


def test_default_font_path_picks_cjk_font() -> None:
    zh_font = default_font_path("校门口玩具的惊人真相", "zh")
    assert zh_font is not None
    # On Windows, should pick YaHei or similar CJK font
    assert any(c in zh_font.lower() for c in ("msyh", "simhei", "yugoth"))

    vi_font = default_font_path("Sự thật về những món đồ chơi", "vi")
    assert vi_font is not None
    assert any(c in vi_font.lower() for c in ("arial", "segoe", "anton", "montserrat"))


def test_wrap_title_lines_cjk() -> None:
    draw = _draw()
    font = autosize_font("校门口玩具的惊人真相", 300, 200, 60, 40, 1.05, language="zh")
    lines = wrap_title_lines(draw, "校门口玩具的惊人真相", font, 300)
    assert len(lines) >= 2
    assert "".join(lines) == "校门口玩具的惊人真相"


def test_multi_word_segment_line_highlights() -> None:
    palette = {"text": "#FFFFFF", "green": "#3BFF35", "red": "#FF3B30"}
    line = "SỰ THẬT VỀ NHỮNG MÓN ĐỒ CHƠI CỔNG TRƯỜNG"
    highlights = [("Sự thật", "green"), ("đồ chơi", "red")]
    segments = _segment_line_highlights(line, highlights, "#FFFFFF", palette)

    # Must segment into [('SỰ THẬT', '#3BFF35'), (' VỀ NHỮNG MÓN ', '#FFFFFF'), ('ĐỒ CHƠI', '#FF3B30'), (' CỔNG TRƯỜNG', '#FFFFFF')]
    assert segments[0] == ("SỰ THẬT", "#3BFF35")
    assert segments[1] == (" VỀ NHỮNG MÓN ", "#FFFFFF")
    assert segments[2] == ("ĐỒ CHƠI", "#FF3B30")
    assert segments[3] == (" CỔNG TRƯỜNG", "#FFFFFF")


def test_render_thumbnail_cjk_and_highlights(tmp_path: Path) -> None:
    out = tmp_path / "thumb_zh.jpg"
    result = render_thumbnail(
        _background(tmp_path), "校门口玩具的惊人真相",
        [("玩具", "red"), ("真相", "green")], "drama_left",
        {"green": "#3BFF35", "red": "#FF3B30"},
        out, size=(640, 360), language="zh")

    assert result == out and out.is_file() and out.stat().st_size > 1000
    with Image.open(out) as image:
        assert image.size == (640, 360)


def test_translate_thumbnail_keywords_mock() -> None:
    def fake_gen(prompt: str, key: str, model: str) -> str:
        return '{"zh": {"Sự thật": "真相", "đồ chơi": "玩具"}}'

    res = translate_thumbnail_keywords(
        [("Sự thật", "green"), ("đồ chơi", "red")],
        ["zh"], "test-key", source_language="vi",
        generate_fn=fake_gen,
    )
    assert "zh" in res
    assert res["zh"] == [("真相", "green"), ("玩具", "red")]
