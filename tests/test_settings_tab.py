"""Unit tests for SettingsTab: General options, system paths, and API & AI services."""
from __future__ import annotations

from pathlib import Path
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication
from ui.tab_settings import SettingsTab, detect_hardware


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_detect_hardware() -> None:
    specs = detect_hardware()
    assert "cpu" in specs and specs["cpu"]
    assert "ram" in specs and specs["ram"]
    assert "disk" in specs and specs["disk"]
    assert "rating" in specs


def test_settings_tab_structure_and_cards(app) -> None:
    tab = SettingsTab()

    # Outer tabs: Cài đặt chung & API
    assert tab.inner_tabs.count() == 2
    assert tab.inner_tabs.tabText(0) == "Cài đặt chung"
    assert tab.inner_tabs.tabText(1) == "API"

    # Inner API tabs: Văn bản AI, Giọng đọc (TTS), Ảnh / Video AI
    assert tab.api_tabs.count() == 3
    assert tab.api_tabs.tabText(0) == "Văn bản AI"
    assert tab.api_tabs.tabText(1) == "Giọng đọc (TTS)"
    assert tab.api_tabs.tabText(2) == "Ảnh / Video AI"

    # Verify cards in Text AI: only Gemini Flash remains (Omnira, DeepSeek, OpenAI removed)
    assert hasattr(tab, "card_gemini")
    assert not hasattr(tab, "card_deepseek")
    assert not hasattr(tab, "card_openai")

    # Verify cards in TTS: Gemini TTS, ElevenLabs, Edge-TTS, H2MTTS (OpenAI Audio removed)
    assert hasattr(tab, "card_gemini_tts")
    assert hasattr(tab, "card_elevenlabs")
    assert hasattr(tab, "card_edge_tts")
    assert hasattr(tab, "card_h2mtts")
    assert not hasattr(tab, "card_openai_audio")

    # Verify cards in Media AI
    assert hasattr(tab, "card_gemini_image")
    assert hasattr(tab, "card_fal")
    assert hasattr(tab, "card_veo")
    assert hasattr(tab, "card_seedance_fal")
    assert hasattr(tab, "card_seedance_comet")
    assert hasattr(tab, "card_seedance_piapi")
    assert hasattr(tab, "card_kling_fal")


def test_settings_tab_load_apply_and_shared_keys(app, tmp_path: Path) -> None:
    tab = SettingsTab()

    settings = {
        "paths": {"h2mtts": "D:/test/h2mtts", "ffmpeg": "ffmpeg_custom", "ffprobe": "ffprobe_custom"},
        "app": {"retry": 3, "stop_on_error": True, "log_dir": "custom_logs"},
        "api": {
            "gemini_key": "AIzaSyTestGemini123",
            "fal_key": "fal_key_test_12345",
            "comet_key": "sk-comet-test",
            "piapi_key": "piapi-test-key",
        },
    }

    tab.load_from(settings)

    # Check that keys were loaded
    assert tab.card_gemini.key_edit.text() == "AIzaSyTestGemini123"
    assert tab.card_fal.key_edit.text() == "fal_key_test_12345"
    assert tab.card_seedance_comet.key_edit.text() == "sk-comet-test"
    assert tab.card_seedance_piapi.key_edit.text() == "piapi-test-key"

    # Check shared key statuses
    assert "Dùng chung" in tab.card_gemini_tts.status_badge.text()
    assert "Dùng chung" in tab.card_gemini_image.status_badge.text()
    assert "Dùng chung" in tab.card_veo.status_badge.text()
    assert "Dùng chung" in tab.card_seedance_fal.status_badge.text()
    assert "Dùng chung" in tab.card_kling_fal.status_badge.text()

    # Check apply_to
    out = {}
    tab.apply_to(out)
    assert out["api"]["gemini_key"] == "AIzaSyTestGemini123"
    assert out["script"]["api_key"] == "AIzaSyTestGemini123"
    assert out["video"]["ai_api_key"] == "AIzaSyTestGemini123"
    assert out["paths"]["ffmpeg"] == "ffmpeg_custom"
    assert out["app"]["retry"] == 3
