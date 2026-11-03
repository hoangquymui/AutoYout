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


def test_h2mtts_huggingface_packages_and_detection(app, tmp_path: Path) -> None:
    from ui.tab_settings import H2M_PACKAGES, H2MTTSCard

    # 1. Verify Hugging Face packages are defined correctly
    pkg_repos = [p["repo"] for p in H2M_PACKAGES]
    assert "hoangmui410/H2M-TTS-v3-Turbo" in pkg_repos
    assert "hoangmui410/MOSS-Audio-Tokenizer-Nano" in pkg_repos
    assert "hoangmui410/H2M-Voice-Packs" in pkg_repos
    assert "hoangmui410/H2M-TTS-v3-Nano" in pkg_repos
    assert "hoangmui410/runtime" in pkg_repos

    # 2. Test empty directory detection
    card = H2MTTSCard()
    card.path_edit.setText(str(tmp_path / "empty_h2m"))
    card.check_packages_status()
    for pkg_id in ("turbo", "moss", "voice_packs", "nano", "runtime"):
        assert card.pkg_widgets[pkg_id]["status_badge"].text() == "Chưa tải"
    assert "Chưa cài" in card.status_badge.text()

    # 3. Simulate installed files in fake directory
    fake_h2m = tmp_path / "fake_h2m"
    (fake_h2m / "models" / "H2M-TTS-v3-Turbo").mkdir(parents=True)
    (fake_h2m / "models" / "H2M-TTS-v3-Turbo" / "vieneu-v3-turbo-q8_0.gguf").write_text("dummy")
    (fake_h2m / "models" / "MOSS-Audio-Tokenizer-Nano").mkdir(parents=True)
    (fake_h2m / "models" / "MOSS-Audio-Tokenizer-Nano" / "config.json").write_text("{}")
    (fake_h2m / "voice-packs").mkdir(parents=True)
    (fake_h2m / "voice-packs" / "voice-catalog-v4.json").write_text("{}")

    card.path_edit.setText(str(fake_h2m))
    card.check_packages_status()
    assert card.pkg_widgets["turbo"]["status_badge"].text() == "✓ Đã có"
    assert card.pkg_widgets["moss"]["status_badge"].text() == "✓ Đã có"
    assert card.pkg_widgets["voice_packs"]["status_badge"].text() == "✓ Đã có"
    assert "Đã cài đủ" in card.status_badge.text()


def test_h2mtts_default_path_in_project(app) -> None:
    from core.settings import ROOT, default_h2mtts_path, load_settings
    from core.tts_engine import H2MLayout
    from ui.tab_settings import H2MTTSCard, SettingsTab

    default_path = default_h2mtts_path()
    assert str(ROOT).replace("\\", "/") in default_path.replace("\\", "/")
    assert "H2MTTS" in default_path

    # H2MTTSCard defaults to project path
    card = H2MTTSCard()
    assert card.path_edit.text() == default_path

    # SettingsTab defaults to project path
    tab = SettingsTab()
    assert tab.general_page.h2mtts_edit.text() == default_path
    assert tab.card_h2mtts.path_edit.text() == default_path

    # Legacy external path migration
    legacy_settings = {
        "paths": {"h2mtts": "D:/mui/f375/tts/H2MTTS"},
    }
    tab.load_from(legacy_settings)
    assert tab.general_page.h2mtts_edit.text() == default_path
    assert tab.card_h2mtts.path_edit.text() == default_path

    # Relative path loads into project path
    tab.load_from({"paths": {"h2mtts": "H2MTTS"}})
    assert tab.card_h2mtts.path_edit.text() == default_path

    # Path from another machine that does not exist resolves to default_path
    tab.load_from({"paths": {"h2mtts": "Z:/another_machine/folder/H2MTTS"}})
    assert tab.card_h2mtts.path_edit.text() == default_path

    # Saving settings when pointing to default_path stores portable 'H2MTTS'
    out_settings = {}
    tab.apply_to(out_settings)
    assert out_settings["paths"]["h2mtts"] in ("H2MTTS", "tts/H2MTTS")

    # H2MLayout resolves to project path
    layout = H2MLayout("")
    assert layout.root == Path(default_path)
    layout2 = H2MLayout("Z:/nonexistent/path")
    assert layout2.root == Path(default_path)


def test_h2mtts_download_worker_error_propagation_and_ui_handling(app, tmp_path: Path) -> None:
    from ui.tab_settings import H2M_PACKAGES, H2MTTSCard

    card = H2MTTSCard()
    card.path_edit.setText(str(tmp_path / "test_h2m"))

    # Simulate error status on one package
    card._on_pkg_status_changed("turbo", "error", "Lỗi tải")
    assert card.pkg_widgets["turbo"]["status_badge"].text() == "Lỗi tải"
    assert card.pkg_widgets["turbo"]["btn_dl"].text() == "Thử lại"

    # Simulate finish with errors
    errors = {"turbo": "Connection reset by peer"}
    card.check_packages_status(errors=errors)
    assert card.pkg_widgets["turbo"]["status_badge"].text() == "Lỗi tải"
    assert "Connection reset" in card.pkg_widgets["turbo"]["status_badge"].toolTip()

    # Cancel button changes to Đóng when done
    card.progress_container.setVisible(True)
    card.btn_cancel_dl.setText("Đóng")
    card._cancel_download_clicked()
    assert not card.progress_container.isVisible()


def test_h2mtts_download_worker_null_streams_safe(app, tmp_path: Path, monkeypatch) -> None:
    import sys
    from ui.tab_settings import H2MDownloadWorker

    worker = H2MDownloadWorker(str(tmp_path), [])
    # Set sys.stdout and sys.stderr to None to simulate windowed PyInstaller app
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    # Calling run on empty packages should complete cleanly without AttributeError
    finished_called = []
    worker.all_finished.connect(lambda ok, msg: finished_called.append((ok, msg)))
    worker.run()
    assert len(finished_called) == 1
    assert finished_called[0][0] is True


def test_console_window_receives_backend_streams_and_clear_action(app) -> None:
    from ui.console_window import ConsoleWindow
    from utils.logger import LogEmitter, QtStreamWriter

    cw = ConsoleWindow()
    cw.append_log("INFO", "H2MTTS model download starting...")
    assert "H2MTTS" in cw.console.view.toPlainText()

    # Test QtStreamWriter
    emitter = LogEmitter()
    received = []
    emitter.message.connect(lambda level, msg: received.append((level, msg)))
    writer = QtStreamWriter(emitter, level="INFO")
    writer.write("Processing line 1\nProcessing line 2\n")
    assert ("INFO", "Processing line 1") in received
    assert ("INFO", "Processing line 2") in received

    # Test clear
    cw._clear_logs()
    assert cw.console.view.toPlainText() == ""






