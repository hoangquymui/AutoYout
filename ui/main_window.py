"""AutoYout main window: header, tabs and pipeline automation."""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QSize, QUrl, Qt, QTimer
from PySide6.QtGui import QDesktopServices, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.ffmpeg_renderer import FFmpegRenderer
from core.pipeline_worker import PipelineWorker
from core.project_manager import ProjectManager
from core.settings import save_settings
from core.tts_engine import TTSEngine
from core.youtube_uploader import YouTubeUploader
from ui import theme
from ui.components.icons import (
    brand_icon,
    brand_pixmap,
    clear_cache,
    icon,
    refresh_icons,
    set_icon,
    set_pixmap,
)
from ui.components.page import icon_button, refresh_statuses
from ui.components.status_badge import StatusBadge
from ui.styles import app_stylesheet
from ui.tab_automation import AutomationTab
from ui.tab_project import ProjectTab
from ui.tab_settings import SettingsTab
from ui.tab_script import ScriptTab
from ui.tab_dubbing import DubbingTab
from ui.tab_frame import FrameTab
from ui.tab_video import VideoConfigTab
from ui.tab_youtube import YouTubeConfigTab
from utils.logger import (
    APP_LOGGER_NAME,
    LogEmitter,
    attach_qt_handler,
    detach_handler,
    get_logger,
)
from utils.media_probe import ffmpeg_version, resolve_tool

logger = get_logger("ui")

class ElidedLabel(QLabel):
    """QLabel that elides its text instead of expanding the header."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full = str(text or "")
        self.setToolTip(self._full)
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(40, self.width() - 6)
        super().setText(
            metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, width)
        )


class MainWindow(QMainWindow):
    def __init__(self, settings: Dict[str, Any]) -> None:
        super().__init__()
        self.settings = settings
        self.manager: Optional[ProjectManager] = None
        self.worker: Optional[PipelineWorker] = None
        self.tts_engine: Optional[TTSEngine] = None
        self.renderer: Optional[FFmpegRenderer] = None
        self._preview_busy = False
        self._auth_busy = False
        self._script_busy = False
        self._log_handler = None
        self._log_emitter: Optional[LogEmitter] = None
        self._last_success = False

        theme.set_mode(str((settings.get("app") or {}).get("theme") or theme.DEFAULT_MODE))

        self.setWindowTitle("AutoYout")
        self.setWindowIcon(brand_icon())
        self.setMinimumSize(1000, 680)
        self.resize(1400, 920)

        self._build_ui()
        self._apply_theme(theme.current_mode())
        self._apply_responsive_layout()
        self._load_settings_into_tabs()
        self._attach_logging()
        QTimer.singleShot(60, self._startup)

    # ── responsive chrome ────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        if not hasattr(self, "badge_tts"):
            return
        width = self.width()
        compact = width < 1240
        self._brand_subtitle.setVisible(not compact)
        self.btn_header_project.setText("Project" if compact else "Chọn Project")
        for badge in (self.badge_tts, self.badge_ffmpeg, self.badge_youtube):
            badge.set_compact(width < 1180)

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        layout.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(15, 15))
        self.tabs.setMinimumHeight(320)
        self.tabs.tabBar().setDrawBase(False)
        self.project_tab = ProjectTab()
        self.settings_tab = SettingsTab()
        self.script_tab = ScriptTab()
        self.tts_tab = DubbingTab()
        self.video_tab = VideoConfigTab()
        self.frame_tab = self.video_tab.frame_tab
        self.youtube_tab = YouTubeConfigTab()
        self.automation_tab = AutomationTab()
        self.tabs.addTab(self.project_tab, icon("folder", theme.TEXT_DIM, 15), "Dự án")
        self.tabs.addTab(self.settings_tab, icon("settings", theme.TEXT_DIM, 15), "Cài đặt")
        self.tabs.addTab(self.script_tab, icon("file", theme.TEXT_DIM, 15), "Kịch bản")
        self.tabs.addTab(self.tts_tab, icon("mic", theme.TEXT_DIM, 15), "Lồng tiếng")
        self.tabs.addTab(self.video_tab, icon("film", theme.TEXT_DIM, 15), "Video")
        self.tabs.addTab(self.youtube_tab, icon("youtube", theme.TEXT_DIM, 15), "YouTube")
        self._automation_index = self.tabs.addTab(
            self.automation_tab, icon("zap", theme.TEXT_DIM, 15), "Automation"
        )
        self.tabs.currentChanged.connect(self._update_tab_icons)
        self._update_tab_icons()
        layout.addWidget(self.tabs, 1)

        self.settings_tab.settings_changed.connect(self._on_settings_tab_changed)
        self.automation_tab.run_requested.connect(self._start_pipeline)
        self.automation_tab.pause_requested.connect(self._toggle_pause)
        self.automation_tab.stop_requested.connect(self._stop_pipeline)

        self.project_tab.reload_requested.connect(self._reload_project)
        self.script_tab.reload_requested.connect(self._reload_project)
        self.script_tab.busy_changed.connect(self._on_script_busy)
        self.tts_tab.busy_changed.connect(self._on_preview_busy)
        self.tts_tab.engine_reload_requested.connect(self._reload_tts_engine)
        self.tts_tab.reload_requested.connect(self._reload_project)
        self.video_tab.tools_changed.connect(self._check_tools)
        self.video_tab.run_requested.connect(lambda: self._start_pipeline("video"))
        self.youtube_tab.busy_changed.connect(self._on_auth_busy)
        self.youtube_tab.channels_changed.connect(self._on_channels_changed)
        self.youtube_tab.thumbnail_saved.connect(self._on_thumbnail_saved)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 8, 12, 8)
        layout.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(brand_pixmap(36))
        logo.setFixedSize(36, 36)
        logo.setToolTip("AutoYout")
        layout.addWidget(logo)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("AutoYout")
        title.setObjectName("BrandTitle")
        subtitle = QLabel("Text → TTS → Video → YouTube")
        subtitle.setObjectName("BrandSubtitle")
        self._brand_subtitle = subtitle
        brand.addWidget(title)
        brand.addWidget(subtitle)
        layout.addLayout(brand)

        project_card = QFrame()
        project_card.setObjectName("ProjectCard")
        card_layout = QHBoxLayout(project_card)
        card_layout.setContentsMargins(10, 3, 6, 3)
        card_layout.setSpacing(8)

        project_icon = QLabel()
        set_pixmap(project_icon, "folder", "accent", 17)
        project_icon.setFixedSize(20, 20)
        project_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(project_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        project_box = QVBoxLayout()
        project_box.setSpacing(0)
        caption = QLabel("PROJECT")
        caption.setObjectName("ProjectCaption")
        self.project_label = ElidedLabel("Chưa chọn project")
        self.project_label.setObjectName("ProjectPath")
        project_box.addWidget(caption)
        project_box.addWidget(self.project_label)
        card_layout.addLayout(project_box, 1)

        self.btn_header_project = QPushButton("Chọn Project")
        set_icon(self.btn_header_project, "folder-open", "accent", 15)
        self.btn_header_project.clicked.connect(self._choose_project)
        card_layout.addWidget(self.btn_header_project)

        self.btn_header_open = icon_button(
            "external-link",
            "Mở thư mục project trong Explorer",
            role="muted",
            size=15,
            side=30,
        )
        self.btn_header_open.clicked.connect(self._open_project_folder)
        card_layout.addWidget(self.btn_header_open)
        layout.addWidget(project_card, 1)

        self.badge_tts = StatusBadge("H2MTTS")
        self.badge_ffmpeg = StatusBadge("FFmpeg")
        self.badge_youtube = StatusBadge("YouTube")
        layout.addWidget(self.badge_tts)
        layout.addWidget(self.badge_ffmpeg)
        layout.addWidget(self.badge_youtube)

        self.btn_refresh_status = icon_button(
            "refresh",
            "Kiểm tra lại H2MTTS / FFmpeg / YouTube",
            role="muted",
            size=16,
            side=32,
        )
        self.btn_refresh_status.clicked.connect(self._refresh_statuses)
        layout.addWidget(self.btn_refresh_status)

        self.btn_theme = icon_button(
            "sun",
            "Chuyển sang giao diện sáng",
            role="muted",
            size=16,
            side=32,
        )
        self.btn_theme.clicked.connect(self._toggle_theme)
        layout.addWidget(self.btn_theme)
        return header

    # ── logging ──────────────────────────────────────────────────────────────
    def _attach_logging(self) -> None:
        self._log_emitter = LogEmitter(self)
        self._log_emitter.message.connect(self.automation_tab.log_console.append)
        self._log_handler = attach_qt_handler(
            logging.getLogger(APP_LOGGER_NAME), self._log_emitter, logging.INFO
        )

    # ── startup / settings ───────────────────────────────────────────────────
    def _load_settings_into_tabs(self) -> None:
        self.project_tab.load_from(self.settings)
        self.settings_tab.load_from(self.settings)
        self.script_tab.load_from(self.settings)
        self.tts_tab.load_from(self.settings)
        self.video_tab.load_from(self.settings)
        self.frame_tab.load_from(self.settings)
        self.youtube_tab.load_from(self.settings)

    def _collect_all(self) -> None:
        self.project_tab.apply_to(self.settings)
        self.settings_tab.apply_to(self.settings)
        self.script_tab.apply_to(self.settings)
        self.tts_tab.apply_to(self.settings)
        self.video_tab.apply_to(self.settings)
        self.frame_tab.apply_to(self.settings)
        self.youtube_tab.apply_to(self.settings)
        save_settings(self.settings)

    def _on_settings_tab_changed(self) -> None:
        self.settings_tab.apply_to(self.settings)
        save_settings(self.settings)
        self.video_tab.load_from(self.settings)
        self.script_tab.load_from(self.settings)
        self._refresh_statuses()

    def _startup(self) -> None:
        logger.info("AutoYout khởi động.")
        self._refresh_statuses()
        last = str((self.settings.get("project") or {}).get("last_project") or "").strip()
        if last and Path(last).is_dir():
            self.project_tab.set_project(last)
            self._reload_project(silent=True)
        else:
            logger.info("Hãy chọn Project Folder ở góc trên để bắt đầu.")

    # ── status ───────────────────────────────────────────────────────────────
    def _ensure_tts_engine(self) -> Optional[TTSEngine]:
        raw = str((self.settings.get("paths") or {}).get("h2mtts") or "")
        expected = Path(raw).expanduser() if raw else Path("")
        if self.tts_engine is None or self.tts_engine.layout.root != expected:
            self.tts_engine = TTSEngine(self.settings)
            self.tts_tab.set_engine(self.tts_engine)
        return self.tts_engine

    def _refresh_statuses(self) -> None:
        if self.worker is not None:
            return
        self._collect_all()
        self._refresh_tts_badge()
        self._refresh_ffmpeg_badge()
        self._refresh_youtube_badge()

    def _refresh_tts_badge(self) -> None:
        try:
            engine = self._ensure_tts_engine()
            if engine is None or not engine.layout.valid:
                self.badge_tts.set_state("error", "Không thấy thư mục")
                return
            missing = engine.layout.missing()
            turbo = len(engine.voices("turbo"))
            nano = len(engine.voices("nano"))
            if missing:
                self.badge_tts.set_state("warn", f"Thiếu {len(missing)} tệp")
                self.badge_tts.setToolTip(
                    "Thiếu:\n" + "\n".join(missing) + f"\n\n{engine.layout.root}"
                )
            else:
                self.badge_tts.set_state("ok", f"{turbo} giọng")
                self.badge_tts.setToolTip(
                    f"{turbo} giọng Turbo · {nano} giọng Nano\n{engine.layout.root}"
                )
        except Exception as exc:
            logger.error("Kiểm tra H2MTTS lỗi: %s", exc)
            self.badge_tts.set_state("error", "Lỗi kiểm tra")

    def _refresh_ffmpeg_badge(self) -> None:
        try:
            paths = self.settings.get("paths") or {}
            ffmpeg = resolve_tool(paths.get("ffmpeg"), "ffmpeg")
            ffprobe = resolve_tool(paths.get("ffprobe"), "ffprobe")
            if (
                self.renderer is None
                or self.renderer.ffmpeg != ffmpeg
                or self.renderer.ffprobe != ffprobe
            ):
                self.renderer = FFmpegRenderer(self.settings)
            env = self.renderer.environment()
            if not env.get("ffmpeg_ok"):
                self.badge_ffmpeg.set_state("error", "Không chạy được")
                self.badge_ffmpeg.setToolTip(f"ffmpeg: {ffmpeg}")
                self.video_tab.set_encoder_status([], False)
                return
            version = ffmpeg_version(self.renderer.ffmpeg)
            short = version.split("version ", 1)[-1].split(" ", 1)[0] if "version" in version else version
            short = short.split("-", 1)[0] or short
            encoders = env.get("encoders") or []
            self.badge_ffmpeg.set_state("ok", short)
            self.badge_ffmpeg.setToolTip(
                f"{version}\nEncoder: {', '.join(encoders)}\n"
                f"Hwaccel: {', '.join(env.get('hwaccels') or []) or '—'}"
            )
            self.video_tab.set_encoder_status(encoders, True)
        except Exception as exc:
            logger.error("Kiểm tra FFmpeg lỗi: %s", exc)
            self.badge_ffmpeg.set_state("error", "Lỗi kiểm tra")

    def _refresh_youtube_badge(self) -> None:
        try:
            uploader = YouTubeUploader(self.settings)
            channels = uploader.channels()
            authorized = [ch for ch in channels if ch.authorized]
            has_secret = uploader.has_client_secret()
            if authorized:
                names = ", ".join(ch.title or ch.name for ch in authorized)
                self.badge_youtube.set_state("ok", f"{len(authorized)} kênh")
                self.badge_youtube.setToolTip(f"Đã đăng nhập: {names}")
            elif has_secret:
                self.badge_youtube.set_state("warn", "Chưa đăng nhập")
                self.badge_youtube.setToolTip("Đã có client_secret — vào tab YouTube để đăng nhập.")
            else:
                self.badge_youtube.set_state("warn", "Thiếu secret")
                self.badge_youtube.setToolTip(
                    "Đặt client_secret.json vào config/youtube_credentials/ "
                    "hoặc dùng phương thức Browser Automation."
                )
            self.youtube_tab.refresh_channels(
                select=str((self.settings.get("youtube") or {}).get("channel") or "")
            )
        except Exception as exc:
            logger.error("Kiểm tra YouTube lỗi: %s", exc)
            self.badge_youtube.set_state("error", "Lỗi kiểm tra")

    def _check_tools(self) -> None:
        self._collect_all()
        self._refresh_ffmpeg_badge()
        logger.info("Đã kiểm tra lại FFmpeg/encoder.")
        encoder = self.video_tab.encoder_combo.currentData()
        logger.info("Encoder đang chọn: %s", encoder)

    def _reload_tts_engine(self) -> None:
        self._collect_all()
        self.tts_engine = TTSEngine(self.settings)
        self.tts_tab.set_engine(self.tts_engine)
        self._refresh_tts_badge()
        logger.info("Đã nạp lại danh sách giọng đọc H2MTTS.")

    # ── project ──────────────────────────────────────────────────────────────
    def _choose_project(self) -> None:
        start = str((self.settings.get("project") or {}).get("last_project") or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục Project", start)
        if not chosen:
            return
        self.project_tab.set_project(chosen)
        self._reload_project()

    def _open_project_folder(self) -> None:
        path = str((self.settings.get("project") or {}).get("last_project") or "")
        if path and Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reload_project(self, silent: bool = False) -> None:
        if self.worker is not None:
            return
        self._collect_all()
        project = str((self.settings.get("project") or {}).get("last_project") or "").strip()
        if not project:
            if not silent:
                self._set_status("Hãy chọn Project Folder trước.", error=True)
            self.project_tab.set_manager(None)
            self.script_tab.set_project(None)
            self.tts_tab.set_project(None)
            self.video_tab.set_project(None)
            self.youtube_tab.set_project(None)
            return
        path = Path(project)
        if not path.is_dir():
            logger.error("Thư mục project không tồn tại: %s", project)
            self._set_status(f"Thư mục project không tồn tại: {project}", error=True)
            self.script_tab.set_project(None)
            self.tts_tab.set_project(None)
            self.video_tab.set_project(None)
            self.youtube_tab.set_project(None)
            if not silent:
                QMessageBox.warning(self, "Project", f"Không tìm thấy thư mục:\n{project}")
            return
        project_cfg = self.settings.get("project") or {}
        try:
            manager = ProjectManager(
                path,
                audio_dir=project_cfg.get("audio_dir") or "audio",
                video_dir=project_cfg.get("video_dir") or "video",
                assets_dir=project_cfg.get("assets_dir") or "assets",
                script_dir=project_cfg.get("script_dir") or "script",
                thumbs_dir=project_cfg.get("thumbs_dir") or "thumbs",
                default_language=(self.settings.get("tts") or {}).get("language") or "vi",
            )
            manager.scan()
        except Exception as exc:
            logger.exception("Không mở được project %s", project)
            self._set_status(f"Không mở được project: {exc}", error=True)
            return
        self.manager = manager
        if manager.created_folders:
            labels = dict(ProjectManager.FOLDER_LABELS)
            logger.info(
                "Đã tạo thư mục còn thiếu: %s",
                ", ".join(labels.get(key, key) for key in manager.created_folders),
            )
        self.project_tab.set_manager(manager)
        self.script_tab.set_project(manager)
        self.tts_tab.set_project(manager)
        self.video_tab.set_project(manager)
        self.youtube_tab.set_project(manager)
        self.project_label.setText(str(path))
        self.settings.setdefault("project", {})["last_project"] = str(path)
        save_settings(self.settings)
        logger.info(
            "Đã mở project %s — %d file, %d video job.",
            path, len(manager.items), len(manager.videos),
        )
        self._set_status(f"Đã quét {len(manager.items)} file đầu vào.", error=False)
        self._update_actions()

    # ── pipeline control ─────────────────────────────────────────────────────
    def _start_pipeline(self, mode: str) -> None:
        if self.worker is not None:
            return
        if self._preview_busy or self._auth_busy:
            self._set_status("Đang chạy tác vụ khác, hãy đợi hoặc huỷ trước.", error=True)
            return
        if self.manager is None:
            self._set_status("Chưa chọn project.", error=True)
            return
        if not self.manager.get_items():
            self._set_status("Project không có file .txt/.json nào.", error=True)
            return
        self._collect_all()
        if not self._manager_matches_settings():
            self._reload_project()
            if self.manager is None or not self.manager.get_items():
                return

        snapshot = copy.deepcopy(self.settings)
        worker = PipelineWorker(
            snapshot,
            self.manager,
            mode=mode,
            force=self.project_tab.force_overwrite(),
        )
        worker.overall_changed.connect(self._on_overall)
        worker.task_changed.connect(self._on_task)
        worker.stage_changed.connect(self._on_stage)
        worker.status_changed.connect(self._on_status)
        worker.items_updated.connect(lambda: self.project_tab.refresh())
        worker.videos_updated.connect(lambda: self.project_tab.refresh())
        worker.job_uploaded.connect(self._on_job_uploaded)
        worker.notes_changed.connect(self._on_notes_changed)
        worker.completed.connect(self._on_completed)
        worker.finished.connect(self._on_worker_finished)
        self.worker = worker
        self._last_success = False
        self.automation_tab.set_overall(0)
        self.automation_tab.set_task(0)
        self.automation_tab.set_stage("Đang chuẩn bị…")
        self.automation_tab.set_notes([])
        self._set_status("Pipeline đang khởi động…", error=False)
        self.tts_tab.set_busy(True)
        self.youtube_tab.set_pipeline_busy(True)
        self._update_actions()
        worker.start()

    def _manager_matches_settings(self) -> bool:
        if self.manager is None:
            return False
        project_cfg = self.settings.get("project") or {}
        wanted = {
            "project": Path(str(project_cfg.get("last_project") or "")).resolve()
            if project_cfg.get("last_project") else None,
            "audio": project_cfg.get("audio_dir") or "audio",
            "video": project_cfg.get("video_dir") or "video",
            "assets": project_cfg.get("assets_dir") or "assets",
            "script": project_cfg.get("script_dir") or "script",
            "thumbs": project_cfg.get("thumbs_dir") or "thumbs",
        }
        if wanted["project"] is None or self.manager.project_dir != wanted["project"]:
            return False

        def resolve(value: str, fallback: str) -> Path:
            path = Path(str(value).strip() or fallback)
            if not path.is_absolute():
                path = self.manager.project_dir / path
            return path.resolve()

        return (
            self.manager.audio_dir == resolve(wanted["audio"], "audio")
            and self.manager.video_dir == resolve(wanted["video"], "video")
            and self.manager.assets_dir == resolve(wanted["assets"], "assets")
            and self.manager.script_dir == resolve(wanted["script"], "script")
            and self.manager.thumbs_dir == resolve(wanted["thumbs"], "thumbs")
        )

    def _toggle_pause(self) -> None:
        if self.worker is None:
            return
        if self.worker.is_paused():
            self.worker.resume()
            self._set_pause_ui(False)
            self._set_status("Đã tiếp tục pipeline.", error=False)
            logger.info("Tiếp tục pipeline.")
        else:
            self.worker.pause()
            self._set_pause_ui(True)
            self._set_status("Đã tạm dừng — bấm Tiếp tục để chạy tiếp.", error=False)
            logger.warning("Tạm dừng pipeline.")

    def _set_pause_ui(self, paused: bool) -> None:
        self.automation_tab.set_pause_ui(paused)

    def _stop_pipeline(self) -> None:
        if self.worker is None:
            return
        self.automation_tab.btn_stop.setEnabled(False)
        self.automation_tab.btn_pause.setEnabled(False)
        self._set_status("Đang dừng pipeline…", error=False)
        logger.warning("Yêu cầu dừng pipeline.")
        self.worker.stop()

    # ── worker signals ───────────────────────────────────────────────────────
    def _on_overall(self, value: int) -> None:
        self.automation_tab.set_overall(value)

    def _on_task(self, value: int) -> None:
        self.automation_tab.set_task(value)

    def _on_stage(self, label: str) -> None:
        self.automation_tab.set_stage(label)

    def _on_status(self, message: str, running: bool) -> None:
        self._set_status(message, error=False, running=running)

    def _on_job_uploaded(self, job_id: str, title: str, url: str) -> None:
        self._set_status(f"Đã đăng: {title} — {url}", error=False)
        logger.info("Upload xong %s: %s", job_id, url)

    def _on_notes_changed(self, notes: list) -> None:
        self.automation_tab.set_notes(list(notes))

    def _on_completed(self, success: bool, message: str) -> None:
        self._last_success = success
        first_line = message.splitlines()[0] if message else ""
        self._set_status(first_line, error=not success)
        self.automation_tab.set_stage("Hoàn tất" if success else "Kết thúc có lỗi")
        self.automation_tab.set_task(100)
        if success:
            self.automation_tab.set_overall(100)
        self.project_tab.refresh()

    def _on_worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.project_tab.refresh()
        self.video_tab.refresh_files()
        self.tts_tab.set_busy(False)
        self.youtube_tab.set_pipeline_busy(False)
        self._set_pause_ui(False)
        self._update_actions()
        self._collect_all()

    def _on_preview_busy(self, busy: bool) -> None:
        self._preview_busy = busy
        self._update_actions()

    def _on_auth_busy(self, busy: bool) -> None:
        self._auth_busy = busy
        self._update_actions()

    def _on_script_busy(self, busy: bool) -> None:
        self._script_busy = busy
        self._update_actions()

    def _on_channels_changed(self) -> None:
        self._collect_all()
        self._refresh_youtube_badge()

    def _on_thumbnail_saved(self, base: str) -> None:
        self._set_status(f"Đã gắn thumbnail cho {base}.", error=False)
        self.project_tab.refresh()

    # ── helpers ──────────────────────────────────────────────────────────────
    def _set_status(self, message: str, error: bool = False, running: bool = False) -> None:
        self.automation_tab.set_status(message, error=error, running=running)

    # ── theme ────────────────────────────────────────────────────────────────
    def _update_tab_icons(self, index: int | None = None) -> None:
        names = ("folder", "settings", "file", "mic", "film", "youtube", "zap")
        current = self.tabs.currentIndex() if index is None else index
        for position, name in enumerate(names):
            color = theme.ACCENT_SOFT if position == current else theme.TEXT_DIM
            self.tabs.setTabIcon(position, icon(name, color, 15))

    def _sync_theme_button(self) -> None:
        light = theme.current_mode() == "light"
        set_icon(self.btn_theme, "moon" if light else "sun", "muted", 16)
        self.btn_theme.setToolTip(
            "Chuyển sang giao diện tối" if light else "Chuyển sang giao diện sáng"
        )

    def _toggle_theme(self) -> None:
        mode = "light" if theme.current_mode() == "dark" else "dark"
        self._apply_theme(mode, persist=True)
        logger.info("Đã đổi giao diện sang %s.", "sáng" if mode == "light" else "tối")

    def _apply_theme(self, mode: str, *, persist: bool = False) -> None:
        active = theme.set_mode(mode)
        clear_cache()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(app_stylesheet())
        self._update_tab_icons()
        refresh_icons(self)
        refresh_statuses(self)
        self.automation_tab.apply_theme()
        for badge in (self.badge_tts, self.badge_ffmpeg, self.badge_youtube):
            badge.apply_theme()
        self.project_tab.apply_theme()
        self.settings_tab.apply_theme()
        self.tts_tab.apply_theme()
        self.video_tab.apply_theme()
        self.frame_tab.apply_theme()
        self.youtube_tab.apply_theme()
        self._sync_theme_button()
        if persist:
            self.settings.setdefault("app", {})["theme"] = active
            self._collect_all()

    def _update_actions(self) -> None:
        idle = (
            self.worker is None
            and not self._preview_busy
            and not self._auth_busy
            and not self._script_busy
        )
        running = self.worker is not None
        self.automation_tab.set_actions(idle, running)
        for position in range(self.tabs.count()):
            if position != self._automation_index:
                self.tabs.setTabEnabled(position, not running)
        self.btn_header_project.setEnabled(not running)
        self.btn_header_open.setEnabled(not running)
        self.btn_refresh_status.setEnabled(not running)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.worker is not None:
            answer = QMessageBox.question(
                self,
                "Pipeline đang chạy",
                "Pipeline vẫn đang chạy. Dừng lại và thoát?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.stop()
            if not self.worker.wait(20000):
                QMessageBox.warning(
                    self,
                    "Chưa dừng xong",
                    "Tác vụ chưa dừng hẳn (có thể đang nạp model). Hãy thử thoát lại sau ít phút.",
                )
                event.ignore()
                return
        if self.script_tab.busy():
            answer = QMessageBox.question(
                self,
                "Đang xử lý kịch bản",
                "Đang lấy transcript / chuyển thể. Huỷ và thoát?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if not self.script_tab.stop_and_wait(15000):
                QMessageBox.warning(
                    self,
                    "Chưa dừng xong",
                    "Tác vụ kịch bản chưa dừng hẳn. Hãy thử thoát lại sau ít phút.",
                )
                event.ignore()
                return
        if self.tts_tab.preview_running():
            answer = QMessageBox.question(
                self,
                "Đang lồng tiếng",
                "Tác vụ lồng tiếng / nghe thử đang chạy. Huỷ và thoát?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.tts_tab.stop_and_wait(15000)
            if self.tts_tab.preview_running():
                QMessageBox.warning(
                    self,
                    "Chưa dừng xong",
                    "Tác vụ lồng tiếng chưa dừng hẳn. Hãy thử thoát lại sau ít phút.",
                )
                event.ignore()
                return
        if self.youtube_tab.busy():
            QMessageBox.information(
                self,
                "Đang xác thực YouTube",
                "Tiến trình đăng nhập/kiểm tra kênh đang chạy. Hãy đợi xong rồi thoát.",
            )
            event.ignore()
            return
        try:
            self._collect_all()
        except Exception:
            logger.exception("Không lưu được cài đặt khi thoát.")
        if self._log_handler is not None:
            detach_handler(
                logging.getLogger(APP_LOGGER_NAME), self._log_handler
            )
            self._log_handler = None
        super().closeEvent(event)
