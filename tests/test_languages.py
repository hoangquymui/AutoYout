"""Language registry: normalisation, suffix detection and font mapping."""
from __future__ import annotations

from core.languages import (
    DEFAULT_LANGUAGE,
    language_choices,
    normalize_language,
    resolve_language,
    detect_suffix_language,
    font_candidates,
    subtitle_font,
    youtube_language,
)


def test_normalize_known_and_variants() -> None:
    assert normalize_language("en") == "en"
    assert normalize_language("en-US") == "en"
    assert normalize_language("EN_us") == "en"
    assert normalize_language("中文 (Chinese)") == "zh"
    assert normalize_language("") == DEFAULT_LANGUAGE == "vi"
    assert normalize_language("nope") == "vi"
    assert normalize_language("nope", "en") == "en"


def test_suffix_detection() -> None:
    assert detect_suffix_language("bai_2_zh") == "zh"
    assert detect_suffix_language("tap-01-EN") == "en"
    assert detect_suffix_language("tap_01") == "vi"
    assert detect_suffix_language("tap_01", "fr") == "fr"
    assert detect_suffix_language("bai_en_extra") == "vi"


def test_resolve_precedence() -> None:
    assert resolve_language({"language": "ja"}, "x_en") == "ja"
    assert resolve_language({"lang": "ko"}, "x") == "ko"
    assert resolve_language({}, "x_en") == "en"
    assert resolve_language(None, "x", "th") == "th"
    assert resolve_language({"language": "??"}, "x_es") == "es"


def test_fonts_and_youtube_codes() -> None:
    assert subtitle_font("zh").startswith("Microsoft")
    assert subtitle_font("vi") == "Arial"
    assert "msyh.ttc" in font_candidates("zh")
    assert font_candidates("en") == []
    assert youtube_language("zh") == "zh-Hans"
    assert youtube_language("vi") == "vi"


def test_choices_start_with_vietnamese() -> None:
    choices = language_choices()
    assert choices[0][0] == "vi"
    assert all(len(item) == 2 for item in choices)
