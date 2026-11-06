"""Language registry shared by TTS, subtitles, rendering and upload.

Each language maps to the pieces the pipeline needs: a display name, the
default edge-tts voice (used for every language but Vietnamese), the YouTube
``defaultAudioLanguage`` code and a subtitle font that has the right glyphs
(CJK needs a different font than Latin text).
"""
from __future__ import annotations

from typing import Dict, List, Optional

DEFAULT_LANGUAGE = "vi"

LANGUAGES: Dict[str, Dict[str, str]] = {
    "vi": {
        "name": "Tiếng Việt",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "youtube": "vi",
        "font": "Arial",
    },
    "en": {
        "name": "English",
        "edge_voice": "en-US-AriaNeural",
        "youtube": "en",
        "font": "Arial",
    },
    "zh": {
        "name": "中文 (Chinese)",
        "edge_voice": "zh-CN-XiaoxiaoNeural",
        "youtube": "zh-Hans",
        "font": "Microsoft YaHei",
    },
    "ja": {
        "name": "日本語 (Japanese)",
        "edge_voice": "ja-JP-NanamiNeural",
        "youtube": "ja",
        "font": "Yu Gothic",
    },
    "ko": {
        "name": "한국어 (Korean)",
        "edge_voice": "ko-KR-SunHiNeural",
        "youtube": "ko",
        "font": "Malgun Gothic",
    },
    "fr": {
        "name": "Français",
        "edge_voice": "fr-FR-DeniseNeural",
        "youtube": "fr",
        "font": "Arial",
    },
    "es": {
        "name": "Español",
        "edge_voice": "es-ES-ElviraNeural",
        "youtube": "es",
        "font": "Arial",
    },
    "th": {
        "name": "ไทย (Thai)",
        "edge_voice": "th-TH-PremwadeeNeural",
        "youtube": "th",
        "font": "Leelawadee UI",
    },
    "id": {
        "name": "Bahasa Indonesia",
        "edge_voice": "id-ID-GadisNeural",
        "youtube": "id",
        "font": "Arial",
    },
    "hi": {
        "name": "हिन्दी (Hindi)",
        "edge_voice": "hi-IN-SwaraNeural",
        "youtube": "hi",
        "font": "Nirmala UI",
    },
}

# Subtitle font files to try copying into the render temp dir (libass needs
# the file locally when it cannot reach system fonts).
_FONT_FILES: Dict[str, List[str]] = {
    "zh": ["msyh.ttc", "msyhbd.ttc", "simsun.ttc"],
    "ja": ["YuGothR.ttc", "meiryo.ttc", "msgothic.ttc"],
    "ko": ["malgun.ttf", "malgunbd.ttf"],
    "th": ["leelawui.ttf", "LeelawUI.ttf"],
    "hi": ["Nirmala.ttf", "NirmalaB.ttf"],
}


def normalize_language(value: object, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Coerce ``value`` (``"en"``, ``"en-US"``, ``"English"``…) to a known code."""
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    if raw in LANGUAGES:
        return raw
    primary = raw.replace("_", "-").split("-", 1)[0]
    if primary in LANGUAGES:
        return primary
    for code, info in LANGUAGES.items():
        if raw == info["name"].casefold():
            return code
    return fallback


def is_known(value: object) -> bool:
    return str(value or "").strip().lower() in LANGUAGES


def language_name(code: str) -> str:
    raw = str(code or "").strip()
    if not raw:
        return LANGUAGES[DEFAULT_LANGUAGE]["name"]
    norm = normalize_language(raw, "")
    if norm and norm in LANGUAGES:
        return LANGUAGES[norm]["name"]
    return raw.upper() if len(raw) <= 3 else raw.replace("_", " ").title()


def edge_voice(code: str) -> str:
    return LANGUAGES.get(normalize_language(code), LANGUAGES[DEFAULT_LANGUAGE])["edge_voice"]


def youtube_language(code: str) -> str:
    return LANGUAGES.get(normalize_language(code), LANGUAGES[DEFAULT_LANGUAGE])["youtube"]


def subtitle_font(code: str) -> str:
    return LANGUAGES.get(normalize_language(code), LANGUAGES[DEFAULT_LANGUAGE])["font"]


def font_candidates(code: str) -> List[str]:
    """Windows font files worth copying for ``code`` (empty for Latin scripts)."""
    return list(_FONT_FILES.get(normalize_language(code), []))


def language_choices() -> List[tuple[str, str]]:
    """``(code, label)`` pairs for combo boxes, Vietnamese first."""
    ordered = [DEFAULT_LANGUAGE] + [code for code in LANGUAGES if code != DEFAULT_LANGUAGE]
    return [(code, LANGUAGES[code]["name"]) for code in ordered]


def detect_suffix_language(stem: str, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Infer a language from a filename suffix like ``tap_01_en`` / ``bai-2_zh``."""
    base = str(stem or "").strip().lower().replace("-", "_")
    if "_" not in base:
        return fallback
    tail = base.rsplit("_", 1)[-1]
    return tail if tail in LANGUAGES else fallback


def normalize_language_code(value: object, fallback: str = DEFAULT_LANGUAGE) -> str:
    """Coerce ``value`` to a known code or preserve short custom codes (e.g. ``aa``, ``ca``, ``aas``)."""
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    norm = normalize_language(raw, "")
    if norm:
        return norm
    if raw.isalnum() and len(raw) <= 5:
        return raw
    return fallback


def resolve_language(metadata: Optional[dict], stem: str,
                     default: str = DEFAULT_LANGUAGE) -> str:
    """Language precedence: metadata > filename suffix > project default."""
    if isinstance(metadata, dict):
        explicit = metadata.get("language") or metadata.get("lang")
        if explicit:
            if is_known(explicit):
                return normalize_language(explicit)
            raw_exp = str(explicit).strip().lower()
            if raw_exp.isalnum() and len(raw_exp) <= 5:
                return raw_exp
    return detect_suffix_language(stem, normalize_language_code(default))
