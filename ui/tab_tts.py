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

from core.edge_tts_engine import EdgeTTSEngine
from core.languages import language_choices, normalize_language
from core.piper_tts_engine import (
    ALL_EDGE_VOICES,
    F5TTSEngine,
    H2M_DEFAULT_VOICES,
    PiperTTSEngine,
    XTTSEngine,
    detect_gpu_status,
)
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

    def __init__(
        self,
        settings: Dict[str, Any],
        text: str,
        provider: str = "h2mtts",
        voice: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._text = text
        self._provider = provider
        self._voice = voice
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        if self._provider == "edge":
            engine = EdgeTTSEngine(self._settings)
        elif self._provider == "piper":
            engine = PiperTTSEngine(self._settings)
        elif self._provider == "f5tts":
            engine = F5TTSEngine(self._settings)
        elif self._provider == "xtts":
            engine = XTTSEngine(self._settings)
        else:
            engine = TTSEngine(self._settings)
        try:
            out_dir = Path(tempfile.gettempdir()) / "AutoYout" / "preview"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S_%f")
            out_path = out_dir / f"preview_{stamp}.mp3"
            if self._provider in ("edge", "piper", "f5tts", "xtts"):
                lang = "vi"
                if self._voice and "-" in self._voice and "_" not in self._voice:
                    lang = self._voice.split("-")[0].lower()
                elif self._voice and "_" in self._voice and self._provider == "piper":
                    lang = self._voice.split("_")[0].lower()
                elif self._provider in ("f5tts", "xtts"):
                    if "Vietnamese" in self._voice or "viXTTS" in self._voice:
                        lang = "vi"
                    else:
                        lang = "en"
                engine.synthesize(
                    self._text,
                    out_path,
                    language=lang,
                    voice=self._voice or None,
                    progress_cb=self.progress.emit,
                    message_cb=self.message.emit,
                    cancel_cb=self._cancel.is_set,
                )
            else:
                engine.synthesize(
                    self._text,
                    out_path,
                    voice=self._voice or None,
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
                "Engine & giọng mặc định",
                "Chọn model, giọng đọc (Nam / Nữ), thiết bị xử lý (CPU / GPU / Tự động), nhịp nghỉ và nghe thử.",
            )
        )

        columns = QHBoxLayout()
        columns.setSpacing(12)
        root.addLayout(columns, 1)

        left = QVBoxLayout()
        left.setSpacing(10)
        columns.addLayout(left, 3)

        # Hidden compatibility widgets for settings roundtrip
        self.h2m_edit = QLineEdit(self)
        self.h2m_edit.setPlaceholderText(default_h2mtts_path())
        self.h2m_edit.setVisible(False)
        self.engine_combo = QComboBox(self)
        self.engine_combo.addItem("v3 Turbo — chất lượng cao (48 kHz)", "turbo")
        self.engine_combo.setVisible(False)
        self.language_combo = QComboBox(self)
        for code, label in language_choices():
            self.language_combo.addItem(label, code)
        self.language_combo.setVisible(False)
        self.edge_hint = QLabel("", self)
        self.edge_hint.setVisible(False)

        # ── 1. Khung "Model" (Chọn Model & Giọng đọc) ────────────────────────
        self.model_box = QGroupBox("Model")
        model_form = QFormLayout(self.model_box)
        model_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        model_form.setHorizontalSpacing(12)
        model_form.setVerticalSpacing(8)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("H2MTTS (v3 Turbo — Giọng Việt Offline)", "h2mtts")
        self.provider_combo.addItem("EdgeTTS (Đa ngôn ngữ — Miễn phí)", "edge")
        self.provider_combo.addItem("PiperTTS (Đa ngôn ngữ — Siêu nhẹ Offline)", "piper")
        self.provider_combo.addItem("F5-TTS / E2-TTS (GPU — Zero-Shot Voice Cloning)", "f5tts")
        self.provider_combo.addItem("Coqui XTTS-v2 (GPU — Đa ngôn ngữ & Clone giọng)", "xtts")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        model_form.addRow("Model", self.provider_combo)

        self.region_combo = QComboBox()
        self.region_combo.addItem("Tất cả vùng miền", "")
        self.region_combo.addItem("Miền Bắc", "Bắc")
        self.region_combo.addItem("Miền Trung", "Trung")
        self.region_combo.addItem("Miền Nam", "Nam")
        self.region_combo.currentIndexChanged.connect(self._refresh_voice_list)
        self.region_label = QLabel("Vùng miền")
        model_form.addRow(self.region_label, self.region_combo)

        self.gender_combo = QComboBox()
        self.gender_combo.addItem("Tất cả giới tính (Nam & Nữ)", "")
        self.gender_combo.addItem("Giọng Nữ (Female)", "Nữ")
        self.gender_combo.addItem("Giọng Nam (Male)", "Nam")
        self.gender_combo.currentIndexChanged.connect(self._refresh_voice_list)
        self.gender_label = QLabel("Giới tính")
        model_form.addRow(self.gender_label, self.gender_combo)

        voice_row = QHBoxLayout()
        voice_row.setSpacing(6)
        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(260)
        self.voice_combo.currentIndexChanged.connect(lambda _: self._update_preview_btn())
        reload_btn = QPushButton("Nạp lại giọng")
        set_icon(reload_btn, "refresh", "accent", 14)
        reload_btn.clicked.connect(self._on_reload_voices_clicked)
        voice_row.addWidget(self.voice_combo, 1)
        voice_row.addWidget(reload_btn)
        model_form.addRow("Giọng đọc", self._wrap(voice_row))

        self.model_status = QLabel("Chưa nạp thông tin model.")
        self.model_status.setObjectName("MutedLabel")
        self.model_status.setWordWrap(True)
        model_form.addRow("", self.model_status)
        left.addWidget(self.model_box)

        # ── 2. Khung "Chạy với" (Chọn Tự động / GPU / CPU) ───────────────────
        self.run_with_box = QGroupBox("Chạy với")
        run_form = QFormLayout(self.run_with_box)
        run_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        run_form.setHorizontalSpacing(12)
        run_form.setVerticalSpacing(8)

        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Tự động (Ưu tiên GPU nếu có)", "auto")
        self.backend_combo.addItem("GPU (CUDA / Tăng tốc phần cứng)", "gpu")
        self.backend_combo.addItem("CPU (ONNX Runtime)", "onnx")
        run_form.addRow("Thiết bị chạy", self.backend_combo)
        left.addWidget(self.run_with_box)

        # ── 3. Khung "Nhịp đọc" (Tốc độ, Nghỉ câu, Nghỉ đoạn, Bitrate) ──────
        self.pacing_box = QGroupBox("Nhịp đọc")
        voice_form = QGridLayout(self.pacing_box)
        voice_form.setHorizontalSpacing(12)
        voice_form.setVerticalSpacing(6)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSuffix(" x")
        voice_form.addWidget(self._caption("Tốc độ"), 0, 0)
        voice_form.addWidget(self.speed_spin, 0, 1)

        self.gap_sentence_spin = QDoubleSpinBox()
        self.gap_sentence_spin.setRange(0.0, 3.0)
        self.gap_sentence_spin.setSingleStep(0.1)
        self.gap_sentence_spin.setDecimals(1)
        self.gap_sentence_spin.setSuffix(" giây")
        voice_form.addWidget(self._caption("Nghỉ giữa câu"), 0, 2)
        voice_form.addWidget(self.gap_sentence_spin, 0, 3)

        self.gap_paragraph_spin = QDoubleSpinBox()
        self.gap_paragraph_spin.setRange(0.0, 5.0)
        self.gap_paragraph_spin.setSingleStep(0.1)
        self.gap_paragraph_spin.setDecimals(1)
        self.gap_paragraph_spin.setSuffix(" giây")
        voice_form.addWidget(self._caption("Nghỉ giữa đoạn"), 1, 0)
        voice_form.addWidget(self.gap_paragraph_spin, 1, 1)

        self.max_chars_spin = QSpinBox()
        self.max_chars_spin.setRange(200, 400)
        self.max_chars_spin.setSingleStep(10)
        self.max_chars_spin.setSuffix(" ký tự")
        voice_form.addWidget(self._caption("Độ dài đoạn TTS"), 1, 2)
        voice_form.addWidget(self.max_chars_spin, 1, 3)

        self.bitrate_combo = QComboBox()
        for label, value in _BITRATES:
            self.bitrate_combo.addItem(label, value)
        voice_form.addWidget(self._caption("Bitrate MP3"), 2, 0)
        voice_form.addWidget(self.bitrate_combo, 2, 1, 1, 3)
        voice_form.setColumnStretch(1, 1)
        voice_form.setColumnStretch(3, 1)
        left.addWidget(self.pacing_box)
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
        self.refresh_gpu_state()

    def refresh_gpu_state(self) -> None:
        """Enable F5-TTS / E2-TTS and Coqui XTTS-v2 in provider_combo if GPU is present; dim/disable if no GPU."""
        has_gpu, _ = detect_gpu_status()
        model = self.provider_combo.model()
        gpu_items = [
            (
                "f5tts",
                "F5-TTS / E2-TTS (GPU — Zero-Shot Voice Cloning)",
                "F5-TTS / E2-TTS (Yêu cầu GPU — Đã làm mờ)",
            ),
            (
                "xtts",
                "Coqui XTTS-v2 (GPU — Đa ngôn ngữ & Clone giọng)",
                "Coqui XTTS-v2 (Yêu cầu GPU — Đã làm mờ)",
            ),
        ]
        for key, text_gpu, text_nogpu in gpu_items:
            idx = self.provider_combo.findData(key)
            if idx < 0:
                continue
            item = model.item(idx) if hasattr(model, "item") else None
            if has_gpu:
                self.provider_combo.setItemText(idx, text_gpu)
                if item is not None:
                    item.setEnabled(True)
            else:
                self.provider_combo.setItemText(idx, text_nogpu)
                if item is not None:
                    item.setEnabled(False)

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
        self.refresh_gpu_state()
        tts = settings.get("tts") or {}
        raw_h2m = normalize_h2mtts_path(str((settings.get("paths") or {}).get("h2mtts") or "").strip())
        self.h2m_edit.setText(raw_h2m)
        provider = str(tts.get("provider") or "h2mtts").strip().lower()
        if provider not in ("h2mtts", "edge", "piper", "f5tts", "xtts"):
            provider = "h2mtts"
        self.provider_combo.blockSignals(True)
        self._set_combo(self.provider_combo, provider)
        self.provider_combo.blockSignals(False)
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
        self._refresh_voice_list()
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
        tts["provider"] = self.provider_combo.currentData() or "h2mtts"
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
        self._refresh_voice_list()

    def _on_engine_changed(self) -> None:
        self._refresh_voice_list()

    def _on_provider_changed(self) -> None:
        self._refresh_voice_list()
        self.changed.emit()

    def _on_reload_voices_clicked(self) -> None:
        provider = str(self.provider_combo.currentData() or "h2mtts")
        if provider == "h2mtts":
            self.engine_reload_requested.emit()
        self._refresh_voice_list()

    def _update_preview_btn(self) -> None:
        self.preview_button.setEnabled(self._can_preview())

    def _refresh_voice_list(self) -> None:
        self.refresh_gpu_state()
        provider = str(self.provider_combo.currentData() or "h2mtts")
        is_h2m = provider == "h2mtts"
        self.region_label.setVisible(is_h2m)
        self.region_combo.setVisible(is_h2m)
        has_gender = provider in ("h2mtts", "edge", "f5tts", "xtts")
        self.gender_label.setVisible(has_gender)
        self.gender_combo.setVisible(has_gender)
        gender_filter = str(self.gender_combo.currentData() or "").strip()

        current = self.voice_combo.currentData()
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()

        if provider == "edge":
            for label, vid in ALL_EDGE_VOICES:
                if gender_filter == "Nữ" and "— Nữ" not in label:
                    continue
                if gender_filter == "Nam" and "— Nam" not in label:
                    continue
                self.voice_combo.addItem(label, vid)
            self.model_status.setText(
                f"Đã nạp {self.voice_combo.count()} giọng EdgeTTS đa ngôn ngữ (Nam & Nữ)."
            )
            paint_status(self.model_status, "success")
            self.model_status.setToolTip("EdgeTTS trực tuyến miễn phí (không cần cài model)")
        elif provider == "piper":
            p_eng = PiperTTSEngine(self._settings)
            installed = p_eng.installed_voices()
            if installed:
                for label, model_id in installed:
                    self.voice_combo.addItem(label, model_id)
                self.model_status.setText(
                    f"Đã nạp {len(installed)} giọng PiperTTS đã cài đặt ({p_eng.root})."
                )
                paint_status(self.model_status, "success")
                self.model_status.setToolTip(str(p_eng.root))
            else:
                self.voice_combo.addItem("⚠ Chưa cài đặt PiperTTS / chưa tải model", "")
                self.model_status.setText(
                    "Chưa cài đặt PiperTTS hoặc chưa tải model giọng (Cài đặt → API → Giọng đọc → PiperTTS)."
                )
                paint_status(self.model_status, "warning")
                self.model_status.setToolTip(str(p_eng.root))
        elif provider == "f5tts":
            has_gpu, gpu_desc = detect_gpu_status()
            f5_eng = F5TTSEngine(self._settings)
            installed = f5_eng.installed_voices(gender=gender_filter)
            if not has_gpu:
                self.voice_combo.addItem("⚠ Yêu cầu GPU NVIDIA (Không phát hiện GPU)", "")
                self.model_status.setText(f"F5-TTS / E2-TTS yêu cầu GPU: {gpu_desc}")
                paint_status(self.model_status, "warning")
            elif installed:
                for label, model_id in installed:
                    self.voice_combo.addItem(label, model_id)
                self.model_status.setText(
                    f"Đã nạp {len(installed)} giọng đọc Nam & Nữ F5-TTS / E2-TTS ({gpu_desc})."
                )
                paint_status(self.model_status, "success")
                self.model_status.setToolTip(str(f5_eng.root))
            else:
                self.voice_combo.addItem("⚠ Chưa tải model F5-TTS / E2-TTS nào (Vào Cài đặt để tải)", "")
                self.model_status.setText(
                    "Chưa tải model F5-TTS / E2-TTS phù hợp (Cài đặt → API → Giọng đọc → F5-TTS / E2-TTS)."
                )
                paint_status(self.model_status, "warning")
                self.model_status.setToolTip(str(f5_eng.root))
        elif provider == "xtts":
            has_gpu, gpu_desc = detect_gpu_status()
            xtts_eng = XTTSEngine(self._settings)
            installed = xtts_eng.installed_voices(gender=gender_filter)
            if not has_gpu:
                self.voice_combo.addItem("⚠ Yêu cầu GPU NVIDIA (Không phát hiện GPU)", "")
                self.model_status.setText(f"Coqui XTTS-v2 yêu cầu GPU: {gpu_desc}")
                paint_status(self.model_status, "warning")
            elif installed:
                for label, model_id in installed:
                    self.voice_combo.addItem(label, model_id)
                self.model_status.setText(
                    f"Đã nạp {len(installed)} giọng đọc Nam & Nữ Coqui XTTS-v2 ({gpu_desc})."
                )
                paint_status(self.model_status, "success")
                self.model_status.setToolTip(str(xtts_eng.root))
            else:
                self.voice_combo.addItem("⚠ Chưa tải model Coqui XTTS-v2 nào (Vào Cài đặt để tải)", "")
                self.model_status.setText(
                    "Chưa tải model Coqui XTTS-v2 phù hợp (Cài đặt → API → Giọng đọc → Coqui XTTS-v2)."
                )
                paint_status(self.model_status, "warning")
                self.model_status.setToolTip(str(xtts_eng.root))
        else:
            # H2MTTS
            if self._engine is None:
                self.voice_combo.addItem("— Chưa nạp được danh sách giọng —", "")
                self.model_status.setText("Chưa nạp được engine H2MTTS.")
                paint_status(self.model_status, "muted")
            else:
                layout = self._engine.layout
                engine_key = self.engine_combo.currentData() or "turbo"
                region = self.region_combo.currentData() or ""
                try:
                    voices = self._engine.voices(engine_key)
                except Exception as exc:
                    logger.error("Không đọc được voice-packs: %s", exc)
                    voices = []
                filtered = []
                for voice in voices:
                    if region and voice.region != region:
                        continue
                    v_gen = str(getattr(voice, "gender", "") or "").lower()
                    if gender_filter == "Nữ" and v_gen not in ("nữ", "nu", "female", "f"):
                        continue
                    if gender_filter == "Nam" and v_gen not in ("nam", "male", "m"):
                        continue
                    filtered.append(voice)
                for voice in filtered:
                    parts = [voice.name]
                    if getattr(voice, "gender", ""):
                        parts.append(voice.gender)
                    if voice.region:
                        parts.append(voice.region)
                    self.voice_combo.addItem(" · ".join(parts), voice.name)
                if not layout.valid:
                    self.model_status.setText(f"Không thấy thư mục H2MTTS: {layout.root}")
                    paint_status(self.model_status, "error")
                else:
                    missing = layout.missing()
                    turbo = len(voices)
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

        self.voice_combo.blockSignals(False)
        preferred = current
        if not preferred and self._settings:
            preferred = str((self._settings.get("tts") or {}).get("voice") or "")
        if not preferred and provider == "h2mtts" and self._engine is not None:
            preferred = self._engine.default_voice(self.engine_combo.currentData() or "turbo")
        if preferred:
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
        voice_data = str(self.voice_combo.currentData() or "")
        provider = str(self.provider_combo.currentData() or "h2mtts")
        if not voice_data:
            self.preview_status.setText("Chưa có giọng đọc khả dụng.")
            paint_status(self.preview_status, "warning")
            return
        snapshot = copy.deepcopy(self._settings)
        self.apply_to(snapshot)
        self._worker = _PreviewWorker(
            snapshot,
            text,
            provider=provider,
            voice=voice_data,
            parent=self,
        )
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
        logger.info("Bắt đầu nghe thử giọng %s (%s)", voice_data, provider)
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
