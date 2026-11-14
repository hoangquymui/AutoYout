"""Kokoro-82M TTS Engine (24 kHz):
- CPU via ONNX Runtime (`onnx-community/Kokoro-82M-v1.0-ONNX` + `kokoro_onnx`)
- CUDA GPU via PyTorch (`hexgrad/Kokoro-82M` + `core.kokoro_torch`) when `backend` is ``auto``/``gpu`` and CUDA is available
- Native support for English (US/GB), French, Spanish, Portuguese, Italian, Hindi, Japanese (`misaki.ja`), Chinese (`misaki.zh`).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import types
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
from core.settings import default_kokoro_path, normalize_kokoro_path
from core.text_processor import chunk_text
from core.tts_engine import SynthResult, TTSError
from utils.logger import get_logger
from utils.media_probe import resolve_tool

logger = get_logger("tts.kokoro")

KOKORO_ONNX_REPO = "hoangmui410/Kokoro-82M-ONNX"
KOKORO_ONNX_REV = "main"
KOKORO_ONNX_ALIAS_REPOS: Tuple[str, ...] = ("onnx-community/Kokoro-82M-v1.0-ONNX",)
KOKORO_TORCH_REPO = "hoangmui410/Kokoro-82M-Torch"
KOKORO_TORCH_REV = "main"
KOKORO_TORCH_ALIAS_REPOS: Tuple[str, ...] = ("hexgrad/Kokoro-82M",)

KOKORO_REQUIRED_ONNX_FILES: Tuple[str, ...] = ("onnx/model.onnx",)
KOKORO_REQUIRED_TORCH_FILES: Tuple[str, ...] = ("config.json", "kokoro-v1_0.pth")

# Mapping from AutoYout normalized language -> Kokoro voice prefix(es) & kokoro_onnx lang tag
_LANG_TO_PREFIXES: Dict[str, Tuple[str, ...]] = {
    "en": ("af", "am", "bf", "bm"),
    "es": ("ef", "em"),
    "fr": ("ff",),
    "hi": ("hf", "hm"),
    "it": ("if", "im"),
    "pt": ("pf", "pm"),
    "ja": ("jf", "jm"),
    "zh": ("zf", "zm"),
}

_PREFIX_TO_ESPEAK_LANG: Dict[str, str] = {
    "a": "en-us",
    "b": "en-gb",
    "e": "es",
    "f": "fr-fr",
    "h": "hi",
    "i": "it",
    "p": "pt-br",
    "j": "ja",
    "z": "zh",
}

_PREFIX_DISPLAY: Dict[str, str] = {
    "af": "EN-US · Nữ",
    "am": "EN-US · Nam",
    "bf": "EN-UK · Nữ",
    "bm": "EN-UK · Nam",
    "ef": "ES · Nữ",
    "em": "ES · Nam",
    "ff": "FR · Nữ",
    "hf": "HI · Nữ",
    "hm": "HI · Nam",
    "if": "IT · Nữ",
    "im": "IT · Nam",
    "pf": "PT · Nữ",
    "pm": "PT · Nam",
    "jf": "JA · Nữ",
    "jm": "JA · Nam",
    "zf": "ZH · Nữ",
    "zm": "ZH · Nam",
}

KOKORO_LANGUAGES: Tuple[str, ...] = tuple(_LANG_TO_PREFIXES.keys())

KOKORO_DEFAULT_VOICES: Dict[str, str] = {
    "en": "af_heart",
    "fr": "ff_siwis",
    "es": "ef_dora",
    "pt": "pf_dora",
    "it": "if_sara",
    "hi": "hf_alpha",
    "ja": "jf_alpha",
    "zh": "zf_xiaobei",
}

# Known optional Japanese & Chinese voice files in onnx-community/Kokoro-82M-v1.0-ONNX
KOKORO_JA_VOICES: Tuple[str, ...] = (
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
)
KOKORO_ZH_VOICES: Tuple[str, ...] = (
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
)

KOKORO_SAMPLE_RATE = 24000


def _voice_gender(voice_id: str) -> str:
    prefix = voice_id[:2].lower() if len(voice_id) >= 2 else ""
    if prefix.endswith("f"):
        return "female"
    if prefix.endswith("m"):
        return "male"
    return ""


def _voice_lang(voice_id: str) -> str:
    first = voice_id[:1].lower()
    for lang, prefixes in _LANG_TO_PREFIXES.items():
        if any(p.startswith(first) for p in prefixes):
            return lang
    return "en"


def _ensure_pyopenjtalk_importable() -> None:
    """Stub pyopenjtalk if missing so misaki.ja can import and use fugashi+unidic_lite cleanly."""
    if "pyopenjtalk" not in sys.modules:
        try:
            import pyopenjtalk  # noqa: F401
        except Exception:
            sys.modules["pyopenjtalk"] = types.ModuleType("pyopenjtalk")


class KokoroTTSEngine:
    """Kokoro-82M multilingual TTS engine supporting ONNX (CPU) and PyTorch (CUDA GPU)."""

    def __init__(self, settings: Dict[str, Any]) -> None:
        self.settings = settings
        self.paths_cfg = settings.get("paths") or {}
        self.tts_cfg = settings.get("tts") or {}
        self.ffmpeg = resolve_tool(self.paths_cfg.get("ffmpeg"), "ffmpeg")
        self.ffprobe = resolve_tool(self.paths_cfg.get("ffprobe"), "ffprobe")

        self._lock = threading.RLock()
        self._onnx_root: Optional[Path] = None
        self._torch_root: Optional[Path] = None
        self._kokoro_onnx = None
        self._torch_model = None
        self._torch_vocab: Dict[str, int] = {}
        self._ja_g2p = None
        self._zh_g2p = None
        self._loaded_backend: str = ""

    def resolve_onnx_root(self) -> Optional[Path]:
        raw_cfg = str(self.paths_cfg.get("kokoro") or "").strip()
        norm_cfg = normalize_kokoro_path(raw_cfg)
        extra = [raw_cfg if raw_cfg else None, norm_cfg, default_kokoro_path()]
        return find_model_root(
            repo_id=KOKORO_ONNX_REPO,
            required_relpaths=KOKORO_REQUIRED_ONNX_FILES,
            preferred_rev=KOKORO_ONNX_REV,
            extra_dirs=extra,
            alias_repo_ids=KOKORO_ONNX_ALIAS_REPOS,
        )

    def resolve_torch_root(self) -> Optional[Path]:
        raw_cfg = str(self.paths_cfg.get("kokoro") or "").strip()
        norm_cfg = normalize_kokoro_path(raw_cfg)
        extra = [raw_cfg if raw_cfg else None, norm_cfg, default_kokoro_path()]
        return find_model_root(
            repo_id=KOKORO_TORCH_REPO,
            required_relpaths=KOKORO_REQUIRED_TORCH_FILES,
            preferred_rev=KOKORO_TORCH_REV,
            extra_dirs=extra,
            alias_repo_ids=KOKORO_TORCH_ALIAS_REPOS,
        )

    @property
    def root(self) -> Path:
        found = self.resolve_onnx_root() or self.resolve_torch_root()
        return found if found is not None else Path(normalize_kokoro_path(self.paths_cfg.get("kokoro")))

    def _voice_bin_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        onnx_root = self.resolve_onnx_root()
        if onnx_root is not None and (onnx_root / "voices").is_dir():
            dirs.append(onnx_root / "voices")
        # Also scan managed folder in case JA/ZH extra voices were downloaded into KokoroTTS/voices
        managed = Path(normalize_kokoro_path(self.paths_cfg.get("kokoro"))) / "voices"
        if managed.is_dir() and managed not in dirs:
            dirs.append(managed)
        return dirs

    def _collect_voice_bins(self) -> Dict[str, Path]:
        bins: Dict[str, Path] = {}
        for vdir in self._voice_bin_dirs():
            for p in sorted(vdir.glob("*.bin")):
                bins.setdefault(p.stem, p)
        return bins

    def is_installed(self) -> bool:
        return self.resolve_onnx_root() is not None and bool(self._collect_voice_bins())

    def check_ready(self, language: str = "") -> Tuple[bool, str]:
        if language:
            lang = normalize_language(language)
            if lang not in KOKORO_LANGUAGES:
                return False, f"Kokoro-82M chưa hỗ trợ ngôn ngữ '{language_name(lang)} ({lang})'."
        try:
            import kokoro_onnx  # noqa: F401
        except Exception as exc:
            return False, f"Thiếu thư viện kokoro-onnx cho Kokoro-82M: {exc}"
        onnx_root = self.resolve_onnx_root()
        if onnx_root is None:
            return (
                False,
                "Chưa có model Kokoro-82M ONNX (Vào Cài đặt → API → Giọng đọc → Kokoro-82M để tải).",
            )
        bins = self._collect_voice_bins()
        if not bins:
            return False, f"Không tìm thấy file giọng .bin trong {onnx_root / 'voices'}."
        if language:
            lang = normalize_language(language)
            if not self.available_voices(lang):
                return (
                    False,
                    f"Kokoro-82M chưa tải pack giọng cho '{language_name(lang)} ({lang})'. "
                    "Hãy vào Cài đặt → API → Giọng đọc → Kokoro-82M để tải pack tương ứng.",
                )
        return True, f"Kokoro-82M sẵn sàng ({len(bins)} giọng, {onnx_root})"

    def available_voices(self, language: str = "", gender: str = "") -> List[Tuple[str, str]]:
        """Return ``(voice_id, display_label)`` filtered by optional language and gender."""
        bins = self._collect_voice_bins()
        if not bins:
            return []
        lang_norm = normalize_language(language) if str(language or "").strip() else ""
        if lang_norm and lang_norm not in _LANG_TO_PREFIXES:
            return []
        prefixes = _LANG_TO_PREFIXES.get(lang_norm) if lang_norm else None
        g_norm = str(gender or "").strip().lower()

        out: List[Tuple[str, str]] = []
        for vid in sorted(bins.keys()):
            pref = vid[:2].lower()
            if prefixes is not None and pref not in prefixes:
                continue
            v_gender = _voice_gender(vid)
            if g_norm in ("female", "nu", "nữ", "f") and v_gender != "female":
                continue
            if g_norm in ("male", "nam", "m") and v_gender != "male":
                continue
            tag = _PREFIX_DISPLAY.get(pref, pref.upper())
            pretty_name = vid.split("_", 1)[1].capitalize() if "_" in vid else vid
            out.append((vid, f"{pretty_name} [{tag}] ({vid})"))
        return out

    def _build_voices_npz(self, bins: Dict[str, Path]) -> Path:
        h = hashlib.sha256()
        items = sorted(bins.items())
        for name, p in items:
            try:
                st = p.stat()
                h.update(f"{name}:{st.st_size}:{st.st_mtime_ns};".encode("utf-8"))
            except OSError:
                h.update(f"{name}:{p};".encode("utf-8"))
        cache_dir = Path(tempfile.gettempdir()) / "autoyout_kokoro_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        npz_path = cache_dir / f"kokoro_voices_{h.hexdigest()[:16]}.npz"
        if npz_path.is_file() and npz_path.stat().st_size > 1024:
            return npz_path

        payload: Dict[str, np.ndarray] = {}
        for name, p in items:
            arr = np.fromfile(str(p), dtype=np.float32)
            if arr.size >= 256 and arr.size % 256 == 0:
                payload[name] = arr.reshape(-1, 1, 256)
        if not payload:
            raise TTSError("Không đọc được file giọng .bin hợp lệ nào cho Kokoro-82M.")

        tmp = npz_path.with_name(f"{npz_path.stem}.{os.getpid()}.tmp.npz")
        try:
            np.savez(str(tmp), **payload)
            os.replace(tmp, npz_path)
        finally:
            if tmp.is_file():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return npz_path

    def _want_gpu(self) -> bool:
        backend = str(self.tts_cfg.get("backend") or "auto").strip().lower()
        if backend in ("cpu", "onnx"):
            return False
        if self.resolve_torch_root() is None:
            return False
        try:
            import torch

            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _ensure_onnx_loaded(self) -> Any:
        root = self.resolve_onnx_root()
        if root is None:
            raise TTSError(
                "Chưa tìm thấy model Kokoro-82M ONNX (onnx-community/Kokoro-82M-v1.0-ONNX). "
                "Hãy vào Cài đặt → API → Giọng đọc → Kokoro-82M để tải."
            )
        if self._kokoro_onnx is not None and self._onnx_root == root:
            return self._kokoro_onnx

        import kokoro_onnx
        import onnxruntime as ort

        bins = self._collect_voice_bins()
        if not bins:
            raise TTSError(f"Chưa có file giọng .bin trong {root / 'voices'}.")
        npz_path = self._build_voices_npz(bins)

        opts = ort.SessionOptions()
        threads = int(self.tts_cfg.get("threads") or 0)
        if threads > 0:
            opts.intra_op_num_threads = threads
        sess = ort.InferenceSession(str(root / "onnx" / "model.onnx"), opts, providers=["CPUExecutionProvider"])
        self._kokoro_onnx = kokoro_onnx.Kokoro.from_session(sess, str(npz_path))
        self._onnx_root = root
        self._loaded_backend = "cpu"
        logger.info("Đã nạp Kokoro-82M ONNX (%d giọng) từ %s", len(bins), root)
        return self._kokoro_onnx

    def _ensure_torch_loaded(self) -> Tuple[Any, Dict[str, int]]:
        root = self.resolve_torch_root()
        if root is None:
            raise TTSError("Chưa có model Kokoro-82M PyTorch GPU (hexgrad/Kokoro-82M).")
        if self._torch_model is not None and self._torch_root == root:
            return self._torch_model, self._torch_vocab

        import torch
        from core.kokoro_torch.model import KModel

        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        vocab = {str(k): int(v) for k, v in (cfg.get("vocab") or {}).items()}
        model = KModel(config=cfg, model=str(root / "kokoro-v1_0.pth")).to("cuda").eval()
        self._torch_model = model
        self._torch_vocab = vocab
        self._torch_root = root
        self._loaded_backend = "gpu"
        logger.info("Đã nạp Kokoro-82M PyTorch CUDA từ %s", root)
        return model, vocab

    def _phonemize_ja_zh(self, text: str, lang: str) -> str:
        if lang == "ja":
            if self._ja_g2p is None:
                _ensure_pyopenjtalk_importable()
                from misaki.ja import JAG2P

                self._ja_g2p = JAG2P()
            res = self._ja_g2p(text)
            return res[0] if isinstance(res, tuple) else str(res)
        if lang == "zh":
            if self._zh_g2p is None:
                from misaki.zh import ZHG2P

                self._zh_g2p = ZHG2P()
            res = self._zh_g2p(text)
            return res[0] if isinstance(res, tuple) else str(res)
        return text

    def _phonemize_for_torch(self, text: str, kokoro_lang: str, norm_lang: str, kokoro_inst: Any) -> str:
        if norm_lang in ("ja", "zh"):
            return self._phonemize_ja_zh(text, norm_lang)
        return kokoro_inst.tokenizer.phonemize(text, lang=kokoro_lang)

    def _synth_chunk_onnx(self, kokoro_inst: Any, chunk_text_str: str, voice_id: str, lang: str, speed: float) -> np.ndarray:
        espeak_lang = _PREFIX_TO_ESPEAK_LANG.get(voice_id[:1].lower(), _PREFIX_TO_ESPEAK_LANG.get(lang[:1], "en-us"))
        if lang in ("ja", "zh"):
            phonemes = self._phonemize_ja_zh(chunk_text_str, lang)
            audio, _ = kokoro_inst.create(phonemes, voice=voice_id, speed=speed, lang=espeak_lang, is_phonemes=True)
        else:
            audio, _ = kokoro_inst.create(chunk_text_str, voice=voice_id, speed=speed, lang=espeak_lang)
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    def _synth_chunk_torch(
        self,
        model: Any,
        vocab: Dict[str, int],
        kokoro_inst: Any,
        bins: Dict[str, Path],
        chunk_text_str: str,
        voice_id: str,
        lang: str,
        speed: float,
    ) -> np.ndarray:
        import torch

        espeak_lang = _PREFIX_TO_ESPEAK_LANG.get(voice_id[:1].lower(), "en-us")
        phonemes = self._phonemize_for_torch(chunk_text_str, espeak_lang, lang, kokoro_inst)
        ids = [vocab[c] for c in phonemes if c in vocab]
        if not ids:
            return self._synth_chunk_onnx(kokoro_inst, chunk_text_str, voice_id, lang, speed)
        # Split cleanly if over 500 phonemes
        pack = np.fromfile(str(bins[voice_id]), dtype=np.float32).reshape(-1, 1, 256)
        parts: List[np.ndarray] = []
        step = 480
        for i in range(0, len(ids), step):
            sub_ids = ids[i : i + step]
            idx = min(len(sub_ids), pack.shape[0] - 1)
            ref_s = torch.from_numpy(pack[idx]).to("cuda")
            inp = torch.LongTensor([[0, *sub_ids, 0]]).to("cuda")
            audio_t, _ = model.forward_with_tokens(inp, ref_s, speed=speed)
            parts.append(audio_t.detach().cpu().numpy().astype(np.float32).reshape(-1))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def _resolve_voice(self, requested: str, lang: str, bins: Dict[str, Path]) -> str:
        req = str(requested or "").strip()
        if req and req in bins:
            return req
        default_v = KOKORO_DEFAULT_VOICES.get(lang, "af_heart")
        if default_v in bins:
            return default_v
        avail = self.available_voices(lang)
        if avail:
            return avail[0][0]
        raise TTSError(
            f"Kokoro-82M chưa có giọng đọc cho '{language_name(lang)} ({lang})'. "
            "Vào Cài đặt → API → Giọng đọc → Kokoro-82M để tải pack tương ứng."
        )

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
        if lang not in KOKORO_LANGUAGES:
            raise TTSError(f"Kokoro-82M chưa hỗ trợ ngôn ngữ '{language_name(lang)} ({lang})'.")

        with self._lock:
            kokoro_inst = self._ensure_onnx_loaded()
            bins = self._collect_voice_bins()
            use_gpu = self._want_gpu()
            torch_pair = self._ensure_torch_loaded() if use_gpu else None

        speed_val = max(0.5, min(2.0, float(speed if speed is not None else self.tts_cfg.get("speed") or 1.0)))
        gap_sentence = float(self.tts_cfg.get("gap_sentence") or 0.3)
        gap_paragraph = float(self.tts_cfg.get("gap_paragraph") or 0.8)
        max_chars = max(120, int(self.tts_cfg.get("max_chars") or 280))
        bitrate = str(self.tts_cfg.get("bitrate") or "192k")

        raw_voice = voice or str((self.tts_cfg.get("kokoro_voices") or {}).get(lang) or "")
        vid = self._resolve_voice(raw_voice, lang, bins)

        chunks = chunk_text(
            text,
            max_chars=max_chars,
            gap_sentence=gap_sentence,
            gap_paragraph=gap_paragraph,
        )
        if not chunks:
            raise TTSError("Văn bản trống, không có gì để đọc.")

        backend_used = "gpu" if torch_pair is not None else "cpu"
        total = len(chunks)
        if message_cb:
            message_cb(f"Kokoro-82M ({vid} · {backend_used.upper()}) · {total} đoạn…")

        wavs: List[np.ndarray] = []
        for idx, ch in enumerate(chunks, 1):
            wait_pause_cancel(pause_cb, cancel_cb)
            if torch_pair is not None:
                wav = self._synth_chunk_torch(
                    torch_pair[0], torch_pair[1], kokoro_inst, bins, ch.text, vid, lang, speed_val
                )
            else:
                wav = self._synth_chunk_onnx(kokoro_inst, ch.text, vid, lang, speed_val)
            wavs.append(wav)
            if progress_cb:
                try:
                    progress_cb(min(95, int(idx * 95 / total)))
                except Exception:
                    pass

        out_file, duration, cues_list = finalize_waveforms(
            chunks=chunks,
            wavs=wavs,
            sample_rate=KOKORO_SAMPLE_RATE,
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
            sample_rate=KOKORO_SAMPLE_RATE,
            engine="kokoro",
            backend=backend_used,
            voice=vid,
            chunks=total,
            characters=sum(len(c.text) for c in chunks),
            cues=cues_list,
        )

    def close(self) -> None:
        with self._lock:
            self._kokoro_onnx = None
            self._torch_model = None
            self._torch_vocab = {}
            self._ja_g2p = None
            self._zh_g2p = None
            self._onnx_root = None
            self._torch_root = None
