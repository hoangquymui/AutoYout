"""Project tab: folders, scan results and per-file pipeline status."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.languages import language_choices, language_name, normalize_language
from core.project_manager import (
    ITEM_ERROR,
    ProjectManager,
    VIDEO_UPLOADING,
)
from ui import theme
from ui.components.audio_player import AudioPreview
from ui.components.icons import set_icon, set_pixmap
from ui.components.page import PageHeader, icon_button, section_label, wrap_scroll
from utils.media_probe import probe_duration

_COLUMNS = ["#", "File", "Ngôn ngữ", "Tiêu đề", "Thời lượng", "Trạng thái", "Video", "YouTube", "Ghi chú"]
_IMAGE_FILTER = "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả (*)"
_AUDIO_FILTER = "Âm thanh (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;Tất cả (*)"


class ProjectTab(QWidget):
    reload_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.manager: Optional[ProjectManager] = None
        self._video_path: Optional[Path] = None

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(wrap_scroll(content))

        root = QVBoxLayout(content)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(
            PageHeader(
                "Dự án & Dữ liệu",
                "Chọn project, cấu hình thư mục và theo dõi trạng thái từng file.",
            )
        )

        folders = QGroupBox("Thư mục dự án")
        folder_layout = QVBoxLayout(folders)
        folder_layout.setSpacing(8)

        project_row = QHBoxLayout()
        project_row.setSpacing(8)
        project_icon = QLabel()
        set_pixmap(project_icon, "folder", "accent", 17)
        project_icon.setFixedSize(20, 20)
        project_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        project_row.addWidget(project_icon)
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Chọn thư mục Project gốc…")
        self.project_edit.setReadOnly(True)
        self.project_edit.setToolTip("Thư mục gốc chứa input / audio / video / assets.")
        project_row.addWidget(self.project_edit, 1)
        browse_project = QPushButton("Chọn Project")
        set_icon(browse_project, "folder-open", "accent", 15)
        browse_project.clicked.connect(self._browse_project)
        project_row.addWidget(browse_project)
        create_project = QPushButton("Tạo Project mới")
        set_icon(create_project, "plus", "accent", 14)
        create_project.clicked.connect(self._create_project)
        project_row.addWidget(create_project)
        open_project = icon_button(
            "external-link", "Mở thư mục project trong Explorer", size=15, side=30
        )
        open_project.clicked.connect(self._open_project_folder)
        project_row.addWidget(open_project)
        folder_layout.addLayout(project_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.input_edit = self._folder_field(grid, 0, 0, "INPUT (.TXT / .JSON)", "input")
        self.audio_edit = self._folder_field(grid, 0, 1, "AUDIO OUTPUT", "audio")
        self.video_edit = self._folder_field(grid, 0, 2, "VIDEO OUTPUT", "video")
        self.assets_edit = self._folder_field(grid, 0, 3, "ASSETS", "assets")
        self.script_edit = self._folder_field(grid, 1, 0, "SCRIPT", "script")
        self.thumbs_edit = self._folder_field(grid, 1, 1, "THUMBNAIL", "thumbs")
        self.output_edit = self._folder_field(grid, 1, 2, "OUTPUT", "output")
        for column in range(4):
            grid.setColumnStretch(column, 1)
        folder_layout.addLayout(grid)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.force_check = QCheckBox("Force Overwrite / Tạo lại từ đầu")
        self.force_check.setToolTip("Xoá kết quả cũ và tạo lại audio/video trong lần chạy tới.")
        actions.addWidget(self.force_check)
        actions.addStretch(1)
        reload_button = QPushButton("Áp dụng && quét lại")
        reload_button.setObjectName("primaryButton")
        set_icon(reload_button, "refresh", "white", 14)
        reload_button.clicked.connect(self.reload_requested.emit)
        actions.addWidget(reload_button)
        folder_layout.addLayout(actions)

        root.addWidget(folders)

        structure_box = QGroupBox("Cấu trúc thư mục")
        structure_layout = QVBoxLayout(structure_box)
        structure_layout.setSpacing(6)
        self._structure_grid = QGridLayout()
        self._structure_grid.setHorizontalSpacing(10)
        self._structure_grid.setVerticalSpacing(4)
        structure_layout.addLayout(self._structure_grid)
        self._structure_empty = QLabel("Chọn project để kiểm tra cấu trúc.")
        self._structure_empty.setObjectName("MutedLabel")
        structure_layout.addWidget(self._structure_empty)
        structure_actions = QHBoxLayout()
        structure_actions.setSpacing(8)
        self.structure_status = QLabel("")
        self.structure_status.setObjectName("MutedLabel")
        self.structure_status.setWordWrap(True)
        structure_actions.addWidget(self.structure_status, 1)
        self.btn_make_folders = QPushButton("Tạo thư mục thiếu")
        set_icon(self.btn_make_folders, "folder", "accent", 14)
        self.btn_make_folders.clicked.connect(self._ensure_folders)
        self.btn_make_folders.setEnabled(False)
        structure_actions.addWidget(self.btn_make_folders)
        structure_layout.addLayout(structure_actions)
        root.addWidget(structure_box)

        list_head = QHBoxLayout()
        list_head.setSpacing(8)
        list_head.addWidget(section_label("DANH SÁCH FILE"))
        self.summary_label = QLabel("Chưa chọn project.")
        self.summary_label.setObjectName("SummaryLabel")
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.summary_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        list_head.addWidget(self.summary_label, 1)
        refresh_button = icon_button(
            "refresh", "Quét lại thư mục đầu vào", role="muted", size=14, side=28
        )
        refresh_button.clicked.connect(self.reload_requested.emit)
        list_head.addWidget(refresh_button)
        root.addLayout(list_head)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(120)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in range(4, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(self._on_row_changed)
        root.addWidget(self.table, 1)

        takes_box = QGroupBox("Bản thử (Take) — chọn bản tốt nhất cho file đang chọn")
        takes_layout = QGridLayout(takes_box)
        takes_layout.setHorizontalSpacing(8)
        takes_layout.setVerticalSpacing(6)

        takes_layout.addWidget(self._caption("Ảnh"), 0, 0)
        self.image_take_combo = QComboBox()
        self.image_take_combo.setToolTip(
            "Bản ảnh được dùng khi chế độ hình nền là 'Ảnh AI theo chương'."
        )
        self.image_take_combo.currentIndexChanged.connect(self._on_image_take_changed)
        takes_layout.addWidget(self.image_take_combo, 0, 1)
        add_image = QPushButton("Thêm ảnh")
        set_icon(add_image, "plus", "accent", 13)
        add_image.clicked.connect(self._add_image_take)
        takes_layout.addWidget(add_image, 0, 2)
        remove_image = QPushButton("Xoá")
        set_icon(remove_image, "trash", "muted", 12)
        remove_image.clicked.connect(self._remove_image_take)
        takes_layout.addWidget(remove_image, 0, 3)

        takes_layout.addWidget(self._caption("Giọng"), 1, 0)
        self.voice_take_combo = QComboBox()
        self.voice_take_combo.currentIndexChanged.connect(self._on_voice_take_changed)
        takes_layout.addWidget(self.voice_take_combo, 1, 1)
        add_voice = QPushButton("Thêm audio")
        set_icon(add_voice, "plus", "accent", 13)
        add_voice.clicked.connect(self._add_voice_take)
        takes_layout.addWidget(add_voice, 1, 2)
        remove_voice = QPushButton("Xoá")
        set_icon(remove_voice, "trash", "muted", 12)
        remove_voice.clicked.connect(self._remove_voice_take)
        takes_layout.addWidget(remove_voice, 1, 3)

        takes_layout.addWidget(self._caption("Ngôn ngữ"), 2, 0)
        self.language_combo = QComboBox()
        for code, label in language_choices():
            self.language_combo.addItem(label, code)
        self.language_combo.setToolTip(
            "Ngôn ngữ của file này: vi dùng H2MTTS, ngôn ngữ khác dùng edge-tts."
        )
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        takes_layout.addWidget(self.language_combo, 2, 1, 1, 3)
        takes_layout.setColumnStretch(1, 1)
        root.addWidget(takes_box)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(8)
        self.audio_preview = AudioPreview()
        preview_row.addWidget(self.audio_preview, 1)
        self.open_video_button = QPushButton("Mở video")
        set_icon(self.open_video_button, "external-link", "muted", 14)
        self.open_video_button.setEnabled(False)
        self.open_video_button.clicked.connect(self._open_video)
        preview_row.addWidget(self.open_video_button)
        root.addLayout(preview_row)

    # ── construction helpers ─────────────────────────────────────────────────
    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return label

    def _folder_field(
        self, grid: QGridLayout, row: int, column: int, label: str, key: str
    ) -> QLineEdit:
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(3)

        caption = QLabel(label)
        caption.setObjectName("ProjectCaption")
        cell_layout.addWidget(caption)

        edit_row = QHBoxLayout()
        edit_row.setSpacing(6)
        edit = QLineEdit()
        edit.setToolTip(
            "Để trống hoặc dùng đường dẫn tương đối để tự tạo bên trong Project "
            f"(ví dụ: {key})."
        )
        edit_row.addWidget(edit, 1)
        browse = icon_button(
            "folder", f"Chọn thư mục {label.lower()}…", role="muted", size=15, side=30
        )
        browse.clicked.connect(lambda _=False, e=edit: self._browse_folder(e))
        reset = icon_button(
            "rotate-ccw",
            "Trả về thư mục mặc định trong Project",
            role="muted",
            size=14,
            side=30,
        )
        reset.clicked.connect(lambda _=False, e=edit, k=key: e.setText(k))
        edit_row.addWidget(browse)
        edit_row.addWidget(reset)
        cell_layout.addLayout(edit_row)

        grid.addWidget(cell, row, column)
        return edit

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        project = settings.get("project") or {}
        self.project_edit.setText(str(project.get("last_project") or ""))
        self.input_edit.setText(str(project.get("input_dir") or "input"))
        self.audio_edit.setText(str(project.get("audio_dir") or "audio"))
        self.video_edit.setText(str(project.get("video_dir") or "video"))
        self.assets_edit.setText(str(project.get("assets_dir") or "assets"))
        self.script_edit.setText(str(project.get("script_dir") or "script"))
        self.thumbs_edit.setText(str(project.get("thumbs_dir") or "thumbs"))
        self.output_edit.setText(str(project.get("output_dir") or "output"))
        self.force_check.setChecked(bool(project.get("force_overwrite", False)))

    def apply_to(self, settings: Dict[str, Any]) -> None:
        project = settings.setdefault("project", {})
        project["last_project"] = self.project_edit.text().strip()
        project["input_dir"] = self.input_edit.text().strip() or "input"
        project["audio_dir"] = self.audio_edit.text().strip() or "audio"
        project["video_dir"] = self.video_edit.text().strip() or "video"
        project["assets_dir"] = self.assets_edit.text().strip() or "assets"
        project["script_dir"] = self.script_edit.text().strip() or "script"
        project["thumbs_dir"] = self.thumbs_edit.text().strip() or "thumbs"
        project["output_dir"] = self.output_edit.text().strip() or "output"
        project["force_overwrite"] = self.force_check.isChecked()

    def current_project(self) -> str:
        return self.project_edit.text().strip()

    def set_project(self, path: str) -> None:
        self.project_edit.setText(path)

    def force_overwrite(self) -> bool:
        return self.force_check.isChecked()

    # ── folder pickers ───────────────────────────────────────────────────────
    def _browse_project(self) -> None:
        start = self.project_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục Project", start)
        if chosen:
            self.project_edit.setText(chosen)
            self.reload_requested.emit()

    def _browse_folder(self, edit: QLineEdit) -> None:
        start = edit.text().strip()
        path = Path(start)
        if not path.is_absolute():
            base = self.project_edit.text().strip()
            path = Path(base) / start if base else Path.home()
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục", str(path))
        if chosen:
            edit.setText(chosen)
            self.reload_requested.emit()

    def _open_project_folder(self) -> None:
        path = self.project_edit.text().strip()
        if path and Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ── data ─────────────────────────────────────────────────────────────────
    def apply_theme(self) -> None:
        self.audio_preview.apply_theme()
        if self.manager is not None:
            self.refresh(reset_selection=False)
        else:
            self.summary_label.setText("Chưa chọn project.")

    def set_manager(self, manager: Optional[ProjectManager]) -> None:
        self.manager = manager
        self.refresh(reset_selection=True)
        self.refresh_structure()

    # ── project skeleton ─────────────────────────────────────────────────────
    def _create_project(self) -> None:
        parent = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục cha cho project mới",
            self.project_edit.text().strip() or str(Path.home()),
        )
        if not parent:
            return
        name, ok = QInputDialog.getText(self, "Tạo Project mới", "Tên project:")
        if not ok or not str(name).strip():
            return
        try:
            target = ProjectManager.create_project(parent, str(name))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Tạo Project", str(exc))
            return
        self.project_edit.setText(str(target))
        self.reload_requested.emit()

    def refresh_structure(self) -> None:
        while self._structure_grid.count():
            item = self._structure_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.btn_make_folders.setEnabled(self.manager is not None)
        if self.manager is None:
            self._structure_empty.setVisible(True)
            self.structure_status.setText("")
            return
        self._structure_empty.setVisible(False)
        for row, entry in enumerate(self.manager.folder_status()):
            exists = bool(entry["exists"])
            mark = QLabel("✓" if exists else "✗")
            mark.setFixedWidth(18)
            mark.setStyleSheet(
                f"color:{theme.SUCCESS if exists else theme.DANGER}; font-weight:600;"
            )
            caption = QLabel(entry["label"])
            caption.setMinimumWidth(110)
            path_label = QLabel(entry["path"])
            path_label.setObjectName("MutedLabel")
            path_label.setToolTip(entry["path"])
            path_label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            opener = icon_button(
                "external-link", f"Mở {entry['label']}", role="muted", size=13, side=26
            )
            opener.clicked.connect(
                lambda _=False, p=entry["path"]: self._open_path(p)
            )
            self._structure_grid.addWidget(mark, row, 0)
            self._structure_grid.addWidget(caption, row, 1)
            self._structure_grid.addWidget(path_label, row, 2)
            self._structure_grid.addWidget(opener, row, 3)
        self._structure_grid.setColumnStretch(2, 1)
        missing = [entry["label"] for entry in self.manager.folder_status()
                   if not entry["exists"]]
        self.structure_status.setText(
            "Đủ thư mục." if not missing else f"Thiếu: {', '.join(missing)}"
        )

    def _ensure_folders(self) -> None:
        if self.manager is None:
            return
        created = self.manager.ensure_structure()
        if created:
            labels = dict(ProjectManager.FOLDER_LABELS)
            self.structure_status.setText(
                "Đã tạo: " + ", ".join(labels.get(key, key) for key in created)
            )
        else:
            self.structure_status.setText("Không thiếu thư mục nào.")
        self.refresh_structure()

    @staticmethod
    def _open_path(path: str) -> None:
        if path and Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def refresh(self, reset_selection: bool = False) -> None:
        previous = self._selected_base() if not reset_selection else None
        self.table.clearSpans()
        self.audio_preview.set_file(None)
        self._video_path = None
        self.open_video_button.setEnabled(False)
        self._clear_takes()
        if self.manager is None:
            self.table.setRowCount(0)
            self.summary_label.setText("Chưa chọn project.")
            return

        items = self.manager.get_items()
        self.table.setRowCount(len(items))
        selected_row = -1
        for row, item in enumerate(items):
            job = self.manager.video_for_item(item.base)
            values = [
                str(row + 1),
                item.text_file.name if item.text_file else item.base,
                str(item.language or "vi").upper(),
                " ".join(str(item.metadata.get("title") or item.base).split()),
                item.duration_label if self.manager.audio_exists(item) else "—",
                item.state_label,
                f"{job.path.name} · {job.state_label}" if job else "—",
                self._youtube_text(job),
                item.error or (job.error if job else "") or item.metadata_error or "",
            ]
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text or "")
                cell.setToolTip(text or "")
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.base)
                    cell.setForeground(QColor(theme.TEXT_DIM))
                if column == 2:
                    cell.setToolTip(language_name(item.language))
                    cell.setForeground(QColor(theme.ACCENT_SOFT))
                if column == 5:
                    cell.setForeground(QColor(theme.STATE_COLORS.get(item.state, theme.TEXT_MUTED)))
                if column == 6 and job is not None:
                    cell.setForeground(
                        QColor(theme.VIDEO_STATE_COLORS.get(job.state, theme.TEXT_MUTED))
                    )
                if column == 7:
                    cell.setForeground(
                        QColor(theme.SUCCESS if job and job.youtube_video_id else theme.TEXT_DIM)
                    )
                    if job and job.youtube_url:
                        cell.setToolTip(job.youtube_url)
                if column == 8 and text:
                    cell.setForeground(QColor(theme.DANGER))
                self.table.setItem(row, column, cell)
            if item.base == previous:
                selected_row = row

        if items:
            if selected_row < 0:
                selected_row = 0
            self.table.selectRow(selected_row)
            self._on_row_changed(selected_row, 0, -1, -1)
        self._update_summary()

    def _youtube_text(self, job) -> str:
        if job is None:
            return "—"
        if job.youtube_video_id:
            return "Đã đăng" + (" (hẹn giờ)" if job.scheduled_at else "")
        if job.state == VIDEO_UPLOADING:
            return "Đang đăng…"
        if job.scheduled_at:
            return "Đã hẹn giờ"
        return "—"

    def _update_summary(self) -> None:
        if self.manager is None:
            return
        items = self.manager.get_items()
        videos = list(self.manager.videos.values())
        audio_done = sum(1 for item in items if self.manager.audio_exists(item))
        video_done = sum(1 for job in videos if job.path.is_file())
        uploaded = sum(1 for job in videos if job.youtube_video_id)
        errors = sum(1 for item in items if item.state == ITEM_ERROR) + sum(
            1 for job in videos if job.state == ITEM_ERROR
        )
        self.summary_label.setText(
            f"<span style='color:{theme.TEXT}'><b>{len(items)}</b> file đầu vào</span>"
            f" &nbsp;·&nbsp; <span style='color:{theme.SUCCESS}'>Audio {audio_done}/{len(items)}</span>"
            f" &nbsp;·&nbsp; <span style='color:{theme.SUCCESS}'>Video {video_done}/{len(videos)}</span>"
            f" &nbsp;·&nbsp; <span style='color:{theme.BLUE}'>Đã đăng {uploaded}</span>"
            f" &nbsp;·&nbsp; <span style='color:{theme.DANGER}'>Lỗi {errors}</span>"
        )

    def _selected_base(self) -> Optional[str]:
        row = self.table.currentRow()
        if row < 0:
            return None
        cell = self.table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def _on_row_changed(self, row: int, _column: int, _prev_row: int, _prev_col: int) -> None:
        if self.manager is None or row < 0:
            return
        cell = self.table.item(row, 0)
        base = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        if not base:
            return
        item = self.manager.get_item(base)
        if item is None:
            return
        self.audio_preview.set_file(item.audio_path if self.manager.audio_exists(item) else None)
        job = self.manager.video_for_item(base)
        self._video_path = job.path if job and job.path.is_file() else None
        self.open_video_button.setEnabled(self._video_path is not None)
        self._refresh_takes(item)

    def _open_video(self) -> None:
        if self._video_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._video_path)))

    # ── takes ────────────────────────────────────────────────────────────────
    def _clear_takes(self) -> None:
        for combo in (self.image_take_combo, self.voice_take_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Chưa chọn file", -1)
            combo.blockSignals(False)
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(0)
        self.language_combo.blockSignals(False)

    def _refresh_takes(self, item) -> None:
        self._fill_take_combo(self.image_take_combo, item.image_takes,
                              item.selected_image_index)
        self._fill_take_combo(self.voice_take_combo, item.voice_takes,
                              item.selected_voice_index)
        index = self.language_combo.findData(
            normalize_language(getattr(item, "language", "vi")))
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(max(0, index))
        self.language_combo.blockSignals(False)

    @staticmethod
    def _fill_take_combo(combo: QComboBox, takes: list, selected: int) -> None:
        combo.blockSignals(True)
        combo.clear()
        for index, path in enumerate(takes):
            name = Path(str(path)).name or str(path)
            combo.addItem(f"{index + 1}. {name}", index)
        if not takes:
            combo.addItem("Chưa có bản nào", -1)
        combo.setCurrentIndex(max(0, int(selected)) if takes else 0)
        combo.blockSignals(False)

    def _on_image_take_changed(self, index: int) -> None:
        data = self.image_take_combo.itemData(index)
        if data is None or data < 0:
            return
        base = self._selected_base()
        if not base or self.manager is None:
            return
        self.manager.select_take(base, "image", int(data))
        self.refresh()

    def _on_voice_take_changed(self, index: int) -> None:
        data = self.voice_take_combo.itemData(index)
        if data is None or data < 0:
            return
        base = self._selected_base()
        if not base or self.manager is None:
            return
        self.manager.select_take(base, "voice", int(data))
        item = self.manager.get_item(base)
        if item is not None and self.manager.audio_exists(item):
            try:
                item.duration = probe_duration(item.audio_path)
            except Exception:
                pass
            self.manager.update_item(base, duration=item.duration)
        self.refresh()

    def _on_language_changed(self, _index: int) -> None:
        base = self._selected_base()
        if not base or self.manager is None:
            return
        code = self.language_combo.currentData()
        if not code:
            return
        item = self.manager.get_item(base)
        if item is not None and item.language == code:
            return
        if self.manager.set_item_language(base, str(code)) is not None:
            self.refresh()

    def _add_image_take(self) -> None:
        base = self._selected_base()
        if not base or self.manager is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn ảnh bản thử", str(Path.home()), _IMAGE_FILTER
        )
        for path in paths:
            self.manager.add_take(base, "image", path)
        self.refresh()

    def _remove_image_take(self) -> None:
        base = self._selected_base()
        if not base or self.manager is None:
            return
        data = self.image_take_combo.currentData()
        if data is None or data < 0:
            return
        self.manager.remove_take(base, "image", int(data))
        self.refresh()

    def _add_voice_take(self) -> None:
        base = self._selected_base()
        if not base or self.manager is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn audio bản thử", str(Path.home()), _AUDIO_FILTER
        )
        for path in paths:
            self.manager.add_take(base, "voice", path)
        self.refresh()

    def _remove_voice_take(self) -> None:
        base = self._selected_base()
        if not base or self.manager is None:
            return
        data = self.voice_take_combo.currentData()
        if data is None or data < 0:
            return
        self.manager.remove_take(base, "voice", int(data))
        self.refresh()
