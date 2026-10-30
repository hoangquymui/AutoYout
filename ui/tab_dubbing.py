"""Lồng tiếng tab: cue table, per-cue TTS and MP3/SRT joining.

The tab wraps :mod:`core.dubbing` and embeds the engine/voice/preview page of
:class:`ui.tab_tts.TTSConfigTab`, so the whole model/voice configuration is
reused instead of being duplicated.
"""
from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import dubbing
from core.dubbing import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_PROCESSING,
    CueJob,
    DubbingEngine,
    DubbingError,
    load_cues,
    save_cues,
)
from core.project_manager import ITEM_TTS_DONE
from core.subtitles import format_timestamp
from core.tts_engine import SynthesisCancelled
from ui import theme
from ui.components.audio_player import AudioPreview
from ui.components.icons import set_icon
from ui.components.page import PageHeader, paint_status
from ui.tab_tts import TTSConfigTab
from utils.files import open_in_explorer
from utils.logger import get_logger
from utils.media_probe import format_duration

logger = get_logger("ui.dubbing")

_STATUS_TEXT = {
    STATUS_PENDING: "Chờ",
    STATUS_PROCESSING: "Đang tạo",
    STATUS_DONE: "Xong",
    STATUS_ERROR: "Lỗi",
}


def _status_color(status: str) -> str:
    return {
        STATUS_PENDING: theme.TEXT_DIM,
        STATUS_PROCESSING: theme.BLUE,
        STATUS_DONE: theme.SUCCESS,
        STATUS_ERROR: theme.DANGER,
    }.get(status, theme.TEXT_DIM)


class DubbingWorker(QThread):
    """Render every pending cue of one item in the background."""

    progress = Signal(int, str)
    cue_updated = Signal(int, str)
    done = Signal(bool, str)

    def __init__(self, settings: Dict[str, Any], manager, base: str, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._manager = manager
        self._base = base
        self._pause = threading.Event()
        self._cancel = threading.Event()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def pause_running(self) -> bool:
        return self._pause.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        cues = load_cues(self._manager, self._base)
        item = self._manager.get_item(self._base)
        if item is None or not cues:
            self.done.emit(False, "Không có cue để lồng tiếng.")
            return
        engine = DubbingEngine(self._settings)
        try:
            engine.synthesize_item(
                self._manager,
                item,
                cues,
                progress_cb=lambda percent: self.progress.emit(int(percent), ""),
                message_cb=lambda message: self.progress.emit(-1, message),
                pause_cb=self._pause.is_set,
                cancel_cb=self._cancel.is_set,
                cue_cb=lambda cue: self.cue_updated.emit(int(cue.id), cue.status),
            )
        except SynthesisCancelled:
            self.done.emit(False, "Đã huỷ lồng tiếng.")
        except DubbingError as exc:
            self.done.emit(False, str(exc))
        except Exception as exc:  # unexpected engine failure
            logger.exception("Lồng tiếng lỗi %s", self._base)
            self.done.emit(False, str(exc))
        else:
            failed = sum(1 for cue in cues if cue.status == STATUS_ERROR)
            ok = len(cues) - failed
            message = f"Xong {ok}/{len(cues)} cue."
            if failed:
                message += f" {failed} cue lỗi."
            self.progress.emit(100, message)
            self.done.emit(failed == 0, message)


class JoinWorker(QThread):
    """Join rendered cue MP3s into the item MP3 + SRT."""

    done = Signal(bool, str, str, float)

    def __init__(self, settings: Dict[str, Any], manager, base: str,
                 mode: str, gap: float, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._manager = manager
        self._base = base
        self._mode = mode
        self._gap = gap

    def run(self) -> None:
        cues = load_cues(self._manager, self._base)
        item = self._manager.get_item(self._base)
        if item is None or not cues:
            self.done.emit(False, "Không có cue để ghép.", "", 0.0)
            return
        try:
            engine = DubbingEngine(self._settings)
            mp3, _srt, duration = engine.join_item(
                self._manager, item, cues, mode=self._mode, gap=self._gap)
        except DubbingError as exc:
            self.done.emit(False, str(exc), "", 0.0)
        except Exception as exc:  # unexpected ffmpeg failure
            logger.exception("Ghép cue lỗi %s", self._base)
            self.done.emit(False, str(exc), "", 0.0)
        else:
            self.done.emit(True, f"Đã ghép {mp3.name}.", str(mp3), float(duration))


class DubbingTab(QWidget):
    busy_changed = Signal(bool)
    engine_reload_requested = Signal()
    reload_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._manager = None
        self._base = ""
        self._cues: List[CueJob] = []
        self._worker: Optional[DubbingWorker] = None
        self._join: Optional[JoinWorker] = None
        self._external_busy = False
        self._filling = False
        self._status_level = "muted"

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(8)
        root.addWidget(
            PageHeader(
                "Lồng tiếng theo phụ đề",
                "Nhập SRT/TXT, tạo audio cho từng cue, rồi ghép thành MP3 + SRT.",
            )
        )

        self.inner_tabs = QTabWidget()
        self.inner_tabs.setDocumentMode(True)
        root.addWidget(self.inner_tabs, 1)

        self.tts_page = TTSConfigTab()
        self.tts_page.busy_changed.connect(self.busy_changed.emit)
        self.tts_page.engine_reload_requested.connect(self.engine_reload_requested.emit)
        self.inner_tabs.addTab(self._build_cue_page(), "Bảng cue")
        self.inner_tabs.addTab(self.tts_page, "Engine && giọng")

    # ── construction ─────────────────────────────────────────────────────────
    def _build_cue_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.file_label = QLabel("File")
        self.file_combo = QComboBox()
        self.file_combo.setMinimumWidth(220)
        self.file_combo.currentIndexChanged.connect(self._on_base_changed)
        row1.addWidget(self.file_label)
        row1.addWidget(self.file_combo)

        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primaryButton")
        set_icon(self.start_btn, "play", "white", 14)
        self.start_btn.clicked.connect(self._start)
        self.pause_btn = QPushButton("Tạm dừng")
        set_icon(self.pause_btn, "pause", "warning", 13)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.stop_btn = QPushButton("Stop")
        set_icon(self.stop_btn, "stop", "danger", 13)
        self.stop_btn.clicked.connect(self._stop)
        self.import_btn = QPushButton("Import SRT")
        set_icon(self.import_btn, "file", "accent", 14)
        self.import_btn.clicked.connect(self._import_srt)
        self.import_dir_btn = QPushButton("Import Folder")
        set_icon(self.import_dir_btn, "folder", "accent", 14)
        self.import_dir_btn.clicked.connect(self._import_folder)
        self.open_btn = QPushButton("Open Output")
        set_icon(self.open_btn, "folder-open", "muted", 14)
        self.open_btn.clicked.connect(self._open_output)
        self.join_btn = QPushButton("Ghép MP3 && Xuất SRT")
        set_icon(self.join_btn, "music", "accent", 14)
        self.join_btn.clicked.connect(self._start_join)
        for button in (
            self.start_btn, self.pause_btn, self.stop_btn, self.import_btn,
            self.import_dir_btn, self.open_btn, self.join_btn,
        ):
            row1.addWidget(button)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.auto_split_chk = QCheckBox("Auto Split")
        self.auto_split_chk.setChecked(True)
        self.auto_split_chk.setToolTip("Tách cue dài hơn giới hạn ký tự khi import.")
        self.max_chars_spin = QSpinBox()
        self.max_chars_spin.setRange(80, 800)
        self.max_chars_spin.setSingleStep(10)
        self.max_chars_spin.setSuffix(" ký tự")
        self.max_chars_spin.setValue(350)
        self.merge_chk = QCheckBox("Gộp cue ngắn")
        self.merge_chk.setToolTip("Gộp cue ngắn vào cue kế khi khoảng hở rất nhỏ.")
        mode_label = QLabel("Chế độ ghép")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Giữ timing gốc", "timeline")
        self.mode_combo.addItem("Timing tự nhiên", "natural")
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0, 3.0)
        self.gap_spin.setSingleStep(0.05)
        self.gap_spin.setDecimals(2)
        self.gap_spin.setSuffix(" giây")
        self.gap_spin.setValue(0.15)
        self.stats_label = QLabel("0 cue")
        self.stats_label.setObjectName("MutedLabel")
        row2.addWidget(self.auto_split_chk)
        row2.addWidget(self.max_chars_spin)
        row2.addSpacing(8)
        row2.addWidget(self.merge_chk)
        row2.addSpacing(8)
        row2.addWidget(mode_label)
        row2.addWidget(self.mode_combo)
        row2.addWidget(QLabel("Gap"))
        row2.addWidget(self.gap_spin)
        row2.addStretch(1)
        row2.addWidget(self.stats_label)
        layout.addLayout(row2)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Id", "Timing", "Nội dung", "Giọng", "Trạng thái", "Output"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        bottom.addWidget(self.progress, 1)
        self.audio_preview = AudioPreview()
        bottom.addWidget(self.audio_preview, 1)
        layout.addLayout(bottom)

        self.status_label = QLabel("Sẵn sàng. Hãy chọn project và nhập phụ đề.")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._refresh_buttons()
        return page

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        self.tts_page.load_from(settings)
        dub = settings.get("dubbing") or {}
        self.auto_split_chk.setChecked(bool(dub.get("auto_split", True)))
        self.max_chars_spin.setValue(int(dub.get("max_chars", 350) or 350))
        self.merge_chk.setChecked(bool(dub.get("merge_short", False)))
        index = self.mode_combo.findData(str(dub.get("timing_mode") or "timeline"))
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self.gap_spin.setValue(float(dub.get("gap", 0.15) or 0.0))

    def apply_to(self, settings: Dict[str, Any]) -> None:
        self.tts_page.apply_to(settings)
        dub = settings.setdefault("dubbing", {})
        dub["timing_mode"] = self.mode_combo.currentData() or "timeline"
        dub["gap"] = round(float(self.gap_spin.value()), 2)
        dub["auto_split"] = self.auto_split_chk.isChecked()
        dub["max_chars"] = int(self.max_chars_spin.value())
        dub["merge_short"] = self.merge_chk.isChecked()
        self._settings = settings

    def apply_theme(self) -> None:
        self.tts_page.apply_theme()
        self.audio_preview.apply_theme()
        self._paint_rows()
        paint_status(self.status_label, self._status_level)

    # ── forwarding API used by the main window ───────────────────────────────
    def set_engine(self, engine) -> None:
        self.tts_page.set_engine(engine)

    def set_busy(self, busy: bool) -> None:
        self._external_busy = bool(busy)
        self.tts_page.set_busy(busy)
        self._refresh_buttons()

    def preview_running(self) -> bool:
        return (
            self._worker is not None
            or self._join is not None
            or self.tts_page.preview_running()
        )

    def cancel_preview(self) -> None:
        self.tts_page.cancel_preview()
        if self._worker is not None:
            self._worker.cancel()

    def stop_and_wait(self, timeout_ms: int = 15000) -> None:
        self.cancel_preview()
        for thread in (self._worker, self._join):
            if thread is not None:
                thread.wait(timeout_ms)

    # ── project / cues ───────────────────────────────────────────────────────
    def set_project(self, manager) -> None:
        if self._worker is not None or self._join is not None:
            return
        self._manager = manager
        self._base = ""
        self._refresh_file_list()
        if self.file_combo.count():
            self._base = str(self.file_combo.currentData() or "")
        self._reload_from_disk()

    def _refresh_file_list(self) -> None:
        bases: List[tuple[str, str]] = []
        if self._manager is not None:
            for item in self._manager.get_items():
                if getattr(item, "dub_mode", "text") == "srt":
                    bases.append((item.display_name or item.base, item.base))
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for label, base in bases:
            self.file_combo.addItem(label, base)
        if self._base:
            index = self.file_combo.findData(self._base)
            if index >= 0:
                self.file_combo.setCurrentIndex(index)
        self.file_combo.blockSignals(False)
        self.file_label.setVisible(bool(bases))
        self.file_combo.setVisible(bool(bases))

    def _on_base_changed(self, _index: int) -> None:
        if self._worker is not None or self._join is not None:
            return
        self._base = str(self.file_combo.currentData() or "")
        self._reload_from_disk()

    def _reload_from_disk(self) -> None:
        self._cues = load_cues(self._manager, self._base) if self._manager and self._base else []
        self._fill_table()
        self._refresh_stats()
        self._refresh_buttons()

    def _save_cues(self) -> None:
        if self._manager is not None and self._base:
            save_cues(self._manager, self._base, self._cues)

    # ── table ────────────────────────────────────────────────────────────────
    def _fill_table(self) -> None:
        self._filling = True
        self.table.clearContents()
        self.table.setRowCount(len(self._cues))
        voices = self.tts_page.voice_names()
        for row, cue in enumerate(self._cues):
            self.table.setItem(row, 0, self._readonly(str(cue.id)))
            self.table.setItem(row, 1, self._readonly(
                f"{format_timestamp(cue.start)} → {format_timestamp(cue.end)}"))
            content = QTableWidgetItem(cue.text)
            content.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, content)
            combo = QComboBox()
            combo.setMinimumWidth(170)
            combo.addItem("— Mặc định —", "")
            for name in voices:
                combo.addItem(name, name)
            index = combo.findData(cue.voice)
            combo.setCurrentIndex(max(0, index))
            combo.currentIndexChanged.connect(
                lambda _i, c=cue, box=combo: self._set_cue_voice(c, box.currentData() or ""))
            self.table.setCellWidget(row, 3, combo)
            self.table.setItem(row, 4, self._readonly(""))
            self.table.setItem(row, 5, self._readonly(self._output_name(cue)))
            self._paint_row(row)
        self._filling = False

    @staticmethod
    def _readonly(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def _paint_rows(self) -> None:
        self._filling = True
        for row in range(self.table.rowCount()):
            self._paint_row(row)
        self._filling = False

    def _paint_row(self, row: int) -> None:
        if row >= len(self._cues):
            return
        cue = self._cues[row]
        status = self.table.item(row, 4)
        if status is not None:
            status.setText(_STATUS_TEXT.get(cue.status, cue.status))
            status.setForeground(QColor(_status_color(cue.status)))
        output = self.table.item(row, 5)
        if output is not None:
            output.setText(self._output_name(cue))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 2:
            return
        row = item.row()
        if row >= len(self._cues):
            return
        cue = self._cues[row]
        text = item.text().strip()
        if text == cue.text:
            return
        cue.text = text
        cue.reset()
        self._save_cues()
        self._paint_row(row)
        self._refresh_stats()

    def _set_cue_voice(self, cue: CueJob, voice: str) -> None:
        if self._worker is not None or cue.voice == voice:
            return
        cue.voice = voice
        cue.reset()
        self._save_cues()
        try:
            self._paint_row(self._cues.index(cue))
        except ValueError:
            pass

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column == 2 or row >= len(self._cues):
            return
        self._play_cue(self._cues[row])

    def _play_cue(self, cue: CueJob) -> None:
        path = self._cue_audio(cue)
        if path is not None and path.is_file():
            self.audio_preview.set_file(str(path))
            self.audio_preview.play()

    def _cue_audio(self, cue: CueJob) -> Optional[Path]:
        if self._manager is None or not self._base:
            return None
        if str(cue.out or "").strip():
            path = Path(cue.out)
            if not path.is_absolute():
                path = Path(self._manager.project_dir) / path
            return path
        return Path(self._manager.audio_dir) / "cues" / self._base / f"{int(cue.id):04d}.mp3"

    @staticmethod
    def _output_name(cue: CueJob) -> str:
        raw = str(cue.out or "").strip()
        return Path(raw).name if raw else ""

    # ── context menu ─────────────────────────────────────────────────────────
    def _show_menu(self, position) -> None:
        if self._worker is not None or self._join is not None:
            return
        row = self.table.rowAt(position.y())
        if row < 0 or row >= len(self._cues):
            return
        cue = self._cues[row]
        menu = QMenu(self)
        play = menu.addAction("Phát cue")
        rerun = menu.addAction("Chạy lại cue này")
        assign = menu.addMenu("Gán giọng cho cue đang chọn")
        assign_all = menu.addMenu("Gán giọng cho tất cả")
        voices = self.tts_page.voice_names()
        default_action = assign.addAction("— Mặc định —")
        default_action.triggered.connect(lambda: self._assign_voice(cue, ""))
        all_default = assign_all.addAction("— Mặc định —")
        all_default.triggered.connect(lambda: self._assign_voice_all(""))
        for name in voices:
            action = assign.addAction(name)
            action.triggered.connect(
                lambda _checked=False, n=name, c=cue: self._assign_voice(c, n))
            all_action = assign_all.addAction(name)
            all_action.triggered.connect(
                lambda _checked=False, n=name: self._assign_voice_all(n))
        menu.addSeparator()
        remove = menu.addAction("Xoá cue")
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if chosen is play:
            self._play_cue(cue)
        elif chosen is rerun:
            self._rerun_cue(row)
        elif chosen is remove:
            self._delete_cue(row)

    def _assign_voice(self, cue: CueJob, name: str) -> None:
        self._set_cue_voice(cue, name)
        try:
            row = self._cues.index(cue)
        except ValueError:
            return
        combo = self.table.cellWidget(row, 3)
        if isinstance(combo, QComboBox):
            combo.blockSignals(True)
            index = combo.findData(name)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    def _assign_voice_all(self, name: str) -> None:
        changed = False
        for cue in self._cues:
            if cue.voice != name:
                cue.voice = name
                cue.reset()
                changed = True
        if not changed:
            return
        self._save_cues()
        self._fill_table()
        self.status_label.setText(f"Đã gán giọng cho {len(self._cues)} cue.")

    def _rerun_cue(self, row: int) -> None:
        cue = self._cues[row]
        cue.reset()
        self._save_cues()
        self._paint_row(row)
        self._start()

    def _delete_cue(self, row: int) -> None:
        if row >= len(self._cues):
            return
        del self._cues[row]
        dubbing.reindex(self._cues)
        self._save_cues()
        self._fill_table()
        self._refresh_stats()

    # ── stats ────────────────────────────────────────────────────────────────
    def _refresh_stats(self) -> None:
        done = sum(1 for cue in self._cues if cue.status == STATUS_DONE)
        failed = sum(1 for cue in self._cues if cue.status == STATUS_ERROR)
        span = max((cue.end for cue in self._cues), default=0.0)
        self.stats_label.setText(
            f"{len(self._cues)} cue · Done {done} · Lỗi {failed} · {format_duration(span)}")

    def _set_status(self, message: str, level: str = "muted") -> None:
        self._status_level = level
        paint_status(self.status_label, level)
        self.status_label.setText(message)

    # ── run / stop / join ────────────────────────────────────────────────────
    def _refresh_buttons(self) -> None:
        busy = (
            self._worker is not None
            or self._join is not None
            or self._external_busy
        )
        has_cues = bool(self._cues) and bool(self._base)
        self.start_btn.setEnabled(not busy and has_cues)
        self.pause_btn.setEnabled(self._worker is not None)
        self.stop_btn.setEnabled(self._worker is not None)
        self.import_btn.setEnabled(not busy and self._manager is not None)
        self.import_dir_btn.setEnabled(not busy and self._manager is not None)
        self.open_btn.setEnabled(self._manager is not None)
        self.join_btn.setEnabled(not busy and has_cues)
        self.file_combo.setEnabled(not busy)
        self.table.setEnabled(not busy)

    def _start(self) -> None:
        if self._worker is not None or self._join is not None:
            return
        if self._manager is None or not self._base:
            self._set_status("Hãy chọn project và import phụ đề trước.", "warning")
            return
        if not self._cues:
            self._set_status("Không có cue nào để lồng tiếng.", "warning")
            return
        if self._external_busy:
            self._set_status("Pipeline đang chạy, hãy dừng hoặc chờ xong.", "warning")
            return
        snapshot = copy.deepcopy(self._settings)
        self.apply_to(snapshot)
        self._worker = DubbingWorker(snapshot, self._manager, self._base, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.cue_updated.connect(self._on_cue_updated)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._on_worker_finished)
        self.pause_btn.setText("Tạm dừng")
        self.progress.setValue(0)
        self._set_status("Đang lồng tiếng…", "running")
        self._refresh_buttons()
        self.busy_changed.emit(True)
        self._worker.start()

    def _toggle_pause(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if worker.pause_running():
            worker.resume()
            self.pause_btn.setText("Tạm dừng")
            self._set_status("Tiếp tục lồng tiếng…", "running")
        else:
            worker.pause()
            self.pause_btn.setText("Tiếp tục")
            self._set_status("Đã tạm dừng sau cue hiện tại.", "warning")

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.stop_btn.setEnabled(False)
            self._set_status("Đang dừng sau cue hiện tại…", "warning")

    def _on_progress(self, percent: int, message: str) -> None:
        if percent >= 0:
            self.progress.setValue(max(0, min(100, int(percent))))
        if message:
            self._set_status(message, "running" if percent >= 0 and percent < 100 else "muted")

    def _on_cue_updated(self, _cue_id: int, status: str) -> None:
        for row, cue in enumerate(self._cues):
            if cue.id == _cue_id:
                cue.status = status
                self._filling = True
                self._paint_row(row)
                self._filling = False
                break
        self._refresh_stats()

    def _on_done(self, ok: bool, message: str) -> None:
        self._set_status(message, "success" if ok else ("warning" if "huỷ" in message.lower() else "error"))
        self.progress.setValue(100 if ok else self.progress.value())

    def _on_worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._reload_from_disk()
        self._refresh_buttons()
        self.busy_changed.emit(False)
        self.reload_requested.emit()

    def _start_join(self) -> None:
        if self._join is not None or self._worker is not None:
            return
        if self._manager is None or not self._base:
            self._set_status("Hãy chọn project và import phụ đề trước.", "warning")
            return
        if self._external_busy:
            self._set_status("Pipeline đang chạy, hãy dừng hoặc chờ xong.", "warning")
            return
        mode = str(self.mode_combo.currentData() or "timeline")
        gap = float(self.gap_spin.value())
        snapshot = copy.deepcopy(self._settings)
        self.apply_to(snapshot)
        self._join = JoinWorker(snapshot, self._manager, self._base, mode, gap, self)
        self._join.done.connect(self._on_join_done)
        self._join.finished.connect(self._on_join_finished)
        self._set_status("Đang ghép MP3 và xuất SRT…", "running")
        self._refresh_buttons()
        self.busy_changed.emit(True)
        self._join.start()

    def _on_join_done(self, ok: bool, message: str, mp3: str, duration: float) -> None:
        if not ok:
            self._set_status(message, "error")
            return
        self._set_status(message, "success")
        if mp3:
            self.audio_preview.set_file(mp3)
        if self._manager is not None and self._base:
            self._manager.scan()
            item = self._manager.get_item(self._base)
            if item is not None and mp3:
                try:
                    mtime = Path(mp3).stat().st_mtime
                except OSError:
                    mtime = 0.0
                self._manager.update_item(
                    self._base,
                    state=ITEM_TTS_DONE,
                    error="",
                    duration=float(duration),
                    audio_mtime=mtime,
                )
        self.reload_requested.emit()

    def _on_join_finished(self) -> None:
        worker = self._join
        self._join = None
        if worker is not None:
            worker.deleteLater()
        self._reload_from_disk()
        self._refresh_buttons()
        self.busy_changed.emit(False)

    # ── importing ────────────────────────────────────────────────────────────
    def _browse_dir(self) -> Path:
        if self._manager is not None:
            return Path(self._manager.input_dir)
        return Path.home()

    def _import_srt(self) -> None:
        if self._manager is None:
            self._set_status("Hãy chọn project trước.", "warning")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn phụ đề", str(self._browse_dir()),
            "Phụ đề (*.srt *.vtt);;Tất cả (*)")
        if not path:
            return
        self._import_files([Path(path)])

    def _import_folder(self) -> None:
        if self._manager is None:
            self._set_status("Hãy chọn project trước.", "warning")
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục phụ đề/văn bản", str(self._browse_dir()))
        if not folder:
            return
        directory = Path(folder)
        subtitles: List[Path] = []
        stems = set()
        for pattern in ("*.srt", "*.vtt"):
            for path in sorted(directory.glob(pattern)):
                subtitles.append(path)
                stems.add(path.stem.casefold())
        texts = [
            path for path in sorted(directory.glob("*.txt"))
            if path.stem.casefold() not in stems
        ]
        if not subtitles and not texts:
            self._set_status("Thư mục không có file .srt/.vtt/.txt nào.", "warning")
            return
        self._import_files(subtitles + texts)

    def _import_files(self, paths: List[Path]) -> None:
        if self._manager is None:
            return
        imported: List[str] = []
        errors: List[str] = []
        for path in paths:
            try:
                if path.suffix.lower() in (".srt", ".vtt"):
                    base = dubbing.import_srt(self._manager, path)
                else:
                    base = dubbing.import_txt(
                        self._manager, path, int(self.max_chars_spin.value()))
                self._apply_import_options(base)
                imported.append(base)
            except DubbingError as exc:
                errors.append(f"{path.name}: {exc}")
                logger.error("Import %s lỗi: %s", path.name, exc)
        if imported:
            self._base = imported[-1]
            self._manager.scan()
            self._refresh_file_list()
            self._reload_from_disk()
            self._set_status(
                f"Đã import {len(imported)} file, {len(self._cues)} cue.", "success")
            self.reload_requested.emit()
        if errors:
            QMessageBox.warning(self, "Import", "\n".join(errors[:10]))

    def _apply_import_options(self, base: str) -> None:
        if self._manager is None:
            return
        cues = load_cues(self._manager, base)
        if not cues:
            return
        cues = dubbing.apply_import_options(
            cues,
            auto_split=self.auto_split_chk.isChecked(),
            max_chars=int(self.max_chars_spin.value()),
            merge_short=self.merge_chk.isChecked(),
        )
        save_cues(self._manager, base, cues)

    # ── output ───────────────────────────────────────────────────────────────
    def _open_output(self) -> None:
        if self._manager is None:
            return
        folder = Path(self._manager.audio_dir)
        if self._base:
            item = self._manager.get_item(self._base)
            if item is not None:
                folder = Path(item.audio_path).parent
        folder.mkdir(parents=True, exist_ok=True)
        open_in_explorer(folder)
