"""Video Frame tab: CapCut-inspired drag-and-drop template editor.

Layout
------
* Left Column: Assets / Layers & Preset Stickers library.
* Center Column: 16:9 interactive canvas player with playback & view controls.
* Right Column: Inspector panel (Transform: Scale, Pos X/Y, Rotate, Alignment, Flip, Opacity).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.frame_layout import FrameImage, FrameLayout
from ui import theme
from ui.components.icons import set_icon
from ui.components.page import PageHeader, icon_button, paint_status, wrap_scroll

_IMAGE_FILTER = "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả (*)"
_SAMPLE_TEXT = "Phụ đề hiển thị ở đây"
_HANDLE = 11.0


def _qcolor(value: str, alpha: int = 255) -> QColor:
    color = QColor(value)
    if not color.isValid():
        color = QColor("#4FC3F7")
    color.setAlpha(alpha)
    return color


class FrameCanvas(QWidget):
    """Interactive preview canvas. Reads/writes a shared :class:`FrameLayout`."""

    layout_changed = Signal()
    selection_changed = Signal(str, int)

    def __init__(self, layout: FrameLayout, parent=None) -> None:
        super().__init__(parent)
        self._layout = layout
        self._selected_kind = ""
        self._selected_index = -1
        self._drag_mode = ""
        self._press = QPointF()
        self._origin: Dict[str, float] = {}
        self._pixmaps: Dict[str, QPixmap] = {}
        self._preview: Optional[QPixmap] = None
        self._phase = 0.0

        # View options
        self.show_grid = True
        self.show_safe_area = True
        self.aspect_ratio_mode = "16:9"

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(360, 240)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        self._timer = QTimer(self)
        self._timer.setInterval(55)
        self._timer.timeout.connect(self._tick)
        self._load_assets()

    # ── model binding ────────────────────────────────────────────────────────
    @property
    def layout(self) -> FrameLayout:
        return self._layout

    def set_layout(self, layout: FrameLayout) -> None:
        self._layout = layout
        self._selected_kind = ""
        self._selected_index = -1
        self._load_assets()
        self.update()

    def selection(self) -> Tuple[str, int]:
        return self._selected_kind, self._selected_index

    def set_selection(self, kind: str, index: int = -1) -> None:
        if kind == "image" and not (0 <= index < len(self._layout.images)):
            kind, index = "", -1
        self._selected_kind = kind
        self._selected_index = index if kind == "image" else -1
        self.selection_changed.emit(self._selected_kind, self._selected_index)
        self.update()

    def refresh_from_model(self) -> None:
        self.update()

    def reload_assets(self) -> None:
        self._load_assets()
        self.update()

    def delete_selected(self) -> bool:
        if self._selected_kind != "image":
            return False
        if not (0 <= self._selected_index < len(self._layout.images)):
            return False
        self._layout.images.pop(self._selected_index)
        self.set_selection("", -1)
        self.layout_changed.emit()
        self.update()
        return True

    def _load_assets(self) -> None:
        self._pixmaps.clear()
        for image in self._layout.images:
            if Path(image.path).is_file():
                self._pixmaps[image.path] = QPixmap(image.path)
        self._preview = None
        if self._layout.preview and Path(self._layout.preview).is_file():
            pixmap = QPixmap(self._layout.preview)
            if not pixmap.isNull():
                self._preview = pixmap

    # ── geometry helpers ─────────────────────────────────────────────────────
    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        if self.aspect_ratio_mode == "9:16":
            return int(round(max(1, width - 24) * 16 / 9)) + 24
        elif self.aspect_ratio_mode == "1:1":
            return int(round(max(1, width - 24))) + 24
        return int(round(max(1, width - 24) * 9 / 16)) + 24

    def _frame_rect(self) -> QRectF:
        rect = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        if rect.width() <= 0 or rect.height() <= 0:
            return QRectF()
        if self.aspect_ratio_mode == "9:16":
            ratio = 9.0 / 16.0
        elif self.aspect_ratio_mode == "1:1":
            ratio = 1.0
        else:
            ratio = 16.0 / 9.0

        width = min(rect.width(), rect.height() * ratio)
        height = width / ratio
        return QRectF(
            rect.left() + (rect.width() - width) / 2,
            rect.top() + (rect.height() - height) / 2,
            width,
            height,
        )

    def _subtitle_font(self) -> QFont:
        frame = self._frame_rect()
        base = max(10.0, 44.0 * frame.height() / 1080.0)
        font = QFont(self.font())
        font.setPixelSize(max(9, int(round(base * self._layout.subtitle_scale * 1.6))))
        font.setBold(True)
        return font

    def _subtitle_box(self) -> QRectF:
        frame = self._frame_rect()
        if frame.isEmpty():
            return QRectF()
        metrics = QFontMetricsF(self._subtitle_font())
        width = min(frame.width() * 0.86,
                    metrics.horizontalAdvance(_SAMPLE_TEXT) + 30)
        height = metrics.height() + 18
        cx = frame.left() + self._layout.subtitle_x * frame.width()
        cy = frame.top() + self._layout.subtitle_y * frame.height()
        return QRectF(cx - width / 2, cy - height / 2, width, height)

    def _wave_box(self) -> QRectF:
        frame = self._frame_rect()
        return QRectF(
            frame.left() + self._layout.wave_x * frame.width(),
            frame.top() + self._layout.wave_y * frame.height(),
            self._layout.wave_w * frame.width(),
            self._layout.wave_h * frame.height(),
        )

    def _image_box(self, index: int) -> QRectF:
        frame = self._frame_rect()
        if not (0 <= index < len(self._layout.images)):
            return QRectF()
        image = self._layout.images[index]
        width = max(12.0, image.width * frame.width())
        pixmap = self._pixmaps.get(image.path)
        if pixmap is not None and not pixmap.isNull() and pixmap.width() > 0:
            height = width * pixmap.height() / pixmap.width()
        else:
            height = width
        height = min(height, frame.height())
        x = frame.left() + image.x * frame.width()
        y = frame.top() + image.y * frame.height()
        y = min(y, frame.top() + frame.height() - height)
        return QRectF(x, y, width, height)

    def _resize_handle(self) -> QRectF:
        kind, index = self.selection()
        if kind == "subtitle":
            box = self._subtitle_box()
            return QRectF(box.center().x() - _HANDLE / 2,
                          box.bottom() - _HANDLE / 2, _HANDLE, _HANDLE)
        if kind == "wave":
            box = self._wave_box()
        elif kind == "image":
            box = self._image_box(index)
        else:
            return QRectF()
        return QRectF(box.right() - _HANDLE / 2, box.bottom() - _HANDLE / 2,
                      _HANDLE, _HANDLE)

    # ── alignments & transforms ──────────────────────────────────────────────
    def align_selected(self, mode: str) -> None:
        kind, index = self.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            image = self._layout.images[index]
            box = self._image_box(index)
            frame = self._frame_rect()
            norm_w = image.width
            norm_h = (box.height() / frame.height()) if frame.height() > 0 else norm_w
            if mode == "left":
                image.x = 0.04
            elif mode == "center_x":
                image.x = max(0.0, round((1.0 - norm_w) / 2.0, 4))
            elif mode == "right":
                image.x = max(0.0, round(1.0 - norm_w - 0.04, 4))
            elif mode == "top":
                image.y = 0.04
            elif mode == "center_y":
                image.y = max(0.0, round((1.0 - norm_h) / 2.0, 4))
            elif mode == "bottom":
                image.y = max(0.0, round(1.0 - norm_h - 0.04, 4))
            image.clamp()
        elif kind == "subtitle":
            if mode == "left":
                self._layout.subtitle_x = 0.25
            elif mode == "center_x":
                self._layout.subtitle_x = 0.5
            elif mode == "right":
                self._layout.subtitle_x = 0.75
            elif mode == "top":
                self._layout.subtitle_y = 0.12
            elif mode == "center_y":
                self._layout.subtitle_y = 0.5
            elif mode == "bottom":
                self._layout.subtitle_y = 0.88
            self._layout.clamp()
        elif kind == "wave":
            if mode == "left":
                self._layout.wave_x = 0.05
            elif mode == "center_x":
                self._layout.wave_x = max(0.0, round((1.0 - self._layout.wave_w) / 2.0, 4))
            elif mode == "right":
                self._layout.wave_x = max(0.0, round(1.0 - self._layout.wave_w - 0.05, 4))
            elif mode == "top":
                self._layout.wave_y = 0.05
            elif mode == "center_y":
                self._layout.wave_y = max(0.0, round((1.0 - self._layout.wave_h) / 2.0, 4))
            elif mode == "bottom":
                self._layout.wave_y = max(0.0, round(1.0 - self._layout.wave_h - 0.05, 4))
            self._layout.clamp()

        self.layout_changed.emit()
        self.update()

    def rotate_selected(self, deg: float) -> None:
        kind, index = self.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].rotation = round(deg, 1)
            self._layout.images[index].clamp()
            self.layout_changed.emit()
            self.update()

    def flip_selected(self, horizontal: bool = True) -> None:
        kind, index = self.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            image = self._layout.images[index]
            if horizontal:
                image.flip_h = not getattr(image, "flip_h", False)
            else:
                image.flip_v = not getattr(image, "flip_v", False)
            self.layout_changed.emit()
            self.update()

    # ── painting ─────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        frame = self._frame_rect()
        if frame.isEmpty():
            return
        self._paint_backdrop(painter)
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(frame, 12, 12)
        painter.setClipPath(path)
        self._paint_preview(painter, frame)
        if self.show_grid:
            self._paint_grid(painter, frame)
        if self.show_safe_area:
            self._paint_safe_area(painter, frame)
        self._paint_wave(painter)
        for index in range(len(self._layout.images)):
            self._paint_image(painter, index)
        self._paint_subtitle(painter)
        painter.restore()
        self._paint_frame_border(painter, frame)
        self._paint_badge(painter, frame)

    def _paint_backdrop(self, painter: QPainter) -> None:
        rect = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.SURFACE_ALT))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 14, 14)

    def _paint_preview(self, painter: QPainter, frame: QRectF) -> None:
        if self._preview is not None and not self._preview.isNull():
            scaled = self._preview.scaled(
                frame.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            source = QRectF(
                max(0.0, (scaled.width() - frame.width()) / 2),
                max(0.0, (scaled.height() - frame.height()) / 2),
                frame.width(), frame.height())
            painter.drawPixmap(frame, scaled, source)
            shade = QColor("#000000")
            shade.setAlpha(60)
            painter.fillRect(frame, shade)
            return
        gradient = QLinearGradient(frame.topLeft(), frame.bottomRight())
        gradient.setColorAt(0.0, QColor(theme.SURFACE_RAISED))
        gradient.setColorAt(0.55, QColor(theme.SURFACE))
        gradient.setColorAt(1.0, QColor(theme.BG))
        painter.fillRect(frame, gradient)
        glow = QRadialGradient(frame.center(), frame.width() * 0.6)
        accent = QColor(theme.ACCENT)
        accent.setAlpha(70)
        glow.setColorAt(0.0, accent)
        transparent = QColor(theme.ACCENT)
        transparent.setAlpha(0)
        glow.setColorAt(1.0, transparent)
        painter.fillRect(frame, glow)
        veil = QRadialGradient(frame.center(), frame.width() * 0.75)
        veil.setColorAt(0.0, QColor(0, 0, 0, 0))
        veil.setColorAt(1.0, QColor(0, 0, 0, 70))
        painter.fillRect(frame, veil)

    def _paint_grid(self, painter: QPainter, frame: QRectF) -> None:
        pen = QPen(QColor(theme.BORDER_STRONG))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DotLine)
        color = pen.color()
        color.setAlpha(90)
        pen.setColor(color)
        painter.setPen(pen)
        for step in range(1, 3):
            x = frame.left() + frame.width() * step / 3
            y = frame.top() + frame.height() * step / 3
            painter.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
            painter.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))

    def _paint_safe_area(self, painter: QPainter, frame: QRectF) -> None:
        inset = frame.width() * 0.05
        safe = frame.adjusted(inset, inset, -inset, -inset)
        pen = QPen(_qcolor(theme.BLUE, 80))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(safe, 8, 8)

    def _paint_wave(self, painter: QPainter) -> None:
        if not self._layout.waveform:
            return
        box = self._wave_box()
        color = _qcolor(self._layout.wave_color)
        bg = QColor(color)
        bg.setAlpha(35)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(box, 8, 8)
        bars = max(8, int(box.width() / 14))
        bar_w = box.width() / bars
        mid = box.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(bars):
            phase = self._phase + i * 0.35
            norm = (math.sin(phase) + 1.0) * 0.5
            amp = (0.15 + 0.85 * norm) * (box.height() * 0.42)
            top = mid - amp
            h = amp * 2.0
            x = box.left() + i * bar_w + bar_w * 0.2
            w = max(2.0, bar_w * 0.6)
            bar = QRectF(x, top, w, h)
            bar_color = QColor(color)
            bar_color.setAlpha(int(140 + 115 * norm))
            painter.setBrush(bar_color)
            painter.drawRoundedRect(bar, 1.5, 1.5)
        if self._selected_kind == "wave":
            self._paint_selection(painter, box, "Sóng âm")
        else:
            painter.setPen(QPen(color, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 8, 8)
            self._paint_caption(painter, box, "Sóng âm")

    def _paint_image(self, painter: QPainter, index: int) -> None:
        image = self._layout.images[index]
        box = self._image_box(index)
        if box.isEmpty():
            return
        pixmap = self._pixmaps.get(image.path)

        painter.save()
        painter.setOpacity(max(0.05, image.opacity))
        center = box.center()
        painter.translate(center)
        rot = getattr(image, "rotation", 0.0)
        if rot:
            painter.rotate(rot)
        sx = -1.0 if getattr(image, "flip_h", False) else 1.0
        sy = -1.0 if getattr(image, "flip_v", False) else 1.0
        if sx != 1.0 or sy != 1.0:
            painter.scale(sx, sy)
        painter.translate(-center)

        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(box, pixmap, QRectF(pixmap.rect()))
        else:
            painter.setPen(QPen(_qcolor(theme.WARNING, 180), 1.4,
                                Qt.PenStyle.DashLine))
            painter.setBrush(QColor(0, 0, 0, 60))
            painter.drawRoundedRect(box, 4, 4)
            self._paint_caption(painter, box, "Thiếu ảnh")
        painter.restore()

        if self._selected_kind == "image" and self._selected_index == index:
            self._paint_selection(painter, box, image.name)

    def _paint_subtitle(self, painter: QPainter) -> None:
        box = self._subtitle_box()
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawRoundedRect(box, 7, 7)
        painter.setFont(self._subtitle_font())
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, _SAMPLE_TEXT)
        painter.restore()
        if self._selected_kind == "subtitle":
            self._paint_selection(painter, box, "Phụ đề")
        else:
            self._paint_caption(painter, box, "Phụ đề")
            pen = QPen(_qcolor(theme.ACCENT, 120), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 7, 7)

    def _paint_selection(self, painter: QPainter, box: QRectF, label: str) -> None:
        pulse = 0.55 + 0.45 * abs(math.sin(self._phase * 0.8))
        glow = QColor(theme.ACCENT)
        glow.setAlpha(int(90 * pulse))
        painter.setPen(QPen(glow, 8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 7, 7)
        painter.setPen(QPen(_qcolor(theme.ACCENT), 1.8))
        painter.drawRoundedRect(box, 7, 7)
        handle = self._resize_handle()
        painter.setPen(QPen(_qcolor(theme.PRIMARY_TEXT), 1.2))
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(handle, 3, 3)
        self._paint_caption(painter, box, label)

    def _paint_caption(self, painter: QPainter, box: QRectF, text: str) -> None:
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text) + 12
        chip = QRectF(box.left(), max(0.0, box.top() - 19), width, 17)
        painter.setPen(Qt.PenStyle.NoPen)
        chip_bg = QColor(theme.SURFACE)
        chip_bg.setAlpha(210)
        painter.setBrush(chip_bg)
        painter.drawRoundedRect(chip, 4, 4)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_frame_border(self, painter: QPainter, frame: QRectF) -> None:
        pen = QPen(QColor(theme.BORDER_STRONG), 1.6)
        if self._layout.enabled:
            gradient = QLinearGradient(frame.topLeft(), frame.bottomRight())
            gradient.setColorAt(0.0, QColor(theme.ACCENT))
            gradient.setColorAt(0.5, QColor(theme.BLUE))
            gradient.setColorAt(1.0, QColor(theme.ACCENT))
            pen = QPen(QBrush(gradient), 1.8)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, 12, 12)

    def _paint_badge(self, painter: QPainter, frame: QRectF) -> None:
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        text = f"{self.aspect_ratio_mode} · Player"
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text) + 16
        chip = QRectF(frame.right() - width - 10, frame.top() + 10, width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 110))
        painter.drawRoundedRect(chip, 6, 6)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
        if not self._layout.enabled:
            chip = QRectF(frame.left() + 10, frame.top() + 10, 132, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 110))
            painter.drawRoundedRect(chip, 6, 6)
            painter.setPen(QColor(theme.WARNING))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter,
                             "Đang tắt khung mẫu")

    # ── interaction ──────────────────────────────────────────────────────────
    def _hit_test(self, pos: QPointF) -> Tuple[str, int]:
        if self._subtitle_box().contains(pos):
            return "subtitle", -1
        for index in range(len(self._layout.images) - 1, -1, -1):
            if self._image_box(index).contains(pos):
                return "image", index
        if self._layout.waveform and self._wave_box().contains(pos):
            return "wave", -1
        return "", -1

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        frame = self._frame_rect()
        pos = event.position()
        if not frame.contains(pos):
            self.set_selection("", -1)
            return
        self.setFocus()
        kind, index = self._hit_test(pos)
        if kind and self._resize_handle().contains(pos):
            self.set_selection(kind, index)
            self._drag_mode = "resize"
        elif kind:
            self.set_selection(kind, index)
            self._drag_mode = "move"
        else:
            self.set_selection("", -1)
            return
        self._press = pos
        layout = self._layout
        if kind == "image":
            image = layout.images[index]
            self._origin = {"x": image.x, "y": image.y, "width": image.width,
                            "opacity": image.opacity}
        elif kind == "wave":
            self._origin = {"x": layout.wave_x, "y": layout.wave_y,
                            "w": layout.wave_w, "h": layout.wave_h}
        else:
            self._origin = {"x": layout.subtitle_x, "y": layout.subtitle_y,
                            "scale": layout.subtitle_scale}
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        pos = event.position()
        if not self._drag_mode:
            frame = self._frame_rect()
            if frame.contains(pos) and self._resize_handle().contains(pos):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif frame.contains(pos) and self._hit_test(pos)[0]:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        frame = self._frame_rect()
        if frame.isEmpty():
            return
        dx = (pos.x() - self._press.x()) / frame.width()
        dy = (pos.y() - self._press.y()) / frame.height()
        kind, index = self.selection()
        layout = self._layout
        if kind == "subtitle":
            if self._drag_mode == "move":
                layout.subtitle_x = self._snap_center(self._origin["x"] + dx)
                layout.subtitle_y = self._snap_center(self._origin["y"] + dy)
            else:
                layout.subtitle_scale = min(
                    2.0, max(0.5, self._origin["scale"] + dy * 2.5))
            layout.clamp()
        elif kind == "wave":
            if self._drag_mode == "move":
                layout.wave_x = self._origin["x"] + dx
                layout.wave_y = self._origin["y"] + dy
            else:
                layout.wave_w = max(0.1, min(1.0 - layout.wave_x,
                                             self._origin["w"] + dx))
                layout.wave_h = max(0.02, min(1.0 - layout.wave_y,
                                              self._origin["h"] + dy))
            layout.clamp()
        elif kind == "image" and 0 <= index < len(layout.images):
            image = layout.images[index]
            if self._drag_mode == "move":
                image.x = self._origin["x"] + dx
                image.y = self._origin["y"] + dy
            else:
                image.width = max(0.02, min(1.0 - image.x,
                                            self._origin["width"] + dx))
            image.clamp()
        self.layout_changed.emit()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._drag_mode = ""

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        kind, _index = self._hit_test(event.position())
        if kind:
            self.set_selection(kind, _index)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.delete_selected():
                return
        super().keyPressEvent(event)

    @staticmethod
    def _snap_center(value: float) -> float:
        for target in (0.5, 0.1, 0.9):
            if abs(value - target) < 0.012:
                return target
        return value

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.07) % (math.pi * 2)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(720, 440)


class FrameTab(QWidget):
    """CapCut-style Video Frame editor: 3 columns (Library, Player, Inspector)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = FrameLayout()
        self._block = False

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        scroller = wrap_scroll(content)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.addWidget(scroller)

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        header = PageHeader(
            "Video Frame (Trình chỉnh sửa CapCut)",
            "Chỉnh sửa bố cục lớp phủ, sóng nhạc, phụ đề và sticker mẫu trên video.",
        )
        self._enabled = QCheckBox("Bật khung mẫu")
        self._enabled.setToolTip(
            "Áp dụng bố cục này khi tạo video (tab Video vẫn quyết định có hiện phụ đề).")
        header.add_action(self._enabled)
        root.addWidget(header)

        # 3 Columns Layout: Left Library | Center Player | Right Inspector
        cols = QHBoxLayout()
        cols.setSpacing(12)
        self._cols = cols

        self._canvas = FrameCanvas(self._layout)

        # 1. Left Library & Layers Panel
        self._left_panel = self._build_left_library_panel()
        cols.addWidget(self._left_panel, 0)

        # 2. Center Player Panel
        self._center_panel = self._build_center_player_panel()
        cols.addWidget(self._center_panel, 1)

        # 3. Right Inspector Panel
        self._right_panel = self._build_right_inspector_panel()
        cols.addWidget(self._right_panel, 0)

        root.addLayout(cols, 1)

        self._status = QLabel("Khung mẫu đang tắt — bật “Bật khung mẫu” để áp dụng.")
        self._status.setObjectName("MutedLabel")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._enabled.toggled.connect(self._on_enabled)
        self._canvas.layout_changed.connect(self._on_layout_changed)
        self._canvas.selection_changed.connect(self._on_selection)
        self._sync_controls()

    # ── 1. Left Library Panel (Layers & Stickers) ───────────────────────────
    def _build_left_library_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(260)
        panel.setMaximumWidth(300)
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        tabs = QTabWidget()
        vbox.addWidget(tabs, 1)

        # Tab 1: Layers
        layers_tab = QWidget()
        l_box = QVBoxLayout(layers_tab)
        l_box.setContentsMargins(8, 8, 8, 8)
        l_box.setSpacing(8)

        layer_actions = QHBoxLayout()
        layer_actions.setSpacing(6)
        self._add_button = QPushButton("Thêm PNG")
        set_icon(self._add_button, "plus", "accent", 13)
        self._add_button.clicked.connect(self._add_image)
        layer_actions.addWidget(self._add_button, 1)

        self._remove_button = icon_button("trash", "Xoá layer", role="danger", size=14, side=30)
        self._remove_button.clicked.connect(self._remove_image)
        layer_actions.addWidget(self._remove_button)
        l_box.addLayout(layer_actions)

        self._image_list = QListWidget()
        self._image_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._image_list.currentRowChanged.connect(self._on_list_row)
        l_box.addWidget(self._image_list, 1)

        bg_group = QGroupBox("Nền xem trước")
        bg_form = QVBoxLayout(bg_group)
        bg_form.setSpacing(6)
        bg_row = QHBoxLayout()
        bg_row.setSpacing(6)
        self._preview_button = QPushButton("Chọn nền")
        set_icon(self._preview_button, "image", "accent", 13)
        self._preview_button.clicked.connect(self._pick_preview)
        bg_row.addWidget(self._preview_button, 1)
        self._preview_clear = icon_button("x", "Bỏ ảnh nền", size=13, side=28)
        self._preview_clear.clicked.connect(self._clear_preview)
        bg_row.addWidget(self._preview_clear)
        bg_form.addLayout(bg_row)
        l_box.addWidget(bg_group)

        tabs.addTab(layers_tab, "Danh sách lớp")

        # Tab 2: Preset Stickers
        stickers_tab = QWidget()
        s_box = QVBoxLayout(stickers_tab)
        s_box.setContentsMargins(8, 8, 8, 8)
        s_box.setSpacing(8)

        s_hint = QLabel("Bấm để chèn sticker mẫu nhanh:")
        s_hint.setObjectName("MutedLabel")
        s_box.addWidget(s_hint)

        stickers_grid = QGridLayout()
        stickers_grid.setSpacing(6)

        presets = [
            ("🔴 Subscribe", "assets/stickers/subscribe.png", 0, 0),
            ("👍 Like & Share", "assets/stickers/like.png", 0, 1),
            ("🔔 Chuông", "assets/stickers/bell.png", 1, 0),
            ("🔥 Hot", "assets/stickers/hot.png", 1, 1),
            ("🐱 Webcam Cat", "assets/stickers/cat_avatar.png", 2, 0),
            ("⚠️ Chú ý", "assets/stickers/warning.png", 2, 1),
        ]
        for title, path, r, c in presets:
            btn = QPushButton(title)
            btn.setStyleSheet("text-align: left; padding: 6px 8px; font-weight: 600;")
            btn.clicked.connect(lambda _c=False, p=path: self._insert_preset(p))
            stickers_grid.addWidget(btn, r, c)

        s_box.addLayout(stickers_grid)
        s_box.addStretch(1)
        tabs.addTab(stickers_tab, "Kho Sticker")

        return panel

    def _insert_preset(self, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            return
        index = len(self._layout.images)
        offset = 0.03 * (index % 5)
        self._layout.images.append(FrameImage(
            path=str(p.resolve()), x=0.35 + offset, y=0.35 + offset, width=0.22))
        self._layout.enabled = True
        self._enabled.setChecked(True)
        self._canvas.reload_assets()
        self._rebuild_list(index)
        self._canvas.set_selection("image", index)
        self._canvas.update()

    # ── 2. Center Player Panel (Canvas & Controls) ──────────────────────────
    def _build_center_player_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        lbl = QLabel("Màn hình xem trước (Player)")
        lbl.setObjectName("FieldLabel")
        top_bar.addWidget(lbl)
        top_bar.addStretch(1)

        self._ratio_combo = QComboBox()
        self._ratio_combo.addItem("16:9 (Ngang)", "16:9")
        self._ratio_combo.addItem("9:16 (Dọc Shorts)", "9:16")
        self._ratio_combo.addItem("1:1 (Vuông)", "1:1")
        self._ratio_combo.currentIndexChanged.connect(self._on_ratio_changed)
        top_bar.addWidget(self._ratio_combo)

        vbox.addLayout(top_bar)
        vbox.addWidget(self._canvas, 1)

        # Player Bottom Controls (CapCut playback bar)
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        self._play_btn = icon_button("play", "Bật/Tắt mô phỏng sóng nhạc", size=15, side=32)
        self._play_btn.clicked.connect(self._toggle_playback)
        bottom_bar.addWidget(self._play_btn)

        timecode = QLabel("00:00:02 / 00:00:10")
        timecode.setObjectName("MutedLabel")
        bottom_bar.addWidget(timecode)

        bottom_bar.addStretch(1)

        self._btn_grid = icon_button("grid", "Bật/Tắt đường lưới", size=14, side=30)
        self._btn_grid.clicked.connect(self._toggle_grid)
        bottom_bar.addWidget(self._btn_grid)

        self._btn_safe = icon_button("maximize-2", "Bật/Tắt vùng an toàn", size=14, side=30)
        self._btn_safe.clicked.connect(self._toggle_safe)
        bottom_bar.addWidget(self._btn_safe)

        self._reset_btn = QPushButton("Đặt lại")
        set_icon(self._reset_btn, "rotate-ccw", "muted", 13)
        self._reset_btn.setToolTip("Khôi phục toàn bộ khung mẫu về mặc định.")
        self._reset_btn.clicked.connect(self._reset)
        bottom_bar.addWidget(self._reset_btn)

        vbox.addLayout(bottom_bar)
        return panel

    def _toggle_playback(self) -> None:
        if self._canvas._timer.isActive():
            self._canvas._timer.stop()
            set_icon(self._play_btn, "play", "accent", 15)
        else:
            self._canvas._timer.start()
            set_icon(self._play_btn, "pause", "accent", 15)

    def _toggle_grid(self) -> None:
        self._canvas.show_grid = not self._canvas.show_grid
        self._canvas.update()

    def _toggle_safe(self) -> None:
        self._canvas.show_safe_area = not self._canvas.show_safe_area
        self._canvas.update()

    def _on_ratio_changed(self) -> None:
        mode = str(self._ratio_combo.currentData() or "16:9")
        self._canvas.aspect_ratio_mode = mode
        self._canvas.updateGeometry()
        self._canvas.update()

    # ── 3. Right Inspector Panel (Transform / Opacity / Custom) ──────────────
    def _build_right_inspector_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(340)
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(10)

        # Selected Target Title
        self._inspector_title = QLabel("Chọn một đối tượng để chỉnh sửa")
        self._inspector_title.setObjectName("FieldLabel")
        self._inspector_title.setStyleSheet("font-weight: 700; color: #4FC3F7;")
        vbox.addWidget(self._inspector_title)

        # Group 1: Transform (Kích thước, Vị trí, Xoay, Căn lề, Lật)
        trans_box = QGroupBox("Biến đổi (Transform)")
        t_layout = QVBoxLayout(trans_box)
        t_layout.setSpacing(6)

        # Scale / Kích thước
        self._width_label = QLabel("Kích thước (Scale) 18%")
        self._width_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._width_label)
        self._width = QSlider(Qt.Orientation.Horizontal)
        self._width.setRange(5, 100)
        self._width.setValue(18)
        self._width.valueChanged.connect(self._on_width)
        t_layout.addWidget(self._width)

        # Position X
        self._pos_x_label = QLabel("Vị trí X: 5%")
        self._pos_x_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._pos_x_label)
        x_row = QHBoxLayout()
        x_row.setSpacing(6)
        self._pos_x = QSlider(Qt.Orientation.Horizontal)
        self._pos_x.setRange(0, 100)
        self._pos_x.setValue(5)
        self._pos_x.valueChanged.connect(self._on_pos_x)
        x_row.addWidget(self._pos_x, 1)
        btn_center_x = icon_button("minimize-2", "Căn giữa X (50%)", size=13, side=28)
        btn_center_x.clicked.connect(lambda: self._canvas.align_selected("center_x"))
        x_row.addWidget(btn_center_x)
        t_layout.addLayout(x_row)

        # Position Y
        self._pos_y_label = QLabel("Vị trí Y: 5%")
        self._pos_y_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._pos_y_label)
        y_row = QHBoxLayout()
        y_row.setSpacing(6)
        self._pos_y = QSlider(Qt.Orientation.Horizontal)
        self._pos_y.setRange(0, 100)
        self._pos_y.setValue(5)
        self._pos_y.valueChanged.connect(self._on_pos_y)
        y_row.addWidget(self._pos_y, 1)
        btn_center_y = icon_button("minimize-2", "Căn giữa Y (50%)", size=13, side=28)
        btn_center_y.clicked.connect(lambda: self._canvas.align_selected("center_y"))
        y_row.addWidget(btn_center_y)
        t_layout.addLayout(y_row)

        # Rotate (Góc xoay)
        self._rotate_label = QLabel("Góc xoay (Rotate): 0°")
        self._rotate_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._rotate_label)
        rot_row = QHBoxLayout()
        rot_row.setSpacing(6)
        self._rotate = QSlider(Qt.Orientation.Horizontal)
        self._rotate.setRange(-180, 180)
        self._rotate.setValue(0)
        self._rotate.valueChanged.connect(self._on_rotate)
        rot_row.addWidget(self._rotate, 1)
        btn_reset_rot = icon_button("rotate-ccw", "Về 0°", size=12, side=28)
        btn_reset_rot.clicked.connect(lambda: self._canvas.rotate_selected(0.0))
        rot_row.addWidget(btn_reset_rot)
        t_layout.addLayout(rot_row)

        # Fast Alignment (6 buttons)
        align_label = QLabel("Căn lề nhanh (Alignment)")
        align_label.setObjectName("MutedLabel")
        t_layout.addWidget(align_label)
        align_grid = QGridLayout()
        align_grid.setSpacing(4)
        alignments = [
            ("Trái", "left", 0, 0),
            ("Giữa X", "center_x", 0, 1),
            ("Phải", "right", 0, 2),
            ("Trên", "top", 1, 0),
            ("Giữa Y", "center_y", 1, 1),
            ("Dưới", "bottom", 1, 2),
        ]
        for name, mode, r, c in alignments:
            btn = QPushButton(name)
            btn.setStyleSheet("font-size: 11px; padding: 4px;")
            btn.clicked.connect(lambda _c=False, m=mode: self._canvas.align_selected(m))
            align_grid.addWidget(btn, r, c)
        t_layout.addLayout(align_grid)

        # Flip Horizontal / Vertical
        flip_row = QHBoxLayout()
        flip_row.setSpacing(6)
        self._flip_h_btn = QPushButton("Lật ngang (Flip H)")
        self._flip_h_btn.setStyleSheet("font-size: 11px; padding: 4px;")
        self._flip_h_btn.clicked.connect(lambda: self._canvas.flip_selected(True))
        flip_row.addWidget(self._flip_h_btn)

        self._flip_v_btn = QPushButton("Lật dọc (Flip V)")
        self._flip_v_btn.setStyleSheet("font-size: 11px; padding: 4px;")
        self._flip_v_btn.clicked.connect(lambda: self._canvas.flip_selected(False))
        flip_row.addWidget(self._flip_v_btn)
        t_layout.addLayout(flip_row)

        vbox.addWidget(trans_box)

        # Group 2: Opacity & Blend
        blend_box = QGroupBox("Độ đậm & Trong suốt (Opacity)")
        b_layout = QVBoxLayout(blend_box)
        b_layout.setSpacing(6)
        self._opacity_label = QLabel("Độ đậm 100%")
        self._opacity_label.setObjectName("MutedLabel")
        b_layout.addWidget(self._opacity_label)
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(10, 100)
        self._opacity.setValue(100)
        self._opacity.valueChanged.connect(self._on_opacity)
        b_layout.addWidget(self._opacity)
        vbox.addWidget(blend_box)

        # Group 3: Custom Properties (Subtitle Font / Wave Color)
        custom_box = QGroupBox("Thuộc tính riêng (Custom)")
        c_layout = QVBoxLayout(custom_box)
        c_layout.setSpacing(8)

        self._scale_label = QLabel("Cỡ chữ phụ đề: 100%")
        self._scale_label.setObjectName("MutedLabel")
        c_layout.addWidget(self._scale_label)
        self._scale = QSlider(Qt.Orientation.Horizontal)
        self._scale.setRange(50, 200)
        self._scale.setValue(100)
        self._scale.valueChanged.connect(self._on_scale)
        c_layout.addWidget(self._scale)

        self._wave = QCheckBox("Hiện sóng âm thanh")
        self._wave.toggled.connect(self._on_wave_toggled)
        c_layout.addWidget(self._wave)

        self._color_button = QPushButton("Màu sóng âm")
        self._color_button.clicked.connect(self._pick_color)
        c_layout.addWidget(self._color_button)

        vbox.addWidget(custom_box)
        vbox.addStretch(1)
        return panel

    # ── settings API ─────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._layout = FrameLayout.from_settings(settings)
        self._canvas.set_layout(self._layout)
        self._sync_controls()
        self._rebuild_list()
        self._paint_color_button()
        paint_status(self._status, "muted")
        self._status.setText(
            "Khung mẫu đang bật." if self._layout.enabled
            else "Khung mẫu đang tắt — bật “Bật khung mẫu” để áp dụng.")

    def apply_to(self, settings: Dict[str, Any]) -> None:
        settings["frame"] = self._layout.to_dict()

    def apply_theme(self) -> None:
        self._paint_color_button()
        self._canvas.update()
        paint_status(self._status, "muted")

    # ── state sync ───────────────────────────────────────────────────────────
    def _sync_controls(self) -> None:
        self._block = True
        self._enabled.setChecked(self._layout.enabled)
        self._wave.setChecked(self._layout.waveform)
        self._scale.setValue(int(round(self._layout.subtitle_scale * 100)))
        self._scale_label.setText(
            f"Cỡ chữ phụ đề: {int(round(self._layout.subtitle_scale * 100))}%")
        self._preview_clear.setEnabled(bool(self._layout.preview))
        self._color_button.setEnabled(self._layout.waveform)
        self._block = False
        self._paint_color_button()
        self._on_selection(*self._canvas.selection())

    def _rebuild_list(self, select: int = -1) -> None:
        self._block = True
        self._image_list.clear()

        # Item 0: Subtitle
        item_sub = QListWidgetItem("📝 Phụ đề (Subtitle)")
        self._image_list.addItem(item_sub)

        # Item 1: Waveform
        item_wave = QListWidgetItem("🌊 Sóng âm thanh (Waveform)")
        self._image_list.addItem(item_wave)

        # Image items
        for idx, image in enumerate(self._layout.images):
            item = QListWidgetItem(f"🖼️ {image.name}")
            item.setToolTip(image.path)
            self._image_list.addItem(item)

        target_row = select + 2 if select >= 0 else -1
        if 0 <= target_row < self._image_list.count():
            self._image_list.setCurrentRow(target_row)
        self._block = False
        self._update_inspector()

    def _on_list_row(self, row: int) -> None:
        if self._block or row < 0:
            return
        if row == 0:
            self._canvas.set_selection("subtitle", -1)
        elif row == 1:
            self._canvas.set_selection("wave", -1)
        else:
            self._canvas.set_selection("image", row - 2)

    def _on_selection(self, kind: str, index: int) -> None:
        self._block = True
        if kind == "subtitle":
            self._image_list.setCurrentRow(0)
        elif kind == "wave":
            self._image_list.setCurrentRow(1)
        elif kind == "image" and 0 <= index < len(self._layout.images):
            self._image_list.setCurrentRow(index + 2)
        else:
            self._image_list.setCurrentRow(-1)
        self._block = False
        self._update_inspector()

    def _update_inspector(self) -> None:
        kind, index = self._canvas.selection()
        valid_img = (kind == "image" and 0 <= index < len(self._layout.images))
        self._remove_button.setEnabled(valid_img)
        self._flip_h_btn.setEnabled(valid_img)
        self._flip_v_btn.setEnabled(valid_img)
        self._rotate.setEnabled(valid_img)

        self._block = True
        if valid_img:
            image = self._layout.images[index]
            self._inspector_title.setText(f"Đang chỉnh sửa: 🖼️ {image.name}")
            self._width.setEnabled(True)
            self._width.setValue(int(round(image.width * 100)))
            self._width_label.setText(f"Kích thước (Scale) {int(round(image.width * 100))}%")

            self._pos_x.setEnabled(True)
            self._pos_x.setValue(int(round(image.x * 100)))
            self._pos_x_label.setText(f"Vị trí X: {int(round(image.x * 100))}%")

            self._pos_y.setEnabled(True)
            self._pos_y.setValue(int(round(image.y * 100)))
            self._pos_y_label.setText(f"Vị trí Y: {int(round(image.y * 100))}%")

            self._rotate.setValue(int(round(getattr(image, "rotation", 0.0))))
            self._rotate_label.setText(f"Góc xoay (Rotate): {int(round(getattr(image, 'rotation', 0.0)))}°")

            self._opacity.setEnabled(True)
            self._opacity.setValue(int(round(image.opacity * 100)))
            self._opacity_label.setText(f"Độ đậm {int(round(image.opacity * 100))}%")
            self._scale.setEnabled(False)
        elif kind == "subtitle":
            self._inspector_title.setText("Đang chỉnh sửa: 📝 Phụ đề (Subtitle)")
            self._width.setEnabled(False)
            self._pos_x.setEnabled(True)
            self._pos_x.setValue(int(round(self._layout.subtitle_x * 100)))
            self._pos_x_label.setText(f"Vị trí X: {int(round(self._layout.subtitle_x * 100))}%")
            self._pos_y.setEnabled(True)
            self._pos_y.setValue(int(round(self._layout.subtitle_y * 100)))
            self._pos_y_label.setText(f"Vị trí Y: {int(round(self._layout.subtitle_y * 100))}%")
            self._rotate.setValue(0)
            self._opacity.setEnabled(False)
            self._scale.setEnabled(True)
            self._scale.setValue(int(round(self._layout.subtitle_scale * 100)))
            self._scale_label.setText(f"Cỡ chữ phụ đề: {int(round(self._layout.subtitle_scale * 100))}%")
        elif kind == "wave":
            self._inspector_title.setText("Đang chỉnh sửa: 🌊 Sóng âm thanh")
            self._width.setEnabled(True)
            self._width.setValue(int(round(self._layout.wave_w * 100)))
            self._width_label.setText(f"Độ rộng sóng {int(round(self._layout.wave_w * 100))}%")
            self._pos_x.setEnabled(True)
            self._pos_x.setValue(int(round(self._layout.wave_x * 100)))
            self._pos_x_label.setText(f"Vị trí X: {int(round(self._layout.wave_x * 100))}%")
            self._pos_y.setEnabled(True)
            self._pos_y.setValue(int(round(self._layout.wave_y * 100)))
            self._pos_y_label.setText(f"Vị trí Y: {int(round(self._layout.wave_y * 100))}%")
            self._rotate.setValue(0)
            self._opacity.setEnabled(False)
            self._scale.setEnabled(False)
        else:
            self._inspector_title.setText("Chọn một đối tượng để chỉnh sửa")
            self._width.setEnabled(False)
            self._pos_x.setEnabled(False)
            self._pos_y.setEnabled(False)
            self._rotate.setEnabled(False)
            self._opacity.setEnabled(False)
            self._scale.setEnabled(False)
        self._block = False

    def _on_layout_changed(self) -> None:
        if not self._layout.enabled:
            self._enabled.setChecked(True)
        self._update_inspector()

    # ── actions ──────────────────────────────────────────────────────────────
    def _on_enabled(self, checked: bool) -> None:
        if self._block:
            return
        self._layout.enabled = bool(checked)
        paint_status(self._status, "success" if checked else "muted")
        self._status.setText(
            "Khung mẫu đang bật — vị trí phụ đề, sóng âm và PNG sẽ được áp dụng."
            if checked else
            "Khung mẫu đang tắt — tạo video sẽ bỏ qua bố cục này.")
        self._canvas.update()

    def _on_wave_toggled(self, checked: bool) -> None:
        if self._block:
            return
        self._layout.waveform = bool(checked)
        self._color_button.setEnabled(checked)
        if checked and not self._layout.enabled:
            self._enabled.setChecked(True)
        self._canvas.update()

    def _on_scale(self, value: int) -> None:
        if self._block:
            return
        self._layout.subtitle_scale = value / 100.0
        self._scale_label.setText(f"Cỡ chữ phụ đề: {value}%")
        self._canvas.update()

    def _on_opacity(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        if kind != "image" or not (0 <= index < len(self._layout.images)):
            return
        self._layout.images[index].opacity = value / 100.0
        self._opacity_label.setText(f"Độ đậm {value}%")
        self._canvas.update()

    def _on_width(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].width = value / 100.0
            self._layout.images[index].clamp()
            self._width_label.setText(f"Kích thước (Scale) {value}%")
            self._canvas.update()
        elif kind == "wave":
            self._layout.wave_w = value / 100.0
            self._layout.clamp()
            self._width_label.setText(f"Độ rộng sóng {value}%")
            self._canvas.update()

    def _on_pos_x(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        norm = value / 100.0
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].x = norm
            self._layout.images[index].clamp()
        elif kind == "subtitle":
            self._layout.subtitle_x = norm
            self._layout.clamp()
        elif kind == "wave":
            self._layout.wave_x = norm
            self._layout.clamp()
        self._pos_x_label.setText(f"Vị trí X: {value}%")
        self._canvas.update()

    def _on_pos_y(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        norm = value / 100.0
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].y = norm
            self._layout.images[index].clamp()
        elif kind == "subtitle":
            self._layout.subtitle_y = norm
            self._layout.clamp()
        elif kind == "wave":
            self._layout.wave_y = norm
            self._layout.clamp()
        self._pos_y_label.setText(f"Vị trí Y: {value}%")
        self._canvas.update()

    def _on_rotate(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].rotation = float(value)
            self._layout.images[index].clamp()
            self._rotate_label.setText(f"Góc xoay (Rotate): {value}°")
            self._canvas.update()

    def _add_image(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Chọn ảnh lớp phủ", "", _IMAGE_FILTER)
        if not path:
            return
        index = len(self._layout.images)
        offset = 0.03 * (index % 5)
        self._layout.images.append(FrameImage(
            path=str(Path(path)), x=0.05 + offset, y=0.05 + offset, width=0.18))
        self._layout.enabled = True
        self._enabled.setChecked(True)
        self._canvas.reload_assets()
        self._rebuild_list(index)
        self._canvas.set_selection("image", index)
        self._canvas.update()

    def _remove_image(self) -> None:
        if self._canvas.delete_selected():
            self._rebuild_list(-1)

    def _pick_preview(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Chọn ảnh nền xem trước", "", _IMAGE_FILTER)
        if not path:
            return
        self._layout.preview = str(Path(path))
        self._canvas.reload_assets()
        self._preview_clear.setEnabled(True)
        paint_status(self._status, "success")
        self._status.setText(f"Ảnh nền xem trước: {Path(path).name}")

    def _clear_preview(self) -> None:
        self._layout.preview = ""
        self._canvas.reload_assets()
        self._preview_clear.setEnabled(False)
        self._status.setText("Đã bỏ ảnh nền xem trước.")

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(
            _qcolor(self._layout.wave_color), self, "Chọn màu sóng âm")
        if not color.isValid():
            return
        self._layout.wave_color = color.name().upper()
        self._paint_color_button()
        self._canvas.update()

    def _paint_color_button(self) -> None:
        color = QColor(self._layout.wave_color)
        if not color.isValid():
            color = QColor("#4FC3F7")
        text = "#0B1728" if color.lightness() > 150 else "#FFFFFF"
        self._color_button.setStyleSheet(
            "QPushButton {"
            f" background:{color.name()}; color:{text};"
            f" border:1px solid {theme.BORDER_STRONG}; font-weight:600;"
            "}"
            "QPushButton:disabled { background:transparent; color:"
            f"{theme.TEXT_FAINT}; border-color:{theme.BORDER}; }}"
        )

    def _reset(self) -> None:
        frame = FrameLayout()
        self._layout = frame
        self._canvas.set_layout(frame)
        self._rebuild_list(-1)
        self._sync_controls()
        self._paint_color_button()
        paint_status(self._status, "muted")
        self._status.setText("Đã đặt lại khung mẫu.")
