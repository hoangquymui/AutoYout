"""Project tab: pick a project folder and browse it as a file tree."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QDir, QModelIndex, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFileSystemModel,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.project_manager import ProjectManager
from ui.components.icons import set_icon, set_pixmap
from ui.components.page import PageHeader, icon_button

_NEW_FILE_FILTER = "Văn bản (*.txt);;Tất cả (*)"


class ProjectTab(QWidget):
    reload_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.manager: Optional[ProjectManager] = None

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        page.addWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(
            PageHeader(
                "Dự án",
                "Chọn thư mục project và quản lý file như một trình duyệt file.",
            )
        )

        # ── project picker ────────────────────────────────────────────────────
        project_box = QGroupBox("Project")
        project_layout = QHBoxLayout(project_box)
        project_layout.setSpacing(8)

        project_icon = QLabel()
        set_pixmap(project_icon, "folder", "accent", 17)
        project_icon.setFixedSize(20, 20)
        project_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        project_layout.addWidget(project_icon)

        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("Chọn thư mục Project gốc…")
        self.project_edit.setReadOnly(True)
        self.project_edit.setToolTip("Thư mục gốc chứa script / audio / video / assets.")
        project_layout.addWidget(self.project_edit, 1)

        browse_project = QPushButton("Chọn Project")
        set_icon(browse_project, "folder-open", "accent", 15)
        browse_project.clicked.connect(self._browse_project)
        project_layout.addWidget(browse_project)

        create_project = QPushButton("Tạo Project mới")
        set_icon(create_project, "plus", "accent", 14)
        create_project.clicked.connect(self._create_project)
        project_layout.addWidget(create_project)

        open_project = icon_button(
            "external-link", "Mở thư mục project trong Explorer", size=15, side=30
        )
        open_project.clicked.connect(self._open_project_folder)
        project_layout.addWidget(open_project)
        root.addWidget(project_box)

        # ── toolbar ───────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.force_check = QCheckBox("Tạo lại các file đã tồn tại khi thực hiện quá trình")
        self.force_check.setToolTip(
            "Xoá kết quả cũ và tạo lại audio/video trong lần chạy tới."
        )
        toolbar.addWidget(self.force_check)
        toolbar.addStretch(1)

        new_folder = self._tool_button("Thư mục mới", "folder", "accent", self._new_folder)
        new_file = self._tool_button("File mới", "file", "accent", self._new_file)
        rename = self._tool_button("Đổi tên", "edit", "muted", self._rename)
        delete = self._tool_button("Xoá", "trash", "danger", self._delete)
        open_btn = self._tool_button("Mở", "external-link", "muted", self._open)
        refresh = icon_button(
            "refresh", "Làm mới cây thư mục", role="muted", size=14, side=30
        )
        refresh.clicked.connect(self._refresh_tree)
        toolbar.addWidget(new_folder)
        toolbar.addWidget(new_file)
        toolbar.addWidget(rename)
        toolbar.addWidget(delete)
        toolbar.addWidget(open_btn)
        toolbar.addWidget(refresh)
        root.addLayout(toolbar)

        # ── file tree ─────────────────────────────────────────────────────────
        self.fs_model = QFileSystemModel(self)
        self.fs_model.setReadOnly(False)
        self.fs_model.setFilter(
            QDir.Filter.AllEntries
            | QDir.Filter.NoDotAndDotDot
            | QDir.Filter.AllDirs
        )

        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        self.tree.setRootIndex(QModelIndex())
        self.tree.setHeaderHidden(True)
        for column in range(1, self.fs_model.columnCount()):
            self.tree.hideColumn(column)
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.doubleClicked.connect(self._on_double_click)
        root.addWidget(self.tree, 1)

    # ── construction helpers ─────────────────────────────────────────────────
    @staticmethod
    def _tool_button(text: str, icon_name: str, role: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("smallButton")
        set_icon(button, icon_name, role, 14)
        button.clicked.connect(slot)
        return button

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        project = settings.get("project") or {}
        self.project_edit.setText(str(project.get("last_project") or ""))
        self.force_check.setChecked(bool(project.get("force_overwrite", False)))
        self._set_root(self.project_edit.text().strip())

    def apply_to(self, settings: Dict[str, Any]) -> None:
        project = settings.setdefault("project", {})
        project["last_project"] = self.project_edit.text().strip()
        project["force_overwrite"] = self.force_check.isChecked()

    def set_project(self, path: str) -> None:
        self.project_edit.setText(path)
        self._set_root(path)

    def force_overwrite(self) -> bool:
        return self.force_check.isChecked()

    # ── project picker ───────────────────────────────────────────────────────
    def _browse_project(self) -> None:
        start = self.project_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục Project", start)
        if chosen:
            self.project_edit.setText(chosen)
            self._set_root(chosen)
            self.reload_requested.emit()

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
        self._set_root(str(target))
        self.reload_requested.emit()

    def _open_project_folder(self) -> None:
        path = self.project_edit.text().strip()
        if path and Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ── tree plumbing ────────────────────────────────────────────────────────
    def set_manager(self, manager: Optional[ProjectManager]) -> None:
        self.manager = manager
        if manager is not None:
            self._set_root(str(manager.project_dir))
        else:
            self._clear_root()

    def refresh(self) -> None:
        root = self.fs_model.rootPath()
        if not root or not Path(root).is_dir():
            self._set_root(self.project_edit.text().strip())

    def apply_theme(self) -> None:
        # The tree uses native file icons; header/action icons are repainted
        # globally by MainWindow via refresh_icons().
        pass

    def _set_root(self, path: str) -> None:
        target = Path(path) if str(path or "").strip() else None
        if target is None or not target.is_dir():
            self._clear_root()
            return
        self.fs_model.setRootPath(str(target))
        self.tree.setRootIndex(self.fs_model.index(str(target)))
        self.tree.expandToDepth(0)

    def _clear_root(self) -> None:
        self.tree.setRootIndex(QModelIndex())

    def _refresh_tree(self) -> None:
        self._set_root(self.fs_model.rootPath() or self.project_edit.text().strip())

    # ── selection helpers ────────────────────────────────────────────────────
    def _selected_index(self) -> QModelIndex:
        index = self.tree.currentIndex()
        return index if index.isValid() else self.tree.rootIndex()

    def _selected_path(self) -> Optional[Path]:
        index = self._selected_index()
        if index.isValid():
            return Path(self.fs_model.filePath(index))
        root = self.fs_model.rootPath()
        return Path(root) if root and Path(root).is_dir() else None

    def _folder_parent_index(self) -> QModelIndex:
        index = self.tree.currentIndex()
        if not index.isValid():
            return self.tree.rootIndex()
        if Path(self.fs_model.filePath(index)).is_dir():
            return index
        return index.parent() if index.parent().isValid() else self.tree.rootIndex()

    def _reveal(self, index: QModelIndex) -> None:
        if index.isValid():
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)

    # ── file operations ──────────────────────────────────────────────────────
    def _new_folder(self) -> None:
        parent = self._folder_parent_index()
        if not parent.isValid():
            self._warn_no_project()
            return
        name, ok = QInputDialog.getText(self, "Thư mục mới", "Tên thư mục:")
        if not ok or not str(name).strip():
            return
        index = self.fs_model.mkdir(parent, str(name).strip())
        if not index.isValid():
            QMessageBox.warning(self, "Thư mục mới", "Không tạo được thư mục.")
            return
        self._reveal(index)

    def _new_file(self) -> None:
        parent = self._folder_parent_index()
        parent_path = (
            Path(self.fs_model.filePath(parent))
            if parent.isValid() else Path(self.fs_model.rootPath())
        )
        if not parent_path.is_dir():
            self._warn_no_project()
            return
        name, ok = QInputDialog.getText(
            self, "File mới", "Tên file (mặc định thêm .txt nếu thiếu):"
        )
        if not ok or not str(name).strip():
            return
        name = str(name).strip()
        if not Path(name).suffix:
            name += ".txt"
        target = parent_path / name
        try:
            target.touch(exist_ok=False)
        except OSError as exc:
            QMessageBox.warning(self, "File mới", str(exc))
            return
        self._reveal(self.fs_model.index(str(target)))

    def _rename(self) -> None:
        index = self.tree.currentIndex()
        if index.isValid() and index != self.tree.rootIndex():
            self.tree.edit(index)

    def _delete(self) -> None:
        index = self.tree.currentIndex()
        if not index.isValid() or index == self.tree.rootIndex():
            return
        path = Path(self.fs_model.filePath(index))
        answer = QMessageBox.question(
            self,
            "Xoá",
            f"Xoá '{path.name}'? Hành động này không thể hoàn tác.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Xoá", str(exc))

    def _open(self) -> None:
        path = self._selected_path()
        if path is not None and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_double_click(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        path = Path(self.fs_model.filePath(index))
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _context_menu(self, pos) -> None:
        index = self.tree.indexAt(pos)
        menu = QMenu(self)
        if index.isValid() and index != self.tree.rootIndex():
            self.tree.setCurrentIndex(index)
            menu.addAction("Mở", self._open)
            menu.addAction("Đổi tên", self._rename)
            menu.addAction("Xoá", self._delete)
            menu.addSeparator()
        menu.addAction("Thư mục mới", self._new_folder)
        menu.addAction("File mới", self._new_file)
        menu.addSeparator()
        menu.addAction("Làm mới", self._refresh_tree)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _warn_no_project(self) -> None:
        QMessageBox.information(self, "Project", "Hãy chọn Project trước.")
