"""Floating, independent Developer Terminal Console and Log Viewer Window."""
from __future__ import annotations

import html
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.settings import ROOT
from ui import theme
from ui.components.icons import icon, set_icon
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
                conout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                if hasattr(sys.stdout, "fallback"):
                    sys.stdout.fallback = conout
                else:
                    sys.stdout = conout
                if hasattr(sys.stderr, "fallback"):
                    sys.stderr.fallback = conout
                else:
                    sys.stderr = conout
            except Exception:
                pass
        if hwnd:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception as exc:
        logger.warning("Không thể mở Windows Console: %s", exc)
    return False


_TERMINAL_VIEW_STYLE = """
QPlainTextEdit#TerminalView {
    background-color: #0C0C0C;
    color: #E2E8F0;
    border: 1px solid #1F2937;
    border-radius: 6px;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    padding: 6px 8px;
    selection-background-color: #1E3A8A;
    selection-color: #FFFFFF;
}
QPlainTextEdit#TerminalView QScrollBar:vertical {
    background: #0C0C0C;
    width: 10px;
    margin: 0px;
}
QPlainTextEdit#TerminalView QScrollBar::handle:vertical {
    background: #374151;
    min-height: 20px;
    border-radius: 5px;
}
QPlainTextEdit#TerminalView QScrollBar::handle:vertical:hover {
    background: #4B5563;
}
QPlainTextEdit#TerminalView QScrollBar::add-line:vertical,
QPlainTextEdit#TerminalView QScrollBar::sub-line:vertical {
    height: 0px;
}
QPlainTextEdit#TerminalView QScrollBar:horizontal {
    background: #0C0C0C;
    height: 10px;
    margin: 0px;
}
QPlainTextEdit#TerminalView QScrollBar::handle:horizontal {
    background: #374151;
    min-width: 20px;
    border-radius: 5px;
}
QPlainTextEdit#TerminalView QScrollBar::handle:horizontal:hover {
    background: #4B5563;
}
QPlainTextEdit#TerminalView QScrollBar::add-line:horizontal,
QPlainTextEdit#TerminalView QScrollBar::sub-line:horizontal {
    width: 0px;
}
"""


class TerminalConsole(QWidget):
    """Monospace, colorized developer terminal console view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TerminalConsole")
        self._entries: List[Tuple[str, str, str, str]] = []  # (stamp, level, raw, html)
        self._max_entries = 10000
        self.auto_scroll: bool = True
        self.filter_query: str = ""
        self.level_filter: str = "ALL"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Hidden dummy button for backwards compatibility with tests / callers
        self.clear_button = QPushButton(self)
        self.clear_button.setVisible(False)

        # Count label
        self.count_label = QLabel("0 dòng", self)
        self.count_label.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: bold;")

        # Terminal text edit
        self.view = QPlainTextEdit(self)
        self.view.setObjectName("TerminalView")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(15000)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setStyleSheet(_TERMINAL_VIEW_STYLE)

        font = QFont("Cascadia Code", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(font)

        layout.addWidget(self.view, 1)

        self._insert_banner()

    def _insert_banner(self) -> None:
        banner = (
            '<span style="color:#4B5563;">================================================================================</span><br>'
            '<span style="color:#38BDF8;font-weight:bold;">🖥️ AutoYout Developer Terminal Console</span> '
            '<span style="color:#10B981;font-weight:bold;">[Live Backend &amp; Frontend Stream]</span><br>'
            f'<span style="color:#94A3B8;">Platform: Windows | Python {sys.version.split()[0]} | PID: {os.getpid()} | Encoding: UTF-8</span><br>'
            '<span style="color:#94A3B8;">Logs: sys.stdout, sys.stderr, app loggers, 3rd-party libs, background tasks</span><br>'
            '<span style="color:#FBBF24;">Phím tắt: [F12] hoặc [Ctrl+`] để bật/tắt Terminal bất cứ lúc nào</span><br>'
            '<span style="color:#4B5563;">================================================================================</span><br>'
        )
        self.view.appendHtml(banner)

    def append(self, level: str, message: str) -> None:
        level_str = (level or "INFO").upper()
        stamp = datetime.now().strftime("%H:%M:%S")
        raw_msg = str(message)
        formatted_html = self._format_entry(stamp, level_str, raw_msg)

        self._entries.append((stamp, level_str, raw_msg, formatted_html))
        if len(self._entries) > self._max_entries:
            del self._entries[: len(self._entries) - self._max_entries]

        self.count_label.setText(f"{len(self._entries)} dòng")

        if self._matches_filter(level_str, raw_msg):
            self.view.appendHtml(formatted_html)
            if self.auto_scroll:
                sb = self.view.verticalScrollBar()
                sb.setValue(sb.maximum())

    def _format_entry(self, stamp: str, level: str, raw_msg: str) -> str:
        # Badges and color styles
        badge_colors = {
            "INFO": "#38BDF8",      # Sky blue
            "WARNING": "#FBBF24",   # Amber
            "ERROR": "#F87171",     # Soft red
            "CRITICAL": "#EF4444",  # Bright red
            "DEBUG": "#94A3B8",     # Gray
        }
        badge_color = badge_colors.get(level, "#A3E635")

        safe_msg = html.escape(raw_msg)

        # Highlight traceback or python exception lines
        is_error = level in ("ERROR", "CRITICAL")
        if "Traceback (most recent call last):" in raw_msg or 'File "' in raw_msg:
            msg_color = "#FCA5A5"  # Soft red for traceback
        elif is_error:
            msg_color = "#F87171"
        elif level == "WARNING":
            msg_color = "#FDE68A"
        elif level == "DEBUG":
            msg_color = "#94A3B8"
        else:
            msg_color = "#E2E8F0"

        # If multiline, indent remaining lines
        if "\n" in safe_msg:
            lines = safe_msg.split("\n")
            head = lines[0]
            tail = "<br>&nbsp;&nbsp;&nbsp;&nbsp;".join(lines[1:])
            safe_msg = f"{head}<br>&nbsp;&nbsp;&nbsp;&nbsp;{tail}"

        return (
            f'<span style="color:#64748B;">[{stamp}]</span> '
            f'<span style="color:{badge_color};font-weight:bold;">[{level:7}]</span> '
            f'<span style="color:{msg_color};">{safe_msg}</span>'
        )

    def _matches_filter(self, level: str, raw_msg: str) -> bool:
        if self.level_filter != "ALL":
            if self.level_filter not in level.upper():
                return False
        if self.filter_query:
            q = self.filter_query.lower()
            if q not in raw_msg.lower() and q not in level.lower():
                return False
        return True

    def set_filter_query(self, query: str) -> None:
        self.filter_query = (query or "").strip().lower()
        self._rebuild_view()

    def set_level_filter(self, level: str) -> None:
        self.level_filter = (level or "ALL").upper()
        self._rebuild_view()

    def set_auto_scroll(self, enable: bool) -> None:
        self.auto_scroll = enable
        if enable:
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def set_word_wrap(self, enable: bool) -> None:
        mode = (
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if enable
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.view.setLineWrapMode(mode)

    def _rebuild_view(self) -> None:
        self.view.clear()
        for stamp, level, raw_msg, formatted in self._entries:
            if self._matches_filter(level, raw_msg):
                self.view.appendHtml(formatted)
        if self.auto_scroll:
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def clear(self) -> None:
        self._entries.clear()
        self.view.clear()
        self.count_label.setText("0 dòng")

    def apply_theme(self) -> None:
        self.view.setStyleSheet(_TERMINAL_VIEW_STYLE)


class ConsoleWindow(QDialog):
    """Independent floating developer terminal window for live system logs and debug output."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("ConsoleWindow")
        self.setWindowTitle("AutoYout — Terminal Console [Backend & Frontend]")
        self.resize(920, 560)
        self.setMinimumSize(640, 380)

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

        title_lbl = QLabel("🖥️ TERMINAL CONSOLE — TOÀN BỘ HOẠT ĐỘNG (BACKEND & FRONTEND)")
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {theme.ACCENT};")
        top_bar.addWidget(title_lbl)

        self.live_indicator = QLabel("• Live Stream")
        self.live_indicator.setStyleSheet(
            "background: #064E3B; color: #34D399; border: 1px solid #059669; "
            "border-radius: 8px; padding: 2px 8px; font-size: 10.5px; font-weight: bold;"
        )
        top_bar.addWidget(self.live_indicator)

        top_bar.addStretch(1)

        # Button: Launch Windows CMD
        self.btn_cmd = QPushButton("🪟 Windows CMD")
        self.btn_cmd.setToolTip("Mở cửa sổ Command Prompt thực của Windows (AllocConsole)")
        self.btn_cmd.clicked.connect(self._launch_windows_cmd)
        top_bar.addWidget(self.btn_cmd)

        # Button: Copy All
        self.btn_copy = QPushButton("Copy log")
        set_icon(self.btn_copy, "file", "muted", 12)
        self.btn_copy.setToolTip("Copy toàn bộ nội dung terminal vào clipboard")
        self.btn_copy.clicked.connect(self._copy_all_logs)
        top_bar.addWidget(self.btn_copy)

        # Button: Clear
        self.btn_clear = QPushButton("Xoá màn hình")
        set_icon(self.btn_clear, "trash", "muted", 12)
        self.btn_clear.setToolTip("Xoá sạch màn hình terminal")
        self.btn_clear.clicked.connect(self._clear_logs)
        top_bar.addWidget(self.btn_clear)

        # Button: Open Log Directory
        self.btn_open_folder = QPushButton("Thư mục log")
        set_icon(self.btn_open_folder, "folder-open", "muted", 12)
        self.btn_open_folder.setToolTip("Mở thư mục chứa file log trên máy")
        self.btn_open_folder.clicked.connect(self._open_log_folder)
        top_bar.addWidget(self.btn_open_folder)

        layout.addLayout(top_bar)

        # ── Filter & Options Bar ──────────────────────────────────────────────
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Lọc log (nhập từ khoá grep live)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet(
            "background: #1E293B; color: #E2E8F0; border: 1px solid #334155; "
            "border-radius: 5px; padding: 4px 8px; font-size: 11.5px;"
        )
        ctrl_bar.addWidget(self.search_input, 1)

        self.combo_level = QComboBox()
        self.combo_level.addItems(["Tất cả level", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.combo_level.setStyleSheet(
            "background: #1E293B; color: #E2E8F0; border: 1px solid #334155; "
            "border-radius: 5px; padding: 4px 8px; font-size: 11.5px;"
        )
        ctrl_bar.addWidget(self.combo_level)

        self.cb_autoscroll = QCheckBox("Tự động cuộn")
        self.cb_autoscroll.setChecked(True)
        self.cb_autoscroll.setStyleSheet("color: #94A3B8; font-size: 11.5px;")
        ctrl_bar.addWidget(self.cb_autoscroll)

        self.cb_wordwrap = QCheckBox("Xuống dòng")
        self.cb_wordwrap.setChecked(False)
        self.cb_wordwrap.setStyleSheet("color: #94A3B8; font-size: 11.5px;")
        ctrl_bar.addWidget(self.cb_wordwrap)

        layout.addLayout(ctrl_bar)

        # ── Central Terminal Console ─────────────────────────────────────────
        self.console = TerminalConsole(self)
        layout.addWidget(self.console, 1)

        # Connect controls to console
        self.search_input.textChanged.connect(self.console.set_filter_query)
        self.combo_level.currentTextChanged.connect(
            lambda text: self.console.set_level_filter("ALL" if "Tất cả" in text else text)
        )
        self.cb_autoscroll.toggled.connect(self.console.set_auto_scroll)
        self.cb_wordwrap.toggled.connect(self.console.set_word_wrap)

        # ── Footer / Hint Bar ────────────────────────────────────────────────
        footer = QHBoxLayout()
        hint_lbl = QLabel(
            "💡 Phím tắt: [F12] hoặc [Ctrl+`] để đóng/mở Terminal • Kết nối trực tiếp sys.stdout, sys.stderr & backend loggers."
        )
        hint_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        footer.addWidget(hint_lbl)
        footer.addStretch(1)
        footer.addWidget(self.console.count_label)
        layout.addLayout(footer)

        self.apply_theme()

    def append_log(self, level: str, message: str) -> None:
        self.console.append(level, message)

    def apply_theme(self) -> None:
        self.setStyleSheet("QDialog#ConsoleWindow { background-color: #0F172A; }")
        self.console.apply_theme()

    def _launch_windows_cmd(self) -> None:
        """Open or show the native Windows Command Prompt window."""
        success = toggle_windows_console()
        if success:
            self.console.append(
                "INFO",
                "[TERMINAL] Đã mở / kích hoạt cửa sổ Windows CMD Console (AllocConsole).",
            )
        else:
            self.console.append(
                "WARNING",
                "[TERMINAL] Không thể mở Windows Console (tính năng chỉ khả dụng trên Windows).",
            )

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
