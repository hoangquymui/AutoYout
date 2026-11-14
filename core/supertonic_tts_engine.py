"""Supertonic 3 ONNX TTS Engine (CPU, 44.1 kHz, multilingual across 31 languages, 10 voices F1..F5 / M1..M5)."""
from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.cit_tts_common import (
    CancelCb,
    MessageCb,
    PauseCb,
    ProgressCb,
    finalize_waveforms,
    find_model_root,
    wait_pause_cancel,
)
from core.languages import DEFAULT_LANGUAGE, language_name, normalize_language
from core.settings import default_supertonic_path, normalize_supertonic_path
from core.text_processor import chunk_text
from core.tts_engine import SynthResult, TTSError
from utils.logger import get_logger
from utils.media_probe import resolve_tool

logger = get_logger("tts.supertonic")

SUPERTONIC_REPO = "supertone-oss-archive/supertonic-3"
SUPERTONIC_REV = "aafc6e32416a594460b32413efc49d7fe4ce6d46"

SUPERTONIC_VOICES: Tuple[Tuple[str, str, str], ...] = (
    ("F1", "Female", "F1 · Nữ (ấm áp, tự nhiên)"),
    ("F2", "Female", "F2 · Nữ (trong sáng, rõ nét)"),
    ("F3", "Female", "F3 · Nữ (trẻ trung, năng động)"),
    ("F4", "Female", "F4 · Nữ (trầm ấm, kể chuyện)"),
    ("F5", "Female", "F5 · Nữ (nhẹ nhàng, truyền cảm)"),
    ("M1", "Male", "M1 · Nam (trầm ổn, phóng sự)"),
    ("M2", "Male", "M2 · Nam (rõ ràng, hiện đại)"),
    ("M3", "Male", "M3 · Nam (năng động, dứt khoát)"),
    ("M4", "Male", "M4 · Nam (sâu lắng, đọc sách)"),
    ("M5", "Male", "M5 · Nam (tự nhiên, thân thiện)"),
)
SUPERTONIC_VOICE_IDS: Tuple[str, ...] = tuple(v[0] for v in SUPERTONIC_VOICES)
SUPERTONIC_DEFAULT_VOICE = "F1"

SUPERTONIC_LANGUAGES: Tuple[str, ...] = (
    "vi", "en", "ja", "ko", "fr", "de", "es", "pt",
    "it", "ru", "id", "hi", "ar", "tr", "nl", "pl",
)

SUPERTONIC_REQUIRED_FILES: Tuple[str, ...] = (
    "onnx/tts.json",
    "onnx/unicode_indexer.json",
    "onnx/duration_predictor.onnx",
    "onnx/text_encoder.onnx",
    "onnx/vector_estimator.onnx",
    "onnx/vocoder.onnx",
    "voice_styles/F1.json",
)

SUPERTONIC_ALL_FILES: Tuple[str, ...] = SUPERTONIC_REQUIRED_FILES + tuple(
    f"voice_styles/{vid}.json" for vid in SUPERTONIC_VOICE_IDS if vid != "F1"
)

_RE_PUNCT_END = re.compile(r"[.!?\u3002\uff01\uff1f\u2026:;,\"'\)\]]\s*$")


def _preprocess_supertonic_text(text: str, lang: str, indexer: List[int]) -> np.ndarray:
    """NFKD-normalize text, wrap with Supertonic language tag <lang>...</lang>, and convert to unicode token IDs."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = (
        t.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u00a0", " ")
    )
    t = re.sub(r"\s+", " ", t).strip()
    if t and not _RE_PUNCT_END.search(t):
        t = t + "."

    # Filter characters inside body using indexer
    max_idx = len(indexer)
    body_chars = [ch for ch in t if 0 <= ord(ch) < max_idx and int(indexer[ord(ch)]) >= 0]
    body = "".join(body_chars).strip()
    if not body:
        body = "."

    lang_tag = normalize_language(lang)
    if lang_tag not in SUPERTONIC_LANGUAGES:
        lang_tag = "en"
    tagged = f"<{lang_tag}>{body}</{lang_tag}>"
    ids = [int(indexer[ord(ch)]) for ch in tagged if 0 <= ord(ch) < max_idx and int(indexer[ord(ch)]) >= 0]
    if not ids:
        ids = [0]
    return np.asarray([ids], dtype=np.int64)


class SupertonicTTSEngine:
    """Supertonic 3 multilingual CPU ONNX TTS engine matching AutoYout's ``SynthResult`` contract."""

    def __init__(self, settings: Dict[str, Any]) -> None:
        self.settings = settings
        self.paths_cfg = settings.get("paths") or {}
        self.tts_cfg = settings.get("tts") or {}
        self.ffmpeg = resolve_tool(self.paths_cfg.get("ffmpeg"), "ffmpeg")
        self.ffprobe = resolve_tool(self.paths_cfg.get("ffprobe"), "ffprobe")

        self._lock = threading.RLock()
        self._model_root: Optional[Path] = None
        self._sample_rate: int = 44100
        self._chunk_size: int = 3072
        self._latent_dim: int = 144
        self._indexer: List[int] = []
        self._dp_sess = None
        self._te_sess = None
        self._ve_sess = None
        self._voc_sess = None
        self._styles_cache: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}

    def resolve_root(self) -> Optional[Path]:
        raw_cfg = str(self.paths_cfg.get("supertonic") or "").strip()
        norm_cfg = normalize_supertonic_path(raw_cfg)
        extra = [raw_cfg if raw_cfg else None, norm_cfg, default_supertonic_path()]
        return find_model_root(
            repo_id=SUPERTONIC_REPO,
            required_relpaths=SUPERTONIC_REQUIRED_FILES,
            preferred_rev=SUPERTONIC_REV,
            extra_dirs=extra,
        )

    @property
    def root(self) -> Path:
        found = self.resolve_root()
        return found if found is not None else Path(normalize_supertonic_path(self.paths_cfg.get("supertonic")))

    def is_installed(self) -> bool:
        return self.resolve_root() is not None

    def check_ready(self, language: str = "") -> Tuple[bool, str]:
        if language:
            lang = normalize_language(language)
            if lang not in SUPERTONIC_LANGUAGES:
                return False, f"Supertonic 3 không hỗ trợ ngôn ngữ '{language_name(lang)} ({lang})'."
        try:
            import onnxruntime  # noqa: F401
        except Exception as exc:
            return False, f"Thiếu thư viện onnxruntime cho Supertonic 3: {exc}"
        found = self.resolve_root()
        if found is None:
            return (
                False,
                "Chưa có model Supertonic 3 (Vào Cài đặt → API → Giọng đọc → Supertonic 3 để tải).",
            )
        return True, f"Supertonic 3 ONNX sẵn sàng ({found})"

    def available_voices(self, language: str = "", gender: str = "") -> List[Tuple[str, str]]:
        """Return ``(voice_id, display_label)`` pairs filtered by optional gender."""
        g_norm = str(gender or "").strip().lower()
        found = self.resolve_root()
        out: List[Tuple[str, str]] = []
        for vid, vgender, vdesc in SUPERTONIC_VOICES:
            if g_norm in ("female", "nu", "nữ", "f") and vgender.lower() != "female":
                continue
            if g_norm in ("male", "nam", "m") and vgender.lower() != "male":
                continue
            if found is not None and not (found / "voice_styles" / f"{vid}.json").is_file():
                continue
            out.append((vid, vdesc))
        if not out and found is not None:
            # Fallback list even if filtered tightly
            return [(vid, vdesc) for vid, _, vdesc in SUPERTONIC_VOICES if (found / "voice_styles" / f"{vid}.json").is_file()]
        return out

    def _ensure_loaded(self) -> Path:
        with self._lock:
            root = self.resolve_root()
            if root is None:
                raise TTSError(
                    "Chưa tìm thấy model Supertonic 3 (supertone-oss-archive/supertonic-3). "
                    "Hãy vào Cài đặt → API → Giọng đọc → Supertonic 3 để tải."
                )
            if self._model_root == root and self._dp_sess is not None:
                return root

            try:
                import onnxruntime as ort
            except Exception as exc:
                raise TTSError(f"Thiếu onnxruntime để chạy Supertonic 3: {exc}") from exc

            tts_cfg = json.loads((root / "onnx" / "tts.json").read_text(encoding="utf-8"))
            ae = tts_cfg.get("ae") or {}
            ttl = tts_cfg.get("ttl") or {}
            self._sample_rate = int(ae.get("sample_rate") or 44100)
            base_chunk = int(ae.get("base_chunk_size") or 512)
            compress = int(ttl.get("chunk_compress_factor") or 6)
            ldim = int(ae.get("ldim") or ttl.get("latent_dim") or 24)
            self._chunk_size = base_chunk * compress
            self._latent_dim = ldim * compress

            indexer_raw = json.loads((root / "onnx" / "unicode_indexer.json").read_text(encoding="utf-8"))
            self._indexer = [int(x) for x in indexer_raw]

            opts = ort.SessionOptions()
            threads = int(self.tts_cfg.get("threads") or 0)
            if threads > 0:
                opts.intra_op_num_threads = threads
            providers = ["CPUExecutionProvider"]

            onnx_dir = root / "onnx"
            self._dp_sess = ort.InferenceSession(str(onnx_dir / "duration_predictor.onnx"), opts, providers=providers)
            self._te_sess = ort.InferenceSession(str(onnx_dir / "text_encoder.onnx"), opts, providers=providers)
            self._ve_sess = ort.InferenceSession(str(onnx_dir / "vector_estimator.onnx"), opts, providers=providers)
            self._voc_sess = ort.InferenceSession(str(onnx_dir / "vocoder.onnx"), opts, providers=providers)
            self._styles_cache.clear()
            self._model_root = root
            logger.info("Đã nạp Supertonic 3 ONNX từ %s (%d Hz)", root, self._sample_rate)
            return root

    def _load_style(self, root: Path, voice_id: str) -> Tuple[str, np.ndarray, np.ndarray]:
        vid = str(voice_id or "").strip().upper()
        if vid not in SUPERTONIC_VOICE_IDS:
            vid = SUPERTONIC_DEFAULT_VOICE
        style_path = root / "voice_styles" / f"{vid}.json"
        if not style_path.is_file():
            style_path = root / "voice_styles" / f"{SUPERTONIC_DEFAULT_VOICE}.json"
            vid = SUPERTONIC_DEFAULT_VOICE
        if not style_path.is_file():
            raise TTSError(f"Không tìm thấy voice style Supertonic: {style_path}")

        cached = self._styles_cache.get(vid)
        if cached is not None:
            return vid, cached[0], cached[1]

        data = json.loads(style_path.read_text(encoding="utf-8"))
        ttl_obj = data["style_ttl"]
        dp_obj = data["style_dp"]
        ttl_dims = tuple(int(x) for x in ttl_obj["dims"])
        dp_dims = tuple(int(x) for x in dp_obj["dims"])
        style_ttl = np.asarray(ttl_obj["data"], dtype=np.float32).reshape(ttl_dims)
        style_dp = np.asarray(dp_obj["data"], dtype=np.float32).reshape(dp_dims)
        self._styles_cache[vid] = (style_ttl, style_dp)
        return vid, style_ttl, style_dp

    def _synth_chunk(
        self,
        text: str,
        lang: str,
        style_ttl: np.ndarray,
        style_dp: np.ndarray,
        speed: float,
        steps: int,
    ) -> np.ndarray:
        text_ids = _preprocess_supertonic_text(text, lang, self._indexer)
        t_len = text_ids.shape[1]
        text_mask = np.ones((1, 1, t_len), dtype=np.float32)

        dur_out = self._dp_sess.run(
            None,
            {"text_ids": text_ids, "style_dp": style_dp, "text_mask": text_mask},
        )[0]
        dur_sec = max(0.12, float(np.asarray(dur_out).reshape(-1)[0]) / max(0.25, speed))
        wav_len = max(self._chunk_size, int(math.ceil(dur_sec * self._sample_rate)))
        latent_len = max(1, int(math.ceil(wav_len / float(self._chunk_size))))

        text_emb = self._te_sess.run(
            None,
            {"text_ids": text_ids, "style_ttl": style_ttl, "text_mask": text_mask},
        )[0]

        rng = np.random.default_rng()
        latent_mask = np.ones((1, 1, latent_len), dtype=np.float32)
        noisy_latent = rng.normal(0.0, 1.0, size=(1, self._latent_dim, latent_len)).astype(np.float32) * latent_mask
        total_step = np.asarray([float(steps)], dtype=np.float32)

        for step in range(steps):
            current_step = np.asarray([float(step)], dtype=np.float32)
            noisy_latent = self._ve_sess.run(
                None,
                {
                    "noisy_latent": noisy_latent,
                    "text_emb": text_emb,
                    "style_ttl": style_ttl,
                    "latent_mask": latent_mask,
                    "text_mask": text_mask,
                    "current_step": current_step,
                    "total_step": total_step,
                },
            )[0]

        wav = self._voc_sess.run(None, {"latent": noisy_latent})[0]
        arr = np.asarray(wav, dtype=np.float32).reshape(-1)
        if arr.size > wav_len:
            arr = arr[:wav_len]
        return arr

    def synthesize(
        self,
        text: str,
        out_path: str | Any,
        language: str = DEFAULT_LANGUAGE,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        progress_cb: ProgressCb = None,
        message_cb: MessageCb = None,
        pause_cb: PauseCb = None,
        cancel_cb: CancelCb = None,
    ) -> SynthResult:
        wait_pause_cancel(pause_cb, cancel_cb)
        lang = normalize_language(language or self.tts_cfg.get("language") or DEFAULT_LANGUAGE)
        if lang not in SUPERTONIC_LANGUAGES:
            raise TTSError(f"Supertonic 3 chưa hỗ trợ ngôn ngữ '{language_name(lang)} ({lang})'.")

        root = self._ensure_loaded()
        speed_val = max(0.5, min(2.0, float(speed if speed is not None else self.tts_cfg.get("speed") or 1.0)))
        steps = max(2, min(16, int(self.tts_cfg.get("supertonic_steps") or 8)))
        gap_sentence = float(self.tts_cfg.get("gap_sentence") or 0.3)
        gap_paragraph = float(self.tts_cfg.get("gap_paragraph") or 0.8)
        max_chars = max(120, int(self.tts_cfg.get("max_chars") or 300))
        bitrate = str(self.tts_cfg.get("bitrate") or "192k")

        raw_voice = voice or str((self.tts_cfg.get("supertonic_voices") or {}).get(lang) or SUPERTONIC_DEFAULT_VOICE)
        vid, style_ttl, style_dp = self._load_style(root, raw_voice)

        chunks = chunk_text(
            text,
            max_chars=max_chars,
            gap_sentence=gap_sentence,
            gap_paragraph=gap_paragraph,
        )
        if not chunks:
            raise TTSError("Văn bản trống, không có gì để đọc.")

        total = len(chunks)
        if message_cb:
            message_cb(f"Supertonic 3 ({vid} · {lang}) · {total} đoạn…")

        wavs: List[np.ndarray] = []
        for idx, ch in enumerate(chunks, 1):
            wait_pause_cancel(pause_cb, cancel_cb)
            wavs.append(self._synth_chunk(ch.text, lang, style_ttl, style_dp, speed=speed_val, steps=steps))
            if progress_cb:
                try:
                    progress_cb(min(95, int(idx * 95 / total)))
                except Exception:
                    pass

        out_file, duration, cues_list = finalize_waveforms(
            chunks=chunks,
            wavs=wavs,
            sample_rate=self._sample_rate,
            out_path=Path(out_path),
            ffmpeg=self.ffmpeg,
            ffprobe=self.ffprobe,
            bitrate=bitrate,
            tempo=1.0,
            logger=logger,
            progress_cb=progress_cb,
            pause_cb=pause_cb,
            cancel_cb=cancel_cb,
        )
        return SynthResult(
            path=out_file,
            duration=duration,
            sample_rate=self._sample_rate,
            engine="supertonic",
            backend="cpu",
            voice=vid,
            chunks=total,
            characters=sum(len(c.text) for c in chunks),
            cues=cues_list,
        )

    def close(self) -> None:
        with self._lock:
            self._dp_sess = None
            self._te_sess = None
            self._ve_sess = None
            self._voc_sess = None
            self._styles_cache.clear()
            self._model_root = None
