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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
        self.setFixedWidth(32)
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
        total_steps: int = 5,
    ) -> None:
        super().__init__(parent)
        self.step_id = step_id
        self.step_num = step_num
        self.total_steps = total_steps
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
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.icon_box = QLabel()
        self.icon_box.setFixedSize(28, 28)
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
            pin_color = (
                QColor("#00D2FF" if theme.is_dark() else "#0284C7")
                if self._state == "running"
                else QColor("#64748b")
            )
            painter.setPen(QPen(pin_color, 1.5))
            painter.setBrush(QBrush(QColor("#0f172a" if theme.is_dark() else "#ffffff")))
            painter.drawEllipse(QPointF(1, cy), pin_radius, pin_radius)

        # Draw right connector pin (output)
        if self.step_num < self.total_steps:
            pin_color = (
                QColor("#00D2FF" if theme.is_dark() else "#0284C7")
                if self._state == "running"
                else QColor("#64748b")
            )
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
        self._settings: Optional[Dict[str, Any]] = None

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

        # Target Languages row
        lang_row = QHBoxLayout()
        lang_row.setSpacing(12)
        lbl_lang = QLabel("Ngôn ngữ mục tiêu:")
        lbl_lang.setStyleSheet("font-weight: 600;")
        lang_row.addWidget(lbl_lang)

        self.lang_checkboxes_layout = QHBoxLayout()
        self.lang_checkboxes_layout.setSpacing(12)
        self._lang_checkboxes: Dict[str, QCheckBox] = {}

        popular_languages = [
            ("vi", "Tiếng Việt (vi)"),
            ("en", "Tiếng Anh (en)"),
            ("ja", "Tiếng Nhật (ja)"),
            ("zh", "Tiếng Trung (zh)"),
        ]
        for code, label in popular_languages:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._lang_checkboxes[code] = cb
            self.lang_checkboxes_layout.addWidget(cb)

        lang_row.addLayout(self.lang_checkboxes_layout)
        lang_row.addStretch(1)
        quick_layout.addLayout(lang_row)

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

        # Step buttons
        self.btn_script = QPushButton("Chạy Kịch bản")
        self.btn_script.setToolTip(
            "Lấy transcript YouTube và tạo/dịch kịch bản sang các ngôn ngữ mục tiêu."
        )
        set_icon(self.btn_script, "file-text", "accent", 13)

        self.btn_tts = QPushButton("Chạy Lồng tiếng")
        self.btn_tts.setToolTip(
            "Chia đoạn, gọi TTS theo cue/văn bản & xuất MP3/SRT."
        )
        set_icon(self.btn_tts, "mic", "accent", 13)

        self.btn_video = QPushButton("Chạy Video")
        self.btn_video.setToolTip(
            "Ghép MP4, khung hình, nhạc nền & phụ đề bằng FFmpeg."
        )
        set_icon(self.btn_video, "film", "accent", 13)

        self.btn_thumbnail = QPushButton("Chạy Thumbnail")
        self.btn_thumbnail.setToolTip(
            "AI tạo ảnh bìa Thumbnail theo tiêu đề và nội dung kịch bản."
        )
        set_icon(self.btn_thumbnail, "image", "accent", 13)

        self.btn_upload = QPushButton("Chạy Upload")
        self.btn_upload.setToolTip(
            "Tự động đăng tải các video đã xuất lên kênh YouTube."
        )
        set_icon(self.btn_upload, "upload", "accent", 13)

        self.btn_script.clicked.connect(lambda: self.run_requested.emit("script"))
        self.btn_tts.clicked.connect(lambda: self.run_requested.emit("tts"))
        self.btn_video.clicked.connect(lambda: self.run_requested.emit("video"))
        self.btn_thumbnail.clicked.connect(lambda: self.run_requested.emit("thumbnail"))
        self.btn_upload.clicked.connect(lambda: self.run_requested.emit("upload"))

        # Node cards with glowing ring (5 nodes)
        self.card_script = WorkflowNodeCard(
            "script",
            1,
            "1. Kịch bản",
            "Lấy transcript YouTube & Dịch AI",
            "file-text",
            self.btn_script,
            total_steps=5,
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
        )

        self.nodes: Dict[str, WorkflowNodeCard] = {
            "script": self.card_script,
            "tts": self.card_tts,
            "video": self.card_video,
            "thumbnail": self.card_thumbnail,
            "upload": self.card_upload,
        }
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

        # Pause and Stop buttons
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
            from core.languages import language_name
            display = name or language_name(code)
        except Exception:
            display = name or code
        label = f"{display} ({code})" if code not in display else display
        cb = QCheckBox(label)
        cb.setChecked(checked)
        self._lang_checkboxes[code] = cb
        self.lang_checkboxes_layout.addWidget(cb)
        return cb

    def _on_run_full_clicked(self) -> None:
        url = self.get_youtube_url()
        if url:
            if self._settings is None:
                self._settings = {}
            self.apply_to(self._settings)
        self.run_requested.emit("all")

    def _on_run_project_clicked(self) -> None:
        self.run_requested.emit("all")

    # ── Settings Load / Apply ────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        auto = settings.get("automation") or {}
        url = auto.get("youtube_url", "")
        self.set_youtube_url(url)

        target_langs = auto.get("target_languages")
        script_langs = (settings.get("script") or {}).get("languages")

        codes_from_script: List[str] = []
        if isinstance(script_langs, list):
            for item in script_langs:
                if isinstance(item, str) and item:
                    codes_from_script.append(item)
                elif isinstance(item, dict) and "language" in item:
                    codes_from_script.append(item["language"])

        # Dynamically add checkboxes for any additional languages
        for code in (target_langs or []) + codes_from_script:
            if code and code not in self._lang_checkboxes:
                self._add_language_checkbox(code, checked=False)

        if target_langs is not None and isinstance(target_langs, list):
            self.set_target_languages(target_langs)
        elif codes_from_script:
            self.set_target_languages(codes_from_script)
        else:
            for code in ("vi", "en", "ja", "zh"):
                if code in self._lang_checkboxes:
                    self._lang_checkboxes[code].setChecked(True)

    def apply_to(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        if "automation" not in settings or not isinstance(settings["automation"], dict):
            settings["automation"] = {}
        settings["automation"]["youtube_url"] = self.get_youtube_url()
        settings["automation"]["target_languages"] = self.get_target_languages()

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
        buttons = [
            self.btn_script,
            self.btn_tts,
            self.btn_video,
            self.btn_thumbnail,
            self.btn_upload,
            self.btn_run_full,
            self.btn_run_project,
        ]
        if hasattr(self, "btn_all") and self.btn_all not in buttons:
            buttons.append(self.btn_all)
        for button in buttons:
            button.setEnabled(idle)

        self.url_input.setEnabled(idle)
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
