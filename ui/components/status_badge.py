"""Compact status pill with a coloured dot for H2MTTS / FFmpeg / YouTube."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ui import theme


class StatusBadge(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBadge")
        self.setMinimumWidth(96)
        self._state = "idle"
        self._text = "Đang kiểm tra…"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 5, 10, 5)
        layout.setSpacing(8)

        self.dot = QLabel()
        self.dot.setObjectName("BadgeDot")
        self.dot.setFixedSize(8, 8)
        layout.addWidget(self.dot, 0, alignment=Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("BadgeTitle")
        self.value_label = QLabel("Đang kiểm tra…")
        self.value_label.setObjectName("BadgeValue")
        text_col.addWidget(self.title_label)
        text_col.addWidget(self.value_label)
        layout.addLayout(text_col, 1)

        self.set_state("idle", "Đang kiểm tra…")

    def set_state(self, state: str, text: str) -> None:
        self._state = state
        self._text = text
        self._paint()
        self.value_label.setText(text)
        self.setToolTip(text)
        self.setAccessibleName(f"{self.title_label.text()}: {text}")

    def _paint(self) -> None:
        color = {
            "ok": theme.SUCCESS,
            "warn": theme.WARNING,
            "error": theme.DANGER,
            "idle": theme.TEXT_FAINT,
        }.get(self._state, theme.TEXT_FAINT)
        self.dot.setStyleSheet(f"background:{color}; border-radius:4px;")
        self.value_label.setStyleSheet(
            f"color:{theme.DANGER if self._state == 'error' else theme.TEXT};"
        )

    def apply_theme(self) -> None:
        self._paint()
