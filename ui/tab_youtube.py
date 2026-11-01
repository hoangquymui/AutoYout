"""YouTube tab: channel auth, metadata templates, scheduling and browser mode."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import QDateTime, QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.youtube_uploader import YouTubeUploader
from ui import theme
from ui.components.icons import icon, set_icon
from ui.components.page import (
    PageHeader,
    icon_button,
    paint_status,
    stack_columns,
    wrap_scroll,
)
from ui.tab_thumbnail import ThumbnailPanel
from utils.logger import get_logger

logger = get_logger("ui.youtube")

_CATEGORIES = [
    ("22 — People & Blogs", "22"),
    ("24 — Entertainment", "24"),
    ("27 — Education", "27"),
    ("28 — Science & Technology", "28"),
    ("10 — Music", "10"),
    ("20 — Gaming", "20"),
    ("26 — Howto & Style", "26"),
    ("25 — News & Politics", "25"),
    ("17 — Sports", "17"),
    ("23 — Comedy", "23"),
]

_TITLE_VARS = ["{project_name}", "{file_name}", "{index}", "{part_number}", "{title}"]
_DESCRIPTION_VARS = [
    "{project_name}", "{file_name}", "{index}", "{part_number}",
    "{title}", "{description}", "{timestamps}", "{youtube_url}",
]


class _AuthWorker(QThread):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, settings: Dict[str, Any], channel: str, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._channel = channel

    def run(self) -> None:
        try:
            uploader = YouTubeUploader(self._settings)
            channel = uploader.authorize(self._channel, progress_cb=self.progress.emit)
            self.done.emit(channel)
        except Exception as exc:
            self.failed.emit(str(exc))


class _BrowserLoginWorker(QThread):
    progress = Signal(int, str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, settings: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._settings = settings

    def run(self) -> None:
        try:
            info = YouTubeUploader(self._settings).browser_login(
                progress_cb=self.progress.emit
            )
            self.done.emit(info)
        except Exception as exc:
            self.failed.emit(str(exc))


class _CheckWorker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, settings: Dict[str, Any], channel: str, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._channel = channel

    def run(self) -> None:
        try:
            info = YouTubeUploader(self._settings).channel_info(self._channel)
            self.done.emit(info)
        except Exception as exc:
            self.failed.emit(str(exc))


class YouTubeConfigTab(QWidget):
    busy_changed = Signal(bool)
    channels_changed = Signal()
    thumbnail_saved = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._worker: QThread | None = None
        self._pipeline_busy = False

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setDocumentMode(True)
        page.addWidget(self.sub_tabs)
        content = QWidget()
        content.setObjectName("PageContent")
        self.sub_tabs.addTab(wrap_scroll(content), "Đăng tải")

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addWidget(
            PageHeader(
                "Đăng tải YouTube",
                "Quản lý kênh, metadata, thumbnail và lịch đăng tự động.",
            )
        )

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self._columns = columns
        root.addLayout(columns, 1)

        left = QVBoxLayout()
        left.setSpacing(12)
        columns.addLayout(left, 1)

        channel_box = QGroupBox("Kênh YouTube")
        channel_layout = QVBoxLayout(channel_box)
        channel_layout.setSpacing(8)
        method_form = QFormLayout()
        method_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.method_combo = QComboBox()
        self.method_combo.addItem("YouTube Data API v3 (OAuth2)", "api")
        self.method_combo.addItem("Browser Automation (Playwright + Chrome)", "browser")
        method_form.addRow("Phương thức", self.method_combo)
        channel_layout.addLayout(method_form)

        channel_row = QHBoxLayout()
        channel_row.setSpacing(8)
        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(200)
        self.channel_combo.setIconSize(QSize(14, 14))
        refresh_channels = icon_button(
            "refresh", "Quét lại các token đã lưu", role="muted", size=14, side=30
        )
        refresh_channels.clicked.connect(lambda: self.refresh_channels())
        channel_row.addWidget(QLabel("Kênh đã lưu"))
        channel_row.addWidget(self.channel_combo, 1)
        channel_row.addWidget(refresh_channels)
        channel_layout.addLayout(channel_row)

        new_row = QHBoxLayout()
        new_row.setSpacing(8)
        self.new_channel_edit = QLineEdit()
        self.new_channel_edit.setPlaceholderText("Tên kênh mới (ví dụ: Kênh Chính)")
        self.login_button = QPushButton("Đăng nhập / Cấp quyền")
        self.login_button.setObjectName("primaryButton")
        set_icon(self.login_button, "key", "white", 14)
        self.login_button.clicked.connect(self._login)
        self.check_button = QPushButton("Kiểm tra kênh")
        set_icon(self.check_button, "check-circle", "accent", 14)
        self.check_button.clicked.connect(self._check_channel)
        new_row.addWidget(self.new_channel_edit, 1)
        channel_layout.addLayout(new_row)

        auth_row = QHBoxLayout()
        auth_row.setSpacing(8)
        auth_row.addWidget(self.login_button)
        auth_row.addWidget(self.check_button)
        auth_row.addStretch(1)
        channel_layout.addLayout(auth_row)

        self.client_secret_edit = QLineEdit()
        self.client_secret_edit.setPlaceholderText("config/youtube_credentials/client_secret.json")
        secret_row = QHBoxLayout()
        secret_row.setSpacing(6)
        secret_row.addWidget(self.client_secret_edit, 1)
        secret_browse = icon_button(
            "file", "Chọn client_secret.json…", role="muted", size=15, side=30
        )
        secret_browse.clicked.connect(self._browse_secret)
        secret_row.addWidget(secret_browse)
        secret_label = QLabel("client_secret.json")
        secret_label.setObjectName("FieldLabel")
        channel_layout.addWidget(secret_label)
        channel_layout.addLayout(secret_row)
        secret_hint = QLabel(
            "Token được lưu riêng cho từng kênh trong config/youtube_credentials/."
        )
        secret_hint.setObjectName("MutedLabel")
        secret_hint.setWordWrap(True)
        channel_layout.addWidget(secret_hint)

        self.auth_status = QLabel("Chưa kiểm tra kênh.")
        self.auth_status.setObjectName("MutedLabel")
        self.auth_status.setWordWrap(True)
        channel_layout.addWidget(self.auth_status)
        left.addWidget(channel_box)

        schedule_box = QGroupBox("Lên lịch đăng")
        schedule_form = QFormLayout(schedule_box)
        schedule_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.schedule_combo = QComboBox()
        self.schedule_combo.addItem("Không lên lịch", "none")
        self.schedule_combo.addItem("Cách nhau N giờ", "interval")
        self.schedule_combo.addItem("Giờ cố định mỗi ngày", "times")
        self.schedule_combo.addItem("Theo metadata từng file", "metadata")
        schedule_form.addRow("Kiểu lên lịch", self.schedule_combo)
        self.schedule_stack = QStackedWidget()

        interval_page = QWidget()
        interval_form = QFormLayout(interval_page)
        interval_form.setContentsMargins(0, 0, 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 336)
        self.interval_spin.setSuffix(" giờ")
        interval_form.addRow("Khoảng cách", self.interval_spin)
        self.schedule_stack.addWidget(interval_page)

        times_page = QWidget()
        times_form = QFormLayout(times_page)
        times_form.setContentsMargins(0, 0, 0, 0)
        self.times_edit = QLineEdit()
        self.times_edit.setPlaceholderText("08:00,20:00")
        times_form.addRow("Khung giờ", self.times_edit)
        self.schedule_stack.addWidget(times_page)

        metadata_page = QWidget()
        metadata_layout = QVBoxLayout(metadata_page)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Dùng trường publish_at trong file JSON của từng item.")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        metadata_layout.addWidget(note)
        metadata_layout.addStretch(1)
        self.schedule_stack.addWidget(metadata_page)
        schedule_form.addRow("", self.schedule_stack)

        first_row = QHBoxLayout()
        self.first_check = QCheckBox("Bắt đầu từ")
        self.first_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.first_edit.setCalendarPopup(True)
        self.first_edit.setDisplayFormat("dd/MM/yyyy HH:mm")
        self.first_edit.setEnabled(False)
        self.first_check.toggled.connect(self.first_edit.setEnabled)
        first_row.addWidget(self.first_check)
        first_row.addWidget(self.first_edit, 1)
        schedule_form.addRow("", self._wrap(first_row))
        left.addWidget(schedule_box)

        browser_box = QGroupBox("Browser Automation")
        browser_form = QFormLayout(browser_box)
        browser_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.browser_profile_edit = QLineEdit()
        self.browser_profile_edit.setPlaceholderText(
            "%LOCALAPPDATA%\\AutoYout\\chrome-profile (mặc định)"
        )
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        profile_row.addWidget(self.browser_profile_edit, 1)
        profile_browse = icon_button(
            "folder", "Chọn thư mục Chrome profile…", role="muted", size=15, side=30
        )
        profile_browse.clicked.connect(self._browse_profile)
        profile_row.addWidget(profile_browse)
        browser_form.addRow("Chrome profile", self._wrap(profile_row))
        self.headless_check = QCheckBox("Chạy ẩn (headless)")
        browser_form.addRow("", self.headless_check)
        login_row = QHBoxLayout()
        login_row.setSpacing(8)
        self.browser_login_button = QPushButton("Đăng nhập bằng Chrome")
        set_icon(self.browser_login_button, "log-in", "accent", 14)
        self.browser_login_button.clicked.connect(self._browser_login)
        login_row.addWidget(self.browser_login_button)
        login_row.addStretch(1)
        browser_form.addRow("", self._wrap(login_row))
        self.browser_status = QLabel("Chưa đăng nhập Chrome.")
        self.browser_status.setObjectName("MutedLabel")
        self.browser_status.setWordWrap(True)
        browser_form.addRow("", self.browser_status)
        browser_hint = QLabel(
            "Bấm Đăng nhập bằng Chrome để mở cửa sổ đăng nhập; phiên đăng nhập "
            "được lưu trong profile cho các lần sau."
        )
        browser_hint.setObjectName("MutedLabel")
        browser_hint.setWordWrap(True)
        browser_form.addRow("", browser_hint)
        left.addWidget(browser_box)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(12)
        columns.addLayout(right, 2)

        meta_box = QGroupBox("Tiêu đề && mô tả")
        meta_layout = QVBoxLayout(meta_box)
        meta_layout.setSpacing(8)
        title_label = QLabel("Title template")
        title_label.setObjectName("FieldLabel")
        meta_layout.addWidget(title_label)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("{title}")
        meta_layout.addWidget(self.title_edit)
        meta_layout.addLayout(self._variable_row(_TITLE_VARS, self.title_edit, line=True))
        description_label = QLabel("Description template")
        description_label.setObjectName("FieldLabel")
        meta_layout.addWidget(description_label)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setMinimumHeight(120)
        meta_layout.addWidget(self.description_edit, 1)
        meta_layout.addLayout(
            self._variable_row(_DESCRIPTION_VARS, self.description_edit, line=False)
        )

        details = QGridLayout()
        details.setHorizontalSpacing(12)
        details.setVerticalSpacing(8)
        details.addWidget(QLabel("Tags"), 0, 0)
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("tag1, tag2, tag3")
        details.addWidget(self.tags_edit, 0, 1, 1, 3)
        details.addWidget(QLabel("Danh mục"), 1, 0)
        self.category_combo = QComboBox()
        for label, value in _CATEGORIES:
            self.category_combo.addItem(label, value)
        details.addWidget(self.category_combo, 1, 1)
        details.addWidget(QLabel("Ngôn ngữ"), 1, 2)
        self.language_edit = QLineEdit()
        self.language_edit.setMaximumWidth(80)
        details.addWidget(self.language_edit, 1, 3)
        details.addWidget(QLabel("Quyền riêng tư"), 2, 0)
        self.privacy_combo = QComboBox()
        self.privacy_combo.addItem("Công khai", "public")
        self.privacy_combo.addItem("Không công khai", "unlisted")
        self.privacy_combo.addItem("Riêng tư", "private")
        details.addWidget(self.privacy_combo, 2, 1)
        self.kids_check = QCheckBox("Dành cho trẻ em")
        details.addWidget(self.kids_check, 2, 2, 1, 2)
        details.addWidget(QLabel("Playlist ID"), 3, 0)
        self.playlist_edit = QLineEdit()
        self.playlist_edit.setPlaceholderText("PLxxxxxxxx (tùy chọn)")
        details.addWidget(self.playlist_edit, 3, 1, 1, 3)
        details.addWidget(QLabel("Thumbnail"), 4, 0)
        self.thumbnail_edit = QLineEdit()
        self.thumbnail_edit.setPlaceholderText("Ảnh .jpg/.png (tùy chọn)")
        thumb_row = QHBoxLayout()
        thumb_row.setSpacing(6)
        thumb_row.addWidget(self.thumbnail_edit, 1)
        thumb_browse = icon_button(
            "image", "Chọn thumbnail…", role="muted", size=15, side=30
        )
        thumb_browse.clicked.connect(self._browse_thumbnail)
        thumb_row.addWidget(thumb_browse)
        details.addWidget(self._wrap(thumb_row), 4, 1, 1, 3)
        details.setColumnStretch(1, 1)
        meta_layout.addLayout(details)
        right.addWidget(meta_box, 1)

        hint_box = QGroupBox("Biến động hỗ trợ")
        hint_layout = QVBoxLayout(hint_box)
        hint = QLabel(
            "{project_name} · {file_name} · {index} · {part_number} · {timestamps}\n"
            "{title} · {description} · {youtube_url} · {date} · {channel}"
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        hint_layout.addWidget(hint)
        right.addWidget(hint_box)

        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        self.schedule_combo.currentIndexChanged.connect(self._on_schedule_changed)

        self.thumbnail_panel = ThumbnailPanel()
        self.thumbnail_panel.thumbnail_saved.connect(self.thumbnail_saved.emit)
        self.sub_tabs.addTab(self.thumbnail_panel, "Hình thu nhỏ")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        stack_columns(self, self._columns, 980)

    @staticmethod
    def _wrap(layout) -> QWidget:
        holder = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(layout)
        return holder

    def _variable_row(self, variables, target, line: bool) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        per_row = 5
        for start in range(0, len(variables), per_row):
            row = QHBoxLayout()
            row.setSpacing(4)
            for token in variables[start:start + per_row]:
                button = QPushButton(token)
                button.setObjectName("variableButton")
                if line:
                    button.clicked.connect(
                        lambda _=False, t=token, w=target: self._insert_line(w, t)
                    )
                else:
                    button.clicked.connect(
                        lambda _=False, t=token, w=target: self._insert_text(w, t)
                    )
                row.addWidget(button)
            row.addStretch(1)
            box.addLayout(row)
        return box

    @staticmethod
    def _insert_line(edit: QLineEdit, token: str) -> None:
        edit.insert(token)
        edit.setFocus()

    @staticmethod
    def _insert_text(edit: QPlainTextEdit, token: str) -> None:
        edit.insertPlainText(token)
        edit.setFocus()

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        yt = settings.get("youtube") or {}
        self._set_combo(self.method_combo, yt.get("method", "api"))
        self.client_secret_edit.setText(str(yt.get("client_secret") or ""))
        self.title_edit.setText(str(yt.get("title_template") or "{title}"))
        self.description_edit.setPlainText(str(yt.get("description_template") or ""))
        self.tags_edit.setText(str(yt.get("tags") or ""))
        self._set_combo(self.category_combo, str(yt.get("category_id") or "22"))
        self.language_edit.setText(str(yt.get("language") or "vi"))
        self._set_combo(self.privacy_combo, yt.get("privacy", "private"))
        self._set_combo(self.schedule_combo, yt.get("schedule_mode", "interval"))
        self.interval_spin.setValue(int(float(yt.get("schedule_interval_hours", 24) or 24)))
        self.times_edit.setText(str(yt.get("schedule_times") or "08:00,20:00"))
        first = str(yt.get("schedule_first") or "").strip()
        if first:
            parsed = QDateTime.fromString(first, Qt.DateFormat.ISODate)
            if parsed.isValid():
                self.first_edit.setDateTime(parsed)
                self.first_check.setChecked(True)
            else:
                self.first_check.setChecked(False)
        else:
            self.first_check.setChecked(False)
        self.playlist_edit.setText(str(yt.get("playlist_id") or ""))
        self.thumbnail_edit.setText(str(yt.get("thumbnail") or ""))
        self.kids_check.setChecked(bool(yt.get("made_for_kids", False)))
        self.browser_profile_edit.setText(str(yt.get("browser_profile") or ""))
        self.headless_check.setChecked(bool(yt.get("browser_headless", False)))
        self._on_method_changed()
        self._on_schedule_changed()
        self.refresh_channels(select=str(yt.get("channel") or ""))
        self.thumbnail_panel.load_from(settings)

    def apply_to(self, settings: Dict[str, Any]) -> None:
        yt = settings.setdefault("youtube", {})
        yt["method"] = self.method_combo.currentData() or "api"
        yt["channel"] = self.channel_combo.currentData() or "default"
        yt["client_secret"] = self.client_secret_edit.text().strip()
        yt["title_template"] = self.title_edit.text().strip() or "{title}"
        yt["description_template"] = self.description_edit.toPlainText()
        yt["tags"] = self.tags_edit.text().strip()
        yt["category_id"] = self.category_combo.currentData() or "22"
        yt["language"] = self.language_edit.text().strip() or "vi"
        yt["privacy"] = self.privacy_combo.currentData() or "private"
        yt["schedule_mode"] = self.schedule_combo.currentData() or "none"
        yt["schedule_first"] = (
            self.first_edit.dateTime().toString(Qt.DateFormat.ISODate)
            if self.first_check.isChecked()
            else ""
        )
        yt["schedule_interval_hours"] = float(self.interval_spin.value())
        yt["schedule_times"] = self.times_edit.text().strip() or "08:00,20:00"
        yt["playlist_id"] = self.playlist_edit.text().strip()
        yt["thumbnail"] = self.thumbnail_edit.text().strip()
        yt["made_for_kids"] = self.kids_check.isChecked()
        yt["browser_profile"] = self.browser_profile_edit.text().strip()
        yt["browser_headless"] = self.headless_check.isChecked()
        self.thumbnail_panel.apply_to(settings)
        self._settings = settings

    def set_project(self, manager) -> None:
        self.thumbnail_panel.set_project(manager)

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ── UI behaviour ─────────────────────────────────────────────────────────
    def _on_method_changed(self) -> None:
        browser = self.method_combo.currentData() == "browser"
        self.browser_profile_edit.setEnabled(browser)
        self.headless_check.setEnabled(browser)
        self.browser_login_button.setVisible(browser)
        self.browser_status.setVisible(browser)
        enabled = self._worker is None and not self._pipeline_busy
        self.browser_login_button.setEnabled(enabled)
        self.login_button.setEnabled(enabled)
        self.check_button.setEnabled(enabled)

    def _on_schedule_changed(self) -> None:
        mode = self.schedule_combo.currentData()
        index = {"interval": 0, "times": 1, "metadata": 2}.get(mode, 0)
        self.schedule_stack.setCurrentIndex(index)
        self.schedule_stack.setEnabled(mode in ("interval", "times", "metadata"))

    def _browse_secret(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn client_secret.json", str(Path.home()), "JSON (*.json);;Tất cả (*)"
        )
        if path:
            self.client_secret_edit.setText(path)

    def _browse_thumbnail(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn thumbnail", str(Path.home()),
            "Ảnh (*.png *.jpg *.jpeg *.webp);;Tất cả (*)",
        )
        if path:
            self.thumbnail_edit.setText(path)

    def _browse_profile(self) -> None:
        start = self.browser_profile_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn Chrome profile", start)
        if chosen:
            self.browser_profile_edit.setText(chosen)

    # ── channels / auth ──────────────────────────────────────────────────────
    def apply_theme(self) -> None:
        self.thumbnail_panel.apply_theme()
        if self._settings:
            self.refresh_channels(select=str(self.channel_combo.currentData() or ""))

    def refresh_channels(self, select: str = "") -> None:
        if not self._settings:
            return
        current = select or self.channel_combo.currentData() or ""
        try:
            channels = YouTubeUploader(self._settings).channels()
        except Exception as exc:
            logger.error("Không đọc được danh sách kênh: %s", exc)
            channels = []
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for channel in channels:
            title = f" — {channel.title}" if channel.title else ""
            if channel.authorized:
                self.channel_combo.addItem(
                    icon("check-circle", theme.icon_color("success"), 14),
                    f"{channel.name}{title}",
                    channel.name,
                )
            else:
                self.channel_combo.addItem(
                    icon("clock", theme.icon_color("warning"), 14),
                    f"{channel.name}{title} · chưa đăng nhập",
                    channel.name,
                )
        if not channels:
            self.channel_combo.addItem("— Chưa có kênh, hãy đăng nhập —", "default")
        self.channel_combo.blockSignals(False)
        if current:
            self._set_combo(self.channel_combo, current)
        if self.channel_combo.count() == 0:
            self.channel_combo.addItem("default", "default")

    def _snapshot(self) -> Dict[str, Any]:
        snapshot = copy.deepcopy(self._settings)
        self.apply_to(snapshot)
        return snapshot

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.login_button.setEnabled(not busy)
        self.check_button.setEnabled(not busy)
        self.browser_login_button.setEnabled(
            not busy and self.method_combo.currentData() == "browser"
        )
        self.channel_combo.setEnabled(not busy)
        self.new_channel_edit.setEnabled(not busy)
        if message:
            paint_status(self.auth_status, "running")
            self.auth_status.setText(message)
        self.busy_changed.emit(busy)

    def busy(self) -> bool:
        return self._worker is not None

    def set_pipeline_busy(self, busy: bool) -> None:
        self._pipeline_busy = busy
        self.browser_login_button.setEnabled(
            not busy and self.method_combo.currentData() == "browser"
        )
        if busy:
            self.login_button.setEnabled(False)
            self.check_button.setEnabled(False)
        elif self._worker is None:
            self.login_button.setEnabled(True)
            self.check_button.setEnabled(True)

    def _login(self) -> None:
        if self._worker is not None:
            return
        channel = self.new_channel_edit.text().strip() or str(
            self.channel_combo.currentData() or "default"
        )
        snapshot = self._snapshot()
        snapshot["youtube"]["channel"] = channel
        self._worker = _AuthWorker(snapshot, channel, self)
        self._worker.progress.connect(
            lambda _pct, msg: self._set_status_message(msg)
        )
        self._worker.done.connect(self._on_auth_done)
        self._worker.failed.connect(self._on_auth_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._set_busy(True, f"Đang mở trình duyệt đăng nhập cho kênh '{channel}'…")
        logger.info("Bắt đầu uỷ quyền kênh YouTube '%s'", channel)
        self._worker.start()

    def _browser_login(self) -> None:
        if self._worker is not None:
            return
        self._worker = _BrowserLoginWorker(self._snapshot(), self)
        self._worker.progress.connect(self._on_browser_progress)
        self._worker.done.connect(self._on_browser_login_done)
        self._worker.failed.connect(self._on_browser_login_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._set_busy(True)
        self._set_browser_status("Đang mở Chrome để đăng nhập…", "running")
        logger.info("Bắt đầu đăng nhập YouTube bằng Chrome")
        self._worker.start()

    def _set_browser_status(self, message: str, level: str = "muted") -> None:
        paint_status(self.browser_status, level)
        self.browser_status.setText(message)

    def _on_browser_progress(self, _percent: int, message: str) -> None:
        self._set_browser_status(message, "running")

    def _on_browser_login_done(self, info: dict) -> None:
        title = str(info.get("title") or "")
        profile = str(info.get("profile") or "")
        self._set_browser_status(
            f"Đã đăng nhập: {title}" if title else "Đã đăng nhập Chrome.", "success"
        )
        self.browser_status.setToolTip(profile)
        logger.info("Đăng nhập Chrome xong (%s)", profile)

    def _on_browser_login_failed(self, message: str) -> None:
        self._set_browser_status(message, "error")
        logger.error("Đăng nhập Chrome thất bại: %s", message)

    def check_channel(self) -> None:
        self._check_channel()

    def _check_channel(self) -> None:
        if self._worker is not None:
            return
        channel = str(self.channel_combo.currentData() or "default")
        snapshot = self._snapshot()
        snapshot["youtube"]["channel"] = channel
        self._worker = _CheckWorker(snapshot, channel, self)
        self._worker.done.connect(self._on_check_done)
        self._worker.failed.connect(self._on_auth_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._set_busy(True, f"Đang kiểm tra kênh '{channel}'…")
        self._worker.start()

    def _set_status_message(self, message: str) -> None:
        paint_status(self.auth_status, "running")
        self.auth_status.setText(message)

    def _on_auth_done(self, channel) -> None:
        title = getattr(channel, "title", "") or getattr(channel, "name", "")
        paint_status(self.auth_status, "success")
        self.auth_status.setText(f"Đã kết nối kênh: {title}")
        self.new_channel_edit.clear()
        self.refresh_channels(select=getattr(channel, "name", ""))
        logger.info("Đã kết nối kênh YouTube '%s'", title)
        self.channels_changed.emit()

    def _on_check_done(self, info: dict) -> None:
        title = info.get("title", "")
        subs = info.get("subscribers", "?")
        videos = info.get("videos", "?")
        paint_status(self.auth_status, "success")
        self.auth_status.setText(
            f"{title} · {subs} người đăng ký · {videos} video"
        )
        logger.info("Kênh YouTube: %s (%s sub, %s video)", title, subs, videos)

    def _on_auth_failed(self, message: str) -> None:
        paint_status(self.auth_status, "error")
        self.auth_status.setText(message)
        logger.error("YouTube: %s", message)

    def _on_worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)
        if self._pipeline_busy:
            self.set_pipeline_busy(True)
