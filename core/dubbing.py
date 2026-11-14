"""Cue-based dubbing: per-cue TTS, timeline fitting and final MP3/SRT joining.

The dubbing workflow mirrors dedicated SRT dubbing tools: a cue list is
imported, every cue gets its own MP3, then the files are joined either on the
original timeline (with ``atempo``/padding to hit each slot) or naturally (cue
after cue with a small gap). Everything is Qt-free; the UI and the pipeline
only call into :class:`DubbingEngine`.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.ai_client import clean_transcript_heuristics
from core.edge_tts_engine import EdgeTTSEngine
from core.ffmpeg_renderer import FFmpegRenderer
from core.kokoro_tts_engine import KokoroTTSEngine
from core.languages import DEFAULT_LANGUAGE, normalize_language
from core.piper_tts_engine import (
    PiperTTSEngine,
    resolve_tts_engine_for_language,
    resolve_voice_for_language_engine,
)
from core.supertonic_tts_engine import SupertonicTTSEngine
from core.subtitles import (
    Cue,
    cues_to_text,
    parse_srt,
    parse_vtt,
    write_srt,
)
from core.text_processor import count_words, split_by_sentences, split_by_words
from core.tts_engine import SynthesisCancelled, TTSEngine
from utils.files import load_json, read_text_smart, safe_filename, write_json_atomic
from utils.logger import get_logger
from utils.media_probe import format_duration, probe_duration

logger = get_logger("dubbing")

STATUS_PENDING = "PENDING"
STATUS_PROCESSING = "PROCESSING"
STATUS_DONE = "DONE"
STATUS_ERROR = "ERROR"

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_AUDIO_RATE = 44100


class DubbingError(RuntimeError):
    """Dubbing could not be completed (missing ffmpeg, no rendered cue…)."""


@dataclass
class CueJob:
    """One editable dubbing cue with its render state."""

    id: int
    start: float
    end: float
    text: str
    voice: str = ""
    status: str = STATUS_PENDING
    out: str = ""
    error: str = ""
    text_hash: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    def signature(self) -> str:
        payload = f"{self.text.strip()}|{self.voice.strip()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def reset(self) -> None:
        self.status = STATUS_PENDING
        self.out = ""
        self.error = ""
        self.text_hash = ""

    def to_state(self) -> Dict[str, Any]:
        return {
            "id": int(self.id),
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "text": self.text,
            "voice": self.voice,
            "status": self.status,
            "out": self.out,
            "error": self.error,
            "text_hash": self.text_hash,
        }

    @classmethod
    def from_state(cls, data: Dict[str, Any], index: int = 0) -> "CueJob":
        try:
            start = float(data.get("start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(data.get("end") or start)
        except (TypeError, ValueError):
            end = start
        if end < start:
            start, end = end, start
        try:
            cue_id = int(data.get("id") or index)
        except (TypeError, ValueError):
            cue_id = index
        return cls(
            id=cue_id,
            start=max(0.0, start),
            end=max(0.0, end),
            text=str(data.get("text") or ""),
            voice=str(data.get("voice") or ""),
            status=str(data.get("status") or STATUS_PENDING),
            out=str(data.get("out") or ""),
            error=str(data.get("error") or ""),
            text_hash=str(data.get("text_hash") or ""),
        )


# ── persistence ──────────────────────────────────────────────────────────────
def cues_path(manager, base: str) -> Path:
    item = manager.get_item(base) if hasattr(manager, "get_item") else None
    if item is not None:
        if getattr(item, "cues_path", None):
            return Path(item.cues_path)
        audio_p = getattr(item, "audio_path", None)
        if audio_p:
            return Path(audio_p).parent / f"{base}.cues.json"
    flat = Path(manager.audio_dir) / f"{base}.cues.json"
    if flat.is_file():
        return flat
    if item is not None and getattr(item, "language", ""):
        lang = normalize_language(item.language)
        if hasattr(manager, "lang_dir"):
            return manager.lang_dir("audio", lang, create=True) / f"{base}.cues.json"
    return flat


def load_cues(manager, base: str) -> List[CueJob]:
    data = load_json(cues_path(manager, base), None)
    if isinstance(data, dict):
        entries = data.get("cues") or []
    elif isinstance(data, list):
        entries = data
    else:
        return []
    cues: List[CueJob] = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            continue
        try:
            cue = CueJob.from_state(entry, index)
            if cue.text and ("[" in cue.text or ">>" in cue.text):
                cue.text = clean_transcript_heuristics(cue.text)
            cues.append(cue)
        except Exception as exc:  # corrupt cue: skip it, keep the rest
            logger.warning("Bỏ qua cue lỗi trong %s: %s", base, exc)
    return cues


def save_cues(manager, base: str, cues: Iterable[CueJob]) -> Path:
    target = cues_path(manager, base)
    write_json_atomic(target, {
        "base": base,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "cues": [cue.to_state() for cue in cues],
    })
    return target


def reindex(cues: Iterable[CueJob]) -> List[CueJob]:
    items = list(cues)
    for position, cue in enumerate(items, 1):
        cue.id = position
    return items


# ── importing ────────────────────────────────────────────────────────────────
def _ensure_input_text(manager, base: str, text: str, title: str,
                       source_path: Optional[Path] = None) -> None:
    item = manager.get_item(base) if hasattr(manager, "get_item") else None
    if item is not None and item.text_file is not None:
        return
    if source_path is not None and source_path.is_file():
        try:
            source_path.resolve().relative_to(Path(manager.script_dir).resolve())
            txt_path = source_path
            json_path = source_path.with_suffix(".json")
            if not json_path.exists():
                lang = normalize_language(
                    source_path.parent.name
                    if source_path.parent != Path(manager.script_dir)
                    else getattr(manager, "default_language", DEFAULT_LANGUAGE)
                )
                write_json_atomic(json_path, {"title": title, "language": lang})
            return
        except ValueError:
            pass
    txt_path = Path(manager.script_dir) / f"{base}.txt"
    json_path = Path(manager.script_dir) / f"{base}.json"
    if not txt_path.exists():
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(str(text).strip() + "\n", encoding="utf-8", newline="\n")
    if not json_path.exists():
        write_json_atomic(json_path, {"title": title, "language": DEFAULT_LANGUAGE})


def _mark_dubbing(manager, base: str) -> None:
    manager.scan()
    if manager.get_item(base) is not None:
        manager.update_item(base, dub_mode="srt",
                            cues_path=cues_path(manager, base))


def import_srt(manager, srt_path: str | os.PathLike) -> str:
    """Import an SRT/VTT file as a cue list and register it as an input."""
    source = Path(srt_path)
    if not source.is_file():
        raise DubbingError(f"Không tìm thấy file phụ đề: {source}")
    raw = read_text_smart(source)
    cues = parse_srt(raw) if source.suffix.lower() != ".vtt" else parse_vtt(raw)
    if not cues and source.suffix.lower() == ".vtt":
        cues = parse_srt(raw)
    if not cues:
        raise DubbingError(f"Không đọc được cue nào từ {source.name}.")
    base = safe_filename(source.stem, "script")
    _ensure_input_text(manager, base, cues_to_text(cues), source.stem, source)
    manager.scan()
    jobs = [CueJob(index, cue.start, cue.end, cue.text) for index, cue in enumerate(cues, 1)]
    save_cues(manager, base, jobs)
    _mark_dubbing(manager, base)
    logger.info("Import SRT %s → %d cue.", source.name, len(jobs))
    return base


def _cues_from_pieces(pieces: Iterable[str]) -> List[CueJob]:
    """Build evenly-timed cues from plain-text pieces (no real timeline)."""
    rate = 0.065  # seconds per character, matching subtitles.text_to_cues
    cues: List[CueJob] = []
    cursor = 0.0
    for position, piece in enumerate(pieces, 1):
        clean_piece = clean_transcript_heuristics(str(piece or "")).strip()
        if not clean_piece:
            continue
        duration = max(0.6, len(clean_piece) * rate)
        cues.append(CueJob(position, cursor, cursor + duration, clean_piece))
        cursor += duration
    return cues


def import_txt(manager, txt_path: str | os.PathLike,
               min_words: int = 15, max_words: int = 30,
               split_mode: str = "words") -> str:
    """Import plain text as cues, splitting by word range or each sentence (cứ 1 câu là 1 đoạn)."""
    source = Path(txt_path)
    if not source.is_file():
        raise DubbingError(f"Không tìm thấy file văn bản: {source}")
    text = clean_transcript_heuristics(read_text_smart(source))
    if split_mode == "sentence":
        pieces = split_by_sentences(text)
    else:
        pieces = split_by_words(text, min_words, max_words)
    if not pieces:
        raise DubbingError(f"{source.name} không có nội dung để lồng tiếng.")
    base = safe_filename(source.stem, "script")
    _ensure_input_text(manager, base, text, source.stem, source)
    manager.scan()
    jobs = _cues_from_pieces(pieces)
    save_cues(manager, base, jobs)
    _mark_dubbing(manager, base)
    logger.info("Import TXT %s → %d cue (mode=%s).", source.name, len(jobs), split_mode)
    return base


def apply_import_options(cues: List[CueJob], auto_split: bool = True,
                         min_words: int = 15, max_words: int = 30,
                         merge_short: bool = False,
                         split_mode: str = "words") -> List[CueJob]:
    """Run the optional split/merge import passes in a predictable order."""
    result = list(cues)
    if merge_short:
        result = merge_short_cues(result)
    if auto_split:
        if split_mode == "sentence":
            result = split_cues_by_sentences(result)
        else:
            result = split_cues_by_words(result, min_words, max_words)
    return reindex(result)


def split_cues_by_sentences(cues: Iterable[CueJob]) -> List[CueJob]:
    """Split cues where each cue contains exactly one sentence, keeping the total duration."""
    out: List[CueJob] = []
    for cue in cues:
        raw_text = str(cue.text or "").strip()
        text = clean_transcript_heuristics(raw_text) if ("[" in raw_text or ">>" in raw_text) else raw_text
        cue.text = text
        if not text:
            out.append(cue)
            continue
        pieces = [piece for piece in split_by_sentences(text) if piece.strip()]
        if len(pieces) <= 1:
            out.append(cue)
            continue
        weights = [max(1, len(piece)) for piece in pieces]
        total_weight = float(sum(weights))
        span = cue.duration
        cursor = float(cue.start)
        for position, (piece, weight) in enumerate(zip(pieces, weights)):
            length = span * weight / total_weight
            end = cursor + length
            if position == len(pieces) - 1:
                end = cue.end
            out.append(CueJob(0, cursor, end, piece, voice=cue.voice))
            cursor = end
    return reindex(out)


def split_cues_by_words(cues: Iterable[CueJob], min_words: int = 15,
                        max_words: int = 30) -> List[CueJob]:
    """Split over-long cues at sentence ends, keeping the total duration."""
    lo = max(1, int(min_words or 15))
    hi = max(lo + 1, int(max_words or 30))
    out: List[CueJob] = []
    for cue in cues:
        raw_text = str(cue.text or "").strip()
        text = clean_transcript_heuristics(raw_text) if ("[" in raw_text or ">>" in raw_text) else raw_text
        cue.text = text
        if not text or count_words(text) <= hi:
            out.append(cue)
            continue
        pieces = [piece for piece in split_by_words(text, lo, hi) if piece.strip()]
        if len(pieces) <= 1:
            out.append(cue)
            continue
        weights = [max(1, len(piece)) for piece in pieces]
        total_weight = float(sum(weights))
        span = cue.duration
        cursor = float(cue.start)
        for position, (piece, weight) in enumerate(zip(pieces, weights)):
            length = span * weight / total_weight
            end = cursor + length
            if position == len(pieces) - 1:
                end = cue.end
            out.append(CueJob(0, cursor, end, piece, voice=cue.voice))
            cursor = end
    return reindex(out)


def merge_short_cues(cues: Iterable[CueJob], min_chars: int = 25,
                     max_gap: float = 0.4) -> List[CueJob]:
    """Merge a short cue into the next one when the pause between them is tiny."""
    out: List[CueJob] = []
    for cue in cues:
        if out:
            previous = out[-1]
            gap = float(cue.start) - float(previous.end)
            if len(previous.text.strip()) < min_chars and gap <= max_gap:
                previous.text = f"{previous.text.strip()} {cue.text.strip()}".strip()
                previous.end = max(previous.end, cue.end)
                previous.reset()
                continue
        out.append(cue)
    return reindex(out)


# ── timing maths ─────────────────────────────────────────────────────────────
def fit_plan(audio_dur: float, slot_dur: float, max_tempo: float = 1.30) -> Dict[str, float | bool]:
    """How to fit ``audio_dur`` into ``slot_dur``: tempo, pad and trim amounts."""
    try:
        audio = max(0.0, float(audio_dur or 0.0))
    except (TypeError, ValueError):
        audio = 0.0
    try:
        slot = float(slot_dur or 0.0)
    except (TypeError, ValueError):
        slot = 0.0
    try:
        cap = max(1.0, float(max_tempo or 1.0))
    except (TypeError, ValueError):
        cap = 1.3
    if slot <= 0:
        return {"tempo": 1.0, "pad": 0.0, "trim": 0.0, "natural": True}
    if audio <= slot:
        return {"tempo": 1.0, "pad": slot - audio, "trim": 0.0, "natural": False}
    if audio <= slot * cap:
        return {"tempo": audio / slot, "pad": 0.0, "trim": 0.0, "natural": False}
    return {"tempo": cap, "pad": 0.0, "trim": audio / cap - slot, "natural": False}


def natural_timings(durations: Iterable[float], gap: float = 0.0,
                    start: float = 0.0) -> List[Tuple[float, float]]:
    """Cumulative ``(start, end)`` pairs for cue-after-cue playback."""
    timings: List[Tuple[float, float]] = []
    cursor = max(0.0, float(start or 0.0))
    spacing = max(0.0, float(gap or 0.0))
    for duration in durations:
        try:
            length = max(0.0, float(duration or 0.0))
        except (TypeError, ValueError):
            length = 0.0
        timings.append((cursor, cursor + length))
        cursor += length + spacing
    return timings


# ── engine ───────────────────────────────────────────────────────────────────
class DubbingEngine:
    """Synthesize cues and join them into one MP3 + SRT."""

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        self.settings = settings or {}
        dubbing = self.settings.get("dubbing") or {}
        tts = self.settings.get("tts") or {}
        self.retries = max(0, int(dubbing.get("retries_per_cue", 1) or 0))
        self.edge_parallel = max(1, int(dubbing.get("edge_parallel", 3) or 1))
        self.gap = float(dubbing.get("gap", 0.0) or 0.0)
        self.timing_mode = str(dubbing.get("timing_mode") or "natural")
        self.bitrate = str(tts.get("bitrate") or "192k")
        try:
            self.speed = float(tts.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.speed = 1.0
        self.ffmpeg = FFmpegRenderer(self.settings).ffmpeg
        self._lock = threading.Lock()
        self._silence_cache: Dict[int, Path] = {}

    # ── synthesis ────────────────────────────────────────────────────────────
    def cue_audio_path(self, manager, cue: CueJob) -> Path:
        raw = str(cue.out or "")
        if not raw:
            return Path()
        path = Path(raw)
        if not path.is_absolute():
            path = Path(manager.project_dir) / path
        return path

    @staticmethod
    def _out_value(manager, path: Path) -> str:
        try:
            return str(path.relative_to(Path(manager.project_dir)))
        except ValueError:
            return str(path)

    def synthesize_item(
        self,
        manager,
        item,
        cues: List[CueJob],
        progress_cb: Optional[Callable[[int], None]] = None,
        message_cb: Optional[Callable[[str], None]] = None,
        pause_cb: Optional[Callable[[], bool]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        cue_cb: Optional[Callable[[CueJob], None]] = None,
    ) -> float:
        """Render every pending cue; returns the total audio duration (seconds)."""
        item_base = str(getattr(item, "base", ""))
        audio_parent = (
            Path(item.audio_path).parent
            if getattr(item, "audio_path", None)
            else Path(manager.audio_dir)
        )
        out_dir = audio_parent / "cues" / item_base
        out_dir.mkdir(parents=True, exist_ok=True)
        language = normalize_language(
            getattr(item, "language", "") or manager.default_language)
        total = max(1, len(cues))
        progress = {"done": 0}

        pending: List[CueJob] = []
        for cue in cues:
            path = out_dir / f"{int(cue.id):04d}.mp3"
            if not str(cue.text or "").strip():
                cue.status = STATUS_DONE
                cue.out = ""
                cue.error = ""
                progress["done"] += 1
                continue
            signature = cue.signature()
            if path.is_file() and path.stat().st_size > 0 and cue.text_hash == signature:
                cue.status = STATUS_DONE
                cue.out = self._out_value(manager, path)
                cue.error = ""
                progress["done"] += 1
                continue
            cue.reset()
            pending.append(cue)
        with self._lock:
            save_cues(manager, item_base, cues)

        def finish(cue: CueJob) -> None:
            with self._lock:
                progress["done"] += 1
                percent = int(100 * progress["done"] / total)
                save_cues(manager, item_base, cues)
            if cue_cb:
                try:
                    cue_cb(cue)
                except Exception:
                    pass
            if progress_cb:
                try:
                    progress_cb(percent)
                except Exception:
                    pass

        if not pending:
            if progress_cb:
                progress_cb(100)
            return self._total_duration(manager, cues)

        if message_cb:
            message_cb(
                f"Lồng tiếng {item_base}: {len(pending)} cue cần tạo "
                f"({len(cues) - len(pending)} đã xong)."
            )

        engine_kind = resolve_tts_engine_for_language(self.settings, language)

        seq_factories = {
            "h2mtts": TTSEngine,
            "piper": PiperTTSEngine,
            "supertonic": SupertonicTTSEngine,
            "kokoro": KokoroTTSEngine,
        }
        if engine_kind in seq_factories:
            engine = seq_factories[engine_kind](self.settings)
            try:
                for cue in pending:
                    self._idle(pause_cb, cancel_cb)
                    self._render_cue(
                        engine, manager, item_base, out_dir, cue,
                        language, None, finish, pause_cb, cancel_cb,
                        engine_kind=engine_kind,
                    )
            finally:
                engine.close()
        else:
            engine = EdgeTTSEngine(self.settings)
            workers = min(self.edge_parallel, max(1, len(pending)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for start in range(0, len(pending), workers):
                    self._idle(pause_cb, cancel_cb)
                    batch = pending[start:start + workers]
                    futures = [
                        pool.submit(
                            self._render_cue, engine, manager, item_base, out_dir,
                            cue, language, self.edge_parallel, finish, pause_cb,
                            cancel_cb, "edge",
                        )
                        for cue in batch
                    ]
                    for future in futures:
                        future.result()
        if progress_cb:
            progress_cb(100)
        return self._total_duration(manager, cues)

    def _render_cue(self, engine, manager, item_base: str, out_dir: Path,
                    cue: CueJob, language: str, _parallel: Optional[int],
                    finish: Callable[[CueJob], None],
                    pause_cb, cancel_cb,
                    engine_kind: str = "") -> bool:
        path = out_dir / f"{int(cue.id):04d}.mp3"
        signature = cue.signature()
        attempt = 0
        kind = engine_kind or ("h2mtts" if language == "vi" else "edge")
        while True:
            if cancel_cb and cancel_cb():
                cue.status = STATUS_PENDING
                raise SynthesisCancelled("Đã huỷ lồng tiếng.")
            try:
                cue.status = STATUS_PROCESSING
                cue.error = ""
                if kind == "h2mtts":
                    eff_voice = cue.voice or resolve_voice_for_language_engine(
                        self.settings, language, "h2mtts"
                    ) or None
                    engine.synthesize(
                        cue.text, path,
                        voice=eff_voice,
                        speed=self.speed,
                        pause_cb=pause_cb,
                        cancel_cb=cancel_cb,
                    )
                else:
                    engine.synthesize(
                        cue.text, path,
                        language=language,
                        voice=cue.voice or None,
                        speed=self.speed,
                        pause_cb=pause_cb,
                        cancel_cb=cancel_cb,
                    )
            except SynthesisCancelled:
                cue.status = STATUS_PENDING
                raise
            except Exception as exc:  # engine errors must not kill the whole item
                cue.error = str(exc)
                logger.error("Cue %s lỗi: %s", cue.id, exc)
                if attempt >= self.retries:
                    cue.status = STATUS_ERROR
                    finish(cue)
                    return False
                attempt += 1
                time.sleep(0.5)
                continue
            cue.status = STATUS_DONE
            cue.out = self._out_value(manager, path)
            cue.text_hash = signature
            cue.error = ""
            finish(cue)
            return True

    def _total_duration(self, manager, cues: Iterable[CueJob]) -> float:
        total = 0.0
        for cue in cues:
            path = self.cue_audio_path(manager, cue)
            if path.is_file():
                total += probe_duration(path)
        return total

    @staticmethod
    def _idle(pause_cb, cancel_cb) -> None:
        while pause_cb and pause_cb():
            if cancel_cb and cancel_cb():
                raise SynthesisCancelled("Đã huỷ lồng tiếng.")
            time.sleep(0.2)
        if cancel_cb and cancel_cb():
            raise SynthesisCancelled("Đã huỷ lồng tiếng.")

    # ── joining ──────────────────────────────────────────────────────────────
    def join_item(self, manager, item, cues: List[CueJob], mode: str = "timeline",
                  gap: Optional[float] = None) -> Tuple[Path, Path, float]:
        """Join rendered cues into ``audio/<base>.mp3`` plus a matching SRT."""
        mode = "natural" if str(mode).lower() == "natural" else "timeline"
        spacing = self.gap if gap is None else max(0.0, float(gap))
        usable = [
            cue for cue in cues
            if str(cue.text or "").strip()
            and self.cue_audio_path(manager, cue).is_file()
        ]
        if not usable:
            raise DubbingError(
                "Chưa có cue nào được lồng tiếng — bấm Start để tạo audio trước.")
        item_base = str(getattr(item, "base", ""))
        target_mp3 = Path(getattr(item, "audio_path",
                                  Path(manager.audio_dir) / f"{item_base}.mp3"))
        target_mp3.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_dub_", dir=str(target_mp3.parent)))
        try:
            if mode == "timeline":
                segments, srt_cues = self._timeline_segments(
                    manager, usable, temp_dir)
            else:
                segments, srt_cues = self._natural_segments(
                    manager, usable, temp_dir, spacing)
            concat_file = temp_dir / "concat.txt"
            concat_file.write_text(
                "\n".join(f"file '{self._concat_name(path)}'" for path in segments),
                encoding="utf-8",
            )
            temp_mp3 = target_mp3.with_name(f"{target_mp3.stem}.join.tmp{target_mp3.suffix}")
            self._run([
                self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-c:a", "libmp3lame", "-b:a", self.bitrate, str(temp_mp3),
            ])
            if not temp_mp3.is_file() or temp_mp3.stat().st_size == 0:
                raise DubbingError("FFmpeg không tạo được file MP3 khi ghép.")
            os.replace(temp_mp3, target_mp3)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        srt_path = target_mp3.with_suffix(".srt")
        write_srt(srt_cues, srt_path)
        duration = probe_duration(target_mp3)
        if duration <= 0:
            duration = max((cue.end for cue in srt_cues), default=0.0)
        logger.info("Ghép %d cue → %s (%s, %s).", len(usable), target_mp3.name,
                    format_duration(duration), mode)
        return target_mp3, srt_path, duration

    def _timeline_segments(self, manager, cues: List[CueJob], temp_dir: Path):
        segments: List[Path] = []
        cursor = 0.0
        for index, cue in enumerate(cues, 1):
            if cue.start > cursor + 0.002:
                segments.append(self._silence(temp_dir, cue.start - cursor))
            slot = max(0.0, cue.duration)
            source = self.cue_audio_path(manager, cue)
            audio_dur = probe_duration(source)
            plan = fit_plan(audio_dur, slot)
            segments.append(self._fit_segment(temp_dir, source, index, plan, slot))
            cursor = cue.start + slot
        srt_cues = [
            Cue(position, cue.start, cue.end, cue.text)
            for position, cue in enumerate(cues, 1)
        ]
        return segments, srt_cues

    def _natural_segments(self, manager, cues: List[CueJob], temp_dir: Path,
                          gap: float):
        durations = [probe_duration(self.cue_audio_path(manager, cue)) for cue in cues]
        timings = natural_timings(durations, gap)
        segments: List[Path] = []
        for index, (cue, duration) in enumerate(zip(cues, durations), 1):
            source = self.cue_audio_path(manager, cue)
            segments.append(self._fit_segment(
                temp_dir, source, index,
                {"tempo": 1.0, "pad": 0.0, "trim": 0.0, "natural": True}, 0.0))
            if index < len(cues) and gap > 0:
                segments.append(self._silence(temp_dir, gap))
        srt_cues = [
            Cue(position, start, end, cue.text)
            for position, (cue, (start, end)) in enumerate(zip(cues, timings), 1)
        ]
        return segments, srt_cues

    def _fit_segment(self, temp_dir: Path, source: Path, index: int,
                     plan: Dict[str, Any], slot: float) -> Path:
        target = temp_dir / f"cue_{index:05d}.wav"
        filters: List[str] = []
        tempo = float(plan.get("tempo") or 1.0)
        if abs(tempo - 1.0) > 0.001:
            filters.append(f"atempo={tempo:.6f}")
        if float(plan.get("pad") or 0.0) > 0.0:
            filters.append("apad")
        if slot > 0 and not plan.get("natural"):
            filters.append(f"atrim=0:{slot:.3f}")
        filters.append("asetpts=PTS-STARTPTS")
        self._run([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-af", ",".join(filters),
            "-ar", str(_AUDIO_RATE), "-ac", "2", "-c:a", "pcm_s16le",
            str(target),
        ])
        if not target.is_file():
            raise DubbingError(f"FFmpeg không xử lý được cue {source.name}.")
        return target

    def _silence(self, temp_dir: Path, duration: float) -> Path:
        milliseconds = max(1, int(round(float(duration) * 1000)))
        cached = self._silence_cache.get(milliseconds)
        if cached is not None and cached.is_file():
            return cached
        target = temp_dir / f"silence_{milliseconds:07d}.wav"
        self._run([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={_AUDIO_RATE}",
            "-t", f"{milliseconds / 1000.0:.3f}",
            "-c:a", "pcm_s16le", str(target),
        ])
        self._silence_cache[milliseconds] = target
        return target

    @staticmethod
    def _concat_name(path: Path) -> str:
        return str(path).replace("\\", "/").replace("'", "'\\''")

    @staticmethod
    def _run(cmd: Sequence[str], timeout: float = 600.0) -> None:
        try:
            proc = subprocess.run(
                list(cmd), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, creationflags=_CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise DubbingError(
                "Không tìm thấy ffmpeg. Kiểm tra đường dẫn trong tab Video.") from exc
        except subprocess.TimeoutExpired as exc:
            raise DubbingError("FFmpeg xử lý quá lâu (timeout).") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise DubbingError(f"FFmpeg lỗi: {detail or 'không rõ nguyên nhân'}")


def cue_jobs_from_subtitles(cues: Iterable[Cue]) -> List[CueJob]:
    return [CueJob(index, cue.start, cue.end, cue.text)
            for index, cue in enumerate(cues, 1)]
