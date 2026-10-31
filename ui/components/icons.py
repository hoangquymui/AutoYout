"""Vector icon set and brand mark rendered from inline SVG.

Icons are 24x24 Lucide-style strokes converted to :class:`QIcon` at 2x device
pixel ratio so they stay crisp on HiDPI displays without shipping a font.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

from ui import theme

try:  # pragma: no cover - QtSvg ships with PySide6
    from PySide6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover
    QSvgRenderer = None  # type: ignore[assignment]

ICONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"

DEFAULT_COLOR = "#E8EEF8"

_PATHS = {
    "waveform": (
        '<path d="M3 10v4"/><path d="M7 6v12"/><path d="M11 3v18"/>'
        '<path d="M15 8v8"/><path d="M19 10.5v3"/>'
    ),
    "mic": (
        '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>'
        '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>'
    ),
    "film": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/>'
        '<path d="M7 3v18"/><path d="M17 3v18"/><path d="M3 12h18"/>'
        '<path d="M3 7.5h4"/><path d="M17 7.5h4"/><path d="M3 16.5h4"/><path d="M17 16.5h4"/>'
    ),
    "upload": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>'
    ),
    "zap": '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z"/>',
    "pause": (
        '<rect x="6" y="4" width="4" height="16" rx="1"/>'
        '<rect x="14" y="4" width="4" height="16" rx="1"/>'
    ),
    "play": '<path d="M7 4.5v15l13-7.5Z"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "folder": (
        '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9'
        'A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>'
    ),
    "folder-open": (
        '<path d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 '
        '1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 '
        '0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>'
    ),
    "external-link": (
        '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    ),
    "refresh": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M8 16H3v5"/>'
    ),
    "rotate-ccw": (
        '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'
    ),
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    "alert": (
        '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86'
        'a2 2 0 0 0-3.42 0Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "edit": (
        '<path d="M12 20h9"/>'
        '<path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "key": (
        '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 8.8-8.8"/>'
        '<path d="m15 8 3 3"/><path d="m18 5 3 3"/>'
    ),
    "log-in": (
        '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/>'
        '<path d="m10 17 5-5-5-5"/><path d="M15 12H3"/>'
    ),
    "youtube": (
        '<rect x="2" y="5" width="20" height="14" rx="4"/>'
        '<path d="m10 9 5 3-5 3V9Z"/>'
    ),
    "music": (
        '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'
    ),
    "image": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
        '<path d="m21 15-4.5-4.5L9 18"/>'
    ),
    "file": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>'
        '<path d="M14 2v6h6"/>'
    ),
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/>'
        '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 '
        '15.3 15.3 0 0 1 4-10Z"/>'
    ),
    "list": (
        '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/>'
        '<path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>'
    ),
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/>'
        '<path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>'
    ),
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"/>',
}

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)


def svg_markup(name: str, color: str = DEFAULT_COLOR, stroke: float = 2.0) -> str:
    body = _PATHS.get(name)
    if body is None:
        return ""
    return _SVG.format(color=color, stroke=stroke, body=body)


def _render(name: str, color: str, size: int, ratio: float, stroke: float) -> QPixmap:
    markup = svg_markup(name, color, stroke)
    pixel = max(8, int(round(size * ratio)))
    pixmap = QPixmap(pixel, pixel)
    pixmap.fill(Qt.GlobalColor.transparent)
    if not markup or QSvgRenderer is None:
        return pixmap
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, pixel, pixel))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


@lru_cache(maxsize=256)
def _pixmap_cached(name: str, color: str, size: int, ratio: float, stroke: float) -> QPixmap:
    return _render(name, color, size, ratio, stroke)


def pixmap(name: str, color: str | None = None, size: int = 18,
           ratio: float = 2.0, stroke: float = 2.0) -> QPixmap:
    resolved = color or theme.icon_color("text")
    return _pixmap_cached(name, resolved, size, ratio, stroke)


@lru_cache(maxsize=256)
def _icon_cached(name: str, color: str, disabled: str, size: int) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    result.addPixmap(pixmap(name, disabled, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return result


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    resolved = color or theme.icon_color("text")
    return _icon_cached(name, resolved, theme.icon_color("disabled"), size)


def clear_cache() -> None:
    """Drop rendered pixmaps so the next theme paints with its own colours."""
    _pixmap_cached.cache_clear()
    _icon_cached.cache_clear()


def set_icon(widget: QAbstractButton, name: str, role: str = "muted", size: int = 15) -> None:
    """Assign an icon and remember its role so a theme switch can repaint it."""
    widget.setProperty("ayIconName", name)
    widget.setProperty("ayIconRole", role)
    widget.setProperty("ayIconSize", int(size))
    widget.setIcon(icon(name, theme.icon_color(role), size))
    widget.setIconSize(QSize(size, size))


def set_pixmap(widget: QLabel, name: str, role: str = "accent", size: int = 17) -> None:
    widget.setProperty("ayIconName", name)
    widget.setProperty("ayIconRole", role)
    widget.setProperty("ayIconSize", int(size))
    widget.setPixmap(pixmap(name, theme.icon_color(role), size))


def refresh_icons(root: QWidget) -> None:
    """Re-render every icon tagged through :func:`set_icon`/:func:`set_pixmap`."""
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        name = widget.property("ayIconName")
        if not name:
            continue
        role = str(widget.property("ayIconRole") or "muted")
        size = int(widget.property("ayIconSize") or 15)
        color = theme.icon_color(role)
        if isinstance(widget, QAbstractButton):
            widget.setIcon(icon(name, color, size))
            widget.setIconSize(QSize(size, size))
        elif isinstance(widget, QLabel):
            widget.setPixmap(pixmap(name, color, size))


@lru_cache(maxsize=8)
def brand_pixmap(size: int = 38) -> QPixmap:
    """Rounded gradient mark with a white audio waveform."""
    ratio = 2.0
    pixel = int(round(size * ratio))
    pixmap = QPixmap(pixel, pixel)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(ratio, ratio)

    rect = QRectF(0.5, 0.5, size - 1.0, size - 1.0)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#8B7CFF"))
    gradient.setColorAt(0.55, QColor("#6D8BFF"))
    gradient.setColorAt(1.0, QColor("#38BDF8"))
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.29, size * 0.29)
    painter.fillPath(path, gradient)

    pen = QPen(QColor("#FFFFFF"), max(1.6, size * 0.055))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    bars = ((0.30, 0.38, 0.62), (0.41, 0.26, 0.74), (0.52, 0.16, 0.84),
            (0.63, 0.29, 0.71), (0.74, 0.40, 0.60))
    for x, top, bottom in bars:
        painter.drawLine(
            QPointF(x * size, top * size), QPointF(x * size, bottom * size)
        )
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap
