"""FFmpeg video renderer: audio grouping, cover art, chapters and progress.

The renderer turns the MP3 files produced by the TTS stage into MP4 files.  It is
deliberately free of Qt so it can be unit-tested and reused by the CLI/pipeline.

Highlights
----------
* Grouping: one video per input, one video per group of N, or one video for all.
* Visuals: single image, looping slideshow, looping background video.
* Optional Ken Burns (single image), waveform overlay, chapter titles (ASS),
  Vietnamese subtitle burning (ASS) and BGM with ducking during narration.
* Hardware encoders (NVENC/AMF/QSV) with an automatic libx264 fallback.
* Real-time progress parsed from ``ffmpeg -progress pipe:1``.
* Pause/resume (NtSuspendProcess on Windows) and cooperative cancellation.
* Atomic output: the file only replaces the final name after a successful encode.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.ai_images import (
    AIImageCancelled,
    AIImageError,
    ChapterImages,
    DEFAULT_MODEL as AI_DEFAULT_MODEL,
    DEFAULT_STYLE as AI_DEFAULT_STYLE,
    ensure_images,
    resolve_ai_dir,
)
from core.frame_layout import FrameImage, FrameLayout, color_ffmpeg
from core.languages import (
    DEFAULT_LANGUAGE,
    font_candidates,
    normalize_language,
    subtitle_font,
)
from core.project_manager import (
    ProjectItem,
    ProjectManager,
    VIDEO_DONE,
    VIDEO_ERROR,
    VIDEO_PENDING,
    VIDEO_PROCESSING,
    VIDEO_UPLOAD_DONE,
    VideoJob,
)
from core.settings import get_setting
from core.subtitles import parse_srt, parse_vtt
from utils.files import ensure_dir, natural_key, natural_sorted, safe_filename
from utils.logger import get_logger
from utils.media_probe import (
    build_timestamps,
    detect_encoders,
    detect_hwaccels,
    ffmpeg_available,
    probe_duration,
    resolve_tool,
)

logger = get_logger("render")

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

ProgressCb = Optional[Callable[[int, str], None]]
PauseCb = Optional[Callable[[], bool]]
CancelCb = Optional[Callable[[], bool]]


class RenderError(RuntimeError):
    """A render step failed (ffmpeg missing, bad input, encoder error...)."""


class RenderCancelled(RenderError):
    """The user stopped the render."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── subprocess helpers ───────────────────────────────────────────────────────
def _suspend_resume(pid: int, suspend: bool) -> bool:
    """Freeze/resume a process tree on Windows. Returns True on success."""
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process_suspend_resume = 0x0800
        handle = kernel32.OpenProcess(process_suspend_resume, False, int(pid))
        if not handle:
            return False
        try:
            func = ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess
            func.argtypes = [wintypes.HANDLE]
            func.restype = ctypes.c_long
            return func(handle) == 0
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass


def _tail(lines: Iterable[str], count: int = 6) -> str:
    tail = [line.strip() for line in lines if line.strip()]
    return " | ".join(tail[-count:])


def _run_simple(cmd: Sequence[str], cwd: Optional[Path] = None,
                timeout: float = 1800.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [str(part) for part in cmd],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise RenderError(f"Không tìm thấy ffmpeg ({cmd[0]}). Kiểm tra lại cấu hình.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError("Lệnh ffmpeg chạy quá lâu và đã bị huỷ.") from exc


def _parse_ffmpeg_time(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    if ":" in value:
        try:
            hours, minutes, seconds = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getframerate():
                return float(handle.getnframes()) / float(handle.getframerate())
    except Exception:
        pass
    return probe_duration(path)


# ── configuration ────────────────────────────────────────────────────────────
@dataclass
class VideoConfig:
    grouping: str = "one"           # all | group | one
    group_size: int = 10
    gap: float = 1.5
    visual: str = "image"           # image | slideshow | video | ai
    image: str = ""
    slideshow_dir: str = ""
    slide_seconds: float = 5.0
    video: str = ""
    video_files: List[str] = field(default_factory=list)
    video_pool_dir: str = ""
    video_pool_count: int = 0
    ai_mode: str = "manual"         # manual | imagen
    ai_dir: str = ""
    ai_count: int = 1
    ai_style: str = AI_DEFAULT_STYLE
    ai_model: str = AI_DEFAULT_MODEL
    ai_api_key: str = ""
    ai_xfade: float = 0.5
    ai_motion: bool = True
    bgm: str = ""
    bgm_volume_db: float = -18.0
    bgm_duck: float = 0.25          # 0.0–1.0: BGM level during narration
    resolution: str = "1920x1080"
    fps: int = 30
    encoder: str = "auto"
    crf: int = 20
    ken_burns: bool = False
    showwaves: bool = False
    chapter_titles: bool = False
    subtitle_enabled: bool = False
    subtitle_srt: bool = False        # burn an existing .srt instead of narration
    subtitle_file: str = ""           # explicit .srt; empty = auto-detect per item
    write_srt: bool = False
    frame: FrameLayout = field(default_factory=FrameLayout)
    keep_temp: bool = False

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "VideoConfig":
        raw = settings.get("video") or {}
        files = raw.get("video_files") or []
        if isinstance(files, str):
            files = [part.strip() for part in files.replace("\n", ",").split(",") if part.strip()]
        return cls(
            grouping=str(raw.get("grouping") or "one").lower(),
            group_size=max(1, int(raw.get("group_size") or 10)),
            gap=max(0.0, float(raw.get("gap", 1.5))),
            visual=str(raw.get("visual") or "image").lower(),
            image=str(raw.get("image") or ""),
            slideshow_dir=str(raw.get("slideshow_dir") or ""),
            slide_seconds=max(0.5, float(raw.get("slide_seconds", 5.0))),
            video=str(raw.get("video") or ""),
            video_files=[str(f) for f in files if str(f).strip()],
            video_pool_dir=str(raw.get("video_pool_dir") or ""),
            video_pool_count=max(0, int(raw.get("video_pool_count") or 0)),
            ai_mode=str(raw.get("ai_mode") or "manual").lower(),
            ai_dir=str(raw.get("ai_dir") or ""),
            ai_count=max(1, min(10, int(raw.get("ai_count", 1) or 1))),
            ai_style=str(raw.get("ai_style") or AI_DEFAULT_STYLE),
            ai_model=str(raw.get("ai_model") or AI_DEFAULT_MODEL),
            ai_api_key=str(raw.get("ai_api_key")
                           or os.environ.get("GEMINI_API_KEY") or ""),
            ai_xfade=max(0.0, min(2.0, float(raw.get("ai_xfade", 0.5) or 0.0))),
            ai_motion=bool(raw.get("ai_motion", True)),
            bgm=str(raw.get("bgm") or ""),
            bgm_volume_db=float(raw.get("bgm_volume_db", -18)),
            bgm_duck=max(0.0, min(1.0, float(
                get_setting(settings, "video.bgm_duck", 0.25) or 0.25))),
            resolution=str(raw.get("resolution") or "1920x1080"),
            fps=max(1, min(120, int(raw.get("fps") or 30))),
            encoder=str(raw.get("encoder") or "auto").lower(),
            crf=max(0, min(51, int(raw.get("crf", 20)))),
            ken_burns=bool(raw.get("ken_burns", False)),
            showwaves=bool(raw.get("showwaves", False)),
            chapter_titles=bool(raw.get("chapter_titles", False)),
            subtitle_enabled=bool(get_setting(settings, "video.subtitle_enabled", False)),
            subtitle_srt=bool(raw.get("subtitle_srt", False)),
            subtitle_file=str(raw.get("subtitle_file") or ""),
            write_srt=bool(raw.get("write_srt", False)),
            frame=FrameLayout.from_settings(settings),
            keep_temp=bool(raw.get("keep_temp", False)),
        )

    @property
    def dimensions(self) -> Tuple[int, int]:
        match = re.search(r"(\d+)\s*[xX×]\s*(\d+)", self.resolution or "")
        if match:
            width, height = int(match.group(1)), int(match.group(2))
        else:
            width, height = 1920, 1080
        return _even(width), _even(height)

    @property
    def width(self) -> int:
        return self.dimensions[0]

    @property
    def height(self) -> int:
        return self.dimensions[1]

    def errors(self) -> List[str]:
        problems: List[str] = []
        if self.grouping not in ("all", "group", "one"):
            problems.append(f"Kiểu gộp audio không hợp lệ: {self.grouping}")
        if self.visual not in ("image", "slideshow", "video", "video_pool", "ai"):
            problems.append(f"Kiểu hình nền không hợp lệ: {self.visual}")
        if self.ai_mode not in ("manual", "imagen"):
            problems.append(f"Chế độ ảnh AI không hợp lệ: {self.ai_mode}")
        if not (1 <= self.ai_count <= 10):
            problems.append("Số ảnh AI mỗi chương phải trong khoảng 1–10.")
        if self.group_size < 1:
            problems.append("Số audio mỗi phần phải >= 1.")
        if self.gap < 0:
            problems.append("Khoảng nghỉ giữa các audio không được âm.")
        if not (1 <= self.fps <= 120):
            problems.append("FPS phải trong khoảng 1–120.")
        if not (0 <= self.crf <= 51):
            problems.append("CRF phải trong khoảng 0–51.")
        if self.encoder not in ("auto", "libx264", "h264_nvenc", "h264_amf", "h264_qsv"):
            problems.append(f"Encoder không hỗ trợ: {self.encoder}")
        if not (0.0 <= self.bgm_duck <= 1.0):
            problems.append("Độ giảm nhạc nền (bgm_duck) phải trong khoảng 0.0–1.0.")
        match = re.search(r"(\d+)\s*[xX×]\s*(\d+)", self.resolution or "")
        if not match or int(match.group(1)) < 64 or int(match.group(2)) < 64:
            problems.append("Độ phân giải phải có dạng WxH và lớn hơn 64px.")
        return problems


def _even(value: int) -> int:
    return value if value % 2 == 0 else value + 1


# ── job planning ─────────────────────────────────────────────────────────────
@dataclass
class _Plan:
    id: str
    title: str
    path: Path
    items: List[ProjectItem]


def _item_label(item: ProjectItem) -> str:
    return " ".join(str(item.metadata.get("title") or item.base).split()) or item.base


def _project_name(manager: ProjectManager) -> str:
    return manager.project_dir.name or "AutoYout"


def _resolve_user_path(value: str, manager: Optional[ProjectManager] = None) -> Optional[Path]:
    raw = (value or "").strip().strip('"')
    if not raw:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        bases: List[Path] = []
        if manager is not None:
            bases += [manager.project_dir, manager.assets_dir]
        bases.append(Path.cwd())
        for base in bases:
            candidate = base / path
            if candidate.exists():
                return candidate
        path = bases[0] / path
    return path


def _first_image(folder: Path) -> Optional[Path]:
    if not folder.is_dir():
        return None
    for path in natural_sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTS:
            return path
    return None


def _first_audio(folder: Path) -> Optional[Path]:
    if not folder.is_dir():
        return None
    for path in natural_sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in _AUDIO_EXTS:
            return path
    return None


class FFmpegRenderer:
    """Build render plans and encode them with ffmpeg."""

    def __init__(self, settings: Dict[str, Any]) -> None:
        paths = settings.get("paths") or {}
        self.ffmpeg = resolve_tool(paths.get("ffmpeg"), "ffmpeg")
        self.ffprobe = resolve_tool(paths.get("ffprobe"), "ffprobe")
        self._encoders: Optional[set[str]] = None
        self._hwaccels: Optional[set[str]] = None
        self._filter_cache: Dict[str, bool] = {}
        self._resolved_encoder: str = ""

    # ── capability checks ────────────────────────────────────────────────────
    @property
    def available_encoders(self) -> List[str]:
        if self._encoders is None:
            self._encoders = detect_encoders(self.ffmpeg)
        known = ["libx264", "h264_nvenc", "h264_amf", "h264_qsv"]
        return [name for name in known if name in self._encoders]

    @property
    def hwaccels(self) -> set[str]:
        if self._hwaccels is None:
            self._hwaccels = detect_hwaccels(self.ffmpeg)
        return self._hwaccels

    def has_filter(self, name: str) -> bool:
        if name not in self._filter_cache:
            proc = _run_simple([self.ffmpeg, "-hide_banner", "-filters"], timeout=30)
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            self._filter_cache[name] = bool(re.search(rf"\s{re.escape(name)}\s", text))
        return self._filter_cache[name]

    def environment(self) -> Dict[str, Any]:
        ok = ffmpeg_available(self.ffmpeg)
        return {
            "ffmpeg_ok": ok,
            "ffmpeg": self.ffmpeg,
            "encoders": self.available_encoders if ok else [],
            "hwaccels": sorted(self.hwaccels) if ok else [],
            "subtitles": self.has_filter("subtitles") if ok else False,
            "showwaves": self.has_filter("showwaves") if ok else False,
        }

    def preferred_encoder(self, cfg: VideoConfig) -> str:
        encoders = set(self.available_encoders)
        if cfg.encoder in ("libx264", "h264_nvenc", "h264_amf", "h264_qsv"):
            if cfg.encoder not in encoders:
                raise RenderError(
                    f"Encoder {cfg.encoder} không có trong bản ffmpeg này. "
                    f"Đang có: {', '.join(sorted(encoders)) or 'không có'}"
                )
            return cfg.encoder
        if self._resolved_encoder and self._resolved_encoder in encoders:
            return self._resolved_encoder
        candidates: List[str] = []
        hw = self.hwaccels
        if "cuda" in hw:
            candidates.append("h264_nvenc")
        if "amf" in hw:
            candidates.append("h264_amf")
        if "qsv" in hw:
            candidates.append("h264_qsv")
        candidates.append("libx264")
        for name in candidates:
            if name in encoders:
                return name
        return "libx264"

    # ── planning ─────────────────────────────────────────────────────────────
    def build_jobs(self, manager: ProjectManager, config: Optional[VideoConfig] = None,
                   force: bool = False) -> List[VideoJob]:
        """Create/update ``VideoJob`` entries for the current input set."""
        cfg = config or VideoConfig.from_settings({})
        problems = cfg.errors()
        if problems:
            raise RenderError("Cấu hình video không hợp lệ: " + "; ".join(problems))

        items = manager.get_items()
        for item in items:
            manager.ensure_item_audio_dir(item)
        plans = self._plans(manager, items, cfg)
        used_paths: set[str] = set()
        jobs: List[VideoJob] = []
        for plan in plans:
            path = plan.path
            suffix = 2
            while str(path).casefold() in used_paths:
                path = path.with_name(f"{plan.path.stem}_{suffix}{plan.path.suffix}")
                suffix += 1
            used_paths.add(str(path).casefold())
            old = manager.videos.get(plan.id)
            job = VideoJob(id=plan.id, path=path, items=[item.base for item in plan.items],
                           title=plan.title)
            if old is not None and old.items == job.items and old.path == path:
                job.state = old.state
                job.duration = old.duration
                job.timestamps = list(old.timestamps)
                job.timestamps_text = old.timestamps_text
                job.error = old.error
                job.youtube_video_id = old.youtube_video_id
                job.youtube_url = old.youtube_url
                job.scheduled_at = old.scheduled_at
                job.rendered_at = old.rendered_at
                job.uploaded_at = old.uploaded_at
                job.audio_mtime = old.audio_mtime
            stale = self.needs_render(manager, job, force=force)
            if stale:
                job.state = VIDEO_PENDING
                job.error = ""
                job.duration = 0.0
                job.timestamps = []
                job.timestamps_text = ""
                job.rendered_at = ""
                if not path.is_file():
                    job.uploaded_at = old.uploaded_at if old is not None else ""
            elif job.state in (VIDEO_ERROR, VIDEO_PENDING, VIDEO_PROCESSING):
                job.state = VIDEO_DONE
                job.error = ""
                if not job.duration and path.is_file():
                    job.duration = probe_duration(path)
            elif job.state not in (VIDEO_DONE, VIDEO_UPLOAD_DONE):
                job.state = VIDEO_DONE if path.is_file() else VIDEO_PENDING
            jobs.append(job)

        valid_ids = {job.id for job in jobs}
        manager.videos = {job.id: job for job in jobs}
        manager.remove_stale_videos(valid_ids)
        video_by_item = {base: job.id for job in jobs for base in job.items}
        for base, item in manager.items.items():
            item.video_id = video_by_item.get(base, "")
        manager.save_state()
        return jobs

    def _plans(self, manager: ProjectManager, items: List[ProjectItem],
               cfg: VideoConfig) -> List[_Plan]:
        if not items:
            return []
        project = safe_filename(_project_name(manager), "AutoYout")
        if cfg.grouping == "all":
            title = _project_name(manager)
            return [_Plan(
                id="all",
                title=title,
                path=manager.video_dir / f"{project}.mp4",
                items=list(items),
            )]
        if cfg.grouping == "group":
            plans: List[_Plan] = []
            size = max(1, cfg.group_size)
            for index in range(0, len(items), size):
                chunk = items[index:index + size]
                part = index // size + 1
                plans.append(_Plan(
                    id=f"group::{part:02d}",
                    title=f"{_project_name(manager)} — Phần {part:02d}",
                    path=manager.video_dir / f"{project}_part_{part:02d}.mp4",
                    items=chunk,
                ))
            return plans
        plans = []
        for item in items:
            plans.append(_Plan(
                id=f"one::{item.base}",
                title=_item_label(item),
                path=manager.video_dir / f"{safe_filename(item.base)}.mp4",
                items=[item],
            ))
        return plans

    def needs_render(self, manager: ProjectManager, job: VideoJob,
                     force: bool = False) -> bool:
        if force:
            return True
        path = job.path
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return True
        except OSError:
            return True
        if job.state in (VIDEO_PENDING, VIDEO_PROCESSING, VIDEO_ERROR):
            return True
        items = manager.items_for(job.items)
        if len(items) != len(job.items):
            return True
        if any(not manager.audio_exists(item) for item in items):
            return True
        expected_mtime = max((item.audio_mtime or 0.0) for item in items)
        if expected_mtime <= 0:
            try:
                expected_mtime = max(item.audio_path.stat().st_mtime for item in items)
            except OSError:
                return True
        if job.audio_mtime <= 0 or abs(expected_mtime - job.audio_mtime) > 1.0:
            return True
        return False

    # ── public render API ────────────────────────────────────────────────────
    def render_job(self, manager: ProjectManager, job_id: str,
                   config: Optional[VideoConfig] = None,
                   progress_cb: ProgressCb = None,
                   pause_cb: PauseCb = None,
                   cancel_cb: CancelCb = None) -> VideoJob:
        cfg = config or VideoConfig.from_settings({})
        problems = cfg.errors()
        if problems:
            raise RenderError("Cấu hình video không hợp lệ: " + "; ".join(problems))
        job = manager.get_video(job_id)
        if job is None:
            raise RenderError(f"Không tìm thấy video job {job_id}.")
        items = manager.items_for(job.items)
        if len(items) != len(job.items):
            raise RenderError("Thiếu file đầu vào cho video " + job_id)
        self._validate_inputs(manager, items, cfg)

        def report(percent: int, message: str) -> None:
            if progress_cb:
                try:
                    progress_cb(max(0, min(100, int(percent))), message)
                except Exception:
                    pass

        job.state = VIDEO_PROCESSING
        job.error = ""
        manager.save_state()
        temp_dir = Path(tempfile.mkdtemp(
            prefix=f"render_{safe_filename(job.id)}_",
            dir=str(ensure_dir(manager.video_dir / ".tmp")),
        ))
        keep_temp = cfg.keep_temp
        try:
            report(1, "Đang chuẩn bị dữ liệu...")
            combined, segments, total = self._prepare_audio(
                items, temp_dir, cfg, report, cancel_cb)
            self._check_cancel(cancel_cb)
            entries = self._chapter_entries(segments, total)
            srt_events: List[Tuple[float, float, str]] = []
            if cfg.subtitle_srt:
                srt_events = self._srt_events(manager, items, cfg, segments)
                if not srt_events:
                    raise RenderError("File phụ đề .srt không có dòng nào hợp lệ.")
            visual = self._prepare_visual(
                manager, items, cfg, temp_dir, report, cancel_cb,
                segments=segments, total=total)
            frame_images = self._frame_images(manager, cfg)
            report(24, "Đang dựng video...")

            out_path = temp_dir / "out.mp4"
            self._encode_with_fallback(
                cfg, temp_dir, combined, visual, entries, total,
                out_path, job, report, pause_cb, cancel_cb, manager,
                segments=segments, srt_events=srt_events,
                frame_images=frame_images)

            self._check_cancel(cancel_cb)
            report(99, "Đang hoàn tất...")
            job.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(str(out_path), str(job.path))
            except OSError as exc:
                raise RenderError(
                    f"Không ghi được file video (file đang mở?): {job.path} — {exc}"
                ) from exc

            if cfg.write_srt:
                srt_path = job.path.with_suffix(".srt")
                try:
                    if self._write_srt(segments, total, srt_path):
                        logger.info("Đã ghi phụ đề SRT: %s", srt_path.name)
                except OSError as exc:
                    logger.warning("Không ghi được SRT %s: %s", srt_path, exc)

            job.state = VIDEO_DONE
            job.error = ""
            job.duration = round(total, 3)
            job.timestamps = [[round(start, 3), label] for start, label in entries]
            job.timestamps_text = build_timestamps(entries)
            job.rendered_at = _now()
            job.audio_mtime = max((item.audio_mtime or 0.0) for item in items)
            manager.set_items_state(job.items, VIDEO_DONE)
            manager.save_state()
            report(100, "Đã tạo xong video.")
            logger.info("Render xong %s (%.1fs, %d chương)", job.path.name, total, len(entries))
            return job
        except RenderCancelled:
            job.state = VIDEO_PENDING
            job.error = "Đã huỷ tạo video."
            manager.save_state()
            raise
        except RenderError as exc:
            job.state = VIDEO_ERROR
            job.error = str(exc)
            manager.save_state()
            keep_temp = True
            logger.error("Render lỗi %s: %s", job_id, exc)
            raise
        except Exception as exc:  # unexpected: log + surface a friendly error
            job.state = VIDEO_ERROR
            job.error = f"Lỗi không mong đợi: {exc}"
            manager.save_state()
            keep_temp = True
            logger.exception("Render lỗi không mong đợi %s", job_id)
            raise RenderError(job.error) from exc
        finally:
            if not keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                logger.info("Giữ thư mục tạm: %s", temp_dir)

    @staticmethod
    def has_audio(manager: ProjectManager, job: VideoJob) -> bool:
        """Return True if all items in the job have an existing audio file on disk."""
        items = manager.items_for(job.items)
        if not items or len(items) != len(job.items):
            return False
        return all(manager.audio_exists(item) for item in items)

    def render_all(self, manager: ProjectManager, config: Optional[VideoConfig] = None,
                   force: bool = False, only: Optional[Iterable[str]] = None,
                   progress_cb: ProgressCb = None,
                   pause_cb: PauseCb = None,
                   cancel_cb: CancelCb = None,
                   stop_on_error: bool = False,
                   skip_missing_audio: bool = True) -> Tuple[List[VideoJob], List[str]]:
        cfg = config or VideoConfig.from_settings({})
        jobs = self.build_jobs(manager, cfg, force=force)
        wanted = set(only) if only is not None else None

        # "cái nào có audio thì tạo còn không có thì thôi"
        # Chỉ render những job có đầy đủ audio; bỏ qua các job chưa có audio mà không báo lỗi.
        errors: List[str] = []
        todo: List[VideoJob] = []
        skipped_no_audio: List[VideoJob] = []
        for job in jobs:
            if wanted is not None and job.id not in wanted:
                continue
            if not self.has_audio(manager, job):
                if wanted is not None and len(wanted) == 1 and not skip_missing_audio:
                    items = manager.items_for(job.items)
                    missing = [item.base for item in items if not manager.audio_exists(item)]
                    errors.append(f"{job.title}: Chưa có audio cho: {', '.join(missing[:5])}")
                else:
                    skipped_no_audio.append(job)
                continue
            if self.needs_render(manager, job, force=force):
                todo.append(job)

        if skipped_no_audio:
            logger.info(
                "Bỏ qua %d video chưa có audio: %s",
                len(skipped_no_audio),
                ", ".join(j.id for j in skipped_no_audio[:5]),
            )

        done: List[VideoJob] = []
        for index, job in enumerate(todo):
            def job_progress(percent: int, message: str, _i=index, _job=job) -> None:
                if progress_cb:
                    overall = int((_i * 100 + percent) / max(1, len(todo)))
                    try:
                        progress_cb(overall, f"[{_job.title}] {message}", percent)
                    except TypeError:
                        progress_cb(overall, f"[{_job.title}] {message}")
            try:
                done.append(self.render_job(
                    manager, job.id, cfg, job_progress, pause_cb, cancel_cb))
            except RenderCancelled:
                raise
            except RenderError as exc:
                errors.append(f"{job.title}: {exc}")
                logger.error("Video lỗi: %s — %s", job.id, exc)
                if stop_on_error:
                    break
        return done, errors

    # ── input validation ─────────────────────────────────────────────────────
    def _validate_inputs(self, manager: ProjectManager, items: List[ProjectItem],
                         cfg: VideoConfig) -> None:
        if not ffmpeg_available(self.ffmpeg):
            raise RenderError(f"ffmpeg không chạy được ({self.ffmpeg}). Kiểm tra cài đặt.")
        missing = [item.base for item in items if not manager.audio_exists(item)]
        if missing:
            raise RenderError("Chưa có audio cho: " + ", ".join(missing[:5]))
        if not self.has_filter("showwaves") and (cfg.showwaves or cfg.frame.wave_active):
            raise RenderError("Bản ffmpeg này thiếu filter showwaves.")
        if (cfg.chapter_titles or cfg.subtitle_enabled or cfg.subtitle_srt) \
                and not self.has_filter("subtitles"):
            raise RenderError("Bản ffmpeg này thiếu filter subtitles (libass).")
        if cfg.subtitle_srt and not self._srt_sources(manager, items, cfg):
            raise RenderError(
                "Không tìm thấy file phụ đề .srt. Đặt <tên>.srt cạnh file audio "
                "hoặc chọn file .srt trong tab Video."
            )
        self._frame_images(manager, cfg)
        if cfg.visual == "video":
            sources = self._video_sources(manager, cfg)
            if not sources:
                raise RenderError("Chưa chọn video nền (hoặc file không tồn tại).")
        if cfg.visual == "video_pool":
            if not self._video_pool_sources(manager, cfg):
                raise RenderError(
                    "Thư mục video pool không có clip .mp4/.mov/.mkv/.webm nào."
                )
        if cfg.bgm:
            bgm = _resolve_user_path(cfg.bgm, manager)
            if bgm is None or not (bgm.is_file() or bgm.is_dir()):
                raise RenderError(f"Không tìm thấy nhạc nền: {cfg.bgm}")
        if cfg.visual == "slideshow":
            folder = self._slideshow_folder(manager, cfg)
            if folder is None or _first_image(folder) is None:
                raise RenderError(f"Thư mục slideshow không có ảnh: {cfg.slideshow_dir or folder}")
        if cfg.visual == "ai":
            folder = resolve_ai_dir(manager, cfg.ai_dir)
            if cfg.ai_mode == "imagen" and not cfg.ai_api_key.strip():
                raise RenderError(
                    "Chưa có API key Google Imagen. Nhập key trong tab Video "
                    "(hoặc đặt biến môi trường GEMINI_API_KEY)."
                )
            if cfg.ai_mode == "manual" and not folder.is_dir():
                raise RenderError(
                    f"Thư mục ảnh chương chưa có: {folder}. Tạo thư mục và đặt ảnh "
                    "<tên_chương>_01.png…"
                )
        if cfg.ken_burns and cfg.visual not in ("image", "ai"):
            logger.warning("Ken Burns chỉ áp dụng cho ảnh đơn — bỏ qua.")

    # ── audio preparation ────────────────────────────────────────────────────
    def _prepare_audio(self, items: List[ProjectItem], temp_dir: Path,
                       cfg: VideoConfig, report: Callable[[int, str], None],
                       cancel_cb: CancelCb) -> Tuple[Path, List[Tuple[ProjectItem, float, float]], float]:
        segments: List[Tuple[ProjectItem, float, float]] = []
        names: List[str] = []
        cursor = 0.0
        gap_name = ""
        if cfg.gap > 0.01:
            gap_name = "gap.wav"
            self._write_silence(temp_dir / gap_name, cfg.gap)

        for index, item in enumerate(items):
            self._check_cancel(cancel_cb)
            name = f"seg_{index:04d}.wav"
            target = temp_dir / name
            cmd = [
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(item.audio_path),
                "-vn", "-map", "0:a:0",
                "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
                "-map_metadata", "-1",
                name,
            ]
            proc = _run_simple(cmd, cwd=temp_dir)
            if proc.returncode != 0:
                raise RenderError(
                    f"Không giải mã được audio {item.base}: {_tail((proc.stderr or '').splitlines())}"
                )
            duration = _wav_duration(target)
            if duration <= 0:
                raise RenderError(f"Audio {item.base} rỗng hoặc không đọc được.")
            segments.append((item, cursor, duration))
            names.append(name)
            cursor += duration
            if index < len(items) - 1 and gap_name:
                names.append(gap_name)
                cursor += cfg.gap
            report(4 + int(12 * (index + 1) / max(1, len(items))),
                   f"Chuẩn bị audio {index + 1}/{len(items)}")

        self._check_cancel(cancel_cb)
        report(18, "Ghép audio...")
        if not names:
            raise RenderError("Không có audio để ghép.")
        list_path = temp_dir / "audio_concat.txt"
        list_path.write_text(
            "\n".join(f"file '{self._escape_concat(name)}'" for name in names) + "\n",
            encoding="utf-8",
        )
        combined = temp_dir / "combined.wav"
        proc = _run_simple([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", "audio_concat.txt",
            "-c", "copy", "combined.wav",
        ], cwd=temp_dir)
        if proc.returncode != 0:
            raise RenderError("Ghép audio lỗi: " + _tail((proc.stderr or "").splitlines()))
        combined_duration = _wav_duration(combined)
        if combined_duration <= 0:
            raise RenderError("File audio ghép không hợp lệ.")
        if abs(combined_duration - cursor) > 1.0:
            logger.warning("Audio ghép dài %.2fs, dự kiến %.2fs", combined_duration, cursor)
        return combined, segments, cursor

    @staticmethod
    def _escape_concat(name: str) -> str:
        return name.replace("'", "\\'")

    @staticmethod
    def _write_silence(path: Path, seconds: float) -> None:
        rate = 48000
        frames = max(1, int(round(rate * seconds)))
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\x00\x00" * frames)

    # ── visual preparation ───────────────────────────────────────────────────
    def _slideshow_folder(self, manager: ProjectManager,
                          cfg: VideoConfig) -> Optional[Path]:
        if cfg.slideshow_dir:
            folder = _resolve_user_path(cfg.slideshow_dir, manager)
            if folder is not None:
                return folder
        return manager.assets_dir

    def _video_sources(self, manager: ProjectManager, cfg: VideoConfig) -> List[Path]:
        raw = [cfg.video] if cfg.video else []
        raw += list(cfg.video_files or [])
        sources: List[Path] = []
        seen: set[str] = set()
        for value in raw:
            if not str(value).strip():
                continue
            path = _resolve_user_path(str(value), manager)
            if path is None or not path.is_file():
                if path is not None and (manager.assets_dir / path.name).is_file():
                    path = manager.assets_dir / path.name
                else:
                    logger.warning("Bỏ qua video nền không tồn tại: %s", value)
                    continue
            key = str(path.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            sources.append(path)
        return sources

    def _video_pool_sources(self, manager: ProjectManager,
                            cfg: VideoConfig) -> List[Path]:
        """Random clips from a folder (all shuffled when count is 0)."""
        folder = _resolve_user_path(cfg.video_pool_dir, manager) if cfg.video_pool_dir else None
        if folder is None or not folder.is_dir():
            folder = manager.assets_dir / "video_pool"
        if not folder.is_dir():
            return []
        clips = [path for path in natural_sorted(folder.iterdir())
                 if path.is_file() and path.suffix.lower() in _VIDEO_EXTS]
        if not clips:
            return []
        random.shuffle(clips)
        count = int(cfg.video_pool_count or 0)
        if count > 0:
            clips = clips[:count]
        logger.info("Video pool: chọn %d/%d clip ngẫu nhiên từ %s",
                    len(clips), count or len(clips), folder)
        return clips

    def _prepare_visual(self, manager: ProjectManager, items: List[ProjectItem],
                        cfg: VideoConfig, temp_dir: Path,
                        report: Callable[[int, str], None],
                        cancel_cb: CancelCb = None,
                        segments: Optional[List[Tuple[ProjectItem, float, float]]] = None,
                        total: float = 0.0) -> Dict[str, Any]:
        width, height = cfg.dimensions
        if cfg.visual == "ai":
            return self._prepare_ai_visual(
                manager, items, cfg, temp_dir, report, cancel_cb,
                segments or [], total)
        if cfg.visual == "image":
            image = self._cover_image(manager, items, cfg)
            if image is None:
                raise RenderError(
                    "Chưa có ảnh nền. Chọn ảnh trong tab Video hoặc đặt ảnh vào thư mục assets."
                )
            report(22, "Chuẩn bị ảnh nền...")
            return {"kind": "image", "path": str(image)}
        if cfg.visual == "slideshow":
            folder = self._slideshow_folder(manager, cfg)
            images = [p for p in natural_sorted(folder.iterdir())
                      if p.is_file() and p.suffix.lower() in _IMAGE_EXTS] if folder else []
            if not images:
                raise RenderError(f"Thư mục slideshow không có ảnh: {folder}")
            report(20, f"Chuẩn bị {len(images)} ảnh slideshow...")
            slides: List[str] = []
            for index, source in enumerate(images):
                self._check_cancel(cancel_cb)
                name = f"slide_{index:03d}.jpg"
                cmd = [
                    self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(source),
                    "-vf", (
                        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
                    ),
                    "-frames:v", "1", "-q:v", "2", "-pix_fmt", "yuvj420p", name,
                ]
                proc = _run_simple(cmd, cwd=temp_dir)
                if proc.returncode != 0:
                    raise RenderError(
                        f"Không đọc được ảnh {source.name}: "
                        + _tail((proc.stderr or "").splitlines())
                    )
                slides.append(name)
                report(20 + int(4 * (index + 1) / len(images)),
                       f"Chuẩn bị ảnh {index + 1}/{len(images)}")
            lines: List[str] = []
            for name in slides:
                lines.append(f"file '{self._escape_concat(name)}'")
                lines.append(f"duration {cfg.slide_seconds:.3f}")
            lines.append(f"file '{self._escape_concat(slides[-1])}'")
            (temp_dir / "slides.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return {"kind": "slideshow", "path": "slides.txt"}
        if cfg.visual == "video_pool":
            sources = self._video_pool_sources(manager, cfg)
            if not sources:
                raise RenderError(
                    "Thư mục video pool không có clip .mp4/.mov/.mkv/.webm nào.")
        else:
            sources = self._video_sources(manager, cfg)
            if not sources:
                raise RenderError("Chưa chọn video nền (hoặc file không tồn tại).")
        if len(sources) == 1:
            return {"kind": "video", "path": str(sources[0])}
        report(20, "Chuẩn bị video nền...")
        pieces: List[str] = []
        for index, source in enumerate(sources):
            self._check_cancel(cancel_cb)
            name = f"bg_{index:03d}.mp4"
            cmd = [
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-vf", (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"setsar=1,fps={cfg.fps}"
                ),
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", name,
            ]
            proc = _run_simple(cmd, cwd=temp_dir)
            if proc.returncode != 0:
                raise RenderError(
                    f"Không xử lý được video {source.name}: "
                    + _tail((proc.stderr or "").splitlines())
                )
            pieces.append(name)
            report(20 + int(4 * (index + 1) / len(sources)),
                   f"Chuẩn bị video {index + 1}/{len(sources)}")
        list_path = temp_dir / "bg_concat.txt"
        list_path.write_text(
            "\n".join(f"file '{name}'" for name in pieces) + "\n", encoding="utf-8")
        proc = _run_simple([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", "bg_concat.txt",
            "-c", "copy", "bg_all.mp4",
        ], cwd=temp_dir)
        if proc.returncode != 0:
            raise RenderError("Ghép video nền lỗi: " + _tail((proc.stderr or "").splitlines()))
        return {"kind": "video", "path": "bg_all.mp4"}

    # ── AI chapter visuals ───────────────────────────────────────────────────
    def _prepare_ai_visual(self, manager: ProjectManager, items: List[ProjectItem],
                           cfg: VideoConfig, temp_dir: Path,
                           report: Callable[[int, str], None],
                           cancel_cb: CancelCb,
                           segments: List[Tuple[ProjectItem, float, float]],
                           total: float) -> Dict[str, Any]:
        if not segments:
            raise RenderError("Không có chương nào để dựng hình nền AI.")
        folder = resolve_ai_dir(manager, cfg.ai_dir)
        report(19, "Chuẩn bị ảnh cho từng chương...")
        try:
            chapters = ensure_images(
                manager, segments, folder=folder, mode=cfg.ai_mode,
                count=cfg.ai_count, api_key=cfg.ai_api_key, model=cfg.ai_model,
                style=cfg.ai_style, width=cfg.width, height=cfg.height,
                report=lambda pct, message: report(19 + int(4 * pct / 100), message),
                cancel_cb=cancel_cb,
            )
        except AIImageCancelled as exc:
            raise RenderCancelled(str(exc)) from exc
        except AIImageError as exc:
            raise RenderError(str(exc)) from exc
        for chapter in chapters:
            item = chapter.item
            if item.image_takes and 0 <= item.selected_image_index < len(item.image_takes):
                selected = Path(item.image_takes[item.selected_image_index])
                if selected.is_file():
                    chapter.paths = [selected]
        if not chapters:
            raise RenderError("Không có ảnh cho các chương.")

        timeline: List[Tuple[ChapterImages, float]] = []
        for index, chapter in enumerate(chapters):
            if index + 1 < len(chapters):
                end = chapters[index + 1].start
            else:
                end = max(total, chapter.start + chapter.duration)
            timeline.append((chapter, max(0.25, end - chapter.start)))

        clips: List[Path] = []
        prepared = 0.0
        for index, (chapter, length) in enumerate(timeline):
            self._check_cancel(cancel_cb)
            images = chapter.paths[:max(1, min(12, int(length / 0.1)))]
            if not images:
                raise RenderError(f"Chương '{chapter.item.base}' không có ảnh.")
            slot = length / len(images)
            fade = min(cfg.ai_xfade, slot / 4) if len(images) > 1 else 0.0
            if fade < 0.06:
                fade = 0.0
            base = f"ai_chap_{index:03d}"
            target = temp_dir / f"{base}.mp4"
            if len(images) == 1:
                self._render_ai_clip(images[0], target, length, index, cfg)
            else:
                parts: List[Path] = []
                for part_index, source in enumerate(images):
                    part = temp_dir / f"{base}_img_{part_index:02d}.mp4"
                    duration = slot + fade if fade > 0 else slot
                    self._render_ai_clip(
                        source, part, duration, index + part_index, cfg)
                    parts.append(part)
                if fade > 0:
                    self._xfade_clips(parts, fade, length, target, cfg, temp_dir)
                else:
                    self._concat_clips(parts, target, temp_dir)
            clips.append(target)
            prepared += length
            report(20 + int(3 * min(1.0, prepared / max(1e-6, total))),
                   f"Dựng hình chương {index + 1}/{len(timeline)}")
            logger.debug("AI chapter %d/%d: %d ảnh, %.1fs",
                         index + 1, len(timeline), len(images), length)

        final = temp_dir / "bg_ai.mp4"
        if len(clips) == 1:
            try:
                os.replace(str(clips[0]), str(final))
            except OSError:
                shutil.copy2(clips[0], final)
        else:
            self._concat_clips(clips, final, temp_dir)
        if not final.is_file() or final.stat().st_size == 0:
            raise RenderError("Không tạo được hình nền AI.")
        return {"kind": "video", "path": final.name, "loop": False}

    def _render_ai_clip(self, source: Path, target: Path, seconds: float,
                        index: int, cfg: VideoConfig) -> None:
        width, height = cfg.dimensions
        seconds = max(0.04, seconds)
        frames = max(1, int(round(seconds * cfg.fps)))
        if cfg.ai_motion:
            big_w, big_h = width * 2, height * 2
            if index % 2 == 0:
                zoom = f"1.02+0.08*on/{frames}"
            else:
                zoom = f"1.10-0.08*on/{frames}"
            vf = (
                f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
                f"crop={big_w}:{big_h},"
                f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d=1:s={width}x{height}:fps={cfg.fps},"
                f"setsar=1,format=yuv420p"
            )
        else:
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={cfg.fps},format=yuv420p"
            )
        cmd = [
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-framerate", str(cfg.fps),
            "-t", f"{seconds:.3f}", "-i", str(source),
            "-an", "-vf", vf,
            "-t", f"{seconds:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
            target.name,
        ]
        proc = _run_simple(cmd, cwd=target.parent)
        if proc.returncode != 0:
            raise RenderError(
                f"Không dựng được ảnh chương {source.name}: "
                + _tail((proc.stderr or "").splitlines())
            )

    def _xfade_clips(self, clips: List[Path], fade: float, seconds: float,
                     target: Path, cfg: VideoConfig, temp_dir: Path) -> None:
        cmd: List[str] = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        for clip in clips:
            cmd += ["-i", clip.name]
        slot = seconds / len(clips)
        parts: List[str] = []
        previous = "[0:v]"
        for index in range(1, len(clips)):
            offset = max(0.0, index * slot - fade)
            label = f"[vx{index}]"
            parts.append(
                f"{previous}[{index}:v]xfade=transition=fade:"
                f"duration={fade:.3f}:offset={offset:.3f}{label}"
            )
            previous = label
        cmd += [
            "-filter_complex", ";".join(parts),
            "-map", previous, "-an",
            "-t", f"{max(0.04, seconds):.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
            target.name,
        ]
        proc = _run_simple(cmd, cwd=temp_dir)
        if proc.returncode != 0:
            raise RenderError(
                "Ghép ảnh chương (xfade) lỗi: "
                + _tail((proc.stderr or "").splitlines())
            )

    def _concat_clips(self, clips: List[Path], target: Path, temp_dir: Path) -> None:
        list_path = temp_dir / f"{target.stem}_list.txt"
        list_path.write_text(
            "\n".join(f"file '{self._escape_concat(clip.name)}'" for clip in clips) + "\n",
            encoding="utf-8",
        )
        proc = _run_simple([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path.name,
            "-c", "copy", target.name,
        ], cwd=temp_dir)
        if proc.returncode == 0:
            return
        logger.warning("Concat copy ảnh AI lỗi, encode lại: %s",
                       _tail((proc.stderr or "").splitlines()))
        proc = _run_simple([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path.name,
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", target.name,
        ], cwd=temp_dir)
        if proc.returncode != 0:
            raise RenderError(
                "Ghép các đoạn ảnh AI lỗi: "
                + _tail((proc.stderr or "").splitlines())
            )

    def _cover_image(self, manager: ProjectManager, items: List[ProjectItem],
                     cfg: VideoConfig) -> Optional[Path]:
        if cfg.image:
            path = _resolve_user_path(cfg.image, manager)
            if path is not None and path.is_file():
                return path
            logger.warning("Ảnh nền đã chọn không tồn tại: %s", cfg.image)
        for item in items:
            thumb = str(item.metadata.get("thumbnail_path") or "")
            if thumb and Path(thumb).is_file():
                return Path(thumb)
        image = _first_image(manager.assets_dir)
        if image is not None:
            return image
        return None

    # ── chapters ─────────────────────────────────────────────────────────────
    def _chapter_entries(self, segments: List[Tuple[ProjectItem, float, float]],
                         total: float) -> List[Tuple[float, str]]:
        entries: List[Tuple[float, str]] = []
        for item, start, duration in segments:
            explicit: List[Tuple[float, str]] = []
            chapters = (item.metadata or {}).get("chapters") or []
            if isinstance(chapters, (list, tuple)):
                for chapter in chapters:
                    if not isinstance(chapter, dict):
                        continue
                    title = " ".join(str(chapter.get("title") or chapter.get("name") or "").split())
                    value = chapter.get("start", chapter.get("time"))
                    if not title or value is None:
                        continue
                    try:
                        offset = float(value)
                    except (TypeError, ValueError):
                        continue
                    if offset < 0:
                        continue
                    explicit.append((start + min(offset, duration), title))
            if explicit:
                explicit.sort(key=lambda pair: pair[0])
                if explicit[0][0] - start > 1.0:
                    explicit.insert(0, (start, _item_label(item)))
                entries.extend(explicit)
            else:
                entries.append((start, _item_label(item)))
        entries.sort(key=lambda pair: pair[0])
        deduped: List[Tuple[float, str]] = []
        for start, title in entries:
            if deduped and abs(deduped[-1][0] - start) < 0.25:
                continue
            if start > total:
                continue
            deduped.append((start, title))
        return deduped

    @staticmethod
    def _ass_text(value: str) -> str:
        text = " ".join(str(value or "").split())
        text = text.replace("{", "(").replace("}", ")")
        return text or "Chương"

    @staticmethod
    def _ass_time(seconds: float) -> str:
        seconds = max(0.0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}:{minutes:02d}:{secs:05.2f}"

    def _write_chapters(self, entries: List[Tuple[float, str]], total: float,
                        temp_dir: Path, cfg: VideoConfig,
                        language: str = DEFAULT_LANGUAGE) -> bool:
        if not entries:
            return False
        width, height = cfg.dimensions
        font_size = max(24, int(round(52 * height / 1080)))
        margin_v = max(60, int(round((250 if cfg.showwaves else 90) * height / 1080)))
        style = (
            f"Style: Chapter,{subtitle_font(language)},{font_size},"
            f"&H00FFFFFF,&H000000FF,&H00101010,"
            f"&H80000000,0,0,0,0,100,100,0,0,1,{max(2, font_size // 14)},2,2,"
            f"80,80,{margin_v},1"
        )
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding",
            style,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text",
        ]
        for index, (start, title) in enumerate(entries):
            next_start = entries[index + 1][0] if index + 1 < len(entries) else total
            end = min(total, start + 8.0, next_start)
            if end - start < 0.8:
                continue
            text = f"{index + 1:02d} · {self._ass_text(title)}"
            lines.append(
                f"Dialogue: 0,{self._ass_time(start)},{self._ass_time(end)},"
                f"Chapter,,0,0,0,,{{\\fad(350,450)}}{text}"
            )
        (temp_dir / "chapters.ass").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._copy_font(temp_dir, language)
        return True

    @staticmethod
    def _copy_font(temp_dir: Path, language: str = DEFAULT_LANGUAGE) -> None:
        if os.name != "nt":
            return
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        names: List[str] = list(font_candidates(language))
        names += ["arial.ttf", "arialbd.ttf", "segoeui.ttf", "tahoma.ttf"]
        seen: set[str] = set()
        for name in names:
            if name.casefold() in seen:
                continue
            seen.add(name.casefold())
            source = fonts_dir / name
            if source.is_file():
                try:
                    shutil.copy2(source, temp_dir / name)
                except OSError:
                    pass

    # ── subtitles ─────────────────────────────────────────────────────────────
    @staticmethod
    def _item_subtitle_text(item: ProjectItem) -> str:
        """Subtitle text for one item: metadata chapter texts, else the narration."""
        chapters = (item.metadata or {}).get("chapters") or []
        if isinstance(chapters, (list, tuple)):
            parts = []
            for chapter in chapters:
                if not isinstance(chapter, dict):
                    continue
                value = " ".join(str(chapter.get("text") or chapter.get("title")
                                     or chapter.get("name") or "").split())
                if value:
                    parts.append(value)
            if parts:
                return "\n".join(parts)
        return " ".join(str(item.text or "").split())

    @staticmethod
    def _split_subtitle_text(text: str, start: float, end: float,
                             ) -> List[Tuple[float, float, str]]:
        """Split narration into timed subtitle events filling ``[start, end]``."""
        text = " ".join(str(text or "").split())
        if not text or end - start < 0.4:
            return []
        sentences = [part.strip() for part in
                     re.split(r"(?<=[.!?…;])\s+|(?<=[。！？；])\s*|\n+", text) if part.strip()]
        if not sentences:
            sentences = [text]
        total = max(0.4, end - start)
        max_events = max(1, int(total // 1.2))
        if len(sentences) <= max_events:
            chunks = sentences
        else:
            chunks = [" ".join(sentences[
                index * len(sentences) // max_events:
                (index + 1) * len(sentences) // max_events])
                for index in range(max_events)]
        events: List[Tuple[float, float, str]] = []
        for index, chunk in enumerate(chunks):
            event_start = start + total * index / len(chunks)
            event_end = min(end, start + total * (index + 1) / len(chunks))
            events.append((event_start, event_end, chunk))
        return events

    @staticmethod
    def _subtitle_events(segments: List[Tuple[ProjectItem, float, float]],
                         total: float) -> List[Tuple[float, float, str]]:
        """Timed subtitle events for the whole render (Vietnamese narration)."""
        events: List[Tuple[float, float, str]] = []
        for item, start, duration in segments:
            events.extend(FFmpegRenderer._split_subtitle_text(
                FFmpegRenderer._item_subtitle_text(item),
                start, min(total, start + duration)))
        return events

    def _write_subtitles(self, segments: List[Tuple[ProjectItem, float, float]],
                         total: float, temp_dir: Path, cfg: VideoConfig,
                         language: str = DEFAULT_LANGUAGE) -> bool:
        """Write ``subs.ass`` with the narration subtitles; False when empty."""
        return self._write_ass(
            self._subtitle_events(segments, total), total, temp_dir, cfg, language)

    def _write_ass(self, events: List[Tuple[float, float, str]], total: float,
                   temp_dir: Path, cfg: VideoConfig,
                   language: str = DEFAULT_LANGUAGE) -> bool:
        """Write timed events as ``subs.ass``; False when there is nothing to show."""
        width, height = cfg.dimensions
        frame = cfg.frame if cfg.frame.enabled else None
        pos_tag = ""
        if frame is not None:
            px, py = frame.subtitle_pos(width, height)
            pos_tag = f"\\pos({px},{py})"
        dialogues: List[str] = []
        for start, end, text in events:
            if total > 0 and start >= total:
                continue
            end = min(end, total) if total > 0 else end
            if end - start < 0.3:
                continue
            dialogues.append(
                f"Dialogue: 0,{self._ass_time(start)},{self._ass_time(end)},"
                f"Sub,,0,0,0,,{{\\fad(120,180){pos_tag}}}{self._ass_text(text)}"
            )
        if not dialogues:
            return False
        scale = frame.subtitle_scale if frame is not None else 1.0
        font_size = max(24, int(round(44 * scale * height / 1080)))
        margin_v = 0 if frame is not None else max(40, int(round(70 * height / 1080)))
        alignment = 5 if frame is not None else 2
        style = (
            f"Style: Sub,{subtitle_font(language)},{font_size},"
            f"&H00FFFFFF,&H000000FF,&H00000000,"
            f"&H96000000,0,0,0,0,100,100,0,0,1,{max(2, font_size // 14)},1,"
            f"{alignment},20,20,{margin_v},1"
        )
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding",
            style,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text",
            *dialogues,
        ]
        (temp_dir / "subs.ass").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._copy_font(temp_dir, language)
        return True

    @staticmethod
    def _segments_language(segments: List[Tuple[ProjectItem, float, float]]
                           ) -> str:
        """Most common language among the rendered items (default ``vi``)."""
        counts: Dict[str, int] = {}
        for item, _start, _duration in segments or []:
            code = normalize_language(getattr(item, "language", "") or "", "")
            if code:
                counts[code] = counts.get(code, 0) + 1
        if not counts:
            return DEFAULT_LANGUAGE
        return max(counts, key=lambda key: counts[key])

    @staticmethod
    def _srt_timestamp(seconds: float) -> str:
        millis = int(round(max(0.0, seconds) * 1000))
        hours, rem = divmod(millis, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        secs, ms = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def _write_srt(self, segments: List[Tuple[ProjectItem, float, float]],
                   total: float, path: Path) -> bool:
        """Write an ``.srt`` file next to the video; False when there is no text."""
        events = [event for event in self._subtitle_events(segments, total)
                  if event[1] - event[0] >= 0.3]
        if not events:
            return False
        blocks: List[str] = []
        for index, (start, end, text) in enumerate(events, start=1):
            blocks.append(
                f"{index}\n{self._srt_timestamp(start)} --> "
                f"{self._srt_timestamp(end)}\n{text}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        return True

    # ── existing SRT subtitles ────────────────────────────────────────────────
    @staticmethod
    def _item_srt_candidates(manager: ProjectManager, item: ProjectItem) -> List[Path]:
        """Likely ``.srt`` locations for one item (audio sidecar first)."""
        audio = item.audio_path
        candidates: List[Path] = [audio.with_suffix(".srt"), audio.with_suffix(".vtt")]
        if item.text_file is not None:
            candidates += [item.text_file.with_suffix(".srt"),
                           item.text_file.with_suffix(".vtt")]
        for base in (manager.script_dir, manager.audio_dir, manager.project_dir):
            candidates += [Path(base) / f"{item.base}.srt", Path(base) / f"{item.base}.vtt"]
        seen: set[str] = set()
        unique: List[Path] = []
        for path in candidates:
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _srt_sources(self, manager: ProjectManager, items: List[ProjectItem],
                     cfg: VideoConfig) -> List[Tuple[Optional[ProjectItem], Path]]:
        """Chosen SRT file, or the first sidecar found for each item."""
        chosen = (cfg.subtitle_file or "").strip()
        if chosen:
            path = _resolve_user_path(chosen, manager)
            if path is None or not path.is_file():
                raise RenderError(f"Không tìm thấy file phụ đề: {chosen}")
            return [(None, path)]
        sources: List[Tuple[Optional[ProjectItem], Path]] = []
        for item in items:
            found = None
            for candidate in self._item_srt_candidates(manager, item):
                if candidate.is_file():
                    found = candidate
                    break
            if found is None:
                # Tự động xuất SRT từ cues hoặc kịch bản nếu chưa có
                srt_target = (
                    item.audio_path.with_suffix(".srt")
                    if item.audio_path
                    else Path(manager.audio_dir) / f"{item.base}.srt"
                )
                from core.dubbing import load_cues
                from core.subtitles import Cue, text_to_cues, write_srt
                cues = load_cues(manager, item.base)
                if cues:
                    srt_cues = [Cue(c.id, c.start, c.end, c.text) for c in cues if str(c.text or "").strip()]
                    if srt_cues:
                        write_srt(srt_cues, srt_target)
                        logger.info("FFmpeg: Tự động xuất SRT từ cues cho %s: %s", item.base, srt_target.name)
                        found = srt_target
                elif getattr(item, "text", "").strip():
                    dur = item.duration or (probe_duration(item.audio_path) if manager.audio_exists(item) else 0.0)
                    srt_cues = text_to_cues(item.text, total_seconds=float(dur))
                    if srt_cues:
                        write_srt(srt_cues, srt_target)
                        logger.info("FFmpeg: Tự động tạo SRT từ kịch bản cho %s: %s", item.base, srt_target.name)
                        found = srt_target
            if found is not None and found.is_file():
                sources.append((item, found))
        return sources

    def _srt_events(self, manager: ProjectManager, items: List[ProjectItem],
                    cfg: VideoConfig,
                    segments: List[Tuple[ProjectItem, float, float]],
                    ) -> List[Tuple[float, float, str]]:
        """Timed events from SRT sidecars, shifted to each item's audio start."""
        sources = self._srt_sources(manager, items, cfg)
        if not sources:
            return []
        starts = {item.base: start for item, start, _duration in segments}
        events: List[Tuple[float, float, str]] = []
        for item, path in sources:
            try:
                raw = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError as exc:
                raise RenderError(f"Không đọc được phụ đề {path.name}: {exc}") from exc
            cues = parse_vtt(raw) if path.suffix.lower() in (".vtt", ".webvtt") \
                else parse_srt(raw)
            offset = starts.get(item.base, 0.0) if item is not None else 0.0
            for cue in cues:
                text = " ".join(str(cue.text or "").split())
                if text and cue.end > cue.start:
                    events.append((cue.start + offset, cue.end + offset, text))
            logger.info("Dùng phụ đề %s (%d dòng, lệch %.2fs)",
                        path.name, len(cues), offset)
        events.sort(key=lambda event: event[0])
        return events

    def _frame_images(self, manager: ProjectManager,
                      cfg: VideoConfig) -> List[Tuple[FrameImage, Path]]:
        """Resolve the frame template's overlay images; raises when missing."""
        layout = cfg.frame
        if not layout.enabled:
            return []
        resolved: List[Tuple[FrameImage, Path]] = []
        for image in layout.images:
            path = _resolve_user_path(image.path, manager)
            if path is None or not path.is_file():
                raise RenderError(
                    f"Không tìm thấy ảnh lớp phủ: {image.path or '(chưa chọn ảnh)'}")
            resolved.append((image, path))
        return resolved

    # ── filter graph ─────────────────────────────────────────────────────────
    @staticmethod
    def _duck_expression(segments: List[Tuple[ProjectItem, float, float]],
                         duck: float) -> str:
        """Volume expression lowering BGM to ``duck`` during narration."""
        try:
            level = float(duck)
        except (TypeError, ValueError):
            return ""
        if level >= 1.0 or not segments:
            return ""
        level = max(0.0, min(0.99, level))
        ranges = []
        for _item, start, duration in segments:
            if duration and duration > 0:
                ranges.append(f"between(t,{start:.3f},{start + duration:.3f})")
        if not ranges:
            return ""
        return f"if({'+'.join(ranges)},{level:.4f},1)"

    def _build_filter(self, cfg: VideoConfig, visual: Dict[str, Any],
                      entries: List[Tuple[float, str]], total: float,
                      temp_dir: Path, has_bgm: bool,
                      segments: Optional[List[Tuple[ProjectItem, float, float]]] = None,
                      srt_events: Optional[List[Tuple[float, float, str]]] = None,
                      frame_images: Optional[List[Tuple[int, FrameImage]]] = None,
                      bgm_index: int = 2,
                      ) -> Tuple[str, str, str]:
        width, height = cfg.dimensions
        parts: List[str] = []
        visual_kind = visual["kind"]

        if visual_kind == "image" and cfg.ken_burns:
            big_w, big_h = width * 2, height * 2
            vchain = (
                f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
                f"crop={big_w}:{big_h},"
                f"zoompan=z='min(zoom+0.00005,1.15)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d=1:s={width}x{height}:fps={cfg.fps},"
                f"setsar=1,format=yuv420p"
            )
        else:
            vchain = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={cfg.fps},format=yuv420p"
            )
            if visual_kind == "video" and not visual.get("loop", True):
                # AI visuals are built to the exact audio length; hold the last
                # frame instead of restarting the sequence if we run short.
                vchain += ",tpad=stop_mode=clone:stop_duration=60"
        parts.append(f"[1:v]{vchain}[vbase]")
        vlabel = "vbase"
        language = self._segments_language(segments or [])

        if cfg.chapter_titles and entries and self._write_chapters(
                entries, total, temp_dir, cfg, language):
            parts.append(f"[{vlabel}]subtitles=chapters.ass:fontsdir=.[vsub]")
            vlabel = "vsub"

        if cfg.subtitle_srt and srt_events:
            if self._write_ass(srt_events, total, temp_dir, cfg, language):
                parts.append(f"[{vlabel}]subtitles=subs.ass:fontsdir=.[vsub2]")
                vlabel = "vsub2"
            else:
                logger.warning("Bật phụ đề .srt nhưng không có dòng nào hiển thị.")
        elif cfg.subtitle_enabled and segments:
            if self._write_subtitles(segments, total, temp_dir, cfg, language):
                parts.append(f"[{vlabel}]subtitles=subs.ass:fontsdir=.[vsub2]")
                vlabel = "vsub2"
            else:
                logger.warning("Bật phụ đề nhưng không có nội dung để hiển thị.")

        frame = cfg.frame if cfg.frame.enabled else None
        wave_active = bool(cfg.showwaves or (frame is not None and frame.waveform))
        if wave_active:
            if frame is not None:
                wave_x, wave_y, wave_w, wave_h = frame.wave_rect(width, height)
                wave_color = color_ffmpeg(frame.wave_color)
            else:
                wave_w = width
                wave_h = max(90, min(height // 4, int(round(180 * height / 1080))))
                wave_x, wave_y = 0, height - wave_h
                wave_color = "0x4FC3F7"
            audio_head = "[0:a]asplit=2[a_main][a_wave]"
            parts.append(audio_head)
            parts.append(
                f"[a_wave]showwaves=s={_even(wave_w)}x{_even(wave_h)}:mode=cline:"
                f"rate={cfg.fps}:colors={wave_color}:scale=sqrt:draw=full[waves]"
            )
            parts.append(f"[{vlabel}][waves]overlay={wave_x}:{wave_y}:format=auto[vwave]")
            vlabel = "vwave"
        else:
            audio_head = "[0:a]anull[a_main]"
            parts.append(audio_head)

        for index, (input_index, image) in enumerate(frame_images or []):
            if not getattr(image, "visible", True):
                continue
            image_w = _even(max(2, int(round(image.width * width))))
            chain = f"[{input_index}:v]scale={image_w}:-1,format=rgba"
            if getattr(image, "flip_h", False):
                chain += ",hflip"
            if getattr(image, "flip_v", False):
                chain += ",vflip"
            rot = float(getattr(image, "rotation", 0.0) or 0.0)
            if rot:
                angle_rad = -rot * math.pi / 180.0
                chain += f",rotate={angle_rad:.5f}:ow=rotw({angle_rad:.5f}):oh=roth({angle_rad:.5f}):c=none"
            if image.opacity < 0.999:
                chain += f",colorchannelmixer=aa={image.opacity:.3f}"
            parts.append(chain + f"[frameimg{index}]")
            image_x = max(0, min(width - 1, int(round(image.x * width))))
            image_y = max(0, min(height - 1, int(round(image.y * height))))
            if rot:
                parts.append(
                    f"[{vlabel}][frameimg{index}]overlay='{image_x}+({image_w}-w)/2':'{image_y}+({image_w}-h)/2':"
                    f"format=auto[vframe{index}]"
                )
            else:
                parts.append(
                    f"[{vlabel}][frameimg{index}]overlay={image_x}:{image_y}:"
                    f"format=auto[vframe{index}]"
                )
            vlabel = f"vframe{index}"

        parts.append(f"[{vlabel}]null[vout]")

        parts.append("[a_main]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[voice]")
        if has_bgm:
            db = cfg.bgm_volume_db
            duck = self._duck_expression(segments or [], cfg.bgm_duck)
            if duck:
                parts.append(
                    f"[{bgm_index}:a]volume={db}dB,volume='{duck}':eval=frame,aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[bgm]"
                )
            else:
                parts.append(
                    f"[{bgm_index}:a]volume={db}dB,aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[bgm]"
                )
            parts.append(
                "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:"
                "normalize=0[amixed]"
            )
            parts.append("[amixed]aformat=sample_fmts=fltp:channel_layouts=stereo[aout]")
        else:
            parts.append("[voice]anull[aout]")
        return ";".join(parts), vlabel, "aout"

    @staticmethod
    def _encoder_args(encoder: str, cfg: VideoConfig) -> List[str]:
        if encoder == "h264_nvenc":
            return [
                "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                "-rc", "vbr", "-cq", str(cfg.crf), "-b:v", "0",
                "-pix_fmt", "yuv420p",
            ]
        if encoder == "h264_amf":
            return [
                "-c:v", "h264_amf", "-quality", "balanced",
                "-rc", "cqp", "-qp_i", str(cfg.crf), "-qp_p", str(cfg.crf),
            ]
        if encoder == "h264_qsv":
            return [
                "-c:v", "h264_qsv", "-preset", "medium",
                "-global_quality", str(cfg.crf),
            ]
        return [
            "-c:v", "libx264", "-preset", "medium", "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p",
        ]

    # ── encoding ─────────────────────────────────────────────────────────────
    def _encode_with_fallback(self, cfg: VideoConfig, temp_dir: Path, combined: Path,
                              visual: Dict[str, Any], entries: List[Tuple[float, str]],
                              total: float, out_path: Path, job: VideoJob,
                              report: Callable[[int, str], None],
                              pause_cb: PauseCb, cancel_cb: CancelCb,
                              manager: ProjectManager,
                              segments: Optional[List[Tuple[ProjectItem, float, float]]] = None,
                              srt_events: Optional[List[Tuple[float, float, str]]] = None,
                              frame_images: Optional[List[Tuple[FrameImage, Path]]] = None,
                              ) -> str:
        bgm = self._bgm_path(cfg, manager)
        preferred = self.preferred_encoder(cfg)
        candidates = [preferred]
        if preferred != "libx264":
            candidates.append("libx264")
        last_error: Optional[Exception] = None
        for index, encoder in enumerate(candidates):
            self._check_cancel(cancel_cb)
            if out_path.exists():
                try:
                    out_path.unlink()
                except OSError:
                    pass
            if index > 0:
                report(25, f"Thử lại bằng {encoder}...")
                logger.warning("Encode %s bằng %s thất bại (%s), thử lại bằng %s",
                               job.id, candidates[index - 1], last_error, encoder)
            try:
                self._run_encode(cfg, temp_dir, combined, visual, entries, total,
                                 out_path, encoder, bgm, report, pause_cb, cancel_cb,
                                 segments=segments, srt_events=srt_events,
                                 frame_images=frame_images)
                if encoder == "libx264" or encoder == preferred:
                    self._resolved_encoder = encoder
                return encoder
            except RenderCancelled:
                raise
            except RenderError as exc:
                last_error = exc
                if index + 1 >= len(candidates):
                    break
        raise last_error or RenderError("Không encode được video.")

    def _bgm_path(self, cfg: VideoConfig,
                  manager: Optional[ProjectManager] = None) -> Optional[Path]:
        if not cfg.bgm:
            return None
        path = _resolve_user_path(cfg.bgm, manager)
        if path is None:
            return None
        if path.is_dir():
            return _first_audio(path)
        return path if path.is_file() else None

    def _run_encode(self, cfg: VideoConfig, temp_dir: Path, combined: Path,
                    visual: Dict[str, Any], entries: List[Tuple[float, str]],
                    total: float, out_path: Path, encoder: str,
                    bgm: Optional[Path], report: Callable[[int, str], None],
                    pause_cb: PauseCb, cancel_cb: CancelCb,
                    segments: Optional[List[Tuple[ProjectItem, float, float]]] = None,
                    srt_events: Optional[List[Tuple[float, float, str]]] = None,
                    frame_images: Optional[List[Tuple[FrameImage, Path]]] = None,
                    ) -> None:
        width, height = cfg.dimensions
        overlays = list(frame_images or [])
        frame_inputs = [(2 + index, image) for index, (image, _path) in enumerate(overlays)]
        bgm_index = 2 + len(overlays)
        filter_graph, _, audio_label = self._build_filter(
            cfg, visual, entries, total, temp_dir, bgm is not None,
            segments=segments, srt_events=srt_events,
            frame_images=frame_inputs, bgm_index=bgm_index)
        cmd: List[str] = [
            self.ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-i", str(combined),
        ]
        if visual["kind"] == "image":
            cmd += ["-loop", "1", "-framerate", str(cfg.fps), "-i", str(visual["path"])]
        elif visual["kind"] == "slideshow":
            cmd += ["-stream_loop", "-1", "-f", "concat", "-safe", "0",
                    "-i", os.path.basename(str(visual["path"]))]
        else:
            source = str(visual["path"])
            if not Path(source).is_absolute():
                source = os.path.basename(source)
            if visual.get("loop", True):
                cmd += ["-stream_loop", "-1", "-i", source]
            else:
                cmd += ["-i", source]
        for _image, path in overlays:
            cmd += ["-loop", "1", "-framerate", str(cfg.fps), "-i", str(path)]
        if bgm is not None:
            cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        cmd += [
            "-filter_complex", filter_graph,
            "-map", "[vout]", "-map", f"[{audio_label}]",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            *self._encoder_args(encoder, cfg),
            "-shortest",
            "-t", f"{max(0.02, float(total)):.3f}",
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats",
            os.path.basename(str(out_path)),
        ]
        report(25, f"Đang encode bằng {encoder}...")
        self._run_process(cmd, temp_dir, total, report, pause_cb, cancel_cb, 25, 74)

    # ── progress-aware process runner ────────────────────────────────────────
    def _run_process(self, cmd: List[str], cwd: Path, total: float,
                     report: Callable[[int, str], None],
                     pause_cb: PauseCb, cancel_cb: CancelCb,
                     pct_from: int, pct_span: int) -> None:
        logger.debug("ffmpeg: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise RenderError(f"Không tìm thấy ffmpeg ({cmd[0]}).") from exc

        stdout_lines: "queue.Queue[str]" = queue.Queue()
        stderr_lines: List[str] = []
        stderr_lock = threading.Lock()

        def pump_stdout() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                stdout_lines.put(line.rstrip("\r\n"))
            try:
                proc.stdout.close()
            except Exception:
                pass

        def pump_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                with stderr_lock:
                    stderr_lines.append(line.rstrip("\r\n"))
                    if len(stderr_lines) > 400:
                        del stderr_lines[:200]
            try:
                proc.stderr.close()
            except Exception:
                pass

        threads = [
            threading.Thread(target=pump_stdout, daemon=True),
            threading.Thread(target=pump_stderr, daemon=True),
        ]
        for thread in threads:
            thread.start()

        paused = False
        last_pct = pct_from
        try:
            while True:
                if cancel_cb is not None and cancel_cb():
                    _terminate(proc)
                    raise RenderCancelled("Đã huỷ tạo video.")
                want_pause = bool(pause_cb and pause_cb())
                if want_pause and not paused:
                    paused = True
                    if _suspend_resume(proc.pid, True):
                        report(last_pct, "Tạm dừng...")
                        logger.info("Tạm dừng ffmpeg pid=%s", proc.pid)
                    else:
                        logger.warning("Không tạm dừng được ffmpeg (pid=%s)", proc.pid)
                elif not want_pause and paused:
                    paused = False
                    _suspend_resume(proc.pid, False)
                    report(last_pct, "Tiếp tục...")
                    logger.info("Tiếp tục ffmpeg pid=%s", proc.pid)

                while True:
                    try:
                        line = stdout_lines.get_nowait()
                    except queue.Empty:
                        break
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    seconds = 0.0
                    if key == "out_time_us":
                        try:
                            seconds = float(value) / 1_000_000.0
                        except ValueError:
                            seconds = 0.0
                    elif key == "out_time_ms":
                        try:
                            seconds = float(value) / 1_000_000.0
                        except ValueError:
                            seconds = 0.0
                    elif key == "out_time":
                        seconds = _parse_ffmpeg_time(value)
                    elif key == "progress" and value == "end":
                        seconds = total
                    if seconds > 0 and total > 0:
                        ratio = max(0.0, min(1.0, seconds / total))
                        pct = pct_from + int(pct_span * ratio)
                        if pct > last_pct:
                            last_pct = pct
                            report(pct, f"Đang dựng video... {ratio * 100:.0f}%")

                if proc.poll() is not None:
                    break
                time.sleep(0.08)
        finally:
            for thread in threads:
                thread.join(timeout=2)
            if proc.poll() is None:
                _terminate(proc)

        if paused:
            logger.warning("ffmpeg kết thúc trong lúc tạm dừng (pid=%s)", proc.pid)
        if proc.returncode != 0:
            with stderr_lock:
                tail = _tail(stderr_lines)
            raise RenderError(
                f"ffmpeg lỗi (mã {proc.returncode})"
                + (f": {tail}" if tail else ".")
            )

    @staticmethod
    def _check_cancel(cancel_cb: CancelCb) -> None:
        if cancel_cb is not None and cancel_cb():
            raise RenderCancelled("Đã huỷ tạo video.")
