"""Automation tab: run the pipeline step-by-step or end-to-end and watch logs."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.components.icons import set_icon
from ui.components.log_console import LogConsole
from ui.components.page import PageHeader


class AutomationTab(QWidget):
    run_requested = Signal(str)
    pause_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._status_error = False
        self._status_running = False

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        header = PageHeader(
            "Automation",
            "Chạy từng bước hoặc toàn bộ pipeline — có thể tạm dừng và tiếp tục.",
        )
        self.stage_label = QLabel("Sẵn sàng")
        self.stage_label.setObjectName("StageChip")
        header.add_action(self.stage_label)
        root.addWidget(header)

        # ── controls ─────────────────────────────────────────────────────────
        controls = QGroupBox("Điều khiển")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setSpacing(8)

        self.btn_tts = QPushButton("1. Lồng tiếng")
        self.btn_tts.setToolTip(
            "Lồng tiếng theo cue (nếu có SRT) hoặc đọc toàn bộ .txt/.json thành MP3."
        )
        self.btn_video = QPushButton("2. Tạo Video")
        self.btn_video.setToolTip(
            "Ghép MP3 với hình nền, nhạc nền và xuất MP4 bằng FFmpeg."
        )
        self.btn_upload = QPushButton("3. Upload YouTube")
        self.btn_upload.setToolTip("Đăng các video đã xuất lên kênh YouTube đã chọn.")
        set_icon(self.btn_tts, "mic", "accent", 15)
        set_icon(self.btn_video, "film", "accent", 15)
        set_icon(self.btn_upload, "upload", "accent", 15)
        self.btn_tts.clicked.connect(lambda: self.run_requested.emit("tts"))
        self.btn_video.clicked.connect(lambda: self.run_requested.emit("video"))
        self.btn_upload.clicked.connect(lambda: self.run_requested.emit("upload"))

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        for button in (self.btn_tts, self.btn_video, self.btn_upload):
            run_row.addWidget(button, 1)
        controls_layout.addLayout(run_row)

        self.btn_all = QPushButton("CHẠY TOÀN BỘ (AUTO ALL)")
        self.btn_all.setToolTip("Chạy tuần tự TTS → Video → Upload YouTube.")
        self.btn_all.setObjectName("runAllButton")
        set_icon(self.btn_all, "zap", "white", 16)
        self.btn_all.clicked.connect(lambda: self.run_requested.emit("all"))
        controls_layout.addWidget(self.btn_all)

        self.btn_pause = QPushButton("Tạm dừng")
        self.btn_pause.setObjectName("pauseButton")
        self.btn_stop = QPushButton("Dừng hẳn")
        self.btn_stop.setObjectName("stopButton")
        set_icon(self.btn_pause, "pause", "warning", 14)
        set_icon(self.btn_stop, "stop", "danger", 14)
        self.btn_pause.clicked.connect(self.pause_requested.emit)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)

        pause_row = QHBoxLayout()
        pause_row.setSpacing(8)
        pause_row.addWidget(self.btn_pause, 1)
        pause_row.addWidget(self.btn_stop, 1)
        controls_layout.addLayout(pause_row)

        root.addWidget(controls)

        # ── progress ─────────────────────────────────────────────────────────
        progress = QGroupBox("Tiến độ")
        progress_layout = QVBoxLayout(progress)
        progress_layout.setSpacing(10)

        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setValue(0)
        self.overall_bar.setTextVisible(False)
        self.overall_value = QLabel("0%")
        self.overall_value.setObjectName("PercentLabel")
        progress_layout.addLayout(
            self._progress_block("TỔNG THỂ", self.overall_bar, self.overall_value)
        )

        self.task_bar = QProgressBar()
        self.task_bar.setRange(0, 100)
        self.task_bar.setValue(0)
        self.task_bar.setTextVisible(False)
        self.task_value = QLabel("0%")
        self.task_value.setObjectName("PercentLabel")
        progress_layout.addLayout(
            self._progress_block("TÁC VỤ HIỆN TẠI", self.task_bar, self.task_value)
        )

        root.addWidget(progress)

        # ── status & notes ───────────────────────────────────────────────────
        self.notes_label = QLabel("")
        self.notes_label.setObjectName("MutedLabel")
        self.notes_label.setWordWrap(True)
        self.notes_label.setVisible(False)
        root.addWidget(self.notes_label)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_dot.setStyleSheet(
            f"background:{theme.TEXT_FAINT}; border-radius:4px;"
        )
        status_row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignTop)
        self.status_label = QLabel("Chọn project và bấm chạy để bắt đầu.")
        self.status_label.setObjectName("StatusText")
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        status_row.addWidget(self.status_label, 1)
        root.addLayout(status_row)

        # ── log ──────────────────────────────────────────────────────────────
        self.log_console = LogConsole()
        self.log_console.setMinimumHeight(160)
        root.addWidget(self.log_console, 1)

    @staticmethod
    def _progress_block(title: str, bar: QProgressBar, value: QLabel) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)
        head = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("PanelTitle")
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(value)
        block.addLayout(head)
        block.addWidget(bar)
        return block

    # ── state updates (called by MainWindow) ─────────────────────────────────
    def set_overall(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self.overall_bar.setValue(value)
        self.overall_value.setText(f"{value}%")

    def set_task(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self.task_bar.setValue(value)
        self.task_value.setText(f"{value}%")

    def set_stage(self, text: str) -> None:
        self.stage_label.setText(text)

    def set_notes(self, notes: list) -> None:
        if not notes:
            self.notes_label.setText("")
            self.notes_label.setVisible(False)
            return
        self.notes_label.setText(f"Ghi chú: {len(notes)}")
        self.notes_label.setToolTip("\n".join(f"• {note}" for note in notes))
        self.notes_label.setVisible(True)

    def set_status(self, message: str, error: bool = False, running: bool = False) -> None:
        short = message if len(message) <= 240 else message[:237] + "…"
        self.status_label.setText(short)
        self.status_label.setToolTip(message)
        self._status_error = bool(error)
        self._status_running = bool(running) and not error
        self._paint_status_colors()

    def set_actions(self, idle: bool, running: bool) -> None:
        for button in (self.btn_tts, self.btn_video, self.btn_upload, self.btn_all):
            button.setEnabled(idle)
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)
        if not running:
            self.set_pause_ui(False)

    def set_pause_ui(self, paused: bool) -> None:
        if paused:
            self.btn_pause.setText("Tiếp tục")
            set_icon(self.btn_pause, "play", "success", 14)
        else:
            self.btn_pause.setText("Tạm dừng")
            set_icon(self.btn_pause, "pause", "warning", 14)

    def apply_theme(self) -> None:
        self.log_console.apply_theme()
        self._paint_status_colors()

    def _paint_status_colors(self) -> None:
        if self._status_error:
            color = dot = theme.DANGER
        elif self._status_running:
            color = dot = theme.BLUE
        else:
            color = theme.TEXT_MUTED
            dot = theme.TEXT_FAINT
        self.status_label.setStyleSheet(f"color:{color};")
        self.status_dot.setStyleSheet(f"background:{dot}; border-radius:4px;")
