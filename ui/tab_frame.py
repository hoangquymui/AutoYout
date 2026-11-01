"""Video Frame tab: drag-and-drop template editor for the rendered video.

The canvas draws a 16:9 preview with the subtitle anchor, the waveform box and
any PNG overlays from :mod:`core.frame_layout`.  Dragging changes the shared
normalised coordinates; the FFmpeg renderer applies the same layout when the
template is enabled.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
    QBoxLayout,
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSlider,
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
    """16:9 interactive preview. Reads/writes a shared :class:`FrameLayout`."""

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
            self._pixmaps[image.path] = QPixmap(image.path)
        self._preview = None
        if self._layout.preview:
            pixmap = QPixmap(self._layout.preview)
            if not pixmap.isNull():
                self._preview = pixmap

    # ── geometry helpers ─────────────────────────────────────────────────────
    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        return int(round(max(1, width - 24) * 9 / 16)) + 24

    def _frame_rect(self) -> QRectF:
        rect = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        if rect.width() <= 0 or rect.height() <= 0:
            return QRectF()
        ratio = 16 / 9
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
        self._paint_grid(painter, frame)
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
        pen = QPen(_qcolor(theme.ACCENT, 110))
        pen.setWidthF(1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame.adjusted(inset, inset, -inset, -inset), 8, 8)

    def _paint_wave(self, painter: QPainter) -> None:
        box = self._wave_box()
        if box.isEmpty():
            return
        color = _qcolor(self._layout.wave_color)
        fill = QColor(color)
        fill.setAlpha(32 if self._layout.waveform else 14)
        painter.setPen(QPen(color, 1.4))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 8, 8)
        bars = 48
        widths = box.width() / bars
        for index in range(bars):
            wave = abs(math.sin(index * 0.55 + self._phase)
                       * math.cos(index * 0.17 - self._phase * 0.6))
            height = box.height() * (0.18 + 0.62 * wave)
            x = box.left() + index * widths + widths * 0.2
            bar = QRectF(x, box.center().y() - height / 2,
                         max(1.4, widths * 0.55), height)
            bar_color = QColor(color)
            bar_color.setAlpha(170 if self._layout.waveform else 70)
            painter.setPen(Qt.PenStyle.NoPen)
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
        if pixmap is not None and not pixmap.isNull():
            painter.drawPixmap(box, pixmap,
                               QRectF(pixmap.rect()))
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
        text = "16:9 · 1920×1080"
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
            kind, _index = self._hit_test(pos)
            if kind and self._resize_handle().contains(pos):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif kind:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
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
        return QSize(840, 460)


class FrameTab(QWidget):
    """Editor page for the video frame template."""

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
            "Video Frame",
            "Kéo thả khung mẫu: vị trí phụ đề, sóng âm và ảnh lớp phủ PNG.",
        )
        self._enabled = QCheckBox("Bật khung mẫu")
        self._enabled.setToolTip(
            "Áp dụng bố cục này khi tạo video (tab Video vẫn quyết định có hiện phụ đề).")
        header.add_action(self._enabled)
        root.addWidget(header)

        row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        row.setSpacing(8)
        self._row = row
        self._canvas = FrameCanvas(self._layout)
        canvas_col = QVBoxLayout()
        canvas_col.setContentsMargins(0, 0, 0, 0)
        canvas_col.setSpacing(0)
        canvas_col.addStretch(1)
        canvas_col.addWidget(self._canvas)
        canvas_col.addStretch(1)
        row.addLayout(canvas_col, 1)

        panel = QWidget()
        panel.setMinimumWidth(270)
        panel.setMaximumWidth(330)
        self._panel = panel
        panel_col = QVBoxLayout(panel)
        panel_col.setContentsMargins(0, 0, 0, 0)
        panel_col.setSpacing(10)
        panel_col.addWidget(self._build_subtitle_group())
        panel_col.addWidget(self._build_wave_group())
        panel_col.addWidget(self._build_layers_group(), 1)
        row.addWidget(panel, 0)
        root.addLayout(row, 1)

        hint = QLabel(
            "Kéo vật thể để di chuyển · kéo chấm ở góc để đổi kích thước · "
            "Delete để xoá ảnh. Phụ đề chỉ hiện khi bật “Hiện phụ đề” ở tab Video.")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self._status = QLabel("Khung mẫu đang tắt — bật “Bật khung mẫu” để áp dụng.")
        self._status.setObjectName("MutedLabel")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._enabled.toggled.connect(self._on_enabled)
        self._canvas.layout_changed.connect(self._on_layout_changed)
        self._canvas.selection_changed.connect(self._on_selection)
        self._sync_controls()

    # ── panel construction ───────────────────────────────────────────────────
    def _build_subtitle_group(self) -> QGroupBox:
        box = QGroupBox("Phụ đề")
        column = QVBoxLayout(box)
        column.setSpacing(6)
        self._scale = QSlider(Qt.Orientation.Horizontal)
        self._scale.setRange(50, 200)
        self._scale.setValue(100)
        self._scale.setToolTip("Cỡ chữ phụ đề khi burn vào video.")
        self._scale_label = QLabel("Cỡ chữ 100%")
        self._scale_label.setObjectName("MutedLabel")
        self._scale.valueChanged.connect(self._on_scale)
        column.addWidget(self._scale_label)
        column.addWidget(self._scale)
        return box

    def _build_wave_group(self) -> QGroupBox:
        box = QGroupBox("Sóng âm thanh")
        column = QVBoxLayout(box)
        column.setSpacing(8)
        self._wave = QCheckBox("Hiện sóng âm")
        self._wave.setToolTip("Vẽ waveform từ audio của video tại vị trí đã đặt.")
        self._wave.toggled.connect(self._on_wave_toggled)
        column.addWidget(self._wave)
        self._color_button = QPushButton("Màu sóng")
        self._color_button.setToolTip("Chọn màu cho sóng âm.")
        self._color_button.clicked.connect(self._pick_color)
        column.addWidget(self._color_button)
        return box

    def _build_layers_group(self) -> QGroupBox:
        box = QGroupBox("Ảnh lớp phủ (PNG)")
        column = QVBoxLayout(box)
        column.setSpacing(7)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self._add_button = QPushButton("Thêm PNG")
        set_icon(self._add_button, "plus", "accent", 14)
        self._add_button.clicked.connect(self._add_image)
        tools.addWidget(self._add_button, 1)
        self._remove_button = icon_button("trash", "Xoá ảnh đang chọn", role="danger")
        self._remove_button.clicked.connect(self._remove_image)
        tools.addWidget(self._remove_button)
        column.addLayout(tools)

        self._preview_row = QHBoxLayout()
        self._preview_row.setSpacing(6)
        self._preview_button = QPushButton("Nền xem trước")
        set_icon(self._preview_button, "image", "accent", 14)
        self._preview_button.setToolTip(
            "Chọn ảnh nền để xem trước khung (không được đưa vào video).")
        self._preview_button.clicked.connect(self._pick_preview)
        self._preview_row.addWidget(self._preview_button, 1)
        self._preview_clear = icon_button("x", "Bỏ ảnh nền xem trước")
        self._preview_clear.clicked.connect(self._clear_preview)
        self._preview_row.addWidget(self._preview_clear)
        column.addLayout(self._preview_row)

        self._image_list = QListWidget()
        self._image_list.setMinimumHeight(110)
        self._image_list.setToolTip("Chọn một ảnh để chỉnh độ đậm và độ rộng.")
        self._image_list.currentRowChanged.connect(self._on_list_row)
        column.addWidget(self._image_list, 1)

        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(10, 100)
        self._opacity.setValue(100)
        self._opacity_label = QLabel("Độ đậm 100%")
        self._opacity_label.setObjectName("MutedLabel")
        self._opacity.valueChanged.connect(self._on_opacity)
        column.addWidget(self._opacity_label)
        column.addWidget(self._opacity)

        self._width = QSlider(Qt.Orientation.Horizontal)
        self._width.setRange(5, 100)
        self._width.setValue(18)
        self._width_label = QLabel("Độ rộng 18%")
        self._width_label.setObjectName("MutedLabel")
        self._width.valueChanged.connect(self._on_width)
        column.addWidget(self._width_label)
        column.addWidget(self._width)

        reset = QPushButton("Đặt lại khung mẫu")
        set_icon(reset, "rotate-ccw", "muted", 14)
        reset.clicked.connect(self._reset)
        column.addWidget(reset)
        return box

    # ── responsive reflow ────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        narrow = self.width() < 880
        direction = (QBoxLayout.Direction.TopToBottom if narrow
                     else QBoxLayout.Direction.LeftToRight)
        if self._row.direction() != direction:
            self._row.setDirection(direction)
        if narrow:
            self._panel.setMinimumWidth(0)
            self._panel.setMaximumWidth(16777215)
        else:
            self._panel.setMinimumWidth(270)
            self._panel.setMaximumWidth(330)

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
            f"Cỡ chữ {int(round(self._layout.subtitle_scale * 100))}%")
        self._opacity.setEnabled(False)
        self._width.setEnabled(False)
        self._remove_button.setEnabled(False)
        self._preview_clear.setEnabled(bool(self._layout.preview))
        self._color_button.setEnabled(self._layout.waveform)
        self._block = False
        self._paint_color_button()

    def _rebuild_list(self, select: int = -1) -> None:
        self._block = True
        self._image_list.clear()
        for image in self._layout.images:
            item = QListWidgetItem(image.name)
            item.setToolTip(image.path)
            self._image_list.addItem(item)
        if 0 <= select < self._image_list.count():
            self._image_list.setCurrentRow(select)
        self._block = False
        self._update_image_sliders(select)

    def _update_image_sliders(self, index: int) -> None:
        valid = 0 <= index < len(self._layout.images)
        self._opacity.setEnabled(valid)
        self._width.setEnabled(valid)
        self._remove_button.setEnabled(valid)
        if not valid:
            return
        image = self._layout.images[index]
        self._block = True
        self._opacity.setValue(int(round(image.opacity * 100)))
        self._width.setValue(int(round(image.width * 100)))
        self._block = False
        self._opacity_label.setText(f"Độ đậm {int(round(image.opacity * 100))}%")
        self._width_label.setText(f"Độ rộng {int(round(image.width * 100))}%")

    def _on_selection(self, kind: str, index: int) -> None:
        if kind != "image":
            self._opacity.setEnabled(False)
            self._width.setEnabled(False)
            self._remove_button.setEnabled(False)
            self._block = True
            self._image_list.setCurrentRow(-1)
            self._block = False
            return
        self._block = True
        self._image_list.setCurrentRow(index)
        self._block = False
        self._update_image_sliders(index)

    def _on_layout_changed(self) -> None:
        if not self._layout.enabled:
            self._enabled.setChecked(True)
        kind, index = self._canvas.selection()
        if kind == "image":
            self._update_image_sliders(index)
            if 0 <= index < self._image_list.count():
                self._image_list.item(index).setText(self._layout.images[index].name)
        self._block = True
        if kind == "subtitle":
            self._scale.setValue(int(round(self._layout.subtitle_scale * 100)))
            self._scale_label.setText(
                f"Cỡ chữ {int(round(self._layout.subtitle_scale * 100))}%")
        self._block = False

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
        self._scale_label.setText(f"Cỡ chữ {value}%")
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
        if kind != "image" or not (0 <= index < len(self._layout.images)):
            return
        self._layout.images[index].width = value / 100.0
        self._layout.images[index].clamp()
        self._width_label.setText(f"Độ rộng {value}%")
        self._canvas.update()

    def _on_list_row(self, row: int) -> None:
        if self._block:
            return
        if row < 0:
            self._canvas.set_selection("", -1)
            return
        self._canvas.set_selection("image", row)

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
