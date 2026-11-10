"""Local Piper TTS engine (ONNX Runtime C++ binary / Python piper-tts) for multi-language synthesis."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.languages import DEFAULT_LANGUAGE, language_name, normalize_language
from core.settings import (
    ROOT,
    default_f5tts_path,
    default_piper_path,
    default_xtts_path,
    normalize_f5tts_path,
    normalize_piper_path,
    normalize_xtts_path,
)
from core.text_processor import chunk_text
from core.tts_engine import SynthesisCancelled, SynthResult, TTSError
from utils.logger import get_logger
from utils.media_probe import probe_duration, resolve_tool

logger = get_logger("tts.piper")

ProgressCb = Optional[Callable[[int], None]]
MessageCb = Optional[Callable[[str], None]]
CancelCb = Optional[Callable[[], bool]]
PauseCb = Optional[Callable[[], bool]]

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CATALOG_PATH = ROOT / "config" / "tts_catalog.json"

# Default curated Piper ONNX voice models per language
PIPER_DEFAULT_VOICES: Dict[str, str] = {
    "vi": "vi_VN-vais1000-medium",
    "en": "en_US-lessac-medium",
    "zh": "zh_CN-huayan-medium",
    "fr": "fr_FR-upmc-medium",
    "es": "es_ES-davefx-medium",
    "de": "de_DE-thorsten-medium",
    "ru": "ru_RU-irina-medium",
    "ja": "en_US-lessac-medium",
    "ko": "en_US-lessac-medium",
    "th": "en_US-lessac-medium",
    "id": "en_US-lessac-medium",
    "hi": "hi_IN-rohan-medium",
}


def _load_tts_catalog() -> Dict[str, Any]:
    if _CATALOG_PATH.is_file():
        try:
            return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Không đọc được tts_catalog.json: %s", exc)
    return {"edge_voices": [], "piper_voices": []}


_CATALOG_DATA = _load_tts_catalog()

# Build complete Edge-TTS voice lists (all 322+ voices)
ALL_EDGE_VOICES: List[Tuple[str, str]] = []
EDGE_VOICES_BY_LANG: Dict[str, List[Tuple[str, str]]] = {}

for _ev in _CATALOG_DATA.get("edge_voices", []):
    _sn = str(_ev.get("short_name") or "").strip()
    _lbl = str(_ev.get("label") or _sn).strip()
    _lang = str(_ev.get("lang") or "").strip().lower()
    if _sn:
        ALL_EDGE_VOICES.append((_lbl, _sn))
        if _lang:
            EDGE_VOICES_BY_LANG.setdefault(_lang, []).append((_lbl, _sn))

if not ALL_EDGE_VOICES:
    EDGE_VOICES_BY_LANG = {
        "vi": [
            ("[vi-VN] HoaiMy — Nữ (Vietnamese) [vi-VN-HoaiMyNeural]", "vi-VN-HoaiMyNeural"),
            ("[vi-VN] NamMinh — Nam (Vietnamese) [vi-VN-NamMinhNeural]", "vi-VN-NamMinhNeural"),
        ],
        "en": [
            ("[en-US] Aria — Nữ (English) [en-US-AriaNeural]", "en-US-AriaNeural"),
            ("[en-US] Jenny — Nữ (English) [en-US-JennyNeural]", "en-US-JennyNeural"),
            ("[en-US] Guy — Nam (English) [en-US-GuyNeural]", "en-US-GuyNeural"),
        ],
    }
    for _lst in EDGE_VOICES_BY_LANG.values():
        ALL_EDGE_VOICES.extend(_lst)

# Build complete PiperTTS voice lists (all 180+ voice models)
ALL_PIPER_MODELS: List[Dict[str, Any]] = list(_CATALOG_DATA.get("piper_voices", []))
PIPER_VOICES_BY_LANG: Dict[str, List[Tuple[str, str]]] = {}

for _pv in ALL_PIPER_MODELS:
    _key = str(_pv.get("key") or "").strip()
    _lang = str(_pv.get("lang") or "").strip().lower()
    _native = str(_pv.get("name_native") or _lang).strip()
    _name = str(_pv.get("name") or _key).strip()
    _qual = str(_pv.get("quality") or "medium").strip()
    _size = _pv.get("size_mb", 60)
    if _key:
        _disp = f"[{_pv.get('locale', _lang)}] {_name} ({_qual} · ~{_size}MB · {_native}) [{_key}]"
        PIPER_VOICES_BY_LANG.setdefault(_lang, []).append((_disp, _key))
        if _lang not in PIPER_DEFAULT_VOICES:
            PIPER_DEFAULT_VOICES[_lang] = _key

if not PIPER_VOICES_BY_LANG:
    PIPER_VOICES_BY_LANG = {
        "vi": [
            ("[vi_VN] vais1000 (medium · ~60MB · Tiếng Việt) [vi_VN-vais1000-medium]", "vi_VN-vais1000-medium"),
            ("[vi_VN] 25hours_single (low · ~60MB · Tiếng Việt) [vi_VN-25hours_single-low]", "vi_VN-25hours_single-low"),
            ("[vi_VN] vivos (x_low · ~29MB · Tiếng Việt) [vi_VN-vivos-x_low]", "vi_VN-vivos-x_low"),
        ],
        "en": [
            ("[en_US] lessac (medium · ~60MB · English) [en_US-lessac-medium]", "en_US-lessac-medium"),
            ("[en_US] amy (medium · ~60MB · English) [en_US-amy-medium]", "en_US-amy-medium"),
        ],
    }


def get_edge_voices_for_language(language: str = "") -> List[Tuple[str, str]]:
    """Return all Edge-TTS voices for the given language code, or all voices if empty/unknown."""
    raw = str(language or "").strip().lower()
    if not raw:
        return list(ALL_EDGE_VOICES)
    primary = raw.replace("_", "-").split("-", 1)[0]
    matched = EDGE_VOICES_BY_LANG.get(primary)
    if matched:
        return list(matched)
    code = normalize_language(raw, "")
    if code and code in EDGE_VOICES_BY_LANG:
        return list(EDGE_VOICES_BY_LANG[code])
    return list(ALL_EDGE_VOICES)

H2M_DEFAULT_VOICES: List[Tuple[str, str]] = [
    ("Hải Đăng · Bắc (Nam)", "Hải Đăng"),
    ("Ngọc Huyền · Bắc (Nữ)", "Ngọc Huyền"),
    ("Bình Minh · Bắc (Nam)", "Bình Minh"),
    ("Tuyên Quang · Bắc (Nam)", "Tuyên Quang"),
    ("Thanh Hà · Bắc (Nữ)", "Thanh Hà"),
    ("Minh Khang · Nam (Nam)", "Minh Khang"),
    ("Thảo Vy · Nam (Nữ)", "Thảo Vy"),
    ("Hoàng Dũng · Trung (Nam)", "Hoàng Dũng"),
]


_GPU_OVERRIDE: Optional[Tuple[bool, str]] = None
_GPU_CACHE: Optional[Tuple[bool, str]] = None


def set_gpu_override(has_gpu: Optional[bool], info: str = "") -> None:
    """Override GPU detection result (useful for testing or manual simulation)."""
    global _GPU_OVERRIDE
    if has_gpu is None:
        _GPU_OVERRIDE = None
    else:
        _GPU_OVERRIDE = (
            bool(has_gpu),
            info or ("GPU NVIDIA sẵn sàng" if has_gpu else "Chỉ có CPU (Không phát hiện GPU NVIDIA)"),
        )


def detect_gpu_status(force_refresh: bool = False) -> Tuple[bool, str]:
    """Return (has_gpu, gpu_description) for local GPU-dependent TTS engines (F5-TTS / E2-TTS)."""
    global _GPU_CACHE
    if _GPU_OVERRIDE is not None:
        return _GPU_OVERRIDE
    if _GPU_CACHE is not None and not force_refresh:
        return _GPU_CACHE

    gpu_name = ""
    gpu_vram = 0.0
    driver = ""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2,
            creationflags=_CREATE_NO_WINDOW,
        )
        parts = [p.strip() for p in out.strip().split(",")]
        if len(parts) >= 3 and parts[0]:
            gpu_name = parts[0]
            gpu_vram = round(float(parts[1]) / 1024, 1)
            driver = parts[2]
    except Exception:
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                gpu_vram = round(
                    torch.cuda.get_device_properties(0).total_memory / (1024**3), 1
                )
        except Exception:
            pass

    if gpu_name:
        desc = (
            f"{gpu_name} · {gpu_vram} GB · driver {driver}"
            if driver
            else f"{gpu_name} · {gpu_vram} GB"
        )
        _GPU_CACHE = (True, desc)
    else:
        _GPU_CACHE = (False, "Chỉ có CPU (Không phát hiện GPU NVIDIA)")
    return _GPU_CACHE


F5_MODELS_CATALOG: List[Tuple[str, str]] = [
    ("F5-TTS v1 Base (Đa ngôn ngữ · Zero-Shot)", "F5TTS_v1_Base"),
    ("E2-TTS Base (Đa ngôn ngữ · Zero-Shot)", "E2TTS_Base"),
    ("F5-TTS Tiếng Việt (ViVoice · Zero-Shot)", "F5TTS_Vietnamese"),
    ("F5-TTS Base (24kHz · Zero-Shot)", "F5TTS_Base"),
]

XTTS_MODELS_CATALOG: List[Tuple[str, str]] = [
    ("Coqui XTTS-v2 Multilingual (17+ ngôn ngữ · Zero-Shot)", "XTTS_v2_Base"),
    ("viXTTS – Coqui XTTS-v2 Tiếng Việt (Zero-Shot)", "viXTTS"),
]


def resolve_tts_engine_for_language(settings: Optional[Dict[str, Any]], language: str) -> str:
    """Return the configured TTS engine ID ('h2mtts', 'edge', 'piper', 'f5tts', or 'xtts') for a language."""
    code = normalize_language(language)
    cfg = settings if isinstance(settings, dict) else {}
    tts_cfg = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    lang_engines = tts_cfg.get("language_engines") if isinstance(tts_cfg.get("language_engines"), dict) else {}
    chosen = str(lang_engines.get(code) or "").strip().lower()
    if chosen in ("h2mtts", "h2m", "local"):
        return "h2mtts"
    if chosen in ("piper", "pipertts"):
        return "piper"
    if chosen in ("f5tts", "f5", "e2tts", "f5-tts", "e2-tts"):
        return "f5tts"
    if chosen in ("xtts", "xttsv2", "xtts-v2", "coqui", "coqui_xtts"):
        return "xtts"
    if chosen in ("edge", "edgetts", "edge_tts"):
        return "edge"
    # Default: Vietnamese uses h2mtts, other languages use edge
    return "h2mtts" if code == "vi" else "edge"


def resolve_voice_for_language_engine(
    settings: Optional[Dict[str, Any]], language: str, engine_id: str
) -> str:
    """Return the configured voice/model name for a (language, engine_id) pair."""
    code = normalize_language(language)
    cfg = settings if isinstance(settings, dict) else {}
    tts_cfg = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    if engine_id == "h2mtts":
        lang_voices = tts_cfg.get("h2m_voices") if isinstance(tts_cfg.get("h2m_voices"), dict) else {}
        return str(lang_voices.get(code) or tts_cfg.get("voice") or "Hải Đăng").strip()
    if engine_id == "piper":
        piper_voices = tts_cfg.get("piper_voices") if isinstance(tts_cfg.get("piper_voices"), dict) else {}
        return str(
            piper_voices.get(code)
            or PIPER_DEFAULT_VOICES.get(code)
            or "vi_VN-vais1000-medium"
        ).strip()
    if engine_id == "f5tts":
        f5_voices = tts_cfg.get("f5_voices") if isinstance(tts_cfg.get("f5_voices"), dict) else {}
        default_f5 = "F5TTS_Vietnamese" if code == "vi" else "F5TTS_v1_Base"
        return str(f5_voices.get(code) or default_f5).strip()
    if engine_id == "xtts":
        xtts_voices = tts_cfg.get("xtts_voices") if isinstance(tts_cfg.get("xtts_voices"), dict) else {}
        default_xtts = "viXTTS" if code == "vi" else "XTTS_v2_Base"
        return str(xtts_voices.get(code) or default_xtts).strip()
    # edge
    edge_voices = tts_cfg.get("edge_voices") if isinstance(tts_cfg.get("edge_voices"), dict) else {}
    from core.languages import edge_voice

    return str(edge_voices.get(code) or edge_voice(code)).strip()


class PiperTTSEngine:
    """Offline Piper TTS engine matching TTSEngine and EdgeTTSEngine interfaces."""

    def __init__(self, settings: Optional[Dict[str, Any] | str | Path] = None) -> None:
        if isinstance(settings, (str, Path)):
            self.settings: Dict[str, Any] = {}
            raw_piper = str(settings).strip()
            paths: Dict[str, Any] = {"piper": raw_piper}
            tts: Dict[str, Any] = {}
        elif isinstance(settings, dict):
            self.settings = settings
            paths = self.settings.get("paths") if isinstance(self.settings.get("paths"), dict) else {}
            tts = self.settings.get("tts") if isinstance(self.settings.get("tts"), dict) else {}
        else:
            self.settings = {}
            paths = {}
            tts = {}
        self.root = Path(normalize_piper_path(paths.get("piper") or default_piper_path()))
        self.ffmpeg = resolve_tool(str(paths.get("ffmpeg") or "ffmpeg"), "ffmpeg")
        self.bitrate = str(tts.get("bitrate") or "192k")
        try:
            self.default_speed = float(tts.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.default_speed = 1.0
        raw_map = tts.get("piper_voices") if isinstance(tts.get("piper_voices"), dict) else {}
        self._voice_map: Dict[str, str] = {
            normalize_language(k): str(v).strip()
            for k, v in raw_map.items()
            if str(v).strip()
        }

    def close(self) -> None:
        pass

    def find_binary(self) -> Optional[Path]:
        """Locate piper executable inside PiperTTS directory or system PATH."""
        candidates = [
            self.root / "runtime" / "piper" / "piper.exe",
            self.root / "runtime" / "piper.exe",
            self.root / "piper" / "piper.exe",
            self.root / "piper.exe",
            self.root / "runtime" / "piper" / "piper",
            self.root / "piper" / "piper",
        ]
        for cand in candidates:
            if cand.is_file():
                return cand
        which = shutil.which("piper")
        if which:
            return Path(which)
        return None

    def find_model(self, voice_or_model: str, language: str = "vi") -> Optional[Path]:
        """Locate the .onnx voice file in PiperTTS directory."""
        name = str(voice_or_model or "").strip()
        code = normalize_language(language)
        if not name:
            name = self._voice_map.get(code) or PIPER_DEFAULT_VOICES.get(code, "vi_VN-vais1000-medium")
        if name.endswith(".onnx"):
            stem = name[:-5]
        else:
            stem = name

        direct = Path(name)
        if direct.is_file():
            return direct

        search_dirs = [
            self.root / "voices" / code,
            self.root / "voices",
            self.root / "models" / code,
            self.root / "models",
            self.root,
        ]
        for folder in search_dirs:
            if not folder.is_dir():
                continue
            cand = folder / f"{stem}.onnx"
            if cand.is_file():
                return cand
            for found in folder.rglob(f"{stem}.onnx"):
                if found.is_file():
                    return found
        # Fallback: any .onnx matching language prefix in PiperTTS folder
        if self.root.is_dir():
            for found in self.root.rglob("*.onnx"):
                if found.is_file() and (
                    found.stem.lower().startswith(f"{code}_")
                    or found.stem.lower().startswith(f"{code}-")
                ):
                    return found
        return None

    def installed_voices(self, language: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) actually downloaded on disk for a language."""
        code = normalize_language(language) if language else ""
        if not self.root.is_dir():
            return []
        known_labels = {
            val: label
            for lang_list in PIPER_VOICES_BY_LANG.values()
            for label, val in lang_list
        }
        installed: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for onnx in self.root.rglob("*.onnx"):
            if not onnx.is_file():
                continue
            stem = onnx.stem
            if stem in seen:
                continue
            if code:
                stem_lower = stem.lower()
                in_lang_folder = code in [p.lower() for p in onnx.parts]
                prefix_match = stem_lower.startswith(f"{code}_") or stem_lower.startswith(f"{code}-")
                if not (in_lang_folder or prefix_match):
                    continue
            seen.add(stem)
            label = known_labels.get(stem, f"{stem} (Đã tải)")
            installed.append((label, stem))
        return installed

    def check_ready(self, language: str = "") -> Tuple[bool, str]:
        """Verify whether PiperTTS binary and at least one model for `language` are installed."""
        binary = self.find_binary()
        if binary is None:
            return (
                False,
                f"Chưa cài đặt bộ chạy PiperTTS (piper.exe) trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải PiperTTS.",
            )
        if language:
            code = normalize_language(language)
            models = self.installed_voices(code)
            if not models:
                return (
                    False,
                    f"Chưa tải model giọng đọc PiperTTS cho ngôn ngữ '{language_name(code)} ({code})'. "
                    "Vui lòng vào Cài đặt → API → Giọng đọc để tải model.",
                )
        return True, "PiperTTS đã sẵn sàng."

    def available_voices(self, language: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) available for a language."""
        installed = self.installed_voices(language)
        if installed:
            return installed
        code = normalize_language(language) if language else ""
        base_list = list(PIPER_VOICES_BY_LANG.get(code, [])) if code else []
        seen = {val for _, val in base_list}
        if not base_list:
            for lang_voices in PIPER_VOICES_BY_LANG.values():
                for label, val in lang_voices:
                    if val not in seen:
                        seen.add(val)
                        base_list.append((label, val))
        return base_list

    def voice_for(self, language: str) -> str:
        code = normalize_language(language)
        return self._voice_map.get(code) or PIPER_DEFAULT_VOICES.get(code, "vi_VN-vais1000-medium")

    def synthesize(
        self,
        text: str,
        out_path: str | os.PathLike,
        language: str = DEFAULT_LANGUAGE,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        progress_cb: ProgressCb = None,
        message_cb: MessageCb = None,
        pause_cb: PauseCb = None,
        cancel_cb: CancelCb = None,
    ) -> SynthResult:
        clean_text = " ".join(str(text or "").split())
        if not clean_text:
            raise TTSError("Văn bản rỗng, không có gì để đọc.")

        code = normalize_language(language)
        chosen_voice = (voice or self.voice_for(code)).strip()
        binary = self.find_binary()
        if binary is None:
            raise TTSError(
                f"Chưa cài đặt PiperTTS (không tìm thấy piper.exe tại '{self.root}'). "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải PiperTTS."
            )
        model_path = self.find_model(chosen_voice, code)
        if model_path is None:
            raise TTSError(
                f"Chưa tải model giọng đọc PiperTTS '{chosen_voice}' cho ngôn ngữ '{language_name(code)} ({code})'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải model."
            )

        chunks = [
            c.text for c in chunk_text(clean_text, min_chars=250, max_chars=900) if c.text.strip()
        ]
        if not chunks:
            raise TTSError("Văn bản rỗng sau khi chia đoạn.")

        if message_cb:
            message_cb(
                f"PiperTTS ({language_name(code)}): {len(chunks)} đoạn, model {model_path.stem}"
            )

        eff_speed = float(speed or self.default_speed or 1.0)
        length_scale = round(1.0 / max(0.25, min(4.0, eff_speed)), 3)

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_piper_", dir=str(target.parent)))

        try:
            wav_parts: List[Path] = []
            for idx, chunk in enumerate(chunks):
                while pause_cb and pause_cb():
                    if cancel_cb and cancel_cb():
                        raise SynthesisCancelled("Đã huỷ tạo audio PiperTTS.")
                    time.sleep(0.2)
                if cancel_cb and cancel_cb():
                    raise SynthesisCancelled("Đã huỷ tạo audio PiperTTS.")

                part_wav = temp_dir / f"part_{idx:04d}.wav"
                cmd = [
                    str(binary),
                    "--model",
                    str(model_path),
                    "--output_file",
                    str(part_wav),
                    "--length_scale",
                    str(length_scale),
                ]
                proc = subprocess.run(
                    cmd,
                    input=chunk.encode("utf-8"),
                    capture_output=True,
                    timeout=120,
                    creationflags=_CREATE_NO_WINDOW,
                )
                if proc.returncode != 0 or not part_wav.is_file() or part_wav.stat().st_size == 0:
                    err = proc.stderr.decode("utf-8", errors="replace")[:300]
                    raise TTSError(f"PiperTTS lỗi tạo audio đoạn {idx + 1}: {err}")
                wav_parts.append(part_wav)
                if progress_cb:
                    progress_cb(int(round(100 * (idx + 1) / len(chunks))))

            if len(wav_parts) == 1 and target.suffix.lower() == ".wav":
                os.replace(wav_parts[0], target)
            else:
                concat_txt = temp_dir / "concat.txt"
                concat_txt.write_text(
                    "\n".join(f"file '{p.name}'" for p in wav_parts),
                    encoding="utf-8",
                )
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_txt),
                ]
                if target.suffix.lower() == ".mp3":
                    cmd_ff.extend(["-c:a", "libmp3lame", "-b:a", self.bitrate])
                cmd_ff.append(str(target))
                res = subprocess.run(
                    cmd_ff,
                    cwd=str(temp_dir),
                    capture_output=True,
                    timeout=180,
                    creationflags=_CREATE_NO_WINDOW,
                )
                if res.returncode != 0 or not target.is_file():
                    err = res.stderr.decode("utf-8", errors="replace")[:300]
                    raise TTSError(f"FFmpeg ghép audio PiperTTS lỗi: {err}")

            dur = probe_duration(target)
            return SynthResult(
                path=target,
                duration=dur,
                chunks=len(chunks),
                voice=model_path.stem,
                engine="piper",
                backend="onnx",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class F5TTSEngine:
    """Offline F5-TTS / E2-TTS GPU engine for high-quality zero-shot voice synthesis."""

    def __init__(self, settings: Optional[Dict[str, Any] | str | Path] = None) -> None:
        if isinstance(settings, (str, Path)):
            self.settings: Dict[str, Any] = {}
            raw_f5 = str(settings).strip()
            paths: Dict[str, Any] = {"f5tts": raw_f5}
            tts: Dict[str, Any] = {}
        elif isinstance(settings, dict):
            self.settings = settings
            paths = self.settings.get("paths") if isinstance(self.settings.get("paths"), dict) else {}
            tts = self.settings.get("tts") if isinstance(self.settings.get("tts"), dict) else {}
        else:
            self.settings = {}
            paths = {}
            tts = {}
        self.root = Path(normalize_f5tts_path(paths.get("f5tts") or default_f5tts_path()))
        self.ffmpeg = resolve_tool(str(paths.get("ffmpeg") or "ffmpeg"), "ffmpeg")
        self.bitrate = str(tts.get("bitrate") or "192k")
        try:
            self.default_speed = float(tts.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.default_speed = 1.0

    def close(self) -> None:
        pass

    def installed_voices(self, language: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) actually installed inside F5TTS directory."""
        if not self.root.is_dir():
            return []
        known = {mid: label for label, mid in F5_MODELS_CATALOG}
        found_list: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for ext in ("*.safetensors", "*.pt", "*.ckpt"):
            for f in self.root.rglob(ext):
                if not f.is_file():
                    continue
                parent_name = f.parent.name
                mid = parent_name if parent_name in known else f.stem
                if mid not in seen:
                    seen.add(mid)
                    found_list.append((known.get(mid, f"{mid} (Đã tải)"), mid))
        return found_list

    def check_ready(self, language: str = "") -> Tuple[bool, str]:
        has_gpu, gpu_desc = detect_gpu_status()
        if not has_gpu:
            return (
                False,
                f"F5-TTS / E2-TTS yêu cầu GPU NVIDIA (CUDA) để hoạt động ({gpu_desc}).",
            )
        voices = self.installed_voices(language)
        if not voices:
            return (
                False,
                f"Chưa cài đặt model F5-TTS / E2-TTS trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → F5-TTS / E2-TTS để tải model.",
            )
        return True, f"F5-TTS / E2-TTS đã sẵn sàng ({gpu_desc})."

    def synthesize(
        self,
        text: str,
        out_path: str | os.PathLike,
        language: str = DEFAULT_LANGUAGE,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        progress_cb: ProgressCb = None,
        message_cb: MessageCb = None,
        pause_cb: PauseCb = None,
        cancel_cb: CancelCb = None,
    ) -> SynthResult:
        ok, reason = self.check_ready(language)
        if not ok:
            raise TTSError(reason)

        clean_text = " ".join(str(text or "").split())
        if not clean_text:
            raise TTSError("Văn bản rỗng, không có gì để đọc.")

        code = normalize_language(language)
        chosen_model = (voice or ("F5TTS_Vietnamese" if code == "vi" else "F5TTS_v1_Base")).strip()
        cli_bin = shutil.which("f5-tts_infer-cli")
        if not cli_bin:
            raise TTSError(
                "Chưa tìm thấy công cụ dòng lệnh 'f5-tts_infer-cli'. "
                "Vui lòng cài đặt gói f5-tts hoặc tải đầy đủ tài nguyên trong Cài đặt → API → Giọng đọc."
            )

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_f5tts_", dir=str(target.parent)))
        try:
            out_wav = temp_dir / "infer_cli_out.wav"
            cmd = [
                cli_bin,
                "--model",
                chosen_model,
                "--gen_text",
                clean_text,
                "--output_dir",
                str(temp_dir),
                "--output_file",
                out_wav.name,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,
                creationflags=_CREATE_NO_WINDOW,
            )
            if proc.returncode != 0 or not out_wav.is_file():
                err = proc.stderr.decode("utf-8", errors="replace")[:300]
                raise TTSError(f"F5-TTS / E2-TTS lỗi tạo audio: {err}")
            if progress_cb:
                progress_cb(100)
            if target.suffix.lower() == ".wav":
                os.replace(out_wav, target)
            else:
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-i",
                    str(out_wav),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    self.bitrate,
                    str(target),
                ]
                subprocess.run(cmd_ff, capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW)
            dur = probe_duration(target)
            return SynthResult(
                path=target,
                duration=dur,
                chunks=1,
                voice=chosen_model,
                engine="f5tts",
                backend="gpu",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class XTTSEngine:
    """Offline Coqui XTTS-v2 GPU engine for multilingual zero-shot voice synthesis."""

    def __init__(self, settings: Optional[Dict[str, Any] | str | Path] = None) -> None:
        if isinstance(settings, (str, Path)):
            self.settings: Dict[str, Any] = {}
            raw_xtts = str(settings).strip()
            paths: Dict[str, Any] = {"xtts": raw_xtts}
            tts: Dict[str, Any] = {}
        elif isinstance(settings, dict):
            self.settings = settings
            paths = self.settings.get("paths") if isinstance(self.settings.get("paths"), dict) else {}
            tts = self.settings.get("tts") if isinstance(self.settings.get("tts"), dict) else {}
        else:
            self.settings = {}
            paths = {}
            tts = {}
        self.root = Path(normalize_xtts_path(paths.get("xtts") or default_xtts_path()))
        self.ffmpeg = resolve_tool(str(paths.get("ffmpeg") or "ffmpeg"), "ffmpeg")
        self.bitrate = str(tts.get("bitrate") or "192k")
        try:
            self.default_speed = float(tts.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.default_speed = 1.0

    def close(self) -> None:
        pass

    def installed_voices(self, language: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) actually installed inside XTTSv2 directory."""
        if not self.root.is_dir():
            return []
        known = {mid: label for label, mid in XTTS_MODELS_CATALOG}
        found_list: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for f in self.root.rglob("*.pth"):
            if not f.is_file() or f.name == "speakers_xtts.pth":
                continue
            parent_name = f.parent.name
            mid = parent_name if parent_name in known else f.stem
            if mid not in seen:
                seen.add(mid)
                found_list.append((known.get(mid, f"{mid} (Đã tải)"), mid))
        return found_list

    def check_ready(self, language: str = "") -> Tuple[bool, str]:
        has_gpu, gpu_desc = detect_gpu_status()
        if not has_gpu:
            return (
                False,
                f"Coqui XTTS-v2 yêu cầu GPU NVIDIA (CUDA) để hoạt động ({gpu_desc}).",
            )
        voices = self.installed_voices(language)
        if not voices:
            return (
                False,
                f"Chưa cài đặt model Coqui XTTS-v2 trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → Coqui XTTS-v2 để tải model.",
            )
        return True, f"Coqui XTTS-v2 đã sẵn sàng ({gpu_desc})."

    def synthesize(
        self,
        text: str,
        out_path: str | os.PathLike,
        language: str = DEFAULT_LANGUAGE,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        progress_cb: ProgressCb = None,
        message_cb: MessageCb = None,
        pause_cb: PauseCb = None,
        cancel_cb: CancelCb = None,
    ) -> SynthResult:
        ok, reason = self.check_ready(language)
        if not ok:
            raise TTSError(reason)

        clean_text = " ".join(str(text or "").split())
        if not clean_text:
            raise TTSError("Văn bản rỗng, không có gì để đọc.")

        code = normalize_language(language)
        chosen_model = (voice or ("viXTTS" if code == "vi" else "XTTS_v2_Base")).strip()
        cli_bin = shutil.which("tts")
        if not cli_bin:
            raise TTSError(
                "Chưa tìm thấy công cụ dòng lệnh 'tts' (coqui-tts). "
                "Vui lòng cài đặt gói coqui-tts hoặc tải đầy đủ tài nguyên trong Cài đặt → API → Giọng đọc."
            )

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_xtts_", dir=str(target.parent)))
        try:
            out_wav = temp_dir / "xtts_out.wav"
            model_dir = self.root / "models" / chosen_model
            cmd = [
                cli_bin,
                "--text",
                clean_text,
                "--language_idx",
                code,
                "--out_path",
                str(out_wav),
                "--use_cuda",
                "true",
            ]
            if model_dir.is_dir():
                cmd.extend(["--model_path", str(model_dir), "--config_path", str(model_dir / "config.json")])
            else:
                cmd.extend(["--model_name", "tts_models/multilingual/multi-dataset/xtts_v2"])
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,
                creationflags=_CREATE_NO_WINDOW,
            )
            if proc.returncode != 0 or not out_wav.is_file():
                err = proc.stderr.decode("utf-8", errors="replace")[:300]
                raise TTSError(f"Coqui XTTS-v2 lỗi tạo audio: {err}")
            if progress_cb:
                progress_cb(100)
            if target.suffix.lower() == ".wav":
                os.replace(out_wav, target)
            else:
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-i",
                    str(out_wav),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    self.bitrate,
                    str(target),
                ]
                subprocess.run(cmd_ff, capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW)
            dur = probe_duration(target)
            return SynthResult(
                path=target,
                duration=dur,
                chunks=1,
                voice=chosen_model,
                engine="xtts",
                backend="gpu",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


