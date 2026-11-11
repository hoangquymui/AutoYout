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


F5_MODELS_CATALOG: List[Tuple[str, str]] = [
    ("F5-TTS v1 Base (Đa ngôn ngữ · Zero-Shot)", "F5TTS_v1_Base"),
    ("E2-TTS Base (Đa ngôn ngữ · Zero-Shot)", "E2TTS_Base"),
    ("F5-TTS Tiếng Việt (ViVoice · Zero-Shot)", "F5TTS_Vietnamese"),
    ("F5-TTS Base (24kHz · Zero-Shot)", "F5TTS_Base"),
]

# Danh sách giọng đọc Nam / Nữ cho F5-TTS & E2-TTS: (label, voice_id, gender, lang_scope)
F5_VOICES_CATALOG: List[Tuple[str, str, str, str]] = [
    # Tiếng Việt — Nữ
    ("Ngọc Ánh — Nữ · Miền Bắc (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Ngọc Ánh", "Nữ", "vi"),
    ("Thanh Hà — Nữ · Truyền cảm (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Thanh Hà", "Nữ", "vi"),
    ("Mai Phương — Nữ · Miền Nam (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Mai Phương", "Nữ", "vi"),
    ("Khánh Linh — Nữ · Trẻ trung (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Khánh Linh", "Nữ", "vi"),
    ("Thu Trang — Nữ · Miền Trung (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Thu Trang", "Nữ", "vi"),
    # Tiếng Việt — Nam
    ("Hải Đăng — Nam · Miền Bắc (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Hải Đăng", "Nam", "vi"),
    ("Minh Quân — Nam · Thuyết minh (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Minh Quân", "Nam", "vi"),
    ("Bảo Nam — Nam · Miền Nam (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Bảo Nam", "Nam", "vi"),
    ("Hoàng Long — Nam · Trầm ấm (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Hoàng Long", "Nam", "vi"),
    ("Quốc Khánh — Nam · Miền Trung (F5-TTS Tiếng Việt)", "F5TTS_Vietnamese::Quốc Khánh", "Nam", "vi"),
    # Đa ngôn ngữ F5-TTS — Nữ & Nam
    ("Emma — Nữ · Tự nhiên (F5-TTS v1 Đa ngôn ngữ)", "F5TTS_v1_Base::Emma_Female", "Nữ", "multi"),
    ("Clara — Nữ · Thuyết minh (F5-TTS v1 Đa ngôn ngữ)", "F5TTS_v1_Base::Clara_Female", "Nữ", "multi"),
    ("Liam — Nam · Trầm ấm (F5-TTS v1 Đa ngôn ngữ)", "F5TTS_v1_Base::Liam_Male", "Nam", "multi"),
    ("Ryan — Nam · Tin tức (F5-TTS v1 Đa ngôn ngữ)", "F5TTS_v1_Base::Ryan_Male", "Nam", "multi"),
    # Đa ngôn ngữ E2-TTS — Nữ & Nam
    ("Aria — Nữ · Trong trẻo (E2-TTS Đa ngôn ngữ)", "E2TTS_Base::Aria_Female", "Nữ", "multi"),
    ("Jenny — Nữ · Kể chuyện (E2-TTS Đa ngôn ngữ)", "E2TTS_Base::Jenny_Female", "Nữ", "multi"),
    ("Guy — Nam · Chững chạc (E2-TTS Đa ngôn ngữ)", "E2TTS_Base::Guy_Male", "Nam", "multi"),
    ("Christopher — Nam · Documentary (E2-TTS Đa ngôn ngữ)", "E2TTS_Base::Christopher_Male", "Nam", "multi"),
]

# Mẫu câu & cấu hình tham chiếu mặc định cho F5-TTS / E2-TTS voice cloning
F5_REF_SAMPLES: Dict[str, Dict[str, str]] = {
    # Nữ — Tiếng Anh / Đa ngôn ngữ
    "Emma_Female": {
        "text": "Some call me nature, others call me mother nature.",
        "edge_voice": "en-US-EmmaMultilingualNeural",
    },
    "Clara_Female": {
        "text": "Hello there, welcome to our presentation on science and nature.",
        "edge_voice": "en-US-AnaNeural",
    },
    "Aria_Female": {
        "text": "When you look up at the night sky, billions of stars shine brightly.",
        "edge_voice": "en-US-AriaNeural",
    },
    "Jenny_Female": {
        "text": "Once upon a time in a small quiet village, something wonderful happened.",
        "edge_voice": "en-US-JennyNeural",
    },
    # Nam — Tiếng Anh / Đa ngôn ngữ
    "Liam_Male": {
        "text": "The early morning mist was beginning to lift, revealing the towering peaks.",
        "edge_voice": "en-CA-LiamNeural",
    },
    "Ryan_Male": {
        "text": "This is the morning news report bringing you updates from around the world.",
        "edge_voice": "en-GB-RyanNeural",
    },
    "Guy_Male": {
        "text": "Technology is constantly moving forward and changing how we live our lives.",
        "edge_voice": "en-US-GuyNeural",
    },
    "Christopher_Male": {
        "text": "Deep beneath the ocean surface lies a mysterious world waiting to be explored.",
        "edge_voice": "en-US-ChristopherNeural",
    },
    # Nữ — Tiếng Việt
    "Ngọc Ánh": {
        "text": "Xin chào quý vị, đây là bản tin tổng hợp các sự kiện công nghệ và đời sống trong ngày.",
        "edge_voice": "vi-VN-HoaiMyNeural",
    },
    "Thanh Hà": {
        "text": "Hôm nay chúng ta cùng lắng nghe những câu chuyện ý nghĩa và bài học sâu sắc về cuộc sống.",
        "edge_voice": "vi-VN-HoaiMyNeural",
    },
    "Mai Phương": {
        "text": "Chào mừng bạn đến với kênh khám phá kiến thức khoa học và những điều kỳ thú quanh ta.",
        "edge_voice": "vi-VN-HoaiMyNeural",
    },
    "Khánh Linh": {
        "text": "Một ngày mới đã bắt đầu, hãy cùng bắt tay vào công việc với thật nhiều niềm vui và hứng khởi.",
        "edge_voice": "vi-VN-HoaiMyNeural",
    },
    "Thu Trang": {
        "text": "Mỗi khoảnh khắc trôi qua đều là cơ hội tuyệt vời để chúng ta học hỏi và trưởng thành hơn.",
        "edge_voice": "vi-VN-HoaiMyNeural",
    },
    # Nam — Tiếng Việt
    "Hải Đăng": {
        "text": "Kính chào quý vị và các bạn, chúc một ngày làm việc tràn đầy năng lượng và may mắn.",
        "edge_voice": "vi-VN-NamMinhNeural",
    },
    "Minh Quân": {
        "text": "Trong chương trình tài liệu hôm nay, chúng tôi sẽ đưa quý khán giả đến với những vùng đất mới lạ.",
        "edge_voice": "vi-VN-NamMinhNeural",
    },
    "Bảo Nam": {
        "text": "Xin chào tất cả các bạn đã quay trở lại với chuỗi video hướng dẫn tự động hóa hiện đại.",
        "edge_voice": "vi-VN-NamMinhNeural",
    },
    "Hoàng Long": {
        "text": "Hành trình khám phá tri thức và công nghệ luôn mở ra những chân trời vô tận cho cuộc sống.",
        "edge_voice": "vi-VN-NamMinhNeural",
    },
    "Quốc Khánh": {
        "text": "Xin chào các bạn.",
        "edge_voice": "vi-VN-NamMinhNeural",
    },
}

XTTS_MODELS_CATALOG: List[Tuple[str, str]] = [
    ("Coqui XTTS-v2 Multilingual (17+ ngôn ngữ · Zero-Shot)", "XTTS_v2"),
    ("viXTTS – Coqui XTTS-v2 Tiếng Việt (Zero-Shot)", "viXTTS"),
]

# Danh sách giọng đọc Nam / Nữ cho Coqui XTTS-v2 (Studio Speakers & viXTTS): (label, voice_id, gender, lang_scope)
XTTS_VOICES_CATALOG: List[Tuple[str, str, str, str]] = [
    # Tiếng Việt (viXTTS) — Nữ
    ("Ngọc Ánh — Nữ · Miền Bắc (viXTTS Tiếng Việt)", "viXTTS::Ngọc Ánh", "Nữ", "vi"),
    ("Thanh Hà — Nữ · Truyền cảm (viXTTS Tiếng Việt)", "viXTTS::Thanh Hà", "Nữ", "vi"),
    ("Mai Phương — Nữ · Miền Nam (viXTTS Tiếng Việt)", "viXTTS::Mai Phương", "Nữ", "vi"),
    ("Khánh Linh — Nữ · Trẻ trung (viXTTS Tiếng Việt)", "viXTTS::Khánh Linh", "Nữ", "vi"),
    ("Thu Trang — Nữ · Miền Trung (viXTTS Tiếng Việt)", "viXTTS::Thu Trang", "Nữ", "vi"),
    # Tiếng Việt (viXTTS) — Nam
    ("Hải Đăng — Nam · Miền Bắc (viXTTS Tiếng Việt)", "viXTTS::Hải Đăng", "Nam", "vi"),
    ("Minh Quân — Nam · Thuyết minh (viXTTS Tiếng Việt)", "viXTTS::Minh Quân", "Nam", "vi"),
    ("Bảo Nam — Nam · Miền Nam (viXTTS Tiếng Việt)", "viXTTS::Bảo Nam", "Nam", "vi"),
    ("Hoàng Long — Nam · Trầm ấm (viXTTS Tiếng Việt)", "viXTTS::Hoàng Long", "Nam", "vi"),
    ("Quốc Khánh — Nam · Miền Trung (viXTTS Tiếng Việt)", "viXTTS::Quốc Khánh", "Nam", "vi"),
    # Coqui XTTS-v2 Studio Speakers — Nữ (Đa ngôn ngữ)
    ("Claribel Dervla — Nữ · Trầm ấm (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Claribel Dervla", "Nữ", "multi"),
    ("Daisy Studious — Nữ · Thuyết minh (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Daisy Studious", "Nữ", "multi"),
    ("Gracie Wise — Nữ · Kể chuyện (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Gracie Wise", "Nữ", "multi"),
    ("Ana Florence — Nữ · Tin tức (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Ana Florence", "Nữ", "multi"),
    ("Sofia Hellen — Nữ · Tự nhiên (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Sofia Hellen", "Nữ", "multi"),
    ("Tammie Ema — Nữ · Trẻ trung (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Tammie Ema", "Nữ", "multi"),
    ("Asya Anara — Nữ · Podcast (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Asya Anara", "Nữ", "multi"),
    # Coqui XTTS-v2 Studio Speakers — Nam (Đa ngôn ngữ)
    ("Andrew Chipper — Nam · Trẻ trung (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Andrew Chipper", "Nam", "multi"),
    ("Badr Odhiambo — Nam · Trầm ấm (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Badr Odhiambo", "Nam", "multi"),
    ("Dionisio Schuyler — Nam · Kể chuyện (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Dionisio Schuyler", "Nam", "multi"),
    ("Royston Min — Nam · Tin tức (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Royston Min", "Nam", "multi"),
    ("Viktor Eka — Nam · Documentary (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Viktor Eka", "Nam", "multi"),
    ("Damien Black — Nam · Điện ảnh (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Damien Black", "Nam", "multi"),
    ("Craig Gutsy — Nam · Mạnh mẽ (XTTS-v2 Đa ngôn ngữ)", "XTTS_v2::Craig Gutsy", "Nam", "multi"),
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
        if model_path is None or model_path.stat().st_size == 0:
            raise TTSError(
                f"Chưa tải model giọng đọc PiperTTS '{chosen_voice}' cho ngôn ngữ '{language_name(code)} ({code})'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải model."
            )
        json_cand = model_path.with_name(f"{model_path.name}.json")
        if not json_cand.is_file():
            json_cand2 = model_path.with_suffix(".json")
            if not json_cand2.is_file():
                raise TTSError(
                    f"Thiếu file cấu hình '{json_cand.name}' của model PiperTTS '{model_path.stem}'. "
                    "Vui lòng vào Cài đặt → API → Giọng đọc để tải lại model."
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

        target = Path(out_path).resolve()
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
                espeak_dir = binary.parent / "espeak-ng-data"
                if espeak_dir.is_dir():
                    cmd.extend(["--espeak_data", str(espeak_dir)])
                proc = subprocess.run(
                    cmd,
                    input=chunk.encode("utf-8"),
                    cwd=str(binary.parent),
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
                sample_rate=22050,
                chunks=len(chunks),
                characters=len(clean_text),
                voice=model_path.stem,
                engine="piper",
                backend="onnx",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _extract_clean_stderr(raw: bytes) -> str:
    """Filter out benign Python warnings and return clean error message."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        if "FutureWarning" in line or "google.api_core" in line or "Please upgrade to the latest Python" in line:
            continue
        lines.append(line)
    clean = "\n".join(lines).strip()
    return clean[-500:] if len(clean) > 500 else (clean or text[:400])


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

    def installed_models(self) -> set[str]:
        """Return set of model IDs that are physically downloaded on disk with valid weights."""
        if not self.root.is_dir():
            return set()
        installed: set[str] = set()
        models_dir = self.root / "models"
        if not models_dir.is_dir():
            return installed

        def _has_weights(p: Path, exts: Tuple[str, ...]) -> bool:
            if not p.is_dir():
                return False
            for ext in exts:
                for f in p.glob(ext):
                    if f.is_file() and f.stat().st_size > 0:
                        return True
            return False

        # F5TTS_v1_Base: model_1250000.safetensors or any .safetensors > 1KB
        if _has_weights(models_dir / "F5TTS_v1_Base", ("*.safetensors", "*.pt")):
            installed.add("F5TTS_v1_Base")

        # E2TTS_Base: model_1200000.safetensors or any .safetensors > 1KB
        if _has_weights(models_dir / "E2TTS_Base", ("*.safetensors", "*.pt")):
            installed.add("E2TTS_Base")

        # F5TTS_Vietnamese: model_last.pt or any .pt/.safetensors > 1KB
        if _has_weights(models_dir / "F5TTS_Vietnamese", ("*.pt", "*.safetensors")):
            installed.add("F5TTS_Vietnamese")

        # Generic fallback for any other subfolder containing weights > 1KB
        for d in models_dir.iterdir():
            if d.is_dir() and d.name not in ("vocos-mel-24khz",):
                if _has_weights(d, ("*.safetensors", "*.pt", "*.ckpt")):
                    installed.add(d.name)
        return installed

    def available_voices(self, language: str = "", gender: str = "") -> List[Tuple[str, str]]:
        """Return Male (Nam) and Female (Nữ) voices for F5-TTS / E2-TTS filtered by language & gender."""
        code = normalize_language(language) if language else ""
        g_filter = str(gender or "").strip().lower()
        results: List[Tuple[str, str]] = []
        for label, vid, v_gender, lang_scope in F5_VOICES_CATALOG:
            if g_filter and v_gender.lower() != g_filter:
                continue
            if code == "vi" and lang_scope != "vi":
                continue
            if code and code != "vi" and lang_scope == "vi":
                continue
            results.append((label, vid))
        return results

    def installed_voices(self, language: str = "", gender: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) actually installed inside F5TTS directory."""
        installed_mdls = self.installed_models()
        if not installed_mdls:
            return []
        code = normalize_language(language) if language else ""
        g_filter = str(gender or "").strip().lower()
        results: List[Tuple[str, str]] = []
        for label, vid, v_gender, lang_scope in F5_VOICES_CATALOG:
            model_key = vid.split("::")[0]
            if model_key not in installed_mdls:
                continue
            if g_filter and v_gender.lower() != g_filter:
                continue
            if code == "vi" and lang_scope != "vi":
                continue
            if code and code != "vi" and lang_scope == "vi":
                continue
            results.append((label, vid))
        return results

    def find_checkpoint(self, model_key: str) -> Optional[Tuple[Path, Optional[Path]]]:
        """Locate (checkpoint_path, vocab_path) for model_key."""
        models_dir = self.root / "models"
        target_dir = models_dir / model_key
        if not target_dir.is_dir():
            return None
        ckpts = (
            list(target_dir.glob("*.safetensors"))
            or list(target_dir.glob("*.pt"))
            or list(target_dir.glob("*.ckpt"))
        )
        if not ckpts:
            return None
        ckpt_file = ckpts[0]
        # Vocab file in target_dir or fallback to F5TTS_v1_Base/vocab.txt
        vocab_files = list(target_dir.glob("vocab.txt")) or list(target_dir.glob("*.txt"))
        if vocab_files:
            vocab_file = vocab_files[0]
        else:
            fallback_vocab = models_dir / "F5TTS_v1_Base" / "vocab.txt"
            vocab_file = fallback_vocab if fallback_vocab.is_file() else None
        return ckpt_file, vocab_file

    def check_ready(self, language: str = "", model_key: str = "") -> Tuple[bool, str]:
        has_gpu, gpu_desc = detect_gpu_status()
        if not has_gpu:
            return (
                False,
                f"F5-TTS / E2-TTS yêu cầu GPU NVIDIA (CUDA) để hoạt động ({gpu_desc}).",
            )
        installed_mdls = self.installed_models()
        if not installed_mdls:
            return (
                False,
                f"Chưa cài đặt model F5-TTS / E2-TTS nào trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → F5-TTS / E2-TTS để tải model.",
            )
        if model_key and model_key not in installed_mdls:
            return (
                False,
                f"Chưa tải model F5-TTS / E2-TTS '{model_key}' trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → F5-TTS / E2-TTS để tải model.",
            )
        code = normalize_language(language) if language else ""
        if code == "vi" and not model_key and "F5TTS_Vietnamese" not in installed_mdls:
            return (
                False,
                f"Chưa tải model F5-TTS Tiếng Việt (ViVoice Fine-tuned) trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → F5-TTS / E2-TTS để tải model Tiếng Việt.",
            )
        return True, f"F5-TTS / E2-TTS đã sẵn sàng ({gpu_desc})."

    def ensure_voice_sample(self, speaker_name: str) -> None:
        """Tự động chuẩn bị file mẫu giọng tham chiếu (.wav 24kHz mono và .txt) cho speaker_name."""
        voices_dir = self.root / "voices"
        voices_dir.mkdir(parents=True, exist_ok=True)
        wav_file = voices_dir / f"{speaker_name}.wav"
        txt_file = voices_dir / f"{speaker_name}.txt"
        if wav_file.is_file() and wav_file.stat().st_size > 1000:
            return
        sample_info = F5_REF_SAMPLES.get(speaker_name)
        if not sample_info:
            return
        sample_text = sample_info.get("text", "")
        edge_v = sample_info.get("edge_voice", "en-US-EmmaMultilingualNeural")
        txt_file.write_text(sample_text, encoding="utf-8")
        tmp_mp3 = voices_dir / f"{speaker_name}.tmp.mp3"
        try:
            cmd_edge = ["edge-tts", "--voice", edge_v, "--text", sample_text, "--write-media", str(tmp_mp3)]
            subprocess.run(cmd_edge, capture_output=True, timeout=30, creationflags=_CREATE_NO_WINDOW)
            if tmp_mp3.is_file() and tmp_mp3.stat().st_size > 0:
                subprocess.run(
                    [self.ffmpeg, "-y", "-i", str(tmp_mp3), "-ar", "24000", "-ac", "1", str(wav_file)],
                    capture_output=True,
                    timeout=30,
                    creationflags=_CREATE_NO_WINDOW,
                )
        except Exception as exc:
            logger.warning("Không thể tự sinh voice sample cho F5-TTS (%s): %s", speaker_name, exc)
        finally:
            if tmp_mp3.exists():
                tmp_mp3.unlink()

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
        installed_mdls = self.installed_models()
        default_voice = (
            "F5TTS_Vietnamese::Ngọc Ánh"
            if (code == "vi" and "F5TTS_Vietnamese" in installed_mdls)
            else "F5TTS_v1_Base::Emma_Female"
        )
        raw_voice = str(voice or default_voice).strip()
        if "::" in raw_voice:
            chosen_model, speaker_name = raw_voice.split("::", 1)
        else:
            chosen_model = raw_voice or "F5TTS_v1_Base"
            speaker_name = "Emma_Female"

        ok, reason = self.check_ready(language=code if not chosen_model else "", model_key=chosen_model)
        if not ok:
            raise TTSError(reason)

        ckpt_info = self.find_checkpoint(chosen_model)
        if ckpt_info is None:
            raise TTSError(
                f"Chưa tải model F5-TTS / E2-TTS '{chosen_model}' trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → F5-TTS / E2-TTS để tải model."
            )
        ckpt_path, vocab_path = ckpt_info

        cli_cmd: List[str]
        cli_bin = shutil.which("f5-tts_infer-cli")
        if cli_bin:
            cli_cmd = [cli_bin]
        elif (Path(sys.prefix) / "Scripts" / "f5-tts_infer-cli.exe").is_file():
            cli_cmd = [str(Path(sys.prefix) / "Scripts" / "f5-tts_infer-cli.exe")]
        elif (Path(sys.executable).parent / "Scripts" / "f5-tts_infer-cli.exe").is_file():
            cli_cmd = [str(Path(sys.executable).parent / "Scripts" / "f5-tts_infer-cli.exe")]
        else:
            cli_cmd = [sys.executable, "-m", "f5_tts.infer.infer_cli"]

        backend_pref = ""
        if isinstance(self.settings.get("tts"), dict):
            backend_pref = str(self.settings["tts"].get("backend", "auto")).lower()
        if backend_pref in ("cpu", "onnx"):
            device = "cpu"
        elif backend_pref == "gpu":
            device = "cuda" if is_cuda_available() else "cpu"
        else:
            device = "cuda" if is_cuda_available() else "cpu"

        if message_cb:
            message_cb(f"F5-TTS ({device.upper()} · {chosen_model}): 1 đoạn, giọng {speaker_name}")

        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_f5tts_", dir=str(target.parent)))
        try:
            out_wav = temp_dir / "f5_out.wav"
            cmd = list(cli_cmd) + [
                "-m", chosen_model,
                "-p", str(ckpt_path),
                "-t", clean_text,
                "-o", str(temp_dir),
                "-w", out_wav.name,
                "--device", device,
            ]
            if vocab_path and vocab_path.is_file():
                cmd.extend(["-v", str(vocab_path)])
            eff_speed = float(speed or self.default_speed or 1.0)
            if eff_speed != 1.0:
                cmd.extend(["--speed", str(round(eff_speed, 2))])

            voices_dir = self.root / "voices"
            ref_wav = voices_dir / f"{speaker_name}.wav"
            ref_txt = voices_dir / f"{speaker_name}.txt"
            if not ref_wav.is_file():
                self.ensure_voice_sample(speaker_name)

            if ref_wav.is_file():
                cmd.extend(["-r", str(ref_wav)])
                ref_text = ""
                if ref_txt.is_file():
                    try:
                        ref_text = ref_txt.read_text(encoding="utf-8").strip()
                    except Exception:
                        ref_text = ""
                if not ref_text and speaker_name in F5_REF_SAMPLES:
                    ref_text = F5_REF_SAMPLES[speaker_name]["text"]
                if ref_text:
                    cmd.extend(["-s", ref_text])

            env = os.environ.copy()
            env["PYTHONWARNINGS"] = "ignore"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["WANDB_MODE"] = "disabled"
            env["WANDB_SILENT"] = "true"

            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,
                creationflags=_CREATE_NO_WINDOW,
                env=env,
            )
            if proc.returncode != 0 or not out_wav.is_file() or out_wav.stat().st_size == 0:
                err = _extract_clean_stderr(proc.stderr)
                raise TTSError(f"F5-TTS tạo audio thất bại (mã {proc.returncode}): {err}")

            if progress_cb:
                progress_cb(100)

            if target.suffix.lower() == ".wav":
                shutil.copy2(out_wav, target)
            else:
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-i", str(out_wav),
                    "-c:a", "libmp3lame",
                    "-b:a", self.bitrate,
                    str(target),
                ]
                res_ff = subprocess.run(
                    cmd_ff, capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW
                )
                if res_ff.returncode != 0 or not target.is_file():
                    err = res_ff.stderr.decode("utf-8", errors="replace")[:300]
                    raise TTSError(f"FFmpeg chuyển audio F5-TTS sang MP3 lỗi: {err}")

            dur = probe_duration(target)
            return SynthResult(
                path=target,
                duration=dur,
                sample_rate=24000,
                chunks=1,
                characters=len(clean_text),
                voice=raw_voice,
                engine="f5tts",
                backend=device,
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

    def installed_models(self) -> set[str]:
        """Return set of model IDs that are physically downloaded on disk."""
        if not self.root.is_dir():
            return set()
        installed: set[str] = set()
        models_dir = self.root / "models"
        if not models_dir.is_dir():
            return installed

        # XTTS_v2 / XTTS_v2_Base
        for cand in ("XTTS_v2", "XTTS_v2_Base"):
            m_v2 = models_dir / cand
            f_v2 = m_v2 / "model.pth"
            if m_v2.is_dir() and f_v2.is_file() and f_v2.stat().st_size > 0:
                installed.add("XTTS_v2")
                installed.add("XTTS_v2_Base")

        # viXTTS
        m_vi = models_dir / "viXTTS"
        f_vi = m_vi / "model.pth"
        if m_vi.is_dir() and f_vi.is_file() and f_vi.stat().st_size > 0:
            installed.add("viXTTS")
        return installed

    def available_voices(self, language: str = "", gender: str = "") -> List[Tuple[str, str]]:
        """Return Male (Nam) and Female (Nữ) voices for Coqui XTTS-v2 filtered by language & gender."""
        code = normalize_language(language) if language else ""
        g_filter = str(gender or "").strip().lower()
        results: List[Tuple[str, str]] = []
        for label, vid, v_gender, lang_scope in XTTS_VOICES_CATALOG:
            if g_filter and v_gender.lower() != g_filter:
                continue
            if code == "vi" and lang_scope != "vi":
                continue
            if code and code != "vi" and lang_scope == "vi":
                continue
            results.append((label, vid))
        return results

    def installed_voices(self, language: str = "", gender: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) actually installed inside XTTSv2 directory."""
        installed_mdls = self.installed_models()
        if not installed_mdls:
            return []
        code = normalize_language(language) if language else ""
        g_filter = str(gender or "").strip().lower()
        results: List[Tuple[str, str]] = []
        for label, vid, v_gender, lang_scope in XTTS_VOICES_CATALOG:
            model_key = vid.split("::")[0]
            if model_key not in installed_mdls:
                continue
            if g_filter and v_gender.lower() != g_filter:
                continue
            if code == "vi" and lang_scope != "vi":
                continue
            if code and code != "vi" and lang_scope == "vi":
                continue
            results.append((label, vid))
        return results

    def find_model_dir(self, model_key: str) -> Optional[Path]:
        """Locate model directory containing model.pth and config.json."""
        models_dir = self.root / "models"
        aliases = [model_key]
        if model_key == "XTTS_v2_Base":
            aliases.append("XTTS_v2")
        elif model_key == "XTTS_v2":
            aliases.append("XTTS_v2_Base")
        for alias in aliases:
            cand = models_dir / alias
            if cand.is_dir() and (cand / "model.pth").is_file():
                return cand
        return None

    def check_ready(self, language: str = "", model_key: str = "") -> Tuple[bool, str]:
        has_gpu, gpu_desc = detect_gpu_status()
        if not has_gpu:
            return (
                False,
                f"Coqui XTTS-v2 yêu cầu GPU NVIDIA (CUDA) để hoạt động ({gpu_desc}).",
            )
        installed_mdls = self.installed_models()
        if not installed_mdls:
            return (
                False,
                f"Chưa cài đặt model Coqui XTTS-v2 nào trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → Coqui XTTS-v2 để tải model.",
            )
        if model_key:
            norm_key = "XTTS_v2" if model_key in ("XTTS_v2", "XTTS_v2_Base") else model_key
            if norm_key not in installed_mdls:
                return (
                    False,
                    f"Chưa tải model Coqui XTTS-v2 '{model_key}' trong thư mục '{self.root}'. "
                    "Vui lòng vào Cài đặt → API → Giọng đọc → Coqui XTTS-v2 để tải model.",
                )
        code = normalize_language(language) if language else ""
        if code == "vi" and not model_key and "viXTTS" not in installed_mdls:
            return (
                False,
                f"Chưa tải model Coqui XTTS-v2 Tiếng Việt (viXTTS) trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → Coqui XTTS-v2 để tải model Tiếng Việt.",
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
        clean_text = " ".join(str(text or "").split())
        if not clean_text:
            raise TTSError("Văn bản rỗng, không có gì để đọc.")

        code = normalize_language(language)
        installed_mdls = self.installed_models()
        default_voice = (
            "viXTTS::Ngọc Ánh"
            if (code == "vi" and "viXTTS" in installed_mdls)
            else "XTTS_v2::Claribel Dervla"
        )
        raw_voice = str(voice or default_voice).strip()
        if "::" in raw_voice:
            chosen_model, speaker_idx = raw_voice.split("::", 1)
        else:
            chosen_model = raw_voice or "XTTS_v2"
            speaker_idx = "Claribel Dervla"

        ok, reason = self.check_ready(language=code if not chosen_model else "", model_key=chosen_model)
        if not ok:
            raise TTSError(reason)

        model_dir = self.find_model_dir(chosen_model)
        if model_dir is None:
            raise TTSError(
                f"Chưa tải model Coqui XTTS-v2 '{chosen_model}' trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc → Coqui XTTS-v2 để tải model."
            )

        cli_cmd: List[str]
        cli_bin = shutil.which("tts")
        if cli_bin:
            cli_cmd = [cli_bin]
        elif (Path(sys.prefix) / "Scripts" / "tts.exe").is_file():
            cli_cmd = [str(Path(sys.prefix) / "Scripts" / "tts.exe")]
        elif (Path(sys.executable).parent / "Scripts" / "tts.exe").is_file():
            cli_cmd = [str(Path(sys.executable).parent / "Scripts" / "tts.exe")]
        else:
            cli_cmd = [sys.executable, "-m", "TTS.bin.synthesize"]

        backend_pref = ""
        if isinstance(self.settings.get("tts"), dict):
            backend_pref = str(self.settings["tts"].get("backend", "auto")).lower()
        if backend_pref in ("cpu", "onnx"):
            device = "cpu"
        elif backend_pref == "gpu":
            device = "cuda" if is_cuda_available() else "cpu"
        else:
            device = "cuda" if is_cuda_available() else "cpu"

        if message_cb:
            message_cb(f"Coqui XTTS-v2 ({device.upper()} · {chosen_model}): 1 đoạn, giọng {speaker_idx}")

        xtts_langs = ("en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl", "cs", "ar", "zh-cn", "hu", "ko", "ja", "hi")
        if chosen_model == "viXTTS":
            eff_lang = "vi"
        elif code in xtts_langs:
            eff_lang = code
        elif code == "zh":
            eff_lang = "zh-cn"
        else:
            eff_lang = "en"

        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_xtts_", dir=str(target.parent)))
        try:
            out_wav = temp_dir / "xtts_out.wav"
            cmd = list(cli_cmd) + [
                "--text", clean_text,
                "--model_path", str(model_dir),
                "--config_path", str(model_dir / "config.json"),
                "--speaker_idx", speaker_idx,
                "--language_idx", eff_lang,
                "--out_path", str(out_wav),
            ]
            if device == "cuda":
                cmd.append("--use_cuda")

            env = os.environ.copy()
            env["PYTHONWARNINGS"] = "ignore"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["WANDB_MODE"] = "disabled"
            env["WANDB_SILENT"] = "true"

            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,
                creationflags=_CREATE_NO_WINDOW,
                env=env,
            )
            if proc.returncode != 0 or not out_wav.is_file() or out_wav.stat().st_size == 0:
                err = _extract_clean_stderr(proc.stderr)
                raise TTSError(f"Coqui XTTS-v2 tạo audio thất bại (mã {proc.returncode}): {err}")

            if progress_cb:
                progress_cb(100)

            if target.suffix.lower() == ".wav":
                shutil.copy2(out_wav, target)
            else:
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-i", str(out_wav),
                    "-c:a", "libmp3lame",
                    "-b:a", self.bitrate,
                    str(target),
                ]
                res_ff = subprocess.run(
                    cmd_ff, capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW
                )
                if res_ff.returncode != 0 or not target.is_file():
                    err = res_ff.stderr.decode("utf-8", errors="replace")[:300]
                    raise TTSError(f"FFmpeg chuyển audio Coqui XTTS sang MP3 lỗi: {err}")

            dur = probe_duration(target)
            return SynthResult(
                path=target,
                duration=dur,
                sample_rate=24000,
                chunks=1,
                characters=len(clean_text),
                voice=raw_voice,
                engine="xtts",
                backend=device,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)



