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
from core.languages import normalize_language, normalize_language_code
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
from utils.media_probe import probe_duration

logger = get_logger("pipeline")

SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

_MODES = ("script", "tts", "video", "thumbnail", "upload", "all")
_STAGE_LABELS = {
    "script": "KỊCH BẢN (TRANSCRIPT & DỊCH)",
    "tts": "TẠO ÂM THANH (TTS / LỒNG TIẾNG)",
    "video": "TẠO VIDEO (FFMPEG)",
    "thumbnail": "TẠO THUMBNAIL (AI BÌA)",
    "upload": "UPLOAD YOUTUBE",
}
_STAGE_WEIGHTS = {
    "script": 20,
    "tts": 25,
    "video": 25,
    "thumbnail": 10,
    "upload": 20,
}


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
    script_updated = Signal(str, str, dict, dict)  # url, text, translations, meta
    cues_updated = Signal(str)                     # item base
    job_uploaded = Signal(str, str, str)           # job id, title, url
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
                        stage_errors = self._run_stage(name, start, end, is_retry=True)
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
            ready_videos = [job for job in videos if FFmpegRenderer.has_audio(self.manager, job)]
            target_count = len(ready_videos) if ready_videos else len(videos)
            done = sum(
                1 for job in videos
                if job.path.is_file() and job.state in (VIDEO_DONE, VIDEO_UPLOAD_DONE)
            )
            parts.append(f"{done}/{target_count} video")
        if self.mode in ("upload", "all"):
            done = sum(1 for job in videos if job.youtube_video_id)
            parts.append(f"{done}/{len(videos)} đã đăng")
        return " · ".join(parts)

    # ── stage planning ───────────────────────────────────────────────────────
    def _stage_plan(self) -> List[Tuple[str, float, float]]:
        if self.mode == "all":
            auto_cfg = self.settings.get("automation") or {}
            has_yt = bool(
                auto_cfg.get("youtube_url")
                or self.settings.get("script", {}).get("youtube_url")
            )
            names: List[str] = []
            if auto_cfg.get("script_enabled", True) and has_yt:
                names.append("script")
            if auto_cfg.get("tts_enabled", True):
                names.append("tts")
            if auto_cfg.get("video_enabled", True):
                names.append("video")
            if auto_cfg.get("thumbnail_enabled", True):
                names.append("thumbnail")
            if auto_cfg.get("upload_enabled", True):
                names.append("upload")
            if not names:
                raise PipelineError(
                    "Tất cả các bước tự động hóa đều bị tắt hoặc không có bước nào khả dụng để chạy."
                )
        else:
            names = [self.mode]
        total = sum(_STAGE_WEIGHTS[name] for name in names) or 1
        plan: List[Tuple[str, float, float]] = []
        offset = 0.0
        for name in names:
            span = 100.0 * _STAGE_WEIGHTS[name] / total
            plan.append((name, offset, offset + span))
            offset += span
        return plan

    def _run_stage(self, name: str, start: float, end: float, is_retry: bool = False) -> List[str]:
        if name == "script":
            return self._stage_script(start, end, is_retry=is_retry)
        if name == "tts":
            return self._stage_tts(start, end, is_retry=is_retry)
        if name == "video":
            return self._stage_video(start, end, is_retry=is_retry)
        if name == "thumbnail":
            return self._stage_thumbnail(start, end, is_retry=is_retry)
        return self._stage_upload(start, end)

    # ── script ───────────────────────────────────────────────────────────────
    def _stage_script(self, start: float, end: float, is_retry: bool = False) -> List[str]:
        def report(stage_pct: int, message: str, task_pct: Optional[int] = None) -> None:
            pct = max(0, min(100, int(stage_pct)))
            t_pct = max(0, min(100, int(task_pct))) if task_pct is not None else pct
            self.task_changed.emit(t_pct)
            self.overall_changed.emit(int(round(start + (end - start) * pct / 100.0)))
            self.status_changed.emit(message, self.isRunning())

        yt_url = (
            self.settings.get("automation", {}).get("youtube_url")
            or self.settings.get("script", {}).get("youtube_url")
            or ""
        ).strip()

        if not yt_url:
            existing = self.manager.get_items()
            if existing:
                logger.info("Kịch bản: Không có link YouTube mới, sử dụng %d file hiện có trong dự án.", len(existing))
                report(100, f"Dùng {len(existing)} file kịch bản hiện có.")
                return []
            raise PipelineError("Chưa có link YouTube và dự án chưa có file kịch bản nào. Hãy nhập link YouTube.")

        report(10, f"Đang lấy transcript từ YouTube: {yt_url}")
        try:
            from core.input_pipeline import get_youtube_transcript
            from core.ai_client import (
                DEFAULT_CLEAN_PROMPT,
                DEFAULT_MODEL,
                build_clean_prompt,
                clean_transcript,
                clean_transcript_heuristics,
                gemini_generate,
                translate_text,
            )
            from utils.files import safe_filename

            cues, meta = get_youtube_transcript(
                yt_url,
                progress_cb=lambda pct, msg: report(10 + int(pct * 0.2), msg),
                cancel_cb=self.is_cancelled,
            )
        except Exception as exc:
            logger.error("Lỗi lấy transcript: %s", exc)
            return [f"Kịch bản: Không lấy được transcript từ YouTube ({exc})"]

        paragraphs = []
        buffer = []
        previous_end = None
        for cue in cues:
            txt = " ".join(str(getattr(cue, "text", "") or "").split())
            if not txt:
                continue
            start = float(getattr(cue, "start", 0.0) or 0.0)
            if buffer and previous_end is not None and start - previous_end > 2.0:
                paragraphs.append(" ".join(buffer))
                buffer = []
            buffer.append(txt)
            previous_end = float(getattr(cue, "end", 0.0) or 0.0)
        if buffer:
            paragraphs.append(" ".join(buffer))
        raw_text = "\n\n".join(paragraphs).strip()
        if not raw_text:
            return ["Kịch bản: Transcript rỗng."]

        # 1. Luôn làm sạch cơ bản bằng heuristic (loại bỏ [âm nhạc], >>, tiếng ồn, định dạng lỗi)
        heuristic_cleaned = clean_transcript_heuristics(raw_text) or raw_text
        self.script_updated.emit(yt_url, heuristic_cleaned, {}, meta)

        api_key = (
            (self.settings.get("api") or {}).get("gemini_key")
            or (self.settings.get("script") or {}).get("api_key")
            or (self.settings.get("ai") or {}).get("api_key")
            or (self.settings.get("video") or {}).get("ai_api_key")
            or ""
        ).strip()
        model = str((self.settings.get("script") or {}).get("model") or DEFAULT_MODEL)
        saved_prompt = str((self.settings.get("script") or {}).get("transcript_clean_prompt") or "")
        if not saved_prompt or "Som Tam" in saved_prompt or "DNSE" in saved_prompt:
            clean_prompt = DEFAULT_CLEAN_PROMPT
        else:
            clean_prompt = saved_prompt

        cleaned_text = heuristic_cleaned
        if api_key:
            report(32, "Đang làm sạch transcript bằng AI…")
            try:
                def _prog(pct: int, msg: str) -> None:
                    report(32 + int(pct * 0.15), msg)

                cleaned_text = clean_transcript(
                    heuristic_cleaned,
                    api_key=api_key,
                    model=model,
                    instruction=clean_prompt,
                    progress_cb=_prog,
                    cancel_cb=self.is_cancelled,
                )
                self.script_updated.emit(yt_url, cleaned_text, {}, meta)
            except Exception as exc:
                logger.warning("Không thể làm sạch bằng AI (%s), dùng bản làm sạch cơ bản.", exc)
                self._add_note(f"Làm sạch transcript: AI gặp lỗi ({exc}), đã tự động lọc sạch âm thanh & tiếng ồn.")
                cleaned_text = heuristic_cleaned
                self.script_updated.emit(yt_url, cleaned_text, {}, meta)
        else:
            logger.info("Chưa có API key Gemini, sử dụng bản làm sạch cơ bản (heuristic).")
            self._add_note("Làm sạch transcript: Chưa có API key AI, đã áp dụng bộ lọc regex tự động.")

        target_langs = (
            self.settings.get("automation", {}).get("target_languages")
            or self.settings.get("script", {}).get("languages")
            or ["vi"]
        )
        if isinstance(target_langs, str):
            target_langs = [target_langs]

        topic_name = str(
            (self.settings.get("automation") or {}).get("topic_name")
            or (self.settings.get("script") or {}).get("topic_name")
            or ""
        ).strip()
        base_title = topic_name or meta.get("title") or meta.get("video_id") or "youtube_video"
        base_name = safe_filename(base_title, "video")

        from core.language_profiles import load_profiles
        from utils.files import write_json_atomic
        profiles_by_lang = {
            normalize_language_code(p.language): p.prompt
            for p in load_profiles()
            if p.language
        }
        source_lang = normalize_language_code(meta.get("source_language") or "vi")
        self.manager.ensure_language_dirs(target_langs)

        errors: List[str] = []
        translations: Dict[str, str] = {}
        total_langs = max(1, len(target_langs))
        for idx, lang in enumerate(target_langs):
            self._checkpoint()
            report(
                50 + int((idx / total_langs) * 45),
                f"Đang chuẩn bị kịch bản tiếng {lang} ({idx + 1}/{total_langs})…",
            )
            lang_code = normalize_language_code(lang)
            text_for_lang = cleaned_text
            if api_key and lang_code != source_lang:
                try:
                    text_for_lang = translate_text(
                        cleaned_text,
                        api_key=api_key,
                        model=model,
                        source_lang=source_lang,
                        target_lang=lang_code,
                        custom_prompt=profiles_by_lang.get(lang_code, ""),
                    )
                except Exception as exc:
                    logger.warning("Dịch sang %s lỗi: %s, dùng bản tiếng gốc.", lang_code, exc)
                    errors.append(f"Dịch kịch bản {lang_code}: {exc}")

            lang_script_dir = self.manager.lang_dir("script", lang_code, create=True)
            base_key = f"{base_name}_{lang_code}"
            out_file = lang_script_dir / f"{base_key}.txt"
            try:
                out_file.write_text(text_for_lang.strip() + "\n", encoding="utf-8", newline="\n")
                is_source = lang_code == source_lang
                write_json_atomic(out_file.with_suffix(".json"), {
                    "title": base_title if is_source else f"{base_title} [{lang_code.upper()}]",
                    "language": lang_code,
                    "url": yt_url,
                })
                # Xóa file cues cũ (nếu có từ lần chạy lỗi/chưa làm sạch trước đó) để buộc tạo lại sạch
                from core.dubbing import cues_path
                old_cues = cues_path(self.manager, base_key)
                if old_cues.is_file():
                    try:
                        old_cues.unlink()
                        logger.info("Đã xóa file cues cũ %s để đồng bộ kịch bản mới.", old_cues.name)
                    except OSError:
                        pass
                logger.info("Đã tạo file kịch bản: %s/%s", lang_code, out_file.name)
                translations[lang_code] = text_for_lang
                self.script_updated.emit(yt_url, cleaned_text, dict(translations), meta)
                self.cues_updated.emit(base_key)
            except Exception as exc:
                errors.append(f"Lưu file {out_file.name}: {exc}")

        self.manager.scan()
        self.items_updated.emit()
        report(100, f"Đã chuẩn bị xong {len(target_langs)} bản kịch bản.")

        # Automation: giới hạn các bước tiếp theo (TTS, Video, Upload) vào đúng kịch bản vừa tạo
        created_bases = [f"{base_name}_{normalize_language_code(lang)}" for lang in target_langs]
        if self.mode == "all" and created_bases:
            self.only = set(created_bases)
            logger.info("Automation giới hạn các bước TTS/Video/Upload vào kịch bản mới: %s", created_bases)

        return errors

    # ── TTS ──────────────────────────────────────────────────────────────────
    def _tts_targets(self, is_retry: bool = False):
        targets = []
        for item in self.manager.get_items():
            if self.only and item.base not in self.only:
                continue
            if not item.text.strip():
                continue
            srt_path = item.audio_path.with_suffix(".srt")
            srt_exists = (
                srt_path.is_file()
                or (item.text_file and item.text_file.with_suffix(".srt").is_file())
            )
            has_audio = self.manager.audio_exists(item)
            cues = load_cues(self.manager, item.base)
            cues_pending = any(cue.status != "DONE" for cue in cues) if cues else False

            if (
                (self.force and not is_retry)
                or not has_audio
                or item.state in (ITEM_PENDING, ITEM_TTS_PROCESSING)
                or (cues and cues_pending)
            ):
                targets.append(item)
        return targets

    def _stage_tts(self, start: float, end: float, is_retry: bool = False) -> List[str]:
        targets = self._tts_targets(is_retry=is_retry)
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
                    srt_path = item.audio_path.with_suffix(".srt")
                    cues = load_cues(self.manager, item.base)
                    has_audio = self.manager.audio_exists(item)
                    cues_pending = any(cue.status != "DONE" for cue in cues) if cues else False

                    # Quick recovery: nếu đã có audio và cues đã xong (hoặc không có cues), chỉ thiếu srt:
                    if has_audio and not cues_pending and not srt_path.is_file():
                        if cues:
                            try:
                                engine = DubbingEngine(self.settings)
                                dub_cfg = self.settings.get("dubbing") or {}
                                mode = str(dub_cfg.get("timing_mode") or "natural")
                                gap = float(dub_cfg.get("gap", 0.15) or 0.0)
                                engine.join_item(self.manager, item, cues, mode=mode, gap=gap)
                                logger.info("Đã ghép xuất SRT từ cues có sẵn cho %s.", item.base)
                                report(100)
                                continue
                            except Exception as exc:
                                logger.warning("Không thể ghép SRT từ cues: %s. Sẽ tạo từ kịch bản.", exc)

                        from core.subtitles import text_to_cues, write_srt
                        dur = float(item.duration or probe_duration(item.audio_path) or 1.0)
                        sub_cues = text_to_cues(item.text, total_seconds=dur)
                        if sub_cues:
                            write_srt(sub_cues, srt_path)
                            logger.info("Đã tự động tạo SRT cho %s (%.1fs, %d đoạn).", srt_path.name, dur, len(sub_cues))
                            self.manager.update_item(item.base, state=ITEM_TTS_DONE, error="")
                            report(100)
                            continue

                    # Ưu tiên chế độ lồng tiếng nếu file có cues hoặc dub_mode == "srt"
                    should_dub = getattr(item, "dub_mode", "text") == "srt" or bool(cues)
                    if should_dub and self._try_dubbing(item, report, errors, label):
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
                    # Luôn đảm bảo có file SRT khi dùng TTS toàn bài
                    if not srt_path.is_file():
                        from core.subtitles import Cue, text_to_cues, write_srt
                        if getattr(result, "cues", None):
                            sub_cues = [Cue(idx, s, e, t) for idx, (s, e, t) in enumerate(result.cues, 1)]
                        else:
                            sub_cues = text_to_cues(item.text, total_seconds=float(result.duration))
                        if sub_cues:
                            write_srt(sub_cues, srt_path)
                            logger.info("Đã tạo SRT %s sau khi TTS toàn bộ.", srt_path.name)
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
        if not cues or self.force:
            if not getattr(item, "text", "").strip():
                return False
            dub_cfg = self.settings.get("dubbing") or {}
            min_w = int(dub_cfg.get("min_words", 15) or 15)
            max_w = int(dub_cfg.get("max_words", 30) or 30)
            from core.dubbing import _cues_from_pieces, split_by_words, save_cues
            pieces = split_by_words(item.text, min_w, max_w)
            if pieces:
                cues = _cues_from_pieces(pieces)
                save_cues(self.manager, item.base, cues)
                self.manager.update_item(item.base, dub_mode="srt")
                logger.info("Tự động chia đoạn %s: %d đoạn (%d-%d từ).", item.base, len(cues), min_w, max_w)
            else:
                return False
        else:
            self.manager.update_item(item.base, dub_mode="srt")
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
                cue_cb=lambda _cue: (self.cues_updated.emit(item.base), self.items_updated.emit()),
            )
            dubbing = self.settings.get("dubbing") or {}
            user_mode = str(dubbing.get("timing_mode") or "").lower()
            has_real_srt = (
                getattr(item, "dub_mode", "text") == "srt"
                and item.text_file is not None
                and item.text_file.with_suffix(".srt").is_file()
            )
            mode = "timeline" if (user_mode == "timeline" and has_real_srt) else "natural"
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
            dub_mode="srt",
        )
        # Đảm bảo file SRT tồn tại cạnh file MP3
        srt_path = mp3.with_suffix(".srt")
        if not srt_path.is_file():
            from core.subtitles import Cue, write_srt
            srt_cues = [Cue(c.id, c.start, c.end, c.text) for c in cues if str(c.text or "").strip()]
            if srt_cues:
                write_srt(srt_cues, srt_path)
                logger.info("Đã tạo srt kèm theo cho %s.", srt_path.name)
        logger.log(
            SUCCESS_LEVEL,
            "Lồng tiếng xong %s (%.1f phút, %d cue, %s).",
            mp3.name, float(duration) / 60.0, len(cues), mode,
        )
        report(100)
        return True

    # ── video ────────────────────────────────────────────────────────────────
    def _stage_video(self, start: float, end: float, is_retry: bool = False) -> List[str]:
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
            force=(self.force and not is_retry),
            only=self.only,
            progress_cb=report,
            pause_cb=self.is_paused,
            cancel_cb=self.is_cancelled,
            stop_on_error=self._stop_on_error,
            skip_missing_audio=True,
        )
        self.videos_updated.emit()
        self.items_updated.emit()
        no_audio_count = sum(
            1 for j in self.manager.videos.values()
            if not renderer.has_audio(self.manager, j)
        )
        if no_audio_count > 0:
            self._add_note(f"Video: bỏ qua {no_audio_count} video chưa có audio.")

        if not done and not errors:
            logger.info("Video: không có file nào cần render.")
            self.status_changed.emit("Video: không có file cần render.", self.isRunning())
            if no_audio_count == 0:
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

    # ── thumbnail ─────────────────────────────────────────────────────────────
    def _stage_thumbnail(self, start: float, end: float, is_retry: bool = False) -> List[str]:
        def report(stage_pct: int, message: str, task_pct: Optional[int] = None) -> None:
            pct = max(0, min(100, int(stage_pct)))
            t_pct = max(0, min(100, int(task_pct))) if task_pct is not None else pct
            self.task_changed.emit(t_pct)
            self.overall_changed.emit(int(round(start + (end - start) * pct / 100.0)))
            self.status_changed.emit(message, self.isRunning())

        items = self.manager.get_items()
        if not items:
            report(100, "Thumbnail: không có mục nào.")
            return []

        from core.thumbnail import (
            DEFAULT_HIGHLIGHT_PALETTE,
            attach_thumbnail,
            build_image_prompt,
            default_font_path,
            generate_image,
            render_thumbnail,
        )

        thumb_cfg = self.settings.get("thumbnail") or {}
        selected_langs_raw = thumb_cfg.get("languages")
        if isinstance(selected_langs_raw, list) and selected_langs_raw:
            allowed_langs = {normalize_language_code(c) for c in selected_langs_raw if str(c).strip()}
            filtered = [
                it for it in items
                if normalize_language_code(getattr(it, "language", "") or self.manager.default_language) in allowed_langs
            ]
            if filtered:
                items = filtered

        report(10, "Bắt đầu tạo thumbnail cho các mục…")
        errors: List[str] = []
        total = len(items)

        provider = str(thumb_cfg.get("provider") or "pollinations")
        api = self.settings.get("api") or {}
        api_key = str(
            thumb_cfg.get("api_key")
            or api.get("imagen_key")
            or api.get("gemini_key")
            or (self.settings.get("ai") or {}).get("api_key")
            or (self.settings.get("video") or {}).get("ai_api_key")
            or ""
        ).strip()
        style = str(thumb_cfg.get("style") or "")
        template_id = str(thumb_cfg.get("template_id") or "drama_left")
        palette = dict(DEFAULT_HIGHLIGHT_PALETTE)
        palette.update(thumb_cfg.get("palette") or {})
        font_path = str(thumb_cfg.get("font_path") or default_font_path())

        for idx, item in enumerate(items):
            self._checkpoint()
            item_lang = normalize_language_code(getattr(item, "language", "") or self.manager.default_language)
            thumbs_lang_dir = self.manager.lang_dir("thumbs", item_lang, create=True)
            report(
                int((idx / total) * 100),
                f"Tạo thumbnail ({idx + 1}/{total}): {item.display_name} [{item_lang}]",
            )
            target_thumb = thumbs_lang_dir / f"{item.base}.jpg"
            if target_thumb.is_file() and not self.force and not is_retry:
                continue

            try:
                title = str(item.metadata.get("title") or item.display_name or item.base)
                prompt = build_image_prompt(
                    title=title,
                    transcript_excerpt=item.text[:600],
                    style=style,
                    template_id=template_id,
                )
                bg_target = thumbs_lang_dir / f"{item.base}_bg.png"
                generate_image(
                    provider=provider,
                    prompt=prompt,
                    out_path=bg_target,
                    api_key=api_key,
                    cancel_cb=self.is_cancelled,
                )
                render_thumbnail(
                    bg_path=bg_target,
                    title=title,
                    highlights=[],
                    template_id=template_id,
                    palette=palette,
                    out_path=target_thumb,
                    font_path=font_path,
                )
                attach_thumbnail(self.manager, item.base, target_thumb, language=item_lang)
                logger.info("Tạo thumbnail thành công: %s/%s", item_lang, target_thumb.name)
            except Exception as exc:
                logger.warning("Không tạo được thumbnail cho %s: %s", item.base, exc)
                errors.append(f"Thumbnail {item.display_name}: {exc}")

        self.items_updated.emit()
        report(100, f"Hoàn tất tạo thumbnail ({total - len(errors)}/{total}).")
        return errors

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
