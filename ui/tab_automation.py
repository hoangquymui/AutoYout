"""Automation tab: run the pipeline step-by-step or end-to-end and watch logs with n8n-style workflow nodes."""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
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
from ui.components.icons import icon, set_icon
from ui.components.log_console import LogConsole
from ui.components.page import PageHeader


class WorkflowConnector(QWidget):
    """Sleek horizontal connector line with an arrow linking workflow steps (n8n style)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(36)
        self.setMinimumHeight(80)
        self._active = False

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        cy = h / 2.0

        if self._active:
            glow_color = QColor("#00D2FF" if theme.is_dark() else "#0284C7")
            pen = QPen(glow_color, 2.5)
            brush = QBrush(glow_color)
        else:
            line_color = QColor("#475569" if theme.is_dark() else "#CBD5E1")
            pen = QPen(line_color, 1.5, Qt.PenStyle.DashLine)
            brush = QBrush(line_color)

        painter.setPen(pen)
        painter.drawLine(0, int(cy), int(w - 7), int(cy))

        painter.setBrush(brush)
        arrow = QPolygonF([
            QPointF(w - 1, cy),
            QPointF(w - 8, cy - 4),
            QPointF(w - 8, cy + 4),
        ])
        painter.drawPolygon(arrow)


class WorkflowNodeCard(QFrame):
    """An n8n-style workflow node card with status chip and glowing ring when running."""

    def __init__(
        self,
        step_id: str,
        step_num: int,
        title: str,
        subtitle: str,
        icon_name: str,
        run_button: QPushButton,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.step_id = step_id
        self.step_num = step_num
        self.title_text = title
        self.subtitle_text = subtitle
        self.icon_name = icon_name
        self.run_button = run_button
        self._state = "idle"  # idle | running | done | error
        self._glow_phase = 0.0

        self.setObjectName(f"WorkflowNode_{step_id}")
        self.setMinimumHeight(96)

        # Pulsing timer for the active glowing halo
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(45)
        self._glow_timer.timeout.connect(self._pulse_glow)

        self._shadow_effect = QGraphicsDropShadowEffect(self)
        self._shadow_effect.setOffset(0, 0)
        self._shadow_effect.setEnabled(False)
        self.setGraphicsEffect(self._shadow_effect)

        # Internal card layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.icon_box = QLabel()
        self.icon_box.setFixedSize(30, 30)
        self.icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_icon()
        top_row.addWidget(self.icon_box)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PanelTitle")
        self.title_label.setStyleSheet("font-weight: 700; font-size: 13px;")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("MutedLabel")
        self.subtitle_label.setStyleSheet("font-size: 11px;")
        self.subtitle_label.setWordWrap(True)
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)
        top_row.addLayout(title_col, 1)

        self.status_chip = QLabel("Chờ chạy")
        self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.status_chip)
        layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        bottom_row.addWidget(run_button)
        layout.addLayout(bottom_row)

        self._apply_style()

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if self._state == state:
            return
        self._state = state
        self._apply_style()
        if state == "running":
            self._shadow_effect.setEnabled(True)
            self._glow_timer.start()
        elif state == "done":
            self._glow_timer.stop()
            self._shadow_effect.setEnabled(True)
            self._shadow_effect.setBlurRadius(14)
            self._shadow_effect.setColor(QColor(16, 185, 129, 140))
        elif state == "error":
            self._glow_timer.stop()
            self._shadow_effect.setEnabled(True)
            self._shadow_effect.setBlurRadius(14)
            self._shadow_effect.setColor(QColor(239, 68, 68, 140))
        else:
            self._glow_timer.stop()
            self._shadow_effect.setEnabled(False)
        self.update()

    def _pulse_glow(self) -> None:
        self._glow_phase += 0.12
        radius = int(18 + 9 * math.sin(self._glow_phase))
        alpha = int(175 + 55 * math.sin(self._glow_phase))
        self._shadow_effect.setBlurRadius(radius)
        glow_color = (
            QColor(0, 210, 255, alpha) if theme.is_dark() else QColor(2, 132, 199, alpha)
        )
        self._shadow_effect.setColor(glow_color)

    def _apply_style(self) -> None:
        dark = theme.is_dark()
        bg = "#111827" if dark else "#FFFFFF"
        if self._state == "running":
            border = "2px solid #00D2FF" if dark else "2px solid #0284C7"
            chip_bg = "rgba(0, 210, 255, 0.18)" if dark else "rgba(2, 132, 199, 0.15)"
            chip_color = "#38BDF8" if dark else "#0284C7"
            chip_text = "⚡ ĐANG CHẠY..."
        elif self._state == "done":
            border = "2px solid #10B981"
            chip_bg = "rgba(16, 185, 129, 0.18)"
            chip_color = "#10B981"
            chip_text = "✓ Hoàn thành"
        elif self._state == "error":
            border = "2px solid #EF4444"
            chip_bg = "rgba(239, 68, 68, 0.18)"
            chip_color = "#EF4444"
            chip_text = "✕ Có lỗi"
        else:
            border = "1px solid #334155" if dark else "1px solid #CBD5E1"
            chip_bg = "rgba(100, 116, 139, 0.12)"
            chip_color = "#94A3B8" if dark else "#64748B"
            chip_text = "Chờ chạy"

        self.setStyleSheet(f"""
            QFrame#{self.objectName()} {{
                background-color: {bg};
                border: {border};
                border-radius: 12px;
            }}
        """)
        self.status_chip.setText(chip_text)
        self.status_chip.setStyleSheet(f"""
            background-color: {chip_bg};
            color: {chip_color};
            font-size: 11px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 8px;
            border: 1px solid {chip_color};
        """)
        self._update_icon()

    def _update_icon(self) -> None:
        ic = icon(self.icon_name, theme.icon_color("accent"), 18)
        self.icon_box.setPixmap(ic.pixmap(QSize(18, 18)))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        cy = h / 2.0
        pin_radius = 4

        # Draw left connector pin (input)
        if self.step_num > 1:
            pin_color = QColor("#00D2FF" if self._state == "running" else "#64748b")
            painter.setPen(QPen(pin_color, 1.5))
            painter.setBrush(QBrush(QColor("#0f172a" if theme.is_dark() else "#ffffff")))
            painter.drawEllipse(QPointF(1, cy), pin_radius, pin_radius)

        # Draw right connector pin (output)
        if self.step_num < 3:
            pin_color = QColor("#00D2FF" if self._state == "running" else "#64748b")
            painter.setPen(QPen(pin_color, 1.5))
            painter.setBrush(QBrush(QColor("#0f172a" if theme.is_dark() else "#ffffff")))
            painter.drawEllipse(QPointF(w - 1, cy), pin_radius, pin_radius)


class AutomationTab(QWidget):
    run_requested = Signal(str)
    pause_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
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

        # ── n8n-style workflow pipeline ──────────────────────────────────────
        controls = QGroupBox("Quy trình tự động hóa (Workflow Pipeline)")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setSpacing(12)

        # Step buttons
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

        set_icon(self.btn_tts, "mic", "accent", 14)
        set_icon(self.btn_video, "film", "accent", 14)
        set_icon(self.btn_upload, "upload", "accent", 14)

        self.btn_tts.clicked.connect(lambda: self.run_requested.emit("tts"))
        self.btn_video.clicked.connect(lambda: self.run_requested.emit("video"))
        self.btn_upload.clicked.connect(lambda: self.run_requested.emit("upload"))

        # Node cards with glowing ring
        self.card_tts = WorkflowNodeCard(
            "tts", 1, "1. Lồng tiếng", "Chuyển kịch bản thành giọng đọc (TTS / SRT)", "mic", self.btn_tts
        )
        self.conn_1 = WorkflowConnector()
        self.card_video = WorkflowNodeCard(
            "video", 2, "2. Tạo Video", "Ghép MP4, ảnh nền, nhạc & phụ đề", "film", self.btn_video
        )
        self.conn_2 = WorkflowConnector()
        self.card_upload = WorkflowNodeCard(
            "upload", 3, "3. Upload YouTube", "Tự động đăng tải video lên kênh", "upload", self.btn_upload
        )

        self.nodes: Dict[str, WorkflowNodeCard] = {
            "tts": self.card_tts,
            "video": self.card_video,
            "upload": self.card_upload,
        }
        self.connectors: List[WorkflowConnector] = [self.conn_1, self.conn_2]

        flow_row = QHBoxLayout()
        flow_row.setSpacing(4)
        flow_row.addWidget(self.card_tts, 1)
        flow_row.addWidget(self.conn_1, 0)
        flow_row.addWidget(self.card_video, 1)
        flow_row.addWidget(self.conn_2, 0)
        flow_row.addWidget(self.card_upload, 1)
        controls_layout.addLayout(flow_row)

        # Master Pipeline Controls
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
        self.overall_desc = QLabel("(Tiến trình toàn bộ pipeline)")
        self.overall_desc.setObjectName("MutedLabel")
        progress_layout.addLayout(
            self._progress_block(
                "TỔNG THỂ", self.overall_bar, self.overall_value, self.overall_desc
            )
        )

        self.task_bar = QProgressBar()
        self.task_bar.setRange(0, 100)
        self.task_bar.setValue(0)
        self.task_bar.setTextVisible(False)
        self.task_value = QLabel("0%")
        self.task_value.setObjectName("PercentLabel")
        self.task_desc = QLabel("(Tiến trình file / tác vụ đang xử lý)")
        self.task_desc.setObjectName("MutedLabel")
        progress_layout.addLayout(
            self._progress_block(
                "TÁC VỤ HIỆN TẠI", self.task_bar, self.task_value, self.task_desc
            )
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
    def _progress_block(
        title: str, bar: QProgressBar, value: QLabel, desc: Optional[QLabel] = None
    ) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)
        head = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("PanelTitle")
        head.addWidget(label)
        if desc is not None:
            head.addWidget(desc)
        head.addStretch(1)
        head.addWidget(value)
        block.addLayout(head)
        block.addWidget(bar)
        return block

    # ── state updates (called by MainWindow) ─────────────────────────────────
    def set_overall(self, value: int, detail: str = "") -> None:
        value = max(0, min(100, int(value)))
        self.overall_bar.setValue(value)
        self.overall_value.setText(f"{value}%")
        if detail:
            self.overall_desc.setText(detail)

    def set_task(self, value: int, detail: str = "") -> None:
        value = max(0, min(100, int(value)))
        self.task_bar.setValue(value)
        self.task_value.setText(f"{value}%")
        if detail:
            self.task_desc.setText(detail)

    def set_active_step(self, step_name: str) -> None:
        """Set the active step to 'running' with a glowing ring, preceding to 'done'."""
        steps = ["tts", "video", "upload"]
        if step_name not in steps:
            return
        idx = steps.index(step_name)
        for i, name in enumerate(steps):
            node = self.nodes.get(name)
            if not node:
                continue
            if i < idx:
                node.set_state("done")
                if i < len(self.connectors):
                    self.connectors[i].set_active(True)
            elif i == idx:
                node.set_state("running")
                if i > 0 and (i - 1) < len(self.connectors):
                    self.connectors[i - 1].set_active(True)
            else:
                node.set_state("idle")
                if i - 1 < len(self.connectors) and i - 1 >= 0:
                    self.connectors[i - 1].set_active(False)

    def set_all_steps_done(self) -> None:
        for node in self.nodes.values():
            node.set_state("done")
        for conn in self.connectors:
            conn.set_active(True)

    def set_step_error(self, step_name: str) -> None:
        node = self.nodes.get(step_name)
        if node:
            node.set_state("error")

    def reset_steps(self) -> None:
        for node in self.nodes.values():
            node.set_state("idle")
        for conn in self.connectors:
            conn.set_active(False)

    def set_stage(self, text: str) -> None:
        self.stage_label.setText(text)
        low = text.lower()
        if "lồng tiếng" in low or "tts" in low:
            self.set_active_step("tts")
            self.overall_desc.setText("(Bước 1/3: Lồng tiếng)")
        elif "video" in low:
            self.set_active_step("video")
            self.overall_desc.setText("(Bước 2/3: Tạo Video)")
        elif "đăng" in low or "upload" in low or "youtube" in low:
            self.set_active_step("upload")
            self.overall_desc.setText("(Bước 3/3: Upload YouTube)")
        elif "hoàn tất" in low or "xong" in low:
            self.set_all_steps_done()
            self.overall_desc.setText("(Đã hoàn tất)")
        elif "lỗi" in low:
            for node in self.nodes.values():
                if node.state == "running":
                    node.set_state("error")
                    break
        elif "sẵn sàng" in low or "chuẩn bị" in low:
            self.reset_steps()
            self.overall_desc.setText("(Tiến trình toàn bộ pipeline)")

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
        for node in self.nodes.values():
            node._apply_style()
        for conn in self.connectors:
            conn.update()

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
