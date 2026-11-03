"""H2MTTS integration: voice discovery and text -> MP3 synthesis.

Three real execution paths are supported, all against the local model folder:

* ``turbo`` + ``gpu``   - VieNeu v3 Turbo PyTorch on CUDA (48 kHz, batched)
* ``turbo`` + ``onnx``  - VieNeu v3 Turbo ONNX-Int8 on CPU (48 kHz)
* ``nano``              - VieNeu v3 Nano ONNX Flow on CPU (24 kHz)

Voice packs are read from ``<H2MTTS>/voice-packs``; the model directories are
loaded directly (no network needed once the weights are present).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from core.text_processor import chunk_text, normalize_text
from utils.files import load_json, natural_key
from utils.logger import get_logger
from utils.media_probe import ffmpeg_version, probe_duration, resolve_tool

logger = get_logger("tts")

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ProgressCb = Optional[Callable[[int], None]]
MessageCb = Optional[Callable[[str], None]]
CancelCb = Optional[Callable[[], bool]]
PauseCb = Optional[Callable[[], bool]]


def audio_variant_paths(out_path: str | os.PathLike, variants: int) -> List[Path]:
    """Deterministic outputs for N audio variants: ``<stem>-1<suffix>`` … ``<stem>-N<suffix>``."""
    target = Path(out_path)
    count = max(1, int(variants))
    return [
        target.with_name(f"{target.stem}-{variant}{target.suffix}")
        for variant in range(1, count + 1)
    ]


class TTSError(RuntimeError):
    """Any user-visible synthesis failure."""


class SynthesisCancelled(TTSError):
    """Raised when the user stops the running pipeline."""


@dataclass
class VoiceInfo:
    name: str
    engine: str
    description: str = ""
    gender: str = ""
    region: str = ""
    featured: Optional[int] = None
    aliases: List[str] = field(default_factory=list)
    speaker_emb: Optional[np.ndarray] = None
    codes: Optional[np.ndarray] = None
    style: Optional[np.ndarray] = None
    source: str = "pack"
    usable: bool = True

    def public(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": self.engine,
            "description": self.description,
            "gender": self.gender,
            "region": self.region,
            "featured": self.featured,
            "aliases": list(self.aliases),
            "source": self.source,
            "usable": self.usable,
        }

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.region:
            parts.append(self.region)
        if self.description and self.description not in self.name:
            parts.append(self.description)
        return " · ".join(parts)


@dataclass
class SynthResult:
    path: Path
    duration: float
    sample_rate: int
    engine: str
    backend: str
    voice: str
    chunks: int
    characters: int

    def public(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "duration": self.duration,
            "sample_rate": self.sample_rate,
            "engine": self.engine,
            "backend": self.backend,
            "voice": self.voice,
            "chunks": self.chunks,
            "characters": self.characters,
        }


class H2MLayout:
    """All paths the app needs inside the H2MTTS model repository."""

    def __init__(self, root: str | os.PathLike | None) -> None:
        from core.settings import normalize_h2mtts_path

        self.root = Path(normalize_h2mtts_path(root))
        models = self.root / "models"
        self.turbo_dir = models / "H2M-TTS-v3-Turbo"
        self.turbo_weights = self.turbo_dir / "update" / "model.safetensors"
        self.turbo_onnx_dir = self.turbo_dir / "onnx_int8"
        self.nano_dir = models / "H2M-TTS-v3-Nano"
        self.moss_torch_dir = models / "MOSS-Audio-Tokenizer-Nano"
        self.moss_onnx_dir = models / "MOSS-Audio-Tokenizer-ONNX"
        self.voice_packs = self.root / "voice-packs"
        self.turbo_pack = self.voice_packs / "voice-pack.json"
        self.nano_pack = self.voice_packs / "voice-pack-nano.json"
        self.catalog = self.voice_packs / "voice-catalog-v4.json"
        self.embeddings = self.voice_packs / "embeddings"
        self.model_turbo_voices = self.turbo_dir / "voices_v3_turbo.json"
        self.model_nano_pack = self.nano_dir / "voice-pack-nano.json"

    @property
    def valid(self) -> bool:
        return self.root.is_dir()

    def missing(self) -> List[str]:
        required = {
            "Turbo weights (update/model.safetensors)": self.turbo_weights.is_file(),
            "Turbo ONNX (onnx_int8/config.json)": (self.turbo_onnx_dir / "config.json").is_file(),
            "Nano model (config.json)": (self.nano_dir / "config.json").is_file(),
            "MOSS codec PyTorch (config.json)": (self.moss_torch_dir / "config.json").is_file(),
            "MOSS codec ONNX (decode_full)": (self.moss_onnx_dir / "moss_audio_tokenizer_decode_full.onnx").is_file(),
            "Turbo voice pack": self.turbo_pack.is_file(),
            "Nano voice pack": self.nano_pack.is_file(),
        }
        return [name for name, ok in required.items() if not ok]


def _region_from_text(text: str) -> str:
    haystack = (text or "").lower()
    if "bắc" in haystack or "north" in haystack:
        return "Bắc"
    if "nam" in haystack or "south" in haystack:
        return "Nam"
    if "trung" in haystack or "central" in haystack:
        return "Trung"
    return ""


def _featured_rank(value: Any) -> Optional[int]:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def _sdk_assets_dir() -> Optional[Path]:
    try:
        spec = importlib.util.find_spec("vieneu")
        if spec and spec.origin:
            path = Path(spec.origin).parent / "assets"
            if path.is_dir():
                return path
    except Exception:
        pass
    return None


def _strip_pad(codes: np.ndarray | None) -> Optional[np.ndarray]:
    if codes is None:
        return None
    try:
        from vieneu_utils.core_utils import strip_encoder_pad_frame

        return strip_encoder_pad_frame(codes)
    except Exception:
        return codes


class TTSEngine:
    """Loads VieNeu models on demand and renders text to MP3."""

    BATCH_SIZE = 8

    def __init__(self, settings: Dict[str, Any]) -> None:
        self.settings = settings or {}
        self.layout = H2MLayout((self.settings.get("paths") or {}).get("h2mtts"))
        self.ffmpeg = resolve_tool((self.settings.get("paths") or {}).get("ffmpeg"), "ffmpeg")
        self.ffprobe = resolve_tool((self.settings.get("paths") or {}).get("ffprobe"), "ffprobe")
        self._lock = threading.RLock()
        self._tts: Any = None
        self._loaded_key: Optional[tuple] = None
        self._engine_name = ""
        self._backend_used = ""
        self._sample_rate = 48000
        self._torbo_phonemize = None
        self._max_expected_frames = None
        self._voice_cache: Dict[str, List[VoiceInfo]] = {}
        self._defaults: Dict[str, str] = {}
        self._catalog_cache: Optional[List[dict]] = None
        self._embedding_cache: Optional[List[VoiceInfo]] = None

    # ── discovery ────────────────────────────────────────────────────────────
    def reload_voices(self) -> None:
        self._voice_cache.clear()
        self._defaults.clear()
        self._catalog_cache = None
        self._embedding_cache = None

    def _load_pack_file(self, path: Path) -> tuple[str, Dict[str, dict]]:
        data = load_json(path, {}) or {}
        if not isinstance(data, dict):
            return "", {}
        presets = data.get("presets") if isinstance(data.get("presets"), dict) else {}
        return str(data.get("default_voice") or ""), presets

    def _turbo_voices(self) -> List[VoiceInfo]:
        if "turbo" in self._voice_cache:
            return self._voice_cache["turbo"]
        sources: List[Path] = []
        if self.layout.turbo_pack.is_file():
            sources.append(self.layout.turbo_pack)
        if self.layout.model_turbo_voices.is_file():
            sources.append(self.layout.model_turbo_voices)
        if not sources:
            assets = _sdk_assets_dir()
            if assets and (assets / "voices_v3_turbo.json").is_file():
                sources.append(assets / "voices_v3_turbo.json")
        merged: Dict[str, VoiceInfo] = {}
        default = ""
        for path in sources:
            pack_default, presets = self._load_pack_file(path)
            if pack_default and not default:
                default = pack_default
            for name, raw in presets.items():
                if not isinstance(raw, dict):
                    continue
                emb = raw.get("speaker_emb")
                codes = raw.get("codes")
                speaker = np.asarray(emb, dtype=np.float32) if emb is not None else None
                ref_codes = np.asarray(codes, dtype=np.int64) if codes is not None else None
                ref_codes = _strip_pad(ref_codes)
                merged[name] = VoiceInfo(
                    name=name,
                    engine="turbo",
                    description=str(raw.get("description") or ""),
                    gender=str(raw.get("gender") or ""),
                    region=str(raw.get("region") or _region_from_text(str(raw.get("description") or ""))),
                    featured=_featured_rank(raw.get("featured")),
                    aliases=[str(a) for a in (raw.get("aliases") or []) if a],
                    speaker_emb=speaker,
                    codes=ref_codes,
                    source="pack",
                    usable=speaker is not None,
                )
        if default not in merged:
            default = next(iter(merged), "")
        self._defaults["turbo"] = default
        result = sorted(
            merged.values(),
            key=lambda v: (v.featured is None, v.featured or 0, natural_key(v.name)),
        )
        self._voice_cache["turbo"] = result
        logger.info("Đã nạp %d giọng Turbo (mặc định: %s)", len(result), default or "—")
        return result

    def _nano_voices(self) -> List[VoiceInfo]:
        if "nano" in self._voice_cache:
            return self._voice_cache["nano"]
        sources: List[Path] = []
        if self.layout.nano_pack.is_file():
            sources.append(self.layout.nano_pack)
        if self.layout.model_nano_pack.is_file() and self.layout.model_nano_pack != self.layout.nano_pack:
            sources.append(self.layout.model_nano_pack)
        if not sources:
            assets = _sdk_assets_dir()
            if assets and (assets / "voices_v3_nano.json").is_file():
                sources.append(assets / "voices_v3_nano.json")
        merged: Dict[str, VoiceInfo] = {}
        default = ""
        for path in sources:
            pack_default, presets = self._load_pack_file(path)
            if pack_default and not default:
                default = pack_default
            for name, raw in presets.items():
                if not isinstance(raw, dict):
                    continue
                emb = raw.get("speaker_emb")
                style = raw.get("style")
                speaker = np.asarray(emb, dtype=np.float32) if emb is not None else None
                style_arr = np.asarray(style, dtype=np.float32) if style is not None else None
                merged[name] = VoiceInfo(
                    name=name,
                    engine="nano",
                    description=str(raw.get("description") or ""),
                    gender=str(raw.get("gender") or ""),
                    region=str(raw.get("region") or _region_from_text(str(raw.get("description") or ""))),
                    featured=_featured_rank(raw.get("featured")),
                    speaker_emb=speaker,
                    style=style_arr,
                    source="pack",
                    usable=speaker is not None and style_arr is not None,
                )
        if default not in merged:
            default = next(iter(merged), "")
        self._defaults["nano"] = default
        result = sorted(merged.values(), key=lambda v: (v.featured is None, v.featured or 0, natural_key(v.name)))
        self._voice_cache["nano"] = result
        logger.info("Đã nạp %d giọng Nano (mặc định: %s)", len(result), default or "—")
        return result

    def voices(self, engine: str) -> List[VoiceInfo]:
        engine = "nano" if str(engine).lower() == "nano" else "turbo"
        return self._turbo_voices() if engine == "turbo" else self._nano_voices()

    def default_voice(self, engine: str) -> str:
        engine = "nano" if str(engine).lower() == "nano" else "turbo"
        self.voices(engine)
        return self._defaults.get(engine, "")

    def resolve_voice(self, engine: str, name: Optional[str]) -> VoiceInfo:
        engine = "nano" if str(engine).lower() == "nano" else "turbo"
        voices = self.voices(engine)
        if not voices:
            raise TTSError(
                f"Không tìm thấy giọng {engine}. Kiểm tra lại thư mục voice-packs của H2MTTS."
            )
        if not name:
            name = self.default_voice(engine)
        resolved: Optional[VoiceInfo] = None
        for voice in voices:
            if voice.name == name:
                resolved = voice
                break
        folded = str(name).casefold()
        if resolved is None:
            for voice in voices:
                if voice.name.casefold() == folded:
                    resolved = voice
                    break
        if resolved is None:
            for voice in voices:
                if any(str(alias).casefold() == folded for alias in voice.aliases):
                    resolved = voice
                    break
        if resolved is None:
            raise TTSError(
                f"Giọng '{name}' không có trong bộ giọng {engine}. "
                f"Hiện có: {', '.join(v.name for v in voices[:10])}..."
            )
        if not resolved.usable:
            raise TTSError(f"Giọng '{resolved.name}' chỉ là dữ liệu tham khảo, không tổng hợp được.")
        return resolved

    def regions(self, engine: str) -> List[str]:
        wanted = {"Bắc", "Trung", "Nam"}
        found = {v.region for v in self.voices(engine) if v.region in wanted}
        return [r for r in ("Bắc", "Trung", "Nam") if r in found] + sorted(found - wanted)

    def catalog_entries(self) -> List[dict]:
        if self._catalog_cache is not None:
            return self._catalog_cache
        data = load_json(self.layout.catalog, []) if self.layout.catalog.is_file() else []
        entries: List[dict] = []
        if isinstance(data, list):
            for raw in data:
                if isinstance(raw, dict) and raw.get("name"):
                    entries.append(
                        {
                            "name": str(raw.get("name")),
                            "description": str(raw.get("description") or ""),
                            "gender": str(raw.get("gender") or ""),
                            "region": _region_from_text(str(raw.get("region") or raw.get("description") or "")),
                        }
                    )
        self._catalog_cache = entries
        return entries

    def embedding_voices(self) -> List[VoiceInfo]:
        if self._embedding_cache is not None:
            return self._embedding_cache
        result: List[VoiceInfo] = []
        if self.layout.embeddings.is_dir():
            for path in sorted(self.layout.embeddings.glob("*.pt"), key=lambda p: natural_key(p.name)):
                stem = path.stem
                result.append(
                    VoiceInfo(
                        name=stem,
                        engine="turbo",
                        description="File mã nhúng tham chiếu (.pt) — chỉ để tham khảo",
                        gender="Nam" if "(nam" in stem.lower() else ("Nữ" if "(nữ" in stem.lower() else ""),
                        region=_region_from_text(stem),
                        source="embedding",
                        usable=False,
                    )
                )
        self._embedding_cache = result
        return result

    # ── environment ──────────────────────────────────────────────────────────
    def environment(self, deep: bool = False) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "h2mtts": str(self.layout.root),
            "h2mtts_ok": self.layout.valid,
            "missing": self.layout.missing(),
            "turbo_voices": len(self.voices("turbo")),
            "nano_voices": len(self.voices("nano")),
            "catalog_voices": len(self.catalog_entries()),
            "embedding_files": len(self.embedding_voices()),
            "ffmpeg": self.ffmpeg,
            "ffmpeg_version": ffmpeg_version(self.ffmpeg),
            "gpu": "",
            "torch": "",
            "cuda": False,
            "onnxruntime": "",
        }
        try:
            from importlib.metadata import version

            info["vieneu_sdk"] = version("vieneu")
        except Exception:
            info["vieneu_sdk"] = "not installed"
        if deep:
            try:
                import torch

                info["torch"] = torch.__version__
                info["cuda"] = bool(torch.cuda.is_available())
                if info["cuda"]:
                    info["gpu"] = torch.cuda.get_device_name(0)
            except Exception as exc:
                info["torch"] = f"unavailable ({exc})"
            try:
                import onnxruntime as ort

                info["onnxruntime"] = ort.__version__
                info["onnx_providers"] = list(ort.get_available_providers())
            except Exception as exc:
                info["onnxruntime"] = f"unavailable ({exc})"
        return info

    # ── model loading ────────────────────────────────────────────────────────
    def _turbo_preset(self, voice: VoiceInfo) -> Dict[str, Any]:
        preset: Dict[str, Any] = {"speaker_emb": voice.speaker_emb}
        if voice.codes is not None:
            preset["codes"] = voice.codes
        return preset

    def _nano_preset(self, voice: VoiceInfo) -> Dict[str, Any]:
        return {"speaker_emb": voice.speaker_emb, "style": voice.style}

    def _resolve_backend(self, engine: str, backend: str) -> List[str]:
        backend = (backend or "auto").lower()
        if backend not in ("auto", "gpu", "onnx"):
            backend = "auto"
        if engine == "nano":
            return ["onnx"]
        if backend == "gpu":
            return ["gpu"]
        if backend == "onnx":
            return ["onnx"]
        try:
            import torch

            return ["gpu", "onnx"] if torch.cuda.is_available() else ["onnx"]
        except Exception:
            return ["onnx"]

    def load(
        self,
        engine: Optional[str] = None,
        backend: Optional[str] = None,
        message_cb: MessageCb = None,
    ) -> tuple[str, str]:
        """Load (or reuse) the requested engine; returns ``(engine, backend)``."""
        with self._lock:
            engine = engine or str((self.settings.get("tts") or {}).get("engine") or "turbo")
            engine = "nano" if engine.lower() == "nano" else "turbo"
            requested_backend = backend or str((self.settings.get("tts") or {}).get("backend") or "auto")
            candidates = self._resolve_backend(engine, requested_backend)

            if self._tts is not None and self._loaded_key:
                loaded_engine, loaded_backend = self._loaded_key
                if loaded_engine == engine and (requested_backend == "auto" or loaded_backend == requested_backend):
                    return loaded_engine, loaded_backend

            self.close()
            if not self.layout.valid:
                raise TTSError(
                    f"Không tìm thấy thư mục H2MTTS hợp lệ: {self.layout.root}. "
                    "Hãy kiểm tra lại đường dẫn trong Cài đặt."
                )

            last_error: Optional[Exception] = None
            for candidate in candidates:
                try:
                    if message_cb:
                        message_cb(f"Đang nạp model {engine.upper()} ({candidate.upper()})...")
                    started = time.time()
                    if engine == "nano":
                        self._load_nano()
                    elif candidate == "gpu":
                        self._load_turbo_gpu()
                    else:
                        self._load_turbo_onnx()
                    elapsed = time.time() - started
                    self._engine_name = engine
                    self._backend_used = candidate
                    self._loaded_key = (engine, candidate)
                    logger.info("Nạp model %s/%s xong sau %.1fs (sr=%d)", engine, candidate, elapsed, self._sample_rate)
                    if message_cb:
                        message_cb(f"Model {engine.upper()} ({candidate.upper()}) sẵn sàng sau {elapsed:.0f}s")
                    return engine, candidate
                except Exception as exc:
                    last_error = exc
                    logger.error("Nạp model %s/%s lỗi: %s", engine, candidate, exc)
                    logger.debug(traceback.format_exc())
                    self._tts = None
                    self._loaded_key = None
                    if requested_backend != "auto":
                        break
                    if message_cb:
                        message_cb(f"Không nạp được {engine}/{candidate}: {exc} — thử nhánh tiếp theo")
            raise TTSError(f"Không nạp được model {engine}: {last_error}")

    def _load_turbo_gpu(self) -> None:
        if not self.layout.turbo_weights.is_file():
            raise TTSError("Thiếu trọng số Turbo: update/model.safetensors")
        if not self.layout.moss_torch_dir.is_dir():
            raise TTSError("Thiếu MOSS-Audio-Tokenizer-Nano (bản PyTorch).")
        import torch

        if not torch.cuda.is_available():
            raise TTSError("PyTorch không thấy GPU CUDA. Chọn backend CPU/ONNX thay thế.")
        from vieneu import Vieneu

        self._tts = Vieneu(
            mode="v3turbo",
            backbone_repo=str(self.layout.turbo_dir),
            model_subfolder="update",
            moss_tokenizer=str(self.layout.moss_torch_dir),
            device="cuda",
            backend="pytorch",
            max_batch_size=self.BATCH_SIZE,
        )
        self._sample_rate = int(getattr(self._tts, "sample_rate", 48000))

    def _load_turbo_onnx(self) -> None:
        if not (self.layout.turbo_onnx_dir / "config.json").is_file():
            raise TTSError("Thiếu ONNX-Int8 của Turbo: models/H2M-TTS-v3-Turbo/onnx_int8")
        if not self.layout.moss_onnx_dir.is_dir():
            raise TTSError("Thiếu MOSS-Audio-Tokenizer-ONNX.")
        from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine
        from vieneu_utils.core_utils import max_expected_frames
        from vieneu_utils.phonemize_text import phonemize_text_with_emotions

        threads = int((self.settings.get("tts") or {}).get("threads") or 0)
        self._tts = OnnxV3LiteEngine(
            onnx_dir=str(self.layout.turbo_onnx_dir),
            codec_dir=str(self.layout.moss_onnx_dir),
            threads=threads,
        )
        self._torbo_phonemize = phonemize_text_with_emotions
        self._max_expected_frames = max_expected_frames
        self._sample_rate = 48000

    def _load_nano(self) -> None:
        if not (self.layout.nano_dir / "config.json").is_file():
            raise TTSError("Thiếu model Nano: models/H2M-TTS-v3-Nano")
        from vieneu import Vieneu

        threads = int((self.settings.get("tts") or {}).get("threads") or 0)
        self._tts = Vieneu(mode="v3nano", onnx_dir=str(self.layout.nano_dir), threads=threads)
        self._sample_rate = int(getattr(self._tts, "sample_rate", 24000))

    def close(self) -> None:
        with self._lock:
            tts = self._tts
            self._tts = None
            self._loaded_key = None
            self._engine_name = ""
            self._backend_used = ""
            if tts is not None:
                try:
                    close = getattr(tts, "close", None)
                    if callable(close):
                        close()
                except Exception:
                    pass
                del tts
            try:
                import gc

                gc.collect()
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    # ── synthesis ────────────────────────────────────────────────────────────
    @staticmethod
    def _wait(pause_cb: PauseCb, cancel_cb: CancelCb) -> None:
        while pause_cb is not None and pause_cb():
            if cancel_cb is not None and cancel_cb():
                raise SynthesisCancelled("Đã dừng tạo audio.")
            time.sleep(0.15)
        if cancel_cb is not None and cancel_cb():
            raise SynthesisCancelled("Đã dừng tạo audio.")

    def _atempo_chain(self, speed: float) -> str:
        factor = max(0.25, min(4.0, float(speed)))
        parts: List[str] = []
        while factor > 2.0:
            parts.append("atempo=2.0")
            factor /= 2.0
        while factor < 0.5:
            parts.append("atempo=0.5")
            factor /= 0.5
        if abs(factor - 1.0) > 1e-4:
            parts.append(f"atempo={factor:.6f}")
        return ",".join(parts)

    def _encode(self, wav_path: Path, out_path: Path, tempo: float, bitrate: str) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = out_path.suffix.lower()
        args = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav_path)]
        if abs(tempo - 1.0) > 1e-4:
            chain = self._atempo_chain(tempo)
            if chain:
                args += ["-filter:a", chain]
        if suffix == ".wav":
            args += ["-codec:a", "pcm_s16le"]
        else:
            args += ["-codec:a", "libmp3lame", "-b:a", str(bitrate or "192k"), "-ar", "44100"]
        args += ["-vn", str(out_path)]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                creationflags=_CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise TTSError(f"Không tìm thấy ffmpeg ({self.ffmpeg}). Cấu hình lại đường dẫn ffmpeg.") from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSError("ffmpeg mã hoá MP3 quá lâu, đã huỷ.") from exc
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-4:]
            raise TTSError("ffmpeg mã hoá MP3 lỗi: " + " | ".join(tail))

    def synthesize(
        self,
        text: str,
        out_path: str | os.PathLike,
        engine: Optional[str] = None,
        backend: Optional[str] = None,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        progress_cb: ProgressCb = None,
        message_cb: MessageCb = None,
        pause_cb: PauseCb = None,
        cancel_cb: CancelCb = None,
    ) -> SynthResult:
        """Render ``text`` to ``out_path`` (MP3 by default) and return its info."""
        with self._lock:
            tts_settings = self.settings.get("tts") or {}
            engine = engine or str(tts_settings.get("engine") or "turbo")
            engine = "nano" if str(engine).lower() == "nano" else "turbo"
            backend = backend or str(tts_settings.get("backend") or "auto")
            voice_name = voice or tts_settings.get("voice") or ""
            speed_value = float(tts_settings.get("speed", 1.0) if speed is None else speed)
            speed_value = max(0.5, min(2.0, speed_value))
            gap_sentence = float(tts_settings.get("gap_sentence", 0.3))
            gap_paragraph = float(tts_settings.get("gap_paragraph", 0.8))
            max_chars = int(tts_settings.get("max_chars", 350))
            bitrate = str(tts_settings.get("bitrate") or "192k")
            voice_info = self.resolve_voice(engine, voice_name)

            loaded_engine, loaded_backend = self.load(engine, backend, message_cb)
            self._wait(pause_cb, cancel_cb)

            effective_max = 140 if engine == "nano" else max(120, max_chars)
            chunks = chunk_text(
                text,
                min_chars=min(200, effective_max),
                max_chars=effective_max,
                gap_sentence=gap_sentence,
                gap_paragraph=gap_paragraph,
                gap_inner=min(0.15, gap_sentence),
            )
            if not chunks:
                raise TTSError(f"Văn bản rỗng hoặc không có nội dung đọc được: {out_path}")

            total = len(chunks)
            logger.info(
                "TTS %s/%s · giọng %s · %d đoạn · %.1fx · %s",
                loaded_engine, loaded_backend, voice_info.name, total, speed_value, Path(out_path).name,
            )
            if message_cb:
                message_cb(f"Tạo audio {Path(out_path).name}: {total} đoạn, giọng {voice_info.name}")

            wavs: List[np.ndarray] = []
            completed = 0

            def report() -> None:
                if progress_cb:
                    try:
                        progress_cb(int(completed * 100 / max(1, total)))
                    except Exception:
                        pass

            if engine == "turbo" and loaded_backend == "gpu":
                preset = self._turbo_preset(voice_info)
                batch = 4
                for start in range(0, total, batch):
                    self._wait(pause_cb, cancel_cb)
                    group = chunks[start:start + batch]
                    try:
                        results = self._tts.infer_batch(
                            [c.text for c in group],
                            voice=preset,
                            apply_watermark=False,
                            max_chars=effective_max,
                            batch_size=min(self.BATCH_SIZE, len(group)),
                            temperature=0.8,
                            top_k=25,
                            top_p=0.95,
                        )
                    except Exception as exc:
                        raise TTSError(f"Lỗi tổng hợp Turbo GPU: {exc}") from exc
                    if len(results) != len(group):
                        raise TTSError("Số waveform trả về không khớp số đoạn văn bản.")
                    wavs.extend(np.asarray(w, dtype=np.float32).reshape(-1) for w in results)
                    completed += len(group)
                    report()
            elif engine == "turbo":
                preset = self._turbo_preset(voice_info)
                use_ref = preset.get("codes") is not None
                for chunk in chunks:
                    self._wait(pause_cb, cancel_cb)
                    phonemes = self._torbo_phonemize(chunk.text) if self._torbo_phonemize else None
                    cap = 300
                    if self._max_expected_frames and phonemes is not None:
                        cap = min(cap, int(self._max_expected_frames(phonemes)))
                    try:
                        wav = self._tts.infer(
                            phonemes=phonemes,
                            speaker_emb=voice_info.speaker_emb,
                            ref_codes=voice_info.codes,
                            use_ref_codes=use_ref,
                            max_new_frames=cap,
                            temperature=0.8,
                            top_k=25,
                            top_p=0.95,
                        )
                    except Exception as exc:
                        raise TTSError(f"Lỗi tổng hợp Turbo ONNX: {exc}") from exc
                    wavs.append(np.asarray(wav, dtype=np.float32).reshape(-1))
                    completed += 1
                    report()
            else:
                preset = self._nano_preset(voice_info)
                steps = int(tts_settings.get("nano_steps", 16) or 16)
                cfg = float(tts_settings.get("nano_cfg", 3.0) or 3.0)
                for chunk in chunks:
                    self._wait(pause_cb, cancel_cb)
                    try:
                        wav = self._tts.infer(
                            chunk.text,
                            voice=preset,
                            speed=speed_value,
                            steps=steps,
                            cfg=cfg,
                            apply_watermark=False,
                            max_chars=effective_max,
                        )
                    except Exception as exc:
                        raise TTSError(f"Lỗi tổng hợp Nano: {exc}") from exc
                    wavs.append(np.asarray(wav, dtype=np.float32).reshape(-1))
                    completed += 1
                    report()

            if not wavs:
                raise TTSError("Không tạo được waveform nào.")

            sample_rate = self._sample_rate
            pieces: List[np.ndarray] = []
            for index, wav in enumerate(wavs):
                pieces.append(wav)
                gap = chunks[index].gap_after if index < len(chunks) else 0.0
                if gap > 0:
                    pieces.append(np.zeros(max(1, int(round(gap * sample_rate))), dtype=np.float32))
            audio = np.concatenate(pieces).astype(np.float32, copy=False)
            if audio.size < sample_rate // 10:
                raise TTSError(f"Audio tạo ra quá ngắn ({audio.size} mẫu).")

            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix="autoyout_tts_", suffix=".wav", dir=str(out_path.parent))
            os.close(fd)
            temp_path = Path(temp_name)
            part_path = out_path.with_name(f"{out_path.stem}.part{out_path.suffix}")
            try:
                try:
                    import soundfile as sf

                    sf.write(str(temp_path), np.clip(audio, -1.0, 1.0), sample_rate)
                except Exception as exc:
                    raise TTSError(f"Không ghi được WAV tạm: {exc}") from exc
                self._wait(pause_cb, cancel_cb)
                tempo = speed_value if engine == "turbo" else 1.0
                self._encode(temp_path, part_path, tempo=tempo, bitrate=bitrate)
                if not part_path.is_file() or part_path.stat().st_size < 512:
                    raise TTSError("File âm thanh xuất ra không hợp lệ.")
                os.replace(part_path, out_path)
            finally:
                for leftover in (temp_path, part_path):
                    try:
                        leftover.unlink(missing_ok=True)
                    except OSError:
                        pass

            if not out_path.is_file() or out_path.stat().st_size < 512:
                raise TTSError(f"File MP3 không hợp lệ: {out_path}")
            duration = probe_duration(out_path, self.ffprobe)
            if duration <= 0:
                duration = len(audio) / float(sample_rate) / max(1e-3, tempo if engine == "turbo" else 1.0)
            if progress_cb:
                try:
                    progress_cb(100)
                except Exception:
                    pass
            logger.info("Đã tạo %s (%.2fs)", out_path.name, duration)
            return SynthResult(
                path=out_path,
                duration=duration,
                sample_rate=sample_rate,
                engine=loaded_engine,
                backend=loaded_backend,
                voice=voice_info.name,
                chunks=total,
                characters=len(normalize_text(text)),
            )

    def synthesize_variants(
        self,
        text: str,
        out_path: str | os.PathLike,
        variants: int = 2,
        engine: Optional[str] = None,
        backend: Optional[str] = None,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        progress_cb: ProgressCb = None,
        message_cb: MessageCb = None,
        pause_cb: PauseCb = None,
        cancel_cb: CancelCb = None,
    ) -> List[SynthResult]:
        """Render ``text`` into ``variants`` audio files (``<stem>-1.mp3`` …) and return their info."""
        variants = max(1, int(variants))
        paths = audio_variant_paths(out_path, variants)
        results: List[SynthResult] = []
        for index, path in enumerate(paths, start=1):
            self._wait(pause_cb, cancel_cb)
            if message_cb:
                message_cb(f"Tạo audio {path.name} (bản {index}/{variants})…")
            logger.info("TTS biến thể %d/%d · %s", index, variants, path.name)
            results.append(
                self.synthesize(
                    text,
                    path,
                    engine=engine,
                    backend=backend,
                    voice=voice,
                    speed=speed,
                    progress_cb=progress_cb,
                    message_cb=message_cb,
                    pause_cb=pause_cb,
                    cancel_cb=cancel_cb,
                )
            )
        return results

    def preview(
        self,
        text: Optional[str] = None,
        voice: Optional[str] = None,
        engine: Optional[str] = None,
        backend: Optional[str] = None,
        speed: Optional[float] = None,
        message_cb: MessageCb = None,
        progress_cb: ProgressCb = None,
    ) -> SynthResult:
        tts_settings = self.settings.get("tts") or {}
        sample = (text or tts_settings.get("preview_text") or "Xin chào, đây là bản nghe thử.").strip()
        if len(sample) > 600:
            sample = sample[:600]
        out_dir = Path(tempfile.gettempdir()) / "AutoYout" / "preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S_%f")
        safe_voice = re.sub(r"[^\w\-]+", "_", voice or tts_settings.get("voice") or "voice")
        out_path = out_dir / f"preview_{safe_voice}_{stamp}.mp3"
        return self.synthesize(
            sample,
            out_path,
            engine=engine,
            backend=backend,
            voice=voice,
            speed=speed,
            progress_cb=progress_cb,
            message_cb=message_cb,
        )
