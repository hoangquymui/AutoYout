"""Video tab: background visuals, BGM and encoder options."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.ai_images import (
    DEFAULT_MODEL as AI_DEFAULT_MODEL,
    DEFAULT_STYLE as AI_DEFAULT_STYLE,
)
from ui.tab_frame import FrameTab
from ui import theme
from ui.components.icons import pixmap, set_icon
from ui.components.page import (
    PageHeader,
    icon_button,
    paint_status,
    stack_columns,
    wrap_scroll,
)

_IMAGE_FILTER = "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp);;Tất cả (*)"
_VIDEO_FILTER = "Video (*.mp4 *.mov *.mkv *.webm *.avi);;Tất cả (*)"
_AUDIO_FILTER = "Âm thanh (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;Tất cả (*)"
_SRT_FILTER = "Phụ đề (*.srt *.vtt);;Tất cả (*)"
_EXE_FILTER = "Chương trình (*.exe);;Tất cả (*)"

_ENCODER_LABELS = {
    "auto": "Tự động (GPU nếu có)",
    "h264_nvenc": "NVIDIA NVENC (h264_nvenc)",
    "h264_amf": "AMD AMF (h264_amf)",
    "h264_qsv": "Intel QSV (h264_qsv)",
    "libx264": "CPU (libx264)",
}


class VideoConfigTab(QWidget):
    tools_changed = Signal()
    run_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}
        self._pipeline_busy = False
        self._manager = None
        self._files_expanded = False
        self._ready_count = 0
        self._total_count = 0
        self.frame_tab = FrameTab(self)

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        self.inner_tabs = QTabWidget(self)
        self.inner_tabs.setDocumentMode(True)
        page.addWidget(self.inner_tabs, 1)

        config_page = QWidget()
        config_page.setObjectName("PageContent")
        config_layout = QVBoxLayout(config_page)
        config_layout.setContentsMargins(14, 10, 14, 10)
        config_layout.setSpacing(10)

        self.inner_tabs.addTab(config_page, "Cấu hình video")
        self.inner_tabs.addTab(self.frame_tab, "Khung mẫu (Video Frame)")

        config_layout.addWidget(PageHeader(
            "Kết xuất Video",
            "Chọn nguồn hình, âm thanh và cách ghép — rồi bấm Tạo video ở thanh dưới.",
        ))

        self.files_box = self._build_files_box()
        config_layout.addWidget(self.files_box)

        content = QWidget()
        content.setObjectName("PageContent")
        scroller = wrap_scroll(content)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        config_layout.addWidget(scroller, 1)

        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self._columns = columns
        root.addLayout(columns)

        left = QVBoxLayout()
        left.setSpacing(12)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        columns.addLayout(right, 1)

        # ── Card: Hình ảnh & nền ─────────────────────────────────────────────
        visual_box = QGroupBox("Hình ảnh và nền")
        visual_layout = QVBoxLayout(visual_box)
        visual_layout.setSpacing(8)
        self.visual_combo = QComboBox()
        self.visual_combo.addItem("Một ảnh tĩnh", "image")
        self.visual_combo.addItem("Trình chiếu nhiều ảnh (lặp)", "slideshow")
        self.visual_combo.addItem("Video lặp lại", "video")
        self.visual_combo.addItem("Video ngẫu nhiên từ thư mục (pool)", "video_pool")
        self.visual_combo.addItem("Ảnh AI theo chương", "ai")
        visual_layout.addWidget(self.visual_combo)
        self.visual_stack = QStackedWidget()
        visual_layout.addWidget(self.visual_stack)

        image_page = QWidget()
        image_form = QFormLayout(image_page)
        image_form.setContentsMargins(0, 0, 0, 0)
        image_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.image_edit = QLineEdit()
        self.image_edit.setPlaceholderText("Chọn ảnh .jpg/.png…")
        image_row = QHBoxLayout()
        image_row.setSpacing(6)
        image_row.addWidget(self.image_edit, 1)
        image_browse = icon_button(
            "image", "Chọn ảnh nền…", role="muted", size=15, side=30
        )
        image_browse.clicked.connect(
            lambda: self._browse_into(self.image_edit, "Chọn ảnh nền", _IMAGE_FILTER)
        )
        image_row.addWidget(image_browse)
        image_form.addRow("Ảnh", self._wrap(image_row))
        self.visual_stack.addWidget(image_page)

        slideshow_page = QWidget()
        slide_form = QFormLayout(slideshow_page)
        slide_form.setContentsMargins(0, 0, 0, 0)
        slide_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.slideshow_edit = QLineEdit()
        self.slideshow_edit.setPlaceholderText("Thư mục chứa nhiều ảnh…")
        slide_row = QHBoxLayout()
        slide_row.setSpacing(6)
        slide_row.addWidget(self.slideshow_edit, 1)
        slide_browse = icon_button(
            "folder", "Chọn thư mục chứa ảnh…", role="muted", size=15, side=30
        )
        slide_browse.clicked.connect(self._browse_slideshow)
        slide_row.addWidget(slide_browse)
        slide_form.addRow("Thư mục ảnh", self._wrap(slide_row))
        self.slide_seconds_spin = QDoubleSpinBox()
        self.slide_seconds_spin.setRange(1.0, 60.0)
        self.slide_seconds_spin.setSingleStep(0.5)
        self.slide_seconds_spin.setDecimals(1)
        self.slide_seconds_spin.setSuffix(" giây/ảnh")
        slide_form.addRow("Thời gian mỗi ảnh", self.slide_seconds_spin)
        self.visual_stack.addWidget(slideshow_page)

        video_page = QWidget()
        video_form = QFormLayout(video_page)
        video_form.setContentsMargins(0, 0, 0, 0)
        video_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.video_list = QListWidget()
        self.video_list.setMinimumHeight(92)
        self.video_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        video_form.addRow("Video nền", self.video_list)
        video_buttons = QHBoxLayout()
        video_buttons.setSpacing(6)
        add_video = QPushButton("Thêm video")
        set_icon(add_video, "plus", "accent", 14)
        add_video.clicked.connect(self._add_video_files)
        remove_video = QPushButton("Xoá")
        set_icon(remove_video, "trash", "muted", 13)
        remove_video.clicked.connect(self._remove_video_file)
        video_buttons.addWidget(add_video)
        video_buttons.addWidget(remove_video)
        video_buttons.addStretch(1)
        video_form.addRow("", self._wrap(video_buttons))
        self.visual_stack.addWidget(video_page)

        pool_page = QWidget()
        pool_form = QFormLayout(pool_page)
        pool_form.setContentsMargins(0, 0, 0, 0)
        pool_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.pool_dir_edit = QLineEdit()
        self.pool_dir_edit.setPlaceholderText("Thư mục clip .mp4 (mặc định assets/video_pool)")
        pool_row = QHBoxLayout()
        pool_row.setSpacing(6)
        pool_row.addWidget(self.pool_dir_edit, 1)
        pool_browse = icon_button(
            "folder", "Chọn thư mục video pool…", role="muted", size=15, side=30
        )
        pool_browse.clicked.connect(
            lambda: self._browse_folder_into(
                self.pool_dir_edit, "Chọn thư mục video pool")
        )
        pool_row.addWidget(pool_browse)
        pool_form.addRow("Thư mục pool", self._wrap(pool_row))
        self.pool_count_spin = QSpinBox()
        self.pool_count_spin.setRange(0, 200)
        self.pool_count_spin.setSpecialValueText("Tất cả clip")
        pool_form.addRow("Số clip mỗi video", self.pool_count_spin)
        pool_hint = QLabel(
            "Mỗi lần render chọn ngẫu nhiên clip từ thư mục, ghép nối và lặp "
            "đến khi hết audio. Đặt 0 để dùng toàn bộ clip."
        )
        pool_hint.setObjectName("MutedLabel")
        pool_hint.setWordWrap(True)
        pool_form.addRow("", pool_hint)
        self.visual_stack.addWidget(pool_page)

        ai_page = QWidget()
        ai_form = QFormLayout(ai_page)
        ai_form.setContentsMargins(0, 0, 0, 0)
        ai_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItem("Thủ công (ảnh có sẵn)", "manual")
        self.ai_mode_combo.addItem("Google Imagen (tạo ảnh AI)", "imagen")
        ai_form.addRow("Chế độ ảnh", self.ai_mode_combo)
        self.ai_dir_edit = QLineEdit()
        self.ai_dir_edit.setPlaceholderText("Mặc định assets/ai — ảnh tên <chương>_01.png…")
        ai_dir_row = QHBoxLayout()
        ai_dir_row.setSpacing(6)
        ai_dir_row.addWidget(self.ai_dir_edit, 1)
        ai_dir_browse = icon_button(
            "folder", "Chọn thư mục ảnh chương…", role="muted", size=15, side=30
        )
        ai_dir_browse.clicked.connect(
            lambda: self._browse_folder_into(
                self.ai_dir_edit, "Chọn thư mục ảnh chương")
        )
        ai_dir_row.addWidget(ai_dir_browse)
        ai_form.addRow("Thư mục ảnh", self._wrap(ai_dir_row))
        self.ai_count_spin = QSpinBox()
        self.ai_count_spin.setRange(1, 10)
        self.ai_count_spin.setToolTip(
            "Số ảnh cho mỗi chương. Chế độ thủ công dùng tất cả ảnh có trong thư mục."
        )
        ai_form.addRow("Số ảnh mỗi chương", self.ai_count_spin)
        self.ai_motion_check = QCheckBox("Chuyển động zoom nhẹ (Ken Burns)")
        ai_form.addRow("", self.ai_motion_check)
        self.ai_xfade_spin = QDoubleSpinBox()
        self.ai_xfade_spin.setRange(0.0, 2.0)
        self.ai_xfade_spin.setSingleStep(0.1)
        self.ai_xfade_spin.setDecimals(1)
        self.ai_xfade_spin.setSuffix(" giây")
        ai_form.addRow("Chuyển cảnh mờ", self.ai_xfade_spin)

        self.ai_imagen_box = QWidget()
        imagen_form = QFormLayout(self.ai_imagen_box)
        imagen_form.setContentsMargins(0, 0, 0, 0)
        imagen_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ai_api_key_edit = QLineEdit()
        self.ai_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_api_key_edit.setPlaceholderText(
            "API key Gemini/Imagen (hoặc đặt biến môi trường GEMINI_API_KEY)")
        imagen_form.addRow("API key", self.ai_api_key_edit)
        self.ai_model_edit = QLineEdit()
        self.ai_model_edit.setPlaceholderText(AI_DEFAULT_MODEL)
        imagen_form.addRow("Model", self.ai_model_edit)
        self.ai_style_edit = QLineEdit()
        self.ai_style_edit.setPlaceholderText(AI_DEFAULT_STYLE)
        imagen_form.addRow("Phong cách", self.ai_style_edit)
        ai_key_hint = QLabel(
            "Ảnh đã tạo được lưu vào thư mục trên và dùng lại, không gọi API lần sau."
        )
        ai_key_hint.setObjectName("MutedLabel")
        ai_key_hint.setWordWrap(True)
        imagen_form.addRow("", ai_key_hint)
        ai_form.addRow(self.ai_imagen_box)
        self.visual_stack.addWidget(ai_page)
        left.addWidget(visual_box)

        # ── Card: Âm thanh ───────────────────────────────────────────────────
        bgm_box = QGroupBox("Âm thanh (Nhạc nền)")
        bgm_layout = QVBoxLayout(bgm_box)
        bgm_layout.setSpacing(8)
        bgm_row = QHBoxLayout()
        bgm_row.setSpacing(6)
        self.bgm_edit = QLineEdit()
        self.bgm_edit.setPlaceholderText("Không dùng nhạc nền")
        bgm_row.addWidget(self.bgm_edit, 1)
        bgm_browse = icon_button(
            "music", "Chọn nhạc nền…", role="muted", size=15, side=30
        )
        bgm_browse.clicked.connect(
            lambda: self._browse_into(self.bgm_edit, "Chọn nhạc nền", _AUDIO_FILTER)
        )
        bgm_clear = icon_button(
            "x", "Bỏ nhạc nền", role="muted", size=14, side=30
        )
        bgm_clear.clicked.connect(lambda: self.bgm_edit.setText(""))
        bgm_row.addWidget(bgm_browse)
        bgm_row.addWidget(bgm_clear)
        bgm_layout.addLayout(bgm_row)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Âm lượng"))
        self.bgm_volume = QSlider(Qt.Orientation.Horizontal)
        self.bgm_volume.setRange(-30, -5)
        self.bgm_volume.valueChanged.connect(self._update_bgm_label)
        self.bgm_volume_label = QLabel("-18 dB")
        self.bgm_volume_label.setMinimumWidth(54)
        volume_row.addWidget(self.bgm_volume, 1)
        volume_row.addWidget(self.bgm_volume_label)
        bgm_layout.addLayout(volume_row)
        duck_row = QHBoxLayout()
        duck_row.addWidget(QLabel("Khi có giọng"))
        self.bgm_duck = QSlider(Qt.Orientation.Horizontal)
        self.bgm_duck.setRange(0, 100)
        self.bgm_duck.valueChanged.connect(self._update_duck_label)
        self.bgm_duck_label = QLabel("25%")
        self.bgm_duck_label.setMinimumWidth(54)
        duck_row.addWidget(self.bgm_duck, 1)
        duck_row.addWidget(self.bgm_duck_label)
        bgm_layout.addLayout(duck_row)
        bgm_hint = QLabel(
            "Âm lượng -20 dB đến -15 dB để giọng vẫn rõ. Khi có giọng, nhạc "
            "tự hạ xuống mức phần trăm bên trên (ducking)."
        )
        bgm_hint.setObjectName("MutedLabel")
        bgm_hint.setWordWrap(True)
        bgm_layout.addWidget(bgm_hint)
        left.addWidget(bgm_box)
        left.addStretch(1)

        # ── Card: Ghép & Xuất ────────────────────────────────────────────────
        render_box = QGroupBox("Ghép và xuất video")
        render_layout = QVBoxLayout(render_box)
        render_layout.setSpacing(8)

        group_caption = QLabel("Cách ghép file")
        group_caption.setObjectName("FieldLabel")
        render_layout.addWidget(group_caption)

        group_form = QFormLayout()
        group_form.setContentsMargins(0, 0, 0, 0)
        group_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        group_form.setHorizontalSpacing(12)
        group_form.setVerticalSpacing(6)

        self.grouping_combo = QComboBox()
        self.grouping_combo.addItem("Mỗi file là một video", "one")
        self.grouping_combo.addItem("Gộp tất cả thành 1 video", "all")
        self.grouping_combo.addItem("Ghép theo nhóm file", "group")
        group_form.addRow("Chế độ ghép", self.grouping_combo)

        self.group_size_spin = QSpinBox()
        self.group_size_spin.setRange(2, 200)
        self.group_size_spin.setValue(10)
        self.group_size_spin.setSuffix(" file / video")
        self.group_size_label = QLabel("Số file mỗi video")
        group_form.addRow(self.group_size_label, self.group_size_spin)

        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.0, 10.0)
        self.gap_spin.setSingleStep(0.5)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setValue(1.5)
        self.gap_spin.setSuffix(" giây")
        self.gap_label = QLabel("Khoảng lặng giữa file")
        group_form.addRow(self.gap_label, self.gap_spin)

        self.group_hint = QLabel(
            "• Mỗi file là một video: Từng file audio/kịch bản trong dự án sẽ được tạo thành một video .mp4 riêng biệt."
        )
        self.group_hint.setObjectName("MutedLabel")
        self.group_hint.setWordWrap(True)
        group_form.addRow("", self.group_hint)
        render_layout.addLayout(group_form)

        render_layout.addWidget(self._panel_sep())

        output_caption = QLabel("Thông số xuất")
        output_caption.setObjectName("FieldLabel")
        render_layout.addWidget(output_caption)

        output_form = QFormLayout()
        output_form.setContentsMargins(0, 0, 0, 0)
        output_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        output_form.setHorizontalSpacing(12)
        output_form.setVerticalSpacing(6)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItem("1920×1080 (16:9 Full HD)", "1920x1080")
        self.resolution_combo.addItem("1280×720 (16:9 HD)", "1280x720")
        self.resolution_combo.addItem("1080×1920 (9:16 Shorts/TikTok)", "1080x1920")
        self.resolution_combo.addItem("720×1280 (9:16 Shorts)", "720x1280")
        self.resolution_combo.addItem("1080×1080 (1:1 Vuông)", "1080x1080")
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(24, 60)
        res_row = QHBoxLayout()
        res_row.setSpacing(8)
        res_row.addWidget(self.resolution_combo, 1)
        res_row.addWidget(QLabel("FPS"))
        res_row.addWidget(self.fps_spin)
        output_form.addRow("Độ phân giải", self._wrap(res_row))

        self.encoder_combo = QComboBox()
        for value, label in _ENCODER_LABELS.items():
            self.encoder_combo.addItem(label, value)
        output_form.addRow("Encoder", self.encoder_combo)

        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setToolTip(
            "CRF/CQ càng thấp càng nét và file càng lớn (20 là cân bằng).")
        self.crf_spin.setMaximumWidth(120)
        output_form.addRow("Chất lượng (CRF/CQ)", self.crf_spin)
        render_layout.addLayout(output_form)

        render_layout.addWidget(self._panel_sep())

        overlay_caption = QLabel("Lớp phủ và phụ đề")
        overlay_caption.setObjectName("FieldLabel")
        render_layout.addWidget(overlay_caption)

        self.ken_burns_check = QCheckBox("Ken Burns (zoom nhẹ)")
        self.showwaves_check = QCheckBox("Hiển thị sóng nhạc")
        self.chapters_check = QCheckBox("Hiện tiêu đề chương trên hình")
        self.subtitle_check = QCheckBox("Hiện phụ đề (theo lời đọc, đúng ngôn ngữ file)")
        self.srt_check = QCheckBox("Gắn phụ đề từ file .srt có sẵn")
        self.srt_check.setToolTip(
            "Ưu tiên file .srt ở ô bên dưới; nếu để trống sẽ tự tìm <tên>.srt "
            "cạnh file audio hoặc file kịch bản.")
        self.write_srt_check = QCheckBox("Xuất thêm phụ đề .srt cạnh video (từ lời đọc)")
        self.keep_temp_check = QCheckBox("Giữ thư mục tạm để kiểm tra")

        render_layout.addWidget(self.subtitle_check)
        render_layout.addWidget(self.srt_check)

        srt_row = QHBoxLayout()
        srt_row.setSpacing(6)
        self.srt_edit = QLineEdit()
        self.srt_edit.setPlaceholderText("Để trống: tự tìm <tên>.srt cạnh audio")
        srt_row.addWidget(self.srt_edit, 1)
        self.srt_browse = icon_button(
            "file", "Chọn file phụ đề .srt…", role="muted", size=15, side=30
        )
        self.srt_browse.clicked.connect(
            lambda: self._browse_into(self.srt_edit, "Chọn file phụ đề", _SRT_FILTER)
        )
        srt_row.addWidget(self.srt_browse)
        self.srt_clear = icon_button(
            "x", "Bỏ file phụ đề đã chọn", role="muted", size=14, side=30
        )
        self.srt_clear.clicked.connect(lambda: self.srt_edit.setText(""))
        srt_row.addWidget(self.srt_clear)
        render_layout.addLayout(srt_row)
        self.srt_check.toggled.connect(self._set_srt_enabled)
        self._set_srt_enabled(self.srt_check.isChecked())

        toggles = QGridLayout()
        toggles.setHorizontalSpacing(12)
        toggles.setVerticalSpacing(4)
        toggles.addWidget(self.showwaves_check, 0, 0)
        toggles.addWidget(self.chapters_check, 0, 1)
        toggles.addWidget(self.ken_burns_check, 1, 0)
        toggles.addWidget(self.write_srt_check, 1, 1)
        toggles.addWidget(self.keep_temp_check, 2, 0)
        render_layout.addLayout(toggles)
        right.addWidget(render_box)
        right.addStretch(1)

        # ── Bottom row: FFmpeg tools + tips ─────────────────────────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        tools_box = QGroupBox("Công cụ FFmpeg")
        tools_form = QFormLayout(tools_box)
        tools_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        tools_form.setHorizontalSpacing(12)
        tools_form.setVerticalSpacing(8)
        self.ffmpeg_edit = QLineEdit()
        self.ffmpeg_edit.setPlaceholderText("ffmpeg (dùng PATH nếu để trống)")
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.setSpacing(6)
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_browse = icon_button(
            "file", "Chọn ffmpeg.exe…", role="muted", size=15, side=30
        )
        ffmpeg_browse.clicked.connect(
            lambda: self._browse_into(self.ffmpeg_edit, "Chọn ffmpeg.exe", _EXE_FILTER)
        )
        ffmpeg_row.addWidget(ffmpeg_browse)
        tools_form.addRow("ffmpeg", self._wrap(ffmpeg_row))
        self.ffprobe_edit = QLineEdit()
        self.ffprobe_edit.setPlaceholderText("ffprobe (dùng PATH nếu để trống)")
        ffprobe_row = QHBoxLayout()
        ffprobe_row.setSpacing(6)
        ffprobe_row.addWidget(self.ffprobe_edit, 1)
        ffprobe_browse = icon_button(
            "file", "Chọn ffprobe.exe…", role="muted", size=15, side=30
        )
        ffprobe_browse.clicked.connect(
            lambda: self._browse_into(self.ffprobe_edit, "Chọn ffprobe.exe", _EXE_FILTER)
        )
        ffprobe_row.addWidget(ffprobe_browse)
        tools_form.addRow("ffprobe", self._wrap(ffprobe_row))
        check_button = QPushButton("Kiểm tra FFmpeg")
        set_icon(check_button, "refresh", "accent", 14)
        check_button.clicked.connect(self.tools_changed.emit)
        tools_form.addRow("", check_button)
        self.tools_status = QLabel("Chưa kiểm tra.")
        self.tools_status.setObjectName("MutedLabel")
        self.tools_status.setWordWrap(True)
        tools_form.addRow("", self.tools_status)
        bottom_row.addWidget(tools_box, 1)

        hint_box = QGroupBox("Gợi ý")
        hint_layout = QVBoxLayout(hint_box)
        hint = QLabel(
            "• Video luôn dài đúng bằng phần audio đã ghép (MP3).\n"
            "• Video nền và trình chiếu tự lặp đến khi hết audio.\n"
            "• Chương và timestamps tự sinh khi gộp nhiều file.\n"
            "• NVENC/QSV/AMF chỉ hiện khi ffmpeg và driver hỗ trợ.\n"
            "• Bố cục phụ đề, sóng âm và sticker chỉnh ở tab “Khung mẫu”."
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        hint_layout.addWidget(hint)
        hint_layout.addStretch(1)
        bottom_row.addWidget(hint_box, 1)
        root.addLayout(bottom_row)
        root.addStretch(1)

        # ── Sticky summary bar ──────────────────────────────────────────────
        self.run_button = QPushButton("Tạo video")
        self.run_button.setObjectName("runAllButton")
        set_icon(self.run_button, "film", "white", 15)
        self.run_button.setToolTip(
            "Chạy bước ghép video cho toàn bộ project (như nút “2. Tạo Video”).")
        self.run_button.clicked.connect(self.run_requested.emit)
        config_layout.addWidget(self._build_summary_bar())

        self.visual_combo.currentIndexChanged.connect(self.visual_stack.setCurrentIndex)
        self.visual_combo.currentIndexChanged.connect(self._sync_visual_height)
        self.ai_mode_combo.currentIndexChanged.connect(self._on_ai_mode_changed)
        self.grouping_combo.currentIndexChanged.connect(self._on_grouping_changed)
        self.resolution_combo.currentIndexChanged.connect(self._update_summary)
        self.encoder_combo.currentIndexChanged.connect(self._update_summary)
        self.fps_spin.valueChanged.connect(self._update_summary)
        self.group_size_spin.valueChanged.connect(self._update_summary)
        self._sync_visual_height()
        self._on_grouping_changed()
        self._update_summary()

    @staticmethod
    def _panel_sep() -> QFrame:
        sep = QFrame()
        sep.setObjectName("PanelSep")
        sep.setFixedHeight(1)
        return sep

    def _build_summary_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("SummaryBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        icon = QLabel()
        icon.setPixmap(pixmap("film", theme.icon_color("accent"), 16))
        row.addWidget(icon)

        self._chip_res = QLabel("1920×1080")
        self._chip_fps = QLabel("30 FPS")
        self._chip_enc = QLabel("Tự động")
        self._chip_group = QLabel("Mỗi file một video")
        for chip in (self._chip_res, self._chip_fps,
                     self._chip_enc, self._chip_group):
            chip.setObjectName("StageChip")
            row.addWidget(chip)

        self.summary_files = QLabel("Chưa mở project.")
        self.summary_files.setObjectName("MutedLabel")
        row.addWidget(self.summary_files)
        row.addStretch(1)

        self.summary_hint = QLabel("Chạy cho toàn bộ project")
        self.summary_hint.setObjectName("MutedLabel")
        row.addWidget(self.summary_hint)

        row.addWidget(self.run_button)
        return bar

    def _update_summary(self, *_args) -> None:
        resolution = self.resolution_combo.currentText().split(" (")[0]
        fps = self.fps_spin.value()
        encoder = self.encoder_combo.currentText().split(" (")[0]
        mode = str(self.grouping_combo.currentData() or "one")
        if mode == "all":
            grouping = "Gộp 1 video"
        elif mode == "group":
            grouping = f"Nhóm {self.group_size_spin.value()} file"
        else:
            grouping = "Mỗi file một video"
        self._chip_res.setText(resolution)
        self._chip_fps.setText(f"{fps} FPS")
        self._chip_enc.setText(encoder)
        self._chip_group.setText(grouping)
        if not self._manager:
            self.summary_files.setText("Chưa mở project.")
        else:
            self.summary_files.setText(
                f"{self._ready_count}/{self._total_count} audio sẵn sàng")

    def _on_grouping_changed(self) -> None:
        mode = str(self.grouping_combo.currentData() or "one")
        is_group = (mode == "group")
        is_all_or_group = (mode in ("all", "group"))
        self.group_size_label.setVisible(is_group)
        self.group_size_spin.setVisible(is_group)
        self.gap_label.setVisible(is_all_or_group)
        self.gap_spin.setVisible(is_all_or_group)
        if mode == "one":
            self.group_hint.setText(
                "• Mỗi file là một video: Từng file audio/kịch bản trong dự án sẽ được tạo thành một video .mp4 riêng biệt."
            )
        elif mode == "all":
            self.group_hint.setText(
                "• Gộp tất cả thành 1 video: Toàn bộ file trong dự án được ghép nối thành một video dài duy nhất, tự động tạo chapters."
            )
        else:
            self.group_hint.setText(
                "• Ghép theo nhóm: Chia dự án thành các phần video, mỗi video gồm số file quy định bên trên."
            )
        self._update_summary()

    def _build_files_box(self) -> QFrame:
        box = QFrame()
        box.setObjectName("FilesPanel")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 8, 12, 8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.files_summary = QLabel("Chưa mở project.")
        self.files_summary.setObjectName("FieldLabel")
        top_row.addWidget(self.files_summary, 1)

        self.files_toggle = QPushButton("Xem danh sách")
        set_icon(self.files_toggle, "chevron-down", "accent", 13)
        self.files_toggle.setCheckable(True)
        self.files_toggle.setChecked(False)
        self.files_toggle.toggled.connect(self._toggle_files)
        top_row.addWidget(self.files_toggle)

        self.btn_refresh_files = QPushButton("Làm mới")
        set_icon(self.btn_refresh_files, "rotate-cw", "accent", 13)
        self.btn_refresh_files.clicked.connect(self.refresh_files)
        top_row.addWidget(self.btn_refresh_files)

        layout.addLayout(top_row)

        self.files_table = QTableWidget(0, 5)
        self.files_table.setHorizontalHeaderLabels([
            "STT", "Tên file kịch bản / audio", "Ngôn ngữ", "File Audio", "Trạng thái Video"
        ])
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.setMinimumHeight(140)
        self.files_table.setMaximumHeight(210)
        self.files_table.setVisible(self._files_expanded)

        header = self.files_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.files_table)
        return box

    def _toggle_files(self, expanded: bool) -> None:
        self._files_expanded = bool(expanded)
        self.files_table.setVisible(self._files_expanded)
        self.files_toggle.setText(
            "Thu gọn" if self._files_expanded else "Xem danh sách")
        set_icon(
            self.files_toggle,
            "chevron-up" if self._files_expanded else "chevron-down",
            "accent",
            13,
        )

    def set_project(self, manager: Any) -> None:
        self._manager = manager
        self.refresh_files()

    def refresh_files(self) -> None:
        self.files_table.setRowCount(0)
        self._ready_count = 0
        self._total_count = 0
        if not self._manager:
            self.files_summary.setText("Chưa mở project.")
            self._update_summary()
            return

        items = list(self._manager.items.values()) if hasattr(self._manager, "items") else []
        ready_count = 0
        self.files_table.setRowCount(len(items))

        for row, item in enumerate(items):
            stt_item = QTableWidgetItem(str(row + 1))
            stt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.files_table.setItem(row, 0, stt_item)

            name = getattr(item, "name", "") or getattr(item, "base", "")
            name_item = QTableWidgetItem(name)
            self.files_table.setItem(row, 1, name_item)

            lang = (getattr(item, "language", "") or "vi").upper()
            lang_item = QTableWidgetItem(lang)
            lang_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.files_table.setItem(row, 2, lang_item)

            has_audio = bool(getattr(item, "has_audio", False))
            if not has_audio and hasattr(item, "audio_path") and item.audio_path:
                try:
                    has_audio = Path(item.audio_path).is_file()
                except Exception:
                    pass

            if has_audio:
                ready_count += 1
                audio_item = QTableWidgetItem("✓ Sẵn sàng")
                audio_item.setForeground(QColor("#22C55E"))
            else:
                audio_item = QTableWidgetItem("Chưa có audio")
                audio_item.setForeground(QColor("#94A3B8"))
            audio_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.files_table.setItem(row, 3, audio_item)

            video_done = False
            video_id = getattr(item, "video_id", "")
            if video_id and hasattr(self._manager, "videos") and video_id in self._manager.videos:
                vjob = self._manager.videos[video_id]
                video_done = bool(vjob.path and Path(vjob.path).is_file())

            if video_done:
                video_item = QTableWidgetItem("✓ Đã tạo")
                video_item.setForeground(QColor("#3B82F6"))
            elif has_audio:
                video_item = QTableWidgetItem("Chờ tạo")
                video_item.setForeground(QColor("#F59E0B"))
            else:
                video_item = QTableWidgetItem("—")
                video_item.setForeground(QColor("#94A3B8"))
            video_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.files_table.setItem(row, 4, video_item)

        total = len(items)
        self._ready_count = ready_count
        self._total_count = total
        if total == 0:
            self.files_summary.setText("Chưa có file kịch bản hoặc audio nào trong project.")
        else:
            self.files_summary.setText(f"{ready_count}/{total} file audio sẵn sàng tạo video")
        if total and not self._files_expanded:
            self.files_toggle.setEnabled(True)
        self._update_summary()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        stack_columns(self, self._columns, 960)

    @staticmethod
    def _wrap(layout) -> QWidget:
        holder = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(layout)
        return holder

    # ── settings ─────────────────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        video = settings.get("video") or {}
        paths = settings.get("paths") or {}
        grouping = str(video.get("grouping") or "one").lower()
        self._set_combo(self.grouping_combo, grouping)
        self.group_size_spin.setValue(int(video.get("group_size", 10) or 10))
        self.gap_spin.setValue(float(video.get("gap", 1.5) or 1.5))
        self._on_grouping_changed()
        self._set_combo(self.visual_combo, video.get("visual", "image"))
        self.image_edit.setText(str(video.get("image") or ""))
        self.slideshow_edit.setText(str(video.get("slideshow_dir") or ""))
        self.slide_seconds_spin.setValue(float(video.get("slide_seconds", 5.0) or 5.0))
        self.video_list.clear()
        for path in video.get("video_files") or []:
            self._add_video_item(str(path))
        self.pool_dir_edit.setText(str(video.get("video_pool_dir") or ""))
        self.pool_count_spin.setValue(int(video.get("video_pool_count", 0) or 0))
        self._set_combo(self.ai_mode_combo, video.get("ai_mode", "manual"))
        self.ai_dir_edit.setText(str(video.get("ai_dir") or ""))
        self.ai_count_spin.setValue(int(video.get("ai_count", 1) or 1))
        self.ai_motion_check.setChecked(bool(video.get("ai_motion", True)))
        self.ai_xfade_spin.setValue(float(video.get("ai_xfade", 0.5) or 0.0))
        self.ai_api_key_edit.setText(str(video.get("ai_api_key") or ""))
        self.ai_model_edit.setText(str(video.get("ai_model") or ""))
        self.ai_style_edit.setText(str(video.get("ai_style") or ""))
        self.bgm_edit.setText(str(video.get("bgm") or ""))
        self.bgm_volume.setValue(int(video.get("bgm_volume_db", -18) or -18))
        self.bgm_duck.setValue(int(round(float(video.get("bgm_duck", 0.25) or 0.25) * 100)))
        self._set_combo(self.resolution_combo, video.get("resolution", "1920x1080"))
        self.fps_spin.setValue(int(video.get("fps", 30) or 30))
        self._set_combo(self.encoder_combo, video.get("encoder", "auto"))
        self.crf_spin.setValue(int(video.get("crf", 20) or 20))
        self.ken_burns_check.setChecked(bool(video.get("ken_burns", False)))
        self.showwaves_check.setChecked(bool(video.get("showwaves", False)))
        self.chapters_check.setChecked(bool(video.get("chapter_titles", False)))
        self.subtitle_check.setChecked(bool(video.get("subtitle_enabled", False)))
        self.srt_check.setChecked(bool(video.get("subtitle_srt", False)))
        self.srt_edit.setText(str(video.get("subtitle_file") or ""))
        self._set_srt_enabled(self.srt_check.isChecked())
        self.write_srt_check.setChecked(bool(video.get("write_srt", False)))
        self.keep_temp_check.setChecked(bool(video.get("keep_temp", False)))
        self.ffmpeg_edit.setText(str(paths.get("ffmpeg") or ""))
        self.ffprobe_edit.setText(str(paths.get("ffprobe") or ""))
        self._on_ai_mode_changed()
        self.visual_stack.setCurrentIndex(max(0, self.visual_combo.currentIndex()))
        self._sync_visual_height()
        self._update_bgm_label(self.bgm_volume.value())
        self._update_duck_label(self.bgm_duck.value())
        self.frame_tab.load_from(settings)

    def apply_to(self, settings: Dict[str, Any]) -> None:
        video = settings.setdefault("video", {})
        paths = settings.setdefault("paths", {})
        video["grouping"] = self.grouping_combo.currentData() or "one"
        video["group_size"] = int(self.group_size_spin.value())
        video["gap"] = round(float(self.gap_spin.value()), 2)
        video["visual"] = self.visual_combo.currentData() or "image"
        video["image"] = self.image_edit.text().strip()
        video["slideshow_dir"] = self.slideshow_edit.text().strip()
        video["slide_seconds"] = round(float(self.slide_seconds_spin.value()), 2)
        video["video_files"] = self._video_files()
        video["video_pool_dir"] = self.pool_dir_edit.text().strip()
        video["video_pool_count"] = int(self.pool_count_spin.value())
        video["ai_mode"] = self.ai_mode_combo.currentData() or "manual"
        video["ai_dir"] = self.ai_dir_edit.text().strip()
        video["ai_count"] = int(self.ai_count_spin.value())
        video["ai_motion"] = self.ai_motion_check.isChecked()
        video["ai_xfade"] = round(float(self.ai_xfade_spin.value()), 2)
        video["ai_api_key"] = self.ai_api_key_edit.text().strip()
        video["ai_model"] = self.ai_model_edit.text().strip() or AI_DEFAULT_MODEL
        video["ai_style"] = self.ai_style_edit.text().strip() or AI_DEFAULT_STYLE
        video["bgm"] = self.bgm_edit.text().strip()
        video["bgm_volume_db"] = int(self.bgm_volume.value())
        video["bgm_duck"] = round(self.bgm_duck.value() / 100.0, 2)
        video["resolution"] = self.resolution_combo.currentData() or "1920x1080"
        video["fps"] = int(self.fps_spin.value())
        video["encoder"] = self.encoder_combo.currentData() or "auto"
        video["crf"] = int(self.crf_spin.value())
        video["ken_burns"] = self.ken_burns_check.isChecked()
        video["showwaves"] = self.showwaves_check.isChecked()
        video["chapter_titles"] = self.chapters_check.isChecked()
        video["subtitle_enabled"] = self.subtitle_check.isChecked()
        video["subtitle_srt"] = self.srt_check.isChecked()
        video["subtitle_file"] = self.srt_edit.text().strip()
        video["write_srt"] = self.write_srt_check.isChecked()
        video["keep_temp"] = self.keep_temp_check.isChecked()
        paths["ffmpeg"] = self.ffmpeg_edit.text().strip() or "ffmpeg"
        paths["ffprobe"] = self.ffprobe_edit.text().strip() or "ffprobe"
        self.frame_tab.apply_to(settings)
        self._settings = settings

    def apply_theme(self) -> None:
        self.frame_tab.apply_theme()

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ── widgets behaviour ────────────────────────────────────────────────────
    def _on_ai_mode_changed(self) -> None:
        imagen = self.ai_mode_combo.currentData() == "imagen"
        self.ai_imagen_box.setVisible(imagen)
        self.ai_count_spin.setEnabled(imagen)
        self._sync_visual_height()

    def _set_srt_enabled(self, enabled: bool) -> None:
        for widget in (self.srt_edit, self.srt_browse, self.srt_clear):
            widget.setEnabled(enabled)

    def _sync_visual_height(self) -> None:
        page = self.visual_stack.currentWidget()
        if page is not None:
            self.visual_stack.setMaximumHeight(page.sizeHint().height())

    def _update_bgm_label(self, value: int) -> None:
        self.bgm_volume_label.setText(f"{int(value)} dB")

    def _update_duck_label(self, value: int) -> None:
        self.bgm_duck_label.setText(f"{int(value)}%")

    def _browse_into(self, edit: QLineEdit, title: str, filters: str) -> None:
        start = edit.text().strip()
        path, _ = QFileDialog.getOpenFileName(self, title, start, filters)
        if path:
            edit.setText(path)

    def _browse_slideshow(self) -> None:
        self._browse_folder_into(self.slideshow_edit, "Chọn thư mục ảnh")

    def _browse_folder_into(self, edit: QLineEdit, title: str) -> None:
        start = edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, title, start)
        if chosen:
            edit.setText(chosen)

    def _add_video_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn video nền", str(Path.home()), _VIDEO_FILTER
        )
        for path in paths:
            self._add_video_item(path)

    def _add_video_item(self, path: str) -> None:
        if not path:
            return
        for index in range(self.video_list.count()):
            if self.video_list.item(index).data(Qt.ItemDataRole.UserRole) == path:
                return
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.video_list.addItem(item)
        self._sync_visual_height()

    def _remove_video_file(self) -> None:
        row = self.video_list.currentRow()
        if row >= 0:
            self.video_list.takeItem(row)
            self._sync_visual_height()

    def _video_files(self) -> List[str]:
        return [
            str(self.video_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.video_list.count())
        ]

    # ── encoder availability (from the FFmpeg probe) ─────────────────────────
    def set_encoder_status(self, encoders: List[str], supported: bool = True) -> None:
        if not supported:
            self.tools_status.setText("Không chạy được ffmpeg — kiểm tra lại đường dẫn.")
            paint_status(self.tools_status, "error")
            return
        available = set(encoders) | {"auto"}
        model = self.encoder_combo.model()
        for index in range(self.encoder_combo.count()):
            data = str(self.encoder_combo.itemData(index))
            item = model.item(index)
            if item is not None:
                item.setEnabled(data in available)
            label = _ENCODER_LABELS.get(data, data)
            if data not in available:
                label += " (không có)"
            self.encoder_combo.setItemText(index, label)
        current = self.encoder_combo.currentData()
        if current not in available:
            self._set_combo(self.encoder_combo, "auto")
        self.tools_status.setText(
            "ffmpeg sẵn sàng · encoder: " + ", ".join(sorted(set(encoders)))
        )
        paint_status(self.tools_status, "success")

    def set_pipeline_busy(self, busy: bool) -> None:
        self._pipeline_busy = bool(busy)
        for btn in self.findChildren(QPushButton):
            btn.setEnabled(not busy)
        for combo in self.findChildren(QComboBox):
            combo.setEnabled(not busy)
        for spin in self.findChildren(QSpinBox):
            spin.setEnabled(not busy)
        for dspin in self.findChildren(QDoubleSpinBox):
            dspin.setEnabled(not busy)
        for slider in self.findChildren(QSlider):
            slider.setEnabled(not busy)
        for chk in self.findChildren(QCheckBox):
            chk.setEnabled(not busy)
        for edit in self.findChildren(QLineEdit):
            edit.setReadOnly(busy)
