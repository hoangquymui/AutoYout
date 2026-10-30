"""Thumbnail sub-tab: title hooks, AI background, template render and save."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.thumbnail import (
    TEMPLATES,
    attach_thumbnail,
    build_image_prompt,
    generate_image,
    render_thumbnail,
    suggest_titles,
)
from ui import theme
from ui.components.icons import set_icon
from ui.components.page import paint_status, wrap_scroll
from utils.files import load_json, open_in_explorer
from utils.logger import get_logger

logger = get_logger("ui.thumbnail")

_PROVIDERS = [
    ("Pollinations (miễn phí)", "pollinations"),
    ("Imagen (Gemini API)", "imagen"),
    ("Frame từ video đã render", "frame"),
]
_COLOR_LABELS = {"green": "Xanh", "red": "Đỏ"}


class TitleWorker(QThread):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, transcript: str, api_key: str, model: str,
                 language: str, parent=None) -> None:
        super().__init__(parent)
        self._transcript = transcript
        self._api_key = api_key
        self._model = model
        self._language = language

    def run(self) -> None:
        try:
            titles = suggest_titles(
                self._transcript, self._api_key, model=self._model,
                language=self._language)
            self.done.emit(list(titles))
        except Exception as exc:
            logger.error("Gợi ý tiêu đề lỗi: %s", exc)
            self.failed.emit(str(exc))


class ImageWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, provider: str, prompt: str, out_path: Path, *,
                 api_key: str, model: str, seconds: float, ffmpeg: str,
                 parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._prompt = prompt
        self._out_path = out_path
        self._api_key = api_key
        self._model = model
        self._seconds = seconds
        self._ffmpeg = ffmpeg
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            path = generate_image(
                self._provider, self._prompt, self._out_path,
                api_key=self._api_key, model=self._model,
                seconds=self._seconds, ffmpeg=self._ffmpeg,
            )
        except Exception as exc:
            logger.error("Tạo ảnh nền lỗi: %s", exc)
            self.failed.emit(str(exc))
            return
        if self._cancel.is_set():
            self.failed.emit("Đã huỷ tạo ảnh nền.")
            return
        self.done.emit(str(path))


class ThumbnailPanel(QWidget):
    thumbnail_saved = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._manager = None
        self._base = ""
        self._transcript = ""
        self._auto_prompt = ""
        self._bg_path: Optional[Path] = None
        self._preview_file: Optional[Path] = None
        self._pixmap: Optional[QPixmap] = None
        self._highlights: List[Tuple[str, str]] = []
        self._title_worker: Optional[TitleWorker] = None
        self._image_worker: Optional[ImageWorker] = None
        self._status_level = "muted"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        outer.addWidget(wrap_scroll(content))

        columns = QHBoxLayout(content)
        columns.setContentsMargins(12, 12, 12, 8)
        columns.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(8)
        columns.addLayout(left, 3)
        right = QVBoxLayout()
        right.setSpacing(8)
        columns.addLayout(right, 2)

        left.addWidget(self._build_info_group())
        left.addWidget(self._build_highlight_group())
        left.addWidget(self._build_image_group())
        left.addWidget(self._build_template_group())
        left.addStretch(1)

        right.addWidget(self._build_preview_group())
        right.addStretch(1)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._render_preview)

        self.title_edit.textChanged.connect(self._on_title_changed)
        self._apply_theme_styles()
        self._refresh_buttons()

    # ── construction ─────────────────────────────────────────────────────────
    def _build_info_group(self) -> QGroupBox:
        box = QGroupBox("Nội dung && tiêu đề")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("File"))
        self.item_combo = QComboBox()
        self.item_combo.setMinimumWidth(200)
        self.item_combo.currentIndexChanged.connect(self._on_item_changed)
        row.addWidget(self.item_combo, 1)
        refresh = QPushButton()
        refresh.setObjectName("iconButton")
        refresh.setFixedSize(30, 30)
        set_icon(refresh, "refresh", "muted", 15)
        refresh.setToolTip("Quét lại danh sách file")
        refresh.clicked.connect(self._refresh_items)
        row.addWidget(refresh)
        layout.addLayout(row)

        self.title_edit = QPlainTextEdit()
        self.title_edit.setPlaceholderText("Tiêu đề hiển thị trên thumbnail (in hoa khi render).")
        self.title_edit.setFixedHeight(70)
        layout.addWidget(self.title_edit)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.suggest_btn = QPushButton("Gợi ý tiêu đề")
        set_icon(self.suggest_btn, "zap", "accent", 14)
        self.suggest_btn.clicked.connect(self._suggest_titles)
        self.transcript_label = QLabel("Chưa có transcript.")
        self.transcript_label.setObjectName("MutedLabel")
        self.transcript_label.setWordWrap(True)
        row2.addWidget(self.suggest_btn)
        row2.addWidget(self.transcript_label, 1)
        layout.addLayout(row2)

        self.suggest_list = QListWidget()
        self.suggest_list.setFixedHeight(90)
        self.suggest_list.itemClicked.connect(self._apply_suggestion)
        layout.addWidget(self.suggest_list)
        return box

    def _build_highlight_group(self) -> QGroupBox:
        box = QGroupBox("Từ khoá tô màu")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.highlight_edit = QLineEdit()
        self.highlight_edit.setPlaceholderText("Từ cần tô (khớp nguyên từ)…")
        self.highlight_color = QComboBox()
        self.highlight_color.addItem("Xanh", "green")
        self.highlight_color.addItem("Đỏ", "red")
        add_btn = QPushButton("Thêm")
        add_btn.clicked.connect(self._add_highlight)
        self.highlight_edit.returnPressed.connect(self._add_highlight)
        row.addWidget(self.highlight_edit, 1)
        row.addWidget(self.highlight_color)
        row.addWidget(add_btn)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.highlight_list = QListWidget()
        self.highlight_list.setFixedHeight(80)
        remove_btn = QPushButton("Bỏ")
        set_icon(remove_btn, "trash", "muted", 13)
        remove_btn.clicked.connect(self._remove_highlight)
        row2.addWidget(self.highlight_list, 1)
        row2.addWidget(remove_btn)
        layout.addLayout(row2)
        return box

    def _build_image_group(self) -> QGroupBox:
        box = QGroupBox("Ảnh nền")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        self.provider_combo = QComboBox()
        for label, value in _PROVIDERS:
            self.provider_combo.addItem(label, value)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("Nguồn ảnh", self.provider_combo)

        self.style_edit = QLineEdit()
        self.style_edit.setPlaceholderText("cinematic dramatic lighting, photo-realistic")
        form.addRow("Style", self.style_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Để trống sẽ dùng key ở tab Kịch bản / Video")
        form.addRow("API key Gemini", self.api_key_edit)

        self.frame_spin = QDoubleSpinBox()
        self.frame_spin.setRange(0.0, 3600.0)
        self.frame_spin.setDecimals(1)
        self.frame_spin.setSuffix(" giây")
        self.frame_spin.setValue(5.0)
        form.addRow("Mốc lấy frame", self.frame_spin)
        layout.addLayout(form)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("Prompt tạo ảnh nền (tự điền theo tiêu đề/transcript).")
        self.prompt_edit.setFixedHeight(80)
        layout.addWidget(self.prompt_edit)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.image_btn = QPushButton("Tạo ảnh nền")
        self.image_btn.setObjectName("primaryButton")
        set_icon(self.image_btn, "image", "white", 14)
        self.image_btn.clicked.connect(self._generate_background)
        self.image_cancel_btn = QPushButton("Huỷ")
        set_icon(self.image_cancel_btn, "x", "muted", 13)
        self.image_cancel_btn.setEnabled(False)
        self.image_cancel_btn.clicked.connect(self._cancel_background)
        self.image_status = QLabel("Chưa có ảnh nền.")
        self.image_status.setObjectName("MutedLabel")
        self.image_status.setWordWrap(True)
        row.addWidget(self.image_btn)
        row.addWidget(self.image_cancel_btn)
        row.addWidget(self.image_status, 1)
        layout.addLayout(row)
        return box

    def _build_template_group(self) -> QGroupBox:
        box = QGroupBox("Template && chữ")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        self.template_list = QListWidget()
        self.template_list.setFixedHeight(104)
        for template in TEMPLATES.values():
            item = QListWidgetItem(f"{template.name} — {template.description}")
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            self.template_list.addItem(item)
        self.template_list.currentItemChanged.connect(self._on_template_changed)
        layout.addWidget(self.template_list)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Palette"))
        self.palette_combo = QComboBox()
        self.palette_combo.addItem("Mặc định (trắng/xanh/đỏ)", "default")
        self.palette_combo.addItem("Theo cấu hình", "custom")
        self.palette_combo.currentIndexChanged.connect(self._on_render_option_changed)
        row.addWidget(self.palette_combo)

        row.addWidget(QLabel("Cỡ chữ"))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(40, 150)
        self.font_spin.setSingleStep(2)
        self.font_spin.setValue(96)
        self.font_spin.setSuffix(" px")
        self.font_spin.valueChanged.connect(self._on_render_option_changed)
        row.addWidget(self.font_spin)
        row.addStretch(1)
        self.auto_preview = QCheckBox("Xem trước tự động")
        self.auto_preview.setChecked(True)
        row.addWidget(self.auto_preview)
        layout.addLayout(row)
        return box

    def _build_preview_group(self) -> QGroupBox:
        box = QGroupBox("Xem trước")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)
        self.preview_label = QLabel("Chưa có ảnh nền")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 270)
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.preview_btn = QPushButton("Xem trước")
        set_icon(self.preview_btn, "image", "muted", 14)
        self.preview_btn.clicked.connect(self._render_preview)
        self.save_btn = QPushButton("Lưu thumbnail")
        self.save_btn.setObjectName("primaryButton")
        set_icon(self.save_btn, "check", "white", 14)
        self.save_btn.clicked.connect(self._save)
        self.open_btn = QPushButton("Mở file")
        set_icon(self.open_btn, "folder-open", "muted", 14)
        self.open_btn.clicked.connect(self._open_thumbnail)
        buttons.addWidget(self.preview_btn)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.open_btn)
        layout.addLayout(buttons)

        self.status_label = QLabel("Chọn file trong project để bắt đầu.")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._apply_theme_styles()
        return box

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        thumb = settings.get("thumbnail") or {}
        index = self.provider_combo.findData(str(thumb.get("provider") or "pollinations"))
        self.provider_combo.setCurrentIndex(max(0, index))
        self.style_edit.setText(str(thumb.get("image_style") or ""))
        self.api_key_edit.setText(str(thumb.get("api_key") or ""))
        self.frame_spin.setValue(float(thumb.get("frame_seconds", 5.0) or 0.0))
        self.font_spin.setValue(int(thumb.get("font_size", 96) or 96))
        index = self.palette_combo.findData(str(thumb.get("palette") or "default"))
        self.palette_combo.setCurrentIndex(max(0, index))
        template_id = str(thumb.get("template") or "drama_left")
        for row in range(self.template_list.count()):
            item = self.template_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == template_id:
                self.template_list.setCurrentRow(row)
                break
        self._on_provider_changed()

    def apply_to(self, settings: Dict[str, Any]) -> None:
        thumb = settings.setdefault("thumbnail", {})
        thumb["provider"] = self.provider_combo.currentData() or "pollinations"
        thumb["image_model"] = str(
            (settings.get("thumbnail") or {}).get("image_model")
            or (settings.get("video") or {}).get("ai_model")
            or "imagen-4.0-generate-001")
        thumb["image_style"] = self.style_edit.text().strip()
        thumb["template"] = self._current_template_id()
        thumb["palette"] = self.palette_combo.currentData() or "default"
        thumb["font_size"] = int(self.font_spin.value())
        thumb["frame_seconds"] = round(float(self.frame_spin.value()), 2)
        thumb["api_key"] = self.api_key_edit.text().strip()
        thumb.setdefault("title_max_chars", 60)
        thumb.setdefault("color_primary", "#3BFF35")
        thumb.setdefault("color_secondary", "#FF3B30")
        self._settings = settings

    def apply_theme(self) -> None:
        self._apply_theme_styles()

    def _apply_theme_styles(self) -> None:
        self.preview_label.setStyleSheet(
            f"border:1px solid {theme.BORDER}; border-radius:8px;"
            f"background:{theme.SURFACE_ALT}; color:{theme.TEXT_DIM}; padding:6px;")

    # ── project / items ──────────────────────────────────────────────────────
    def set_project(self, manager) -> None:
        self._manager = manager
        self._base = ""
        self._bg_path = None
        self._preview_file = None
        self._highlights = []
        self.highlight_list.clear()
        self.suggest_list.clear()
        self._refresh_items()

    def _refresh_items(self) -> None:
        previous = self._base
        self.item_combo.blockSignals(True)
        self.item_combo.clear()
        if self._manager is not None:
            for item in self._manager.get_items():
                if item.text.strip():
                    self.item_combo.addItem(item.display_name, item.base)
        index = self.item_combo.findData(previous)
        if index < 0 and self.item_combo.count():
            index = 0
        if index >= 0:
            self.item_combo.setCurrentIndex(index)
        self.item_combo.blockSignals(False)
        self._on_item_changed()

    def _on_item_changed(self, _index: int = -1) -> None:
        self._base = str(self.item_combo.currentData() or "")
        self.suggest_list.clear()
        self.highlight_list.clear()
        self._highlights = []
        self._bg_path = None
        self._preview_file = None
        item = self._manager.get_item(self._base) if (self._manager and self._base) else None
        if item is None:
            self.title_edit.blockSignals(True)
            self.title_edit.setPlainText("")
            self.title_edit.blockSignals(False)
            self.prompt_edit.clear()
            self._transcript = ""
            self.transcript_label.setText("Chưa chọn file.")
            self._show_placeholder("Chưa có file trong project.")
            self._refresh_buttons()
            return
        title = str(item.metadata.get("title") or item.base)
        self.title_edit.blockSignals(True)
        self.title_edit.setPlainText(title)
        self.title_edit.blockSignals(False)
        self._transcript = self._load_transcript(self._base, item)
        self.transcript_label.setText(
            f"Transcript: {len(self._transcript):,} ký tự.".replace(",", ".")
            if self._transcript.strip() else "Chưa có transcript — dùng nội dung input.")
        saved = Path(str(item.metadata.get("thumbnail_path") or ""))
        if not saved.is_file():
            saved = Path(self._manager.thumbs_dir) / f"{self._base}.jpg"
        if saved.is_file():
            self._bg_path = saved
        self.prompt_edit.clear()
        self._auto_prompt = ""
        self._auto_fill_prompt()
        self._set_status("Sẵn sàng tạo thumbnail.", "muted")
        if self._bg_path is not None:
            self._schedule_preview()
        else:
            self._show_placeholder("Chưa có ảnh nền — bấm “Tạo ảnh nền”.")
        self._refresh_buttons()

    def _load_transcript(self, base: str, item) -> str:
        if self._manager is None:
            return item.text
        data = load_json(Path(self._manager.script_dir) / f"{base}.json", None)
        if isinstance(data, dict):
            segments = data.get("segments") or []
            text = " ".join(
                str(seg.get("text") or "") for seg in segments
                if isinstance(seg, dict))
            if text.strip():
                return text
        return item.text

    # ── titles ───────────────────────────────────────────────────────────────
    def _suggest_titles(self) -> None:
        if not self._base:
            self._set_status("Hãy chọn file trước.", "warning")
            return
        if not self._transcript.strip():
            self._set_status("File không có nội dung để gợi ý tiêu đề.", "warning")
            return
        if self._title_worker is not None:
            return
        item = self._manager.get_item(self._base) if self._manager else None
        language = getattr(item, "language", "") or "vi"
        script = self._settings.get("script") or {}
        key = self._api_key()
        if not key:
            self._set_status(
                "Thiếu API key Gemini — nhập ở tab Kịch bản hoặc ô API key bên dưới.",
                "warning")
            return
        self._title_worker = TitleWorker(
            self._transcript, key, str(script.get("model") or "gemini-2.0-flash"),
            language, self)
        self._title_worker.done.connect(self._on_titles)
        self._title_worker.failed.connect(self._on_titles_failed)
        self._title_worker.finished.connect(self._on_title_worker_finished)
        self.suggest_btn.setEnabled(False)
        self._set_status("Đang gợi ý tiêu đề…", "running")
        self._title_worker.start()

    def _on_titles(self, titles: list) -> None:
        self.suggest_list.clear()
        for title in titles:
            self.suggest_list.addItem(str(title))
        self._set_status(f"Đã gợi ý {len(titles)} tiêu đề.", "success")
        if titles:
            self.title_edit.setPlainText(str(titles[0]))

    def _on_titles_failed(self, message: str) -> None:
        self._set_status(message, "error")

    def _on_title_worker_finished(self) -> None:
        worker = self._title_worker
        self._title_worker = None
        if worker is not None:
            worker.deleteLater()
        self.suggest_btn.setEnabled(True)

    def _apply_suggestion(self, item: QListWidgetItem) -> None:
        self.title_edit.setPlainText(item.text())

    # ── highlights ───────────────────────────────────────────────────────────
    def _add_highlight(self) -> None:
        word = self.highlight_edit.text().strip()
        if not word:
            return
        color = str(self.highlight_color.currentData() or "green")
        for existing, _ in self._highlights:
            if existing.casefold() == word.casefold():
                self._set_status(f"Từ “{word}” đã có trong danh sách.", "warning")
                return
        self._highlights.append((word, color))
        entry = QListWidgetItem(f"{word} — {_COLOR_LABELS.get(color, color)}")
        entry.setIcon(self._color_icon(color))
        self.highlight_list.addItem(entry)
        self.highlight_edit.clear()
        self._schedule_preview()

    def _remove_highlight(self) -> None:
        row = self.highlight_list.currentRow()
        if row < 0 or row >= len(self._highlights):
            return
        del self._highlights[row]
        self.highlight_list.takeItem(row)
        self._schedule_preview()

    @staticmethod
    def _color_icon(color: str) -> QIcon:
        pixmap = QPixmap(12, 12)
        pixmap.fill(QColor("#3BFF35" if color == "green" else "#FF3B30"))
        return QIcon(pixmap)

    # ── background ───────────────────────────────────────────────────────────
    def _on_provider_changed(self, _index: int = -1) -> None:
        provider = str(self.provider_combo.currentData() or "")
        self.frame_spin.setEnabled(provider == "frame")
        self.api_key_edit.setEnabled(provider in ("imagen", "pollinations"))

    def _generate_background(self) -> None:
        if self._image_worker is not None:
            return
        if self._manager is None or not self._base:
            self._set_status("Hãy chọn file trước.", "warning")
            return
        provider = str(self.provider_combo.currentData() or "pollinations")
        prompt = self.prompt_edit.toPlainText().strip()
        if provider != "frame" and not prompt:
            self._set_status("Prompt ảnh đang trống.", "warning")
            return
        out = Path(self._manager.thumbs_dir) / f"{self._base}_bg.jpg"
        seconds = float(self.frame_spin.value())
        if provider == "frame":
            job = self._manager.video_for_item(self._base)
            video = Path(job.path) if job is not None else Path()
            if not video.is_file():
                self._set_status(
                    "Chưa có video đã render cho file này — hãy tạo video trước.",
                    "warning")
                return
            prompt = str(video)
        thumb = self._settings.get("thumbnail") or {}
        model = str(thumb.get("image_model") or (self._settings.get("video") or {}).get("ai_model")
                    or "imagen-4.0-generate-001")
        ffmpeg = str((self._settings.get("paths") or {}).get("ffmpeg") or "")
        self._image_worker = ImageWorker(
            provider, prompt, out, api_key=self._api_key(), model=model,
            seconds=seconds, ffmpeg=ffmpeg, parent=self)
        self._image_worker.done.connect(self._on_background_done)
        self._image_worker.failed.connect(self._on_background_failed)
        self._image_worker.finished.connect(self._on_image_worker_finished)
        self.image_btn.setEnabled(False)
        self.image_cancel_btn.setEnabled(True)
        paint_status(self.image_status, "running")
        self.image_status.setText("Đang tạo ảnh nền…")
        self._set_status("Đang tạo ảnh nền…", "running")
        self._image_worker.start()

    def _cancel_background(self) -> None:
        if self._image_worker is not None:
            self._image_worker.cancel()
            self.image_cancel_btn.setEnabled(False)
            self.image_status.setText("Đang huỷ…")

    def _on_background_done(self, path: str) -> None:
        self._bg_path = Path(path)
        paint_status(self.image_status, "success")
        self.image_status.setText(f"Đã tạo {self._bg_path.name}.")
        self._set_status("Đã tạo ảnh nền.", "success")
        self._render_preview()

    def _on_background_failed(self, message: str) -> None:
        paint_status(self.image_status, "error")
        self.image_status.setText(message)
        self._set_status(message, "error")

    def _on_image_worker_finished(self) -> None:
        worker = self._image_worker
        self._image_worker = None
        if worker is not None:
            worker.deleteLater()
        self.image_btn.setEnabled(True)
        self.image_cancel_btn.setEnabled(False)
        self._refresh_buttons()

    # ── prompt ───────────────────────────────────────────────────────────────
    def _on_title_changed(self) -> None:
        self._auto_fill_prompt()
        self._schedule_preview()

    def _auto_fill_prompt(self) -> None:
        current = self.prompt_edit.toPlainText().strip()
        if current and current != self._auto_prompt:
            return
        title = self.title_edit.toPlainText().strip()
        if not title:
            return
        excerpt = self._transcript[:600]
        prompt = build_image_prompt(
            title, excerpt, self.style_edit.text().strip(), self._current_template_id())
        self._auto_prompt = prompt
        self.prompt_edit.setPlainText(prompt)

    # ── template / preview ───────────────────────────────────────────────────
    def _current_template_id(self) -> str:
        item = self.template_list.currentItem()
        if item is not None:
            return str(item.data(Qt.ItemDataRole.UserRole) or "drama_left")
        return "drama_left"

    def _on_template_changed(self, *_args) -> None:
        self._auto_fill_prompt()
        self._schedule_preview()

    def _on_render_option_changed(self, *_args) -> None:
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        if self.auto_preview.isChecked():
            self._preview_timer.start(250)

    def _palette(self) -> Dict[str, str]:
        thumb = self._settings.get("thumbnail") or {}
        colors = {
            "text": "#FFFFFF",
            "green": str(thumb.get("color_primary") or "#3BFF35"),
            "red": str(thumb.get("color_secondary") or "#FF3B30"),
        }
        if str(self.palette_combo.currentData() or "default") == "default":
            return {}
        return colors

    def _render_preview(self) -> None:
        if self._bg_path is None or not self._bg_path.is_file():
            self._show_placeholder("Chưa có ảnh nền — bấm “Tạo ảnh nền”.")
            return
        title = self.title_edit.toPlainText().strip()
        if not title:
            self._set_status("Hãy nhập tiêu đề thumbnail.", "warning")
            return
        out_dir = Path(tempfile.gettempdir()) / "AutoYout" / "preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"thumb_{(self._base or 'item')}.jpg"
        try:
            render_thumbnail(
                self._bg_path, title, self._highlights, self._current_template_id(),
                self._palette(), out, font_size=int(self.font_spin.value()))
        except Exception as exc:
            logger.error("Render thumbnail lỗi: %s", exc)
            self._set_status(str(exc), "error")
            return
        self._preview_file = out
        self._pixmap = QPixmap(str(out))
        self._show_pixmap()
        self._set_status("Đã cập nhật xem trước.", "success")

    def _show_pixmap(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        target = self.preview_label.size()
        self.preview_label.setPixmap(self._pixmap.scaled(
            target, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def _show_placeholder(self, message: str) -> None:
        self._pixmap = None
        self.preview_label.setText(message)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._show_pixmap()

    # ── save / open ──────────────────────────────────────────────────────────
    def _save(self) -> None:
        if self._manager is None or not self._base:
            self._set_status("Hãy chọn file trước.", "warning")
            return
        if self._bg_path is None or not self._bg_path.is_file():
            self._set_status("Hãy tạo ảnh nền trước khi lưu.", "warning")
            return
        title = self.title_edit.toPlainText().strip()
        if not title:
            self._set_status("Hãy nhập tiêu đề thumbnail.", "warning")
            return
        out = Path(self._manager.thumbs_dir) / f"{self._base}_draft.jpg"
        try:
            render_thumbnail(
                self._bg_path, title, self._highlights, self._current_template_id(),
                self._palette(), out, font_size=int(self.font_spin.value()))
            target = attach_thumbnail(self._manager, self._base, out)
            Path(out).unlink(missing_ok=True)
        except Exception as exc:
            logger.error("Lưu thumbnail lỗi: %s", exc)
            self._set_status(str(exc), "error")
            return
        self._preview_file = target
        self._pixmap = QPixmap(str(target))
        self._show_pixmap()
        self._set_status(f"Đã lưu {target.name}.", "success")
        self.thumbnail_saved.emit(self._base)

    def _open_thumbnail(self) -> None:
        if self._manager is None or not self._base:
            return
        target = Path(self._manager.thumbs_dir) / f"{self._base}.jpg"
        if target.is_file():
            open_in_explorer(target)
        else:
            open_in_explorer(Path(self._manager.thumbs_dir))

    # ── helpers ──────────────────────────────────────────────────────────────
    def _api_key(self) -> str:
        key = self.api_key_edit.text().strip()
        if key:
            return key
        settings = self._settings or {}
        return str(
            (settings.get("thumbnail") or {}).get("api_key")
            or (settings.get("script") or {}).get("api_key")
            or (settings.get("video") or {}).get("ai_api_key")
            or "").strip()

    def _refresh_buttons(self) -> None:
        busy = self._image_worker is not None or self._title_worker is not None
        has_item = bool(self._base)
        self.image_btn.setEnabled(not busy and has_item)
        self.suggest_btn.setEnabled(not busy and has_item)
        self.save_btn.setEnabled(has_item)

    def _set_status(self, message: str, level: str = "muted") -> None:
        self._status_level = level
        paint_status(self.status_label, level)
        self.status_label.setText(message)

    def stop_and_wait(self, timeout_ms: int = 5000) -> None:
        if self._image_worker is not None:
            self._image_worker.cancel()
            self._image_worker.wait(timeout_ms)
        if self._title_worker is not None:
            self._title_worker.wait(timeout_ms)
