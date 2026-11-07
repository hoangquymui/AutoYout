"""YouTube tab: channel auth, metadata templates, scheduling and browser mode."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QDateTime, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.language_profiles import load_profiles
from core.languages import LANGUAGES, language_name, normalize_language, normalize_language_code

from core.project_manager import (
    VIDEO_DONE,
    VIDEO_ERROR,
    VIDEO_PENDING,
    VIDEO_PROCESSING,
    VIDEO_UPLOADING,
    VIDEO_UPLOAD_DONE,
    VideoJob,
    format_duration,
    natural_key,
)
from core.youtube_uploader import YouTubeUploader
from ui import theme
from ui.components.icons import icon, set_icon, set_pixmap
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

LANGUAGE_FLAGS: Dict[str, str] = {
    "vi": "🇻🇳",
    "en": "🇺🇸",
    "zh": "🇨🇳",
    "ja": "🇯🇵",
    "ko": "🇰🇷",
    "fr": "🇫🇷",
    "es": "🇪🇸",
    "th": "🇹🇭",
    "id": "🇮🇩",
    "hi": "🇮🇳",
    "de": "🇩🇪",
    "ru": "🇷🇺",
    "pt": "🇧🇷",
    "it": "🇮🇹",
}

COMMON_LANGUAGES: List[Tuple[str, str]] = [
    ("vi", "🇻🇳 Tiếng Việt"),
    ("en", "🇺🇸 English"),
    ("ja", "🇯🇵 日本語"),
    ("zh", "🇨🇳 中文"),
    ("ko", "🇰🇷 한국어"),
    ("fr", "🇫🇷 Français"),
    ("es", "🇪🇸 Español"),
    ("th", "🇹🇭 ไทย"),
    ("id", "🇮🇩 Indonesia"),
    ("hi", "🇮🇳 हिन्दी"),
]


def _make_language_chips_widget(langs: List[str]) -> QWidget:
    """Render attractive chips/badges for assigned target languages."""
    container = QWidget()
    h = QHBoxLayout(container)
    h.setContentsMargins(6, 2, 6, 2)
    h.setSpacing(6)
    h.setAlignment(Qt.AlignmentFlag.AlignCenter)

    cleaned = [str(l).strip().lower() for l in langs if str(l).strip()]
    if not cleaned or "all" in cleaned or "*" in cleaned:
        lbl = QLabel("🌐 Tất cả")
        lbl.setStyleSheet(
            f"background: {theme.STAGE_BG}; color: {theme.STAGE_TEXT}; "
            f"border: 1px solid {theme.STAGE_BORDER}; border-radius: 10px; "
            "padding: 3px 8px; font-weight: 600; font-size: 11px;"
        )
        lbl.setToolTip("Phụ trách tất cả ngôn ngữ (kênh mặc định/dự phòng)")
        h.addWidget(lbl)
    else:
        for code in cleaned:
            flag = LANGUAGE_FLAGS.get(code, "🌐")
            lbl = QLabel(f"{flag} {code.upper()}")
            lbl.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                f"border: 1px solid {theme.SUCCESS}; border-radius: 10px; "
                "padding: 3px 8px; font-weight: 600; font-size: 11px;"
            )
            lbl.setToolTip(f"{code.upper()} — {language_name(code)}")
            h.addWidget(lbl)
    return container


def _configured_languages(profiles: Optional[List[Any]] = None) -> List[Tuple[str, str]]:
    """Return ``(code, display_name)`` pairs from script language profiles (with ``vi`` first)."""
    if profiles is None:
        try:
            profiles = load_profiles()
        except Exception:
            profiles = []
    items: List[Tuple[str, str]] = [("vi", "Tiếng Việt")]
    seen = {"vi"}
    if profiles:
        for prof in profiles:
            code = normalize_language_code(getattr(prof, "language", "") or "", "")
            if not code:
                continue
            custom = getattr(prof, "name", "") or language_name(code)
            if code in seen:
                if getattr(prof, "name", ""):
                    items = [(c, custom if c == code else lbl) for c, lbl in items]
                continue
            seen.add(code)
            items.append((code, custom))
    else:
        for code, label in [
            ("en", "Tiếng Anh"),
            ("ja", "Tiếng Nhật"),
            ("zh", "Tiếng Trung"),
            ("ko", "Tiếng Hàn"),
            ("fr", "Tiếng Pháp"),
        ]:
            if code not in seen:
                seen.add(code)
                items.append((code, label))
    return items


class AssignLanguagesDialog(QDialog):
    """Dialog for assigning target languages to a YouTube channel."""

    def __init__(self, channel_name: str, current_langs: List[str], parent=None,
                 profiles: Optional[List[Any]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Gán ngôn ngữ chỉ định — {channel_name}")
        self.setMinimumWidth(460)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        desc = QLabel(f"Chọn các ngôn ngữ mà kênh <b>{channel_name}</b> sẽ phụ trách tải lên:")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.all_cb = QCheckBox("🌐 Tất cả ngôn ngữ (kênh mặc định/chung)")
        self.all_cb.setStyleSheet("font-weight: bold; margin-bottom: 4px;")
        layout.addWidget(self.all_cb)

        grid_box = QGroupBox("Ngôn ngữ đã cấu hình")
        grid = QGridLayout(grid_box)
        grid.setSpacing(8)
        self.checkboxes: Dict[str, QCheckBox] = {}
        lang_items = _configured_languages(profiles)
        for idx, (code, name) in enumerate(lang_items):
            flag = LANGUAGE_FLAGS.get(code, "🌐")
            label = f"{flag} {name} ({code})" if code not in name else f"{flag} {name}"
            cb = QCheckBox(label)
            self.checkboxes[code] = cb
            grid.addWidget(cb, idx // 2, idx % 2)
        layout.addWidget(grid_box)

        other_row = QHBoxLayout()
        other_row.setSpacing(8)
        other_row.addWidget(QLabel("Mã khác:"))
        self.other_edit = QLineEdit()
        self.other_edit.setPlaceholderText("de, ru, it, pt... (phân cách bằng dấu phẩy)")
        other_row.addWidget(self.other_edit, 1)
        layout.addLayout(other_row)

        def _on_all_toggled(checked: bool) -> None:
            if checked:
                for c in self.checkboxes.values():
                    c.blockSignals(True)
                    c.setChecked(False)
                    c.blockSignals(False)
                self.other_edit.setEnabled(False)
            else:
                self.other_edit.setEnabled(True)

        def _on_single_toggled(_checked: bool) -> None:
            any_chk = any(c.isChecked() for c in self.checkboxes.values()) or bool(self.other_edit.text().strip())
            if any_chk:
                self.all_cb.blockSignals(True)
                self.all_cb.setChecked(False)
                self.all_cb.blockSignals(False)
                self.other_edit.setEnabled(True)
            else:
                self.all_cb.blockSignals(True)
                self.all_cb.setChecked(True)
                self.all_cb.blockSignals(False)
                self.other_edit.setEnabled(False)

        self.all_cb.toggled.connect(_on_all_toggled)
        for c in self.checkboxes.values():
            c.toggled.connect(_on_single_toggled)
        self.other_edit.textChanged.connect(lambda: _on_single_toggled(True))

        cleaned = [str(l).strip().lower() for l in current_langs if str(l).strip()]
        if not cleaned or "all" in cleaned or "*" in cleaned:
            self.all_cb.setChecked(True)
            for c in self.checkboxes.values():
                c.setChecked(False)
            self.other_edit.setEnabled(False)
        else:
            self.all_cb.setChecked(False)
            others: List[str] = []
            for code in cleaned:
                if code in self.checkboxes:
                    self.checkboxes[code].setChecked(True)
                else:
                    others.append(code)
            if others:
                self.other_edit.setText(", ".join(others))

        btn_box = QHBoxLayout()
        btn_box.addStretch(1)
        btn_cancel = QPushButton("Huỷ")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Lưu ngôn ngữ")
        btn_save.setObjectName("primaryButton")
        set_icon(btn_save, "check", "white", 14)
        btn_save.clicked.connect(self.accept)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_save)
        layout.addLayout(btn_box)

    def get_languages(self) -> List[str]:
        if self.all_cb.isChecked():
            return []
        res: List[str] = []
        for code, cb in self.checkboxes.items():
            if cb.isChecked():
                res.append(code)
        other = self.other_edit.text().strip()
        if other:
            for p in other.split(","):
                p = p.strip().lower()
                if p and p not in res:
                    res.append(p)
        return res


class PreFlightUploadDialog(QDialog):
    """Cổng an toàn kiểm duyệt thông tin trước khi tải lên YouTube (Pre-Flight Safety Gate).

    Hiển thị thông tin trực quan và chi tiết cho từng video trong hàng đợi đăng tải:
    - Ảnh bìa (Thumbnail preview hoặc placeholder nếu chưa có)
    - Tên video & tệp MP4 nguồn (hỗ trợ tooltip đầy đủ)
    - Định tuyến ngôn ngữ & Kênh YouTube đích (kèm trạng thái kết nối tài khoản)
    - Thiết lập quyền riêng tư (Private / Unlisted / Public) với khả năng ghi đè độc lập
    - Công cụ đổi quyền riêng tư hàng loạt (Batch Override) và chọn/bỏ chọn linh hoạt
    - Thanh cảnh báo rủi ro an toàn (Safety Banner) cập nhật tức thời khi có video đặt Công khai (Public)
    - Tổng hợp dung lượng, thời lượng ước tính và tự động vô hiệu hóa nút đăng khi 0 video được chọn
    """

    def __init__(
        self,
        jobs: List[VideoJob],
        settings: Dict[str, Any],
        manager: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Xác nhận tải lên YouTube — Pre-Flight Safety Gate")
        self.resize(960, 620)
        self.setMinimumSize(820, 480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._jobs: List[VideoJob] = list(jobs)
        self._settings: Dict[str, Any] = settings or {}
        self._manager = manager
        self._row_data: List[Dict[str, Any]] = []

        self._init_ui()
        self._populate_table()
        self._update_safety_banner()

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {theme.DIALOG_BG};
                color: {theme.TEXT};
            }}
            QTableWidget {{
                background-color: {theme.TABLE_BG};
                alternate-background-color: {theme.TABLE_ALT};
                gridline-color: {theme.TABLE_GRID};
                border: 1px solid {theme.BORDER};
                border-radius: 6px;
                color: {theme.TEXT};
            }}
            QHeaderView::section {{
                background-color: {theme.TABLE_HEADER_BG};
                color: {theme.TABLE_HEADER_TEXT};
                font-weight: 600;
                font-size: 11px;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {theme.TABLE_HEADER_BORDER};
                text-transform: uppercase;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Title
        header_layout = QHBoxLayout()
        header_icon = QLabel()
        set_pixmap(header_icon, "shield-alert", "accent", 24)
        header_layout.addWidget(header_icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel("Cổng kiểm soát an toàn trước khi đăng (Pre-Flight Safety Gate)")
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {theme.TEXT};")
        subtitle_lbl = QLabel(
            "Rà soát kỹ kênh YouTube đích, ảnh thumbnail và quyền riêng tư trước khi phát hành trực tiếp."
        )
        subtitle_lbl.setStyleSheet(f"font-size: 11px; color: {theme.TEXT_MUTED};")
        title_box.addWidget(title_lbl)
        title_box.addWidget(subtitle_lbl)
        header_layout.addLayout(title_box, 1)
        layout.addLayout(header_layout)

        # Safety Banner Frame
        self.banner_frame = QFrame()
        self.banner_frame.setObjectName("safetyBanner")
        banner_layout = QHBoxLayout(self.banner_frame)
        banner_layout.setContentsMargins(12, 10, 12, 10)
        banner_layout.setSpacing(10)
        self.banner_icon = QLabel()
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        banner_layout.addWidget(self.banner_icon, 0, Qt.AlignmentFlag.AlignTop)
        banner_layout.addWidget(self.banner_text, 1)
        layout.addWidget(self.banner_frame)

        # Batch Controls Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.cb_select_all = QCheckBox(f"Chọn tất cả ({len(self._jobs)} video)")
        self.cb_select_all.setChecked(True)
        self.cb_select_all.setStyleSheet(f"font-weight: 600; color: {theme.TEXT};")
        self.cb_select_all.toggled.connect(self._on_select_all_toggled)
        toolbar.addWidget(self.cb_select_all)

        toolbar.addStretch(1)

        batch_lbl = QLabel("Đặt quyền riêng tư tất cả:")
        batch_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        toolbar.addWidget(batch_lbl)

        self.combo_batch_privacy = QComboBox()
        self.combo_batch_privacy.addItem("🔒 Riêng tư (Private)", "private")
        self.combo_batch_privacy.addItem("👁️ Không công khai (Unlisted)", "unlisted")
        self.combo_batch_privacy.addItem("🌐 Công khai (Public)", "public")
        self.combo_batch_privacy.setMinimumWidth(170)
        toolbar.addWidget(self.combo_batch_privacy)

        btn_apply_batch = QPushButton("Áp dụng tất cả")
        set_icon(btn_apply_batch, "check", "accent", 12)
        btn_apply_batch.clicked.connect(self._apply_batch_privacy)
        toolbar.addWidget(btn_apply_batch)

        layout.addLayout(toolbar)

        # Review Table
        self.table = QTableWidget(len(self._jobs), 6)
        self.table.setHorizontalHeaderLabels([
            "Chọn", "Ảnh bìa", "Tiêu đề video & Tệp gốc",
            "Kênh YouTube đích", "Quyền riêng tư", "Thời lượng & Dung lượng"
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 80)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(3, 220)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 180)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.table, 1)

        # Footer Action Bar
        footer = QHBoxLayout()
        footer.setSpacing(12)

        self.lbl_summary = QLabel()
        self.lbl_summary.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        footer.addWidget(self.lbl_summary)

        footer.addStretch(1)

        btn_cancel = QPushButton("Hủy bỏ")
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        self.btn_confirm = QPushButton("🚀 Xác nhận & Bắt đầu tải lên")
        self.btn_confirm.setObjectName("primaryButton")
        set_icon(self.btn_confirm, "upload", "white", 14)
        self.btn_confirm.clicked.connect(self.accept)
        footer.addWidget(self.btn_confirm)

        layout.addLayout(footer)

    def _populate_table(self) -> None:
        uploader = YouTubeUploader(self._settings)
        auth_channels = {ch.name: ch for ch in uploader.channels()}
        default_channel = (self._settings.get("youtube") or {}).get("channel") or "default"
        default_privacy = str((self._settings.get("youtube") or {}).get("privacy") or "private").lower()

        for row, job in enumerate(self._jobs):
            self.table.setRowHeight(row, 56)

            # Language detection
            lang = ""
            if hasattr(job, "language") and getattr(job, "language", None):
                lang = str(job.language).strip().lower()
            if not lang and job.path:
                match_stem = re.search(r"[_\-\(\[\s]([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)$", job.path.stem)
                if match_stem and normalize_language(match_stem.group(1).lower(), ""):
                    lang = match_stem.group(1).lower()
            if not lang and job.title:
                match_title = re.search(r"[\b_\-\(\[]([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)[\b_\-\)\]]", job.title)
                if match_title and normalize_language(match_title.group(1).lower(), ""):
                    lang = match_title.group(1).lower()
            if not lang and self._manager and hasattr(self._manager, "get_item") and job.items:
                item = self._manager.get_item(job.items[0])
                if item:
                    item_lang = (item.metadata or {}).get("language") or getattr(item, "language", "")
                    if item_lang:
                        lang = str(item_lang).strip().lower()
            if not lang:
                lang = (self._settings.get("youtube") or {}).get("language") or "vi"

            flag = LANGUAGE_FLAGS.get(lang, "🌐")

            # Target channel
            target_ch_name = uploader.get_channel_for_language(lang) or default_channel
            ch_info = auth_channels.get(target_ch_name)
            is_authorized = bool(ch_info and ch_info.authorized)
            ch_display_title = ch_info.title if (ch_info and ch_info.title) else target_ch_name

            # Privacy status
            initial_privacy = default_privacy
            if hasattr(job, "privacy") and getattr(job, "privacy", None):
                initial_privacy = str(job.privacy).lower()
            elif self._manager and hasattr(self._manager, "get_item") and job.items:
                item = self._manager.get_item(job.items[0])
                if item and (item.metadata or {}).get("privacy"):
                    initial_privacy = str(item.metadata.get("privacy")).lower()

            if initial_privacy not in ("public", "unlisted", "private"):
                initial_privacy = "private"

            # File size & duration
            size_mb = 0.0
            if job.path:
                try:
                    p = Path(job.path)
                    if p.is_file():
                        size_mb = p.stat().st_size / (1024 * 1024)
                except Exception:
                    pass

            dur_text = format_duration(job.duration) if getattr(job, "duration", 0) > 0 else "—"

            # Col 0: Checkbox
            cb_container = QWidget()
            cb_layout = QHBoxLayout(cb_container)
            cb_layout.setContentsMargins(6, 0, 6, 0)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb = QCheckBox()
            cb.setChecked(True)
            cb.toggled.connect(lambda _c: self._update_safety_banner())
            cb_layout.addWidget(cb)
            self.table.setCellWidget(row, 0, cb_container)

            # Col 1: Thumbnail preview
            thumb_container = QWidget()
            thumb_layout = QHBoxLayout(thumb_container)
            thumb_layout.setContentsMargins(4, 4, 4, 4)
            thumb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_label = QLabel()
            thumb_label.setFixedSize(68, 40)
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_pixmap = self._find_thumbnail_pixmap(job, lang)
            if thumb_pixmap and not thumb_pixmap.isNull():
                thumb_label.setPixmap(
                    thumb_pixmap.scaled(
                        68, 40,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                thumb_label.setStyleSheet(f"border-radius: 4px; border: 1px solid {theme.BORDER};")
            else:
                thumb_label.setText("Chưa có\nảnh bìa")
                thumb_label.setStyleSheet(
                    f"background: {theme.SURFACE_RAISED}; color: {theme.TEXT_MUTED}; "
                    f"font-size: 9px; font-weight: 500; border-radius: 4px; "
                    f"border: 1px dashed {theme.BORDER};"
                )
            thumb_layout.addWidget(thumb_label)
            self.table.setCellWidget(row, 1, thumb_container)

            # Col 2: Title & File
            info_widget = QWidget()
            info_layout = QVBoxLayout(info_widget)
            info_layout.setContentsMargins(8, 4, 8, 4)
            info_layout.setSpacing(2)
            lbl_title = QLabel(job.title or (job.path.stem if job.path else job.id))
            lbl_title.setStyleSheet(f"font-weight: 600; font-size: 12px; color: {theme.TEXT};")
            lbl_title.setToolTip(f"{job.title}\n{job.path}")
            lbl_file = QLabel(job.path.name if job.path else job.id)
            lbl_file.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_MUTED};")
            info_layout.addWidget(lbl_title)
            info_layout.addWidget(lbl_file)
            self.table.setCellWidget(row, 2, info_widget)

            # Col 3: Channel & Language
            ch_widget = QWidget()
            ch_layout = QVBoxLayout(ch_widget)
            ch_layout.setContentsMargins(8, 4, 8, 4)
            ch_layout.setSpacing(2)
            lbl_channel = QLabel(f"📺 {ch_display_title}")
            lbl_channel.setStyleSheet(f"font-weight: 600; font-size: 11px; color: {theme.TEXT};")
            lbl_channel.setToolTip(f"Kênh: {target_ch_name} ({ch_display_title})")

            auth_text = "🟢 Sẵn sàng" if is_authorized else "⚠️ Chưa kết nối"
            auth_color = theme.SUCCESS if is_authorized else theme.WARNING
            lbl_lang_auth = QLabel(f"{flag} {lang.upper()}  •  {auth_text}")
            lbl_lang_auth.setStyleSheet(f"font-size: 10px; color: {auth_color};")
            ch_layout.addWidget(lbl_channel)
            ch_layout.addWidget(lbl_lang_auth)
            self.table.setCellWidget(row, 3, ch_widget)

            # Col 4: Privacy ComboBox
            combo_container = QWidget()
            combo_layout = QHBoxLayout(combo_container)
            combo_layout.setContentsMargins(6, 6, 6, 6)
            privacy_combo = QComboBox()
            privacy_combo.addItem("🔒 Riêng tư", "private")
            privacy_combo.addItem("👁️ Không công khai", "unlisted")
            privacy_combo.addItem("🌐 Công khai", "public")
            idx = privacy_combo.findData(initial_privacy)
            if idx >= 0:
                privacy_combo.setCurrentIndex(idx)
            privacy_combo.currentIndexChanged.connect(lambda _i, r=row: self._on_row_privacy_changed(r))
            self._style_privacy_combo(privacy_combo)
            combo_layout.addWidget(privacy_combo)
            self.table.setCellWidget(row, 4, combo_container)

            # Col 5: Duration & Size
            stat_widget = QWidget()
            stat_layout = QVBoxLayout(stat_widget)
            stat_layout.setContentsMargins(6, 4, 6, 4)
            stat_layout.setSpacing(2)
            lbl_dur = QLabel(f"⏱️ {dur_text}")
            lbl_dur.setStyleSheet(f"font-size: 11px; color: {theme.TEXT};")
            lbl_size = QLabel(f"💾 {size_mb:.1f} MB" if size_mb >= 0.1 else "💾 <0.1 MB")
            lbl_size.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_MUTED};")
            stat_layout.addWidget(lbl_dur)
            stat_layout.addWidget(lbl_size)
            self.table.setCellWidget(row, 5, stat_widget)

            self._row_data.append({
                "job": job,
                "cb": cb,
                "privacy_combo": privacy_combo,
                "size_mb": size_mb,
                "target_channel": target_ch_name,
                "lang": lang,
                "is_authorized": is_authorized,
            })

    def _style_privacy_combo(self, combo: QComboBox) -> None:
        data = combo.currentData()
        if data == "public":
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {theme.SURFACE_RAISED};
                    color: {theme.WARNING};
                    border: 1px solid {theme.WARNING};
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-weight: 600;
                }}
            """)
        elif data == "unlisted":
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {theme.SURFACE};
                    color: {theme.ACCENT};
                    border: 1px solid {theme.BORDER};
                    border-radius: 4px;
                    padding: 4px 8px;
                }}
            """)
        else:
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {theme.SURFACE};
                    color: {theme.TEXT};
                    border: 1px solid {theme.BORDER};
                    border-radius: 4px;
                    padding: 4px 8px;
                }}
            """)

    def _on_row_privacy_changed(self, row: int) -> None:
        if 0 <= row < len(self._row_data):
            combo = self._row_data[row]["privacy_combo"]
            self._style_privacy_combo(combo)
        self._update_safety_banner()

    def _on_select_all_toggled(self, checked: bool) -> None:
        for r in self._row_data:
            r["cb"].blockSignals(True)
            r["cb"].setChecked(checked)
            r["cb"].blockSignals(False)
        self._update_safety_banner()

    def _apply_batch_privacy(self) -> None:
        val = self.combo_batch_privacy.currentData()
        for r in self._row_data:
            combo = r["privacy_combo"]
            idx = combo.findData(val)
            if idx >= 0:
                combo.setCurrentIndex(idx)
                self._style_privacy_combo(combo)
        self._update_safety_banner()

    def _find_thumbnail_pixmap(self, job: VideoJob, lang: str) -> Optional[QPixmap]:
        candidate_paths: List[Path] = []
        if self._manager:
            if hasattr(self._manager, "get_item") and job.items:
                item = self._manager.get_item(job.items[0])
                if item and (item.metadata or {}).get("thumbnail_path"):
                    candidate_paths.append(Path(item.metadata["thumbnail_path"]))
            pdir = getattr(self._manager, "project_dir", None)
            if pdir:
                base = job.items[0] if job.items else (job.path.stem if job.path else job.id)
                if lang:
                    candidate_paths.append(pdir / "thumbs" / lang / f"{base}.jpg")
                    candidate_paths.append(pdir / "thumbs" / lang / f"{base}.png")
                candidate_paths.append(pdir / "thumbs" / f"{base}.jpg")
                candidate_paths.append(pdir / "thumbs" / f"{base}.png")

        if job.path:
            candidate_paths.append(Path(job.path).with_suffix(".jpg"))
            candidate_paths.append(Path(job.path).with_suffix(".png"))

        global_thumb = (self._settings.get("youtube") or {}).get("thumbnail")
        if global_thumb:
            candidate_paths.append(Path(global_thumb))

        for cand in candidate_paths:
            try:
                if cand and cand.is_file():
                    pm = QPixmap(str(cand))
                    if not pm.isNull():
                        return pm
            except Exception:
                pass
        return None

    def _update_safety_banner(self) -> None:
        selected_rows = [r for r in self._row_data if r["cb"].isChecked()]
        selected_count = len(selected_rows)
        total_count = len(self._row_data)
        total_mb = sum(r["size_mb"] for r in selected_rows)
        public_count = sum(1 for r in selected_rows if r["privacy_combo"].currentData() == "public")
        unauth_count = sum(1 for r in selected_rows if not r["is_authorized"])

        if public_count > 0:
            set_pixmap(self.banner_icon, "alert-triangle", "warning", 20)
            self.banner_frame.setStyleSheet(f"""
                QFrame#safetyBanner {{
                    background-color: {theme.SURFACE_RAISED};
                    border: 1px solid {theme.WARNING};
                    border-radius: 6px;
                }}
            """)
            self.banner_text.setStyleSheet(f"color: {theme.WARNING}; font-size: 12px;")
            self.banner_text.setText(
                f"<b>⚠️ CẢNH BÁO XUẤT BẢN CÔNG KHAI:</b> Có <b>{public_count}</b> video được đặt ở chế độ <b>Công khai (Public)</b>.<br>"
                f"Video sẽ hiển thị công khai ngay lập tức trên kênh YouTube và gửi thông báo tới người đăng ký sau khi tải lên."
            )
        else:
            set_pixmap(self.banner_icon, "shield-check", "accent", 20)
            self.banner_frame.setStyleSheet(f"""
                QFrame#safetyBanner {{
                    background-color: {theme.SURFACE_RAISED};
                    border: 1px solid {theme.ACCENT};
                    border-radius: 6px;
                }}
            """)
            self.banner_text.setStyleSheet(f"color: {theme.ACCENT}; font-size: 12px;")
            self.banner_text.setText(
                f"<b>🛡️ CHẾ ĐỘ AN TOÀN:</b> Tất cả {selected_count} video đã chọn đều ở chế độ <b>Riêng tư / Không công khai</b>.<br>"
                f"Video tải lên an toàn để bạn kiểm tra nội dung và tối ưu SEO trong YouTube Studio trước khi công khai."
            )

        if unauth_count > 0:
            auth_warn = f" · <span style='color: {theme.WARNING};'>⚠️ Có {unauth_count} video gửi tới kênh chưa đăng nhập!</span>"
        else:
            auth_warn = ""

        self.lbl_summary.setText(
            f"Đã chọn: <b>{selected_count}/{total_count}</b> video · Dung lượng: <b>{total_mb:.1f} MB</b>{auth_warn}"
        )

        self.btn_confirm.setEnabled(selected_count > 0)
        if selected_count == 0:
            self.btn_confirm.setToolTip("Vui lòng chọn ít nhất 1 video để tải lên.")
        else:
            self.btn_confirm.setToolTip("")

    def apply_overrides(self) -> None:
        """Lưu lại các lựa chọn quyền riêng tư của người dùng vào Job và ProjectItem."""
        for r in self._row_data:
            if not r["cb"].isChecked():
                continue
            job: VideoJob = r["job"]
            chosen_privacy = r["privacy_combo"].currentData()
            setattr(job, "privacy", chosen_privacy)

            if self._manager and hasattr(self._manager, "get_item"):
                for base in (getattr(job, "items", None) or []):
                    item = self._manager.get_item(base)
                    if item:
                        meta = dict(getattr(item, "metadata", None) or {})
                        meta["privacy"] = chosen_privacy
                        if hasattr(self._manager, "update_item"):
                            self._manager.update_item(base, metadata=meta)

        if self._manager and hasattr(self._manager, "save_state"):
            try:
                self._manager.save_state()
            except Exception as exc:
                logger.debug("Lưu trạng thái project sau preflight: %s", exc)

    def get_confirmed_jobs(self) -> List[VideoJob]:
        """Danh sách các VideoJob đã được tích chọn để tải lên."""
        return [r["job"] for r in self._row_data if r["cb"].isChecked()]


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


class _CheckAllWorker(QThread):
    account_checked = Signal(str, dict, str)
    progress = Signal(int, int, str)
    finished_all = Signal(int, int)

    def __init__(self, settings: Dict[str, Any], channel_names: list[str], parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._names = channel_names

    def run(self) -> None:
        uploader = YouTubeUploader(self._settings)
        total = len(self._names)
        ok = 0
        fail = 0
        for i, name in enumerate(self._names):
            self.progress.emit(i + 1, total, f"Đang kiểm tra {name}...")
            try:
                info = uploader.channel_info(name)
                self.account_checked.emit(name, info, "")
                ok += 1
            except Exception as exc:
                self.account_checked.emit(name, {}, str(exc))
                fail += 1
        self.finished_all.emit(ok, fail)


class YouTubeConfigTab(QWidget):
    busy_changed = Signal(bool)
    channels_changed = Signal()
    thumbnail_saved = Signal(str)
    upload_requested = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._worker: QThread | None = None
        self._pipeline_busy = False

        self._manager = None
        self._selected_video_ids: set[str] = set()
        self._saved_selected_ids: Optional[List[str]] = None
        self._custom_videos: Dict[str, VideoJob] = {}
        self._updating_table: bool = False
        self._channel_languages: Dict[str, List[str]] = {}
        self._pending_new_account_languages: List[str] = []

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setDocumentMode(True)
        page.addWidget(self.sub_tabs)

        self.thumbnail_panel = ThumbnailPanel()
        self.thumbnail_panel.thumbnail_saved.connect(self.thumbnail_saved.emit)

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

        self.videos_box = self._build_videos_box()
        root.addWidget(self.videos_box)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self._columns = columns
        root.addLayout(columns, 1)

        left = QVBoxLayout()
        left.setSpacing(12)
        columns.addLayout(left, 1)

        # 1. Hộp chọn phương thức đăng tải
        method_box = QGroupBox("Phương thức đăng tải")
        method_layout = QVBoxLayout(method_box)
        method_layout.setSpacing(6)
        method_form = QFormLayout()
        method_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.method_combo = QComboBox()
        self.method_combo.addItem("YouTube Data API v3 (OAuth2)", "api")
        self.method_combo.addItem("Browser Automation (Playwright + Chrome)", "browser")
        method_form.addRow("Phương thức", self.method_combo)
        method_layout.addLayout(method_form)
        self.method_desc = QLabel()
        self.method_desc.setObjectName("MutedLabel")
        self.method_desc.setWordWrap(True)
        method_layout.addWidget(self.method_desc)
        left.addWidget(method_box)

        # 2. Hộp xác thực YouTube Data API (chỉ hiện khi chọn API)
        api_box = QGroupBox("Xác thực YouTube API (OAuth2)")
        self.api_box = api_box
        self.channel_box = api_box  # alias tương thích ngược
        api_layout = QVBoxLayout(api_box)
        api_layout.setSpacing(8)

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
        api_layout.addLayout(channel_row)

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
        api_layout.addLayout(new_row)

        auth_row = QHBoxLayout()
        auth_row.setSpacing(8)
        auth_row.addWidget(self.login_button)
        auth_row.addWidget(self.check_button)
        auth_row.addStretch(1)
        api_layout.addLayout(auth_row)

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
        api_layout.addWidget(secret_label)
        api_layout.addLayout(secret_row)
        secret_hint = QLabel(
            "Token được lưu riêng cho từng kênh trong config/youtube_credentials/."
        )
        secret_hint.setObjectName("MutedLabel")
        secret_hint.setWordWrap(True)
        api_layout.addWidget(secret_hint)

        self.auth_status = QLabel("Chưa kiểm tra kênh.")
        self.auth_status.setObjectName("MutedLabel")
        self.auth_status.setWordWrap(True)
        api_layout.addWidget(self.auth_status)
        left.addWidget(api_box)

        # 3. Hộp Cấu hình Trình duyệt (chỉ hiện khi chọn Browser)
        browser_box = QGroupBox("Cấu hình Trình duyệt (Playwright + Chrome)")
        self.browser_box = browser_box
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
            "Bấm Đăng nhập bằng Chrome để mở trình duyệt và đăng nhập tài khoản YouTube; "
            "phiên đăng nhập sẽ được lưu trong profile cho các lần đăng video sau."
        )
        browser_hint.setObjectName("MutedLabel")
        browser_hint.setWordWrap(True)
        browser_form.addRow("", browser_hint)
        left.addWidget(browser_box)

        # 4. Hộp Lên lịch đăng (áp dụng cho cả 2 phương thức)
        schedule_box = QGroupBox("Lên lịch đăng")
        self.schedule_box = schedule_box
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



        self.accounts_page = self._build_accounts_page()
        self.sub_tabs.addTab(wrap_scroll(self.accounts_page), "Tài khoản")
        self._on_method_changed()

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
        self._channel_languages = {}
        raw_ch_langs = yt.get("channel_languages") or {}
        if isinstance(raw_ch_langs, dict):
            for ch_name, l_list in raw_ch_langs.items():
                if isinstance(l_list, (list, tuple, set)):
                    self._channel_languages[str(ch_name)] = [str(x).strip().lower() for x in l_list if str(x).strip()]
                elif isinstance(l_list, str):
                    self._channel_languages[str(ch_name)] = [x.strip().lower() for x in l_list.split(",") if x.strip()]
        selected = yt.get("selected_videos")
        if isinstance(selected, list):
            self._saved_selected_ids = list(selected)
            self._selected_video_ids = set(selected)
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
        secret_val = str(yt.get("client_secret") or "")
        self.acc_secret_edit.setText(secret_val)
        self.refresh_accounts_table()
        if hasattr(self, "refresh_videos"):
            self.refresh_videos()

    def apply_to(self, settings: Dict[str, Any]) -> None:
        yt = settings.setdefault("youtube", {})
        yt["method"] = self.method_combo.currentData() or "api"
        yt["channel"] = self.channel_combo.currentData() or "default"
        yt["client_secret"] = (
            self.client_secret_edit.text().strip()
            or self.acc_secret_edit.text().strip()
        )
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
        yt["channel_languages"] = copy.deepcopy(self._channel_languages)
        yt["selected_videos"] = self.get_selected_video_ids()
        self.thumbnail_panel.apply_to(settings)
        self._settings = settings

    # ── Videos Selection Box ──────────────────────────────────────────────────
    def _build_videos_box(self) -> QGroupBox:
        box = QGroupBox("Chọn video để đăng tải")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 10, 12, 10)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.videos_summary = QLabel("Chưa mở project.")
        self.videos_summary.setObjectName("FieldLabel")
        top_row.addWidget(self.videos_summary, 1)

        self.btn_select_all_videos = QPushButton("Chọn tất cả")
        set_icon(self.btn_select_all_videos, "check-square", "accent", 13)
        self.btn_select_all_videos.clicked.connect(self._select_all_videos)
        top_row.addWidget(self.btn_select_all_videos)

        self.btn_deselect_all_videos = QPushButton("Bỏ chọn")
        set_icon(self.btn_deselect_all_videos, "square", "muted", 13)
        self.btn_deselect_all_videos.clicked.connect(self._deselect_all_videos)
        top_row.addWidget(self.btn_deselect_all_videos)

        self.btn_add_custom_video = QPushButton("Thêm video ngoài…")
        set_icon(self.btn_add_custom_video, "plus", "accent", 13)
        self.btn_add_custom_video.clicked.connect(self._browse_custom_videos)
        top_row.addWidget(self.btn_add_custom_video)

        self.btn_refresh_videos = QPushButton("Làm mới")
        set_icon(self.btn_refresh_videos, "rotate-cw", "accent", 13)
        self.btn_refresh_videos.clicked.connect(lambda: self.refresh_videos())
        top_row.addWidget(self.btn_refresh_videos)

        self.btn_upload_selected = QPushButton("Đăng video đã chọn")
        self.btn_upload_selected.setObjectName("primaryButton")
        set_icon(self.btn_upload_selected, "upload", "white", 14)
        self.btn_upload_selected.clicked.connect(self._on_upload_selected_clicked)
        self.btn_upload_selected.setEnabled(False)
        top_row.addWidget(self.btn_upload_selected)

        layout.addLayout(top_row)

        self.videos_table = QTableWidget(0, 6)
        self.videos_table.setHorizontalHeaderLabels([
            "Chọn", "STT", "Tên video", "Thời lượng", "File MP4", "Trạng thái YouTube"
        ])
        self.videos_table.verticalHeader().setVisible(False)
        self.videos_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.videos_table.setAlternatingRowColors(True)
        self.videos_table.setMinimumHeight(150)
        self.videos_table.setMaximumHeight(260)

        header = self.videos_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        self.videos_table.itemChanged.connect(self._on_video_item_changed)
        self.videos_table.cellDoubleClicked.connect(self._on_video_cell_double_clicked)
        layout.addWidget(self.videos_table)
        return box

    def _on_video_cell_double_clicked(self, row: int, col: int) -> None:
        chk_item = self.videos_table.item(row, 0)
        if not chk_item:
            return
        job_id = chk_item.data(Qt.ItemDataRole.UserRole)
        job = None
        if self._manager and hasattr(self._manager, "videos") and job_id in self._manager.videos:
            job = self._manager.videos[job_id]
        elif job_id in self._custom_videos:
            job = self._custom_videos[job_id]
        if not job:
            return

        if col == 0:
            new_state = (
                Qt.CheckState.Unchecked
                if chk_item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            chk_item.setCheckState(new_state)
            return

        if col == 5 and job.youtube_url:
            QDesktopServices.openUrl(QUrl(job.youtube_url))
            return

        if job.path and Path(job.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(job.path)))

    def refresh_videos(self) -> None:
        if not hasattr(self, "videos_table"):
            return

        self._updating_table = True
        self.videos_table.setRowCount(0)

        if not self._manager:
            self.videos_summary.setText("Chưa mở project.")
            self.btn_upload_selected.setEnabled(False)
            self._updating_table = False
            return

        if hasattr(self._manager, "videos"):
            if not self._manager.videos and hasattr(self._manager, "items") and self._manager.items:
                try:
                    from core.ffmpeg_renderer import FFmpegRenderer, VideoConfig
                    v_cfg = VideoConfig.from_settings(self._settings)
                    FFmpegRenderer(self._settings).build_jobs(self._manager, v_cfg, force=False)
                except Exception as exc:
                    logger.debug("Tự động lập video jobs: %s", exc)

        jobs_dict = dict(self._manager.videos) if hasattr(self._manager, "videos") else {}
        if self._custom_videos:
            for cid, cjob in self._custom_videos.items():
                if cid not in jobs_dict:
                    jobs_dict[cid] = cjob

        jobs = list(jobs_dict.values())
        jobs.sort(key=lambda j: natural_key(j.path.name if j.path else j.id))

        self.videos_table.setRowCount(len(jobs))
        ready_count = 0

        for row, job in enumerate(jobs):
            has_file = False
            size_str = ""
            if job.path:
                try:
                    p = Path(job.path)
                    if p.is_file():
                        has_file = True
                        size_mb = p.stat().st_size / (1024 * 1024)
                        size_str = f" ({size_mb:.1f} MB)" if size_mb >= 0.1 else " (<0.1 MB)"
                except Exception:
                    pass

            if has_file:
                ready_count += 1

            if self._saved_selected_ids is not None:
                checked = job.id in self._saved_selected_ids
            elif job.id in self._selected_video_ids:
                checked = True
            elif not self._selected_video_ids:
                already_uploaded = bool(job.youtube_url or job.state == VIDEO_UPLOAD_DONE)
                checked = has_file and not already_uploaded
            else:
                checked = False

            if checked:
                self._selected_video_ids.add(job.id)
            else:
                self._selected_video_ids.discard(job.id)

            chk_item = QTableWidgetItem()
            chk_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            chk_item.setData(Qt.ItemDataRole.UserRole, job.id)
            self.videos_table.setItem(row, 0, chk_item)

            stt_item = QTableWidgetItem(str(row + 1))
            stt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            stt_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.videos_table.setItem(row, 1, stt_item)

            title = job.title or (job.path.stem if job.path else job.id)
            title_item = QTableWidgetItem(title)
            title_item.setToolTip(str(job.path) if job.path else title)
            title_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.videos_table.setItem(row, 2, title_item)

            dur_text = format_duration(job.duration) if getattr(job, "duration", 0) > 0 else "—"
            dur_item = QTableWidgetItem(dur_text)
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dur_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.videos_table.setItem(row, 3, dur_item)

            if has_file:
                file_item = QTableWidgetItem(f"✓ Sẵn sàng{size_str}")
                file_item.setForeground(QColor(theme.SUCCESS))
                file_item.setToolTip(str(job.path))
            else:
                file_item = QTableWidgetItem("Chưa tạo video")
                file_item.setForeground(QColor(theme.TEXT_MUTED))
                file_item.setToolTip("File MP4 chưa được render hoặc không tồn tại.")
            file_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            file_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.videos_table.setItem(row, 4, file_item)

            if job.youtube_url or job.state == VIDEO_UPLOAD_DONE:
                yt_item = QTableWidgetItem("✓ Đã đăng")
                yt_item.setForeground(QColor(theme.ACCENT))
                if job.youtube_url:
                    yt_item.setToolTip(f"Double click để mở: {job.youtube_url}")
            elif job.state == VIDEO_UPLOADING:
                yt_item = QTableWidgetItem("Đang đăng…")
                yt_item.setForeground(QColor(theme.WARNING))
            elif job.state == VIDEO_ERROR or job.error:
                yt_item = QTableWidgetItem("Lỗi đăng")
                yt_item.setForeground(QColor(theme.DANGER))
                if job.error:
                    yt_item.setToolTip(job.error)
            else:
                yt_item = QTableWidgetItem("Chờ đăng")
                yt_item.setForeground(QColor(theme.TEXT_MUTED))
            yt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            yt_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.videos_table.setItem(row, 5, yt_item)

        self._saved_selected_ids = None
        self._updating_table = False
        self._update_videos_summary()

    def _on_video_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table or item.column() != 0:
            return
        job_id = item.data(Qt.ItemDataRole.UserRole)
        if not job_id:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_video_ids.add(job_id)
        else:
            self._selected_video_ids.discard(job_id)
        self._update_videos_summary()

    def _select_all_videos(self) -> None:
        if not hasattr(self, "videos_table"):
            return
        self._updating_table = True
        for row in range(self.videos_table.rowCount()):
            item = self.videos_table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
                job_id = item.data(Qt.ItemDataRole.UserRole)
                if job_id:
                    self._selected_video_ids.add(job_id)
        self._updating_table = False
        self._update_videos_summary()

    def _deselect_all_videos(self) -> None:
        if not hasattr(self, "videos_table"):
            return
        self._updating_table = True
        for row in range(self.videos_table.rowCount()):
            item = self.videos_table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
                job_id = item.data(Qt.ItemDataRole.UserRole)
                if job_id:
                    self._selected_video_ids.discard(job_id)
        self._updating_table = False
        self._update_videos_summary()

    def _browse_custom_videos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Chọn video ngoài để đăng tải",
            str(Path.home()),
            "Video (*.mp4 *.mkv *.mov *.webm *.avi);;Tất cả (*)",
        )
        if not paths:
            return
        for p_str in paths:
            p = Path(p_str)
            if not p.is_file():
                continue
            custom_id = f"custom::{p.stem}"
            job = VideoJob(
                id=custom_id,
                path=p,
                items=[],
                title=p.stem,
                state=VIDEO_DONE,
            )
            self._custom_videos[custom_id] = job
            if self._manager and hasattr(self._manager, "videos"):
                self._manager.videos[custom_id] = job
            self._selected_video_ids.add(custom_id)
        self.refresh_videos()

    def _update_videos_summary(self) -> None:
        if not hasattr(self, "videos_table"):
            return
        total = self.videos_table.rowCount()
        selected = len(self._selected_video_ids)
        if total == 0:
            self.videos_summary.setText("Chưa có video nào trong dự án.")
        else:
            self.videos_summary.setText(f"{selected}/{total} video được chọn để đăng tải")
        enabled = (
            selected > 0
            and not self._pipeline_busy
            and self._worker is None
        )
        self.btn_upload_selected.setEnabled(enabled)

    def get_selected_video_ids(self) -> List[str]:
        return sorted(self._selected_video_ids)

    def confirm_preflight_upload(
        self,
        job_ids: Optional[Iterable[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> Optional[List[str]]:
        """Mở PreFlightUploadDialog để kiểm tra và xác nhận trước khi đăng tải."""
        if job_ids is None:
            job_ids = self.get_selected_video_ids()
        job_ids = list(job_ids or [])
        if not job_ids:
            QMessageBox.information(
                self, "Chưa chọn video",
                "Vui lòng chọn ít nhất một video trong danh sách để đăng tải."
            )
            return None

        jobs_dict = {}
        if self._manager and hasattr(self._manager, "videos"):
            jobs_dict.update(self._manager.videos)
        jobs_dict.update(self._custom_videos)

        valid_jobs: List[VideoJob] = []
        missing_files: List[str] = []
        for vid in job_ids:
            job = jobs_dict.get(vid)
            if job and job.path and Path(job.path).is_file():
                valid_jobs.append(job)
            else:
                title = job.title if job else vid
                missing_files.append(title)

        if not valid_jobs:
            QMessageBox.warning(
                self, "Chưa có file video",
                "Không có video nào trong số mục đã chọn có file MP4 trên đĩa.\n"
                "Vui lòng tạo video trước khi đăng tải."
            )
            return None

        if missing_files:
            reply = QMessageBox.question(
                self, "Một số video chưa có file",
                f"{len(missing_files)} video chưa có file MP4 trên đĩa:\n"
                f"• " + "\n• ".join(missing_files[:5]) +
                (f"\n... và {len(missing_files) - 5} video khác." if len(missing_files) > 5 else "") +
                f"\n\nBạn có muốn tiếp tục đăng tải {len(valid_jobs)} video đã có file?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return None

        dialog = PreFlightUploadDialog(
            valid_jobs,
            self._settings,
            self._manager,
            parent=parent or self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            logger.info("Người dùng đã hủy đăng tải tại PreFlightUploadDialog.")
            return None

        confirmed_jobs = dialog.get_confirmed_jobs()
        if not confirmed_jobs:
            logger.info("Không có video nào được chọn sau khi duyệt PreFlightUploadDialog.")
            return None

        dialog.apply_overrides()
        return [j.id for j in confirmed_jobs]

    def _on_upload_selected_clicked(self) -> None:
        confirmed_ids = self.confirm_preflight_upload()
        if confirmed_ids:
            self.upload_requested.emit(confirmed_ids)

    def set_project(self, manager) -> None:
        self._manager = manager
        self.thumbnail_panel.set_project(manager)
        self.refresh_videos()

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ── UI behaviour ─────────────────────────────────────────────────────────
    def _on_method_changed(self) -> None:
        browser = self.method_combo.currentData() == "browser"
        if hasattr(self, "api_box"):
            self.api_box.setVisible(not browser)
        if hasattr(self, "browser_box"):
            self.browser_box.setVisible(browser)
        if hasattr(self, "method_desc"):
            if browser:
                self.method_desc.setText(
                    "Đang dùng Browser Automation: Đăng tải trực tiếp qua trình duyệt Chrome "
                    "(YouTube Studio), không cần file client_secret.json."
                )
            else:
                self.method_desc.setText(
                    "Đang dùng YouTube Data API v3: Đăng tải chính thức qua Google OAuth2 "
                    "(yêu cầu file client_secret.json và tài khoản được cấp quyền)."
                )

        self.browser_profile_edit.setEnabled(browser)
        self.headless_check.setEnabled(browser)
        self.browser_login_button.setVisible(browser)
        self.browser_status.setVisible(browser)
        enabled = self._worker is None and not self._pipeline_busy
        self.browser_login_button.setEnabled(enabled)
        self.login_button.setEnabled(enabled)
        self.check_button.setEnabled(enabled)

        if hasattr(self, "sub_tabs") and self.sub_tabs.count() >= 2:
            acc_tab_idx = self.sub_tabs.count() - 1
            self.sub_tabs.setTabVisible(acc_tab_idx, not browser)
            if browser and self.sub_tabs.currentIndex() == acc_tab_idx:
                self.sub_tabs.setCurrentIndex(0)

        self.channels_changed.emit()

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
            self.refresh_accounts_table()

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
        if hasattr(self, "acc_add_btn"):
            self.acc_add_btn.setEnabled(not busy)
        if hasattr(self, "acc_check_all_btn"):
            self.acc_check_all_btn.setEnabled(not busy)
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
        if hasattr(self, "btn_upload_selected"):
            self.btn_upload_selected.setEnabled(
                not busy and bool(self._selected_video_ids) and self._worker is None
            )
        if hasattr(self, "btn_refresh_videos"):
            self.btn_refresh_videos.setEnabled(not busy)

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

    # ── Accounts Management Page ──────────────────────────────────────────────
    def _build_accounts_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageContent")
        root = QVBoxLayout(page)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        root.addWidget(
            PageHeader(
                "Quản lý tài khoản YouTube",
                "Quản lý danh sách kênh YouTube, thêm tài khoản OAuth mới, kiểm tra trạng thái hoạt động và xoá tài khoản.",
            )
        )

        add_box = QGroupBox("Thêm tài khoản mới")
        add_layout = QVBoxLayout(add_box)
        add_layout.setSpacing(10)

        add_form = QFormLayout()
        add_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.acc_name_edit = QLineEdit()
        self.acc_name_edit.setPlaceholderText("Tên định danh kênh (ví dụ: KenhChinh, ReviewPhim, Channel2...)")
        add_form.addRow("Tên nhận diện", self.acc_name_edit)

        secret_row = QHBoxLayout()
        secret_row.setSpacing(6)
        self.acc_secret_edit = QLineEdit()
        self.acc_secret_edit.setPlaceholderText("config/youtube_credentials/client_secret.json (mặc định)")
        secret_browse = icon_button(
            "file", "Chọn client_secret.json…", role="muted", size=15, side=30
        )
        secret_browse.clicked.connect(self._browse_acc_secret)
        secret_row.addWidget(self.acc_secret_edit, 1)
        secret_row.addWidget(secret_browse)
        add_form.addRow("client_secret.json", self._wrap(secret_row))

        langs_box = QWidget()
        langs_vbox = QVBoxLayout(langs_box)
        langs_vbox.setContentsMargins(0, 0, 0, 0)
        langs_vbox.setSpacing(6)

        self.acc_lang_grid = QGridLayout()
        self.acc_lang_grid.setContentsMargins(0, 0, 0, 0)
        self.acc_lang_grid.setHorizontalSpacing(12)
        self.acc_lang_grid.setVerticalSpacing(6)
        self.acc_lang_all_cb = QCheckBox("🌐 Tất cả")
        self.acc_lang_all_cb.setChecked(True)
        self.acc_lang_checkboxes: Dict[str, QCheckBox] = {}
        langs_vbox.addLayout(self.acc_lang_grid)

        langs_row2 = QHBoxLayout()
        langs_row2.setSpacing(6)
        langs_row2.addWidget(QLabel("Mã khác:"))
        self.acc_lang_other_edit = QLineEdit()
        self.acc_lang_other_edit.setPlaceholderText("vi, en, ja, zh, es, th... (phân cách bằng dấu phẩy)")
        self.acc_lang_other_edit.setMaximumWidth(320)
        langs_row2.addWidget(self.acc_lang_other_edit)
        langs_row2.addStretch(1)
        langs_vbox.addLayout(langs_row2)

        self.acc_lang_all_cb.toggled.connect(self._on_acc_lang_all_toggled)
        self.acc_lang_other_edit.textChanged.connect(lambda: self._on_acc_lang_item_toggled(True))
        self.acc_lang_other_edit.setEnabled(False)
        self.sync_languages()

        add_form.addRow("Ngôn ngữ chỉ định", self._wrap(langs_vbox))

        add_layout.addLayout(add_form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.acc_add_btn = QPushButton("Đăng nhập / Thêm tài khoản")
        self.acc_add_btn.setObjectName("primaryButton")
        self.acc_add_btn.setMinimumHeight(38)
        set_icon(self.acc_add_btn, "key", "white", 14)
        self.acc_add_btn.clicked.connect(self._add_account)
        btn_row.addWidget(self.acc_add_btn)

        self.acc_add_status = QLabel("")
        self.acc_add_status.setWordWrap(True)
        btn_row.addWidget(self.acc_add_status, 1)
        add_layout.addLayout(btn_row)

        root.addWidget(add_box)

        list_box = QGroupBox("Danh sách tài khoản đã lưu")
        list_layout = QVBoxLayout(list_box)
        list_layout.setSpacing(10)

        top_actions = QHBoxLayout()
        top_actions.setSpacing(8)

        self.acc_check_all_btn = QPushButton("Kiểm tra tất cả")
        set_icon(self.acc_check_all_btn, "check-circle", "accent", 14)
        self.acc_check_all_btn.clicked.connect(self._check_all_accounts)
        top_actions.addWidget(self.acc_check_all_btn)

        self.acc_refresh_btn = icon_button(
            "refresh", "Quét lại danh sách token đã lưu", role="muted", size=14, side=30
        )
        self.acc_refresh_btn.clicked.connect(lambda: self.refresh_accounts_table())
        top_actions.addWidget(self.acc_refresh_btn)

        self.acc_table_status = QLabel("")
        self.acc_table_status.setWordWrap(True)
        top_actions.addWidget(self.acc_table_status, 1)
        list_layout.addLayout(top_actions)

        self.acc_table = QTableWidget()
        self.acc_table.setColumnCount(7)
        self.acc_table.setHorizontalHeaderLabels([
            "Tên định danh",
            "Kênh YouTube",
            "Channel ID",
            "Ngôn ngữ chỉ định",
            "Trạng thái",
            "Thông tin chi tiết",
            "Thao tác",
        ])
        header = self.acc_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.acc_table.setColumnWidth(3, 160)
        self.acc_table.setColumnWidth(6, 330)
        self.acc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.acc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.acc_table.verticalHeader().setVisible(False)
        self.acc_table.verticalHeader().setDefaultSectionSize(48)
        self.acc_table.setAlternatingRowColors(True)
        self.acc_table.setMinimumHeight(260)
        self.acc_table.cellDoubleClicked.connect(self._on_table_double_clicked)
        list_layout.addWidget(self.acc_table, 1)

        root.addWidget(list_box, 1)
        return page

    def _on_acc_lang_all_toggled(self, checked: bool) -> None:
        if checked:
            for c in self.acc_lang_checkboxes.values():
                c.blockSignals(True)
                c.setChecked(False)
                c.blockSignals(False)
            self.acc_lang_other_edit.setEnabled(False)
        else:
            self.acc_lang_other_edit.setEnabled(True)

    def _on_acc_lang_item_toggled(self, _checked: bool) -> None:
        any_checked = any(c.isChecked() for c in self.acc_lang_checkboxes.values()) or bool(self.acc_lang_other_edit.text().strip())
        if any_checked:
            self.acc_lang_all_cb.blockSignals(True)
            self.acc_lang_all_cb.setChecked(False)
            self.acc_lang_all_cb.blockSignals(False)
            self.acc_lang_other_edit.setEnabled(True)
        else:
            self.acc_lang_all_cb.blockSignals(True)
            self.acc_lang_all_cb.setChecked(True)
            self.acc_lang_all_cb.blockSignals(False)
            self.acc_lang_other_edit.setEnabled(False)

    def sync_languages(self, profiles: Optional[List[Any]] = None) -> None:
        self._last_profiles = profiles
        checked_before = {
            code: cb.isChecked() for code, cb in self.acc_lang_checkboxes.items()
        }
        items = _configured_languages(profiles)
        self.acc_lang_grid.removeWidget(self.acc_lang_all_cb)
        while self.acc_lang_grid.count():
            child = self.acc_lang_grid.takeAt(0)
            if child.widget() and child.widget() is not self.acc_lang_all_cb:
                child.widget().deleteLater()
        self.acc_lang_checkboxes.clear()

        cols_per_row = 5
        self.acc_lang_grid.addWidget(self.acc_lang_all_cb, 0, 0)
        for idx, (code, name) in enumerate(items, start=1):
            flag = LANGUAGE_FLAGS.get(code, "🌐")
            cb = QCheckBox(f"{flag} {code.upper()}")
            cb.setToolTip(f"{name} ({code})" if code not in name else name)
            cb.setChecked(checked_before.get(code, False))
            cb.toggled.connect(self._on_acc_lang_item_toggled)
            self.acc_lang_checkboxes[code] = cb
            self.acc_lang_grid.addWidget(cb, idx // cols_per_row, idx % cols_per_row)
        self.acc_lang_grid.setColumnStretch(cols_per_row, 1)
        if hasattr(self, "thumbnail_panel"):
            self.thumbnail_panel.sync_languages(profiles)

    def _browse_acc_secret(self) -> None:
        start = self.acc_secret_edit.text().strip() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Chọn client_secret.json", start, "JSON (*.json);;Tất cả (*)"
        )
        if chosen:
            self.acc_secret_edit.setText(chosen)
            self.client_secret_edit.setText(chosen)

    def get_selected_new_account_languages(self) -> List[str]:
        if self.acc_lang_all_cb.isChecked():
            return []
        langs: List[str] = []
        for code, cb in self.acc_lang_checkboxes.items():
            if cb.isChecked():
                langs.append(code)
        other = self.acc_lang_other_edit.text().strip()
        if other:
            for part in other.split(","):
                part = part.strip().lower()
                if part and part not in langs:
                    langs.append(part)
        return langs

    def _add_account(self) -> None:
        if self._worker is not None:
            return
        name = self.acc_name_edit.text().strip()
        if not name:
            paint_status(self.acc_add_status, "error")
            self.acc_add_status.setText("Vui lòng nhập tên nhận diện kênh (ví dụ: KenhChinh).")
            return
        self._pending_new_account_languages = self.get_selected_new_account_languages()
        snapshot = self._snapshot()
        secret = self.acc_secret_edit.text().strip() or self.client_secret_edit.text().strip()
        if secret:
            snapshot["youtube"]["client_secret"] = secret
        snapshot["youtube"]["channel"] = name

        self._worker = _AuthWorker(snapshot, name, self)
        self._worker.progress.connect(
            lambda _pct, msg: self._on_acc_auth_progress(msg)
        )
        self._worker.done.connect(lambda ch: self._on_acc_auth_done(ch, name))
        self._worker.failed.connect(self._on_acc_auth_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._set_busy(True, f"Đang mở trình duyệt đăng nhập cho tài khoản '{name}'…")
        paint_status(self.acc_add_status, "running")
        self.acc_add_status.setText(f"Đang mở trình duyệt đăng nhập cho '{name}'…")
        self._worker.start()

    def _on_acc_auth_progress(self, msg: str) -> None:
        paint_status(self.acc_add_status, "running")
        self.acc_add_status.setText(msg)

    def _on_acc_auth_done(self, channel: Any, requested_name: str) -> None:
        title = getattr(channel, "title", "") or getattr(channel, "name", "") or requested_name
        paint_status(self.acc_add_status, "success")
        self.acc_add_status.setText(f"Đã thêm và kết nối tài khoản thành công: {title}")
        self.acc_name_edit.clear()
        langs = getattr(self, "_pending_new_account_languages", [])
        self._save_channel_languages(requested_name, langs)
        self.acc_lang_all_cb.setChecked(True)
        self.acc_lang_other_edit.clear()
        self.refresh_accounts_table()
        self.refresh_channels(select=requested_name)
        self.channels_changed.emit()

    def _on_acc_auth_failed(self, message: str) -> None:
        paint_status(self.acc_add_status, "error")
        self.acc_add_status.setText(f"Đăng nhập thất bại: {message}")
        self._on_auth_failed(message)

    def _reauth_account(self, name: str) -> None:
        self.acc_name_edit.setText(name)
        langs = self._channel_languages.get(name, [])
        if not langs or "all" in langs or "*" in langs:
            self.acc_lang_all_cb.setChecked(True)
            for c in self.acc_lang_checkboxes.values():
                c.setChecked(False)
            self.acc_lang_other_edit.clear()
        else:
            self.acc_lang_all_cb.setChecked(False)
            others: List[str] = []
            for code in langs:
                if code in self.acc_lang_checkboxes:
                    self.acc_lang_checkboxes[code].setChecked(True)
                else:
                    others.append(code)
            self.acc_lang_other_edit.setText(", ".join(others) if others else "")
        self._add_account()

    def _delete_account(self, name: str) -> None:
        reply = QMessageBox.question(
            self,
            "Xác nhận xoá tài khoản",
            f"Bạn có chắc muốn xoá tài khoản '{name}' không?\n"
            "File token và metadata lưu trữ sẽ bị xoá vĩnh viễn.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        uploader = YouTubeUploader(self._settings)
        deleted = uploader.delete_channel(name)
        self._channel_languages.pop(name, None)
        yt_cfg = self._settings.setdefault("youtube", {})
        yt_cfg.setdefault("channel_languages", {}).pop(name, None)
        if yt_cfg.get("channel") == name:
            yt_cfg["channel"] = ""
        try:
            from core.settings import save_settings
            save_settings(self._settings)
        except Exception as exc:
            logger.warning("Không thể lưu settings sau khi xoá kênh: %s", exc)

        self.refresh_accounts_table()
        self.refresh_channels()
        self.channels_changed.emit()
        paint_status(self.acc_table_status, "success")
        self.acc_table_status.setText(f"Đã xoá tài khoản '{name}'.")

    def _check_single_account(self, name: str, row: int) -> None:
        if self._worker is not None:
            paint_status(self.acc_table_status, "warning")
            self.acc_table_status.setText("Đang có tiến trình khác đang chạy, vui lòng chờ…")
            return
        snapshot = self._snapshot()
        secret = self.acc_secret_edit.text().strip() or self.client_secret_edit.text().strip()
        if secret:
            snapshot["youtube"]["client_secret"] = secret
        snapshot["youtube"]["channel"] = name

        self._worker = _CheckWorker(snapshot, name, self)
        self._worker.done.connect(lambda info: self._on_single_check_done(name, info))
        self._worker.failed.connect(lambda err: self._on_single_check_failed(name, err))
        self._worker.finished.connect(self._on_worker_finished)
        self._set_busy(True)
        paint_status(self.acc_table_status, "running")
        self.acc_table_status.setText(f"Đang kiểm tra tài khoản '{name}'…")
        self._worker.start()

    def _on_single_check_done(self, name: str, info: dict) -> None:
        self._update_account_row(name, info, "")
        paint_status(self.acc_table_status, "success")
        title = info.get("title", "")
        self.acc_table_status.setText(f"Tài khoản '{name}' hoạt động tốt · Kênh: {title}")
        self.refresh_channels(select=name)

    def _on_single_check_failed(self, name: str, message: str) -> None:
        self._update_account_row(name, {}, message)
        paint_status(self.acc_table_status, "error")
        self.acc_table_status.setText(f"Tài khoản '{name}' kiểm tra thất bại: {message}")

    def _update_account_row(self, name: str, info: dict, error: str = "") -> None:
        for row in range(self.acc_table.rowCount()):
            item = self.acc_table.item(row, 0)
            if item and item.text() == name:
                if error:
                    st_item = self.acc_table.item(row, 4)
                    if st_item:
                        st_item.setText("🔴 Không hoạt động / Hết hạn")
                    inf_item = self.acc_table.item(row, 5)
                    if inf_item:
                        inf_item.setText(error)
                        inf_item.setToolTip(error)
                else:
                    title = info.get("title", "")
                    cid = info.get("channel_id", "")
                    subs = info.get("subscribers", "?")
                    vids = info.get("videos", "?")
                    if title:
                        self.acc_table.setItem(row, 1, QTableWidgetItem(title))
                    if cid:
                        item_cid = QTableWidgetItem(cid)
                        item_cid.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        self.acc_table.setItem(row, 2, item_cid)
                    st_item = self.acc_table.item(row, 4)
                    if st_item:
                        st_item.setText("🟢 Đang hoạt động")
                    inf_item = self.acc_table.item(row, 5)
                    if inf_item:
                        inf_item.setText(f"{subs} người đăng ký · {vids} video")
                        inf_item.setToolTip(f"Kênh: {title}\nID: {cid}\nSubscribers: {subs}\nVideos: {vids}")
                break

    def _check_all_accounts(self) -> None:
        if self._worker is not None:
            return
        names = []
        for r in range(self.acc_table.rowCount()):
            it = self.acc_table.item(r, 0)
            if it and it.text():
                names.append(it.text())
        if not names:
            paint_status(self.acc_table_status, "muted")
            self.acc_table_status.setText("Chưa có tài khoản nào trong danh sách để kiểm tra.")
            return

        snapshot = self._snapshot()
        secret = self.acc_secret_edit.text().strip() or self.client_secret_edit.text().strip()
        if secret:
            snapshot["youtube"]["client_secret"] = secret

        self._worker = _CheckAllWorker(snapshot, names, self)
        self._worker.account_checked.connect(self._on_check_all_single)
        self._worker.progress.connect(self._on_check_all_progress)
        self._worker.finished_all.connect(self._on_check_all_finished)
        self._worker.finished.connect(self._on_worker_finished)
        self._set_busy(True)
        paint_status(self.acc_table_status, "running")
        self.acc_table_status.setText(f"Bắt đầu kiểm tra {len(names)} tài khoản…")
        self._worker.start()

    def _on_check_all_progress(self, current: int, total: int, msg: str) -> None:
        paint_status(self.acc_table_status, "running")
        self.acc_table_status.setText(f"[{current}/{total}] {msg}")

    def _on_check_all_single(self, name: str, info: dict, err: str) -> None:
        self._update_account_row(name, info, err)

    def _on_check_all_finished(self, ok: int, fail: int) -> None:
        paint_status(self.acc_table_status, "success" if fail == 0 else "warning")
        self.acc_table_status.setText(f"Kiểm tra xong: {ok} hoạt động, {fail} lỗi.")
        self.refresh_channels()

    def _on_table_double_clicked(self, row: int, _column: int) -> None:
        it = self.acc_table.item(row, 0)
        if it and it.text():
            self._open_assign_languages(it.text())

    def _open_assign_languages(self, channel_name: str) -> None:
        current = self._channel_languages.get(channel_name)
        if current is None:
            try:
                for ch in YouTubeUploader(self._settings).channels():
                    if ch.name == channel_name:
                        current = ch.languages
                        break
            except Exception:
                pass
        current = current or []
        dlg = AssignLanguagesDialog(
            channel_name,
            current,
            self,
            profiles=getattr(self, "_last_profiles", None),
        )
        if dlg.exec():
            new_langs = dlg.get_languages()
            self._save_channel_languages(channel_name, new_langs)
            self.refresh_accounts_table()
            self.channels_changed.emit()
            paint_status(self.acc_table_status, "success")
            disp = ", ".join(new_langs) if new_langs else "Tất cả"
            self.acc_table_status.setText(f"Đã gán ngôn ngữ cho kênh '{channel_name}': {disp}")

    def _save_channel_languages(self, channel_name: str, langs: List[str]) -> None:
        self._channel_languages[channel_name] = langs
        yt = self._settings.setdefault("youtube", {})
        yt.setdefault("channel_languages", {})[channel_name] = langs
        try:
            uploader = YouTubeUploader(self._settings)
            uploader.save_channel_languages(channel_name, langs)
        except Exception as exc:
            logger.warning("Không lưu được languages vào metadata sidecar: %s", exc)

    def refresh_accounts_table(self) -> None:
        if not self._settings:
            return
        try:
            channels = YouTubeUploader(self._settings).channels()
        except Exception as exc:
            logger.error("Không đọc được danh sách kênh: %s", exc)
            channels = []

        self.acc_table.setRowCount(len(channels))
        for row, ch in enumerate(channels):
            item_name = QTableWidgetItem(ch.name)
            item_name.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_name.setToolTip(str(ch.token_path))
            self.acc_table.setItem(row, 0, item_name)

            item_title = QTableWidgetItem(ch.title or "(Chưa đồng bộ)")
            self.acc_table.setItem(row, 1, item_title)

            item_id = QTableWidgetItem(ch.channel_id or "-")
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.acc_table.setItem(row, 2, item_id)

            ch_langs = self._channel_languages.get(ch.name)
            if ch_langs is None:
                ch_langs = ch.languages or []
                self._channel_languages[ch.name] = list(ch_langs)
            summary_langs = ", ".join(ch_langs) if ch_langs else "Tất cả"
            item_lang = QTableWidgetItem(summary_langs)
            item_lang.setForeground(QColor(0, 0, 0, 0))  # Transparent text to avoid double-text overlap behind chips
            item_lang.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_lang.setToolTip(f"Ngôn ngữ phụ trách: {summary_langs}")
            self.acc_table.setItem(row, 3, item_lang)
            self.acc_table.setCellWidget(row, 3, _make_language_chips_widget(ch_langs))

            if ch.authorized:
                status_text = "🟢 Đang hoạt động" if ch.title else "🟢 Đã uỷ quyền"
            else:
                status_text = "🔴 Chưa đăng nhập / Hết hạn"
            item_status = QTableWidgetItem(status_text)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.acc_table.setItem(row, 4, item_status)

            info_text = f"Cập nhật: {ch.updated_at}" if ch.updated_at else "Bấm 'Kiểm tra' để xem thông tin"
            item_info = QTableWidgetItem(info_text)
            self.acc_table.setItem(row, 5, item_info)

            actions_widget = QWidget()
            act_layout = QHBoxLayout(actions_widget)
            act_layout.setContentsMargins(6, 4, 6, 4)
            act_layout.setSpacing(6)
            act_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            btn_style = "QPushButton { padding: 4px 8px; font-size: 8.5pt; max-height: 26px; border-radius: 6px; }"

            ch_name = ch.name
            btn_chk = QPushButton("Kiểm tra")
            btn_chk.setObjectName("smallButton")
            btn_chk.setStyleSheet(btn_style)
            set_icon(btn_chk, "check-circle", "accent", 12)
            btn_chk.setToolTip("Kiểm tra xem token còn hoạt động không")
            btn_chk.clicked.connect(lambda _, n=ch_name, r=row: self._check_single_account(n, r))
            act_layout.addWidget(btn_chk)

            btn_lang = QPushButton("Gán ngôn ngữ")
            btn_lang.setObjectName("smallButton")
            btn_lang.setStyleSheet(btn_style)
            set_icon(btn_lang, "globe", "accent", 12)
            btn_lang.setToolTip("Đổi ngôn ngữ chỉ định cho kênh này")
            btn_lang.clicked.connect(lambda _, n=ch_name: self._open_assign_languages(n))
            act_layout.addWidget(btn_lang)

            btn_auth = QPushButton("Cấp lại")
            btn_auth.setObjectName("smallButton")
            btn_auth.setStyleSheet(btn_style)
            set_icon(btn_auth, "key", "muted", 12)
            btn_auth.setToolTip("Đăng nhập và cấp quyền lại cho kênh này")
            btn_auth.clicked.connect(lambda _, n=ch_name: self._reauth_account(n))
            act_layout.addWidget(btn_auth)

            btn_del = QPushButton("Xoá")
            btn_del.setObjectName("smallButton")
            btn_del.setStyleSheet(btn_style)
            set_icon(btn_del, "trash", "danger", 12)
            btn_del.setToolTip("Xoá tài khoản này khỏi AutoYout")
            btn_del.clicked.connect(lambda _, n=ch_name: self._delete_account(n))
            act_layout.addWidget(btn_del)

            item_act = QTableWidgetItem("")
            self.acc_table.setItem(row, 6, item_act)
            self.acc_table.setCellWidget(row, 6, actions_widget)
            self.acc_table.setRowHeight(row, 48)
