"""Background pipeline: TTS -> FFmpeg video -> YouTube upload.

``PipelineWorker`` is a ``QThread`` so the GUI never blocks. Pause/resume/stop
are thread-safe; every stage delegates to the real engines and forwards their
progress to Qt signals (workers never touch widgets directly).

Retry works by simply running the same stage again: TTS targets items without
valid audio, the renderer rebuilds missing/stale videos and the uploader skips
videos that already have a YouTube id, so failed items are retried while
finished work is preserved.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from core.dubbing import DubbingEngine, DubbingError, load_cues
from core.edge_tts_engine import EdgeTTSEngine
from core.ffmpeg_renderer import (
    FFmpegRenderer,
    RenderCancelled,
    RenderError,
    VideoConfig,
)
from core.languages import normalize_language
from core.project_manager import (
    ITEM_ERROR,
    ITEM_PENDING,
    ITEM_TTS_DONE,
    ITEM_TTS_PROCESSING,
    ProjectManager,
    VIDEO_DONE,
    VIDEO_UPLOAD_DONE,
)
from core.tts_engine import SynthesisCancelled, TTSEngine, TTSError
from core.youtube_uploader import (
    UploadCancelled,
    YouTubeConfig,
    YouTubeError,
    YouTubeUploader,
)
from utils.logger import get_logger

logger = get_logger("pipeline")

SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

_MODES = ("tts", "video", "upload", "all")
_STAGE_LABELS = {
    "tts": "TẠO ÂM THANH (TTS)",
    "video": "TẠO VIDEO (FFMPEG)",
    "upload": "UPLOAD YOUTUBE",
}
_STAGE_WEIGHTS = {"tts": 40, "video": 35, "upload": 25}


class PipelineCancelled(RuntimeError):
    """The user stopped the pipeline."""


class PipelineError(RuntimeError):
    """Configuration/setup problem that prevents a stage from starting."""


class PipelineWorker(QThread):
    """Runs one stage or the whole pipeline in a background thread."""

    overall_changed = Signal(int)
    task_changed = Signal(int)
    stage_changed = Signal(str)
    status_changed = Signal(str, bool)   # message, running
    items_updated = Signal()
    videos_updated = Signal()
    job_uploaded = Signal(str, str, str)  # job id, title, url
    notes_changed = Signal(list)          # snapshot of the notes accumulator
    completed = Signal(bool, str)         # success, message

    def __init__(
        self,
        settings: Dict[str, Any],
        manager: ProjectManager,
        mode: str = "all",
        force: bool = False,
        only: Optional[Iterable[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        mode = str(mode or "all").lower()
        if mode not in _MODES:
            raise ValueError(f"Chế độ pipeline không hợp lệ: {mode}")
        self.settings = settings
        self.manager = manager
        self.mode = mode
        self.force = bool(force)
        self.only = set(only) if only is not None else None
        self._paused = threading.Event()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pause_logged = False
        self._started = 0.0
        self._stop_on_error = bool((settings.get("app") or {}).get("stop_on_error", False))
        self.notes: List[str] = []

    # ── controls (called from the GUI thread) ────────────────────────────────
    def pause(self) -> None:
        if self.isRunning() and not self.is_cancelled():
            self._paused.set()
            self._wake.clear()

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()
        self._wake.set()
        self.requestInterruption()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def is_cancelled(self) -> bool:
        return self._stop.is_set() or self.isInterruptionRequested()

    # ── thread body ──────────────────────────────────────────────────────────
    def run(self) -> None:  # noqa: C901 - stage orchestration is intentionally linear
        self._started = time.time()
        errors: List[str] = []
        self.notes = []
        self.notes_changed.emit(list(self.notes))
        logger.info("Bắt đầu pipeline: %s", "TOÀN BỘ" if self.mode == "all" else _STAGE_LABELS[self.mode])
        self.overall_changed.emit(0)
        try:
            self.manager.scan()
            self.items_updated.emit()
            self.videos_updated.emit()
            plan = self._stage_plan()
            for name, start, end in plan:
                self._checkpoint()
                self.stage_changed.emit(_STAGE_LABELS[name])
                self.status_changed.emit(_STAGE_LABELS[name], True)
                self.task_changed.emit(0)
                logger.info("=== %s ===", _STAGE_LABELS[name])
                try:
                    stage_errors = self._run_stage(name, start, end)
                    attempts = 0
                    retries = max(0, int((self.settings.get("app") or {}).get("retry", 1) or 0))
                    while stage_errors and attempts < retries and not self._stop_on_error:
                        attempts += 1
                        self._checkpoint()
                        logger.warning(
                            "%s: thử lại %d/%d cho %d mục lỗi (bỏ qua phần đã xong).",
                            _STAGE_LABELS[name], attempts, retries, len(stage_errors),
                        )
                        self._add_note(
                            f"Thử lại {name.upper()} ({attempts}/{retries}) "
                            f"cho {len(stage_errors)} mục lỗi."
                        )
                        self.status_changed.emit(
                            f"Thử lại {_STAGE_LABELS[name].lower()} ({attempts}/{retries})…",
                            True,
                        )
                        stage_errors = self._run_stage(name, start, end)
                except (SynthesisCancelled, RenderCancelled, UploadCancelled) as exc:
                    raise PipelineCancelled(str(exc)) from exc
                errors.extend(stage_errors)
                self.task_changed.emit(100 if not stage_errors else 0)
                self.overall_changed.emit(int(round(end)))
                if stage_errors and self._stop_on_error:
                    logger.warning("Dừng pipeline vì có lỗi (stop_on_error=True).")
                    break
            self._finish(errors)
        except PipelineCancelled as exc:
            logger.warning("Pipeline đã dừng: %s", exc or "theo yêu cầu")
            self.status_changed.emit("Đã dừng pipeline.", False)
            self.completed.emit(False, "Đã dừng pipeline theo yêu cầu.")
        except PipelineError as exc:
            logger.error("%s", exc)
            self.status_changed.emit(str(exc), False)
            self.completed.emit(False, str(exc))
        except (TTSError, RenderError, YouTubeError, DubbingError) as exc:
            logger.error("Pipeline dừng do lỗi: %s", exc)
            self.status_changed.emit(str(exc), False)
            self.completed.emit(False, str(exc))
        except Exception as exc:
            logger.exception("Pipeline lỗi không mong đợi")
            self.status_changed.emit(f"Lỗi: {exc}", False)
            self.completed.emit(False, f"Lỗi không mong đợi: {exc}")

    def _finish(self, errors: List[str]) -> None:
        elapsed = max(0.0, time.time() - self._started)
        minutes, seconds = divmod(int(elapsed), 60)
        took = f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"
        summary = self._summary()
        notes_block = self._notes_block()
        if errors:
            message = f"Hoàn tất có {len(errors)} lỗi sau {took}. {summary}"
            logger.warning(message)
            details = "\n".join(f"• {err}" for err in errors[:12])
            if len(errors) > 12:
                details += f"\n• … và {len(errors) - 12} lỗi khác (xem log)."
            details += notes_block
            self.status_changed.emit(message, False)
            self.task_changed.emit(100)
            self.completed.emit(False, f"{message}\n{details}")
            return
        message = f"Hoàn tất sau {took}. {summary}"
        logger.log(SUCCESS_LEVEL, message)
        self.status_changed.emit(message, False)
        self.task_changed.emit(100)
        self.completed.emit(True, message + notes_block)

    def _add_note(self, text: str) -> None:
        """Append a human-readable note and notify listeners (worker thread only)."""
        self.notes.append(text)
        logger.info("Ghi chú: %s", text)
        self.notes_changed.emit(list(self.notes))

    def _notes_block(self) -> str:
        """Build the bulleted notes section shown in the completion summary."""
        if not self.notes:
            return ""
        lines = "\n".join(f"• {note}" for note in self.notes)
        return f"\n\nGhi chú:\n{lines}"

    def _summary(self) -> str:
        items = self.manager.get_items()
        videos = list(self.manager.videos.values())
        parts: List[str] = []
        if self.mode in ("tts", "all"):
            done = sum(1 for item in items if self.manager.audio_exists(item))
            parts.append(f"{done}/{len(items)} audio")
        if self.mode in ("video", "all"):
            done = sum(
                1 for job in videos
                if job.path.is_file() and job.state in (VIDEO_DONE, VIDEO_UPLOAD_DONE)
            )
            parts.append(f"{done}/{len(videos)} video")
        if self.mode in ("upload", "all"):
            done = sum(1 for job in videos if job.youtube_video_id)
            parts.append(f"{done}/{len(videos)} đã đăng")
        return " · ".join(parts)

    # ── stage planning ───────────────────────────────────────────────────────
    def _stage_plan(self) -> List[Tuple[str, float, float]]:
        names = ["tts", "video", "upload"] if self.mode == "all" else [self.mode]
        total = sum(_STAGE_WEIGHTS[name] for name in names) or 1
        plan: List[Tuple[str, float, float]] = []
        offset = 0.0
        for name in names:
            span = 100.0 * _STAGE_WEIGHTS[name] / total
            plan.append((name, offset, offset + span))
            offset += span
        return plan

    def _run_stage(self, name: str, start: float, end: float) -> List[str]:
        if name == "tts":
            return self._stage_tts(start, end)
        if name == "video":
            return self._stage_video(start, end)
        return self._stage_upload(start, end)

    # ── TTS ──────────────────────────────────────────────────────────────────
    def _tts_targets(self):
        targets = []
        for item in self.manager.get_items():
            if not item.text.strip():
                continue
            if (
                self.force
                or not self.manager.audio_exists(item)
                or item.state in (ITEM_PENDING, ITEM_TTS_PROCESSING)
            ):
                targets.append(item)
                continue
            if getattr(item, "dub_mode", "text") == "srt":
                cues = load_cues(self.manager, item.base)
                if any(cue.status != "DONE" for cue in cues):
                    targets.append(item)
        return targets

    def _stage_tts(self, start: float, end: float) -> List[str]:
        targets = self._tts_targets()
        if not targets:
            logger.info("TTS: mọi file đã có audio, không cần tạo lại.")
            self.status_changed.emit("TTS: không có file cần tạo.", self.isRunning())
            skipped = sum(1 for item in self.manager.get_items() if item.text.strip())
            if skipped:
                self._add_note(f"TTS: bỏ qua {skipped} file đã có audio.")
            return []

        def language_of(item) -> str:
            return normalize_language(
                getattr(item, "language", "") or self.manager.default_language)

        local_items = [item for item in targets if language_of(item) == "vi"]
        online_items = [item for item in targets if language_of(item) != "vi"]
        local_engine = TTSEngine(self.settings) if local_items else None
        online_engine = EdgeTTSEngine(self.settings) if online_items else None
        if online_items:
            logger.info("TTS: %d file dùng edge-tts (%s).", len(online_items),
                        ", ".join(sorted({language_of(item) for item in online_items})))
        errors: List[str] = []
        total = len(targets)
        skipped = sum(1 for item in self.manager.get_items() if item.text.strip()) - total
        if skipped > 0:
            self._add_note(f"TTS: bỏ qua {skipped} file đã có audio.")
        logger.info("TTS: %d/%d file cần tạo audio.", total, len(self.manager.get_items()))
        try:
            for index, item in enumerate(targets):
                self._checkpoint()
                label = item.display_name
                self.status_changed.emit(
                    f"TTS {index + 1}/{total}: {label}", self.isRunning())
                self.manager.set_item_state(item.base, ITEM_TTS_PROCESSING)
                self.items_updated.emit()

                def report(
                    percent: int,
                    _index: int = index,
                    _label: str = label,
                ) -> None:
                    percent = max(0, min(100, int(percent)))
                    self.task_changed.emit(percent)
                    self.overall_changed.emit(
                        int(round(start + (end - start) * (_index + percent / 100.0) / total))
                    )
                    self.status_changed.emit(
                        f"TTS {_index + 1}/{total}: {_label} — {percent}%", True)

                try:
                    if getattr(item, "dub_mode", "text") == "srt" and self._try_dubbing(
                        item, report, errors, label
                    ):
                        continue
                    language = language_of(item)
                    if language == "vi":
                        result = local_engine.synthesize(
                            item.text,
                            item.audio_path,
                            progress_cb=report,
                            message_cb=lambda msg: self.status_changed.emit(msg, True),
                            pause_cb=self.is_paused,
                            cancel_cb=self.is_cancelled,
                        )
                    else:
                        result = online_engine.synthesize(
                            item.text,
                            item.audio_path,
                            language=language,
                            progress_cb=report,
                            message_cb=lambda msg: self.status_changed.emit(msg, True),
                            pause_cb=self.is_paused,
                            cancel_cb=self.is_cancelled,
                        )
                    try:
                        mtime = item.audio_path.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    self.manager.update_item(
                        item.base,
                        state=ITEM_TTS_DONE,
                        error="",
                        duration=float(result.duration),
                        audio_mtime=mtime,
                    )
                    logger.log(
                        SUCCESS_LEVEL,
                        "TTS xong %s (%.1f phút, %d đoạn, %s).",
                        item.audio_path.name, result.duration / 60.0, result.chunks,
                        getattr(result, "engine", ""),
                    )
                    report(100)
                except SynthesisCancelled:
                    self.manager.set_item_state(item.base, ITEM_PENDING)
                    raise
                except TTSError as exc:
                    self.manager.set_item_state(item.base, ITEM_ERROR, str(exc))
                    errors.append(f"TTS {label}: {exc}")
                    logger.error("TTS lỗi %s: %s", label, exc)
                    if self._stop_on_error:
                        break
                except Exception as exc:  # unexpected engine failure
                    self.manager.set_item_state(item.base, ITEM_ERROR, str(exc))
                    errors.append(f"TTS {label}: {exc}")
                    logger.exception("TTS lỗi không mong đợt %s", label)
                    if self._stop_on_error:
                        break
                finally:
                    self.items_updated.emit()
        finally:
            if local_engine is not None:
                local_engine.close()
                logger.info("Đã đóng TTS engine, giải phóng bộ nhớ.")
        self.task_changed.emit(100)
        return errors

    def _try_dubbing(self, item, report, errors: List[str], label: str) -> bool:
        """Synthesize cue-by-cue and join; returns True when the item was handled."""
        cues = load_cues(self.manager, item.base)
        if not cues:
            logger.debug("Bỏ qua lồng tiếng %s: không có cue.", item.base)
            return False
        engine = DubbingEngine(self.settings)
        try:
            engine.synthesize_item(
                self.manager,
                item,
                cues,
                progress_cb=report,
                message_cb=lambda msg: self.status_changed.emit(msg, True),
                pause_cb=self.is_paused,
                cancel_cb=self.is_cancelled,
                cue_cb=lambda _cue: self.items_updated.emit(),
            )
            dubbing = self.settings.get("dubbing") or {}
            mode = str(dubbing.get("timing_mode") or "timeline")
            gap = float(dubbing.get("gap", 0.15) or 0.0)
            mp3, _srt, duration = engine.join_item(
                self.manager, item, cues, mode=mode, gap=gap)
        except SynthesisCancelled:
            self.manager.set_item_state(item.base, ITEM_PENDING)
            raise
        except DubbingError as exc:
            self.manager.set_item_state(item.base, ITEM_ERROR, str(exc))
            errors.append(f"Lồng tiếng {label}: {exc}")
            logger.error("Lồng tiếng lỗi %s: %s", label, exc)
            return True
        except Exception as exc:  # unexpected engine failure
            self.manager.set_item_state(item.base, ITEM_ERROR, str(exc))
            errors.append(f"Lồng tiếng {label}: {exc}")
            logger.exception("Lồng tiếng lỗi không mong đợi %s", label)
            return True
        try:
            mtime = mp3.stat().st_mtime
        except OSError:
            mtime = 0.0
        self.manager.update_item(
            item.base,
            state=ITEM_TTS_DONE,
            error="",
            duration=float(duration),
            audio_mtime=mtime,
            audio_path=mp3,
        )
        logger.log(
            SUCCESS_LEVEL,
            "Lồng tiếng xong %s (%.1f phút, %d cue, %s).",
            mp3.name, float(duration) / 60.0, len(cues), mode,
        )
        report(100)
        return True

    # ── video ────────────────────────────────────────────────────────────────
    def _stage_video(self, start: float, end: float) -> List[str]:
        cfg = VideoConfig.from_settings(self.settings)
        problems = cfg.errors()
        if problems:
            raise PipelineError("Cấu hình video không hợp lệ: " + "; ".join(problems))

        renderer = FFmpegRenderer(self.settings)

        def report(percent: int, message: str, task_percent: int = None) -> None:
            stage_pct = max(0, min(100, int(percent)))
            task_val = max(0, min(100, int(task_percent))) if task_percent is not None else stage_pct
            self.task_changed.emit(task_val)
            self.overall_changed.emit(int(round(start + (end - start) * stage_pct / 100.0)))
            self.status_changed.emit(message, self.isRunning())

        self.status_changed.emit("Đang lập kế hoạch video...", True)
        self.videos_updated.emit()
        done, errors = renderer.render_all(
            self.manager,
            cfg,
            force=self.force,
            only=self.only,
            progress_cb=report,
            pause_cb=self.is_paused,
            cancel_cb=self.is_cancelled,
            stop_on_error=self._stop_on_error,
        )
        self.videos_updated.emit()
        self.items_updated.emit()
        if not done and not errors:
            logger.info("Video: không có file nào cần render.")
            self.status_changed.emit("Video: không có file cần render.", self.isRunning())
            self._add_note("Video: bỏ qua — không có file cần render.")
        else:
            finished = sum(
                1 for job in self.manager.videos.values()
                if job.path.is_file() and job.state in (VIDEO_DONE, VIDEO_UPLOAD_DONE)
            )
            skipped = finished - len(done)
            if skipped > 0:
                self._add_note(f"Video: bỏ qua {skipped} video đã xong.")
        for job in done:
            logger.log(SUCCESS_LEVEL, "Video xong: %s", job.path.name)
        self.task_changed.emit(100)
        return [f"Video {err}" for err in errors]

    # ── upload ───────────────────────────────────────────────────────────────
    def _stage_upload(self, start: float, end: float) -> List[str]:
        cfg = YouTubeConfig.from_settings(self.settings)
        problems = cfg.errors()
        if problems:
            raise PipelineError("Cấu hình YouTube không hợp lệ: " + "; ".join(problems))

        video_cfg = VideoConfig.from_settings(self.settings)
        try:
            FFmpegRenderer(self.settings).build_jobs(self.manager, video_cfg, force=False)
        except RenderError as exc:
            raise PipelineError(f"Không lập được danh sách video: {exc}") from exc
        self.videos_updated.emit()

        uploader = YouTubeUploader(self.settings)

        def report(percent: int, message: str, task_percent: int = None) -> None:
            stage_pct = max(0, min(100, int(percent)))
            task_val = max(0, min(100, int(task_percent))) if task_percent is not None else stage_pct
            self.task_changed.emit(task_val)
            self.overall_changed.emit(int(round(start + (end - start) * stage_pct / 100.0)))
            self.status_changed.emit(message, self.isRunning())

        results, errors = uploader.upload_all(
            self.manager,
            cfg,
            force=False,
            only=self.only,
            progress_cb=report,
            pause_cb=self.is_paused,
            cancel_cb=self.is_cancelled,
            stop_on_error=self._stop_on_error,
        )
        for result in results:
            self.job_uploaded.emit(result.job_id, result.title, result.url)
            logger.log(SUCCESS_LEVEL, "Đã đăng: %s → %s", result.title, result.url)
            for warning in result.warnings:
                logger.warning("%s: %s", result.title, warning)
        self.videos_updated.emit()
        self.items_updated.emit()
        if not results and not errors:
            logger.info("Upload: không có video nào cần đăng.")
            self.status_changed.emit("Upload: không có video cần đăng.", self.isRunning())
            self._add_note("Upload: bỏ qua — không có video cần đăng.")
        else:
            already = sum(1 for job in self.manager.videos.values() if job.youtube_video_id)
            if already:
                self._add_note(f"Upload: bỏ qua {already} video vì đã đăng.")
        self.task_changed.emit(100)
        return [f"Upload {err}" for err in errors]

    # ── pause/cancel helpers ─────────────────────────────────────────────────
    def _checkpoint(self) -> None:
        if self.is_cancelled():
            raise PipelineCancelled("Đã dừng pipeline.")
        if not self._paused.is_set():
            return
        if not self._pause_logged:
            self._pause_logged = True
            logger.info("Pipeline tạm dừng — bấm Tiếp tục để chạy tiếp.")
        while self._paused.is_set() and not self.is_cancelled():
            self._wake.wait(0.2)
        if self.is_cancelled():
            raise PipelineCancelled("Đã dừng pipeline.")
        if self._pause_logged:
            self._pause_logged = False
            logger.info("Pipeline tiếp tục chạy.")
