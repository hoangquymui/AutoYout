"""Unit tests for SettingsTab: General options, system paths, and Gemini API services."""
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

    # Verify Text AI: only Gemini Flash
    assert hasattr(tab, "card_gemini")
    assert not hasattr(tab, "card_deepseek")
    assert not hasattr(tab, "card_openai")

    # Verify TTS: Gemini TTS, Edge-TTS, H2MTTS (ElevenLabs and OpenAI removed)
    assert hasattr(tab, "card_gemini_tts")
    assert hasattr(tab, "card_edge_tts")
    assert hasattr(tab, "card_h2mtts")
    assert not hasattr(tab, "card_elevenlabs")
    assert not hasattr(tab, "card_openai_audio")

    # Verify Media AI: Google Gemini Image and Google Veo
    assert hasattr(tab, "card_gemini_image")
    assert hasattr(tab, "card_veo")
    assert not hasattr(tab, "card_fal")
    assert not hasattr(tab, "card_seedance_fal")
    assert not hasattr(tab, "card_seedance_comet")
    assert not hasattr(tab, "card_seedance_piapi")
    assert not hasattr(tab, "card_kling_fal")

    # Verify model counts matching rate limit
    assert tab.card_gemini.model_combo.count() >= 10
    assert tab.card_gemini_tts.model_combo.count() >= 8
    assert tab.card_gemini_image.model_combo.count() >= 5
    assert tab.card_veo.model_combo.count() >= 3
    assert tab.card_gemini.model_combo.isEditable()


def test_settings_tab_load_apply_and_shared_keys(app, tmp_path: Path) -> None:
    tab = SettingsTab()

    settings = {
        "paths": {"h2mtts": "D:/test/h2mtts", "ffmpeg": "ffmpeg_custom", "ffprobe": "ffprobe_custom"},
        "app": {"retry": 3, "stop_on_error": True, "log_dir": "custom_logs"},
        "api": {
            "gemini_key": "AIzaSyTestGemini123",
            "gemini_model": "gemini-3.1-flash-lite",
            "gemini_tts_model": "gemini-2.5-flash-tts",
            "gemini_image_model": "nano-banana-2",
            "veo_model": "veo-3-fast-generate",
        },
    }

    tab.load_from(settings)

    # Check that Gemini key was loaded
    assert tab.card_gemini.key_edit.text() == "AIzaSyTestGemini123"

    # Check that shared cards resolve get_key() from card_gemini
    assert tab.card_gemini_tts.get_key() == "AIzaSyTestGemini123"
    assert tab.card_gemini_image.get_key() == "AIzaSyTestGemini123"
    assert tab.card_veo.get_key() == "AIzaSyTestGemini123"

    # Check shared key statuses
    assert "Dùng chung" in tab.card_gemini_tts.status_badge.text()
    assert "Dùng chung" in tab.card_gemini_image.status_badge.text()
    assert "Dùng chung" in tab.card_veo.status_badge.text()

    # Check model resolution
    assert tab.card_gemini.get_model() == "gemini-3.1-flash-lite"
    assert tab.card_gemini_tts.get_model() == "gemini-2.5-flash-tts"
    assert tab.card_gemini_image.get_model() == "nano-banana-2"
    assert tab.card_veo.get_model() == "veo-3-fast-generate"

    # Check custom editable model
    tab.card_gemini.set_model("custom-experimental-model")
    assert tab.card_gemini.get_model() == "custom-experimental-model"

    # Check apply_to
    out = {}
    tab.apply_to(out)
    assert out["api"]["gemini_key"] == "AIzaSyTestGemini123"
    assert out["script"]["api_key"] == "AIzaSyTestGemini123"
    assert out["video"]["ai_api_key"] == "AIzaSyTestGemini123"
    assert out["api"]["gemini_model"] == "custom-experimental-model"
    assert out["paths"]["ffmpeg"] == "ffmpeg_custom"
    assert out["app"]["retry"] == 3


def test_check_connection_shared_key_behavior(app) -> None:
    tab = SettingsTab()

    # 1. When no key is entered in Gemini Flash, clicking check_connection on shared cards shows Cần key
    tab.card_gemini.key_edit.setText("")
    tab.card_gemini_tts.check_connection()
    assert "Cần key" in tab.card_gemini_tts.status_badge.text()
    tab.card_gemini_image.check_connection()
    assert "Cần key" in tab.card_gemini_image.status_badge.text()

    # 2. When key is entered, get_key resolves correctly
    tab.card_gemini.key_edit.setText("AIzaSyValidTestKey")
    assert tab.card_gemini_tts.get_key() == "AIzaSyValidTestKey"
    assert tab.card_gemini_image.get_key() == "AIzaSyValidTestKey"

    # 3. Successful check callback preserves "Dùng chung" status
    tab.card_gemini_tts._on_check_done("gemini_tts", True, "Kết nối thành công")
    assert "Hợp lệ (Dùng chung Gemini Flash)" in tab.card_gemini_tts.status_badge.text()

    tab.card_gemini_image._on_check_done("gemini_image", True, "Kết nối thành công")
    assert "Hợp lệ (Dùng chung Gemini Flash)" in tab.card_gemini_image.status_badge.text()

