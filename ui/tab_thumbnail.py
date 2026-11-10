"""Thumbnail sub-tab: title hooks, AI background, template render and save."""
from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
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

from core.ai_client import DEFAULT_MODEL
from core.language_profiles import LanguageProfile, load_profiles
from core.languages import language_name, normalize_language, normalize_language_code
from core.thumbnail import (
    TEMPLATES,
    attach_thumbnail,
    build_image_prompt,
    generate_image,
    render_thumbnail,
    suggest_titles,
    translate_thumbnail_keywords,
    translate_thumbnail_titles,
)
from ui import theme
from ui.components.icons import set_icon
from ui.components.page import PageHeader, paint_status, wrap_scroll
from utils.files import load_json, open_in_explorer
from utils.logger import get_logger

logger = get_logger("ui.thumbnail")

_PROVIDERS = [
    ("Cloudflare Workers AI", "cloudflare"),
    ("Pollinations (miễn phí)", "pollinations"),
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


class TranslateWorker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, title: str, target_langs: List[str], api_key: str,
                 source_lang: str = "vi", model: str = DEFAULT_MODEL,
                 keywords: Optional[List[Tuple[str, str]]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._target_langs = target_langs
        self._api_key = api_key
        self._source_lang = source_lang
        self._model = model
        self._keywords = list(keywords or [])

    def run(self) -> None:
        try:
            titles = translate_thumbnail_titles(
                self._title,
                self._target_langs,
                self._api_key,
                source_language=self._source_lang,
                model=self._model,
            )
            kw_map = {}
            if self._keywords:
                try:
                    kw_map = translate_thumbnail_keywords(
                        self._keywords,
                        self._target_langs,
                        self._api_key,
                        source_language=self._source_lang,
                        model=self._model,
                    )
                except Exception as kw_exc:
                    logger.warning("Dịch keywords thumbnail lỗi (tiếp tục): %s", kw_exc)
            self.done.emit({"titles": titles, "keywords": kw_map})
        except Exception as exc:
            logger.error("Dịch tiêu đề thumbnail lỗi: %s", exc)
            self.failed.emit(str(exc))


class ImageWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, provider: str, prompt: str, out_path: Path, *,
                 api_key: str = "", model: str = "", seconds: float = 5.0,
                 ffmpeg: str = "", account_id: str = "", api_token: str = "",
                 steps: int = 4, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._prompt = prompt
        self._out_path = out_path
        self._api_key = api_key
        self._model = model
        self._seconds = seconds
        self._ffmpeg = ffmpeg
        self._account_id = account_id
        self._api_token = api_token
        self._steps = steps
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            path = generate_image(
                self._provider, self._prompt, self._out_path,
                api_key=self._api_key, model=self._model,
                seconds=self._seconds, ffmpeg=self._ffmpeg,
                account_id=self._account_id, api_token=self._api_token,
                steps=self._steps,
            )
        except Exception as exc:
            logger.error("Tạo ảnh nền lỗi: %s", exc)
            self.failed.emit(str(exc))
            return
        if self._cancel.is_set():
            self.failed.emit("Đã huỷ tạo ảnh nền.")
            return
        self.done.emit(str(path))


class PromptEditDialog(QDialog):
    """Dialog chỉnh sửa prompt tạo ảnh thumbnail, cho phép giữ nguyên hoặc tái tạo."""

    def __init__(self, current_prompt: str, on_generate_ai=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sửa Prompt tạo ảnh")
        self.setMinimumSize(560, 320)
        self.resize(620, 360)
        self._on_generate_ai = on_generate_ai

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header_lbl = QLabel("Prompt tạo ảnh nền (AI):")
        header_lbl.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(header_lbl)

        desc_lbl = QLabel(
            "Bạn có thể tùy chỉnh hoặc bổ sung mô tả cảnh cho ảnh nền thumbnail. "
            "Prompt hiện tại được giữ nguyên."
        )
        desc_lbl.setObjectName("MutedLabel")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        self.edit = QPlainTextEdit(self)
        self.edit.setPlainText(current_prompt)
        self.edit.setPlaceholderText("Nhập prompt mô tả cảnh ảnh nền...")
        layout.addWidget(self.edit, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        if on_generate_ai is not None:
            self.gen_btn = QPushButton("⚡ Tạo lại prompt từ tiêu đề")
            set_icon(self.gen_btn, "zap", "accent", 13)
            self.gen_btn.setToolTip("Dùng AI tự động tạo lại prompt dựa trên tiêu đề và kịch bản hiện tại.")
            self.gen_btn.clicked.connect(self._do_generate)
            bottom.addWidget(self.gen_btn)

        bottom.addStretch(1)

        self.cancel_btn = QPushButton("Huỷ")
        self.cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Lưu prompt")
        self.save_btn.setObjectName("primaryButton")
        set_icon(self.save_btn, "check", "white", 13)
        self.save_btn.clicked.connect(self.accept)
        bottom.addWidget(self.save_btn)

        layout.addLayout(bottom)

    def _do_generate(self) -> None:
        if self._on_generate_ai:
            new_prompt = self._on_generate_ai()
            if new_prompt:
                self.edit.setPlainText(new_prompt)

    def get_prompt(self) -> str:
        return self.edit.toPlainText().strip()


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
        self._highlights_by_lang: Dict[str, List[Tuple[str, str]]] = {}
        self._title_worker: Optional[TitleWorker] = None
        self._image_worker: Optional[ImageWorker] = None
        self._translate_worker: Optional[TranslateWorker] = None
        self._status_level = "muted"
        self.lang_checks: Dict[str, QCheckBox] = {}
        self._titles_by_lang: Dict[str, str] = {}
        self._title_edits: Dict[str, QPlainTextEdit] = {}
        self._preview_lang: str = "vi"
        self._gallery_entries: List[Tuple[str, Path]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        outer.addWidget(wrap_scroll(content))

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 10)
        root.setSpacing(10)
        root.addWidget(PageHeader(
            "Thumbnail",
            "Tạo và xuất ảnh bìa YouTube theo từng ngôn ngữ — lưu vào thư mục thumbs/<ngôn_ngữ>.",
        ))

        columns = QHBoxLayout()
        columns.setSpacing(12)
        root.addLayout(columns, 1)

        left = QVBoxLayout()
        left.setSpacing(8)
        columns.addLayout(left, 3)
        right = QVBoxLayout()
        right.setSpacing(8)
        columns.addLayout(right, 2)

        left.addWidget(self._build_languages_group())
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
        self.sync_languages()
        self._apply_theme_styles()
        self._refresh_buttons()

    # ── construction ─────────────────────────────────────────────────────────
    def _build_languages_group(self) -> QGroupBox:
        box = QGroupBox("Ngôn ngữ")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        self._lang_checks_host = QWidget()
        self._lang_checks_layout = QGridLayout(self._lang_checks_host)
        self._lang_checks_layout.setContentsMargins(0, 2, 0, 2)
        self._lang_checks_layout.setHorizontalSpacing(14)
        self._lang_checks_layout.setVerticalSpacing(6)
        layout.addWidget(self._lang_checks_host)
        return box

    def sync_languages(self, profiles: Optional[List[Any]] = None) -> None:
        checked_before = {
            code: cb.isChecked() for code, cb in self.lang_checks.items()
        }
        saved_langs = (self._settings.get("thumbnail") or {}).get("languages")
        if profiles is None:
            try:
                profiles = load_profiles()
            except Exception:
                profiles = []
        items: List[Tuple[str, str]] = [("vi", "Tiếng Việt (vi)")]
        seen = {"vi"}
        if profiles:
            for prof in profiles:
                code = normalize_language_code(getattr(prof, "language", "") or "", "")
                if not code:
                    continue
                custom = getattr(prof, "name", "") or language_name(code)
                label = f"{custom} ({code})" if code not in custom else custom
                if code in seen:
                    items = [(c, label if c == code and getattr(prof, "name", "") else lbl) for c, lbl in items]
                    continue
                seen.add(code)
                items.append((code, label))
        else:
            for code, label in [
                ("en", "Tiếng Anh (en)"),
                ("ja", "Tiếng Nhật (ja)"),
                ("zh", "Tiếng Trung (zh)"),
            ]:
                if code not in seen:
                    seen.add(code)
                    items.append((code, label))

        while self._lang_checks_layout.count():
            child = self._lang_checks_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.lang_checks.clear()

        cols_per_row = 4
        for idx, (code, label) in enumerate(items):
            cb = QCheckBox(label)
            if code in checked_before:
                cb.setChecked(checked_before[code])
            elif isinstance(saved_langs, list) and saved_langs:
                cb.setChecked(code in saved_langs)
            else:
                cb.setChecked(True)
            cb.toggled.connect(self._on_languages_changed)
            self.lang_checks[code] = cb
            self._lang_checks_layout.addWidget(cb, idx // cols_per_row, idx % cols_per_row)
        self._lang_checks_layout.setColumnStretch(cols_per_row, 1)
        self._on_languages_changed()

    def get_selected_languages(self) -> List[str]:
        selected = [code for code, cb in self.lang_checks.items() if cb.isChecked()]
        return selected or ["vi"]

    def _on_languages_changed(self) -> None:
        self._update_language_title_fields()

    def _update_language_title_fields(self) -> None:
        if not hasattr(self, "titles_container_layout"):
            return
        selected_langs = self.get_selected_languages()
        current_item = self._manager.get_item(self._base) if (self._manager and self._base) else None
        base_lang = normalize_language_code(getattr(current_item, "language", "") or "vi")
        langs_ordered = [base_lang] + [l for l in selected_langs if l != base_lang]

        # Preserve any text from existing edits
        for code, edit in list(self._title_edits.items()):
            txt = edit.toPlainText().strip()
            if txt:
                self._titles_by_lang[code] = txt

        # Detach self.title_edit before deleting child widgets
        if self.title_edit.parent() is not None:
            self.title_edit.setParent(None)

        # Clear layout
        while self.titles_container_layout.count():
            child = self.titles_container_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

        self._title_edits.clear()
        for code in langs_ordered:
            lang_label_text = f"{language_name(code)} ({code})"
            wrapper = QWidget()
            w_layout = QVBoxLayout(wrapper)
            w_layout.setContentsMargins(0, 2, 0, 4)
            w_layout.setSpacing(3)

            header = QHBoxLayout()
            header.setSpacing(6)
            is_base = (code == base_lang)
            prefix = "Tiêu đề gốc" if is_base else "Tiêu đề"
            lbl = QLabel(f"{prefix} ({lang_label_text}):")
            lbl.setStyleSheet("font-weight: 600; font-size: 11px;")
            header.addWidget(lbl)
            header.addStretch(1)

            btn_preview = QPushButton("👁 Xem trước")
            btn_preview.setFixedHeight(22)
            btn_preview.setStyleSheet("padding: 1px 8px; font-size: 11px;")
            btn_preview.clicked.connect(lambda _, c=code: self._preview_for_language(c))
            header.addWidget(btn_preview)
            w_layout.addLayout(header)

            if is_base:
                self.title_edit.setParent(wrapper)
                self.title_edit.setFixedHeight(48)
                self.title_edit.setPlaceholderText(f"Nhập tiêu đề {lang_label_text}...")
                val = self._titles_by_lang.get(code, "")
                if val and self.title_edit.toPlainText().strip() != val:
                    self.title_edit.blockSignals(True)
                    self.title_edit.setPlainText(val)
                    self.title_edit.blockSignals(False)
                w_layout.addWidget(self.title_edit)
                self._title_edits[code] = self.title_edit
            else:
                edit = QPlainTextEdit()
                edit.setFixedHeight(48)
                edit.setPlaceholderText(f"Tiêu đề {lang_label_text} (tự dịch khi chọn gợi ý hoặc bấm Dịch)...")
                val = self._titles_by_lang.get(code, "")
                edit.setPlainText(val)
                edit.textChanged.connect(lambda c=code, ed=edit: self._on_sub_title_changed(c, ed))
                w_layout.addWidget(edit)
                self._title_edits[code] = edit

            self.titles_container_layout.addWidget(wrapper)

        self._update_preview_lang_combo(langs_ordered)

    def _on_sub_title_changed(self, code: str, edit: QPlainTextEdit) -> None:
        self._titles_by_lang[code] = edit.toPlainText().strip()
        if getattr(self, "_preview_lang", "") == code:
            self._schedule_preview()

    def _preview_for_language(self, code: str) -> None:
        self._preview_lang = code
        if hasattr(self, "preview_lang_combo"):
            idx = self.preview_lang_combo.findData(code)
            if idx >= 0:
                self.preview_lang_combo.blockSignals(True)
                self.preview_lang_combo.setCurrentIndex(idx)
                self.preview_lang_combo.blockSignals(False)
        self._render_preview()

    def _update_preview_lang_combo(self, langs: List[str]) -> None:
        if not hasattr(self, "preview_lang_combo"):
            return
        cur_code = str(self.preview_lang_combo.currentData() or "")
        self.preview_lang_combo.blockSignals(True)
        self.preview_lang_combo.clear()
        for code in langs:
            self.preview_lang_combo.addItem(f"{language_name(code)} ({code})", code)
        idx = self.preview_lang_combo.findData(cur_code)
        if idx < 0 and langs:
            idx = 0
        if idx >= 0:
            self.preview_lang_combo.setCurrentIndex(idx)
            self._preview_lang = str(self.preview_lang_combo.currentData() or "vi")
        self.preview_lang_combo.blockSignals(False)

    def _on_preview_lang_changed(self, _index: int = -1) -> None:
        self._preview_lang = str(self.preview_lang_combo.currentData() or "vi")
        self._render_preview()

    def get_title_for_lang(self, code: str) -> str:
        code_norm = normalize_language_code(code)
        if code_norm in self._titles_by_lang and self._titles_by_lang[code_norm]:
            return self._titles_by_lang[code_norm]
        if code_norm in self._title_edits:
            return self._title_edits[code_norm].toPlainText().strip()
        return self.title_edit.toPlainText().strip()

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

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.suggest_btn = QPushButton("Gợi ý tiêu đề")
        set_icon(self.suggest_btn, "zap", "accent", 14)
        self.suggest_btn.clicked.connect(self._suggest_titles)

        self.edit_prompt_btn = QPushButton()
        self.edit_prompt_btn.setObjectName("iconButton")
        self.edit_prompt_btn.setFixedSize(30, 30)
        set_icon(self.edit_prompt_btn, "edit", "accent", 15)
        self.edit_prompt_btn.setToolTip("Sửa prompt tạo ảnh (vẫn giữ im prompt hiện tại)")
        self.edit_prompt_btn.clicked.connect(self._open_edit_prompt_dialog)

        self.translate_btn = QPushButton("Dịch sang các thứ tiếng")
        set_icon(self.translate_btn, "globe", "accent", 13)
        self.translate_btn.setToolTip("Dịch tiêu đề gốc sang tất cả các ngôn ngữ đã chọn.")
        self.translate_btn.clicked.connect(lambda: self._translate_titles())

        self.transcript_label = QLabel("Chưa có transcript.")
        self.transcript_label.setObjectName("MutedLabel")
        self.transcript_label.setWordWrap(True)
        row2.addWidget(self.suggest_btn)
        row2.addWidget(self.edit_prompt_btn)
        row2.addWidget(self.translate_btn)
        row2.addWidget(self.transcript_label, 1)
        layout.addLayout(row2)

        self.suggest_list = QListWidget()
        self.suggest_list.setFixedHeight(85)
        self.suggest_list.itemClicked.connect(self._apply_suggestion)
        layout.addWidget(self.suggest_list)

        self.title_edit = QPlainTextEdit()
        self.title_edit.setPlaceholderText("Tiêu đề hiển thị trên thumbnail (in hoa khi render).")
        self.title_edit.setFixedHeight(48)

        self.titles_container = QWidget()
        self.titles_container_layout = QVBoxLayout(self.titles_container)
        self.titles_container_layout.setContentsMargins(0, 4, 0, 0)
        self.titles_container_layout.setSpacing(6)
        layout.addWidget(self.titles_container)
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

        # Headless compat instance (no longer displayed in UI; shared Gemini key from Settings is used)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

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
        self.pick_image_btn = QPushButton("Chọn ảnh từ máy…")
        set_icon(self.pick_image_btn, "folder-open", "muted", 13)
        self.pick_image_btn.setToolTip("Chọn tệp ảnh có sẵn trên máy làm ảnh nền thumbnail.")
        self.pick_image_btn.clicked.connect(self._pick_custom_background)
        self.image_cancel_btn = QPushButton("Huỷ")
        set_icon(self.image_cancel_btn, "x", "muted", 13)
        self.image_cancel_btn.setEnabled(False)
        self.image_cancel_btn.clicked.connect(self._cancel_background)
        self.image_status = QLabel("Chưa có ảnh nền.")
        self.image_status.setObjectName("MutedLabel")
        self.image_status.setWordWrap(True)
        row.addWidget(self.image_btn)
        row.addWidget(self.pick_image_btn)
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

        preview_lang_row = QHBoxLayout()
        preview_lang_row.setSpacing(6)
        preview_lang_row.addWidget(QLabel("Ngôn ngữ xem trước:"))
        self.preview_lang_combo = QComboBox()
        self.preview_lang_combo.currentIndexChanged.connect(self._on_preview_lang_changed)
        preview_lang_row.addWidget(self.preview_lang_combo, 1)
        layout.addLayout(preview_lang_row)

        gallery_row = QHBoxLayout()
        gallery_row.setSpacing(6)
        gallery_row.addWidget(QLabel("Ảnh đã xuất:"))
        self.preview_gallery_combo = QComboBox()
        self.preview_gallery_combo.setMinimumWidth(180)
        self.preview_gallery_combo.currentIndexChanged.connect(self._on_gallery_selected)
        gallery_row.addWidget(self.preview_gallery_combo, 1)
        self.prev_thumb_btn = QPushButton("◀")
        self.prev_thumb_btn.setFixedWidth(30)
        self.prev_thumb_btn.setToolTip("Ảnh trước")
        self.prev_thumb_btn.clicked.connect(lambda: self._step_gallery(-1))
        self.next_thumb_btn = QPushButton("▶")
        self.next_thumb_btn.setFixedWidth(30)
        self.next_thumb_btn.setToolTip("Ảnh tiếp theo")
        self.next_thumb_btn.clicked.connect(lambda: self._step_gallery(1))
        gallery_row.addWidget(self.prev_thumb_btn)
        gallery_row.addWidget(self.next_thumb_btn)
        layout.addLayout(gallery_row)

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
        self.cancel_thumb_btn = QPushButton("Huỷ")
        set_icon(self.cancel_thumb_btn, "x", "muted", 14)
        self.cancel_thumb_btn.setToolTip("Huỷ ảnh hiện tại để tạo lại ảnh mới.")
        self.cancel_thumb_btn.clicked.connect(self._reset_or_cancel_thumbnail)
        self.open_btn = QPushButton("Mở file")
        set_icon(self.open_btn, "folder-open", "muted", 14)
        self.open_btn.clicked.connect(self._open_thumbnail)
        buttons.addWidget(self.preview_btn)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.cancel_thumb_btn)
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
        provider_val = str(thumb.get("provider") or "cloudflare")
        if provider_val in ("gemini", "imagen"):
            provider_val = "cloudflare"
        index = self.provider_combo.findData(provider_val)
        if index < 0:
            index = 0
        self.provider_combo.setCurrentIndex(index)
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
        self.sync_languages()
        saved_langs = thumb.get("languages")
        if isinstance(saved_langs, list) and saved_langs:
            for code, cb in self.lang_checks.items():
                cb.setChecked(code in saved_langs)
        self._on_provider_changed()

    def apply_to(self, settings: Dict[str, Any]) -> None:
        thumb = settings.setdefault("thumbnail", {})
        thumb["provider"] = self.provider_combo.currentData() or "cloudflare"
        api_cfg = settings.get("api") or {}
        raw_model = (
            api_cfg.get("cloudflare_image_model")
            or thumb.get("image_model")
            or "@cf/black-forest-labs/flux-1-schnell"
        )
        thumb["image_model"] = str(raw_model).strip()
        thumb["image_style"] = self.style_edit.text().strip()
        thumb["template"] = self._current_template_id()
        thumb["palette"] = self.palette_combo.currentData() or "default"
        thumb["font_size"] = int(self.font_spin.value())
        thumb["frame_seconds"] = round(float(self.frame_spin.value()), 2)
        thumb["languages"] = self.get_selected_languages()
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
        previous_base = self._base or str(self.item_combo.currentData() or "")
        self._bg_path = None
        self._preview_file = None
        self._highlights = []
        self._highlights_by_lang.clear()
        self.highlight_list.clear()
        self.suggest_list.clear()
        self._refresh_items(previous_base)

    def _refresh_items(self, previous: str = "") -> None:
        if not previous:
            previous = self._base or str(self.item_combo.currentData() or "")
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
        self._highlights_by_lang.clear()
        self._bg_path = None
        self._preview_file = None
        item = self._manager.get_item(self._base) if (self._manager and self._base) else None
        if item is None:
            self.title_edit.blockSignals(True)
            self.title_edit.setPlainText("")
            self.title_edit.blockSignals(False)
            self._titles_by_lang.clear()
            self._update_language_title_fields()
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

        self._titles_by_lang.clear()
        item_lang = normalize_language_code(getattr(item, "language", "") or "vi")
        self._titles_by_lang[item_lang] = title
        if self._manager is not None:
            root_stem = re.sub(r"_[a-zA-Z]{2,5}$", "", self._base)
            for it in self._manager.get_items():
                it_root = re.sub(r"_[a-zA-Z]{2,5}$", "", it.base)
                if it_root == root_stem:
                    it_l = normalize_language_code(getattr(it, "language", "") or "")
                    if it_l and it.metadata.get("title"):
                        self._titles_by_lang[it_l] = str(it.metadata["title"])
        self._update_language_title_fields()
        self._transcript = self._load_transcript(self._base, item)
        self.transcript_label.setText(
            f"Transcript: {len(self._transcript):,} ký tự.".replace(",", ".")
            if self._transcript.strip() else "Chưa có transcript — dùng nội dung input.")
        saved = Path(str(item.metadata.get("thumbnail_path") or ""))
        item_lang = normalize_language(getattr(item, "language", "") or "vi")
        if not saved.is_file() and hasattr(self._manager, "lang_dir"):
            saved = self._manager.lang_dir("thumbs", item_lang, create=False) / f"{self._base}.jpg"
        if not saved.is_file():
            saved = Path(self._manager.thumbs_dir) / f"{self._base}.jpg"
        if saved.is_file():
            self._bg_path = saved
        self.prompt_edit.clear()
        self._auto_prompt = ""
        self._auto_fill_prompt()
        self._set_status("Sẵn sàng tạo thumbnail.", "muted")
        self._refresh_gallery(select_path=saved if saved.is_file() else None)
        if self._bg_path is not None:
            self._schedule_preview()
        elif not self._gallery_entries:
            self._show_placeholder("Chưa có ảnh nền — bấm “Tạo ảnh nền”.")
        self._refresh_buttons()

    def _load_transcript(self, base: str, item) -> str:
        if self._manager is None:
            return item.text
        json_file = getattr(item, "json_file", None) or (Path(self._manager.script_dir) / f"{base}.json")
        data = load_json(json_file, None)
        if isinstance(data, dict):
            segments = data.get("segments") or []
            text = " ".join(
                str(seg.get("text") or "") for seg in segments
                if isinstance(seg, dict))
            if text.strip():
                return text
        return item.text

    def _refresh_gallery(self, select_path: Optional[Path] = None) -> None:
        self._gallery_entries.clear()
        self.preview_gallery_combo.blockSignals(True)
        self.preview_gallery_combo.clear()
        if self._manager is not None and Path(self._manager.thumbs_dir).exists():
            thumbs_root = Path(self._manager.thumbs_dir)
            # Scan language subfolders first
            for subdir in sorted(thumbs_root.iterdir()):
                if subdir.is_dir() and not subdir.name.startswith("."):
                    lang_code = subdir.name
                    for img in sorted(subdir.iterdir()):
                        if (
                            img.is_file()
                            and img.suffix.lower() in (".jpg", ".jpeg", ".png")
                            and not img.stem.endswith("_bg")
                            and not img.stem.endswith("_draft")
                        ):
                            label = f"[{lang_code.upper()}] {img.name}"
                            self._gallery_entries.append((label, img))
                            self.preview_gallery_combo.addItem(label, str(img))
            # Also scan flat files in thumbs/
            for img in sorted(thumbs_root.iterdir()):
                if (
                    img.is_file()
                    and img.suffix.lower() in (".jpg", ".jpeg", ".png")
                    and not img.stem.endswith("_bg")
                    and not img.stem.endswith("_draft")
                ):
                    label = img.name
                    self._gallery_entries.append((label, img))
                    self.preview_gallery_combo.addItem(label, str(img))

        selected_idx = -1
        if select_path is not None:
            target_str = str(select_path.resolve()) if select_path.exists() else str(select_path)
            for idx, (_lbl, p) in enumerate(self._gallery_entries):
                p_str = str(p.resolve()) if p.exists() else str(p)
                if p_str == target_str:
                    selected_idx = idx
                    break
        if selected_idx < 0 and self._gallery_entries:
            selected_idx = 0
        if selected_idx >= 0:
            self.preview_gallery_combo.setCurrentIndex(selected_idx)
        self.preview_gallery_combo.blockSignals(False)
        has_many = len(self._gallery_entries) > 1
        self.prev_thumb_btn.setEnabled(has_many)
        self.next_thumb_btn.setEnabled(has_many)
        if selected_idx >= 0 and self._bg_path is None:
            self._on_gallery_selected(selected_idx)

    def _on_gallery_selected(self, index: int = -1) -> None:
        if index < 0 or index >= len(self._gallery_entries):
            return
        _label, path = self._gallery_entries[index]
        if path.is_file():
            self._preview_file = path
            self._pixmap = QPixmap(str(path))
            self._show_pixmap()
            self._set_status(f"Đang xem: {_label}", "muted")

    def _step_gallery(self, delta: int) -> None:
        count = len(self._gallery_entries)
        if count <= 1:
            return
        cur = max(0, self.preview_gallery_combo.currentIndex())
        nxt = (cur + delta) % count
        self.preview_gallery_combo.setCurrentIndex(nxt)

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
        if not key and not self._has_text_ai_credentials():
            self._set_status(
                "Chưa có API key Gemini hoặc cấu hình Cloudflare Worker AI — vui lòng cấu hình trong tab Cài đặt.",
                "warning")
            return
        self._title_worker = TitleWorker(
            self._transcript, key, str(script.get("model") or DEFAULT_MODEL),
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
            first_title = str(titles[0]).strip()
            self.title_edit.setPlainText(first_title)
            current_item = self._manager.get_item(self._base) if (self._manager and self._base) else None
            base_lang = normalize_language_code(getattr(current_item, "language", "") or "vi")
            self._titles_by_lang[base_lang] = first_title
            selected_langs = self.get_selected_languages()
            other_langs = [l for l in selected_langs if l != base_lang]
            if other_langs and self._has_text_ai_credentials():
                self._translate_titles(first_title)

    def _on_titles_failed(self, message: str) -> None:
        self._set_status(message, "error")

    def _on_title_worker_finished(self) -> None:
        worker = self._title_worker
        self._title_worker = None
        if worker is not None:
            worker.deleteLater()
        self.suggest_btn.setEnabled(True)

    def _open_edit_prompt_dialog(self) -> None:
        if not self._base:
            self._set_status("Hãy chọn file trước.", "warning")
            return
        current_prompt = self.prompt_edit.toPlainText().strip()

        def _generate_from_title() -> str:
            title = self.title_edit.toPlainText().strip()
            excerpt = self._transcript[:600]
            return build_image_prompt(
                title, excerpt, self.style_edit.text().strip(), self._current_template_id()
            )

        dlg = PromptEditDialog(current_prompt, on_generate_ai=_generate_from_title, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_prompt = dlg.get_prompt()
            self.prompt_edit.setPlainText(new_prompt)
            self._auto_prompt = new_prompt
            self._set_status("Đã cập nhật prompt tạo ảnh.", "success")

    def _apply_suggestion(self, item: QListWidgetItem) -> None:
        chosen_title = item.text().strip()
        self.title_edit.setPlainText(chosen_title)
        current_item = self._manager.get_item(self._base) if (self._manager and self._base) else None
        base_lang = normalize_language_code(getattr(current_item, "language", "") or "vi")
        self._titles_by_lang[base_lang] = chosen_title

        selected_langs = self.get_selected_languages()
        other_langs = [l for l in selected_langs if l != base_lang]
        if other_langs and self._has_text_ai_credentials():
            self._translate_titles(chosen_title)
        else:
            self._schedule_preview()

    def _translate_titles(self, title_override: str = "") -> None:
        title = (title_override or self.title_edit.toPlainText()).strip()
        if not title:
            self._set_status("Chưa có tiêu đề để dịch.", "warning")
            return
        selected_langs = self.get_selected_languages()
        current_item = self._manager.get_item(self._base) if (self._manager and self._base) else None
        base_lang = normalize_language_code(getattr(current_item, "language", "") or "vi")
        targets = [l for l in selected_langs if l != base_lang]
        if not targets:
            self._set_status("Chưa tick thêm ngôn ngữ nào khác để dịch.", "warning")
            return
        key = self._api_key()
        if not key and not self._has_text_ai_credentials():
            self._set_status("Chưa có API key Gemini hoặc cấu hình Cloudflare để dịch tiêu đề. Hãy cấu hình ở tab Cài đặt.", "warning")
            return
        if self._translate_worker is not None:
            return

        self._set_status(f"Đang dịch tiêu đề sang: {', '.join(targets)}…", "running")
        if hasattr(self, "translate_btn"):
            self.translate_btn.setEnabled(False)
        self._translate_worker = TranslateWorker(
            title, targets, key, source_lang=base_lang,
            keywords=self._highlights, parent=self
        )
        self._translate_worker.done.connect(self._on_translate_done)
        self._translate_worker.failed.connect(self._on_translate_failed)
        self._translate_worker.finished.connect(self._on_translate_finished)
        self._translate_worker.start()

    def _on_translate_done(self, results: dict) -> None:
        titles = results.get("titles") if (isinstance(results, dict) and "titles" in results) else results
        kw_map = results.get("keywords") if (isinstance(results, dict) and isinstance(results.get("keywords"), dict)) else {}
        if isinstance(titles, dict):
            for code, trans in titles.items():
                if not trans:
                    continue
                self._titles_by_lang[code] = trans
                if code in self._title_edits and self._title_edits[code] != self.title_edit:
                    self._title_edits[code].blockSignals(True)
                    self._title_edits[code].setPlainText(trans)
                    self._title_edits[code].blockSignals(False)
        if isinstance(kw_map, dict):
            for code, kws in kw_map.items():
                if kws:
                    self._highlights_by_lang[code] = kws
        label_keys = list(titles.keys()) if isinstance(titles, dict) else []
        self._set_status(f"Đã dịch tiêu đề sang: {', '.join(label_keys)}.", "success")
        self._schedule_preview()

    def _on_translate_failed(self, error: str) -> None:
        self._set_status(f"Dịch tiêu đề lỗi: {error}", "error")

    def _on_translate_finished(self) -> None:
        worker = self._translate_worker
        self._translate_worker = None
        if worker is not None:
            worker.deleteLater()
        if hasattr(self, "translate_btn"):
            self.translate_btn.setEnabled(True)
        self._refresh_buttons()

    # ── highlights ───────────────────────────────────────────────────────────
    def _get_highlights_for_lang(self, lang: str) -> List[Tuple[str, str]]:
        lang_norm = normalize_language_code(lang)
        combined: List[Tuple[str, str]] = list(self._highlights)
        if lang_norm:
            extra = self._highlights_by_lang.get(lang_norm, [])
            for w, c in extra:
                if not any(w.casefold() == ex.casefold() for ex, _ in combined):
                    combined.append((w, c))
        return combined

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

        # Quick translation of keyword for other selected languages if API key available
        other_langs = [l for l in self.get_selected_languages() if l != "vi"]
        key = self._api_key()
        if other_langs and self._has_text_ai_credentials():
            try:
                kw_trans = translate_thumbnail_keywords([(word, color)], other_langs, key, source_language="vi")
                for c, kws in kw_trans.items():
                    if c not in self._highlights_by_lang:
                        self._highlights_by_lang[c] = []
                    self._highlights_by_lang[c].extend(kws)
            except Exception as e:
                logger.debug("Quick translate keyword failed: %s", e)

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
        is_frame = provider == "frame"
        self.frame_spin.setEnabled(is_frame)
        self.style_edit.setEnabled(not is_frame)
        self.prompt_edit.setEnabled(not is_frame)
        if provider in ("cloudflare", "cf"):
            paint_status(self.image_status, "muted")
            self.image_status.setText("Tạo ảnh nền bằng Cloudflare Workers AI từ tab Cài đặt.")
        elif provider == "pollinations":
            paint_status(self.image_status, "muted")
            self.image_status.setText("Tạo ảnh miễn phí qua Pollinations AI (không cần API key).")
        elif provider == "frame":
            paint_status(self.image_status, "muted")
            self.image_status.setText("Trích xuất khung hình từ video đã render.")

    def _pick_custom_background(self) -> None:
        start_dir = ""
        if self._manager is not None:
            assets = getattr(self._manager, "assets_dir", None)
            proj = getattr(self._manager, "project_dir", None)
            if assets and Path(assets).is_dir():
                start_dir = str(assets)
            elif proj and Path(proj).is_dir():
                start_dir = str(proj)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh nền thumbnail",
            start_dir,
            "Hình ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả tệp (*.*)",
        )
        if not file_path:
            return
        p = Path(file_path)
        if not p.is_file():
            return
        self._bg_path = p
        paint_status(self.image_status, "success")
        self.image_status.setText(f"Ảnh nền: {p.name}")
        self._set_status(f"Đã chọn ảnh nền: {p.name}", "success")
        self._schedule_preview()

    def _generate_background(self) -> None:
        if self._image_worker is not None:
            return
        if self._manager is None or not self._base:
            self._set_status("Hãy chọn file trước.", "warning")
            return
        provider = str(self.provider_combo.currentData() or "cloudflare")
        if provider in ("gemini", "imagen"):
            provider = "cloudflare"
        prompt = self.prompt_edit.toPlainText().strip()
        if provider != "frame" and not prompt:
            self._set_status("Prompt ảnh đang trống.", "warning")
            return
        item = self._manager.get_item(self._base)
        item_lang = normalize_language_code(getattr(item, "language", "") or "vi")
        thumbs_lang_dir = (
            self._manager.lang_dir("thumbs", item_lang, create=True)
            if hasattr(self._manager, "lang_dir")
            else Path(self._manager.thumbs_dir) / item_lang
        )
        thumbs_lang_dir.mkdir(parents=True, exist_ok=True)
        out = thumbs_lang_dir / f"{self._base}_bg.jpg"
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

        key = self._api_key()
        thumb = self._settings.get("thumbnail") or {}
        api_cfg = self._settings.get("api") or {}

        cf_account = str(api_cfg.get("cloudflare_account_id") or os.environ.get("CF_ACCOUNT_ID", "")).strip()
        cf_token = str(api_cfg.get("cloudflare_token") or os.environ.get("CF_API_TOKEN", "")).strip()
        cf_model = str(api_cfg.get("cloudflare_image_model") or thumb.get("image_model") or "@cf/black-forest-labs/flux-1-schnell").strip()

        if provider in ("cloudflare", "cf") and (not cf_account or not cf_token):
            self._set_status(
                "Chưa có Account ID hoặc API Token Cloudflare — hãy cấu hình trong tab Cài đặt > API > Ảnh.",
                "warning")
            paint_status(self.image_status, "warning")
            self.image_status.setText("Chưa có Cloudflare credentials. Hãy cấu hình ở tab Cài đặt.")
            return

        model = cf_model if provider in ("cloudflare", "cf") else ""
        ffmpeg = str((self._settings.get("paths") or {}).get("ffmpeg") or "")
        self._image_worker = ImageWorker(
            provider, prompt, out, api_key=key, model=model,
            seconds=seconds, ffmpeg=ffmpeg, account_id=cf_account,
            api_token=cf_token, steps=4, parent=self)
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
        self.image_status.setText(f"Ảnh nền: {self._bg_path.name}")
        self._set_status("Đã tạo ảnh nền thành công.", "success")
        self._render_preview()

    def _on_background_failed(self, message: str) -> None:
        paint_status(self.image_status, "error")
        clean_msg = re.sub(r"^(Imagen|Gemini|Cloudflare Workers AI|Cloudflare)\s*lỗi:\s*", "", str(message).strip(), flags=re.IGNORECASE)
        self.image_status.setText(f"Lỗi: {clean_msg}")
        self._set_status(f"Tạo ảnh thất bại: {clean_msg}", "error")

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
        current_item = self._manager.get_item(self._base) if (self._manager and self._base) else None
        base_lang = normalize_language_code(getattr(current_item, "language", "") or "vi")
        self._titles_by_lang[base_lang] = self.title_edit.toPlainText().strip()
        self._auto_fill_prompt()
        if getattr(self, "_preview_lang", "") == base_lang or not getattr(self, "_preview_lang", ""):
            self._schedule_preview()

    def _auto_fill_prompt(self, force: bool = False) -> None:
        current = self.prompt_edit.toPlainText().strip()
        if current and not force:
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
        return {
            "text": "#FFFFFF",
            "green": str(thumb.get("color_primary") or "#3BFF35"),
            "red": str(thumb.get("color_secondary") or "#FF3B30"),
        }

    def _render_preview(self) -> None:
        if self._bg_path is None or not self._bg_path.is_file():
            self._show_placeholder("Chưa có ảnh nền — bấm “Tạo ảnh nền”.")
            return
        lang = getattr(self, "_preview_lang", "") or "vi"
        title = self.get_title_for_lang(lang) or self.title_edit.toPlainText().strip()
        if not title:
            self._set_status(f"Chưa có tiêu đề ({lang}) để xem trước.", "warning")
            return
        out_dir = Path(tempfile.gettempdir()) / "AutoYout" / "preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"thumb_{(self._base or 'item')}_{lang}.jpg"
        try:
            highlights = self._get_highlights_for_lang(lang)
            render_thumbnail(
                self._bg_path, title, highlights, self._current_template_id(),
                self._palette(), out, font_size=int(self.font_spin.value()),
                language=lang)
        except Exception as exc:
            logger.error("Render thumbnail lỗi: %s", exc)
            self._set_status(str(exc), "error")
            return
        self._preview_file = out
        self._pixmap = QPixmap(str(out))
        self._show_pixmap()
        self._set_status(f"Đã cập nhật xem trước ({language_name(lang)} - {lang}).", "success")

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
            self._set_status("Hãy tạo hoặc chọn ảnh nền trước khi lưu.", "warning")
            return
        base_title = self.title_edit.toPlainText().strip()
        if not base_title:
            self._set_status("Hãy nhập tiêu đề thumbnail.", "warning")
            return
        selected_langs = self.get_selected_languages()
        current_item = self._manager.get_item(self._base)
        current_lang = normalize_language_code(getattr(current_item, "language", "") or "vi")
        if not selected_langs:
            selected_langs = [current_lang]

        root_stem = re.sub(r"_[a-zA-Z]{2,5}$", "", self._base)
        saved_targets: List[Path] = []
        all_items = self._manager.get_items()
        try:
            for lang_code in selected_langs:
                lang_norm = normalize_language_code(lang_code)
                lang_title = self.get_title_for_lang(lang_norm) or base_title
                thumbs_lang_dir = (
                    self._manager.lang_dir("thumbs", lang_norm, create=True)
                    if hasattr(self._manager, "lang_dir")
                    else Path(self._manager.thumbs_dir) / lang_norm
                )
                thumbs_lang_dir.mkdir(parents=True, exist_ok=True)
                # Find matching items scoped to this video's root stem
                lang_items = [
                    it for it in all_items
                    if normalize_language_code(getattr(it, "language", "") or "") == lang_norm
                    and (it.base == self._base or re.sub(r"_[a-zA-Z]{2,5}$", "", it.base) == root_stem)
                ]
                if not lang_items:
                    if current_item is not None and lang_norm == current_lang:
                        lang_items = [current_item]
                    else:
                        # Direct thumbnail render when no specific language item exists
                        target_name = f"{root_stem}_{lang_norm}.jpg" if root_stem else f"{self._base}_{lang_norm}.jpg"
                        target = thumbs_lang_dir / target_name
                        highlights = self._get_highlights_for_lang(lang_norm)
                        render_thumbnail(
                            self._bg_path, lang_title, highlights, self._current_template_id(),
                            self._palette(), target, font_size=int(self.font_spin.value()),
                            language=lang_norm)
                        saved_targets.append(target)
                        continue

                for it in lang_items:
                    item_title = lang_title or str(it.metadata.get("title") or base_title or it.base)
                    draft = thumbs_lang_dir / f"{it.base}_draft.jpg"
                    highlights = self._get_highlights_for_lang(lang_norm)
                    render_thumbnail(
                        self._bg_path, item_title, highlights, self._current_template_id(),
                        self._palette(), draft, font_size=int(self.font_spin.value()),
                        language=lang_norm)
                    target = attach_thumbnail(self._manager, it.base, draft, language=lang_norm)
                    Path(draft).unlink(missing_ok=True)
                    saved_targets.append(target)
        except Exception as exc:
            logger.error("Lưu thumbnail lỗi: %s", exc)
            self._set_status(str(exc), "error")
            return
        if not saved_targets:
            return
        matching_preview = [p for p in saved_targets if f"_{self._preview_lang}." in p.name]
        last_target = matching_preview[0] if matching_preview else saved_targets[0]
        self._preview_file = last_target
        self._pixmap = QPixmap(str(last_target))
        self._show_pixmap()
        self._refresh_gallery(select_path=last_target)
        self._set_status(
            f"Đã lưu {len(saved_targets)} thumbnail theo ngôn ngữ ({', '.join(selected_langs)}).",
            "success",
        )
        self.thumbnail_saved.emit(self._base)

    def _open_thumbnail(self) -> None:
        if self._manager is None:
            return
        if self._preview_file is not None and Path(self._preview_file).is_file():
            open_in_explorer(Path(self._preview_file))
            return
        thumbs_dir = Path(self._manager.thumbs_dir)
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        open_in_explorer(thumbs_dir)

    def _reset_or_cancel_thumbnail(self) -> None:
        if self._image_worker is not None:
            self._cancel_background()
            self._set_status("Đang huỷ tạo ảnh nền…", "muted")
            return
        self._bg_path = None
        self._preview_file = None
        self._pixmap = None
        paint_status(self.image_status, "muted")
        self.image_status.setText("Chưa có ảnh nền.")
        self._show_placeholder("Đã huỷ ảnh hiện tại — bấm “Tạo ảnh nền” để tạo lại.")
        self._set_status(
            "Đã huỷ ảnh hiện tại. Bạn có thể chỉnh sửa prompt và bấm “Tạo ảnh nền” để tạo lại.",
            "muted",
        )
        self._refresh_buttons()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _api_key(self) -> str:
        settings = self._settings or {}
        api = settings.get("api") or {}
        script = settings.get("script") or {}
        video = settings.get("video") or {}
        thumb = settings.get("thumbnail") or {}
        key = (
            api.get("gemini_key")
            or script.get("api_key")
            or video.get("ai_api_key")
            or thumb.get("api_key")
            or self.api_key_edit.text().strip()
            or os.environ.get("GEMINI_API_KEY", "")
            or ""
        )
        return str(key).strip()

    def _has_text_ai_credentials(self) -> bool:
        if bool(self._api_key()):
            return True
        api = (self._settings or {}).get("api") or {}
        cf_acc = str(api.get("cloudflare_account_id") or os.environ.get("CF_ACCOUNT_ID", "")).strip()
        cf_tok = str(api.get("cloudflare_token") or os.environ.get("CF_API_TOKEN", "")).strip()
        return bool(cf_acc and cf_tok)

    def _refresh_buttons(self) -> None:
        busy = (
            self._image_worker is not None
            or self._title_worker is not None
            or self._translate_worker is not None
        )
        has_item = bool(self._base)
        self.image_btn.setEnabled(not busy and has_item)
        if hasattr(self, "pick_image_btn"):
            self.pick_image_btn.setEnabled(not busy and has_item)
        self.suggest_btn.setEnabled(not busy and has_item)
        if hasattr(self, "edit_prompt_btn"):
            self.edit_prompt_btn.setEnabled(has_item)
        if hasattr(self, "translate_btn"):
            self.translate_btn.setEnabled(not busy and has_item)
        self.save_btn.setEnabled(has_item)
        if hasattr(self, "cancel_thumb_btn"):
            self.cancel_thumb_btn.setEnabled(has_item)

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
        if self._translate_worker is not None:
            self._translate_worker.wait(timeout_ms)
