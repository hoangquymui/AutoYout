"""Video tab: background visuals, BGM and encoder options."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.ai_images import (
    DEFAULT_MODEL as AI_DEFAULT_MODEL,
    DEFAULT_STYLE as AI_DEFAULT_STYLE,
)
from ui.components.icons import set_icon
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

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        scroller = wrap_scroll(content)
        # The two option columns need ~1500px; allow scrolling on smaller windows.
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.addWidget(scroller)

        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        header = PageHeader(
            "Kết xuất Video",
            "Tạo video từ audio với hình nền, nhạc nền và encoder GPU/CPU.",
        )
        root.addWidget(header)

        run_row = QHBoxLayout()
        run_row.setSpacing(10)
        self.run_button = QPushButton("Tạo video")
        self.run_button.setObjectName("primaryButton")
        set_icon(self.run_button, "film", "white", 15)
        self.run_button.setToolTip(
            "Chạy bước ghép video cho toàn bộ project (như nút “2. Tạo Video”).")
        self.run_button.clicked.connect(self.run_requested.emit)
        run_row.addWidget(self.run_button)
        run_hint = QLabel(
            "Chạy bước ghép video cho toàn bộ project, dùng đúng các tuỳ chọn bên dưới.")
        run_hint.setObjectName("MutedLabel")
        run_hint.setWordWrap(True)
        run_row.addWidget(run_hint, 1)
        root.addLayout(run_row)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        self._columns = columns
        root.addLayout(columns, 1)

        left = QVBoxLayout()
        left.setSpacing(12)
        columns.addLayout(left, 1)

        visual_box = QGroupBox("Hình nền")
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
            "đến khi hết audio. Đặt 0 để dùng toàn bộ clip (xáo trộn ngẫu nhiên)."
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

        bgm_box = QGroupBox("Nhạc nền (BGM)")
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
            "tự hạ xuống mức phần trăm bên dưới (ducking)."
        )
        bgm_hint.setObjectName("MutedLabel")
        bgm_hint.setWordWrap(True)
        bgm_layout.addWidget(bgm_hint)
        left.addWidget(bgm_box)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(12)
        columns.addLayout(right, 1)

        output_box = QGroupBox("Xuất video")
        output_form = QFormLayout(output_box)
        output_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        output_form.setHorizontalSpacing(12)
        output_form.setVerticalSpacing(8)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItem("1920×1080 (Full HD)", "1920x1080")
        self.resolution_combo.addItem("1280×720 (HD)", "1280x720")
        output_form.addRow("Độ phân giải", self.resolution_combo)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(24, 60)
        output_form.addRow("FPS", self.fps_spin)
        self.encoder_combo = QComboBox()
        for value, label in _ENCODER_LABELS.items():
            self.encoder_combo.addItem(label, value)
        output_form.addRow("Encoder", self.encoder_combo)
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setToolTip("CRF/CQ càng thấp càng nét và file càng lớn (20 là cân bằng).")
        output_form.addRow("Chất lượng (CRF/CQ)", self.crf_spin)
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
        for check in (self.ken_burns_check, self.showwaves_check,
                      self.chapters_check, self.subtitle_check, self.srt_check):
            output_form.addRow("", check)

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
        output_form.addRow("File .srt", self._wrap(srt_row))
        self.srt_check.toggled.connect(self._set_srt_enabled)
        self._set_srt_enabled(self.srt_check.isChecked())

        for check in (self.write_srt_check, self.keep_temp_check):
            output_form.addRow("", check)
        right.addWidget(output_box)

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
        right.addWidget(tools_box)

        hint_box = QGroupBox("Gợi ý")
        hint_layout = QVBoxLayout(hint_box)
        hint = QLabel(
            "• Video luôn dài đúng bằng phần audio đã ghép (MP3).\n"
            "• Ảnh nền nhận .png/.jpg/.webp; video nền và trình chiếu tự lặp "
            "đến khi hết audio.\n"
            "• Phụ đề: bật “Hiện phụ đề” để dùng lời đọc, hoặc “Gắn phụ đề từ "
            "file .srt có sẵn” để chèn SRT của bạn.\n"
            "• Ảnh AI theo chương: chế độ Thủ công dùng ảnh có sẵn, "
            "Google Imagen tạo ảnh từ tiêu đề/nội dung chương.\n"
            "• Ảnh Imagen được lưu trong thư mục ảnh và tái dùng, không tính phí lại.\n"
            "• Chương và timestamps tự sinh khi gộp nhiều file.\n"
            "• NVENC/QSV/AMF chỉ hiện khi ffmpeg và driver hỗ trợ."
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        hint_layout.addWidget(hint)
        right.addWidget(hint_box)
        right.addStretch(1)

        self.visual_combo.currentIndexChanged.connect(self.visual_stack.setCurrentIndex)
        self.visual_combo.currentIndexChanged.connect(self._sync_visual_height)
        self.ai_mode_combo.currentIndexChanged.connect(self._on_ai_mode_changed)
        self._sync_visual_height()

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

    def apply_to(self, settings: Dict[str, Any]) -> None:
        video = settings.setdefault("video", {})
        paths = settings.setdefault("paths", {})
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
        self._settings = settings

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
