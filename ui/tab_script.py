"""Script tab: YouTube transcript → editable cues → multi-language adaptations.

The heavy work happens in :class:`ScriptWorker` (a ``QThread``): fetching the
transcript (YouTube captions or Whisper) and translating with Gemini. The tab
only collects parameters, edits cues and writes the resulting input files.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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

from core.ai_client import AICancelled, AIError, translate_text
from core.input_pipeline import (
    InputCancelled,
    InputError,
    get_youtube_transcript,
    unique_text_path,
)
from core.languages import language_choices, language_name, normalize_language
from core.subtitles import (
    Cue,
    cues_from_dicts,
    cues_to_dicts,
    cues_to_text,
    format_timestamp,
    text_to_cues,
    write_srt,
)
from ui.components.icons import set_icon
from ui.components.page import PageHeader, paint_status, wrap_scroll
from ui import theme
from ui.tab_input import _MODELS
from utils.files import safe_filename, write_json_atomic
from utils.logger import get_logger

logger = get_logger("ui.script")


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
                    spec["url"], spec["dest_dir"],
                    language=spec["language"],
                    prefer_youtube_subs=spec["prefer_subs"],
                    model_size=spec["model"],
                    keep_video=spec["keep_video"],
                    progress_cb=self._emit,
                    cancel_cb=self._cancelled,
                )
                self.transcript_ready.emit(cues_to_dicts(cues), dict(meta))
                return
            languages = list(spec.get("languages") or [])
            total = max(1, len(languages))
            for position, language in enumerate(languages):
                if self._cancelled():
                    raise InputCancelled("Đã huỷ chuyển thể.")

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
                        style=spec["style"], progress_cb=report,
                        cancel_cb=self._cancelled,
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
        self._cues: List[Cue] = []
        self._meta: Dict[str, Any] = {}
        self._translations: Dict[str, str] = {}
        self._result_rows: Dict[str, int] = {}
        self._input_dir: Optional[Path] = None
        self._script_dir: Optional[Path] = None
        self._downloads_dir: Optional[Path] = None
        self._loading = False

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
            "Dán link YouTube → lấy transcript có timestamp → sửa → chuyển thể "
            "sang nhiều ngôn ngữ và lưu thành file INPUT cho pipeline.",
        ))
        root.addWidget(self._build_source_box())

        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(self._build_transcript_box(), 3)
        columns.addWidget(self._build_translate_box(), 2)
        root.addLayout(columns, 1)

        self._set_busy(False)
        self._set_actions_enabled(False)

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

        self.source_language = QComboBox()
        for code, label in language_choices():
            self.source_language.addItem(label, code)
        grid.addWidget(QLabel("Ngôn ngữ nguồn"), 1, 0)
        grid.addWidget(self.source_language, 1, 1)
        self.whisper_model = QComboBox()
        for label, value in _MODELS:
            self.whisper_model.addItem(label, value)
        grid.addWidget(QLabel("Model Whisper"), 1, 2)
        grid.addWidget(self.whisper_model, 1, 3)

        self.prefer_subs_check = QCheckBox("Ưu tiên phụ đề có sẵn của YouTube")
        self.keep_video_check = QCheckBox("Giữ video sau khi nhận dạng")
        grid.addWidget(self.prefer_subs_check, 2, 1)
        grid.addWidget(self.keep_video_check, 2, 3)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(
            "API key Gemini (dùng chung với tab Video nếu để trống)")
        grid.addWidget(QLabel("API key AI"), 3, 0)
        grid.addWidget(self.api_key_edit, 3, 1, 1, 3)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("gemini-2.0-flash")
        grid.addWidget(QLabel("Model AI"), 4, 0)
        grid.addWidget(self.model_edit, 4, 1, 1, 3)

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
        grid.addLayout(action_row, 5, 0, 1, 4)

        self.status_label = QLabel("Sẵn sàng.")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        paint_status(self.status_label, "muted")
        grid.addWidget(self.status_label, 6, 0, 1, 4)
        return box

    def _build_transcript_box(self) -> QGroupBox:
        box = QGroupBox("Transcript")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.btn_delete = QPushButton("Xoá cue")
        set_icon(self.btn_delete, "trash", "muted", 13)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_merge = QPushButton("Gộp cue")
        set_icon(self.btn_merge, "list", "muted", 13)
        self.btn_merge.clicked.connect(self._merge_selected)
        self.btn_save_srt = QPushButton("Lưu SRT")
        set_icon(self.btn_save_srt, "download", "muted", 13)
        self.btn_save_srt.clicked.connect(self._save_srt)
        toolbar.addWidget(self.btn_delete)
        toolbar.addWidget(self.btn_merge)
        toolbar.addWidget(self.btn_save_srt)
        toolbar.addStretch(1)
        self.count_label = QLabel("0 cue · 0 ký tự")
        self.count_label.setObjectName("MutedLabel")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Thời gian", "Nội dung"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(230)
        self.table.itemChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, 1)

        self.fulltext_check = QCheckBox("Sửa toàn văn (dựng lại timing khi tắt)")
        self.fulltext_check.toggled.connect(self._toggle_fulltext)
        layout.addWidget(self.fulltext_check)
        self.full_edit = QPlainTextEdit()
        self.full_edit.setPlaceholderText("Toàn bộ lời kể…")
        self.full_edit.setMinimumHeight(110)
        self.full_edit.setVisible(False)
        layout.addWidget(self.full_edit)
        return box

    def _build_translate_box(self) -> QGroupBox:
        box = QGroupBox("Chuyển thể ngôn ngữ")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Ngôn ngữ đích"))
        self.lang_grid = QGridLayout()
        self.lang_grid.setHorizontalSpacing(8)
        self.lang_grid.setVerticalSpacing(2)
        self._lang_boxes: List[tuple[str, QCheckBox]] = []
        for index, (code, label) in enumerate(language_choices()):
            check = QCheckBox(label)
            check.setChecked(code == "en")
            self._lang_boxes.append((code, check))
            self.lang_grid.addWidget(check, index // 2, index % 2)
        layout.addLayout(self.lang_grid)

        layout.addWidget(QLabel("Prompt / phong cách thêm (không bắt buộc)"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Ví dụ: giọng kể hấp dẫn cho khán giả trẻ…")
        self.prompt_edit.setMaximumHeight(70)
        layout.addWidget(self.prompt_edit)

        self.translate_button = QPushButton("Chuyển thể")
        self.translate_button.setObjectName("primaryButton")
        set_icon(self.translate_button, "globe", "white", 15)
        self.translate_button.clicked.connect(self._translate)
        layout.addWidget(self.translate_button)

        self.result_table = QTableWidget(0, 4)
        self.result_table.setHorizontalHeaderLabels(
            ["Ngôn ngữ", "Trạng thái", "File", ""])
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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
        cfg = settings.get("script") or {}
        self._set_combo(self.source_language, cfg.get("source_language") or
                        (settings.get("input") or {}).get("language") or "vi")
        self._set_combo(self.whisper_model, cfg.get("whisper_model") or "small")
        self.prefer_subs_check.setChecked(bool(cfg.get("prefer_youtube_subs", True)))
        self.keep_video_check.setChecked(bool(cfg.get("keep_video", True)))
        key = str(cfg.get("api_key") or
                  (settings.get("video") or {}).get("ai_api_key") or "")
        self.api_key_edit.setText(key)
        self.model_edit.setText(str(cfg.get("model") or "gemini-2.0-flash"))
        self.prompt_edit.setPlainText(str(cfg.get("prompt_template") or ""))
        targets = {normalize_language(code) for code in (cfg.get("target_languages") or [])}
        for code, check in self._lang_boxes:
            check.setChecked(code in targets if targets else code == "en")

    def apply_to(self, settings: Dict[str, Any]) -> None:
        cfg = settings.setdefault("script", {})
        cfg["source_language"] = self.source_language.currentData() or "vi"
        cfg["whisper_model"] = self.whisper_model.currentData() or "small"
        cfg["prefer_youtube_subs"] = self.prefer_subs_check.isChecked()
        cfg["keep_video"] = self.keep_video_check.isChecked()
        cfg["api_key"] = self.api_key_edit.text().strip()
        cfg["model"] = self.model_edit.text().strip() or "gemini-2.0-flash"
        cfg["prompt_template"] = self.prompt_edit.toPlainText().strip()
        cfg["target_languages"] = [
            code for code, check in self._lang_boxes if check.isChecked()]
        self._settings = settings

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ── project context ──────────────────────────────────────────────────────
    def set_project(self, manager) -> None:
        if manager is None:
            self._input_dir = None
            self._script_dir = None
            self._downloads_dir = None
            self.output_label.setText(
                "Chưa chọn project — chọn project ở tab đầu tiên.")
            self._set_actions_enabled(False)
            return
        self._input_dir = Path(manager.input_dir)
        self._script_dir = Path(manager.script_dir)
        self._downloads_dir = Path(manager.downloads_dir)
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
        self.translate_button.setEnabled(enabled)
        self.save_all_button.setEnabled(enabled)

    def _set_busy(self, busy: bool) -> None:
        has_project = self._input_dir is not None
        self._set_actions_enabled(not busy and has_project)
        self.cancel_button.setEnabled(busy)
        for widget in (self.url_edit, self.api_key_edit, self.model_edit,
                       self.source_language, self.whisper_model):
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
            "dest_dir": str(self._downloads_dir or self._input_dir),
            "language": normalize_language(self.source_language.currentData() or "vi"),
            "prefer_subs": self.prefer_subs_check.isChecked(),
            "model": self.whisper_model.currentData() or "small",
            "keep_video": self.keep_video_check.isChecked(),
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
        self._cues = cues_from_dicts(cues)
        self._translations.clear()
        self._result_rows.clear()
        self.result_table.setRowCount(0)
        self._reload_table()
        self.full_edit.setPlainText(cues_to_text(self._cues))
        title = self._meta.get("title") or self._meta.get("video_id") or "video"
        origin = "phụ đề YouTube" if self._meta.get("origin") == "youtube" else "Whisper"
        paint_status(self.status_label, "success")
        self.status_label.setText(
            f"Đã lấy {len(self._cues)} cue từ {origin}: {title}")

    def _on_translated(self, language: str, text: str, error: str) -> None:
        row = self._result_rows.get(language)
        if row is None:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self._result_rows[language] = row
            self.result_table.setItem(
                row, 0, QTableWidgetItem(language_name(language)))
            self.result_table.setItem(row, 2, QTableWidgetItem("—"))
            save = QPushButton("Lưu")
            save.setObjectName("smallButton")
            save.clicked.connect(
                lambda _checked=False, lang=language: self._save_language(lang))
            self.result_table.setCellWidget(row, 3, save)
        if error:
            self._translations.pop(language, None)
            self._set_result_status(row, f"Lỗi: {error}", "error")
            return
        self._translations[language] = text
        self._set_result_status(row, f"Xong · {len(text)} ký tự", "success")
        button = self.result_table.cellWidget(row, 3)
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

    # ── cue editing ──────────────────────────────────────────────────────────
    def _reload_table(self) -> None:
        self._loading = True
        try:
            self.table.setRowCount(len(self._cues))
            for row, cue in enumerate(self._cues):
                number = QTableWidgetItem(str(row + 1))
                number.setFlags(Qt.ItemFlag.ItemIsEnabled)
                stamp = QTableWidgetItem(
                    f"{format_timestamp(cue.start)} → {format_timestamp(cue.end)}")
                stamp.setFlags(Qt.ItemFlag.ItemIsEnabled)
                text = QTableWidgetItem(cue.text)
                self.table.setItem(row, 0, number)
                self.table.setItem(row, 1, stamp)
                self.table.setItem(row, 2, text)
        finally:
            self._loading = False
        self._refresh_counts()

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 2:
            return
        row = item.row()
        if 0 <= row < len(self._cues):
            self._cues[row].text = item.text().strip()
            self._refresh_counts()

    def _refresh_counts(self) -> None:
        count = len(self._cues)
        chars = sum(len(cue.text) for cue in self._cues)
        self.count_label.setText(f"{count} cue · {chars} ký tự")

    def _selected_rows(self) -> List[int]:
        return sorted({index.row() for index in self.table.selectedIndexes()},
                      reverse=True)

    def _delete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self._warn("Hãy chọn cue cần xoá.")
            return
        for row in rows:
            if 0 <= row < len(self._cues):
                self._cues.pop(row)
        self._reload_table()

    def _merge_selected(self) -> None:
        rows = sorted(self._selected_rows())
        if len(rows) < 2:
            self._warn("Chọn từ 2 cue liền kề để gộp.")
            return
        cues = [self._cues[row] for row in rows if 0 <= row < len(self._cues)]
        merged = Cue(
            index=cues[0].index,
            start=min(cue.start for cue in cues),
            end=max(cue.end for cue in cues),
            text=" ".join(cue.text.strip() for cue in cues if cue.text.strip()),
        )
        for row in reversed(rows):
            if 0 <= row < len(self._cues):
                self._cues.pop(row)
        self._cues.insert(min(rows), merged)
        for position, cue in enumerate(self._cues, 1):
            cue.index = position
        self._reload_table()

    def _toggle_fulltext(self, checked: bool) -> None:
        self.full_edit.setVisible(checked)
        if checked:
            self.full_edit.setPlainText(cues_to_text(self._cues))
            return
        text = self.full_edit.toPlainText().strip()
        if not text:
            return
        total = self._cues[-1].end if self._cues else 0.0
        self._cues = text_to_cues(text, total)
        self._reload_table()

    # ── saving ───────────────────────────────────────────────────────────────
    def _base_name(self) -> str:
        title = str(self._meta.get("title") or "").strip()
        if title:
            return safe_filename(title, "script")
        video_id = str(self._meta.get("video_id") or "").strip()
        return safe_filename(video_id or "script", "script")

    def _save_srt(self) -> None:
        if not self._cues:
            self._warn("Chưa có transcript để lưu.")
            return
        if self._script_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        base = self._base_name()
        try:
            target = write_srt(self._cues, self._script_dir / f"{base}.srt")
            write_json_atomic(self._script_dir / f"{base}.json", {
                "title": str(self._meta.get("title") or base),
                "url": str(self._meta.get("url") or ""),
                "video_id": str(self._meta.get("video_id") or ""),
                "source_language": str(self._meta.get("source_language") or ""),
                "origin": str(self._meta.get("origin") or ""),
                "segments": cues_to_dicts(self._cues),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
        except OSError as exc:
            paint_status(self.status_label, "error")
            self.status_label.setText(f"Không lưu được SRT: {exc}")
            return
        paint_status(self.status_label, "success")
        self.status_label.setText(f"Đã lưu {target.name} và JSON kịch bản.")

    def _translate(self) -> None:
        if self._worker is not None:
            return
        if self._input_dir is None:
            self._warn("Chưa chọn project — chọn project trước.")
            return
        if not self._cues:
            self._warn("Chưa có transcript — lấy transcript trước.")
            return
        languages = [code for code, check in self._lang_boxes if check.isChecked()]
        if not languages:
            self._warn("Hãy chọn ít nhất một ngôn ngữ đích.")
            return
        api_key = self._api_key()
        if not api_key:
            self._warn("Thiếu API key Gemini — nhập key rồi thử lại.")
            return
        source = normalize_language(self.source_language.currentData() or "vi")
        languages = [code for code in languages if code != source] or languages
        self._translations.clear()
        self._result_rows.clear()
        self.result_table.setRowCount(0)
        for code in languages:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self._result_rows[code] = row
            self.result_table.setItem(
                row, 0, QTableWidgetItem(language_name(code)))
            self._set_result_status(row, "Đang dịch…", "running")
            self.result_table.setItem(row, 2, QTableWidgetItem("—"))
            save = QPushButton("Lưu")
            save.setObjectName("smallButton")
            save.setEnabled(False)
            save.clicked.connect(
                lambda _checked=False, lang=code: self._save_language(lang))
            self.result_table.setCellWidget(row, 3, save)
        spec = {
            "kind": "translate",
            "text": cues_to_text(self._cues),
            "api_key": api_key,
            "model": self.model_edit.text().strip() or "gemini-2.0-flash",
            "source_lang": source,
            "languages": languages,
            "style": self.prompt_edit.toPlainText().strip(),
        }
        self.progress_bar.setValue(0)
        paint_status(self.status_label, "running")
        self.status_label.setText("Đang chuyển thể…")
        self._start_worker(spec)

    def _api_key(self) -> str:
        key = self.api_key_edit.text().strip()
        if key:
            return key
        return str((self._settings.get("video") or {}).get("ai_api_key") or "")

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
            item = self.result_table.item(row, 2)
            if item is None:
                item = QTableWidgetItem()
                self.result_table.setItem(row, 2, item)
            item.setText(target.name)
            item.setToolTip(str(target))
            button = self.result_table.cellWidget(row, 3)
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
