"""Video frame template: normalised positions for the video overlay layer.

Qt-free so both the editor UI and the FFmpeg renderer share the same model.
Every position/size is a fraction of the video frame (0..1), independent of
the output resolution.

Layout
------
* ``subtitle_x``/``subtitle_y`` — centre anchor of burned subtitles.
* ``wave_x``/``wave_y``/``wave_w``/``wave_h`` — top-left anchor and size of the
  audio waveform.
* ``images`` — PNG/JPEG overlays (logo, sticker...) with top-left anchor,
  relative width (height follows the image aspect ratio) and opacity.
* ``preview`` — background image used only by the editor canvas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_WAVE_COLOR = "#4FC3F7"
MIN_IMAGE_WIDTH = 0.02
MIN_WAVE_WIDTH = 0.1
MIN_WAVE_HEIGHT = 0.02

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_color(value: Any, default: str = DEFAULT_WAVE_COLOR) -> str:
    """Return ``#RRGGBB`` for a valid hex colour, else ``default``."""
    match = _HEX_RE.match(str(value or "").strip())
    if not match:
        return default
    return "#" + match.group(1).upper()


def color_ffmpeg(value: Any, default: str = DEFAULT_WAVE_COLOR) -> str:
    """``showwaves``/``overlay`` colour syntax (``0xRRGGBB``)."""
    return "0x" + normalize_color(value, default)[1:]


@dataclass
class FrameImage:
    """One PNG/JPEG overlay drawn on top of the video."""

    path: str = ""
    x: float = 0.05
    y: float = 0.05
    width: float = 0.18
    opacity: float = 1.0
    rotation: float = 0.0
    flip_h: bool = False
    flip_v: bool = False

    @property
    def name(self) -> str:
        return Path(self.path).name if self.path else "Ảnh lớp phủ"

    def clamp(self) -> "FrameImage":
        self.width = round(_clamp(_as_float(self.width, 0.18),
                                  MIN_IMAGE_WIDTH, 1.0), 4)
        self.x = round(_clamp(_as_float(self.x, 0.05), 0.0,
                              1.0 - self.width), 4)
        self.y = round(_clamp(_as_float(self.y, 0.05), 0.0, 1.0), 4)
        self.opacity = round(_clamp(_as_float(self.opacity, 1.0), 0.05, 1.0), 3)
        self.rotation = round((_as_float(self.rotation, 0.0) % 360), 1)
        if self.rotation > 180:
            self.rotation -= 360
        self.flip_h = bool(self.flip_h)
        self.flip_v = bool(self.flip_v)
        return self

    def to_dict(self) -> Dict[str, Any]:
        self.clamp()
        d: Dict[str, Any] = {
            "path": self.path,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "opacity": self.opacity,
        }
        if self.rotation != 0.0:
            d["rotation"] = self.rotation
        if self.flip_h:
            d["flip_h"] = True
        if self.flip_v:
            d["flip_v"] = True
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], base: Optional[str] = "") -> "FrameImage":
        raw = raw if isinstance(raw, dict) else {}
        path = str(raw.get("path") or "")
        if path and base and not Path(path).is_absolute():
            candidate = Path(base) / path
            if candidate.exists():
                path = str(candidate)
        return cls(
            path=path,
            x=_as_float(raw.get("x"), 0.05),
            y=_as_float(raw.get("y"), 0.05),
            width=_as_float(raw.get("width"), 0.18),
            opacity=_as_float(raw.get("opacity"), 1.0),
            rotation=_as_float(raw.get("rotation"), 0.0),
            flip_h=bool(raw.get("flip_h")),
            flip_v=bool(raw.get("flip_v")),
        ).clamp()


@dataclass
class FrameLayout:
    """Normalised frame template shared by the editor and the renderer."""

    enabled: bool = False
    subtitle_x: float = 0.5
    subtitle_y: float = 0.9
    subtitle_scale: float = 1.0
    waveform: bool = False
    wave_x: float = 0.06
    wave_y: float = 0.74
    wave_w: float = 0.88
    wave_h: float = 0.08
    wave_color: str = DEFAULT_WAVE_COLOR
    preview: str = ""
    images: List[FrameImage] = field(default_factory=list)

    # ── validation ───────────────────────────────────────────────────────────
    def clamp(self) -> "FrameLayout":
        self.subtitle_x = round(_clamp(_as_float(self.subtitle_x, 0.5), 0.0, 1.0), 4)
        self.subtitle_y = round(_clamp(_as_float(self.subtitle_y, 0.9), 0.02, 0.98), 4)
        self.subtitle_scale = round(
            _clamp(_as_float(self.subtitle_scale, 1.0), 0.5, 2.0), 3)
        self.wave_w = round(_clamp(_as_float(self.wave_w, 0.88),
                                   MIN_WAVE_WIDTH, 1.0), 4)
        self.wave_h = round(_clamp(_as_float(self.wave_h, 0.08),
                                   MIN_WAVE_HEIGHT, 1.0), 4)
        self.wave_x = round(_clamp(_as_float(self.wave_x, 0.06), 0.0,
                                   1.0 - self.wave_w), 4)
        self.wave_y = round(_clamp(_as_float(self.wave_y, 0.74), 0.0,
                                   1.0 - self.wave_h), 4)
        self.wave_color = normalize_color(self.wave_color)
        for image in self.images:
            image.clamp()
        return self

    # ── serialisation ────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, raw: Dict[str, Any],
                  base: Optional[str] = "") -> "FrameLayout":
        raw = raw if isinstance(raw, dict) else {}
        images = raw.get("images")
        if not isinstance(images, (list, tuple)):
            images = []
        return cls(
            enabled=bool(raw.get("enabled", False)),
            subtitle_x=_as_float(raw.get("subtitle_x"), 0.5),
            subtitle_y=_as_float(raw.get("subtitle_y"), 0.9),
            subtitle_scale=_as_float(raw.get("subtitle_scale"), 1.0),
            waveform=bool(raw.get("waveform", False)),
            wave_x=_as_float(raw.get("wave_x"), 0.06),
            wave_y=_as_float(raw.get("wave_y"), 0.74),
            wave_w=_as_float(raw.get("wave_w"), 0.88),
            wave_h=_as_float(raw.get("wave_h"), 0.08),
            wave_color=raw.get("wave_color") or DEFAULT_WAVE_COLOR,
            preview=str(raw.get("preview") or ""),
            images=[FrameImage.from_dict(image, base) for image in images],
        ).clamp()

    @classmethod
    def from_settings(cls, settings: Dict[str, Any],
                      base: Optional[str] = "") -> "FrameLayout":
        raw = (settings or {}).get("frame") or {}
        return cls.from_dict(raw, base)

    def to_dict(self) -> Dict[str, Any]:
        self.clamp()
        return {
            "enabled": self.enabled,
            "subtitle_x": self.subtitle_x,
            "subtitle_y": self.subtitle_y,
            "subtitle_scale": self.subtitle_scale,
            "waveform": self.waveform,
            "wave_x": self.wave_x,
            "wave_y": self.wave_y,
            "wave_w": self.wave_w,
            "wave_h": self.wave_h,
            "wave_color": self.wave_color,
            "preview": self.preview,
            "images": [image.to_dict() for image in self.images],
        }

    # ── derived geometry ─────────────────────────────────────────────────────
    @property
    def wave_active(self) -> bool:
        return bool(self.enabled and self.waveform)

    def subtitle_pos(self, width: int, height: int) -> Tuple[int, int]:
        """Pixel centre anchor for ASS ``\\pos``."""
        return (int(round(self.subtitle_x * max(1, width))),
                int(round(self.subtitle_y * max(1, height))))

    def wave_rect(self, width: int, height: int) -> Tuple[int, int, int, int]:
        """Pixel ``(x, y, w, h)`` box for the waveform overlay."""
        w = max(2, int(round(self.wave_w * max(1, width))))
        h = max(2, int(round(self.wave_h * max(1, height))))
        x = max(0, min(max(0, width - w), int(round(self.wave_x * max(1, width)))))
        y = max(0, min(max(0, height - h), int(round(self.wave_y * max(1, height)))))
        return x, y, w, h

    def image_rect(self, image: FrameImage, width: int, height: int,
                   image_size: Optional[Tuple[int, int]] = None
                   ) -> Tuple[int, int, int, int]:
        """Pixel ``(x, y, w, h)`` box for an overlay image.

        ``h`` is derived from the source aspect ratio when known, else the
        box is square so the renderer and the editor agree.
        """
        w = max(2, int(round(image.width * max(1, width))))
        if image_size and image_size[0] > 0:
            h = max(2, int(round(w * image_size[1] / image_size[0])))
        else:
            h = w
        x = max(0, min(max(0, width - w), int(round(image.x * max(1, width)))))
        y = max(0, min(max(0, height - h), int(round(image.y * max(1, height)))))
        return x, y, w, h
