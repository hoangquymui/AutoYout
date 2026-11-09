"""Video Frame tab: Professional CapCut/Canva-inspired video overlay editor.

Layout
------
* Top Toolbar: Aspect ratio switcher (16:9, 9:16, 1:1, 4:5), view toggles
  (grid, safe area, center guides), history (undo/redo), and reset.
* Left Column: Assets / Layers management, Preset Templates, and Sticker Library.
* Center Column: Responsive interactive canvas player with waveform playback & timeline.
* Right Column: Inspector panel (Transform: Scale, Pos X/Y, Rotate, Alignment, Flip,
  Opacity, and context-specific controls for Subtitles, Waveform, and Images).
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
    QIcon,
    QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.frame_layout import FrameImage, FrameLayout
from ui import theme
from ui.components.icons import pixmap, set_icon
from ui.components.page import PageHeader, icon_button, paint_status

_IMAGE_FILTER = "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả (*)"
_DEFAULT_SAMPLE_TEXT = "Phụ đề hiển thị ở đây"
_HANDLE = 10.0
PREVIEW_SECONDS = 12.0
_TICK_MS = 55
_WAVE_CYCLES = 2.4

_ASPECT_RATIOS = {
    "16:9": 16.0 / 9.0,
    "9:16": 9.0 / 16.0,
    "1:1": 1.0,
    "4:5": 4.0 / 5.0,
}

_QUICK_COLORS = [
    ("#38BDF8", "Cyan"),
    ("#34D399", "Xanh ngọc"),
    ("#F43F5E", "Hồng"),
    ("#FBBF24", "Vàng"),
    ("#A855F7", "Tím"),
    ("#FFFFFF", "Trắng"),
]


def _qcolor(value: str, alpha: int = 255) -> QColor:
    color = QColor(value)
    if not color.isValid():
        color = QColor("#38BDF8")
    color.setAlpha(alpha)
    return color


class FrameCanvas(QWidget):
    """Interactive preview canvas. Reads/writes a shared :class:`FrameLayout`."""

    layout_changed = Signal()
    selection_changed = Signal(str, int)
    layer_structure_changed = Signal()
    edit_committed = Signal()
    phase_changed = Signal(float)

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
        self._playhead = 0.0
        self._playing = False
        self.sample_text = _DEFAULT_SAMPLE_TEXT

        # View options
        self.show_grid = True
        self.show_safe_area = True
        self.show_center_guides = True
        self._aspect_ratio_mode = layout.aspect_ratio or "16:9"

        # Magnetic snapping state
        self._snap_x = False
        self._snap_y = False

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(160, 90)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._load_assets()

    # ── model binding ────────────────────────────────────────────────────────
    @property
    def aspect_ratio_mode(self) -> str:
        return getattr(self, "_aspect_ratio_mode", "16:9")

    @aspect_ratio_mode.setter
    def aspect_ratio_mode(self, value: str) -> None:
        self._aspect_ratio_mode = value or "16:9"
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "reposition_canvas"):
            parent.reposition_canvas()
        self.updateGeometry()
        self.update()

    @property
    def layout(self) -> FrameLayout:
        return self._layout

    def set_layout(self, layout: FrameLayout) -> None:
        self._layout = layout
        self.aspect_ratio_mode = layout.aspect_ratio or "16:9"
        self._selected_kind = ""
        self._selected_index = -1
        self._load_assets()
        self.updateGeometry()
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
        del_idx = self._selected_index
        old_layers = self._layout.ordered_layers()
        self._layout.images.pop(del_idx)
        new_layers: List[Tuple[str, int]] = []
        for k, idx in old_layers:
            if k == "image":
                if idx == del_idx:
                    continue
                new_layers.append(("image", idx - 1 if idx > del_idx else idx))
            else:
                new_layers.append((k, idx))
        self._layout.set_ordered_layers(new_layers)
        self.set_selection("", -1)
        self.layer_structure_changed.emit()
        self.layout_changed.emit()
        self.edit_committed.emit()
        self.update()
        return True

    def duplicate_selected(self) -> bool:
        if self._selected_kind != "image":
            return False
        if not (0 <= self._selected_index < len(self._layout.images)):
            return False
        src_idx = self._selected_index
        old_layers = self._layout.ordered_layers()
        original = self._layout.images[src_idx]
        clone = FrameImage(
            path=original.path,
            x=min(0.85, original.x + 0.03),
            y=min(0.85, original.y + 0.03),
            width=original.width,
            opacity=original.opacity,
            rotation=original.rotation,
            flip_h=original.flip_h,
            flip_v=original.flip_v,
            visible=True,
            locked=False,
        ).clamp()
        insert_at = len(self._layout.images)
        self._layout.images.append(clone)
        new_layers = [("image", insert_at)] + [layer for layer in old_layers if layer != ("image", insert_at)]
        self._layout.set_ordered_layers(new_layers)
        self.set_selection("image", insert_at)
        self.layer_structure_changed.emit()
        self.layout_changed.emit()
        self.edit_committed.emit()
        self.update()
        return True

    def toggle_selected_visibility(self) -> bool:
        if self._selected_kind == "image" and 0 <= self._selected_index < len(self._layout.images):
            img = self._layout.images[self._selected_index]
            img.visible = not getattr(img, "visible", True)
            self.layer_structure_changed.emit()
            self.layout_changed.emit()
            self.edit_committed.emit()
            self.update()
            return True
        elif self._selected_kind == "wave":
            self._layout.waveform = not self._layout.waveform
            self.layer_structure_changed.emit()
            self.layout_changed.emit()
            self.edit_committed.emit()
            self.update()
            return True
        elif self._selected_kind == "subtitle":
            self._layout.subtitle_visible = not getattr(self._layout, "subtitle_visible", True)
            self.layer_structure_changed.emit()
            self.layout_changed.emit()
            self.edit_committed.emit()
            self.update()
            return True
        elif self._selected_kind == "video":
            self._layout.video_visible = not getattr(self._layout, "video_visible", True)
            self.layer_structure_changed.emit()
            self.layout_changed.emit()
            self.edit_committed.emit()
            self.update()
            return True
        return False

    def toggle_selected_lock(self) -> bool:
        if self._selected_kind == "image" and 0 <= self._selected_index < len(self._layout.images):
            img = self._layout.images[self._selected_index]
            img.locked = not getattr(img, "locked", False)
            self.layer_structure_changed.emit()
            self.layout_changed.emit()
            self.edit_committed.emit()
            self.update()
            return True
        elif self._selected_kind == "video":
            self._layout.video_locked = not getattr(self._layout, "video_locked", False)
            self.layer_structure_changed.emit()
            self.layout_changed.emit()
            self.edit_committed.emit()
            self.update()
            return True
        return False

    def _load_assets(self) -> None:
        self._pixmaps.clear()
        for image in self._layout.images:
            if Path(image.path).is_file():
                self._pixmaps[image.path] = QPixmap(image.path)
        self._preview = None
        if self._layout.preview and Path(self._layout.preview).is_file():
            pix = QPixmap(self._layout.preview)
            if not pix.isNull():
                self._preview = pix

    # ── geometry helpers ─────────────────────────────────────────────────────
    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        ratio = _ASPECT_RATIOS.get(self.aspect_ratio_mode, 16.0 / 9.0)
        usable_w = max(1, width)
        return int(round(usable_w / ratio))

    def _frame_rect(self) -> QRectF:
        w = float(self.width())
        h = float(self.height())
        if w <= 0.0 or h <= 0.0:
            return QRectF()
        ratio = _ASPECT_RATIOS.get(self.aspect_ratio_mode, 16.0 / 9.0)
        target_w = min(w, h * ratio)
        target_h = target_w / ratio
        if target_h > h:
            target_h = h
            target_w = target_h * ratio
        return QRectF(
            (w - target_w) / 2.0,
            (h - target_h) / 2.0,
            target_w,
            target_h,
        )

    def _subtitle_font(self) -> QFont:
        frame = self._frame_rect()
        family = getattr(self._layout, "subtitle_font", "Arial") or "Arial"
        base_size = getattr(self._layout, "subtitle_font_size", 44) or 44
        scaled_size = max(8, int(round(base_size * (self._layout.subtitle_scale or 1.0) * frame.height() / 1080.0)))
        font = QFont(family)
        font.setPixelSize(scaled_size)
        font.setBold(True)
        return font

    def _subtitle_box(self) -> QRectF:
        frame = self._frame_rect()
        if frame.isEmpty():
            return QRectF()
        w = max(20.0, getattr(self._layout, "subtitle_w", 0.8) * frame.width())
        h = max(10.0, getattr(self._layout, "subtitle_h", 0.14) * frame.height())
        cx = frame.left() + self._layout.subtitle_x * frame.width()
        cy = frame.top() + self._layout.subtitle_y * frame.height()
        return QRectF(cx - w / 2.0, cy - h / 2.0, w, h)

    def _wave_box(self) -> QRectF:
        frame = self._frame_rect()
        return QRectF(
            frame.left() + self._layout.wave_x * frame.width(),
            frame.top() + self._layout.wave_y * frame.height(),
            self._layout.wave_w * frame.width(),
            self._layout.wave_h * frame.height(),
        )

    def _video_box(self) -> QRectF:
        frame = self._frame_rect()
        vw = float(getattr(self._layout, "video_w", 1.0))
        vx = float(getattr(self._layout, "video_x", 0.0))
        vy = float(getattr(self._layout, "video_y", 0.0))
        return QRectF(
            frame.left() + vx * frame.width(),
            frame.top() + vy * frame.height(),
            vw * frame.width(),
            vw * frame.height(),
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
        x = frame.left() + image.x * frame.width()
        y = frame.top() + image.y * frame.height()
        return QRectF(x, y, width, height)

    def _resize_handle(self) -> QRectF:
        kind, index = self.selection()
        if kind == "subtitle":
            box = self._subtitle_box()
            return QRectF(box.right() - _HANDLE / 2, box.bottom() - _HANDLE / 2,
                          _HANDLE, _HANDLE)
        if kind == "wave":
            box = self._wave_box()
        elif kind == "video":
            box = self._video_box()
        elif kind == "image":
            box = self._image_box(index)
        else:
            return QRectF()
        return QRectF(box.right() - _HANDLE / 2, box.bottom() - _HANDLE / 2,
                      _HANDLE, _HANDLE)

    # ── alignments & transforms ──────────────────────────────────────────────
    def align_selected(self, mode: str) -> None:
        kind, index = self.selection()
        frame = self._frame_rect()
        if kind == "image" and 0 <= index < len(self._layout.images):
            image = self._layout.images[index]
            box = self._image_box(index)
            norm_w = image.width
            norm_h = (box.height() / frame.height()) if frame.height() > 0 else norm_w
            if mode == "left":
                image.x = 0.04
            elif mode == "center_x":
                image.x = round((1.0 - norm_w) / 2.0, 4)
            elif mode == "right":
                image.x = round(1.0 - norm_w - 0.04, 4)
            elif mode == "top":
                image.y = 0.04
            elif mode == "center_y":
                image.y = round((1.0 - norm_h) / 2.0, 4)
            elif mode == "bottom":
                image.y = round(1.0 - norm_h - 0.04, 4)
            elif mode == "center_both":
                image.x = round((1.0 - norm_w) / 2.0, 4)
                image.y = round((1.0 - norm_h) / 2.0, 4)
            image.clamp()
        elif kind == "subtitle":
            half_w = getattr(self._layout, "subtitle_w", 0.8) / 2.0
            half_h = getattr(self._layout, "subtitle_h", 0.14) / 2.0
            if mode == "left":
                self._layout.subtitle_x = max(0.04 + half_w, half_w)
            elif mode in ("center_x", "center_both"):
                self._layout.subtitle_x = 0.5
            elif mode == "right":
                self._layout.subtitle_x = min(0.96 - half_w, 1.0 - half_w)
            elif mode == "top":
                self._layout.subtitle_y = max(0.04 + half_h, half_h)
            elif mode in ("center_y", "center_both"):
                self._layout.subtitle_y = 0.5
            elif mode == "bottom":
                self._layout.subtitle_y = min(0.96 - half_h, 1.0 - half_h)
            self._layout.clamp()
        elif kind == "wave":
            if mode == "left":
                self._layout.wave_x = 0.04
            elif mode in ("center_x", "center_both"):
                self._layout.wave_x = max(0.0, round((1.0 - self._layout.wave_w) / 2.0, 4))
            elif mode == "right":
                self._layout.wave_x = max(0.0, round(1.0 - self._layout.wave_w - 0.04, 4))
            elif mode == "top":
                self._layout.wave_y = 0.06
            elif mode in ("center_y", "center_both"):
                self._layout.wave_y = max(0.0, round((1.0 - self._layout.wave_h) / 2.0, 4))
            elif mode == "bottom":
                self._layout.wave_y = max(0.0, round(1.0 - self._layout.wave_h - 0.06, 4))
            self._layout.clamp()
        elif kind == "video":
            vw = self._layout.video_w
            if mode == "left":
                self._layout.video_x = 0.0
            elif mode in ("center_x", "center_both"):
                self._layout.video_x = max(0.0, round((1.0 - vw) / 2.0, 4))
            elif mode == "right":
                self._layout.video_x = max(0.0, round(1.0 - vw, 4))
            elif mode == "top":
                self._layout.video_y = 0.0
            elif mode in ("center_y", "center_both"):
                self._layout.video_y = max(0.0, round((1.0 - vw) / 2.0, 4))
            elif mode == "bottom":
                self._layout.video_y = max(0.0, round(1.0 - vw, 4))
            if mode == "center_both":
                self._layout.video_y = max(0.0, round((1.0 - vw) / 2.0, 4))
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

        # 1. Fill exact video canvas (strictly 16:9, no excess, no deficit)
        painter.fillRect(frame, QColor("#050810"))

        # 2. Draw layers.
        layers = self._layout.ordered_layers()
        for kind, idx in reversed(layers):
            if kind == "video":
                # Video stream is clipped to the frame boundary
                painter.save()
                painter.setClipRect(frame)
                self._paint_preview(painter, frame)
                painter.restore()
            elif kind == "wave":
                self._paint_wave(painter)
            elif kind == "image" and 0 <= idx < len(self._layout.images):
                self._paint_image(painter, idx)
            elif kind == "subtitle":
                self._paint_subtitle(painter)

        # Inside frame: grid / safe area / snap guides
        painter.save()
        painter.setClipRect(frame)
        if self.show_grid:
            self._paint_grid(painter, frame)
        if self.show_safe_area:
            self._paint_safe_area(painter, frame)
        if self.show_center_guides and (self._snap_x or self._snap_y):
            self._paint_snap_guides(painter, frame)
        painter.restore()

        # Resolution badge
        self._paint_badge(painter, frame)

        # Active selection outline & resize handles drawn unclipped on top
        self._paint_active_selection(painter)

    def _paint_backdrop(self, painter: QPainter) -> None:
        pass

    def _paint_preview(self, painter: QPainter, frame: QRectF) -> None:
        vid_vis = getattr(self._layout, "video_visible", True)
        vbox = self._video_box()
        if not vid_vis:
            if self._selected_kind == "video":
                painter.setPen(QPen(_qcolor(theme.TEXT_MUTED, 120), 1.0, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(vbox)
                self._paint_caption(painter, vbox, "Video (Đang ẩn)")
            return

        if self._preview is not None and not self._preview.isNull():
            scaled = self._preview.scaled(
                vbox.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            source = QRectF(
                max(0.0, (scaled.width() - vbox.width()) / 2),
                max(0.0, (scaled.height() - vbox.height()) / 2),
                vbox.width(), vbox.height())
            painter.drawPixmap(vbox, scaled, source)
            shade = QColor("#000000")
            shade.setAlpha(60)
            painter.fillRect(vbox, shade)
        else:
            gradient = QLinearGradient(vbox.topLeft(), vbox.bottomRight())
            gradient.setColorAt(0.0, QColor(theme.SURFACE_RAISED))
            gradient.setColorAt(0.55, QColor(theme.SURFACE))
            gradient.setColorAt(1.0, QColor(theme.BG))
            painter.fillRect(vbox, gradient)

            glow = QRadialGradient(vbox.center(), vbox.width() * 0.6)
            accent = QColor(theme.ACCENT)
            accent.setAlpha(60)
            glow.setColorAt(0.0, accent)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(vbox, glow)

        if self._selected_kind != "video":
            if self._layout.video_w < 0.999 or self._layout.video_x > 0.001 or self._layout.video_y > 0.001:
                painter.setPen(QPen(_qcolor(theme.BLUE, 130), 1.0, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(vbox)
                self._paint_caption(painter, vbox, "Video")

    def _paint_grid(self, painter: QPainter, frame: QRectF) -> None:
        pen = QPen(QColor(theme.BORDER_STRONG))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DotLine)
        pen.setColor(_qcolor(theme.BORDER_STRONG, 90))
        painter.setPen(pen)
        for step in range(1, 3):
            x = frame.left() + frame.width() * step / 3
            y = frame.top() + frame.height() * step / 3
            painter.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
            painter.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))

    def _paint_safe_area(self, painter: QPainter, frame: QRectF) -> None:
        inset10 = frame.width() * 0.10
        safe_title = frame.adjusted(inset10, inset10, -inset10, -inset10)
        pen10 = QPen(_qcolor(theme.BLUE, 70), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(pen10)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(safe_title)

        if self.aspect_ratio_mode == "9:16":
            right_w = frame.width() * 0.18
            right_zone = QRectF(frame.right() - right_w, frame.top() + frame.height() * 0.45,
                                right_w, frame.height() * 0.40)
            painter.setPen(QPen(_qcolor(theme.WARNING, 80), 1.0, Qt.PenStyle.DotLine))
            painter.setBrush(_qcolor(theme.WARNING, 15))
            painter.drawRect(right_zone)

            bottom_h = frame.height() * 0.20
            bottom_zone = QRectF(frame.left(), frame.bottom() - bottom_h, frame.width(), bottom_h)
            painter.drawRect(bottom_zone)

    def _paint_snap_guides(self, painter: QPainter, frame: QRectF) -> None:
        pen = QPen(_qcolor(theme.ACCENT, 200), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if self._snap_x:
            cx = frame.left() + frame.width() / 2.0
            painter.drawLine(QPointF(cx, frame.top()), QPointF(cx, frame.bottom()))
        if self._snap_y:
            cy = frame.top() + frame.height() / 2.0
            painter.drawLine(QPointF(frame.left(), cy), QPointF(frame.right(), cy))

    def _paint_wave(self, painter: QPainter) -> None:
        if not self._layout.waveform:
            return
        box = self._wave_box()
        color = _qcolor(self._layout.wave_color)
        bg = QColor(color)
        bg.setAlpha(25)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRect(box)

        bars = max(8, int(box.width() / 11))
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

        if self._selected_kind != "wave":
            painter.setPen(QPen(color, 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(box)
            self._paint_caption(painter, box, "Sóng âm")

    def _paint_image(self, painter: QPainter, index: int) -> None:
        image = self._layout.images[index]
        box = self._image_box(index)
        if box.isEmpty():
            return

        visible = getattr(image, "visible", True)
        locked = getattr(image, "locked", False)

        if not visible:
            if self._selected_kind == "image" and self._selected_index == index:
                painter.setPen(QPen(_qcolor(theme.TEXT_MUTED, 120), 1.0, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(box)
                self._paint_caption(painter, box, f"{image.name} (Đang ẩn)")
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
            painter.setPen(QPen(_qcolor(theme.WARNING, 180), 1.4, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(0, 0, 0, 70))
            painter.drawRect(box)
            self._paint_caption(painter, box, "Thiếu file ảnh")
        painter.restore()

    def _paint_subtitle(self, painter: QPainter) -> None:
        sub_visible = getattr(self._layout, "subtitle_visible", True)
        box = self._subtitle_box()
        text = self.sample_text or _DEFAULT_SAMPLE_TEXT

        if not sub_visible:
            if self._selected_kind == "subtitle":
                painter.setPen(QPen(_qcolor(theme.TEXT_MUTED, 120), 1.0, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(box)
                self._paint_caption(painter, box, "Phụ đề (Đang ẩn)")
            return

        painter.save()
        painter.setClipRect(box)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRect(box)
        painter.setFont(self._subtitle_font())
        painter.setPen(QColor("#FFFFFF"))
        inner_box = box.adjusted(8, 4, -8, -4)
        painter.drawText(inner_box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
        painter.restore()

        if self._selected_kind != "subtitle":
            self._paint_caption(painter, box, "Phụ đề")
            pen = QPen(_qcolor(theme.ACCENT, 110), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(box)

    def _paint_active_selection(self, painter: QPainter) -> None:
        kind, index = self.selection()
        if not kind:
            return
        if kind == "subtitle":
            if getattr(self._layout, "subtitle_visible", True):
                box = self._subtitle_box()
                self._paint_selection(painter, box, "Phụ đề (Textbox)")
        elif kind == "wave":
            if self._layout.wave_active:
                box = self._wave_box()
                self._paint_selection(painter, box, "Sóng âm thanh")
        elif kind == "video":
            if getattr(self._layout, "video_visible", True):
                box = self._video_box()
                locked = getattr(self._layout, "video_locked", False)
                label = "Video 🔒" if locked else "Video"
                self._paint_selection(painter, box, label)
        elif kind == "image" and 0 <= index < len(self._layout.images):
            image = self._layout.images[index]
            if getattr(image, "visible", True):
                box = self._image_box(index)
                if not box.isEmpty():
                    locked = getattr(image, "locked", False)
                    label = f"{image.name} 🔒" if locked else image.name
                    self._paint_selection(painter, box, label)

    def _paint_selection(self, painter: QPainter, box: QRectF, label: str) -> None:
        pulse = 0.60 + 0.40 * abs(math.sin(self._phase * 0.8))
        glow = QColor(theme.ACCENT)
        glow.setAlpha(int(70 * pulse))
        painter.setPen(QPen(glow, 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)
        painter.setPen(QPen(_qcolor(theme.ACCENT), 1.6))
        painter.drawRect(box)

        handle = self._resize_handle()
        painter.setPen(QPen(_qcolor(theme.PRIMARY_TEXT), 1.0))
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRect(handle)
        self._paint_caption(painter, box, label)

    def _paint_caption(self, painter: QPainter, box: QRectF, text: str) -> None:
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text) + 12
        chip = QRectF(max(0.0, box.left()), max(0.0, box.top() - 18), width, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        chip_bg = QColor(theme.SURFACE)
        chip_bg.setAlpha(220)
        painter.setBrush(chip_bg)
        painter.drawRect(chip)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_BODY))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_frame_border(self, painter: QPainter, frame: QRectF) -> None:
        pass

    def _paint_badge(self, painter: QPainter, frame: QRectF) -> None:
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        ratio_title = {
            "16:9": "16:9 Ngang",
            "9:16": "9:16 Shorts",
            "1:1": "1:1 Vuông",
            "4:5": "4:5 Dọc",
        }.get(self.aspect_ratio_mode, self.aspect_ratio_mode)
        text = f"{ratio_title} · Player"
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text) + 16
        chip = QRectF(frame.right() - width - 8, frame.top() + 8, width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 130))
        painter.drawRect(chip)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

    # ── interaction ──────────────────────────────────────────────────────────
    def _hit_test(self, pos: QPointF) -> Tuple[str, int]:
        for kind, idx in self._layout.ordered_layers():
            if kind == "subtitle":
                if getattr(self._layout, "subtitle_visible", True) and self._subtitle_box().contains(pos):
                    return "subtitle", -1
            elif kind == "wave":
                if self._layout.waveform and self._wave_box().contains(pos):
                    return "wave", -1
            elif kind == "image" and 0 <= idx < len(self._layout.images):
                img = self._layout.images[idx]
                if getattr(img, "visible", True) and self._image_box(idx).contains(pos):
                    return "image", idx
            elif kind == "video":
                if getattr(self._layout, "video_visible", True) and self._video_box().contains(pos):
                    return "video", -1
        return "", -1

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        self.setFocus()
        cur_kind, cur_idx = self.selection()
        if cur_kind and self._resize_handle().contains(pos):
            kind, index = cur_kind, cur_idx
            self._drag_mode = "resize"
        else:
            kind, index = self._hit_test(pos)
            if kind:
                self.set_selection(kind, index)
                if self._resize_handle().contains(pos):
                    self._drag_mode = "resize"
                else:
                    self._drag_mode = "move"
            else:
                self.set_selection("", -1)
                return

        if kind == "image" and 0 <= index < len(self._layout.images):
            if getattr(self._layout.images[index], "locked", False):
                self._drag_mode = ""
                self.update()
                return
        elif kind == "video":
            if getattr(self._layout, "video_locked", False):
                self._drag_mode = ""
                self.update()
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
        elif kind == "video":
            self._origin = {"x": layout.video_x, "y": layout.video_y,
                            "w": layout.video_w}
        else:
            self._origin = {"x": layout.subtitle_x, "y": layout.subtitle_y,
                            "w": layout.subtitle_w, "h": layout.subtitle_h,
                            "scale": layout.subtitle_scale}
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        pos = event.position()
        if not self._drag_mode:
            if self._resize_handle().contains(pos):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif self._hit_test(pos)[0]:
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

        self._snap_x = False
        self._snap_y = False

        if kind == "subtitle":
            if self._drag_mode == "move":
                target_x = self._origin["x"] + dx
                target_y = self._origin["y"] + dy
                if abs(target_x - 0.5) < 0.02:
                    target_x = 0.5
                    self._snap_x = True
                if abs(target_y - 0.5) < 0.02:
                    target_y = 0.5
                    self._snap_y = True
                layout.subtitle_x = target_x
                layout.subtitle_y = target_y
            else:
                orig_left = self._origin["x"] - self._origin["w"] / 2.0
                orig_top = self._origin["y"] - self._origin["h"] / 2.0
                new_w = max(0.05, min(3.0, self._origin["w"] + dx))
                new_h = max(0.02, min(2.0, self._origin["h"] + dy))
                layout.subtitle_w = new_w
                layout.subtitle_h = new_h
                layout.subtitle_x = orig_left + new_w / 2.0
                layout.subtitle_y = orig_top + new_h / 2.0
            layout.clamp()
        elif kind == "wave":
            if self._drag_mode == "move":
                target_x = self._origin["x"] + dx
                target_y = self._origin["y"] + dy
                center_x = target_x + layout.wave_w / 2.0
                if abs(center_x - 0.5) < 0.02:
                    target_x = 0.5 - layout.wave_w / 2.0
                    self._snap_x = True
                layout.wave_x = target_x
                layout.wave_y = target_y
            else:
                layout.wave_w = max(0.05, min(3.0, self._origin["w"] + dx))
                layout.wave_h = max(0.02, min(2.0, self._origin["h"] + dy))
            layout.clamp()
        elif kind == "image" and 0 <= index < len(layout.images):
            image = layout.images[index]
            if self._drag_mode == "move":
                target_x = self._origin["x"] + dx
                target_y = self._origin["y"] + dy
                center_x = target_x + image.width / 2.0
                if abs(center_x - 0.5) < 0.02:
                    target_x = 0.5 - image.width / 2.0
                    self._snap_x = True
                image.x = target_x
                image.y = target_y
            else:
                image.width = max(0.02, min(5.0, self._origin["width"] + dx))
            image.clamp()
        elif kind == "video":
            if self._drag_mode == "move":
                target_x = self._origin["x"] + dx
                target_y = self._origin["y"] + dy
                center_x = target_x + layout.video_w / 2.0
                center_y = target_y + layout.video_w / 2.0
                if abs(center_x - 0.5) < 0.02:
                    target_x = 0.5 - layout.video_w / 2.0
                    self._snap_x = True
                if abs(center_y - 0.5) < 0.02:
                    target_y = 0.5 - layout.video_w / 2.0
                    self._snap_y = True
                layout.video_x = target_x
                layout.video_y = target_y
            else:
                layout.video_w = max(0.05, min(3.0, self._origin["w"] + dx))
            layout.clamp()

        self.layout_changed.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        was_dragging = bool(self._drag_mode)
        self._drag_mode = ""
        self._snap_x = False
        self._snap_y = False
        if was_dragging:
            self.edit_committed.emit()
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        kind, _index = self._hit_test(event.position())
        if kind:
            self.set_selection(kind, _index)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.delete_selected():
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        pos = event.pos()
        kind, index = self._hit_test(pos)
        if kind:
            self.set_selection(kind, index)
        cur_kind, cur_idx = self.selection()
        if not cur_kind:
            return
        menu = QMenu(self)
        if cur_kind == "image" and 0 <= cur_idx < len(self._layout.images):
            img = self._layout.images[cur_idx]
            act_del = menu.addAction(f"Xoá ảnh “{img.name}” (Delete)")
            set_icon(act_del, "trash", "danger", 12)
            act_dup = menu.addAction("Nhân bản (Duplicate)")
            set_icon(act_dup, "copy", "accent", 12)
            menu.addSeparator()
            vis_text = "Hiện ảnh" if not getattr(img, "visible", True) else "Ẩn ảnh"
            act_vis = menu.addAction(vis_text)
            set_icon(act_vis, "eye", "muted", 12)
            lock_text = "Mở khóa vị trí" if getattr(img, "locked", False) else "Khóa vị trí"
            act_lock = menu.addAction(lock_text)
            set_icon(act_lock, "lock", "muted", 12)
            menu.addSeparator()
            act_center = menu.addAction("Căn chính giữa")
            set_icon(act_center, "crosshair", "accent", 12)
            chosen = menu.exec(event.globalPos())
            if chosen == act_del:
                self.delete_selected()
            elif chosen == act_dup:
                self.duplicate_selected()
            elif chosen == act_vis:
                self.toggle_selected_visibility()
            elif chosen == act_lock:
                self.toggle_selected_lock()
            elif chosen == act_center:
                self.align_selected("center_both")
        elif cur_kind in ("wave", "subtitle", "video"):
            act_center = menu.addAction("Căn chính giữa")
            set_icon(act_center, "crosshair", "accent", 12)
            chosen = menu.exec(event.globalPos())
            if chosen == act_center:
                self.align_selected("center_both")

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._playing:
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        super().hideEvent(event)

    # ── playback / timeline ──────────────────────────────────────────────────
    @property
    def playhead(self) -> float:
        return self._playhead

    @property
    def playing(self) -> bool:
        return self._playing

    def set_playhead(self, fraction: float) -> None:
        self._playhead = max(0.0, min(1.0, float(fraction)))
        self._phase = self._playhead * math.tau * _WAVE_CYCLES
        self.phase_changed.emit(self._playhead)
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        if self._playing:
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        step = (_TICK_MS / 1000.0) / PREVIEW_SECONDS
        self._playhead = (self._playhead + step) % 1.0
        self._phase = self._playhead * math.tau * _WAVE_CYCLES
        self.phase_changed.emit(self._playhead)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        ratio = _ASPECT_RATIOS.get(self.aspect_ratio_mode, 16.0 / 9.0)
        if ratio >= 1.0:
            return QSize(640, max(1, int(round(640 / ratio))))
        return QSize(max(1, int(round(360 * ratio))), 360)


class CanvasContainer(QWidget):
    """Host container that keeps the canvas strictly sized to the selected aspect ratio, centered with no excess bars."""

    def __init__(self, canvas: FrameCanvas, parent=None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.canvas.setParent(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(160, 90)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        ratio = _ASPECT_RATIOS.get(self.canvas.aspect_ratio_mode, 16.0 / 9.0)
        return max(1, int(round(width / ratio)))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.reposition_canvas()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self.reposition_canvas()

    def reposition_canvas(self) -> None:
        cw = float(self.width())
        ch = float(self.height())
        if cw <= 0.0 or ch <= 0.0:
            return
        ratio = _ASPECT_RATIOS.get(self.canvas.aspect_ratio_mode, 16.0 / 9.0)
        target_w = min(cw, ch * ratio)
        target_h = target_w / ratio
        if target_h > ch:
            target_h = ch
            target_w = target_h * ratio
        w = max(1, int(round(target_w)))
        h = max(1, int(round(target_h)))
        x = int(round((cw - w) / 2.0))
        y = int(round((ch - h) / 2.0))
        self.canvas.setGeometry(x, y, w, h)


class FrameTab(QWidget):
    """Professional creative studio editor: 3 columns, compact padding, undo/redo."""

    _PRESETS = (
        {
            "name": "YouTube Chuẩn",
            "desc": "16:9 · Phụ đề dưới · Sóng âm đáy",
            "ratio": "16:9",
            "values": dict(subtitle_x=0.5, subtitle_y=0.88, subtitle_scale=1.0, subtitle_visible=True,
                           waveform=True, wave_x=0.06, wave_y=0.76, wave_w=0.88, wave_h=0.08,
                           wave_color="#38BDF8"),
        },
        {
            "name": "Shorts / TikTok Viral",
            "desc": "9:16 · Phụ đề lớn giữa · Sóng âm sát đáy",
            "ratio": "9:16",
            "values": dict(subtitle_x=0.5, subtitle_y=0.74, subtitle_scale=1.35, subtitle_visible=True,
                           waveform=True, wave_x=0.08, wave_y=0.86, wave_w=0.84, wave_h=0.07,
                           wave_color="#F43F5E"),
        },
        {
            "name": "Podcast Studio",
            "desc": "16:9 · Sóng âm lớn nổi bật · Phụ đề rõ",
            "ratio": "16:9",
            "values": dict(subtitle_x=0.5, subtitle_y=0.86, subtitle_scale=1.05, subtitle_visible=True,
                           waveform=True, wave_x=0.08, wave_y=0.68, wave_w=0.84, wave_h=0.10,
                           wave_color="#34D399"),
        },
        {
            "name": "Điện Ảnh Cinematic",
            "desc": "16:9 · Phụ đề sát đáy · Tối giản",
            "ratio": "16:9",
            "values": dict(subtitle_x=0.5, subtitle_y=0.93, subtitle_scale=1.05, subtitle_visible=True,
                           waveform=False),
        },
        {
            "name": "Bản Tin Breaking News",
            "desc": "16:9 · Thanh sóng âm trên phụ đề",
            "ratio": "16:9",
            "values": dict(subtitle_x=0.5, subtitle_y=0.84, subtitle_scale=1.0, subtitle_visible=True,
                           waveform=True, wave_x=0.05, wave_y=0.72, wave_w=0.90, wave_h=0.07,
                           wave_color="#FBBF24"),
        },
        {
            "name": "Music Visualizer",
            "desc": "16:9 · Sóng âm lớn giữa màn hình",
            "ratio": "16:9",
            "values": dict(subtitle_x=0.5, subtitle_y=0.88, subtitle_scale=1.1, subtitle_visible=True,
                           waveform=True, wave_x=0.05, wave_y=0.45, wave_w=0.90, wave_h=0.18,
                           wave_color="#A855F7"),
        },
        {
            "name": "Tối Giản Toàn Bộ",
            "desc": "16:9 · Chỉ phụ đề, không sóng âm",
            "ratio": "16:9",
            "values": dict(subtitle_x=0.5, subtitle_y=0.90, subtitle_scale=1.0, subtitle_visible=True,
                           waveform=False),
        },
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = FrameLayout(enabled=True)
        self._block = False
        self._undo_stack: List[Dict[str, Any]] = []
        self._redo_stack: List[Dict[str, Any]] = []
        self._snapshot = self._layout.to_dict()
        self._timeline_block = False

        self._canvas = FrameCanvas(self._layout)
        self._manager: Optional[Any] = None
        self._settings: Optional[Dict[str, Any]] = None

        page = QVBoxLayout(self)
        page.setContentsMargins(8, 6, 8, 6)
        page.setSpacing(6)

        header = PageHeader(
            "Khung mẫu Video",
            "Thiết kế bố cục trực quan: Phụ đề, sóng âm thanh, ảnh và hình ảnh thương hiệu.",
        )
        page.addWidget(header)

        self._toolbar = self._build_toolbar()
        page.addWidget(self._toolbar)

        cols = QHBoxLayout()
        cols.setSpacing(8)
        self._cols = cols

        self._left_panel = self._build_left_library_panel()
        cols.addWidget(self._left_panel, 0)

        self._center_panel = self._build_center_player_panel()
        cols.addWidget(self._center_panel, 1)

        self._right_panel = self._build_right_inspector_panel()
        cols.addWidget(self._right_panel, 0)

        page.addLayout(cols, 1)

        self._status = QLabel("Khung mẫu đang hoạt động — áp dụng cho toàn bộ video khi kết xuất.")
        self._status.setObjectName("MutedLabel")
        self._status.setWordWrap(True)
        page.addWidget(self._status)

        undo = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo.activated.connect(self._undo_action)
        redo = QShortcut(QKeySequence.StandardKey.Redo, self)
        redo.activated.connect(self._redo_action)
        del_sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        del_sc.activated.connect(self._remove_image)
        backspace_sc = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        backspace_sc.activated.connect(self._remove_image)
        self._undo_shortcut = undo
        self._redo_shortcut = redo
        self._delete_shortcut = del_sc
        self._backspace_shortcut = backspace_sc

        self._canvas.layout_changed.connect(self._on_layout_changed)
        self._canvas.layer_structure_changed.connect(lambda: self._rebuild_list())
        self._canvas.selection_changed.connect(self._on_selection)
        self._canvas.edit_committed.connect(self._commit_history)
        self._canvas.phase_changed.connect(self._on_phase)

        for slider in (self._width, self._pos_x, self._pos_y,
                       self._rotate, self._opacity, self._scale,
                       self._sub_h_slider, self._wave_h_slider):
            slider.sliderReleased.connect(self._commit_history)
        for spin in (self._width_spin, self._pos_x_spin, self._pos_y_spin,
                     self._rotate_spin, self._opacity_spin, self._scale_spin,
                     self._sub_h_spin, self._sub_font_size_spin, self._wave_h_spin):
            spin.editingFinished.connect(self._commit_history)

        self._canvas.set_playing(False)
        self._on_phase(0.0)
        self._sync_controls()

    # ── toolbar ──────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("EditorToolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)

        self._undo_btn = icon_button("rotate-ccw", "Hoàn tác (Ctrl+Z)", size=13, side=28)
        self._undo_btn.clicked.connect(self._undo_action)
        row.addWidget(self._undo_btn)

        self._redo_btn = icon_button("rotate-cw", "Làm lại (Ctrl+Y)", size=13, side=28)
        self._redo_btn.clicked.connect(self._redo_action)
        row.addWidget(self._redo_btn)

        row.addWidget(self._toolbar_sep())

        lbl = QLabel("Tỉ lệ:")
        lbl.setObjectName("MutedLabel")
        row.addWidget(lbl)

        self._ratio_combo = QComboBox()
        self._ratio_combo.addItem("16:9 Ngang", "16:9")
        self._ratio_combo.addItem("9:16 Shorts/TikTok", "9:16")
        self._ratio_combo.addItem("1:1 Vuông", "1:1")
        self._ratio_combo.addItem("4:5 Dọc", "4:5")
        self._ratio_combo.setFixedWidth(136)
        self._ratio_combo.currentIndexChanged.connect(self._on_ratio_changed)
        row.addWidget(self._ratio_combo)

        row.addWidget(self._toolbar_sep())

        self._btn_grid = icon_button("grid", "Bật/Tắt đường lưới", size=13, side=28)
        self._btn_grid.setCheckable(True)
        self._btn_grid.setChecked(True)
        self._btn_grid.clicked.connect(self._toggle_grid)
        row.addWidget(self._btn_grid)

        self._btn_safe = icon_button("maximize", "Bật/Tắt vùng an toàn & nút Shorts", size=13, side=28)
        self._btn_safe.setCheckable(True)
        self._btn_safe.setChecked(True)
        self._btn_safe.clicked.connect(self._toggle_safe)
        row.addWidget(self._btn_safe)

        self._btn_center = icon_button("crosshair", "Bật/Tắt căn từ tính bắt dính tâm", size=13, side=28)
        self._btn_center.setCheckable(True)
        self._btn_center.setChecked(True)
        self._btn_center.clicked.connect(self._toggle_center_guides)
        row.addWidget(self._btn_center)

        self._update_view_btn_styles()

        row.addStretch(1)

        self._history_label = QLabel("Chưa có thay đổi")
        self._history_label.setObjectName("MutedLabel")
        row.addWidget(self._history_label)

        self._reset_btn = QPushButton("Đặt lại")
        self._reset_btn.setObjectName("smallButton")
        self._reset_btn.setFixedHeight(28)
        self._reset_btn.setStyleSheet(
            "QPushButton#smallButton { padding: 4px 12px; font-size: 8.5pt; font-weight: 500; border-radius: 6px; }"
        )
        set_icon(self._reset_btn, "refresh", "muted", 13)
        self._reset_btn.setToolTip("Khôi phục toàn bộ khung mẫu về mặc định.")
        self._reset_btn.clicked.connect(self._reset)
        row.addWidget(self._reset_btn)

        self._save_btn = QPushButton("Lưu")
        self._save_btn.setObjectName("primaryButton")
        self._save_btn.setFixedHeight(28)
        self._save_btn.setStyleSheet(
            "QPushButton#primaryButton { padding: 4px 14px; font-size: 8.5pt; font-weight: 600; border-radius: 6px; }"
        )
        set_icon(self._save_btn, "save", "white", 13)
        self._save_btn.setToolTip("Lưu lại thiết kế khung mẫu vào video/frame của project đang chọn.")
        self._save_btn.clicked.connect(self._manual_save_project)
        row.addWidget(self._save_btn)
        return bar

    @staticmethod
    def _toolbar_sep() -> QFrame:
        sep = QFrame()
        sep.setObjectName("ToolbarSep")
        sep.setFixedWidth(1)
        sep.setFixedHeight(16)
        return sep

    # ── 1. Left: library / presets / stickers ───────────────────────────────
    def _build_left_library_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("EditorPanel")
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(265)
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(4)

        title = QLabel("Thư viện & Lớp")
        title.setObjectName("PanelTitle")
        vbox.addWidget(title)

        tabs = QTabWidget()
        self._library_tabs = tabs
        tabs.setDocumentMode(True)
        tabs.setUsesScrollButtons(False)
        vbox.addWidget(tabs, 1)

        # Tab 1: layers
        layers_tab = QWidget()
        l_box = QVBoxLayout(layers_tab)
        l_box.setContentsMargins(0, 4, 0, 0)
        l_box.setSpacing(6)

        layer_actions = QHBoxLayout()
        layer_actions.setSpacing(3)
        self._add_button = QPushButton("Thêm ảnh")
        set_icon(self._add_button, "plus", "accent", 12)
        self._add_button.clicked.connect(self._add_image)
        self._add_button.hide()

        self._up_button = icon_button("chevron-up", "Lên 1 lớp", size=12, side=26)
        self._up_button.clicked.connect(lambda: self._move_layer(-1))
        layer_actions.addWidget(self._up_button)

        self._down_button = icon_button("chevron-down", "Xuống 1 lớp", size=12, side=26)
        self._down_button.clicked.connect(lambda: self._move_layer(1))
        layer_actions.addWidget(self._down_button)

        self._dup_button = icon_button("copy", "Nhân bản", size=12, side=26)
        self._dup_button.clicked.connect(self._duplicate_layer)
        layer_actions.addWidget(self._dup_button)

        self._eye_button = icon_button("eye", "Ẩn / Hiện lớp", size=12, side=26)
        self._eye_button.clicked.connect(self._toggle_visibility)
        layer_actions.addWidget(self._eye_button)

        self._lock_button = icon_button("lock", "Khóa / Mở khóa", size=12, side=26)
        self._lock_button.clicked.connect(self._toggle_lock)
        layer_actions.addWidget(self._lock_button)

        self._remove_button = icon_button("trash", "Xoá lớp", role="danger", size=12, side=26)
        self._remove_button.clicked.connect(self._remove_image)
        layer_actions.addWidget(self._remove_button)

        layer_actions.addStretch(1)

        l_box.addLayout(layer_actions)

        self._image_list = QListWidget()
        self._image_list.setObjectName("LayerList")
        self._image_list.setIconSize(QSize(18, 18))
        self._image_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._image_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._image_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._image_list.currentRowChanged.connect(self._on_list_row)
        orig_list_key = self._image_list.keyPressEvent
        def _list_key_press(event: QKeyEvent) -> None:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._remove_image()
                return
            orig_list_key(event)
        self._image_list.keyPressEvent = _list_key_press
        self._image_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._image_list.customContextMenuRequested.connect(self._on_layer_list_context_menu)
        l_box.addWidget(self._image_list, 1)

        bg_group = QGroupBox("Nền xem trước")
        bg_form = QVBoxLayout(bg_group)
        bg_form.setContentsMargins(6, 6, 6, 6)
        bg_form.setSpacing(4)
        bg_row = QHBoxLayout()
        bg_row.setSpacing(4)
        self._preview_button = QPushButton("Chọn nền...")
        set_icon(self._preview_button, "image", "accent", 12)
        self._preview_button.clicked.connect(self._pick_preview)
        bg_row.addWidget(self._preview_button, 1)
        self._preview_clear = icon_button("x", "Bỏ ảnh nền", size=12, side=26)
        self._preview_clear.clicked.connect(self._clear_preview)
        bg_row.addWidget(self._preview_clear)
        bg_form.addLayout(bg_row)
        l_box.addWidget(bg_group)

        tabs.addTab(layers_tab, "Lớp")

        # Tab 2: layout presets
        presets_tab = QWidget()
        p_scroll = QScrollArea()
        p_scroll.setWidgetResizable(True)
        p_scroll.setFrameShape(QFrame.Shape.NoFrame)
        p_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        p_inner = QWidget()
        p_box = QVBoxLayout(p_inner)
        p_box.setContentsMargins(0, 4, 0, 0)
        p_box.setSpacing(5)

        p_hint = QLabel("Bấm chọn mẫu áp dụng:")
        p_hint.setObjectName("MutedLabel")
        p_hint.setWordWrap(True)
        p_box.addWidget(p_hint)

        self._preset_buttons: List[QPushButton] = []
        for preset in self._PRESETS:
            btn = QPushButton(f"{preset['name']}\n{preset['desc']}")
            btn.setObjectName("presetCard")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(38)
            btn.clicked.connect(lambda _c=False, p=preset: self._apply_preset(p))
            p_box.addWidget(btn)
            self._preset_buttons.append(btn)
        p_box.addStretch(1)
        p_scroll.setWidget(p_inner)

        presets_layout = QVBoxLayout(presets_tab)
        presets_layout.setContentsMargins(0, 0, 0, 0)
        presets_layout.addWidget(p_scroll)
        tabs.addTab(presets_tab, "Mẫu")

        # Tab 3: image library
        stickers_tab = QWidget()
        s_scroll = QScrollArea()
        s_scroll.setWidgetResizable(True)
        s_scroll.setFrameShape(QFrame.Shape.NoFrame)
        s_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        s_inner = QWidget()
        s_box = QVBoxLayout(s_inner)
        s_box.setContentsMargins(0, 4, 0, 0)
        s_box.setSpacing(5)

        s_hint = QLabel("Chèn nhanh ảnh:")
        s_hint.setObjectName("MutedLabel")
        s_box.addWidget(s_hint)

        stickers_grid = QGridLayout()
        stickers_grid.setSpacing(4)
        presets = [
            ("Sub Đỏ", "ui/assets/images/subscribe.png", 0, 0),
            ("Sub Chuông", "ui/assets/images/subscribe_bell.png", 0, 1),
            ("YouTube", "ui/assets/images/subscribe_youtube.png", 1, 0),
            ("Chấm Audio", "ui/assets/images/cham_audio_logo.png", 1, 1),
            ("Like & Share", "assets/stickers/like.png", 2, 0),
            ("Chuông", "assets/stickers/bell.png", 2, 1),
            ("Hot Viral", "assets/stickers/hot.png", 3, 0),
            ("Cảnh báo", "assets/stickers/warning.png", 3, 1),
            ("Mèo Avatar", "assets/stickers/cat_avatar.png", 4, 0),
        ]
        for title_text, path, r, c in presets:
            btn = QPushButton(title_text)
            btn.setObjectName("presetCard")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(34)
            asset = _resolve_asset(path)
            if asset is not None:
                btn.setIcon(QIcon(str(asset)))
                btn.setIconSize(QSize(20, 20))
            btn.clicked.connect(lambda _c=False, p=path: self._insert_preset(p))
            stickers_grid.addWidget(btn, r, c)

        s_box.addLayout(stickers_grid)

        btn_more = QPushButton("Thêm ảnh từ file...")
        set_icon(btn_more, "plus", "accent", 12)
        btn_more.clicked.connect(self._add_image)
        s_box.addWidget(btn_more)

        s_box.addStretch(1)
        s_scroll.setWidget(s_inner)

        stickers_layout = QVBoxLayout(stickers_tab)
        stickers_layout.setContentsMargins(0, 0, 0, 0)
        stickers_layout.addWidget(s_scroll)
        tabs.addTab(stickers_tab, "Ảnh")

        return panel

    # ── 2. Center: canvas + timeline ────────────────────────────────────────
    def _build_center_player_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(6)

        self._canvas_container = CanvasContainer(self._canvas, panel)
        vbox.addWidget(self._canvas_container, 1)

        bar = QFrame()
        bar.setObjectName("PlaybackBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)

        self._play_btn = icon_button(
            "play", "Phát/Tạm dừng mô phỏng sóng âm", role="accent", size=13, side=28)
        self._play_btn.clicked.connect(self._toggle_playback)
        row.addWidget(self._play_btn)

        self._time_label = QLabel("00:00 / 00:12")
        self._time_label.setObjectName("Timecode")
        row.addWidget(self._time_label)

        self._timeline = QSlider(Qt.Orientation.Horizontal)
        self._timeline.setObjectName("TimelineSlider")
        self._timeline.setRange(0, 1000)
        self._timeline.setValue(0)
        self._timeline.setSingleStep(5)
        self._timeline.valueChanged.connect(self._on_timeline)
        row.addWidget(self._timeline, 1)

        self._duration_label = QLabel(_format_time(PREVIEW_SECONDS))
        self._duration_label.setObjectName("MutedLabel")
        row.addWidget(self._duration_label)

        vbox.addWidget(bar)
        return panel

    # ── 3. Right: inspector ─────────────────────────────────────────────────
    def _build_right_inspector_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("EditorPanel")
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(340)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        heading = QLabel("Thuộc tính")
        heading.setObjectName("PanelTitle")
        heading.setContentsMargins(8, 6, 8, 2)
        outer.addWidget(heading)

        scroll = QScrollArea()
        scroll.setObjectName("InspectorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(8, 4, 12, 8)
        vbox.setSpacing(6)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self._inspector_title = QLabel("Chọn một đối tượng để chỉnh sửa")
        self._inspector_title.setObjectName("FieldLabel")
        self._inspector_title.setWordWrap(True)
        self._paint_inspector_title()
        vbox.addWidget(self._inspector_title)

        # Group 1: Transform
        trans_box = QGroupBox("Biến đổi (Transform)")
        t_layout = QVBoxLayout(trans_box)
        t_layout.setContentsMargins(6, 8, 6, 6)
        t_layout.setSpacing(4)

        # Scale / Width
        self._width_label = QLabel("Kích thước (Scale): 18%")
        self._width_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._width_label)
        w_row = QHBoxLayout()
        w_row.setSpacing(4)
        self._width = QSlider(Qt.Orientation.Horizontal)
        self._width.setRange(2, 300)
        self._width.setValue(18)
        self._width.valueChanged.connect(self._on_width_slider)
        w_row.addWidget(self._width, 1)
        self._width_spin = QSpinBox()
        self._width_spin.setRange(2, 300)
        self._width_spin.setValue(18)
        self._width_spin.setSuffix("%")
        self._width_spin.setFixedWidth(68)
        self._width_spin.setFixedHeight(24)
        self._width_spin.valueChanged.connect(self._on_width_spin)
        w_row.addWidget(self._width_spin)
        t_layout.addLayout(w_row)

        # Pos X
        self._pos_x_label = QLabel("Vị trí X: 5%")
        self._pos_x_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._pos_x_label)
        x_row = QHBoxLayout()
        x_row.setSpacing(4)
        self._pos_x = QSlider(Qt.Orientation.Horizontal)
        self._pos_x.setRange(-150, 200)
        self._pos_x.setValue(5)
        self._pos_x.valueChanged.connect(self._on_pos_x_slider)
        x_row.addWidget(self._pos_x, 1)
        self._pos_x_spin = QSpinBox()
        self._pos_x_spin.setRange(-150, 200)
        self._pos_x_spin.setValue(5)
        self._pos_x_spin.setSuffix("%")
        self._pos_x_spin.setFixedWidth(68)
        self._pos_x_spin.setFixedHeight(24)
        self._pos_x_spin.valueChanged.connect(self._on_pos_x_spin)
        x_row.addWidget(self._pos_x_spin)
        btn_center_x = icon_button("maximize", "Căn giữa X (50%)", size=12, side=24)
        btn_center_x.clicked.connect(lambda: self._canvas_action("center_x"))
        x_row.addWidget(btn_center_x)
        t_layout.addLayout(x_row)

        # Pos Y
        self._pos_y_label = QLabel("Vị trí Y: 5%")
        self._pos_y_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._pos_y_label)
        y_row = QHBoxLayout()
        y_row.setSpacing(4)
        self._pos_y = QSlider(Qt.Orientation.Horizontal)
        self._pos_y.setRange(-150, 200)
        self._pos_y.setValue(5)
        self._pos_y.valueChanged.connect(self._on_pos_y_slider)
        y_row.addWidget(self._pos_y, 1)
        self._pos_y_spin = QSpinBox()
        self._pos_y_spin.setRange(-150, 200)
        self._pos_y_spin.setValue(5)
        self._pos_y_spin.setSuffix("%")
        self._pos_y_spin.setFixedWidth(68)
        self._pos_y_spin.setFixedHeight(24)
        self._pos_y_spin.valueChanged.connect(self._on_pos_y_spin)
        y_row.addWidget(self._pos_y_spin)
        btn_center_y = icon_button("maximize", "Căn giữa Y (50%)", size=12, side=24)
        btn_center_y.clicked.connect(lambda: self._canvas_action("center_y"))
        y_row.addWidget(btn_center_y)
        t_layout.addLayout(y_row)

        # Rotate
        self._rotate_label = QLabel("Góc xoay: 0°")
        self._rotate_label.setObjectName("MutedLabel")
        t_layout.addWidget(self._rotate_label)
        rot_row = QHBoxLayout()
        rot_row.setSpacing(4)
        self._rotate = QSlider(Qt.Orientation.Horizontal)
        self._rotate.setRange(-180, 180)
        self._rotate.setValue(0)
        self._rotate.valueChanged.connect(self._on_rotate_slider)
        rot_row.addWidget(self._rotate, 1)
        self._rotate_spin = QSpinBox()
        self._rotate_spin.setRange(-180, 180)
        self._rotate_spin.setValue(0)
        self._rotate_spin.setSuffix("°")
        self._rotate_spin.setFixedWidth(64)
        self._rotate_spin.setFixedHeight(24)
        self._rotate_spin.valueChanged.connect(self._on_rotate_spin)
        rot_row.addWidget(self._rotate_spin)
        btn_reset_rot = icon_button("rotate-ccw", "Về 0°", size=11, side=24)
        btn_reset_rot.clicked.connect(lambda: self._canvas_action("reset_rotation"))
        rot_row.addWidget(btn_reset_rot)
        t_layout.addLayout(rot_row)

        # Quick Alignments
        align_label = QLabel("Căn lề nhanh:")
        align_label.setObjectName("MutedLabel")
        t_layout.addWidget(align_label)

        align_grid = QGridLayout()
        align_grid.setSpacing(3)
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
            btn.setObjectName("smallButton")
            btn.setFixedHeight(24)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _c=False, m=mode: self._canvas_action(m))
            align_grid.addWidget(btn, r, c)

        btn_center_both = QPushButton("Chính giữa")
        btn_center_both.setObjectName("smallButton")
        btn_center_both.setFixedHeight(24)
        set_icon(btn_center_both, "crosshair", "accent", 11)
        btn_center_both.clicked.connect(lambda: self._canvas_action("center_both"))
        align_grid.addWidget(btn_center_both, 2, 0, 1, 3)
        t_layout.addLayout(align_grid)

        # Flip
        flip_row = QHBoxLayout()
        flip_row.setSpacing(4)
        self._flip_h_btn = QPushButton("Lật ngang")
        self._flip_h_btn.setObjectName("smallButton")
        self._flip_h_btn.setFixedHeight(24)
        self._flip_h_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._flip_h_btn.clicked.connect(lambda: self._canvas_action("flip_h"))
        flip_row.addWidget(self._flip_h_btn)

        self._flip_v_btn = QPushButton("Lật dọc")
        self._flip_v_btn.setObjectName("smallButton")
        self._flip_v_btn.setFixedHeight(24)
        self._flip_v_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._flip_v_btn.clicked.connect(lambda: self._canvas_action("flip_v"))
        flip_row.addWidget(self._flip_v_btn)
        t_layout.addLayout(flip_row)

        self._del_img_btn = QPushButton("Xoá ảnh này khỏi khung")
        self._del_img_btn.setObjectName("smallButton")
        self._del_img_btn.setFixedHeight(26)
        set_icon(self._del_img_btn, "trash", "danger", 12)
        self._del_img_btn.clicked.connect(self._remove_image)
        t_layout.addWidget(self._del_img_btn)

        vbox.addWidget(trans_box)

        # Group 2: Opacity
        blend_box = QGroupBox("Độ đậm (Opacity)")
        b_layout = QVBoxLayout(blend_box)
        b_layout.setContentsMargins(6, 8, 6, 6)
        b_layout.setSpacing(4)
        self._opacity_label = QLabel("Độ đậm: 100%")
        self._opacity_label.setObjectName("MutedLabel")
        b_layout.addWidget(self._opacity_label)
        op_row = QHBoxLayout()
        op_row.setSpacing(4)
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(10, 100)
        self._opacity.setValue(100)
        self._opacity.valueChanged.connect(self._on_opacity_slider)
        op_row.addWidget(self._opacity, 1)
        self._opacity_spin = QSpinBox()
        self._opacity_spin.setRange(10, 100)
        self._opacity_spin.setValue(100)
        self._opacity_spin.setSuffix("%")
        self._opacity_spin.setFixedWidth(68)
        self._opacity_spin.setFixedHeight(24)
        self._opacity_spin.valueChanged.connect(self._on_opacity_spin)
        op_row.addWidget(self._opacity_spin)
        b_layout.addLayout(op_row)
        vbox.addWidget(blend_box)

        # Group 3: Context properties
        custom_box = QGroupBox("Thuộc tính riêng")
        c_layout = QVBoxLayout(custom_box)
        c_layout.setContentsMargins(6, 8, 6, 6)
        c_layout.setSpacing(6)

        # Subtitle properties
        self._scale_label = QLabel("Tỉ lệ cỡ chữ: 100%")
        self._scale_label.setObjectName("MutedLabel")
        c_layout.addWidget(self._scale_label)
        sc_row = QHBoxLayout()
        sc_row.setSpacing(4)
        self._scale = QSlider(Qt.Orientation.Horizontal)
        self._scale.setRange(50, 200)
        self._scale.setValue(100)
        self._scale.valueChanged.connect(self._on_scale_slider)
        sc_row.addWidget(self._scale, 1)
        self._scale_spin = QSpinBox()
        self._scale_spin.setRange(50, 200)
        self._scale_spin.setValue(100)
        self._scale_spin.setSuffix("%")
        self._scale_spin.setFixedWidth(68)
        self._scale_spin.setFixedHeight(24)
        self._scale_spin.valueChanged.connect(self._on_scale_spin)
        sc_row.addWidget(self._scale_spin)
        c_layout.addLayout(sc_row)

        self._sub_h_label = QLabel("Chiều cao khung chữ: 14%")
        self._sub_h_label.setObjectName("MutedLabel")
        c_layout.addWidget(self._sub_h_label)
        sh_row = QHBoxLayout()
        sh_row.setSpacing(4)
        self._sub_h_slider = QSlider(Qt.Orientation.Horizontal)
        self._sub_h_slider.setRange(3, 80)
        self._sub_h_slider.setValue(14)
        self._sub_h_slider.valueChanged.connect(self._on_sub_h_slider)
        sh_row.addWidget(self._sub_h_slider, 1)
        self._sub_h_spin = QSpinBox()
        self._sub_h_spin.setRange(3, 80)
        self._sub_h_spin.setValue(14)
        self._sub_h_spin.setSuffix("%")
        self._sub_h_spin.setFixedWidth(68)
        self._sub_h_spin.setFixedHeight(24)
        self._sub_h_spin.valueChanged.connect(self._on_sub_h_spin)
        sh_row.addWidget(self._sub_h_spin)
        c_layout.addLayout(sh_row)

        self._sub_font_label = QLabel("Phông chữ phụ đề:")
        self._sub_font_label.setObjectName("MutedLabel")
        c_layout.addWidget(self._sub_font_label)
        self._sub_font_combo = QFontComboBox()
        self._sub_font_combo.setFixedHeight(28)
        self._sub_font_combo.currentFontChanged.connect(self._on_sub_font_changed)
        c_layout.addWidget(self._sub_font_combo)

        fs_row = QHBoxLayout()
        fs_row.setSpacing(4)
        self._sub_font_size_label = QLabel("Cỡ chữ chuẩn:")
        self._sub_font_size_label.setObjectName("MutedLabel")
        fs_row.addWidget(self._sub_font_size_label, 1)
        self._sub_font_size_spin = QSpinBox()
        self._sub_font_size_spin.setRange(14, 120)
        self._sub_font_size_spin.setValue(44)
        self._sub_font_size_spin.setSuffix(" px")
        self._sub_font_size_spin.setFixedWidth(72)
        self._sub_font_size_spin.setFixedHeight(24)
        self._sub_font_size_spin.valueChanged.connect(self._on_sub_font_size_changed)
        fs_row.addWidget(self._sub_font_size_spin)
        c_layout.addLayout(fs_row)

        lbl_text = QLabel("Xem trước phụ đề mẫu:")
        lbl_text.setObjectName("MutedLabel")
        c_layout.addWidget(lbl_text)
        self._sample_text_edit = QLineEdit(_DEFAULT_SAMPLE_TEXT)
        self._sample_text_edit.setFixedHeight(28)
        self._sample_text_edit.setPlaceholderText("Gõ văn bản mẫu...")
        self._sample_text_edit.textChanged.connect(self._on_sample_text_changed)
        c_layout.addWidget(self._sample_text_edit)

        # Waveform controls
        self._wave = QCheckBox("Hiện dải sóng âm thanh")
        self._wave.toggled.connect(self._on_wave_toggled)
        c_layout.addWidget(self._wave)

        self._wave_h_label = QLabel("Chiều cao sóng: 8%")
        self._wave_h_label.setObjectName("MutedLabel")
        c_layout.addWidget(self._wave_h_label)
        wh_row = QHBoxLayout()
        wh_row.setSpacing(4)
        self._wave_h_slider = QSlider(Qt.Orientation.Horizontal)
        self._wave_h_slider.setRange(2, 35)
        self._wave_h_slider.setValue(8)
        self._wave_h_slider.valueChanged.connect(self._on_wave_h_slider)
        wh_row.addWidget(self._wave_h_slider, 1)
        self._wave_h_spin = QSpinBox()
        self._wave_h_spin.setRange(2, 35)
        self._wave_h_spin.setValue(8)
        self._wave_h_spin.setSuffix("%")
        self._wave_h_spin.setFixedWidth(68)
        self._wave_h_spin.setFixedHeight(24)
        self._wave_h_spin.valueChanged.connect(self._on_wave_h_spin)
        wh_row.addWidget(self._wave_h_spin)
        c_layout.addLayout(wh_row)

        self._color_button = QPushButton("Chọn màu sóng âm")
        self._color_button.setFixedHeight(28)
        self._color_button.clicked.connect(self._pick_color)
        c_layout.addWidget(self._color_button)

        palette_row = QHBoxLayout()
        palette_row.setSpacing(4)
        for hex_code, color_name in _QUICK_COLORS:
            btn_col = QPushButton()
            btn_col.setToolTip(f"Màu {color_name} ({hex_code})")
            btn_col.setFixedSize(20, 20)
            btn_col.setStyleSheet(f"background:{hex_code}; border-radius:4px; border:1px solid {theme.BORDER_STRONG};")
            btn_col.clicked.connect(lambda _c=False, h=hex_code: self._set_wave_color(h))
            palette_row.addWidget(btn_col)
        palette_row.addStretch(1)
        c_layout.addLayout(palette_row)

        self._replace_img_btn = QPushButton("Đổi file ảnh...")
        self._replace_img_btn.setFixedHeight(28)
        set_icon(self._replace_img_btn, "image", "accent", 12)
        self._replace_img_btn.clicked.connect(self._replace_image)
        c_layout.addWidget(self._replace_img_btn)

        vbox.addWidget(custom_box)
        vbox.addStretch(1)
        return panel

    # ── settings & project API ──────────────────────────────────────────────
    def set_project(self, manager: Any) -> None:
        self._manager = manager
        if manager is not None and hasattr(manager, "load_frame_layout"):
            saved = manager.load_frame_layout()
            if saved is not None:
                self._layout = saved
                self._canvas.set_layout(self._layout)
                self._canvas.reload_assets()
                self._undo_stack.clear()
                self._redo_stack.clear()
                self._snapshot = self._layout.to_dict()
                self._sync_history_buttons()
                self._sync_controls()
                self._rebuild_list()
                self._paint_color_button()
                paint_status(self._status, "success")
                proj_name = getattr(manager, "project_dir", Path(".")).name
                self._status.setText(f"Đã nạp khung mẫu từ video/frame ({proj_name}).")
                if self._settings is not None:
                    self._settings["frame"] = self._layout.to_dict()
                return
        if manager is not None and getattr(self._layout, "enabled", False):
            self._save_to_current_project()

    def _save_to_current_project(self) -> None:
        if not self._manager:
            return
        try:
            if hasattr(self._manager, "save_frame_layout"):
                self._manager.save_frame_layout(self._layout)
                self._canvas.reload_assets()
            if self._settings is not None:
                self._settings["frame"] = self._layout.to_dict()
            paint_status(self._status, "success")
            proj_name = getattr(self._manager, "project_dir", Path(".")).name
            self._status.setText(f"Đã lưu khung vào video/frame ({proj_name}).")
        except Exception as exc:
            logger.warning("Không thể lưu khung mẫu vào project: %s", exc)

    def _manual_save_project(self) -> None:
        if not self._manager:
            if self._settings is not None:
                self._settings["frame"] = self._layout.to_dict()
            paint_status(self._status, "warning")
            self._status.setText("Chưa chọn project. Khung đã được lưu vào cấu hình tạm.")
            return
        try:
            self._save_to_current_project()
            proj_name = getattr(self._manager, "project_dir", Path(".")).name
            paint_status(self._status, "success")
            self._status.setText(f"✓ Đã lưu thành công khung mẫu vào video/frame ({proj_name}).")
            self._save_btn.setText("Đã lưu ✓")
            set_icon(self._save_btn, "check", "white", 13)
            QTimer.singleShot(1500, self._restore_save_btn)
        except Exception as exc:
            logger.warning("Không thể lưu khung mẫu vào project: %s", exc)
            paint_status(self._status, "danger")
            self._status.setText(f"Lỗi khi lưu khung mẫu: {exc}")

    def _restore_save_btn(self) -> None:
        self._save_btn.setText("Lưu")
        set_icon(self._save_btn, "save", "white", 13)

    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        if self._manager is not None and hasattr(self._manager, "load_frame_layout"):
            saved = self._manager.load_frame_layout()
            if saved is not None:
                self._layout = saved
                self._canvas.set_layout(self._layout)
                self._canvas.reload_assets()
                self._undo_stack.clear()
                self._redo_stack.clear()
                self._snapshot = self._layout.to_dict()
                self._sync_history_buttons()
                self._sync_controls()
                self._rebuild_list()
                self._paint_color_button()
                paint_status(self._status, "success")
                proj_name = getattr(self._manager, "project_dir", Path(".")).name
                self._status.setText(f"Đã nạp khung mẫu từ video/frame ({proj_name}).")
                return

        self._layout = FrameLayout.from_settings(settings)
        self._canvas.set_layout(self._layout)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._snapshot = self._layout.to_dict()
        self._sync_history_buttons()
        self._sync_controls()
        self._rebuild_list()
        self._paint_color_button()
        paint_status(self._status, "success")
        self._status.setText("Khung mẫu đang hoạt động — áp dụng cho toàn bộ video khi kết xuất.")

    def apply_to(self, settings: Dict[str, Any]) -> None:
        settings["frame"] = self._layout.to_dict()

    def apply_theme(self) -> None:
        self._paint_color_button()
        self._paint_inspector_title()
        self._rebuild_list()
        self._canvas.update()
        paint_status(self._status, "success")

    # ── history ─────────────────────────────────────────────────────────────
    def _state(self) -> Dict[str, Any]:
        return self._layout.to_dict()

    def _commit_history(self) -> None:
        current = self._state()
        if current == self._snapshot:
            return
        self._undo_stack.append(self._snapshot)
        if len(self._undo_stack) > 60:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._snapshot = current
        self._sync_history_buttons()
        self._save_to_current_project()

    def _sync_history_buttons(self) -> None:
        self._undo_btn.setEnabled(bool(self._undo_stack))
        self._redo_btn.setEnabled(bool(self._redo_stack))
        if self._undo_stack:
            self._history_label.setText(f"{len(self._undo_stack)} bước hoàn tác")
        else:
            self._history_label.setText("Chưa có thay đổi")

    def _undo_action(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self._state())
        self._apply_state(self._undo_stack.pop())

    def _redo_action(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(self._state())
        self._apply_state(self._redo_stack.pop())

    def _apply_state(self, state: Dict[str, Any]) -> None:
        self._layout = FrameLayout.from_dict(state)
        self._canvas.set_layout(self._layout)
        self._snapshot = self._layout.to_dict()
        self._sync_controls()
        self._rebuild_list()
        self._paint_color_button()
        self._sync_history_buttons()
        self._canvas.update()
        self._save_to_current_project()

    # ── state sync ──────────────────────────────────────────────────────────
    def _sync_controls(self) -> None:
        self._block = True
        self._wave.setChecked(self._layout.waveform)

        scale_val = int(round(self._layout.subtitle_scale * 100))
        self._scale.setValue(scale_val)
        self._scale_spin.setValue(scale_val)
        self._scale_label.setText(f"Tỉ lệ cỡ chữ: {scale_val}%")

        sub_h_val = int(round(getattr(self._layout, "subtitle_h", 0.14) * 100))
        self._sub_h_slider.setValue(sub_h_val)
        self._sub_h_spin.setValue(sub_h_val)
        self._sub_h_label.setText(f"Chiều cao khung chữ: {sub_h_val}%")

        font_fam = getattr(self._layout, "subtitle_font", "Arial") or "Arial"
        self._sub_font_combo.setCurrentFont(QFont(font_fam))

        font_sz = int(getattr(self._layout, "subtitle_font_size", 44) or 44)
        self._sub_font_size_spin.setValue(font_sz)

        wave_h_val = int(round(self._layout.wave_h * 100))
        self._wave_h_slider.setValue(wave_h_val)
        self._wave_h_spin.setValue(wave_h_val)
        self._wave_h_label.setText(f"Chiều cao sóng: {wave_h_val}%")

        ratio = self._layout.aspect_ratio or "16:9"
        idx = self._ratio_combo.findData(ratio)
        if idx >= 0:
            self._ratio_combo.setCurrentIndex(idx)
        self._canvas.aspect_ratio_mode = ratio

        self._preview_clear.setEnabled(bool(self._layout.preview))
        self._color_button.setEnabled(self._layout.waveform)
        self._block = False
        self._paint_color_button()
        self._on_selection(*self._canvas.selection())

    def _layer_icon(self, kind: str, image: Optional[FrameImage] = None) -> QIcon:
        if image is not None:
            if Path(image.path).is_file():
                source = QPixmap(image.path)
                if not source.isNull():
                    return QIcon(source.scaled(
                        18, 18, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
            return QIcon(pixmap("image", theme.WARNING, 15))
        if kind == "video":
            return QIcon(pixmap("film", theme.icon_color("accent"), 15))
        name = "waveform" if kind == "wave" else "type"
        role = "accent" if kind == "wave" else "muted"
        return QIcon(pixmap(name, theme.icon_color(role), 15))

    def _rebuild_list(self, select: int = -1) -> None:
        self._block = True
        self._image_list.clear()

        layers = self._layout.ordered_layers()
        for kind, idx in layers:
            if kind == "subtitle":
                sub_vis = getattr(self._layout, "subtitle_visible", True)
                sub_text = "Phụ đề (Subtitle)" if sub_vis else "Phụ đề (Đang ẩn)"
                item = QListWidgetItem(self._layer_icon("subtitle"), sub_text)
                item.setToolTip("Lớp phụ đề tự động trên video.")
                item.setData(Qt.ItemDataRole.UserRole, ("subtitle", -1))
                self._image_list.addItem(item)
            elif kind == "wave":
                wave_text = "Sóng âm thanh" if self._layout.waveform else "Sóng âm thanh (Đang ẩn)"
                item = QListWidgetItem(self._layer_icon("wave"), wave_text)
                item.setToolTip("Dải sóng âm thanh chuyển động.")
                item.setData(Qt.ItemDataRole.UserRole, ("wave", -1))
                self._image_list.addItem(item)
            elif kind == "image" and 0 <= idx < len(self._layout.images):
                image = self._layout.images[idx]
                name_label = image.name
                if not getattr(image, "visible", True):
                    name_label += " [Ẩn]"
                if getattr(image, "locked", False):
                    name_label += " [Khóa]"
                item = QListWidgetItem(self._layer_icon("image", image), name_label)
                item.setToolTip(image.path)
                item.setData(Qt.ItemDataRole.UserRole, ("image", idx))
                self._image_list.addItem(item)
            elif kind == "video":
                vid_vis = getattr(self._layout, "video_visible", True)
                vid_text = "Video" if vid_vis else "Video (Đang ẩn)"
                if getattr(self._layout, "video_locked", False):
                    vid_text += " [Khóa]"
                item = QListWidgetItem(self._layer_icon("video"), vid_text)
                item.setToolTip("Khung hình video chính.")
                item.setData(Qt.ItemDataRole.UserRole, ("video", -1))
                self._image_list.addItem(item)

        if select >= 0:
            wanted = ("image", select)
        else:
            sel_kind, sel_idx = self._canvas.selection()
            wanted = (sel_kind, sel_idx if sel_kind == "image" else -1)

        target_row = -1
        if wanted[0]:
            for r in range(self._image_list.count()):
                it = self._image_list.item(r)
                if it is not None and it.data(Qt.ItemDataRole.UserRole) == wanted:
                    target_row = r
                    break
        if 0 <= target_row < self._image_list.count():
            self._image_list.setCurrentRow(target_row)
        self._block = False
        self._update_inspector()

    def _on_list_row(self, row: int) -> None:
        if self._block or row < 0:
            return
        it = self._image_list.item(row)
        if it is not None:
            data = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and len(data) == 2:
                self._canvas.set_selection(str(data[0]), int(data[1]))
                return
        layers = self._layout.ordered_layers()
        if 0 <= row < len(layers):
            self._canvas.set_selection(*layers[row])

    def _on_selection(self, kind: str, index: int) -> None:
        self._block = True
        wanted = (kind, index if kind == "image" else -1)
        target_row = -1
        if kind:
            for r in range(self._image_list.count()):
                it = self._image_list.item(r)
                if it is not None and it.data(Qt.ItemDataRole.UserRole) == wanted:
                    target_row = r
                    break
        self._image_list.setCurrentRow(target_row)
        self._block = False
        self._update_inspector()

    def _update_inspector(self) -> None:
        kind, index = self._canvas.selection()
        valid_img = (kind == "image" and 0 <= index < len(self._layout.images))
        layers = self._layout.ordered_layers()
        cur_pair = (kind, index if kind == "image" else -1)
        cur_pos = layers.index(cur_pair) if cur_pair in layers else -1

        self._remove_button.setEnabled(valid_img)
        self._del_img_btn.setEnabled(valid_img)
        self._del_img_btn.setVisible(valid_img)
        self._dup_button.setEnabled(valid_img)
        self._eye_button.setEnabled(valid_img or kind in ("wave", "subtitle", "video"))
        self._lock_button.setEnabled(valid_img or kind == "video")
        self._flip_h_btn.setEnabled(valid_img)
        self._flip_v_btn.setEnabled(valid_img)
        self._rotate.setEnabled(valid_img)
        self._rotate_spin.setEnabled(valid_img)
        self._replace_img_btn.setEnabled(valid_img)
        self._up_button.setEnabled(cur_pos > 0)
        self._down_button.setEnabled(0 <= cur_pos < len(layers) - 1)

        self._block = True
        if valid_img:
            image = self._layout.images[index]
            locked_tag = " (Đã khóa)" if getattr(image, "locked", False) else ""
            self._inspector_title.setText(f"Đang chọn: {image.name}{locked_tag}")

            w_val = int(round(image.width * 100))
            self._width.setEnabled(True)
            self._width.setValue(w_val)
            self._width_spin.setEnabled(True)
            self._width_spin.setValue(w_val)
            self._width_label.setText(f"Kích thước (Scale): {w_val}%")

            x_val = int(round(image.x * 100))
            self._pos_x.setEnabled(True)
            self._pos_x.setValue(x_val)
            self._pos_x_spin.setEnabled(True)
            self._pos_x_spin.setValue(x_val)
            self._pos_x_label.setText(f"Vị trí X: {x_val}%")

            y_val = int(round(image.y * 100))
            self._pos_y.setEnabled(True)
            self._pos_y.setValue(y_val)
            self._pos_y_spin.setEnabled(True)
            self._pos_y_spin.setValue(y_val)
            self._pos_y_label.setText(f"Vị trí Y: {y_val}%")

            rotation = int(round(float(getattr(image, "rotation", 0.0))))
            self._rotate.setValue(rotation)
            self._rotate_spin.setValue(rotation)
            self._rotate_label.setText(f"Góc xoay: {rotation}°")

            op_val = int(round(image.opacity * 100))
            self._opacity.setEnabled(True)
            self._opacity.setValue(op_val)
            self._opacity_spin.setEnabled(True)
            self._opacity_spin.setValue(op_val)
            self._opacity_label.setText(f"Độ đậm: {op_val}%")

            self._scale.setEnabled(False)
            self._scale_spin.setEnabled(False)
            self._sub_h_slider.setEnabled(False)
            self._sub_h_spin.setEnabled(False)
            self._sub_font_combo.setEnabled(False)
            self._sub_font_size_spin.setEnabled(False)
            self._sample_text_edit.setEnabled(False)
            self._wave_h_slider.setEnabled(False)
            self._wave_h_spin.setEnabled(False)
        elif kind == "subtitle":
            sub_vis = getattr(self._layout, "subtitle_visible", True)
            vis_tag = "" if sub_vis else " (Đang ẩn)"
            self._inspector_title.setText(f"Đang chọn: Phụ đề{vis_tag}")
            self._width.setEnabled(True)
            self._width_spin.setEnabled(True)
            sub_w_val = int(round(getattr(self._layout, "subtitle_w", 0.8) * 100))
            self._width.setValue(sub_w_val)
            self._width_spin.setValue(sub_w_val)
            self._width_label.setText(f"Chiều rộng khung chữ: {sub_w_val}%")

            x_val = int(round(self._layout.subtitle_x * 100))
            self._pos_x.setEnabled(True)
            self._pos_x.setValue(x_val)
            self._pos_x_spin.setEnabled(True)
            self._pos_x_spin.setValue(x_val)
            self._pos_x_label.setText(f"Vị trí X: {x_val}%")

            y_val = int(round(self._layout.subtitle_y * 100))
            self._pos_y.setEnabled(True)
            self._pos_y.setValue(y_val)
            self._pos_y_spin.setEnabled(True)
            self._pos_y_spin.setValue(y_val)
            self._pos_y_label.setText(f"Vị trí Y: {y_val}%")

            self._rotate.setValue(0)
            self._rotate_spin.setValue(0)
            self._rotate_label.setText("Góc xoay: 0°")

            self._opacity.setEnabled(False)
            self._opacity_spin.setEnabled(False)

            self._scale.setEnabled(True)
            self._scale_spin.setEnabled(True)
            sc_val = int(round(self._layout.subtitle_scale * 100))
            self._scale.setValue(sc_val)
            self._scale_spin.setValue(sc_val)
            self._scale_label.setText(f"Tỉ lệ cỡ chữ: {sc_val}%")

            self._sub_h_slider.setEnabled(True)
            self._sub_h_spin.setEnabled(True)
            sub_h_val = int(round(getattr(self._layout, "subtitle_h", 0.14) * 100))
            self._sub_h_slider.setValue(sub_h_val)
            self._sub_h_spin.setValue(sub_h_val)
            self._sub_h_label.setText(f"Chiều cao khung chữ: {sub_h_val}%")

            self._sub_font_combo.setEnabled(True)
            self._sub_font_combo.setCurrentFont(QFont(getattr(self._layout, "subtitle_font", "Arial") or "Arial"))

            self._sub_font_size_spin.setEnabled(True)
            self._sub_font_size_spin.setValue(int(getattr(self._layout, "subtitle_font_size", 44) or 44))

            self._sample_text_edit.setEnabled(True)
            self._wave_h_slider.setEnabled(False)
            self._wave_h_spin.setEnabled(False)
        elif kind == "wave":
            wave_tag = "" if self._layout.waveform else " (Đang ẩn)"
            self._inspector_title.setText(f"Đang chọn: Sóng âm thanh{wave_tag}")

            w_val = int(round(self._layout.wave_w * 100))
            self._width.setEnabled(True)
            self._width.setValue(w_val)
            self._width_spin.setEnabled(True)
            self._width_spin.setValue(w_val)
            self._width_label.setText(f"Độ rộng sóng: {w_val}%")

            x_val = int(round(self._layout.wave_x * 100))
            self._pos_x.setEnabled(True)
            self._pos_x.setValue(x_val)
            self._pos_x_spin.setEnabled(True)
            self._pos_x_spin.setValue(x_val)
            self._pos_x_label.setText(f"Vị trí X: {x_val}%")

            y_val = int(round(self._layout.wave_y * 100))
            self._pos_y.setEnabled(True)
            self._pos_y.setValue(y_val)
            self._pos_y_spin.setEnabled(True)
            self._pos_y_spin.setValue(y_val)
            self._pos_y_label.setText(f"Vị trí Y: {y_val}%")

            self._rotate.setValue(0)
            self._rotate_spin.setValue(0)
            self._rotate_label.setText("Góc xoay: 0°")

            self._opacity.setEnabled(False)
            self._opacity_spin.setEnabled(False)
            self._scale.setEnabled(False)
            self._scale_spin.setEnabled(False)
            self._sub_h_slider.setEnabled(False)
            self._sub_h_spin.setEnabled(False)
            self._sub_font_combo.setEnabled(False)
            self._sub_font_size_spin.setEnabled(False)
            self._sample_text_edit.setEnabled(False)

            self._wave_h_slider.setEnabled(True)
            self._wave_h_spin.setEnabled(True)
            wh_val = int(round(self._layout.wave_h * 100))
            self._wave_h_slider.setValue(wh_val)
            self._wave_h_spin.setValue(wh_val)
            self._wave_h_label.setText(f"Chiều cao sóng: {wh_val}%")
        elif kind == "video":
            vid_vis = getattr(self._layout, "video_visible", True)
            vid_locked = getattr(self._layout, "video_locked", False)
            vid_tag = "" if vid_vis else " (Đang ẩn)"
            if vid_locked:
                vid_tag += " (Đã khóa)"
            self._inspector_title.setText(f"Đang chọn: Video{vid_tag}")

            w_val = int(round(getattr(self._layout, "video_w", 1.0) * 100))
            self._width.setEnabled(not vid_locked)
            self._width.setValue(w_val)
            self._width_spin.setEnabled(not vid_locked)
            self._width_spin.setValue(w_val)
            self._width_label.setText(f"Kích thước khung Video: {w_val}%")

            x_val = int(round(getattr(self._layout, "video_x", 0.0) * 100))
            self._pos_x.setEnabled(not vid_locked)
            self._pos_x.setValue(x_val)
            self._pos_x_spin.setEnabled(not vid_locked)
            self._pos_x_spin.setValue(x_val)
            self._pos_x_label.setText(f"Vị trí X: {x_val}%")

            y_val = int(round(getattr(self._layout, "video_y", 0.0) * 100))
            self._pos_y.setEnabled(not vid_locked)
            self._pos_y.setValue(y_val)
            self._pos_y_spin.setEnabled(not vid_locked)
            self._pos_y_spin.setValue(y_val)
            self._pos_y_label.setText(f"Vị trí Y: {y_val}%")

            self._rotate.setValue(0)
            self._rotate_spin.setValue(0)
            self._rotate_label.setText("Góc xoay: 0°")

            self._opacity.setEnabled(False)
            self._opacity_spin.setEnabled(False)
            self._scale.setEnabled(False)
            self._scale_spin.setEnabled(False)
            self._sub_h_slider.setEnabled(False)
            self._sub_h_spin.setEnabled(False)
            self._sub_font_combo.setEnabled(False)
            self._sub_font_size_spin.setEnabled(False)
            self._sample_text_edit.setEnabled(False)
            self._wave_h_slider.setEnabled(False)
            self._wave_h_spin.setEnabled(False)
        else:
            self._inspector_title.setText("Chọn một đối tượng để chỉnh sửa")
            self._width.setEnabled(False)
            self._width_spin.setEnabled(False)
            self._pos_x.setEnabled(False)
            self._pos_x_spin.setEnabled(False)
            self._pos_y.setEnabled(False)
            self._pos_y_spin.setEnabled(False)
            self._rotate.setEnabled(False)
            self._rotate_spin.setEnabled(False)
            self._opacity.setEnabled(False)
            self._opacity_spin.setEnabled(False)
            self._scale.setEnabled(False)
            self._scale_spin.setEnabled(False)
            self._sub_h_slider.setEnabled(False)
            self._sub_h_spin.setEnabled(False)
            self._sub_font_combo.setEnabled(False)
            self._sub_font_size_spin.setEnabled(False)
            self._sample_text_edit.setEnabled(False)
            self._wave_h_slider.setEnabled(False)
            self._wave_h_spin.setEnabled(False)
        self._block = False

    def _on_layout_changed(self) -> None:
        self._layout.enabled = True
        if self._image_list.count() != len(self._layout.ordered_layers()):
            self._rebuild_list()
        self._update_inspector()

    # ── playback ────────────────────────────────────────────────────────────
    def _toggle_playback(self) -> None:
        playing = not self._canvas.playing
        self._canvas.set_playing(playing)
        set_icon(self._play_btn, "pause" if playing else "play", "accent", 13)

    def _on_timeline(self, value: int) -> None:
        if self._timeline_block:
            return
        self._canvas.set_playhead(value / 1000.0)

    def _on_phase(self, fraction: float) -> None:
        self._timeline_block = True
        self._timeline.setValue(int(round(fraction * 1000)))
        self._timeline_block = False
        seconds = fraction * PREVIEW_SECONDS
        self._time_label.setText(f"{_format_time(seconds)} / {_format_time(PREVIEW_SECONDS)}")

    def _update_view_btn_styles(self) -> None:
        for btn, name in [
            (getattr(self, "_btn_grid", None), "grid"),
            (getattr(self, "_btn_safe", None), "maximize"),
            (getattr(self, "_btn_center", None), "crosshair"),
        ]:
            if btn is None:
                continue
            if btn.isChecked():
                set_icon(btn, name, "white", 13)
                btn.setStyleSheet(
                    f"QPushButton#iconButton {{ background: {theme.ACCENT}; border: 1.5px solid {theme.ACCENT_BORDER}; border-radius: 8px; }}"
                    f"QPushButton#iconButton:hover {{ background: {theme.ACCENT_STRONG}; border-color: {theme.ACCENT_BORDER}; }}"
                )
            else:
                set_icon(btn, name, "muted", 13)
                btn.setStyleSheet("")

    def _toggle_grid(self) -> None:
        self._canvas.show_grid = self._btn_grid.isChecked()
        self._update_view_btn_styles()
        self._canvas.update()

    def _toggle_safe(self) -> None:
        self._canvas.show_safe_area = self._btn_safe.isChecked()
        self._update_view_btn_styles()
        self._canvas.update()

    def _toggle_center_guides(self) -> None:
        self._canvas.show_center_guides = self._btn_center.isChecked()
        self._update_view_btn_styles()
        self._canvas.update()

    def _on_ratio_changed(self) -> None:
        mode = str(self._ratio_combo.currentData() or "16:9")
        self._layout.aspect_ratio = mode
        self._canvas.aspect_ratio_mode = mode
        self._canvas.updateGeometry()
        self._canvas.update()
        self._commit_history()

    # ── actions ─────────────────────────────────────────────────────────────
    def _canvas_action(self, mode: str) -> None:
        if mode in ("flip_h", "flip_v"):
            self._canvas.flip_selected(mode == "flip_h")
        elif mode == "reset_rotation":
            self._canvas.rotate_selected(0.0)
            self._rotate.setValue(0)
            self._rotate_spin.setValue(0)
        else:
            self._canvas.align_selected(mode)
        self._commit_history()

    def _on_wave_toggled(self, checked: bool) -> None:
        if self._block:
            return
        self._layout.waveform = bool(checked)
        self._color_button.setEnabled(checked)
        self._canvas.update()
        self._rebuild_list()
        self._commit_history()

    def _on_scale_slider(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._scale_spin.setValue(value)
        self._block = False
        self._layout.subtitle_scale = value / 100.0
        self._scale_label.setText(f"Cỡ chữ phụ đề: {value}%")
        self._canvas.update()

    def _on_scale_spin(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._scale.setValue(value)
        self._block = False
        self._layout.subtitle_scale = value / 100.0
        self._scale_label.setText(f"Cỡ chữ phụ đề: {value}%")
        self._canvas.update()

    def _on_sample_text_changed(self, text: str) -> None:
        self._canvas.sample_text = text or _DEFAULT_SAMPLE_TEXT
        self._canvas.update()

    def _on_wave_h_slider(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._wave_h_spin.setValue(value)
        self._block = False
        self._layout.wave_h = value / 100.0
        self._layout.clamp()
        self._wave_h_label.setText(f"Chiều cao sóng: {value}%")
        self._canvas.update()

    def _on_wave_h_spin(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._wave_h_slider.setValue(value)
        self._block = False
        self._layout.wave_h = value / 100.0
        self._layout.clamp()
        self._wave_h_label.setText(f"Chiều cao sóng: {value}%")
        self._canvas.update()

    def _on_opacity_slider(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        if kind != "image" or not (0 <= index < len(self._layout.images)):
            return
        self._block = True
        self._opacity_spin.setValue(value)
        self._block = False
        self._layout.images[index].opacity = value / 100.0
        self._opacity_label.setText(f"Độ đậm: {value}%")
        self._canvas.update()

    def _on_opacity_spin(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        if kind != "image" or not (0 <= index < len(self._layout.images)):
            return
        self._block = True
        self._opacity.setValue(value)
        self._block = False
        self._layout.images[index].opacity = value / 100.0
        self._opacity_label.setText(f"Độ đậm: {value}%")
        self._canvas.update()

    def _on_sub_h_slider(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._sub_h_spin.setValue(value)
        self._block = False
        self._layout.subtitle_h = value / 100.0
        self._layout.clamp()
        self._sub_h_label.setText(f"Chiều cao khung chữ: {value}%")
        self._canvas.update()

    def _on_sub_h_spin(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._sub_h_slider.setValue(value)
        self._block = False
        self._layout.subtitle_h = value / 100.0
        self._layout.clamp()
        self._sub_h_label.setText(f"Chiều cao khung chữ: {value}%")
        self._canvas.update()

    def _on_sub_font_changed(self, font: QFont) -> None:
        if self._block:
            return
        self._layout.subtitle_font = font.family()
        self._canvas.update()
        self._commit_history()

    def _on_sub_font_size_changed(self, value: int) -> None:
        if self._block:
            return
        self._layout.subtitle_font_size = value
        self._canvas.update()

    def _on_width_slider(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        self._block = True
        self._width_spin.setValue(value)
        self._block = False
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].width = value / 100.0
            self._layout.images[index].clamp()
            self._width_label.setText(f"Kích thước (Scale): {value}%")
            self._canvas.update()
        elif kind == "subtitle":
            self._layout.subtitle_w = value / 100.0
            self._layout.clamp()
            self._width_label.setText(f"Chiều rộng khung chữ: {value}%")
            self._canvas.update()
        elif kind == "wave":
            self._layout.wave_w = value / 100.0
            self._layout.clamp()
            self._width_label.setText(f"Độ rộng sóng: {value}%")
            self._canvas.update()
        elif kind == "video":
            self._layout.video_w = max(0.10, value / 100.0)
            self._layout.clamp()
            w_pct = int(round(self._layout.video_w * 100))
            self._width_label.setText(f"Kích thước khung Video: {w_pct}%")
            self._canvas.update()

    def _on_width_spin(self, value: int) -> None:
        if self._block:
            return
        kind, index = self._canvas.selection()
        self._block = True
        self._width.setValue(value)
        self._block = False
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].width = value / 100.0
            self._layout.images[index].clamp()
            self._width_label.setText(f"Kích thước (Scale): {value}%")
            self._canvas.update()
        elif kind == "subtitle":
            self._layout.subtitle_w = value / 100.0
            self._layout.clamp()
            self._width_label.setText(f"Chiều rộng khung chữ: {value}%")
            self._canvas.update()
        elif kind == "wave":
            self._layout.wave_w = value / 100.0
            self._layout.clamp()
            self._width_label.setText(f"Độ rộng sóng: {value}%")
            self._canvas.update()
        elif kind == "video":
            self._layout.video_w = max(0.10, value / 100.0)
            self._layout.clamp()
            w_pct = int(round(self._layout.video_w * 100))
            self._width_label.setText(f"Kích thước khung Video: {w_pct}%")
            self._canvas.update()

    def _on_pos_x_slider(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._pos_x_spin.setValue(value)
        self._block = False
        self._set_pos_x(value / 100.0)

    def _on_pos_x_spin(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._pos_x.setValue(value)
        self._block = False
        self._set_pos_x(value / 100.0)

    def _set_pos_x(self, norm: float) -> None:
        kind, index = self._canvas.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].x = norm
            self._layout.images[index].clamp()
        elif kind == "subtitle":
            self._layout.subtitle_x = norm
            self._layout.clamp()
        elif kind == "wave":
            self._layout.wave_x = norm
            self._layout.clamp()
        elif kind == "video":
            self._layout.video_x = norm
            self._layout.clamp()
        self._pos_x_label.setText(f"Vị trí X: {int(round(norm * 100))}%")
        self._canvas.update()

    def _on_pos_y_slider(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._pos_y_spin.setValue(value)
        self._block = False
        self._set_pos_y(value / 100.0)

    def _on_pos_y_spin(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._pos_y.setValue(value)
        self._block = False
        self._set_pos_y(value / 100.0)

    def _set_pos_y(self, norm: float) -> None:
        kind, index = self._canvas.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].y = norm
            self._layout.images[index].clamp()
        elif kind == "subtitle":
            self._layout.subtitle_y = norm
            self._layout.clamp()
        elif kind == "wave":
            self._layout.wave_y = norm
            self._layout.clamp()
        elif kind == "video":
            self._layout.video_y = norm
            self._layout.clamp()
        self._pos_y_label.setText(f"Vị trí Y: {int(round(norm * 100))}%")
        self._canvas.update()

    def _on_rotate_slider(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._rotate_spin.setValue(value)
        self._block = False
        kind, index = self._canvas.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].rotation = float(value)
            self._layout.images[index].clamp()
            self._rotate_label.setText(f"Góc xoay: {value}°")
            self._canvas.update()

    def _on_rotate_spin(self, value: int) -> None:
        if self._block:
            return
        self._block = True
        self._rotate.setValue(value)
        self._block = False
        kind, index = self._canvas.selection()
        if kind == "image" and 0 <= index < len(self._layout.images):
            self._layout.images[index].rotation = float(value)
            self._layout.images[index].clamp()
            self._rotate_label.setText(f"Góc xoay: {value}°")
            self._canvas.update()

    def _apply_preset(self, preset: Dict[str, Any]) -> None:
        self._layout.enabled = True
        for key, value in preset.get("values", {}).items():
            setattr(self._layout, key, value)
        ratio = str(preset.get("ratio") or "16:9")
        self._layout.aspect_ratio = ratio
        self._layout.clamp()

        self._block = True
        idx = self._ratio_combo.findData(ratio)
        if idx >= 0:
            self._ratio_combo.setCurrentIndex(idx)
        self._canvas.aspect_ratio_mode = ratio
        self._block = False

        self._sync_controls()
        self._canvas.updateGeometry()
        self._canvas.update()
        self._commit_history()
        paint_status(self._status, "success")
        self._status.setText(f"Đã áp dụng mẫu “{preset.get('name', '')}”.")

    def _insert_preset(self, path: str) -> None:
        asset = _resolve_asset(path)
        if asset is None:
            return
        old_layers = self._layout.ordered_layers()
        index = len(self._layout.images)
        offset = 0.03 * (index % 5)
        self._layout.images.append(FrameImage(
            path=str(asset.resolve()), x=0.35 + offset, y=0.35 + offset, width=0.22))
        new_layers = [("image", index)] + [layer for layer in old_layers if layer != ("image", index)]
        self._layout.set_ordered_layers(new_layers)
        self._layout.enabled = True
        self._canvas.reload_assets()
        self._rebuild_list(index)
        self._canvas.set_selection("image", index)
        self._canvas.update()
        self._commit_history()

    def _add_image(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Chọn ảnh lớp phủ", "", _IMAGE_FILTER)
        if not path:
            return
        old_layers = self._layout.ordered_layers()
        index = len(self._layout.images)
        offset = 0.03 * (index % 5)
        self._layout.images.append(FrameImage(
            path=str(Path(path)), x=0.06 + offset, y=0.06 + offset, width=0.18))
        new_layers = [("image", index)] + [layer for layer in old_layers if layer != ("image", index)]
        self._layout.set_ordered_layers(new_layers)
        self._layout.enabled = True
        self._canvas.reload_assets()
        self._rebuild_list(index)
        self._canvas.set_selection("image", index)
        self._canvas.update()
        self._commit_history()

    def _replace_image(self) -> None:
        kind, index = self._canvas.selection()
        if kind != "image" or not (0 <= index < len(self._layout.images)):
            return
        path, _filter = QFileDialog.getOpenFileName(self, "Thay đổi file ảnh", "", _IMAGE_FILTER)
        if not path:
            return
        self._layout.images[index].path = str(Path(path))
        self._canvas.reload_assets()
        self._rebuild_list(index)
        self._canvas.update()
        self._commit_history()

    def _remove_image(self) -> None:
        focused = self.focusWidget()
        if isinstance(focused, (QLineEdit, QSpinBox)):
            return
        if self._canvas.delete_selected():
            self._rebuild_list(-1)
            self._commit_history()

    def _on_layer_list_context_menu(self, point) -> None:
        item = self._image_list.itemAt(point)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not (isinstance(data, tuple) and len(data) == 2):
            return
        kind, idx = data
        self._canvas.set_selection(kind, idx)
        menu = QMenu(self)
        if kind == "image":
            act_del = menu.addAction("Xoá lớp ảnh này (Delete)")
            set_icon(act_del, "trash", "danger", 12)
            act_dup = menu.addAction("Nhân bản (Duplicate)")
            set_icon(act_dup, "copy", "accent", 12)
            menu.addSeparator()
            act_vis = menu.addAction("Ẩn / Hiện")
            set_icon(act_vis, "eye", "muted", 12)
            act_lock = menu.addAction("Khóa / Mở khóa")
            set_icon(act_lock, "lock", "muted", 12)
            chosen = menu.exec(self._image_list.mapToGlobal(point))
            if chosen == act_del:
                self._remove_image()
            elif chosen == act_dup:
                self._duplicate_layer()
            elif chosen == act_vis:
                self._toggle_visibility()
            elif chosen == act_lock:
                self._toggle_lock()
        elif kind in ("wave", "subtitle", "video"):
            act_vis = menu.addAction("Ẩn / Hiện")
            set_icon(act_vis, "eye", "muted", 12)
            chosen = menu.exec(self._image_list.mapToGlobal(point))
            if chosen == act_vis:
                self._toggle_visibility()

    def _duplicate_layer(self) -> None:
        if self._canvas.duplicate_selected():
            self._rebuild_list(self._canvas.selection()[1])
            self._commit_history()

    def _toggle_visibility(self) -> None:
        if self._canvas.toggle_selected_visibility():
            self._rebuild_list(self._canvas.selection()[1])

    def _toggle_lock(self) -> None:
        if self._canvas.toggle_selected_lock():
            self._rebuild_list(self._canvas.selection()[1])

    def _move_layer(self, delta: int) -> None:
        kind, index = self._canvas.selection()
        if not kind:
            return
        layers = self._layout.ordered_layers()
        cur_pair = (kind, index if kind == "image" else -1)
        if cur_pair not in layers:
            return
        cur_pos = layers.index(cur_pair)
        target_pos = cur_pos + delta
        if not (0 <= target_pos < len(layers)):
            return
        layers[cur_pos], layers[target_pos] = layers[target_pos], layers[cur_pos]
        self._layout.set_ordered_layers(layers)
        self._rebuild_list(index if kind == "image" else -1)
        self._canvas.set_selection(kind, index if kind == "image" else -1)
        self._canvas.update()
        self._commit_history()

    def _pick_preview(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Chọn ảnh nền xem trước", "", _IMAGE_FILTER)
        if not path:
            return
        self._layout.preview = str(Path(path))
        self._canvas.reload_assets()
        self._preview_clear.setEnabled(True)
        paint_status(self._status, "success")
        self._status.setText(f"Ảnh nền xem trước: {Path(path).name}")
        self._commit_history()

    def _clear_preview(self) -> None:
        self._layout.preview = ""
        self._canvas.reload_assets()
        self._preview_clear.setEnabled(False)
        self._status.setText("Đã bỏ ảnh nền xem trước.")
        self._commit_history()

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(_qcolor(self._layout.wave_color), self, "Chọn màu sóng âm")
        if not color.isValid():
            return
        self._set_wave_color(color.name().upper())

    def _set_wave_color(self, hex_code: str) -> None:
        self._layout.wave_color = hex_code.upper()
        self._paint_color_button()
        self._canvas.update()
        self._commit_history()

    def _paint_color_button(self) -> None:
        color = QColor(self._layout.wave_color)
        if not color.isValid():
            color = QColor("#38BDF8")
        text = "#0B1728" if color.lightness() > 150 else "#FFFFFF"
        self._color_button.setStyleSheet(
            "QPushButton {"
            f" background:{color.name()}; color:{text};"
            f" border:1px solid {theme.BORDER_STRONG}; font-weight:600; border-radius:4px;"
            "}"
            "QPushButton:disabled { background:transparent; color:"
            f"{theme.TEXT_FAINT}; border-color:{theme.BORDER}; }}"
        )

    def _paint_inspector_title(self) -> None:
        self._inspector_title.setStyleSheet(f"font-weight:700; color:{theme.ACCENT}; font-size:11px;")

    def _reset(self) -> None:
        self._commit_history()
        previous = self._state()
        frame = FrameLayout(enabled=False)
        self._layout = frame
        self._canvas.set_layout(frame)
        self._undo_stack.append(previous)
        if len(self._undo_stack) > 60:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._snapshot = self._state()
        self._sync_history_buttons()
        self._sync_controls()
        self._rebuild_list(-1)
        self._paint_color_button()
        paint_status(self._status, "muted")
        self._status.setText("Đã đặt lại khung mẫu.")
        self._save_to_current_project()


def _format_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _resolve_asset(path: str) -> Optional[Path]:
    candidate = Path(path)
    if candidate.is_file():
        return candidate
    try:
        from core.settings import ROOT
        rooted = ROOT / path
        if rooted.is_file():
            return rooted
    except Exception:
        pass
    return None
