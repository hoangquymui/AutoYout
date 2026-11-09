"""Floating toast notifications docked at the bottom-right of the window."""
from __future__ import annotations

import html
import time
from typing import Callable, List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
    QObject,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.components.icons import pixmap
from ui.components.page import icon_button
from utils.logger import get_logger

logger = get_logger("toast")


class ToastWidget(QFrame):
    """An individual toast card floating in the bottom-right corner."""

    closed = Signal()

    def __init__(
        self,
        parent: QWidget,
        title: str,
        message: str,
        level: str = "error",  # "error", "warning", "success", "info"
        timeout_ms: int = 10000,
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToastNotification")
        self._level = level
        self._action_callback = action_callback
        self._timeout_ms = timeout_ms
        self._closing = False
        self._title = title
        self._message = message

        # Fixed width, auto height
        self.setFixedWidth(420)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

        # Build layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(6)

        # Header row: Icon, Title, Close button
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        icon_label = QLabel()
        icon_name = {
            "error": "alert",
            "warning": "alert",
            "success": "check-circle",
            "info": "clock",
        }.get(level, "alert")
        icon_color = {
            "error": theme.icon_color("danger"),
            "warning": theme.icon_color("warning"),
            "success": theme.icon_color("success"),
            "info": theme.icon_color("accent"),
        }.get(level, theme.icon_color("danger"))

        icon_label.setPixmap(pixmap(icon_name, icon_color, 18))
        icon_label.setFixedSize(20, 20)
        header_row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("ToastTitle")
        self.title_label.setWordWrap(True)
        header_row.addWidget(self.title_label, 1)

        self.close_btn = icon_button("x", "Đóng thông báo", role="muted", size=11, side=22)
        self.close_btn.clicked.connect(self.close_animated)
        header_row.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        main_layout.addLayout(header_row)

        # Message body
        self.msg_label = QLabel(message)
        self.msg_label.setObjectName("ToastMessage")
        self.msg_label.setWordWrap(True)
        self.msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        main_layout.addWidget(self.msg_label)

        # Optional action button row
        if action_text and action_callback:
            action_row = QHBoxLayout()
            action_row.setContentsMargins(0, 4, 0, 0)
            action_row.setSpacing(8)
            action_row.addStretch(1)

            btn_action = QPushButton(action_text)
            btn_action.setObjectName("ToastActionButton")
            btn_action.setFixedHeight(26)
            btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_action.clicked.connect(self._on_action_clicked)
            action_row.addWidget(btn_action)
            main_layout.addLayout(action_row)

        self._apply_style()

        # Fade in effect
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(220)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Auto-dismiss timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close_animated)
        if timeout_ms > 0:
            self._timer.start(timeout_ms)

        self.adjustSize()
        self.show()
        self.raise_()
        self._fade_anim.start()

    def _apply_style(self) -> None:
        dark = theme.is_dark()
        bg = "#182235" if dark else "#FFFFFF"
        border = "#2A3F60" if dark else "#CBD5E1"
        accent_color = {
            "error": theme.DANGER,
            "warning": theme.WARNING,
            "success": theme.SUCCESS,
            "info": theme.ACCENT,
        }.get(self._level, theme.DANGER)

        text_title = theme.DANGER if self._level == "error" else theme.TEXT
        text_body = theme.TEXT if dark else "#1E293B"

        btn_bg = "rgba(248, 113, 113, 0.15)" if self._level == "error" and dark else (
            "#FEE2E2" if self._level == "error" else theme.BUTTON_BG
        )
        btn_hover = "rgba(248, 113, 113, 0.28)" if self._level == "error" and dark else (
            "#FECACA" if self._level == "error" else theme.BUTTON_HOVER
        )
        btn_text = theme.DANGER if self._level == "error" else theme.TEXT

        self.setStyleSheet(f"""
            QFrame#ToastNotification {{
                background-color: {bg};
                border: 1px solid {border};
                border-left: 5px solid {accent_color};
                border-radius: 10px;
            }}
            QLabel#ToastTitle {{
                color: {text_title};
                font-weight: 700;
                font-size: 13px;
            }}
            QLabel#ToastMessage {{
                color: {text_body};
                font-size: 12px;
                line-height: 1.4;
            }}
            QPushButton#ToastActionButton {{
                background-color: {btn_bg};
                color: {btn_text};
                border: 1px solid {accent_color};
                border-radius: 6px;
                padding: 3px 12px;
                font-size: 11.5px;
                font-weight: 600;
            }}
            QPushButton#ToastActionButton:hover {{
                background-color: {btn_hover};
            }}
        """)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self._timer.isActive():
            self._timer.stop()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._timeout_ms > 0 and not self._closing:
            self._timer.start(4000)

    def close_animated(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._fade_anim.stop()
        self._fade_anim.setDuration(180)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim.finished.connect(self._on_fade_finished)
        self._fade_anim.start()

    def _on_fade_finished(self) -> None:
        self.closed.emit()
        self.deleteLater()

    def _on_action_clicked(self) -> None:
        if self._action_callback:
            try:
                self._action_callback()
            except Exception as exc:
                logger.warning("Lỗi khi mở hành động toast: %s", exc)
        self.close_animated()


class ToastManager(QObject):
    """Manages stacked toast notifications anchored to the bottom-right of a parent widget."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._parent = parent
        self._toasts: List[ToastWidget] = []
        self._last_shown: dict[str, float] = {}

    def show(
        self,
        title: str,
        message: str,
        level: str = "error",
        timeout_ms: int = 10000,
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> ToastWidget:
        # Throttle exact duplicate messages within 3 seconds
        key = f"{title}:{message}"
        now = time.time()
        if key in self._last_shown and (now - self._last_shown[key]) < 3.0:
            if self._toasts:
                return self._toasts[-1]
        self._last_shown[key] = now

        # Keep max 3 toasts at a time
        if len(self._toasts) >= 3:
            oldest = self._toasts[0]
            oldest.close_animated()

        toast = ToastWidget(
            parent=self._parent,
            title=title,
            message=message,
            level=level,
            timeout_ms=timeout_ms,
            action_text=action_text,
            action_callback=action_callback,
        )
        toast.closed.connect(lambda t=toast: self._remove_toast(t))
        self._toasts.append(toast)
        self.reposition()
        return toast

    def show_error(
        self,
        title: str,
        message: str,
        timeout_ms: int = 10000,
        action_text: str = "Xem nhật ký log (F12)",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> ToastWidget:
        return self.show(
            title=title,
            message=message,
            level="error",
            timeout_ms=timeout_ms,
            action_text=action_text,
            action_callback=action_callback,
        )

    def show_warning(
        self,
        title: str,
        message: str,
        timeout_ms: int = 7000,
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> ToastWidget:
        return self.show(
            title=title,
            message=message,
            level="warning",
            timeout_ms=timeout_ms,
            action_text=action_text,
            action_callback=action_callback,
        )

    def show_success(
        self,
        title: str,
        message: str,
        timeout_ms: int = 5000,
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> ToastWidget:
        return self.show(
            title=title,
            message=message,
            level="success",
            timeout_ms=timeout_ms,
            action_text=action_text,
            action_callback=action_callback,
        )

    def _remove_toast(self, toast: ToastWidget) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
            self.reposition()

    def reposition(self) -> None:
        if not self._parent or not self._toasts:
            return

        parent_w = self._parent.width()
        parent_h = self._parent.height()
        margin_right = 24
        margin_bottom = 24
        gap = 10

        curr_y = parent_h - margin_bottom

        # Position from bottom (newest) to top (older)
        for toast in reversed(self._toasts):
            h = toast.sizeHint().height()
            w = toast.width()
            target_x = max(10, parent_w - w - margin_right)
            target_y = curr_y - h
            toast.move(target_x, target_y)
            toast.raise_()
            curr_y = target_y - gap
