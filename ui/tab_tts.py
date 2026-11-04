"""TTS tab: H2MTTS model, voice, pacing and voice preview."""
from __future__ import annotations

import copy
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.languages import language_choices, normalize_language
from core.settings import ROOT, default_h2mtts_path, normalize_h2mtts_path
from core.tts_engine import SynthesisCancelled, TTSEngine
from ui.components.audio_player import AudioPreview
from ui.components.icons import set_icon
from ui.components.page import PageHeader, icon_button, paint_status, wrap_scroll
from utils.logger import get_logger

logger = get_logger("ui.tts")

_BITRATES = [("192 kbps", "192k"), ("256 kbps", "256k"), ("320 kbps", "320k")]


class _PreviewWorker(QThread):
    progress = Signal(int)
    message = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Dict[str, Any], text: str, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._text = text
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        engine = TTSEngine(self._settings)
        try:
            out_dir = Path(tempfile.gettempdir()) / "AutoYout" / "preview"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S_%f")
            out_path = out_dir / f"preview_{stamp}.mp3"
            engine.synthesize(
                self._text,
                out_path,
                progress_cb=self.progress.emit,
                message_cb=self.message.emit,
                cancel_cb=self._cancel.is_set,
            )
            self.done.emit(str(out_path))
        except SynthesisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            logger.error("Nghe thử lỗi: %s", exc)
            self.failed.emit(str(exc))
        finally:
            engine.close()


class TTSConfigTab(QWidget):
    changed = Signal()
    busy_changed = Signal(bool)
    engine_reload_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._engine: Optional[TTSEngine] = None
        self._worker: Optional[_PreviewWorker] = None
        self._pipeline_busy = False

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(wrap_scroll(content))

        root = QVBoxLayout(content)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(10)
        root.addWidget(
            PageHeader(
                "Engine && giọng mặc định",
                "Chọn model, giọng đọc, nhịp nghỉ và nghe thử trước khi lồng tiếng.",
            )
        )

        columns = QHBoxLayout()
        columns.setSpacing(12)
        root.addLayout(columns, 1)

        left = QVBoxLayout()
        left.setSpacing(8)
        columns.addLayout(left, 3)

        model_box = QGroupBox("Engine H2MTTS")
        model_form = QFormLayout(model_box)
        model_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        model_form.setHorizontalSpacing(12)
        model_form.setVerticalSpacing(5)
        self.h2m_edit = QLineEdit()
        self.h2m_edit.setPlaceholderText(default_h2mtts_path())
        self.h2m_edit.textChanged.connect(lambda _: self.changed.emit())
        h2m_row = QHBoxLayout()
        h2m_row.setSpacing(6)
        h2m_row.addWidget(self.h2m_edit, 1)
        browse_h2m = icon_button(
            "folder", "Chọn thư mục H2MTTS…", role="muted", size=15, side=30
        )
        browse_h2m.clicked.connect(self._browse_h2m)
        reload_btn = QPushButton("Nạp lại giọng")
        set_icon(reload_btn, "refresh", "accent", 14)
        reload_btn.clicked.connect(self.engine_reload_requested.emit)
        h2m_row.addWidget(browse_h2m)
        h2m_row.addWidget(reload_btn)
        model_form.addRow("Thư mục H2MTTS", self._wrap(h2m_row))

        self.engine_combo = QComboBox()
        self.engine_combo.addItem("v3 Turbo — chất lượng cao (48 kHz)", "turbo")
        model_form.addRow("Model", self.engine_combo)

        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Tự động (GPU nếu có)", "auto")
        self.backend_combo.addItem("GPU CUDA (PyTorch)", "gpu")
        self.backend_combo.addItem("CPU / ONNX Runtime", "onnx")
        model_form.addRow("Backend", self.backend_combo)

        self.language_combo = QComboBox()
        for code, label in language_choices():
            self.language_combo.addItem(label, code)
        self.language_combo.setToolTip(
            "Ngôn ngữ mặc định cho file mới. Mỗi file vẫn có thể ghi đè bằng "
            "hậu tố tên file (_en, _zh…) hoặc trường 'language' trong JSON."
        )
        model_form.addRow("Ngôn ngữ mặc định", self.language_combo)
        self.edge_hint = QLabel(
            "Tiếng Việt tổng hợp bằng H2MTTS (offline). Ngôn ngữ khác dùng "
            "edge-tts (cần mạng, giọng mặc định lấy từ registry ngôn ngữ)."
        )
        self.edge_hint.setObjectName("MutedLabel")
        self.edge_hint.setWordWrap(True)
        model_form.addRow("", self.edge_hint)

        self.model_status = QLabel("Chưa nạp thông tin H2MTTS.")
        self.model_status.setObjectName("MutedLabel")
        self.model_status.setWordWrap(True)
        model_form.addRow("", self.model_status)
        left.addWidget(model_box)

        voice_box = QGroupBox("Giọng đọc && nhịp đọc")
        voice_form = QGridLayout(voice_box)
        voice_form.setHorizontalSpacing(12)
        voice_form.setVerticalSpacing(5)

        self.region_combo = QComboBox()
        self.region_combo.addItem("Tất cả vùng miền", "")
        self.region_combo.addItem("Miền Bắc", "Bắc")
        self.region_combo.addItem("Miền Trung", "Trung")
        self.region_combo.addItem("Miền Nam", "Nam")
        self.region_combo.currentIndexChanged.connect(self._refresh_voice_list)
        voice_form.addWidget(self._caption("Vùng miền"), 0, 0)
        voice_form.addWidget(self.region_combo, 0, 1, 1, 3)

        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(280)
        voice_form.addWidget(self._caption("Giọng đọc"), 1, 0)
        voice_form.addWidget(self.voice_combo, 1, 1, 1, 3)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSuffix(" x")
        voice_form.addWidget(self._caption("Tốc độ"), 2, 0)
        voice_form.addWidget(self.speed_spin, 2, 1)

        self.gap_sentence_spin = QDoubleSpinBox()
        self.gap_sentence_spin.setRange(0.0, 3.0)
        self.gap_sentence_spin.setSingleStep(0.1)
        self.gap_sentence_spin.setDecimals(1)
        self.gap_sentence_spin.setSuffix(" giây")
        voice_form.addWidget(self._caption("Nghỉ giữa câu"), 2, 2)
        voice_form.addWidget(self.gap_sentence_spin, 2, 3)

        self.gap_paragraph_spin = QDoubleSpinBox()
        self.gap_paragraph_spin.setRange(0.0, 5.0)
        self.gap_paragraph_spin.setSingleStep(0.1)
        self.gap_paragraph_spin.setDecimals(1)
        self.gap_paragraph_spin.setSuffix(" giây")
        voice_form.addWidget(self._caption("Nghỉ giữa đoạn"), 3, 0)
        voice_form.addWidget(self.gap_paragraph_spin, 3, 1)

        self.max_chars_spin = QSpinBox()
        self.max_chars_spin.setRange(200, 400)
        self.max_chars_spin.setSingleStep(10)
        self.max_chars_spin.setSuffix(" ký tự")
        voice_form.addWidget(self._caption("Độ dài đoạn TTS"), 3, 2)
        voice_form.addWidget(self.max_chars_spin, 3, 3)

        self.bitrate_combo = QComboBox()
        for label, value in _BITRATES:
            self.bitrate_combo.addItem(label, value)
        voice_form.addWidget(self._caption("Bitrate MP3"), 4, 0)
        voice_form.addWidget(self.bitrate_combo, 4, 1, 1, 3)
        voice_form.setColumnStretch(1, 1)
        voice_form.setColumnStretch(3, 1)
        left.addWidget(voice_box)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(10)
        columns.addLayout(right, 2)

        preview_box = QGroupBox("Nghe thử giọng đọc")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setSpacing(8)
        hint = QLabel("Đoạn văn ngắn để kiểm tra giọng (tối đa 600 ký tự).")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        preview_layout.addWidget(hint)
        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setMinimumHeight(110)
        preview_layout.addWidget(self.preview_edit, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.preview_button = QPushButton("Nghe thử")
        self.preview_button.setObjectName("primaryButton")
        set_icon(self.preview_button, "play", "white", 14)
        self.preview_button.clicked.connect(self._start_preview)
        self.cancel_button = QPushButton("Huỷ")
        set_icon(self.cancel_button, "x", "muted", 13)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_preview)
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        preview_layout.addLayout(buttons)
        self.preview_progress = QProgressBar()
        self.preview_progress.setRange(0, 100)
        self.preview_progress.setValue(0)
        preview_layout.addWidget(self.preview_progress)
        self.preview_status = QLabel("Sẵn sàng nghe thử.")
        self.preview_status.setObjectName("MutedLabel")
        self.preview_status.setWordWrap(True)
        preview_layout.addWidget(self.preview_status)
        self.audio_preview = AudioPreview()
        preview_layout.addWidget(self.audio_preview)
        right.addWidget(preview_box)
        right.addStretch(1)

        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return label

    @staticmethod
    def _wrap(layout) -> QWidget:
        holder = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(layout)
        return holder

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        tts = settings.get("tts") or {}
        raw_h2m = normalize_h2mtts_path(str((settings.get("paths") or {}).get("h2mtts") or "").strip())
        self.h2m_edit.setText(raw_h2m)
        self._set_combo(self.engine_combo, tts.get("engine", "turbo"))
        self._set_combo(self.backend_combo, tts.get("backend", "auto"))
        self._set_combo(self.language_combo,
                        normalize_language(tts.get("language", "vi")))
        self.speed_spin.setValue(float(tts.get("speed", 1.0) or 1.0))
        self.gap_sentence_spin.setValue(float(tts.get("gap_sentence", 0.3) or 0.0))
        self.gap_paragraph_spin.setValue(float(tts.get("gap_paragraph", 0.8) or 0.0))
        self.max_chars_spin.setValue(int(tts.get("max_chars", 350) or 350))
        self._set_combo(self.bitrate_combo, tts.get("bitrate", "192k"))
        self.preview_edit.setPlainText(str(tts.get("preview_text") or ""))
        self._on_engine_changed()
        if self._engine is not None:
            self._select_voice(str(tts.get("voice") or ""))

    def apply_to(self, settings: Dict[str, Any]) -> None:
        paths = settings.setdefault("paths", {})
        h2 = self.h2m_edit.text().strip()
        h2_norm = normalize_h2mtts_path(h2)
        try:
            h2_p = Path(h2_norm).resolve()
            root_p = ROOT.resolve()
            if h2_p == (root_p / "H2MTTS").resolve():
                paths["h2mtts"] = "H2MTTS"
            elif h2_p == (root_p / "tts" / "H2MTTS").resolve():
                paths["h2mtts"] = "tts/H2MTTS"
            else:
                paths["h2mtts"] = h2_norm
        except Exception:
            paths["h2mtts"] = h2_norm
        tts = settings.setdefault("tts", {})
        tts["engine"] = self.engine_combo.currentData() or "turbo"
        tts["backend"] = self.backend_combo.currentData() or "auto"
        tts["language"] = self.language_combo.currentData() or "vi"
        voice = self.voice_combo.currentData()
        if voice:
            tts["voice"] = voice
        tts["speed"] = round(float(self.speed_spin.value()), 3)
        tts["gap_sentence"] = round(float(self.gap_sentence_spin.value()), 2)
        tts["gap_paragraph"] = round(float(self.gap_paragraph_spin.value()), 2)
        tts["max_chars"] = int(self.max_chars_spin.value())
        tts["bitrate"] = self.bitrate_combo.currentData() or "192k"
        tts["preview_text"] = self.preview_edit.toPlainText().strip()
        self._settings = settings

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ── engine/voice discovery ───────────────────────────────────────────────
    def apply_theme(self) -> None:
        self.audio_preview.apply_theme()

    def set_engine(self, engine: Optional[TTSEngine]) -> None:
        self._engine = engine
        if engine is None:
            self.model_status.setText("Chưa nạp được engine TTS.")
            paint_status(self.model_status, "muted")
            self._refresh_voice_list()
            return
        layout = engine.layout
        if not layout.valid:
            self.model_status.setText(f"Không thấy thư mục H2MTTS: {layout.root}")
            paint_status(self.model_status, "error")
        else:
            missing = layout.missing()
            try:
                turbo = len(engine.voices("turbo"))
            except Exception as exc:
                turbo = 0
                missing = missing + [f"Lỗi đọc voice-packs: {exc}"]
            if missing:
                self.model_status.setText(
                    f"{turbo} giọng Turbo · thiếu {len(missing)} thành phần."
                )
                paint_status(self.model_status, "warning")
                self.model_status.setToolTip("\n".join(missing))
            else:
                self.model_status.setText(
                    f"{turbo} giọng Turbo · model đầy đủ."
                )
                paint_status(self.model_status, "success")
                self.model_status.setToolTip(str(layout.root))
        self._refresh_voice_list()

    def _on_engine_changed(self) -> None:
        self._refresh_voice_list()

    def _refresh_voice_list(self) -> None:
        if self._engine is None:
            self.voice_combo.blockSignals(True)
            self.voice_combo.clear()
            self.voice_combo.addItem("— Chưa nạp được danh sách giọng —", "")
            self.voice_combo.blockSignals(False)
            return
        engine_key = self.engine_combo.currentData() or "turbo"
        region = self.region_combo.currentData() or ""
        current = self.voice_combo.currentData()
        try:
            voices = self._engine.voices(engine_key)
        except Exception as exc:
            logger.error("Không đọc được voice-packs: %s", exc)
            voices = []
        filtered = [voice for voice in voices if not region or voice.region == region]
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for voice in filtered:
            suffix = f" · {voice.region}" if voice.region else ""
            self.voice_combo.addItem(f"{voice.name}{suffix}", voice.name)
        self.voice_combo.blockSignals(False)
        preferred = current
        if not preferred and self._settings:
            preferred = str((self._settings.get("tts") or {}).get("voice") or "")
        if not preferred:
            preferred = self._engine.default_voice(engine_key)
        self._select_voice(preferred)
        if self.voice_combo.count() == 0:
            self.voice_combo.addItem("— Không có giọng phù hợp —", "")
        self.preview_button.setEnabled(self._can_preview())

    def voice_names(self) -> List[str]:
        """Voice names available for the selected model (all regions)."""
        if self._engine is None:
            return []
        engine_key = self.engine_combo.currentData() or "turbo"
        try:
            return [voice.name for voice in self._engine.voices(engine_key)]
        except Exception as exc:
            logger.error("Không đọc được voice-packs: %s", exc)
            return []

    def default_voice_name(self) -> str:
        if self._engine is None:
            return ""
        try:
            return self._engine.default_voice(self.engine_combo.currentData() or "turbo")
        except Exception:
            return ""

    def _select_voice(self, name: str) -> None:
        index = self.voice_combo.findData(name)
        if index < 0 and name:
            folded = name.casefold()
            for i in range(self.voice_combo.count()):
                if str(self.voice_combo.itemData(i)).casefold() == folded:
                    index = i
                    break
        if index >= 0:
            self.voice_combo.setCurrentIndex(index)

    # ── preview ──────────────────────────────────────────────────────────────
    def _can_preview(self) -> bool:
        return (
            not self._pipeline_busy
            and self._worker is None
            and bool(self.voice_combo.currentData())
        )

    def set_busy(self, busy: bool) -> None:
        self._pipeline_busy = busy
        self.preview_button.setEnabled(self._can_preview())

    def preview_running(self) -> bool:
        return self._worker is not None

    def _browse_h2m(self) -> None:
        start = self.h2m_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục H2MTTS", start)
        if chosen:
            chosen = str(Path(chosen).resolve().as_posix())
            self.h2m_edit.setText(chosen)
            self.engine_reload_requested.emit()
            self.changed.emit()

    def _start_preview(self) -> None:
        if self._worker is not None:
            return
        text = self.preview_edit.toPlainText().strip()
        if not text:
            self.preview_status.setText("Hãy nhập đoạn văn nghe thử.")
            paint_status(self.preview_status, "warning")
            return
        if len(text) > 600:
            text = text[:600]
            self.preview_status.setText("Đoạn nghe thử đã được rút còn 600 ký tự.")
        if not self.voice_combo.currentData():
            self.preview_status.setText("Chưa có giọng đọc khả dụng.")
            paint_status(self.preview_status, "warning")
            return
        snapshot = copy.deepcopy(self._settings)
        self.apply_to(snapshot)
        self._worker = _PreviewWorker(snapshot, text, self)
        self._worker.progress.connect(self._on_preview_progress)
        self._worker.message.connect(self._on_preview_message)
        self._worker.done.connect(self._on_preview_done)
        self._worker.failed.connect(self._on_preview_failed)
        self._worker.cancelled.connect(self._on_preview_cancelled)
        self._worker.finished.connect(self._on_preview_finished)
        self.preview_progress.setValue(0)
        paint_status(self.preview_status, "running")
        self.preview_status.setText("Đang nạp model và tổng hợp giọng đọc…")
        self.preview_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.busy_changed.emit(True)
        logger.info("Bắt đầu nghe thử giọng %s", self.voice_combo.currentData())
        self._worker.start()

    def cancel_preview(self) -> None:
        self._cancel_preview()

    def _cancel_preview(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            self.preview_status.setText("Đang huỷ nghe thử (chờ model xử lý xong đoạn hiện tại)…")

    def _on_preview_progress(self, percent: int) -> None:
        self.preview_progress.setValue(max(0, min(100, int(percent))))

    def _on_preview_message(self, message: str) -> None:
        self.preview_status.setText(message)

    def _on_preview_done(self, path: str) -> None:
        self.preview_progress.setValue(100)
        paint_status(self.preview_status, "success")
        self.preview_status.setText("Đã tạo bản nghe thử — đang phát.")
        self.audio_preview.set_file(path)
        self.audio_preview.play()
        logger.info("Nghe thử xong: %s", Path(path).name)

    def _on_preview_failed(self, message: str) -> None:
        paint_status(self.preview_status, "error")
        self.preview_status.setText(message)

    def _on_preview_cancelled(self) -> None:
        paint_status(self.preview_status, "warning")
        self.preview_status.setText("Đã huỷ nghe thử.")

    def _on_preview_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.cancel_button.setEnabled(False)
        self.preview_button.setEnabled(self._can_preview())
        if self.audio_preview.path is None:
            self.audio_preview.set_file(None)
        self.busy_changed.emit(False)
