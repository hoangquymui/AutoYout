"""Coloured, auto-scrolling log console for the bottom panel."""
from __future__ import annotations

import html
from datetime import datetime
from typing import List, Tuple

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.components.page import icon_button

_MAX_ENTRIES = 2000


class LogConsole(QWidget):
    """Read-only log view fed by :class:`utils.logger.QtLogHandler`."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LogConsole")
        self._entries: List[Tuple[str, str, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("NHẬT KÝ HOẠT ĐỘNG")
        title.setObjectName("PanelTitle")
        header.addWidget(title)
        self.count_label = QLabel("0 dòng")
        self.count_label.setObjectName("LogCount")
        header.addWidget(self.count_label)
        header.addStretch(1)
        self.clear_button = icon_button(
            "trash", "Xoá nội dung nhật ký", role="muted", size=14, side=28
        )
        self.clear_button.clicked.connect(self.clear)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("LogView")
        self.view.setMaximumBlockCount(5000)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setPlaceholderText("Nhật ký chạy TTS / video / upload sẽ hiện ở đây…")
        layout.addWidget(self.view, 1)

    def append(self, level: str, message: str) -> None:
        level = (level or "INFO").upper()
        stamp = datetime.now().strftime("%H:%M:%S")
        safe = html.escape(str(message)).replace("\n", "<br>")
        self._entries.append((stamp, level, safe))
        if len(self._entries) > _MAX_ENTRIES:
            del self._entries[: len(self._entries) - _MAX_ENTRIES]
        scrollbar = self.view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 6
        self.view.appendHtml(self._format(stamp, level, safe))
        self.count_label.setText(f"{len(self._entries)} dòng")
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def _format(stamp: str, level: str, safe: str) -> str:
        color = theme.LOG_LEVELS.get(level, theme.LOG_DEFAULT)
        return (
            f'<span style="color:{theme.LOG_TIME}">{stamp}</span>'
            f'<span style="color:{theme.LOG_SEP}">&nbsp;·&nbsp;</span>'
            f'<span style="color:{color};font-weight:600">{level:7}</span>'
            f'<span style="color:{theme.LOG_MSG}">&nbsp;{safe}</span>'
        )

    def apply_theme(self) -> None:
        if not self._entries:
            return
        scrollbar = self.view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 6
        self.view.clear()
        for stamp, level, safe in self._entries:
            self.view.appendHtml(self._format(stamp, level, safe))
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def clear(self) -> None:
        self.view.clear()
        self._entries.clear()
        self.count_label.setText("0 dòng")
