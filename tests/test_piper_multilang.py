"""Unit tests for Piper TTS multi-language support (English, Japanese, Chinese, Polish, Portuguese, Korean)."""
import json
import pytest
from pathlib import Path

from core.languages import normalize_language, language_name
from core.piper_tts_engine import (
    PiperTTSEngine,
    sanitize_piper_config,
    PIPER_DEFAULT_VOICES,
)


def test_piper_default_voices_mapping():
    """Verify that default Piper voices are mapped to native models for requested languages."""
    assert PIPER_DEFAULT_VOICES["vi"] == "vi_VN-vais1000-medium"
    assert PIPER_DEFAULT_VOICES["en"] == "en_US-lessac-medium"
    assert PIPER_DEFAULT_VOICES["zh"] == "zh_CN-huayan-medium"
    assert PIPER_DEFAULT_VOICES["ja"] == "ja_JP-hi_fi_captain-medium"
    assert PIPER_DEFAULT_VOICES["ko"] == "ko_KR-kss-medium"
    assert PIPER_DEFAULT_VOICES["pt"] == "pt_BR-edresson-low"
    assert PIPER_DEFAULT_VOICES["pl"] == "pl_PL-bass-high"


def test_language_normalization_for_piper_languages():
    """Verify normalize_language handles all requested languages without fallback to 'vi'."""
    assert normalize_language("pl") == "pl"
    assert normalize_language("pl-PL") == "pl"
    assert normalize_language("pl_PL") == "pl"
    assert normalize_language("pt") == "pt"
    assert normalize_language("pt-BR") == "pt"
    assert normalize_language("pt_BR") == "pt"
    assert normalize_language("ja") == "ja"
    assert normalize_language("zh") == "zh"
    assert normalize_language("ko") == "ko"
    assert normalize_language("en") == "en"
    assert normalize_language("vi") == "vi"


def test_sanitize_piper_config_fixes_codepoints_and_voice(tmp_path: Path):
    """Verify sanitize_piper_config removes multi-character phonemes and changes voice 'zh' to 'cmn'."""
    cfg = tmp_path / "test.onnx.json"
    cfg.write_text(
        json.dumps({
            "phoneme_id_map": {
                "a": [1],
                "b": [2],
                "ar": [161],
                "aɪ": [162],
            },
            "phoneme_map": {
                "x": "y",
                "xy": "z",
            },
            "espeak": {
                "voice": "zh"
            }
        }),
        encoding="utf-8"
    )

    changed = sanitize_piper_config(cfg)
    assert changed is True

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "ar" not in data["phoneme_id_map"]
    assert "aɪ" not in data["phoneme_id_map"]
    assert "a" in data["phoneme_id_map"]
    assert "b" in data["phoneme_id_map"]
    assert "xy" not in data["phoneme_map"]
    assert data["phoneme_map"]["x"] == "y"
    assert data["espeak"]["voice"] == "cmn"


def test_japanese_hiragana_conversion():
    """Verify pykakasi converts Japanese Kanji to Hiragana so espeak does not spell out 'Chinese letter'."""
    import pykakasi
    kks = pykakasi.kakasi()
    text = "暗闇の中にいると感じるときは"
    conv = kks.convert(text)
    hira = "".join(item["hira"] for item in conv)
    assert hira == "くらやみのなかにいるとかんじるときは"
    assert "暗" not in hira
    assert "闇" not in hira


def test_piper_engine_synthesis_multi_languages(tmp_path: Path):
    """Verify PiperTTSEngine can synthesize test phrases for available installed models."""
    engine = PiperTTSEngine()
    binary = engine.find_binary()
    if not binary:
        pytest.skip("Piper binary not installed")

    test_samples = [
        ("en", "Testing Piper speech synthesis in English."),
        ("ja", "暗闇の中にいると感じるときは、自分が埋められたのではなく、種として植えられたのだと思い出してください。"),
        ("zh", "你好，欢迎使用 AutoYout 自动化视频制作。"),
        ("pl", "Dzień dobry, to jest test syntezy mowy."),
        ("pt", "Olá, este é um teste de síntese de voz."),
        ("ko", "안녕하세요, AutoYout 음성 합성 테스트입니다."),
    ]

    for lang, text in test_samples:
        model = engine.find_model(engine.voice_for(lang), lang)
        if not model:
            continue
        out_wav = tmp_path / f"test_{lang}.wav"
        res = engine.synthesize(text, out_wav, language=lang)
        assert res.duration > 0
        assert out_wav.is_file()
        assert out_wav.stat().st_size > 0
