"""Automation tab: run the pipeline step-by-step or end-to-end and watch logs with n8n-style workflow nodes."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.language_profiles import load_profiles
from core.languages import language_name, normalize_language, normalize_language_code
from ui import theme
from ui.components.icons import icon, set_icon
from ui.components.log_console import LogConsole
from ui.components.page import PageHeader, wrap_scroll
from ui.tab_settings import ToggleSwitch


class WorkflowConnector(QWidget):
    """Sleek horizontal connector line with an arrow linking workflow steps (n8n style)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(24)
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
            glow_color = QColor(theme.ACCENT_SOFT if theme.is_dark() else theme.ACCENT)
            pen = QPen(glow_color, 2.5)
            brush = QBrush(glow_color)
        else:
            line_color = QColor(theme.BORDER_STRONG if theme.is_dark() else theme.BORDER_CARD)
            pen = QPen(line_color, 1.5, Qt.PenStyle.DashLine)
            brush = QBrush(line_color)

        painter.setPen(pen)
        painter.drawLine(0, int(cy), int(w - 6), int(cy))

        painter.setBrush(brush)
        arrow = QPolygonF([
            QPointF(w - 1, cy),
            QPointF(w - 7, cy - 4),
            QPointF(w - 7, cy + 4),
        ])
        painter.drawPolygon(arrow)


class WorkflowNodeCard(QFrame):
    """An n8n-style workflow node card with status chip and glowing ring when running."""

    toggled_step = Signal(str, bool)

    def __init__(
        self,
        step_id: str,
        step_num: int,
        title: str,
        subtitle: str,
        icon_name: str,
        run_button: QPushButton,
        parent: Optional[QWidget] = None,
        total_steps: int = 5,
        toggleable: bool = False,
    ) -> None:
        super().__init__(parent)
        self.step_id = step_id
        self.step_num = step_num
        self.total_steps = total_steps
        self.title_text = title
        self.subtitle_text = subtitle
        self.icon_name = icon_name
        self.run_button = run_button
        self.toggleable = toggleable
        self._step_enabled = True
        self._pipeline_idle = True
        self._state = "idle"  # idle | running | done | error
        self._glow_phase = 0.0

        self.setObjectName(f"WorkflowNode_{step_id}")
        self.setMinimumHeight(125)
        self.setMinimumWidth(155)

        # Pulsing timer for the active glowing halo
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(45)
        self._glow_timer.timeout.connect(self._pulse_glow)

        self._shadow_effect = QGraphicsDropShadowEffect(self)
        self._shadow_effect.setOffset(0, 0)
        self._shadow_effect.setEnabled(False)
        self.setGraphicsEffect(self._shadow_effect)

        # Internal card layout: spacious, modern and responsive
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Top row: Icon + Title + Status chip
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self.icon_box = QLabel()
        self.icon_box.setFixedSize(22, 22)
        self.icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_icon()
        header_row.addWidget(self.icon_box)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("PanelTitle")
        self.title_label.setStyleSheet("font-weight: 700; font-size: 12.5px;")
        header_row.addWidget(self.title_label, 1)

        self.status_chip = QLabel("Chờ chạy")
        self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self.status_chip)
        layout.addLayout(header_row)

        # Subtitle label: full card width for comfortable word wrapping without squishing
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("MutedLabel")
        self.subtitle_label.setStyleSheet("font-size: 11px; line-height: 1.3;")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        layout.addStretch(1)

        # Bottom row: optional toggle on left + run button cleanly right-aligned
        bottom_row = QHBoxLayout()
        self.enable_toggle: Optional[ToggleSwitch] = None
        if self.toggleable:
            self.enable_toggle = ToggleSwitch(checked=True, parent=self)
            self.enable_toggle.setToolTip(f"Bật / tắt bước {title}")
            self.enable_toggle.toggled.connect(self._on_toggle_changed)
            bottom_row.addWidget(self.enable_toggle)
        bottom_row.addStretch(1)
        bottom_row.addWidget(run_button)
        layout.addLayout(bottom_row)

        self._apply_style()

    def _on_toggle_changed(self, checked: bool) -> None:
        self.set_step_enabled(checked)
        self.toggled_step.emit(self.step_id, bool(checked))

    def is_step_enabled(self) -> bool:
        return bool(self._step_enabled)

    def set_step_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._step_enabled = enabled
        if self.enable_toggle is not None and self.enable_toggle.isChecked() != enabled:
            self.enable_toggle.blockSignals(True)
            self.enable_toggle.setChecked(enabled)
            self.enable_toggle.blockSignals(False)
        if not enabled:
            self._glow_timer.stop()
            self._shadow_effect.setEnabled(False)
        self.run_button.setEnabled(self._pipeline_idle and enabled)
        self._apply_style()
        self.update()

    def set_pipeline_idle(self, idle: bool) -> None:
        self._pipeline_idle = bool(idle)
        self.run_button.setEnabled(self._pipeline_idle and self._step_enabled)
        if self.enable_toggle is not None:
            self.enable_toggle.setEnabled(self._pipeline_idle)

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
        if not self._step_enabled:
            bg = "rgba(17, 24, 39, 0.42)" if dark else "rgba(241, 245, 249, 0.55)"
            border = "1px dashed rgba(100, 116, 139, 0.35)"
            chip_bg = "rgba(100, 116, 139, 0.08)"
            chip_color = "rgba(148, 163, 184, 0.55)" if dark else "rgba(100, 116, 139, 0.55)"
            chip_text = "Đã tắt"
            title_color = "rgba(148, 163, 184, 0.45)" if dark else "rgba(100, 116, 139, 0.5)"
            sub_color = "rgba(148, 163, 184, 0.38)" if dark else "rgba(148, 163, 184, 0.6)"
        else:
            bg = theme.SURFACE if dark else "#FFFFFF"
            title_color = theme.TEXT
            sub_color = theme.TEXT_MUTED
            if self._state == "running":
                border = f"2px solid {theme.ACCENT_SOFT if dark else theme.ACCENT}"
                chip_bg = "rgba(14, 165, 233, 0.18)" if dark else "rgba(2, 132, 199, 0.15)"
                chip_color = theme.ACCENT_SOFT if dark else theme.ACCENT
                chip_text = "⚡ ĐANG CHẠY..."
            elif self._state == "done":
                border = f"2px solid {theme.SUCCESS}"
                chip_bg = "rgba(52, 211, 153, 0.18)" if dark else "rgba(5, 150, 105, 0.15)"
                chip_color = theme.SUCCESS
                chip_text = "✓ Hoàn thành"
            elif self._state == "error":
                border = f"2px solid {theme.DANGER}"
                chip_bg = "rgba(248, 113, 113, 0.18)" if dark else "rgba(220, 38, 38, 0.15)"
                chip_color = theme.DANGER
                chip_text = "✕ Có lỗi"
            else:
                border = f"1px solid {theme.BORDER}"
                chip_bg = "rgba(100, 116, 139, 0.12)"
                chip_color = theme.TEXT_MUTED
                chip_text = "Chờ chạy"

        self.setStyleSheet(f"""
            QFrame#{self.objectName()} {{
                background-color: {bg};
                border: {border};
                border-radius: 12px;
            }}
        """)
        self.title_label.setStyleSheet(f"font-weight: 700; font-size: 12.5px; color: {title_color};")
        self.subtitle_label.setStyleSheet(f"font-size: 11px; line-height: 1.3; color: {sub_color};")
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
        role = "muted" if not getattr(self, "_step_enabled", True) else "accent"
        ic = icon(self.icon_name, theme.icon_color(role), 18)
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
            pin_color = (
                QColor(theme.ACCENT_SOFT if theme.is_dark() else theme.ACCENT)
                if self._state == "running"
                else QColor(theme.BORDER_STRONG if theme.is_dark() else theme.BORDER)
            )
            painter.setPen(QPen(pin_color, 1.5))
            painter.setBrush(QBrush(QColor(theme.SURFACE_ALT if theme.is_dark() else theme.SURFACE)))
            painter.drawEllipse(QPointF(1, cy), pin_radius, pin_radius)

        # Draw right connector pin (output)
        if self.step_num < self.total_steps:
            pin_color = (
                QColor(theme.ACCENT_SOFT if theme.is_dark() else theme.ACCENT)
                if self._state == "running"
                else QColor(theme.BORDER_STRONG if theme.is_dark() else theme.BORDER)
            )
            painter.setPen(QPen(pin_color, 1.5))
            painter.setBrush(QBrush(QColor(theme.SURFACE_ALT if theme.is_dark() else theme.SURFACE)))
            painter.drawEllipse(QPointF(w - 1, cy), pin_radius, pin_radius)


class AutomationTab(QWidget):
    run_requested = Signal(str)
    pause_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._status_error = False
        self._status_running = False
        self._settings: Optional[Dict[str, Any]] = None

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(wrap_scroll(content))

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        header = PageHeader(
            "Automation",
            "Chạy từng bước hoặc toàn bộ quy trình tự động hóa — có thể tạm dừng và tiếp tục.",
        )
        self.stage_label = QLabel("Sẵn sàng")
        self.stage_label.setObjectName("StageChip")
        header.add_action(self.stage_label)
        root.addWidget(header)

        # ── 1. Quick Input Bar (Khung nhập liệu nhanh) ────────────────────────
        quick_box = QGroupBox("Khởi chạy nhanh quy trình (Quick Input Bar)")
        quick_layout = QVBoxLayout(quick_box)
        quick_layout.setSpacing(10)

        # YouTube URL row
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        lbl_url = QLabel("Link YouTube:")
        lbl_url.setStyleSheet("font-weight: 600;")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Nhập link YouTube để bắt đầu quy trình tự động (ví dụ: https://www.youtube.com/watch?v=...)"
        )
        self.url_input.setClearButtonEnabled(False)
        self.btn_paste = QPushButton("Dán link")
        set_icon(self.btn_paste, "clipboard", "accent", 14)
        self.btn_paste.setToolTip("Dán link YouTube từ clipboard")
        self.btn_paste.clicked.connect(self._paste_url)

        self.btn_clear = QPushButton("Xoá")
        set_icon(self.btn_clear, "trash", "muted", 14)
        self.btn_clear.setToolTip("Xoá link YouTube đã nhập")
        self.btn_clear.clicked.connect(self._clear_url)

        url_row.addWidget(lbl_url)
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.btn_paste)
        url_row.addWidget(self.btn_clear)
        quick_layout.addLayout(url_row)

        # Topic Name row
        topic_row = QHBoxLayout()
        topic_row.setSpacing(8)
        lbl_topic = QLabel("Tên đề tài:")
        lbl_topic.setStyleSheet("font-weight: 600;")
        lbl_topic.setMinimumWidth(84)
        self.topic_input = QLineEdit()
        self.topic_input.setPlaceholderText(
            "Nhập tên đề tài / tên file kịch bản (để trống nếu muốn giữ tên mặc định từ YouTube)"
        )
        topic_row.addWidget(lbl_topic)
        topic_row.addWidget(self.topic_input, 1)
        quick_layout.addLayout(topic_row)

        # Target Languages row
        lang_row = QHBoxLayout()
        lang_row.setSpacing(12)
        lbl_lang = QLabel("Ngôn ngữ mục tiêu:")
        lbl_lang.setStyleSheet("font-weight: 600;")
        lang_row.addWidget(lbl_lang, 0, Qt.AlignmentFlag.AlignTop)

        self.lang_checkboxes_layout = QGridLayout()
        self.lang_checkboxes_layout.setContentsMargins(0, 0, 0, 0)
        self.lang_checkboxes_layout.setHorizontalSpacing(14)
        self.lang_checkboxes_layout.setVerticalSpacing(6)
        self._lang_checkboxes: Dict[str, QCheckBox] = {}

        lang_row.addLayout(self.lang_checkboxes_layout, 1)
        quick_layout.addLayout(lang_row)
        self.sync_languages()

        # Action Buttons row
        btn_action_row = QHBoxLayout()
        btn_action_row.setSpacing(10)

        self.btn_run_full = QPushButton("⚡ THỰC HIỆN TOÀN BỘ (END-TO-END)")
        self.btn_run_full.setObjectName("primaryButton")
        self.btn_run_full.setToolTip(
            "Tự động chạy toàn bộ quy trình: Kịch bản → Lồng tiếng → Tạo Video → Thumbnail → Đăng YouTube."
        )
        set_icon(self.btn_run_full, "zap", "white", 16)
        self.btn_run_full.setMinimumHeight(40)
        self.btn_run_full.clicked.connect(self._on_run_full_clicked)

        self.btn_run_project = QPushButton("▶ Chạy theo Project hiện tại")
        self.btn_run_project.setToolTip(
            "Chạy với các file kịch bản có sẵn trong project hiện tại mà không cần nhập link YouTube."
        )
        set_icon(self.btn_run_project, "play", "accent", 14)
        self.btn_run_project.setMinimumHeight(40)
        self.btn_run_project.clicked.connect(self._on_run_project_clicked)

        # Backward compatibility alias
        self.btn_all = self.btn_run_full

        btn_action_row.addWidget(self.btn_run_full, 3)
        btn_action_row.addWidget(self.btn_run_project, 2)
        quick_layout.addLayout(btn_action_row)

        root.addWidget(quick_box)

        # ── 2. n8n-style workflow pipeline (5 Nodes) ─────────────────────────
        controls = QGroupBox("Quy trình tự động hóa (Workflow Pipeline)")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setSpacing(12)

        # Step buttons with smallButton styling for compact fit inside cards
        self.btn_script = QPushButton("Chạy Kịch bản")
        self.btn_script.setObjectName("smallButton")
        self.btn_script.setToolTip(
            "Lấy transcript YouTube và tạo/dịch kịch bản sang các ngôn ngữ mục tiêu."
        )
        set_icon(self.btn_script, "file-text", "accent", 13)

        self.btn_tts = QPushButton("Chạy Lồng tiếng")
        self.btn_tts.setObjectName("smallButton")
        self.btn_tts.setToolTip(
            "Chia đoạn, gọi TTS theo cue/văn bản & xuất MP3/SRT."
        )
        set_icon(self.btn_tts, "mic", "accent", 13)

        self.btn_video = QPushButton("Chạy Video")
        self.btn_video.setObjectName("smallButton")
        self.btn_video.setToolTip(
            "Ghép MP4, khung hình, nhạc nền & phụ đề bằng FFmpeg."
        )
        set_icon(self.btn_video, "film", "accent", 13)

        self.btn_thumbnail = QPushButton("Chạy Thumbnail")
        self.btn_thumbnail.setObjectName("smallButton")
        self.btn_thumbnail.setToolTip(
            "AI tạo ảnh bìa Thumbnail theo tiêu đề và nội dung kịch bản."
        )
        set_icon(self.btn_thumbnail, "image", "accent", 13)

        self.btn_upload = QPushButton("Chạy Upload")
        self.btn_upload.setObjectName("smallButton")
        self.btn_upload.setToolTip(
            "Tự động đăng tải các video đã xuất lên kênh YouTube."
        )
        set_icon(self.btn_upload, "upload", "accent", 13)

        self.btn_script.clicked.connect(lambda: self.run_requested.emit("script"))
        self.btn_tts.clicked.connect(lambda: self.run_requested.emit("tts"))
        self.btn_video.clicked.connect(lambda: self.run_requested.emit("video"))
        self.btn_thumbnail.clicked.connect(lambda: self.run_requested.emit("thumbnail"))
        self.btn_upload.clicked.connect(lambda: self.run_requested.emit("upload"))

        # Node cards with glowing ring (5 nodes) - All 5 steps fully toggleable
        self.card_script = WorkflowNodeCard(
            "script",
            1,
            "1. Kịch bản",
            "Lấy transcript YouTube & Dịch AI",
            "file-text",
            self.btn_script,
            total_steps=5,
            toggleable=True,
        )
        self.conn_1 = WorkflowConnector()

        self.card_tts = WorkflowNodeCard(
            "tts",
            2,
            "2. Lồng tiếng",
            "Chia đoạn, gọi TTS & xuất MP3/SRT",
            "mic",
            self.btn_tts,
            total_steps=5,
            toggleable=True,
        )
        self.conn_2 = WorkflowConnector()

        self.card_video = WorkflowNodeCard(
            "video",
            3,
            "3. Tạo Video",
            "Ghép MP4, khung hình & phụ đề",
            "film",
            self.btn_video,
            total_steps=5,
            toggleable=True,
        )
        self.conn_3 = WorkflowConnector()

        self.card_thumbnail = WorkflowNodeCard(
            "thumbnail",
            4,
            "4. Thumbnail",
            "AI tạo ảnh bìa theo kịch bản",
            "image",
            self.btn_thumbnail,
            total_steps=5,
            toggleable=True,
        )
        self.conn_4 = WorkflowConnector()

        self.card_upload = WorkflowNodeCard(
            "upload",
            5,
            "5. Đăng YouTube",
            "Đăng tải theo kênh & ngôn ngữ",
            "upload",
            self.btn_upload,
            total_steps=5,
            toggleable=True,
        )

        self.nodes: Dict[str, WorkflowNodeCard] = {
            "script": self.card_script,
            "tts": self.card_tts,
            "video": self.card_video,
            "thumbnail": self.card_thumbnail,
            "upload": self.card_upload,
        }
        for node in self.nodes.values():
            node.toggled_step.connect(self._on_step_toggled)
        self.connectors: List[WorkflowConnector] = [
            self.conn_1,
            self.conn_2,
            self.conn_3,
            self.conn_4,
        ]

        flow_row = QHBoxLayout()
        flow_row.setSpacing(4)
        flow_row.addWidget(self.card_script, 1)
        flow_row.addWidget(self.conn_1, 0)
        flow_row.addWidget(self.card_tts, 1)
        flow_row.addWidget(self.conn_2, 0)
        flow_row.addWidget(self.card_video, 1)
        flow_row.addWidget(self.conn_3, 0)
        flow_row.addWidget(self.card_thumbnail, 1)
        flow_row.addWidget(self.conn_4, 0)
        flow_row.addWidget(self.card_upload, 1)
        controls_layout.addLayout(flow_row)

        # Pause and Stop control bar with dedicated container and styling
        ctrl_bar = QFrame()
        ctrl_bar.setObjectName("PipelineControlBar")
        ctrl_bar.setStyleSheet(f"""
            QFrame#PipelineControlBar {{
                background: {theme.SURFACE_ALT};
                border: 1px dashed {theme.BORDER};
                border-radius: 8px;
            }}
        """)
        ctrl_layout = QHBoxLayout(ctrl_bar)
        ctrl_layout.setContentsMargins(10, 6, 10, 6)
        ctrl_layout.setSpacing(10)

        lbl_ctrl = QLabel("Điều khiển thực thi:")
        lbl_ctrl.setStyleSheet(f"font-size: 11.5px; font-weight: 600; color: {theme.TEXT_MUTED};")
        ctrl_layout.addWidget(lbl_ctrl)

        self.btn_pause = QPushButton("Tạm dừng")
        self.btn_pause.setObjectName("pauseButton")
        self.btn_pause.setMinimumHeight(32)
        self.btn_pause.setMinimumWidth(110)
        self.btn_stop = QPushButton("Dừng hẳn")
        self.btn_stop.setObjectName("stopButton")
        self.btn_stop.setMinimumHeight(32)
        self.btn_stop.setMinimumWidth(110)
        set_icon(self.btn_pause, "pause", "warning", 14)
        set_icon(self.btn_stop, "stop", "danger", 14)
        self.btn_pause.clicked.connect(self.pause_requested.emit)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)

        ctrl_layout.addWidget(self.btn_pause)
        ctrl_layout.addWidget(self.btn_stop)
        ctrl_layout.addStretch(1)

        controls_layout.addSpacing(6)
        controls_layout.addWidget(ctrl_bar)

        root.addWidget(controls)

        # ── 3. Progress ───────────────────────────────────────────────────────
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

        # ── 4. Status & Notes ─────────────────────────────────────────────────
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
        self.status_label = QLabel("Nhập link YouTube hoặc chọn project để bắt đầu.")
        self.status_label.setObjectName("StatusText")
        self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        status_row.addWidget(self.status_label, 1)
        root.addLayout(status_row)

        # ── 5. Log Console ────────────────────────────────────────────────────
        self.log_console = LogConsole(title="NHẬT KÝ TIẾN TRÌNH TỰ ĐỘNG HOÁ")
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

    # ── Quick Input Bar Helpers ──────────────────────────────────────────────
    def _paste_url(self) -> None:
        clip = QApplication.clipboard()
        text = clip.text().strip() if clip else ""
        if text:
            self.url_input.setText(text)

    def _clear_url(self) -> None:
        self.url_input.clear()

    def get_youtube_url(self) -> str:
        return self.url_input.text().strip()

    def set_youtube_url(self, url: str) -> None:
        self.url_input.setText(str(url or "").strip())

    def get_topic_name(self) -> str:
        return self.topic_input.text().strip()

    def set_topic_name(self, topic: str) -> None:
        self.topic_input.setText(str(topic or "").strip())

    def sync_languages(
        self,
        profiles: Optional[List[Any]] = None,
        extra_codes: Optional[Iterable[str]] = None,
    ) -> None:
        checked_before = {
            code: cb.isChecked() for code, cb in self._lang_checkboxes.items()
        }
        if profiles is None:
            try:
                profiles = load_profiles()
            except Exception:
                profiles = []
        items: List[tuple[str, str]] = [("vi", "Tiếng Việt (vi)")]
        seen = {"vi"}
        if profiles:
            for prof in profiles:
                code = normalize_language_code(getattr(prof, "language", "") or "", "")
                if not code or code in seen:
                    continue
                seen.add(code)
                custom = getattr(prof, "name", "") or language_name(code)
                label = f"{custom} ({code})" if code not in custom else custom
                items.append((code, label))

        if extra_codes:
            for raw_c in extra_codes:
                code = normalize_language_code(str(raw_c or ""), "")
                if not code or code in seen:
                    continue
                seen.add(code)
                label = f"{language_name(code)} ({code})"
                items.append((code, label))

        while self.lang_checkboxes_layout.count():
            child = self.lang_checkboxes_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._lang_checkboxes.clear()

        cols_per_row = 4
        for idx, (code, label) in enumerate(items):
            cb = QCheckBox(label)
            cb.setChecked(checked_before.get(code, True))
            self._lang_checkboxes[code] = cb
            self.lang_checkboxes_layout.addWidget(cb, idx // cols_per_row, idx % cols_per_row)
        self.lang_checkboxes_layout.setColumnStretch(cols_per_row, 1)

    def get_target_languages(self) -> List[str]:
        return [code for code, cb in self._lang_checkboxes.items() if cb.isChecked()]

    def set_target_languages(self, languages: List[str]) -> None:
        target_set = set(languages or [])
        for code, cb in self._lang_checkboxes.items():
            cb.setChecked(code in target_set)

    def _add_language_checkbox(self, code: str, name: str = "", checked: bool = True) -> QCheckBox:
        if code in self._lang_checkboxes:
            cb = self._lang_checkboxes[code]
            cb.setChecked(checked)
            return cb
        try:
            display = name or language_name(code)
        except Exception:
            display = name or code
        label = f"{display} ({code})" if code not in display else display
        cb = QCheckBox(label)
        cb.setChecked(checked)
        idx = len(self._lang_checkboxes)
        cols_per_row = 4
        self._lang_checkboxes[code] = cb
        self.lang_checkboxes_layout.addWidget(cb, idx // cols_per_row, idx % cols_per_row)
        return cb

    def _any_step_enabled(self) -> bool:
        return any(node.is_step_enabled() for node in self.nodes.values())

    def _on_step_toggled(self, step_id: str, enabled: bool) -> None:
        any_on = self._any_step_enabled()
        self.btn_run_full.setEnabled(any_on)
        self.btn_run_project.setEnabled(any_on)
        if self._settings is not None:
            self.apply_to(self._settings)
        if not any_on:
            self.set_status(
                "Tất cả các bước đều đã tắt. Vui lòng bật ít nhất 1 bước để chạy pipeline.",
                error=True,
            )
        else:
            if self._status_error and "Tất cả các bước đều đã tắt" in self.status_label.text():
                self.set_status("Đã sẵn sàng thực hiện.", error=False)

    def _on_run_full_clicked(self) -> None:
        if not self._any_step_enabled():
            QMessageBox.warning(
                self,
                "Không có bước nào được bật",
                "Tất cả các bước tự động hóa đều đang bị tắt.\nVui lòng bật ít nhất một bước quy trình để tiếp tục.",
            )
            return

        url = self.get_youtube_url()
        if self.card_script.is_step_enabled() and not url:
            res = QMessageBox.question(
                self,
                "Chưa nhập link YouTube",
                "Bước 1 (Lấy kịch bản YouTube) đang được bật nhưng bạn chưa nhập link YouTube.\n\n"
                "• Chọn 'Yes' để tiếp tục chạy (bỏ qua Bước 1 và dùng file kịch bản có sẵn trong project)\n"
                "• Chọn 'No' để dừng lại và nhập link YouTube.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                self.url_input.setFocus()
                return

        if url:
            if not (url.startswith("http://") or url.startswith("https://") or "youtu" in url):
                QMessageBox.warning(
                    self,
                    "Link YouTube không hợp lệ",
                    "Định dạng đường dẫn YouTube không đúng. Vui lòng nhập link hợp lệ (ví dụ: https://www.youtube.com/watch?v=...)",
                )
                self.url_input.setFocus()
                return

        if self._settings is None:
            self._settings = {}
        self.apply_to(self._settings)
        self.run_requested.emit("all")

    def _on_run_project_clicked(self) -> None:
        if not self._any_step_enabled():
            QMessageBox.warning(
                self,
                "Không có bước nào được bật",
                "Tất cả các bước tự động hóa đều đang bị tắt.\nVui lòng bật ít nhất một bước quy trình để tiếp tục.",
            )
            return
        if self._settings is None:
            self._settings = {}
        self.apply_to(self._settings)
        self.run_requested.emit("all")

    # ── Settings Load / Apply ────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        auto = settings.get("automation") or {}
        url = auto.get("youtube_url", "")
        self.set_youtube_url(url)
        self.set_topic_name(str(auto.get("topic_name") or ""))

        self.card_script.set_step_enabled(bool(auto.get("script_enabled", True)))
        self.card_tts.set_step_enabled(bool(auto.get("tts_enabled", True)))
        self.card_video.set_step_enabled(bool(auto.get("video_enabled", True)))
        self.card_thumbnail.set_step_enabled(bool(auto.get("thumbnail_enabled", True)))
        self.card_upload.set_step_enabled(bool(auto.get("upload_enabled", True)))

        target_langs = auto.get("target_languages")
        script_langs = (settings.get("script") or {}).get("languages")

        codes_from_script: List[str] = []
        if isinstance(script_langs, list):
            for item in script_langs:
                if isinstance(item, str) and item:
                    codes_from_script.append(item)
                elif isinstance(item, dict) and "language" in item:
                    codes_from_script.append(item["language"])

        extra = list(target_langs or []) + codes_from_script
        self.sync_languages(extra_codes=extra)

        if target_langs is not None and isinstance(target_langs, list):
            self.set_target_languages(target_langs)
        elif codes_from_script:
            self.set_target_languages(codes_from_script)
        else:
            for cb in self._lang_checkboxes.values():
                cb.setChecked(True)

        self._on_step_toggled("", True)

    def apply_to(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        if "automation" not in settings or not isinstance(settings["automation"], dict):
            settings["automation"] = {}
        settings["automation"]["youtube_url"] = self.get_youtube_url()
        settings["automation"]["topic_name"] = self.get_topic_name()
        settings["automation"]["target_languages"] = self.get_target_languages()
        settings["automation"]["script_enabled"] = self.card_script.is_step_enabled()
        settings["automation"]["tts_enabled"] = self.card_tts.is_step_enabled()
        settings["automation"]["video_enabled"] = self.card_video.is_step_enabled()
        settings["automation"]["thumbnail_enabled"] = self.card_thumbnail.is_step_enabled()
        settings["automation"]["upload_enabled"] = self.card_upload.is_step_enabled()

    # ── State updates (called by MainWindow / PipelineWorker) ────────────────
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
            low = detail.lower()
            if "kịch bản" in low or "transcript" in low:
                self.set_active_step("script")
            elif "lồng tiếng" in low or "tts" in low:
                self.set_active_step("tts")
            elif "video" in low or "mp4" in low:
                self.set_active_step("video")
            elif "thumbnail" in low or "ảnh bìa" in low:
                self.set_active_step("thumbnail")
            elif "upload" in low or "đăng" in low:
                self.set_active_step("upload")

    def set_active_step(self, step_name: str) -> None:
        """Set the active step to 'running' with a glowing ring, preceding to 'done'."""
        steps = ["script", "tts", "video", "thumbnail", "upload"]
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
        if "kịch bản" in low or "script" in low or "transcript" in low:
            self.set_active_step("script")
            self.overall_desc.setText("(Bước 1/5: Kịch bản)")
        elif "lồng tiếng" in low or "tts" in low:
            self.set_active_step("tts")
            self.overall_desc.setText("(Bước 2/5: Lồng tiếng)")
        elif "video" in low:
            self.set_active_step("video")
            self.overall_desc.setText("(Bước 3/5: Tạo Video)")
        elif "thumbnail" in low or "ảnh bìa" in low or "thumb" in low:
            self.set_active_step("thumbnail")
            self.overall_desc.setText("(Bước 4/5: Thumbnail)")
        elif "đăng" in low or "upload" in low or "youtube" in low:
            self.set_active_step("upload")
            self.overall_desc.setText("(Bước 5/5: Upload YouTube)")
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
        can_run = idle and self._any_step_enabled()
        for button in (self.btn_run_full, self.btn_run_project):
            button.setEnabled(can_run)
        if hasattr(self, "btn_all") and self.btn_all not in (self.btn_run_full, self.btn_run_project):
            self.btn_all.setEnabled(can_run)
        for node in self.nodes.values():
            node.set_pipeline_idle(idle)

        self.url_input.setEnabled(idle)
        self.topic_input.setEnabled(idle)
        self.btn_paste.setEnabled(idle)
        self.btn_clear.setEnabled(idle)
        for cb in self._lang_checkboxes.values():
            cb.setEnabled(idle)

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
