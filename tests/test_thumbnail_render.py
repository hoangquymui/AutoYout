"""Thumbnail rendering: text fitting and composited output."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from core.thumbnail import (  # noqa: E402
    autosize_font,
    render_thumbnail,
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
