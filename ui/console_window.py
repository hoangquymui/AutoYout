"""Floating, independent Console and Log Viewer Window."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.settings import ROOT
from ui import theme
from ui.components.icons import icon, set_icon
from ui.components.log_console import LogConsole
from ui.components.page import icon_button
from utils.logger import get_logger

logger = get_logger("ui.console")


def toggle_windows_console() -> bool:
    """Attach or show native Windows command prompt console window (AllocConsole)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            kernel32.AllocConsole()
            hwnd = kernel32.GetConsoleWindow()
            try:
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            except Exception:
                pass
        if hwnd:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception as exc:
        logger.warning("Không thể mở Windows Console: %s", exc)
    return False


class ConsoleWindow(QDialog):
    """Independent floating window for viewing live system logs and debug output."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("ConsoleWindow")
        self.setWindowTitle("AutoYout — Cửa sổ Console & Nhật ký")
        self.resize(860, 520)
        self.setMinimumSize(600, 360)

        # Set window icon
        try:
            self.setWindowIcon(icon("terminal", "accent", 20))
        except Exception:
            pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # ── Top Toolbar ───────────────────────────────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        title_lbl = QLabel("🖥️ CỬA SỔ CONSOLE & NHẬT KÝ HỆ THỐNG")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #0284C7;")
        top_bar.addWidget(title_lbl)

        self.live_indicator = QLabel("• Đang ghi nhận log")
        self.live_indicator.setStyleSheet(
            "background: rgba(16, 185, 129, 0.15); color: #10B981; "
            "border-radius: 9px; padding: 2px 8px; font-size: 10.5px; font-weight: bold;"
        )
        top_bar.addWidget(self.live_indicator)

        top_bar.addStretch(1)

        # Button: Copy All
        self.btn_copy = QPushButton("Copy log")
        set_icon(self.btn_copy, "file", "muted", 12)
        self.btn_copy.setToolTip("Copy toàn bộ nội dung log vào clipboard")
        self.btn_copy.clicked.connect(self._copy_all_logs)
        top_bar.addWidget(self.btn_copy)

        # Button: Open Log Directory
        self.btn_open_folder = QPushButton("Thư mục log")
        set_icon(self.btn_open_folder, "folder-open", "muted", 12)
        self.btn_open_folder.setToolTip("Mở thư mục chứa file log trên máy")
        self.btn_open_folder.clicked.connect(self._open_log_folder)
        top_bar.addWidget(self.btn_open_folder)

        layout.addLayout(top_bar)

        # ── Central Log Console ──────────────────────────────────────────────
        self.console = LogConsole(self)
        self.console.clear_button.setVisible(False)
        layout.addWidget(self.console, 1)

        self.apply_theme()

    def append_log(self, level: str, message: str) -> None:
        self.console.append(level, message)

    def apply_theme(self) -> None:
        self.console.apply_theme()
        is_dark = theme.current_mode() == "dark"
        bg = "#0F172A" if is_dark else "#F8FAFC"
        border = "#334155" if is_dark else "#E2E8F0"
        self.setStyleSheet(f"QDialog#ConsoleWindow {{ background-color: {bg}; }}")

    def _clear_logs(self) -> None:
        self.console.clear()

    def _copy_all_logs(self) -> None:
        plain_text = self.console.view.toPlainText()
        if plain_text:
            QApplication.clipboard().setText(plain_text)

    def _open_log_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()
