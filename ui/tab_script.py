"""Script tab: YouTube transcript → merged plain text → multi-language adaptations.

The heavy work happens in :class:`ScriptWorker` (a ``QThread``): fetching the
existing YouTube captions and translating with Gemini. The tab merges the
captions into a single text (no timestamps), cleans it and adapts it into
other languages.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSignalBlocker, QThread, Signal, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.ai_client import (
    DEFAULT_CLEAN_PROMPT,
    DEFAULT_MODEL,
    GEMINI_TEXT_MODELS,
    AICancelled,
    AIError,
    build_clean_prompt,
    clean_transcript,
    clean_transcript_heuristics,
    gemini_generate,
    normalize_model,
    translate_text,
)
from core.input_pipeline import (
    InputCancelled,
    InputError,
    get_youtube_transcript,
)
from core.language_profiles import LanguageProfile, load_profiles, save_profiles
from core.languages import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    is_known,
    language_choices,
    language_name,
    normalize_language,
    normalize_language_code,
)
from core.subtitles import cues_to_dicts
from core.text_processor import count_words
from ui.components.icons import set_icon
from ui.components.page import PageHeader, paint_status, stack_columns, wrap_scroll
from ui import theme
from utils.files import safe_filename, write_json_atomic
from utils.logger import get_logger

logger = get_logger("ui.script")

_ACTION_BTN = 26
_ACTION_GAP = 6
_ITEM_H_PAD = 16  # QTableWidget::item padding 8px × 2


class _LanguagePromptDialog(QDialog):
    """Modal to add one target language and the prompt used to adapt the story."""

    def __init__(self, parent=None, *, language: str = "", name: str = "", prompt: str = "",
                 existing: Optional[set[str]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Thêm ngôn ngữ")
        self.setModal(True)
        self.resize(580, 420)
        self._existing = existing or set()
        self._code_user_modified = False

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Hàng nhập: Tên ngôn ngữ & Viết tắt
        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)

        name_box = QVBoxLayout()
        name_box.setSpacing(4)
        name_box.addWidget(QLabel("Tên ngôn ngữ"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Ví dụ: Tiếng Anh, Tiếng Nhật, Français...")
        name_box.addWidget(self.name_edit)
        lang_row.addLayout(name_box, 3)

        code_box = QVBoxLayout()
        code_box.setSpacing(4)
        code_box.addWidget(QLabel("Viết tắt (mã file)"))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("Ví dụ: en, ja, fr...")
        self.code_edit.setToolTip("Mã viết tắt dùng để đặt tên file, ví dụ: tap_01_en.txt")
        code_box.addWidget(self.code_edit)
        lang_row.addLayout(code_box, 2)

        layout.addLayout(lang_row)

        initial_name = name or (language_name(language) if (language and is_known(language)) else "")
        if initial_name:
            self.name_edit.setText(initial_name)
        if language:
            self.code_edit.setText(language)

        self.name_edit.textChanged.connect(self._on_name_changed)
        self.code_edit.textEdited.connect(self._on_code_edited)

        layout.addWidget(QLabel("Prompt (cách làm sạch / chuyển thể câu chuyện)"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlainText(prompt)
        self.prompt_edit.setPlaceholderText(
            "Ví dụ: làm sạch lời kể, sửa chính tả, câu ngắn dễ đọc, giữ nguyên sự kiện…")
        layout.addWidget(self.prompt_edit, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Huỷ")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Lưu")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _on_code_edited(self, _text: str) -> None:
        self._code_user_modified = True

    def _on_name_changed(self, text: str) -> None:
        if self._code_user_modified:
            return
        t = text.strip()
        if not t:
            return
        lower = t.lower()
        if "anh" in lower or "english" in lower:
            self.code_edit.setText("en")
        elif "nhật" in lower or "japan" in lower:
            self.code_edit.setText("ja")
        elif "trung" in lower or "chinese" in lower or "hoa" in lower:
            self.code_edit.setText("zh")
        elif "hàn" in lower or "korea" in lower:
            self.code_edit.setText("ko")
        elif "pháp" in lower or "french" in lower or "français" in lower:
            self.code_edit.setText("fr")
        elif "tây ban nha" in lower or "spain" in lower or "español" in lower:
            self.code_edit.setText("es")
        elif "thái" in lower or "thai" in lower:
            self.code_edit.setText("th")
        elif "đức" in lower or "german" in lower or "deutsch" in lower:
            self.code_edit.setText("de")
        elif "nga" in lower or "russian" in lower:
            self.code_edit.setText("ru")
        elif "ý" in lower or "italian" in lower:
            self.code_edit.setText("it")
        else:
            for code, info in LANGUAGES.items():
                if t.casefold() in (code.casefold(), info["name"].casefold()):
                    self.code_edit.setText(code)
                    break

    def _accept(self) -> None:
        name = self.name_edit.text().strip()
        code = self.code_edit.text().strip().lower()
        if not code and not name:
            QMessageBox.warning(self, "Ngôn ngữ", "Vui lòng nhập tên ngôn ngữ hoặc viết tắt.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        name = self.name_edit.text().strip()
        code = self.code_edit.text().strip().lower()
        if not code:
            code = normalize_language(name, "") or safe_filename(name.lower(), "lang")[:10]
        return (code, self.prompt_edit.toPlainText().strip())

    def custom_name(self) -> str:
        return self.name_edit.text().strip()


class _PromptEditDialog(QDialog):
    """Modal to edit one language's adaptation prompt."""

    def __init__(self, language: str, prompt: str, parent=None, title_name: str = "") -> None:
        super().__init__(parent)
        title = title_name or language_name(language)
        self.setWindowTitle(f"Sửa prompt — {title}")
        self.setModal(True)
        self.resize(600, 420)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Prompt (cách làm sạch / chuyển thể câu chuyện):"))
        self.edit = QPlainTextEdit()
        self.edit.setPlainText(prompt)
        self.edit.setPlaceholderText(
            "Ví dụ: làm sạch lời kể, sửa chính tả, câu ngắn dễ đọc, giữ nguyên sự kiện…")
        layout.addWidget(self.edit, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Huỷ")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Lưu")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def value(self) -> str:
        return self.edit.toPlainText().strip()


class _CleanPromptDialog(QDialog):
    """Modal to edit the transcript cleaning prompt."""

    def __init__(self, prompt: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Prompt làm sạch")
        self.setModal(True)
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(
            "Hướng dẫn AI làm sạch transcript trước khi chuyển thể:"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlainText(prompt)
        self.prompt_edit.setPlaceholderText(
            "Prompt hướng dẫn AI lọc bỏ thẻ rác, CTA, quảng cáo, sửa chính tả…")
        layout.addWidget(self.prompt_edit, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Huỷ")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Lưu")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def value(self) -> str:
        return self.prompt_edit.toPlainText().strip()


class _ManageLanguagesDialog(QDialog):
    """Modal listing every language profile; click a prompt cell or edit button to edit it."""

    def __init__(self, profiles: List[LanguageProfile], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sửa prompt ngôn ngữ")
        self.setModal(True)
        self.resize(720, 440)
        self._profiles: List[LanguageProfile] = [
            LanguageProfile(p.language, p.prompt, p.id, getattr(p, "name", "")) for p in profiles]

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel(
            "Bấm vào ô Prompt hoặc bấm nút bút chì để sửa prompt, hoặc bấm thùng rác để xoá:"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Ngôn ngữ", "Prompt", "Thao tác"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 105)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Huỷ")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Lưu")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._rebuild()

    def _rebuild(self) -> None:
        self.table.setRowCount(len(self._profiles))
        for row, profile in enumerate(self._profiles):
            self.table.setRowHeight(row, 42)
            name = getattr(profile, "name", "")
            display_name = f"{name} ({profile.language})" if name and profile.language not in name else (name or language_name(profile.language))
            lang_item = QTableWidgetItem(display_name)
            lang_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            lang_item.setData(Qt.ItemDataRole.UserRole, profile.language)

            prompt_item = QTableWidgetItem(profile.prompt)
            prompt_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            prompt_item.setToolTip(profile.prompt or "Bấm để sửa prompt")

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(6, 1, 6, 7)
            action_layout.setSpacing(8)
            action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            edit_btn = QPushButton()
            edit_btn.setObjectName("iconButton")
            set_icon(edit_btn, "edit", "accent", 14)
            edit_btn.setToolTip("Sửa prompt")
            edit_btn.setFixedSize(26, 26)
            edit_btn.clicked.connect(lambda _checked=False, r=row: self._open_edit_dialog(r))

            del_btn = QPushButton()
            del_btn.setObjectName("iconButton")
            set_icon(del_btn, "trash", "muted", 14)
            del_btn.setToolTip("Xoá ngôn ngữ này")
            del_btn.setFixedSize(26, 26)
            del_btn.clicked.connect(
                lambda _checked=False, code=profile.language: self._remove(code))

            action_layout.addWidget(edit_btn)
            action_layout.addWidget(del_btn)

            self.table.setItem(row, 0, lang_item)
            self.table.setItem(row, 1, prompt_item)
            self.table.setCellWidget(row, 2, action_widget)
        self.table.horizontalHeader().resizeSection(2, 105)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column == 1:
            self._open_edit_dialog(row)

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        self._open_edit_dialog(row)

    def _open_edit_dialog(self, row: int) -> None:
        lang_item = self.table.item(row, 0)
        prompt_item = self.table.item(row, 1)
        if lang_item is None or prompt_item is None:
            return
        code = str(lang_item.data(Qt.ItemDataRole.UserRole) or "")
        dialog = _PromptEditDialog(code, prompt_item.text(), self, title_name=lang_item.text())
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new_prompt = dialog.value()
            prompt_item.setText(new_prompt)
            prompt_item.setToolTip(new_prompt)
            if 0 <= row < len(self._profiles):
                self._profiles[row].prompt = new_prompt
        finally:
            dialog.deleteLater()

    def _remove(self, language: str) -> None:
        self._profiles = [p for p in self._collect()
                          if p.language != language]
        self._rebuild()

    def _collect(self) -> List[LanguageProfile]:
        ids = {p.language: p.id for p in self._profiles}
        names = {p.language: getattr(p, "name", "") for p in self._profiles}
        updated: List[LanguageProfile] = []
        for row in range(self.table.rowCount()):
            lang_item = self.table.item(row, 0)
            if lang_item is None:
                continue
            code = str(lang_item.data(Qt.ItemDataRole.UserRole) or "")
            if not code:
                continue
            prompt_item = self.table.item(row, 1)
            name = names.get(code, "")
            profile = LanguageProfile(
                code, prompt_item.text().strip() if prompt_item else "",
                name=name
            )
            if code in ids:
                profile.id = ids[code]
            updated.append(profile)
        return updated

    def _accept(self) -> None:
        self._profiles = self._collect()
        self.accept()

    def profiles(self) -> List[LanguageProfile]:
        return list(self._profiles)


class ScriptWorker(QThread):
    """Fetch a transcript or translate it, emitting progress on the way."""

    progress = Signal(int, str)
    transcript_ready = Signal(list, dict)
    cleaned_ready = Signal(str)
    translated = Signal(str, str, str)   # language, text, error
    failed = Signal(str)

    def __init__(self, spec: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._spec = dict(spec)
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def _cancelled(self) -> bool:
        return self._stop.is_set()

    def _emit(self, percent: int, message: str) -> None:
        self.progress.emit(max(0, min(100, int(percent))), str(message))

    def run(self) -> None:  # noqa: D401 - QThread API
        spec = self._spec
        try:
            if spec.get("kind") == "transcript":
                cues, meta = get_youtube_transcript(
                    spec["url"],
                    progress_cb=self._emit,
                    cancel_cb=self._cancelled,
                )
                self.transcript_ready.emit(cues_to_dicts(cues), dict(meta))
                return
            if spec.get("kind") == "clean":
                text = str(spec.get("text") or "").strip()
                if not text:
                    raise InputError("Chưa có transcript để làm sạch.")
                instruction = str(spec.get("clean_prompt") or DEFAULT_CLEAN_PROMPT).strip()
                if not instruction:
                    raise InputError("Prompt làm sạch rỗng.")
                self._emit(10, "Đang làm sạch transcript bằng AI…")
                if self._cancelled():
                    raise InputCancelled("Đã huỷ làm sạch.")
                cleaned = clean_transcript(
                    text,
                    api_key=str(spec.get("api_key") or ""),
                    model=str(spec.get("model") or DEFAULT_MODEL),
                    instruction=instruction,
                    progress_cb=lambda pct, msg: self._emit(10 + int(pct * 0.85), msg),
                    cancel_cb=self._cancelled,
                )
                if self._cancelled():
                    raise InputCancelled("Đã huỷ làm sạch.")
                self.cleaned_ready.emit(cleaned)
                return
            profiles = list(spec.get("profiles") or [])
            total = max(1, len(profiles))
            for position, profile in enumerate(profiles):
                if self._cancelled():
                    raise InputCancelled("Đã huỷ chuyển thể.")
                language = str(profile.get("language") or DEFAULT_LANGUAGE)
                model = str(spec.get("model") or DEFAULT_MODEL)

                def report(done: int, count: int, _index: int = position) -> None:
                    fraction = done / max(1, count)
                    self._emit(
                        int(100 * (_index + fraction) / total),
                        f"Đang chuyển thể {language_name(language)} "
                        f"({done}/{count} đoạn)…",
                    )

                try:
                    text = translate_text(
                        spec["text"], spec["api_key"], model,
                        source_lang=spec["source_lang"], target_lang=language,
                        style=str(profile.get("prompt") or ""),
                        progress_cb=report, cancel_cb=self._cancelled,
                    )
                except AICancelled:
                    raise
                except Exception as exc:  # one language must not stop the rest
                    logger.error("Chuyển thể %s lỗi: %s", language, exc)
                    self.translated.emit(language, "", str(exc))
                    continue
                self.translated.emit(language, text, "")
        except (InputCancelled, AICancelled):
            self.failed.emit("Đã huỷ.")
        except (InputError, AIError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            logger.exception("Kịch bản lỗi không mong đợi")
            self.failed.emit(f"Lỗi không mong đợi: {exc}")


class ScriptTab(QWidget):
    busy_changed = Signal(bool)
    reload_requested = Signal()
    languages_changed = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._worker: Optional[ScriptWorker] = None
        self._script_text: str = ""
        self._meta: Dict[str, Any] = {}
        self._translations: Dict[str, str] = {}
        self._result_rows: Dict[str, int] = {}
        self._profiles: List[LanguageProfile] = []
        self._clean_prompt: str = DEFAULT_CLEAN_PROMPT
        self._script_dir: Optional[Path] = None
        self._manager = None
        self._source_lang: Optional[str] = None
        self._source_text: str = ""
        self._source_row: int = -1

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(wrap_scroll(content))

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addWidget(PageHeader(
            "Nguồn YouTube",
            "Dán link YouTube → lấy transcript → gộp thành văn bản (bỏ thời gian) "
            "→ làm sạch → chuyển thể sang nhiều ngôn ngữ cho pipeline.",
        ))
        top = QHBoxLayout()
        top.setSpacing(12)
        self._top_columns = top
        top.addWidget(self._build_source_box(), 1)
        top.addWidget(self._build_config_box(), 1)
        root.addLayout(top)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        root.addWidget(self.progress_bar)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self._bottom_columns = bottom
        bottom.addWidget(self._build_table_box(), 2)
        bottom.addWidget(self._build_transcript_box(), 3)
        root.addLayout(bottom, 1)

        self._set_busy(False)
        self._set_actions_enabled(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        stack_columns(self, self._top_columns, 760)
        stack_columns(self, self._bottom_columns, 760)

    # ── construction ─────────────────────────────────────────────────────────
    def _build_source_box(self) -> QGroupBox:
        box = QGroupBox()
        box.setObjectName("headerlessGroup")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        url_label = QLabel("Link YouTube")
        layout.addWidget(url_label)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Dán link video YouTube…")
        layout.addWidget(self.url_edit)

        topic_label = QLabel("Tên đề tài")
        layout.addWidget(topic_label)
        self.topic_edit = QLineEdit()
        self.topic_edit.setPlaceholderText("Để trống nếu muốn giữ tên mặc định")
        self.topic_edit.setToolTip("Đặt tên đề tài / tên tệp kịch bản. Để trống nếu muốn giữ tên mặc định từ video YouTube.")
        layout.addWidget(self.topic_edit)

        self.status_label = QLabel("Sẵn sàng.")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        paint_status(self.status_label, "muted")
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        return box

    def _build_transcript_box(self) -> QGroupBox:
        box = QGroupBox()
        box.setObjectName("headerlessGroup")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("Transcript")
        title.setObjectName("GroupTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.count_label = QLabel("0 từ")
        self.count_label.setObjectName("MutedLabel")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.script_edit = QPlainTextEdit()
        self.script_edit.setPlaceholderText(
            "Lấy transcript xong, nội dung sẽ được gộp ở đây (không kèm thời gian).")
        self.script_edit.textChanged.connect(self._on_script_edited)
        layout.addWidget(self.script_edit, 1)
        return box

    def _build_config_box(self) -> QGroupBox:
        box = QGroupBox()
        box.setObjectName("headerlessGroup")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        # Keep model_combo hidden for settings/test compatibility
        self.model_combo = QComboBox(box)
        self.model_combo.setEditable(True)
        for label, model_id in GEMINI_TEXT_MODELS:
            self.model_combo.addItem(label, model_id)
        self.model_combo.hide()

        fetch_row = QHBoxLayout()
        fetch_row.setSpacing(6)
        self.fetch_button = QPushButton("Lấy transcript")
        self.fetch_button.setObjectName("primaryButton")
        set_icon(self.fetch_button, "download", "white", 15)
        self.fetch_button.clicked.connect(self._fetch_transcript)
        self.cancel_button = QPushButton("Huỷ")
        set_icon(self.cancel_button, "x", "muted", 13)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        fetch_row.addWidget(self.fetch_button, 1)
        fetch_row.addWidget(self.cancel_button)
        grid.addLayout(fetch_row, 0, 0, 1, 2)

        clean_row = QHBoxLayout()
        clean_row.setSpacing(6)
        self.clean_button = QPushButton("Làm sạch")
        self.clean_button.setObjectName("primaryButton")
        set_icon(self.clean_button, "zap", "white", 15)
        self.clean_button.setToolTip(
            "Làm sạch transcript bằng AI theo prompt làm sạch")
        self.clean_button.clicked.connect(self._clean)
        self.clean_prompt_button = QPushButton()
        self.clean_prompt_button.setObjectName("iconButton")
        self.clean_prompt_button.setFixedSize(30, 30)
        set_icon(self.clean_prompt_button, "broom", "muted", 14)
        self.clean_prompt_button.setToolTip("Sửa prompt làm sạch")
        self.clean_prompt_button.clicked.connect(self._edit_clean_prompt)
        clean_row.addWidget(self.clean_button, 1)
        clean_row.addWidget(self.clean_prompt_button)
        grid.addLayout(clean_row, 1, 0, 1, 2)

        self.translate_button = QPushButton("Chuyển thể")
        self.translate_button.setObjectName("primaryButton")
        set_icon(self.translate_button, "globe", "white", 15)
        self.translate_button.clicked.connect(self._translate)
        grid.addWidget(self.translate_button, 2, 0, 1, 2)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        self.add_language_button = QPushButton("Thêm ngôn ngữ")
        set_icon(self.add_language_button, "plus", "accent", 13)
        self.add_language_button.setToolTip("Thêm ngôn ngữ đích + prompt chuyển thể")
        self.add_language_button.clicked.connect(self._add_language)
        self.edit_prompts_button = QPushButton()
        self.edit_prompts_button.setObjectName("iconButton")
        self.edit_prompts_button.setFixedSize(30, 30)
        set_icon(self.edit_prompts_button, "edit", "muted", 14)
        self.edit_prompts_button.setToolTip("Sửa / xoá prompt các ngôn ngữ")
        self.edit_prompts_button.clicked.connect(self._manage_languages)
        lang_row.addWidget(self.add_language_button, 1)
        lang_row.addWidget(self.edit_prompts_button)
        grid.addLayout(lang_row, 2, 2, 1, 2)

        self.target_hint = QLabel(
            "Chưa có ngôn ngữ — bấm “Thêm ngôn ngữ” để tạo.")
        self.target_hint.setObjectName("MutedLabel")
        self.target_hint.setWordWrap(True)
        grid.addWidget(self.target_hint, 3, 0, 1, 4)
        return box

    def _build_table_box(self) -> QGroupBox:
        box = QGroupBox()
        box.setObjectName("headerlessGroup")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self.result_table = QTableWidget(0, 3)
        self.result_table.setHorizontalHeaderLabels(
            ["Ngôn ngữ", "Trạng thái", "Thao tác"])
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.verticalHeader().setDefaultSectionSize(42)
        self.result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 130)
        self.result_table.setMinimumHeight(150)
        layout.addWidget(self.result_table, 1)

        self.save_all_button = QPushButton("Lưu tất cả")
        set_icon(self.save_all_button, "check", "accent", 14)
        self.save_all_button.clicked.connect(self._save_all)
        layout.addWidget(self.save_all_button)

        self.output_label = QLabel(
            "Chưa chọn project — chọn project ở tab đầu tiên.")
        self.output_label.setObjectName("MutedLabel")
        self.output_label.setWordWrap(True)
        layout.addWidget(self.output_label)
        return box

    # ── settings ─────────────────────────────────────────────────────────────
    def _current_model(self) -> str:
        idx = self.model_combo.currentIndex()
        txt = self.model_combo.currentText().strip()
        if idx >= 0 and self.model_combo.itemText(idx) == txt:
            data = self.model_combo.itemData(idx)
            if data:
                return str(data)
        return txt or DEFAULT_MODEL

    def _set_model(self, model_name: str) -> None:
        if not model_name:
            return
        idx = self.model_combo.findData(model_name)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
            return
        idx = self.model_combo.findText(model_name)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
            return
        self.model_combo.setEditText(model_name)

    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        cfg = settings.setdefault("script", {})
        model = normalize_model(str(cfg.get("model") or (settings.get("api") or {}).get("gemini_model") or DEFAULT_MODEL))
        cfg["model"] = model
        self._set_model(model)
        loaded_prompt = str(cfg.get("transcript_clean_prompt") or "")
        if not loaded_prompt or "Som Tam" in loaded_prompt or "DNSE" in loaded_prompt:
            self._clean_prompt = DEFAULT_CLEAN_PROMPT
        else:
            self._clean_prompt = loaded_prompt
        self._profiles = load_profiles()
        self._ensure_profile_folders()
        self._reload_targets()
        self.languages_changed.emit(list(self._profiles))

    def apply_to(self, settings: Dict[str, Any]) -> None:
        cfg = settings.setdefault("script", {})
        cfg["model"] = self._current_model()
        cfg["transcript_clean_prompt"] = self._clean_prompt.strip()
        self._settings = settings

    # ── language profiles ────────────────────────────────────────────────────
    def _add_language(self) -> None:
        dialog = _LanguagePromptDialog(self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            language, prompt = dialog.values()
            name = dialog.custom_name()
        finally:
            dialog.deleteLater()
        language = normalize_language(language, language)
        for profile in self._profiles:
            if profile.language == language:
                profile.prompt = prompt
                if name:
                    profile.name = name
                break
        else:
            self._profiles.append(LanguageProfile(language, prompt, name=name))
        self._persist_profiles()
        self._ensure_profile_folders()
        self._reload_targets()
        self.languages_changed.emit(list(self._profiles))

    def _manage_languages(self) -> None:
        if not self._profiles:
            self._warn("Chưa có ngôn ngữ nào để sửa.")
            return
        dialog = _ManageLanguagesDialog(self._profiles, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self._profiles = dialog.profiles()
        finally:
            dialog.deleteLater()
        self._persist_profiles()
        self._ensure_profile_folders()
        self._reload_targets()
        self.languages_changed.emit(list(self._profiles))

    def _ensure_profile_folders(self) -> None:
        codes = [normalize_language_code(p.language, p.language) for p in self._profiles if p.language]
        if self._manager is not None and hasattr(self._manager, "ensure_language_dirs"):
            self._manager.ensure_language_dirs(codes)
        elif self._script_dir is not None:
            for code in codes:
                (self._script_dir / code).mkdir(parents=True, exist_ok=True)

    def _persist_profiles(self) -> None:
        try:
            self._profiles = save_profiles(self._profiles)
        except OSError as exc:
            self._warn(f"Không lưu được language_prompt.json: {exc}")

    def _reload_targets(self, keep: bool = True) -> None:
        previous = dict(self._translations) if keep else {}
        self._translations.clear()
        self._result_rows.clear()
        self._source_row = -1
        self.result_table.setRowCount(0)
        if self._source_lang:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self.result_table.setRowHeight(row, 42)
            self._source_row = row
            self.result_table.setItem(
                row, 0, QTableWidgetItem(language_name(self._source_lang)))
            widget = self._make_action_widget(
                on_save=self._save_source,
                on_view=self._view_source,
                on_delete=self._delete_source,
            )
            self.result_table.setCellWidget(row, 2, widget)
            if self._source_text.strip():
                self._set_result_status(row, "gốc", "success")
            else:
                self._set_result_status(row, "gốc", "muted")
        for profile in self._profiles:
            text = previous.get(profile.language)
            if not text:
                continue
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self.result_table.setRowHeight(row, 42)
            self._result_rows[profile.language] = row
            display_name = getattr(profile, "name", "") or language_name(profile.language)
            if getattr(profile, "name", "") and profile.language not in display_name:
                display_name = f"{profile.name} ({profile.language})"
            self.result_table.setItem(
                row, 0, QTableWidgetItem(display_name))
            widget = self._make_action_widget(
                on_save=lambda _checked=False, lang=profile.language:
                self._save_language(lang),
                on_view=lambda _checked=False, lang=profile.language:
                self._view_language(lang),
                on_delete=lambda _checked=False, lang=profile.language:
                self._delete_language(lang),
            )
            self.result_table.setCellWidget(row, 2, widget)
            self._translations[profile.language] = text
            self._set_result_status(row, f"Xong · {len(text)} ký tự", "success")
        if self._profiles:
            self.target_hint.setText(
                f"{len(self._profiles)} ngôn ngữ · cấu hình prompt tại nút “Sửa prompt…” bên trên.")
        else:
            self.target_hint.setText(
                "Chưa có ngôn ngữ — bấm “Thêm ngôn ngữ” để tạo.")

    # ── project context ──────────────────────────────────────────────────────
    def set_project(self, manager) -> None:
        self._manager = manager
        if manager is None:
            self._script_dir = None
            self.output_label.setText(
                "Chưa chọn project — chọn project ở tab đầu tiên.")
            self._set_actions_enabled(False)
            return
        self._script_dir = Path(manager.script_dir)
        self._ensure_profile_folders()
        self.output_label.setText(f"Kịch bản: {self._script_dir}")
        self._set_actions_enabled(self._worker is None)

    # ── busy state ───────────────────────────────────────────────────────────
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def stop_and_wait(self, timeout_ms: int = 15000) -> bool:
        if self._worker is None:
            return True
        self._worker.cancel()
        finished = self._worker.wait(timeout_ms)
        if finished:
            self._worker = None
        return finished

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            paint_status(self.status_label, "warning")
            self.status_label.setText("Đang huỷ…")

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.fetch_button.setEnabled(enabled)
        self.clean_button.setEnabled(enabled)
        self.translate_button.setEnabled(enabled)
        self.save_all_button.setEnabled(enabled)

    def _set_busy(self, busy: bool) -> None:
        has_project = self._script_dir is not None
        self._set_actions_enabled(not busy and has_project)
        self.cancel_button.setEnabled(busy)
        for widget in (self.url_edit, self.topic_edit, self.model_combo,
                       self.clean_prompt_button, self.add_language_button,
                       self.edit_prompts_button):
            widget.setEnabled(not busy)

    # ── transcript job ───────────────────────────────────────────────────────
    def _fetch_transcript(self) -> None:
        if self._worker is not None:
            return
        if self._script_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        url = self.url_edit.text().strip()
        if not url:
            self._warn("Hãy dán link YouTube trước.")
            return
        spec = {
            "kind": "transcript",
            "url": url,
        }
        self.progress_bar.setValue(0)
        paint_status(self.status_label, "running")
        self.status_label.setText("Đang khởi động…")
        self._start_worker(spec)

    def _start_worker(self, spec: Dict[str, Any]) -> None:
        worker = ScriptWorker(spec, self)
        worker.progress.connect(self._on_progress)
        worker.transcript_ready.connect(self._on_transcript)
        worker.cleaned_ready.connect(self._on_cleaned)
        worker.translated.connect(self._on_translated)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        self._worker = worker
        self._set_busy(True)
        self.busy_changed.emit(True)
        worker.start()

    def _on_transcript(self, cues: List[dict], meta: dict) -> None:
        self._meta = dict(meta or {})
        self._source_lang = str(
            self._meta.get("source_language") or DEFAULT_LANGUAGE)
        self._script_text = self._merge_cues_to_text(cues)
        self._source_text = self._script_text
        self.script_edit.setPlainText(self._script_text)
        self._reload_targets(keep=False)
        title = self._meta.get("title") or self._meta.get("video_id") or "video"
        paint_status(self.status_label, "success")
        self.status_label.setText(
            f"Đã gộp {len(cues)} dòng phụ đề thành văn bản: {title}")

    def _on_cleaned(self, text: str) -> None:
        self._script_text = text
        self._source_text = text
        self.script_edit.setPlainText(text)
        self._reload_targets(keep=False)
        paint_status(self.status_label, "success")
        self.status_label.setText(
            f"Đã làm sạch transcript ({len(text)} ký tự). Bấm “Chuyển thể” để tiếp tục.")

    @staticmethod
    def _merge_cues_to_text(cues: List[dict]) -> str:
        """Join captions into paragraphs, dropping timestamps at topic breaks."""
        paragraphs: List[str] = []
        buffer: List[str] = []
        previous_end: Optional[float] = None
        for cue in cues:
            text = " ".join(str(cue.get("text") or "").split())
            if not text:
                continue
            start = float(cue.get("start") or 0.0)
            if buffer and previous_end is not None and start - previous_end > 2.0:
                paragraphs.append(" ".join(buffer))
                buffer = []
            buffer.append(text)
            previous_end = float(cue.get("end") or 0.0)
        if buffer:
            paragraphs.append(" ".join(buffer))
        joined = "\n\n".join(paragraphs)
        return clean_transcript_heuristics(joined)

    def _on_translated(self, language: str, text: str, error: str) -> None:
        row = self._result_rows.get(language)
        if row is None:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self.result_table.setRowHeight(row, 42)
            self._result_rows[language] = row
            profile = next((p for p in self._profiles if p.language == language), None)
            display_name = getattr(profile, "name", "") or language_name(language)
            if getattr(profile, "name", "") and language not in display_name:
                display_name = f"{profile.name} ({language})"
            self.result_table.setItem(
                row, 0, QTableWidgetItem(display_name))
            widget = self._make_action_widget(
                on_save=lambda _checked=False, lang=language: self._save_language(lang),
                on_view=lambda _checked=False, lang=language: self._view_language(lang),
                on_delete=lambda _checked=False, lang=language: self._delete_language(lang),
            )
            self.result_table.setCellWidget(row, 2, widget)
        if error:
            self._translations.pop(language, None)
            self._set_result_status(row, f"Lỗi: {error}", "error")
            return
        self._translations[language] = text
        self._set_result_status(row, f"Xong · {len(text)} ký tự", "success")

    def _set_result_status(self, row: int, message: str, level: str) -> None:
        item = self.result_table.item(row, 1)
        if item is None:
            item = QTableWidgetItem()
            self.result_table.setItem(row, 1, item)
        item.setText(message)
        item.setToolTip(message)
        color = {
            "success": theme.SUCCESS,
            "error": theme.DANGER,
            "running": theme.BLUE,
        }.get(level, "")
        if color:
            item.setForeground(QBrush(QColor(color)))

    def _make_action_widget(
        self,
        on_save=None,
        on_view=None,
        on_delete=None,
    ) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 1, 6, 7)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        actions = []
        if on_save is not None:
            actions.append((on_save, "download", "Lưu file TXT này vào SCRIPT", "accent"))
        if on_view is not None:
            actions.append((on_view, "eye", "Xem nội dung trên ô soạn thảo", "text"))
        if on_delete is not None:
            actions.append((on_delete, "trash", "Xoá", "muted"))

        for on_click, name, tip, role in actions:
            button = QPushButton()
            button.setObjectName("iconButton")
            button.setFixedSize(_ACTION_BTN, _ACTION_BTN)
            set_icon(button, name, role, 14)
            button.setToolTip(tip)
            button.clicked.connect(on_click)
            layout.addWidget(button)
        return container

    def _show_text(self, text: str) -> None:
        with QSignalBlocker(self.script_edit):
            self.script_edit.setPlainText(text)
        words = count_words(text)
        self.count_label.setText(f"{words} từ · {len(text)} ký tự")

    def _view_source(self) -> None:
        if self._source_text.strip():
            self._show_text(self._source_text)

    def _view_language(self, language: str) -> None:
        text = self._translations.get(language, "")
        if not text.strip():
            self._warn(f"Ngôn ngữ {language_name(language)} chưa có nội dung.")
            return
        self._show_text(text)

    def _delete_source(self) -> None:
        self._source_lang = None
        self._source_text = ""
        self._source_row = -1
        self._reload_targets(keep=True)

    def _delete_language(self, language: str) -> None:
        self._translations.pop(language, None)
        self._reload_targets(keep=True)

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        paint_status(self.status_label, "running")
        self.status_label.setText(message)

    def _on_failed(self, message: str) -> None:
        paint_status(self.status_label, "error")
        self.status_label.setText(message)
        for row in range(self.result_table.rowCount()):
            item = self.result_table.item(row, 1)
            if item is not None and item.text().startswith("Đang"):
                self._set_result_status(row, "Đã dừng", "error")

    def _on_worker_finished(self, worker: ScriptWorker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
        self.progress_bar.setValue(0)
        self._set_busy(False)
        self.busy_changed.emit(False)

    def _warn(self, message: str) -> None:
        paint_status(self.status_label, "warning")
        self.status_label.setText(message)

    def _on_script_edited(self) -> None:
        self._script_text = self.script_edit.toPlainText()
        words = count_words(self._script_text)
        self.count_label.setText(f"{words} từ · {len(self._script_text)} ký tự")

    # ── saving ───────────────────────────────────────────────────────────────
    def _base_name(self) -> str:
        topic = self.topic_edit.text().strip()
        if topic:
            return safe_filename(topic, "script")
        title = str(self._meta.get("title") or "").strip()
        if title:
            return safe_filename(title, "script")
        video_id = str(self._meta.get("video_id") or "").strip()
        return safe_filename(video_id or "script", "script")

    def _edit_clean_prompt(self) -> None:
        dialog = _CleanPromptDialog(self._clean_prompt, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self._clean_prompt = dialog.value()
        finally:
            dialog.deleteLater()

    def _clean(self) -> None:
        if self._worker is not None:
            return
        if self._script_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        if not self._script_text.strip():
            self._warn("Chưa có transcript — lấy transcript trước.")
            return
        api_key = self._api_key()
        if not api_key:
            self._warn("Chưa cấu hình API key Gemini — hãy nhập API key trong tab Cài đặt.")
            return
        prompt = self._clean_prompt.strip() or DEFAULT_CLEAN_PROMPT
        spec = {
            "kind": "clean",
            "text": self._script_text,
            "api_key": api_key,
            "model": self._current_model(),
            "clean_prompt": prompt,
        }
        self.progress_bar.setValue(0)
        paint_status(self.status_label, "running")
        self.status_label.setText("Đang làm sạch transcript…")
        self._start_worker(spec)

    def _translate(self) -> None:
        if self._worker is not None:
            return
        if self._script_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        if not self._script_text.strip():
            self._warn("Chưa có script — lấy transcript trước.")
            return
        if not self._profiles:
            self._warn("Chưa có ngôn ngữ đích — bấm “Thêm ngôn ngữ” trước.")
            return
        api_key = self._api_key()
        if not api_key:
            self._warn("Chưa cấu hình API key Gemini — hãy nhập API key trong tab Cài đặt.")
            return
        source = normalize_language(
            str(self._meta.get("source_language") or DEFAULT_LANGUAGE),
            DEFAULT_LANGUAGE)
        self._reload_targets(keep=False)
        for row in self._result_rows.values():
            self._set_result_status(row, "Đang chuyển thể…", "running")
            button = self.result_table.cellWidget(row, 2)
            if button is not None:
                button.setEnabled(False)
        spec = {
            "kind": "translate",
            "text": self._script_text,
            "api_key": api_key,
            "model": self._current_model(),
            "source_lang": source,
            "profiles": [p.to_dict() for p in self._profiles],
        }
        self.progress_bar.setValue(0)
        paint_status(self.status_label, "running")
        self.status_label.setText("Đang chuyển thể…")
        self._start_worker(spec)

    def _api_key(self) -> str:
        api = self._settings.get("api") or {}
        script = self._settings.get("script") or {}
        video = self._settings.get("video") or {}
        return (
            str(api.get("gemini_key") or "").strip()
            or str(script.get("api_key") or "").strip()
            or str(video.get("ai_api_key") or "").strip()
        )

    def _write_text_file(self, language: str, text: str) -> Optional[Path]:
        base = self._base_name()
        topic = self.topic_edit.text().strip()
        display_title = topic or str(self._meta.get("title") or base)
        lang_code = normalize_language_code(language, language)
        if self._manager is not None and hasattr(self._manager, "lang_dir"):
            self._manager.ensure_language_dirs([lang_code])
            lang_script_dir = self._manager.lang_dir("script", lang_code, create=True)
        else:
            lang_script_dir = self._script_dir / lang_code
            lang_script_dir.mkdir(parents=True, exist_ok=True)
        target = lang_script_dir / f"{base}_{lang_code}.txt"
        try:
            target.write_text(text.strip() + "\n", encoding="utf-8", newline="\n")
            is_source = lang_code == normalize_language_code(self._source_lang or DEFAULT_LANGUAGE)
            write_json_atomic(target.with_suffix(".json"), {
                "title": display_title if is_source else f"{display_title} [{lang_code.upper()}]",
                "language": lang_code,
                "url": str(self._meta.get("url") or ""),
            })
        except OSError as exc:
            paint_status(self.status_label, "error")
            self.status_label.setText(f"Không lưu được {target.name}: {exc}")
            return None
        return target

    def _save_language(self, language: str) -> None:
        text = self._translations.get(language, "")
        if not text.strip():
            self._warn(f"Ngôn ngữ {language_name(language)} chưa có bản dịch.")
            return
        if self._script_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        target = self._write_text_file(language, text)
        if target is None:
            return
        row = self._result_rows.get(language)
        if row is not None:
            self._set_result_status(row, f"Đã lưu {target.name}", "success")
            item = self.result_table.item(row, 1)
            if item is not None:
                item.setToolTip(str(target))
        paint_status(self.status_label, "success")
        self.status_label.setText(f"Đã lưu {target.name} vào SCRIPT.")
        self.reload_requested.emit()

    def _save_source(self) -> None:
        if not self._source_text.strip():
            self._warn("Chưa có nội dung gốc để lưu.")
            return
        if self._script_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        language = self._source_lang or DEFAULT_LANGUAGE
        target = self._write_text_file(language, self._source_text)
        if target is None:
            return
        row = self._source_row
        if 0 <= row < self.result_table.rowCount():
            self._set_result_status(row, f"Đã lưu {target.name}", "success")
            item = self.result_table.item(row, 1)
            if item is not None:
                item.setToolTip(str(target))
        paint_status(self.status_label, "success")
        self.status_label.setText(f"Đã lưu {target.name} vào SCRIPT.")
        self.reload_requested.emit()

    def _save_all(self) -> None:
        saved = 0
        if self._source_text.strip():
            self._save_source()
            saved += 1
        for language in list(self._translations):
            self._save_language(language)
            saved += 1
        if not saved:
            self._warn("Chưa có nội dung nào để lưu.")

    def update_from_pipeline(
        self,
        url: str = "",
        text: str = "",
        translations: Optional[Dict[str, str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if url and not self.url_edit.text().strip():
            self.url_edit.setText(url)
        if meta:
            self._meta = dict(meta)
            self._source_lang = str(meta.get("source_language") or DEFAULT_LANGUAGE)
        if text:
            self._script_text = text
            self._source_text = text
            with QSignalBlocker(self.script_edit):
                self.script_edit.setPlainText(text)
            words = count_words(text)
            self.count_label.setText(f"{words} từ · {len(text)} ký tự")
        if translations:
            for lang, trans_text in translations.items():
                self._on_translated(lang, trans_text, "")
        paint_status(self.status_label, "running")
        self.status_label.setText("Đang xử lý kịch bản tự động từ Automation…")

    def set_pipeline_busy(self, busy: bool) -> None:
        self._set_busy(busy)

