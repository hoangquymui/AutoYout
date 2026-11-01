"""Script tab: YouTube transcript → merged plain text → multi-language adaptations.

The heavy work happens in :class:`ScriptWorker` (a ``QThread``): fetching the
existing YouTube captions and translating with Gemini. The tab merges the
captions into a single text (no timestamps), saves it as ``.txt`` and then
adapts it into other languages.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal, Qt
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
    DEFAULT_MODEL,
    MODEL_CHOICES,
    AICancelled,
    AIError,
    normalize_model,
    translate_text,
)
from core.input_pipeline import (
    InputCancelled,
    InputError,
    get_youtube_transcript,
    unique_text_path,
)
from core.language_profiles import LanguageProfile, load_profiles, save_profiles
from core.languages import (
    DEFAULT_LANGUAGE,
    language_choices,
    language_name,
    normalize_language,
)
from core.subtitles import cues_to_dicts
from ui.components.icons import set_icon
from ui.components.page import PageHeader, paint_status, stack_columns, wrap_scroll
from ui import theme
from utils.files import safe_filename, write_json_atomic
from utils.logger import get_logger

logger = get_logger("ui.script")


class _LanguagePromptDialog(QDialog):
    """Modal to add one target language and the prompt used to adapt the story."""

    def __init__(self, parent=None, *, language: str = "en", prompt: str = "",
                 title: str = "Thêm ngôn ngữ") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(480, 320)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        self.language_combo = QComboBox()
        for code, label in language_choices():
            self.language_combo.addItem(label, code)
        index = self.language_combo.findData(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        form.addRow("Tên ngôn ngữ", self.language_combo)
        layout.addLayout(form)

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
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def values(self) -> tuple[str, str]:
        return (str(self.language_combo.currentData() or "en"),
                self.prompt_edit.toPlainText().strip())


class _ManageLanguagesDialog(QDialog):
    """Modal listing every language profile to edit prompts or delete entries."""

    def __init__(self, profiles: List[LanguageProfile], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sửa prompt ngôn ngữ")
        self.setModal(True)
        self.resize(640, 440)
        self._profiles: List[LanguageProfile] = [
            LanguageProfile(p.language, p.prompt, p.id) for p in profiles]

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel(
            "Sửa prompt trực tiếp hoặc bấm thùng rác để xoá ngôn ngữ:"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Ngôn ngữ", "Prompt", ""])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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
            lang_item = QTableWidgetItem(language_name(profile.language))
            lang_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            lang_item.setData(Qt.ItemDataRole.UserRole, profile.language)
            delete = QPushButton()
            set_icon(delete, "trash", "muted", 13)
            delete.setToolTip("Xoá ngôn ngữ này")
            delete.clicked.connect(
                lambda _checked=False, code=profile.language: self._remove(code))
            self.table.setItem(row, 0, lang_item)
            self.table.setItem(row, 1, QTableWidgetItem(profile.prompt))
            self.table.setCellWidget(row, 2, delete)

    def _remove(self, language: str) -> None:
        self._profiles = [p for p in self._collect()
                          if p.language != language]
        self._rebuild()

    def _collect(self) -> List[LanguageProfile]:
        ids = {p.language: p.id for p in self._profiles}
        updated: List[LanguageProfile] = []
        for row in range(self.table.rowCount()):
            lang_item = self.table.item(row, 0)
            if lang_item is None:
                continue
            code = str(lang_item.data(Qt.ItemDataRole.UserRole) or "")
            if not code:
                continue
            prompt_item = self.table.item(row, 1)
            profile = LanguageProfile(
                code, prompt_item.text().strip() if prompt_item else "")
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
            profiles = list(spec.get("profiles") or [])
            total = max(1, len(profiles))
            for position, profile in enumerate(profiles):
                if self._cancelled():
                    raise InputCancelled("Đã huỷ chuyển thể.")
                language = str(profile.get("language") or DEFAULT_LANGUAGE)

                def report(done: int, count: int, _index: int = position) -> None:
                    fraction = done / max(1, count)
                    self._emit(
                        int(100 * (_index + fraction) / total),
                        f"Đang chuyển thể {language_name(language)} "
                        f"({done}/{count} đoạn)…",
                    )

                try:
                    text = translate_text(
                        spec["text"], spec["api_key"], spec["model"],
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._worker: Optional[ScriptWorker] = None
        self._script_text: str = ""
        self._meta: Dict[str, Any] = {}
        self._translations: Dict[str, str] = {}
        self._result_rows: Dict[str, int] = {}
        self._profiles: List[LanguageProfile] = []
        self._input_dir: Optional[Path] = None
        self._script_dir: Optional[Path] = None

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(wrap_scroll(content))

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addWidget(PageHeader(
            "Kịch bản",
            "Dán link YouTube → lấy transcript → gộp thành văn bản (bỏ thời gian) "
            "→ lưu TXT → chuyển thể sang nhiều ngôn ngữ cho pipeline.",
        ))
        root.addWidget(self._build_source_box())

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self._columns = columns
        columns.addWidget(self._build_transcript_box(), 3)
        columns.addWidget(self._build_translate_box(), 2)
        root.addLayout(columns, 1)

        self._set_busy(False)
        self._set_actions_enabled(False)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        stack_columns(self, self._columns, 800)

    # ── construction ─────────────────────────────────────────────────────────
    def _build_source_box(self) -> QGroupBox:
        box = QGroupBox("Nguồn YouTube")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Dán link video YouTube…")
        grid.addWidget(QLabel("Link video"), 0, 0)
        grid.addWidget(self.url_edit, 0, 1, 1, 3)

        self.fetch_button = QPushButton("Lấy transcript")
        self.fetch_button.setObjectName("primaryButton")
        set_icon(self.fetch_button, "download", "white", 15)
        self.fetch_button.clicked.connect(self._fetch_transcript)
        self.cancel_button = QPushButton("Huỷ")
        set_icon(self.cancel_button, "x", "muted", 13)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.fetch_button)
        action_row.addWidget(self.cancel_button)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        action_row.addWidget(self.progress_bar, 1)
        grid.addLayout(action_row, 1, 0, 1, 4)

        self.status_label = QLabel("Sẵn sàng.")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        paint_status(self.status_label, "muted")
        grid.addWidget(self.status_label, 2, 0, 1, 4)
        return box

    def _build_transcript_box(self) -> QGroupBox:
        box = QGroupBox("Script (đã gộp, không kèm thời gian)")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.btn_save_txt = QPushButton("Lưu TXT")
        set_icon(self.btn_save_txt, "download", "accent", 14)
        self.btn_save_txt.clicked.connect(self._save_txt)
        toolbar.addWidget(self.btn_save_txt)
        toolbar.addStretch(1)
        self.count_label = QLabel("0 từ")
        self.count_label.setObjectName("MutedLabel")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.script_edit = QPlainTextEdit()
        self.script_edit.setPlaceholderText(
            "Lấy transcript xong, nội dung sẽ được gộp ở đây (không kèm thời gian).")
        self.script_edit.textChanged.connect(self._on_script_edited)
        layout.addWidget(self.script_edit, 1)
        return box

    def _build_translate_box(self) -> QGroupBox:
        box = QGroupBox("Chuyển thể ngôn ngữ")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_row.addWidget(QLabel("API key Gemini"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(
            "Dán API key Gemini (để trống dùng key ở tab Video)")
        key_row.addWidget(self.api_key_edit, 1)
        layout.addLayout(key_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_row.addWidget(QLabel("Model AI"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(MODEL_CHOICES)
        self.model_combo.setToolTip(
            "Model Gemini dùng để chuyển thể — có thể gõ tên model khác")
        model_row.addWidget(self.model_combo, 1)
        layout.addLayout(model_row)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(QLabel("Ngôn ngữ đích"))
        head.addStretch(1)
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
        head.addWidget(self.add_language_button)
        head.addWidget(self.edit_prompts_button)
        layout.addLayout(head)

        self.target_hint = QLabel(
            "Chưa có ngôn ngữ — bấm “Thêm ngôn ngữ” để tạo.")
        self.target_hint.setObjectName("MutedLabel")
        self.target_hint.setWordWrap(True)
        layout.addWidget(self.target_hint)

        self.translate_button = QPushButton("Chuyển thể")
        self.translate_button.setObjectName("primaryButton")
        set_icon(self.translate_button, "globe", "white", 15)
        self.translate_button.clicked.connect(self._translate)
        layout.addWidget(self.translate_button)

        self.result_table = QTableWidget(0, 3)
        self.result_table.setHorizontalHeaderLabels(
            ["Ngôn ngữ", "Trạng thái", ""])
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.result_table.setMinimumHeight(150)
        layout.addWidget(self.result_table, 1)

        self.save_all_button = QPushButton("Lưu tất cả vào INPUT")
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
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        cfg = settings.setdefault("script", {})
        key = str(cfg.get("api_key") or
                  (settings.get("video") or {}).get("ai_api_key") or "")
        self.api_key_edit.setText(key)
        model = normalize_model(str(cfg.get("model") or DEFAULT_MODEL))
        cfg["model"] = model
        self.model_combo.setCurrentText(model)
        self._profiles = load_profiles()
        self._reload_targets()

    def apply_to(self, settings: Dict[str, Any]) -> None:
        cfg = settings.setdefault("script", {})
        cfg["api_key"] = self.api_key_edit.text().strip()
        cfg["model"] = self.model_combo.currentText().strip() or DEFAULT_MODEL
        self._settings = settings

    # ── language profiles ────────────────────────────────────────────────────
    def _add_language(self) -> None:
        dialog = _LanguagePromptDialog(self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            language, prompt = dialog.values()
        finally:
            dialog.deleteLater()
        language = normalize_language(language, language)
        for profile in self._profiles:
            if profile.language == language:
                profile.prompt = prompt
                break
        else:
            self._profiles.append(LanguageProfile(language, prompt))
        self._persist_profiles()
        self._reload_targets()

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
        self._reload_targets()

    def _persist_profiles(self) -> None:
        try:
            self._profiles = save_profiles(self._profiles)
        except OSError as exc:
            self._warn(f"Không lưu được language_prompt.json: {exc}")

    def _reload_targets(self, keep: bool = True) -> None:
        previous = dict(self._translations) if keep else {}
        self._translations.clear()
        self._result_rows.clear()
        self.result_table.setRowCount(0)
        for profile in self._profiles:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self._result_rows[profile.language] = row
            self.result_table.setItem(
                row, 0, QTableWidgetItem(language_name(profile.language)))
            save = QPushButton("Lưu")
            save.setObjectName("smallButton")
            save.clicked.connect(
                lambda _checked=False, lang=profile.language:
                self._save_language(lang))
            self.result_table.setCellWidget(row, 2, save)
            text = previous.get(profile.language)
            if text:
                self._translations[profile.language] = text
                self._set_result_status(row, f"Xong · {len(text)} ký tự", "success")
                save.setEnabled(True)
            else:
                self._set_result_status(row, "Chưa chuyển thể", "muted")
                save.setEnabled(False)
        if self._profiles:
            self.target_hint.setText(
                f"{len(self._profiles)} ngôn ngữ · sửa prompt bằng nút bút chì.")
        else:
            self.target_hint.setText(
                "Chưa có ngôn ngữ — bấm “Thêm ngôn ngữ” để tạo.")

    # ── project context ──────────────────────────────────────────────────────
    def set_project(self, manager) -> None:
        if manager is None:
            self._input_dir = None
            self._script_dir = None
            self.output_label.setText(
                "Chưa chọn project — chọn project ở tab đầu tiên.")
            self._set_actions_enabled(False)
            return
        self._input_dir = Path(manager.input_dir)
        self._script_dir = Path(manager.script_dir)
        self.output_label.setText(
            f"Kịch bản: {self._script_dir} · Input: {self._input_dir}")
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
        self.btn_save_txt.setEnabled(enabled)
        self.translate_button.setEnabled(enabled)
        self.save_all_button.setEnabled(enabled)

    def _set_busy(self, busy: bool) -> None:
        has_project = self._input_dir is not None
        self._set_actions_enabled(not busy and has_project)
        self.cancel_button.setEnabled(busy)
        for widget in (self.url_edit, self.api_key_edit, self.model_combo,
                       self.add_language_button, self.edit_prompts_button):
            widget.setEnabled(not busy)

    # ── transcript job ───────────────────────────────────────────────────────
    def _fetch_transcript(self) -> None:
        if self._worker is not None:
            return
        if self._input_dir is None or self._script_dir is None:
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
        worker.translated.connect(self._on_translated)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        self._worker = worker
        self._set_busy(True)
        self.busy_changed.emit(True)
        worker.start()

    def _on_transcript(self, cues: List[dict], meta: dict) -> None:
        self._meta = dict(meta or {})
        self._reload_targets(keep=False)
        self._script_text = self._merge_cues_to_text(cues)
        self.script_edit.setPlainText(self._script_text)
        title = self._meta.get("title") or self._meta.get("video_id") or "video"
        paint_status(self.status_label, "success")
        self.status_label.setText(
            f"Đã gộp {len(cues)} dòng phụ đề thành văn bản: {title}")

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
        return "\n\n".join(paragraphs)

    def _on_translated(self, language: str, text: str, error: str) -> None:
        row = self._result_rows.get(language)
        if row is None:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self._result_rows[language] = row
            self.result_table.setItem(
                row, 0, QTableWidgetItem(language_name(language)))
            save = QPushButton("Lưu")
            save.setObjectName("smallButton")
            save.clicked.connect(
                lambda _checked=False, lang=language: self._save_language(lang))
            self.result_table.setCellWidget(row, 2, save)
        if error:
            self._translations.pop(language, None)
            self._set_result_status(row, f"Lỗi: {error}", "error")
            return
        self._translations[language] = text
        self._set_result_status(row, f"Xong · {len(text)} ký tự", "success")
        button = self.result_table.cellWidget(row, 2)
        if button is not None:
            button.setEnabled(True)

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
        self._set_busy(False)
        self.busy_changed.emit(False)

    def _warn(self, message: str) -> None:
        paint_status(self.status_label, "warning")
        self.status_label.setText(message)

    def _on_script_edited(self) -> None:
        self._script_text = self.script_edit.toPlainText()
        words = len(self._script_text.split())
        self.count_label.setText(f"{words} từ · {len(self._script_text)} ký tự")

    # ── saving ───────────────────────────────────────────────────────────────
    def _base_name(self) -> str:
        title = str(self._meta.get("title") or "").strip()
        if title:
            return safe_filename(title, "script")
        video_id = str(self._meta.get("video_id") or "").strip()
        return safe_filename(video_id or "script", "script")

    def _save_txt(self) -> None:
        text = self.script_edit.toPlainText().strip()
        if not text:
            self._warn("Chưa có nội dung script để lưu.")
            return
        if self._input_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        base = self._base_name()
        target = unique_text_path(self._input_dir, base)
        try:
            target.write_text(text + "\n", encoding="utf-8", newline="\n")
        except OSError as exc:
            paint_status(self.status_label, "error")
            self.status_label.setText(f"Không lưu được {target.name}: {exc}")
            return
        paint_status(self.status_label, "success")
        self.status_label.setText(f"Đã lưu {target.name} vào INPUT.")
        self.reload_requested.emit()

    def _translate(self) -> None:
        if self._worker is not None:
            return
        if self._input_dir is None:
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
            self._warn("Thiếu API key Gemini — dán key vào ô trên hoặc tab Video.")
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
            "model": self.model_combo.currentText().strip() or DEFAULT_MODEL,
            "source_lang": source,
            "profiles": [p.to_dict() for p in self._profiles],
        }
        self.progress_bar.setValue(0)
        paint_status(self.status_label, "running")
        self.status_label.setText("Đang chuyển thể…")
        self._start_worker(spec)

    def _api_key(self) -> str:
        return (self.api_key_edit.text().strip()
                or str((self._settings.get("video") or {}).get("ai_api_key") or ""))

    def _save_language(self, language: str) -> None:
        text = self._translations.get(language, "")
        if not text.strip():
            self._warn(f"Ngôn ngữ {language_name(language)} chưa có bản dịch.")
            return
        if self._input_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        base = self._base_name()
        target = unique_text_path(self._input_dir, f"{base}_{language}")
        try:
            target.write_text(text.strip() + "\n", encoding="utf-8", newline="\n")
            write_json_atomic(target.with_suffix(".json"), {
                "title": f"{self._meta.get('title') or base} [{language.upper()}]",
                "language": language,
                "url": str(self._meta.get("url") or ""),
            })
        except OSError as exc:
            paint_status(self.status_label, "error")
            self.status_label.setText(f"Không lưu được {target.name}: {exc}")
            return
        row = self._result_rows.get(language)
        if row is not None:
            self._set_result_status(row, f"Đã lưu {target.name}", "success")
            item = self.result_table.item(row, 1)
            if item is not None:
                item.setToolTip(str(target))
            button = self.result_table.cellWidget(row, 2)
            if button is not None:
                button.setEnabled(False)
        paint_status(self.status_label, "success")
        self.status_label.setText(f"Đã lưu {target.name} vào INPUT.")
        self.reload_requested.emit()

    def _save_all(self) -> None:
        saved = 0
        for language in list(self._translations):
            self._save_language(language)
            saved += 1
        if not saved:
            self._warn("Chưa có bản dịch nào để lưu.")
