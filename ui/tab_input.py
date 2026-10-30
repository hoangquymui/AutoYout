"""Input tab: YouTube / web / local media → ``.txt`` inside the project.

This is step 1 of the pipeline. The heavy lifting lives in
:mod:`core.input_pipeline`; this tab only collects parameters and runs the job
in a :class:`QThread` so the window stays responsive.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.input_pipeline import (
    InputCancelled,
    InputError,
    save_text,
    transcribe_media,
    unique_text_path,
    web_to_text,
    youtube_to_text,
)
from core.languages import language_choices, normalize_language
from ui.components.icons import set_icon
from ui.components.page import PageHeader, icon_button, paint_status, wrap_scroll
from utils.files import write_json_atomic
from utils.logger import get_logger

logger = get_logger("ui.input")

_MEDIA_FILTER = (
    "Media (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.m4a *.aac *.flac *.ogg);;"
    "Tất cả (*)"
)
_MODELS = [
    ("tiny (nhanh nhất, kém chính xác)", "tiny"),
    ("base", "base"),
    ("small (khuyến nghị)", "small"),
    ("medium (chậm, chính xác hơn)", "medium"),
    ("large-v3 (chậm nhất, tốt nhất)", "large-v3"),
]


class InputWorker(QThread):
    """Runs one step-1 job: YouTube, web page or local media transcription."""

    progress = Signal(int, str)
    done = Signal(bool, str, str)

    def __init__(self, spec: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._spec = dict(spec)
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    def _cancelled(self) -> bool:
        return self._stop

    def _emit(self, percent: int, message: str) -> None:
        self.progress.emit(max(0, min(100, int(percent))), str(message))

    def run(self) -> None:  # noqa: D401 - QThread API
        spec = self._spec
        try:
            kind = self._spec["kind"]
            if kind == "youtube":
                target = youtube_to_text(
                    spec["url"], spec["out_dir"],
                    language=spec["language"], model_size=spec["model"],
                    video_dir=spec.get("video_dir"), keep_video=spec["keep_video"],
                    api_key=spec.get("api_key", ""), ai_model=spec.get("ai_model", ""),
                    progress_cb=self._emit, cancel_cb=self._cancelled,
                )
            elif kind == "web":
                target = web_to_text(
                    spec["url"], spec["out_dir"],
                    language=spec["language"], api_key=spec.get("api_key", ""),
                    ai_model=spec.get("ai_model", ""),
                    progress_cb=self._emit, cancel_cb=self._cancelled,
                )
            else:
                media = Path(spec["media"])
                self._emit(2, f"Đang nhận dạng {media.name}...")
                text = transcribe_media(
                    media, spec["language"], spec["model"],
                    progress_cb=lambda pct, msg: self._emit(
                        5 + int(pct * 0.85), msg),
                    cancel_cb=self._cancelled,
                )
                target = unique_text_path(spec["out_dir"], media.stem)
                self._emit(95, f"Đang lưu {target.name}...")
                save_text(target, text)
            self._write_meta(target, spec["language"])
            self.done.emit(True, f"Đã lưu {Path(target).name}", str(target))
        except InputCancelled:
            self.done.emit(False, "Đã huỷ nhập liệu.", "")
        except InputError as exc:
            self.done.emit(False, str(exc), "")
        except Exception as exc:  # never let the thread die silently
            logger.exception("Nhập liệu lỗi không mong đợi")
            self.done.emit(False, f"Lỗi không mong đợi: {exc}", "")

    @staticmethod
    def _write_meta(target: Path, language: str) -> None:
        """Create a sidecar ``.json`` so the scan knows this file's language."""
        meta_path = Path(target).with_suffix(".json")
        if meta_path.exists():
            return
        write_json_atomic(meta_path, {
            "title": Path(target).stem,
            "language": normalize_language(language),
        })


class InputTab(QWidget):
    busy_changed = Signal(bool)
    file_created = Signal(str)
    reload_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._worker: Optional[InputWorker] = None
        self._input_dir: Optional[Path] = None
        self._video_dir: Optional[Path] = None
        self._actions: List[QPushButton] = []

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(wrap_scroll(content))

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addWidget(PageHeader(
            "Nhập liệu",
            "YouTube / trang web → văn bản, hoặc phiên âm file media có sẵn. "
            "Kết quả được lưu vào thư mục INPUT của project.",
        ))

        columns = QHBoxLayout()
        columns.setSpacing(12)
        root.addLayout(columns, 1)
        left = QVBoxLayout()
        left.setSpacing(12)
        columns.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(12)
        columns.addLayout(right, 1)

        # ── YouTube ──────────────────────────────────────────────────────────
        yt_box = QGroupBox("YouTube → văn bản (tải MP4 + nhận dạng giọng nói)")
        yt_form = QFormLayout(yt_box)
        yt_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        yt_form.setHorizontalSpacing(12)
        yt_form.setVerticalSpacing(8)
        self.youtube_edit = QLineEdit()
        self.youtube_edit.setPlaceholderText("Dán link video YouTube…")
        yt_form.addRow("Link video", self.youtube_edit)
        self.youtube_language = self._language_combo()
        yt_form.addRow("Ngôn ngữ", self.youtube_language)
        self.youtube_model = QComboBox()
        for label, value in _MODELS:
            self.youtube_model.addItem(label, value)
        yt_form.addRow("Model Whisper", self.youtube_model)
        self.keep_video_check = QCheckBox("Giữ file MP4 sau khi nhận dạng")
        yt_form.addRow("", self.keep_video_check)
        self.ai_fix_check = QCheckBox("Nhờ AI sửa lỗi chính tả / dấu câu (Gemini)")
        yt_form.addRow("", self.ai_fix_check)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(
            "API key Gemini (dùng chung với tab Video nếu để trống)")
        yt_form.addRow("API key AI", self.api_key_edit)
        yt_button = QPushButton("Tải && nhận dạng → TXT")
        yt_button.setObjectName("primaryButton")
        set_icon(yt_button, "download", "white", 15)
        yt_button.clicked.connect(lambda: self._start("youtube"))
        self._actions.append(yt_button)
        yt_form.addRow("", yt_button)
        left.addWidget(yt_box)

        # ── local media ──────────────────────────────────────────────────────
        media_box = QGroupBox("Phiên âm file có sẵn → văn bản")
        media_form = QFormLayout(media_box)
        media_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        media_form.setHorizontalSpacing(12)
        media_form.setVerticalSpacing(8)
        self.media_edit = QLineEdit()
        self.media_edit.setPlaceholderText("Chọn file video/audio…")
        media_row = QHBoxLayout()
        media_row.setSpacing(6)
        media_row.addWidget(self.media_edit, 1)
        media_browse = icon_button(
            "file", "Chọn file media…", role="muted", size=15, side=30)
        media_browse.clicked.connect(self._browse_media)
        media_row.addWidget(media_browse)
        media_form.addRow("File media", self._wrap(media_row))
        self.media_language = self._language_combo()
        media_form.addRow("Ngôn ngữ", self.media_language)
        self.media_model = QComboBox()
        for label, value in _MODELS:
            self.media_model.addItem(label, value)
        media_form.addRow("Model Whisper", self.media_model)
        media_button = QPushButton("Phiên âm → TXT")
        set_icon(media_button, "waveform", "accent", 14)
        media_button.clicked.connect(lambda: self._start("transcribe"))
        self._actions.append(media_button)
        media_form.addRow("", media_button)
        left.addWidget(media_box)
        left.addStretch(1)

        # ── web ──────────────────────────────────────────────────────────────
        web_box = QGroupBox("Trang web → văn bản")
        web_form = QFormLayout(web_box)
        web_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        web_form.setHorizontalSpacing(12)
        web_form.setVerticalSpacing(8)
        self.web_edit = QLineEdit()
        self.web_edit.setPlaceholderText("Dán link bài viết…")
        web_form.addRow("Link trang", self.web_edit)
        self.web_language = self._language_combo()
        web_form.addRow("Ngôn ngữ", self.web_language)
        web_button = QPushButton("Lấy nội dung → TXT")
        set_icon(web_button, "globe", "accent", 14)
        web_button.clicked.connect(lambda: self._start("web"))
        self._actions.append(web_button)
        web_form.addRow("", web_button)
        right.addWidget(web_box)

        # ── progress ─────────────────────────────────────────────────────────
        progress_box = QGroupBox("Tiến trình")
        progress_layout = QVBoxLayout(progress_box)
        progress_layout.setSpacing(8)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_label = QLabel("Sẵn sàng.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("MutedLabel")
        status_row.addWidget(self.status_label, 1)
        self.cancel_button = QPushButton("Huỷ")
        set_icon(self.cancel_button, "x", "muted", 13)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        status_row.addWidget(self.cancel_button)
        progress_layout.addLayout(status_row)
        self.output_label = QLabel("Chưa chọn project — chọn project ở tab đầu tiên.")
        self.output_label.setObjectName("MutedLabel")
        self.output_label.setWordWrap(True)
        progress_layout.addWidget(self.output_label)
        right.addWidget(progress_box)

        hint_box = QGroupBox("Quy trình")
        hint_layout = QVBoxLayout(hint_box)
        hint = QLabel(
            "1. Dán link YouTube → tải MP4 → nhận dạng → TXT (có thể nhờ AI sửa lỗi).\n"
            "2. Hoặc dán link bài viết → TXT.\n"
            "3. Hoặc chọn file media có sẵn để phiên âm.\n"
            "4. File .json đi kèm ghi ngôn ngữ để TTS chọn giọng phù hợp "
            "(vi = H2MTTS, ngôn ngữ khác = edge-tts).\n"
            "5. Quay lại tab Dự án, bấm quét lại để nạp file mới."
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        hint_layout.addWidget(hint)
        right.addWidget(hint_box)
        right.addStretch(1)

        self._set_busy(False)
        self._set_actions_enabled(False)

    # ── construction helpers ─────────────────────────────────────────────────
    @staticmethod
    def _wrap(layout) -> QWidget:
        holder = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(layout)
        return holder

    @staticmethod
    def _language_combo() -> QComboBox:
        combo = QComboBox()
        for code, label in language_choices():
            combo.addItem(label, code)
        return combo

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _language_combos(self) -> List[QComboBox]:
        return [self.youtube_language, self.media_language, self.web_language]

    def _set_actions_enabled(self, enabled: bool) -> None:
        for button in self._actions:
            button.setEnabled(enabled)

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        cfg = settings.get("input") or {}
        language = cfg.get("language") or (settings.get("tts") or {}).get("language") or "vi"
        for combo in self._language_combos():
            self._set_combo(combo, normalize_language(language))
        model = str(cfg.get("whisper_model") or "small")
        self._set_combo(self.youtube_model, model)
        self._set_combo(self.media_model, model)
        self.keep_video_check.setChecked(bool(cfg.get("keep_video", True)))
        self.ai_fix_check.setChecked(bool(cfg.get("ai_fix", False)))
        key = str(cfg.get("ai_api_key") or
                  (settings.get("video") or {}).get("ai_api_key") or "")
        self.api_key_edit.setText(key)

    def apply_to(self, settings: Dict[str, Any]) -> None:
        cfg = settings.setdefault("input", {})
        cfg["language"] = self.youtube_language.currentData() or "vi"
        cfg["whisper_model"] = self.youtube_model.currentData() or "small"
        cfg["keep_video"] = self.keep_video_check.isChecked()
        cfg["ai_fix"] = self.ai_fix_check.isChecked()
        cfg["ai_api_key"] = self.api_key_edit.text().strip()
        self._settings = settings

    # ── project context ──────────────────────────────────────────────────────
    def set_project(self, manager) -> None:
        if manager is None:
            self._input_dir = None
            self._video_dir = None
            self.output_label.setText(
                "Chưa chọn project — chọn project ở tab đầu tiên.")
            self._set_actions_enabled(False)
            return
        self._input_dir = Path(manager.input_dir)
        self._video_dir = Path(manager.project_dir) / "downloads"
        self.output_label.setText(f"Lưu vào: {self._input_dir}")
        self._set_actions_enabled(self._worker is None)

    def set_default_language(self, code: str) -> None:
        for combo in self._language_combos():
            self._set_combo(combo, normalize_language(code))

    # ── jobs ─────────────────────────────────────────────────────────────────
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _api_key(self) -> str:
        if not self.ai_fix_check.isChecked():
            return ""
        key = self.api_key_edit.text().strip()
        if key:
            return key
        return str((self._settings.get("video") or {}).get("ai_api_key") or "")

    def _ai_model(self) -> str:
        return str((self._settings.get("video") or {}).get("ai_model") or "")

    def _start(self, kind: str) -> None:
        if self._worker is not None:
            return
        if self._input_dir is None:
            paint_status(self.status_label, "error")
            self.status_label.setText("Chưa chọn project — chọn project trước.")
            return
        if kind == "youtube":
            url = self.youtube_edit.text().strip()
            if not url:
                self._warn("Hãy dán link YouTube trước.")
                return
            spec = {
                "kind": "youtube", "url": url,
                "out_dir": str(self._input_dir),
                "video_dir": str(self._video_dir) if self._video_dir else "",
                "language": normalize_language(self.youtube_language.currentData() or "vi"),
                "model": self.youtube_model.currentData() or "small",
                "keep_video": self.keep_video_check.isChecked(),
                "api_key": self._api_key(),
                "ai_model": self._ai_model(),
            }
        elif kind == "web":
            url = self.web_edit.text().strip()
            if not url:
                self._warn("Hãy dán link trang web trước.")
                return
            spec = {
                "kind": "web", "url": url,
                "out_dir": str(self._input_dir),
                "language": normalize_language(self.web_language.currentData() or "vi"),
                "model": "", "keep_video": True,
                "api_key": self._api_key(),
                "ai_model": self._ai_model(),
            }
        else:
            media = Path(self.media_edit.text().strip())
            if not media.is_file():
                self._warn("Hãy chọn file media có sẵn.")
                return
            spec = {
                "kind": "transcribe", "media": str(media),
                "out_dir": str(self._input_dir),
                "language": normalize_language(self.media_language.currentData() or "vi"),
                "model": self.media_model.currentData() or "small",
                "keep_video": True, "api_key": "", "ai_model": "",
            }

        worker = InputWorker(spec, self)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self.progress_bar.setValue(0)
        paint_status(self.status_label, "running")
        self.status_label.setText("Đang khởi động…")
        self._set_busy(True)
        self.busy_changed.emit(True)
        worker.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            paint_status(self.status_label, "warning")
            self.status_label.setText("Đang huỷ…")

    def stop_and_wait(self, timeout_ms: int = 15000) -> bool:
        """Cancel the running job and wait for its thread (used when closing)."""
        if self._worker is None:
            return True
        self._worker.cancel()
        finished = self._worker.wait(timeout_ms)
        if finished:
            self._worker = None
        return finished

    def _warn(self, message: str) -> None:
        paint_status(self.status_label, "warning")
        self.status_label.setText(message)

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        paint_status(self.status_label, "running")
        self.status_label.setText(message)

    def _on_done(self, ok: bool, message: str, path: str) -> None:
        self._worker = None
        self._set_busy(False)
        self.busy_changed.emit(False)
        if ok:
            self.progress_bar.setValue(100)
            paint_status(self.status_label, "success")
            self.status_label.setText(message)
            if path:
                self.file_created.emit(path)
            self.reload_requested.emit()
        else:
            paint_status(self.status_label, "error")
            self.status_label.setText(message)

    def _set_busy(self, busy: bool) -> None:
        self._set_actions_enabled(not busy and self._input_dir is not None)
        self.cancel_button.setEnabled(busy)
        for widget in (self.youtube_edit, self.web_edit, self.media_edit,
                       self.api_key_edit):
            widget.setEnabled(not busy)

    def _browse_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file media", self.media_edit.text(), _MEDIA_FILTER)
        if path:
            self.media_edit.setText(path)
