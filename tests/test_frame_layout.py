"""Frame template model: normalised coordinates, clamping and colour helpers."""
from __future__ import annotations

from core.frame_layout import (
    DEFAULT_WAVE_COLOR,
    FrameImage,
    FrameLayout,
    color_ffmpeg,
    normalize_color,
)


def test_defaults_roundtrip() -> None:
    layout = FrameLayout().clamp()
    data = layout.to_dict()
    again = FrameLayout.from_dict(data)
    assert again.to_dict() == data
    assert again.enabled is False
    assert again.subtitle_x == 0.5
    assert again.subtitle_y == 0.9
    assert again.wave_color == DEFAULT_WAVE_COLOR


def test_from_settings_reads_nested_frame() -> None:
    settings = {"frame": {"enabled": True, "subtitle_y": 0.72,
                          "waveform": True, "wave_color": "4fc3f7",
                          "images": [{"path": "logo.png", "x": 0.7, "y": 0.1,
                                      "width": 0.2, "opacity": 0.5}]}}
    layout = FrameLayout.from_settings(settings)
    assert layout.enabled is True
    assert layout.subtitle_y == 0.72
    assert layout.wave_active is True
    assert layout.wave_color == "#4FC3F7"
    assert len(layout.images) == 1
    assert layout.images[0].opacity == 0.5


def test_from_settings_handles_junk() -> None:
    layout = FrameLayout.from_settings({"frame": "nope"})
    assert layout == FrameLayout().clamp()
    assert FrameLayout.from_settings({}).enabled is False


def test_clamp_keeps_boxes_inside_frame() -> None:
    layout = FrameLayout(
        subtitle_y=4.0,
        subtitle_scale=9.0,
        wave_x=0.9,
        wave_y=0.9,
        wave_w=0.5,
        wave_h=0.5,
        images=[FrameImage(path="a.png", x=0.95, width=0.5)],
    ).clamp()
    assert layout.subtitle_y == 0.98
    assert layout.subtitle_scale == 2.0
    assert layout.wave_x == 0.5
    assert layout.wave_y == 0.5
    assert layout.images[0].x == 0.5


def test_image_clamping_and_serialisation() -> None:
    image = FrameImage.from_dict({"path": "logo.png", "x": -2,
                                  "y": 2, "width": 0.01, "opacity": 3})
    assert image.width == 0.02
    assert image.x == 0.0
    assert image.y == 1.0
    assert image.opacity == 1.0
    assert image.to_dict()["path"] == "logo.png"


def test_image_rect_uses_aspect_ratio_and_stays_inside() -> None:
    layout = FrameLayout()
    image = FrameImage(path="a.png", x=0.9, y=0.5, width=0.2)
    x, y, w, h = layout.image_rect(image, 1920, 1080, (400, 200))
    assert w == 384
    assert h == 192
    assert x == 1536
    assert y <= 1080 - h


def test_wave_rect_and_subtitle_pos_scale() -> None:
    layout = FrameLayout(wave_x=0.0, wave_y=0.9, wave_w=1.0, wave_h=0.1,
                         subtitle_x=0.25, subtitle_y=0.75)
    assert layout.wave_rect(1920, 1080) == (0, 972, 1920, 108)
    assert layout.subtitle_pos(1920, 1080) == (480, 810)


def test_colour_helpers() -> None:
    assert normalize_color("#ff00aa") == "#FF00AA"
    assert normalize_color("00FF00") == "#00FF00"
    assert normalize_color("blue") == DEFAULT_WAVE_COLOR
    assert normalize_color("") == DEFAULT_WAVE_COLOR
    assert color_ffmpeg("#12Ab34") == "0x12AB34"


def test_image_visible_locked_and_aspect_ratio() -> None:
    layout = FrameLayout(aspect_ratio="9:16",
                         images=[FrameImage(path="a.png", visible=False, locked=True)])
    data = layout.to_dict()
    assert data["aspect_ratio"] == "9:16"
    assert data["images"][0]["visible"] is False
    assert data["images"][0]["locked"] is True
    again = FrameLayout.from_dict(data)
    assert again.aspect_ratio == "9:16"
    assert again.images[0].visible is False
    assert again.images[0].locked is True


def test_subtitle_textbox_and_custom_font() -> None:
    layout = FrameLayout(
        subtitle_x=0.5,
        subtitle_y=0.8,
        subtitle_w=0.6,
        subtitle_h=0.2,
        subtitle_font="Roboto",
        subtitle_font_size=48,
    )
    x, y, w, h = layout.subtitle_rect(1000, 500)
    assert w == 600
    assert h == 100
    assert x == 200
    assert y == 350
    data = layout.to_dict()
    assert data["subtitle_w"] == 0.6
    assert data["subtitle_h"] == 0.2
    assert data["subtitle_font"] == "Roboto"
    assert data["subtitle_font_size"] == 48

    restored = FrameLayout.from_dict(data)
    assert restored.subtitle_w == 0.6
    assert restored.subtitle_h == 0.2
    assert restored.subtitle_font == "Roboto"
    assert restored.subtitle_font_size == 48
