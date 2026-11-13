"""Local Piper TTS engine (ONNX Runtime C++ binary / Python piper-tts) for multi-language synthesis."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore", category=FutureWarning)

try:
    import torch
except Exception:
    torch = None  # type: ignore

from core.languages import DEFAULT_LANGUAGE, language_name, normalize_language
from core.settings import (
    ROOT,
    default_piper_path,
    normalize_piper_path,
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
    "ja": "ja_JP-hi_fi_captain-medium",
    "ko": "ko_KR-kss-medium",
    "pt": "pt_BR-edresson-low",
    "pl": "pl_PL-bass-high",
    "fr": "fr_FR-upmc-medium",
    "es": "es_ES-davefx-medium",
    "de": "de_DE-thorsten-medium",
    "ru": "ru_RU-irina-medium",
    "th": "en_US-lessac-medium",
    "id": "en_US-lessac-medium",
    "hi": "hi_IN-rohan-medium",
}


def sanitize_piper_config(json_path: Path | str) -> bool:
    """Ensure all keys in phoneme_id_map and phoneme_map in a Piper ONNX model JSON config are single codepoints,
    and espeak voice is valid (e.g. 'cmn' for Mandarin instead of 'zh' which crashes espeak-ng).

    Piper 1.2.0 C++ binary crashes with error:
        "..." is not a single codepoint (ids=...,)
    if any key in phoneme_id_map has len > 1 (e.g. diphthongs in Japanese, Korean, or digraphs in Chinese).
    This function removes invalid multi-character keys from the JSON config in place.
    """
    p = Path(json_path)
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        changed = False
        pid = data.get("phoneme_id_map")
        if isinstance(pid, dict):
            bad_keys = [k for k in pid if len(k) > 1]
            if bad_keys:
                for k in bad_keys:
                    del pid[k]
                changed = True
        pmap = data.get("phoneme_map")
        if isinstance(pmap, dict):
            bad_keys2 = [k for k in pmap if len(k) > 1]
            if bad_keys2:
                for k in bad_keys2:
                    del pmap[k]
                changed = True
        espeak_cfg = data.get("espeak")
        if isinstance(espeak_cfg, dict):
            v = str(espeak_cfg.get("voice") or "").strip().lower()
            if v in ("zh", "zh-cmn"):
                espeak_cfg["voice"] = "cmn"
                changed = True
        if changed:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("Sanitized Piper model config '%s' (purged multi-codepoint keys / fixed espeak voice)", p.name)
        return changed
    except Exception as exc:
        logger.warning("Không thể sanitize Piper config %s: %s", p, exc)
        return False


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


def is_cuda_available() -> bool:
    """Return True if torch and CUDA GPU are usable."""
    if torch is None:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


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
        if is_cuda_available():
            try:
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


def resolve_tts_engine_for_language(settings: Optional[Dict[str, Any]], language: str) -> str:
    """Return the configured TTS engine ID ('h2mtts', 'edge', or 'piper') for a language."""
    code = normalize_language(language)
    cfg = settings if isinstance(settings, dict) else {}
    tts_cfg = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    lang_engines = tts_cfg.get("language_engines") if isinstance(tts_cfg.get("language_engines"), dict) else {}
    chosen = str(lang_engines.get(code) or "").strip().lower()
    if chosen in ("h2mtts", "h2m", "local"):
        return "h2mtts"
    if chosen in ("piper", "pipertts"):
        return "piper"
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
        self.ffprobe = resolve_tool(str(paths.get("ffprobe") or "ffprobe"), "ffprobe")
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
            self._sanitize_model_config(direct)
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
                self._sanitize_model_config(cand)
                return cand
            for found in folder.rglob(f"{stem}.onnx"):
                if found.is_file():
                    self._sanitize_model_config(found)
                    return found
        # Fallback: any .onnx matching language prefix in PiperTTS folder
        if self.root.is_dir():
            for found in self.root.rglob("*.onnx"):
                if found.is_file() and (
                    found.stem.lower().startswith(f"{code}_")
                    or found.stem.lower().startswith(f"{code}-")
                ):
                    self._sanitize_model_config(found)
                    return found
        return None

    @staticmethod
    def _sanitize_model_config(onnx_path: Path) -> None:
        cand1 = onnx_path.with_name(f"{onnx_path.name}.json")
        if cand1.is_file():
            sanitize_piper_config(cand1)
        cand2 = onnx_path.with_suffix(".json")
        if cand2.is_file():
            sanitize_piper_config(cand2)

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
            if not onnx.is_file() or onnx.stat().st_size == 0:
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
        raw_text = str(text or "").strip()
        if not raw_text:
            raise TTSError("Văn bản rỗng, không có gì để đọc.")

        code = normalize_language(language)
        chosen_voice = (voice or self.voice_for(code)).strip()

        # Tiền xử lý tiếng Nhật: Chuyển đổi Hán tự (Kanji) sang Hiragana bằng pykakasi.
        # Piper C++ binary không nhúng OpenJTalk/MeCab nên espeak-ng sẽ đánh vần chữ Hán
        # thành "Chinese letter" thay vì phát âm tiếng Nhật chuẩn xác.
        if (code == "ja" or chosen_voice.startswith(("ja_", "ja-"))) and raw_text:
            try:
                import pykakasi
                kks = pykakasi.kakasi()
                converted_lines: List[str] = []
                for line in raw_text.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        converted_lines.append("")
                    else:
                        conv = kks.convert(stripped)
                        converted_lines.append("".join(item["hira"] for item in conv))
                raw_text = "\n".join(converted_lines).strip()
            except Exception as exc:
                logger.warning("Không thể chuyển đổi Kanji sang Hiragana: %s", exc)

        binary = self.find_binary()
        if binary is None:
            raise TTSError(
                f"Chưa cài đặt PiperTTS (không tìm thấy piper.exe tại '{self.root}'). "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải PiperTTS."
            )
        model_path = self.find_model(chosen_voice, code)
        if model_path is None or model_path.stat().st_size == 0:
            raise TTSError(
                f"Chưa tải model giọng đọc PiperTTS '{chosen_voice}' cho ngôn ngữ '{language_name(code)} ({code})'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải model."
            )
        json_cand = model_path.with_name(f"{model_path.name}.json")
        if json_cand.is_file():
            sanitize_piper_config(json_cand)
        else:
            json_cand2 = model_path.with_suffix(".json")
            if json_cand2.is_file():
                sanitize_piper_config(json_cand2)
            else:
                raise TTSError(
                    f"Thiếu file cấu hình '{json_cand.name}' của model PiperTTS '{model_path.stem}'. "
                    "Vui lòng vào Cài đặt → API → Giọng đọc để tải lại model."
                )

        tts_cfg = (
            self.settings.get("tts")
            if isinstance(self.settings, dict) and isinstance(self.settings.get("tts"), dict)
            else {}
        )
        try:
            cfg_max_chars = int(tts_cfg.get("max_chars", 350) or 350)
        except (TypeError, ValueError):
            cfg_max_chars = 350
        eff_max_chars = max(200, min(900, cfg_max_chars))
        try:
            gap_sentence = max(0.0, float(tts_cfg.get("gap_sentence", 0.3) or 0.0))
        except (TypeError, ValueError):
            gap_sentence = 0.3
        try:
            gap_paragraph = max(0.0, float(tts_cfg.get("gap_paragraph", 0.8) or 0.0))
        except (TypeError, ValueError):
            gap_paragraph = 0.8

        chunks = [
            c
            for c in chunk_text(
                raw_text,
                min_chars=min(200, eff_max_chars),
                max_chars=eff_max_chars,
                gap_sentence=gap_sentence,
                gap_paragraph=gap_paragraph,
                gap_inner=min(0.15, gap_sentence),
            )
            if c.text.strip()
        ]
        if not chunks:
            raise TTSError("Văn bản rỗng sau khi chia đoạn.")

        if message_cb:
            message_cb(
                f"PiperTTS ({language_name(code)}): {len(chunks)} đoạn, model {model_path.stem}"
            )

        eff_speed = float(speed or self.default_speed or 1.0)
        length_scale = round(1.0 / max(0.25, min(4.0, eff_speed)), 3)

        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_piper_", dir=str(target.parent)))

        try:
            concat_parts: List[Path] = []
            cues_list: List[Tuple[float, float, str]] = []
            cursor_time = 0.0
            detected_rate = 22050

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
                espeak_dir = binary.parent / "espeak-ng-data"
                if espeak_dir.is_dir():
                    cmd.extend(["--espeak_data", str(espeak_dir)])
                proc = subprocess.run(
                    cmd,
                    input=chunk.text.encode("utf-8"),
                    cwd=str(binary.parent),
                    capture_output=True,
                    timeout=120,
                    creationflags=_CREATE_NO_WINDOW,
                )
                if proc.returncode != 0 or not part_wav.is_file() or part_wav.stat().st_size == 0:
                    err = proc.stderr.decode("utf-8", errors="replace")[:300]
                    raise TTSError(f"PiperTTS lỗi tạo audio đoạn {idx + 1}: {err}")

                wav_dur = 0.0
                wav_ch = 1
                wav_sw = 2
                try:
                    with wave.open(str(part_wav), "rb") as wf:
                        detected_rate = wf.getframerate() or 22050
                        wav_ch = wf.getnchannels() or 1
                        wav_sw = wf.getsampwidth() or 2
                        wav_dur = wf.getnframes() / float(detected_rate)
                except Exception:
                    wav_dur = probe_duration(part_wav, self.ffprobe)

                if wav_dur > 0:
                    cues_list.append((cursor_time, cursor_time + wav_dur, chunk.text.strip()))
                    cursor_time += wav_dur

                concat_parts.append(part_wav)

                if idx < len(chunks) - 1 and chunk.gap_after > 0:
                    sil_wav = temp_dir / f"silence_{idx:04d}.wav"
                    n_frames = max(1, int(round(chunk.gap_after * detected_rate)))
                    try:
                        with wave.open(str(sil_wav), "wb") as swf:
                            swf.setnchannels(wav_ch)
                            swf.setsampwidth(wav_sw)
                            swf.setframerate(detected_rate)
                            swf.writeframes(b"\x00" * (n_frames * wav_ch * wav_sw))
                        concat_parts.append(sil_wav)
                        cursor_time += n_frames / float(detected_rate)
                    except Exception as exc:
                        logger.warning("Không tạo được khoảng nghỉ PiperTTS: %s", exc)

                if progress_cb:
                    progress_cb(int(round(100 * (idx + 1) / len(chunks))))

            if len(concat_parts) == 1 and target.suffix.lower() == ".wav":
                os.replace(concat_parts[0], target)
            elif len(concat_parts) == 1:
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(concat_parts[0]),
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
                    raise TTSError(f"FFmpeg xuất audio PiperTTS lỗi: {err}")
            else:
                concat_txt = temp_dir / "concat.txt"
                concat_txt.write_text(
                    "\n".join(f"file '{p.name}'" for p in concat_parts),
                    encoding="utf-8",
                )
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
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

            dur = probe_duration(target, self.ffprobe)
            if dur <= 0:
                dur = cursor_time
            return SynthResult(
                path=target,
                duration=dur,
                sample_rate=detected_rate,
                chunks=len(chunks),
                characters=len(" ".join(raw_text.split())),
                voice=model_path.stem,
                engine="piper",
                backend="onnx",
                cues=cues_list or None,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


