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

    # Inner API tabs: Văn bản, Giọng đọc, Ảnh
    assert tab.api_tabs.count() == 3
    assert tab.api_tabs.tabText(0) == "Văn bản"
    assert tab.api_tabs.tabText(1) == "Giọng đọc"
    assert tab.api_tabs.tabText(2) == "Ảnh"

    # Verify Text AI: Gemini Flash + Cloudflare Workers AI (Text)
    assert hasattr(tab, "card_gemini")
    assert hasattr(tab, "card_cloudflare_text")
    assert not hasattr(tab, "card_deepseek")
    assert not hasattr(tab, "card_openai")

    # Verify TTS: Edge-TTS (multilingual), H2MTTS (Google Gemini TTS removed)
    assert hasattr(tab, "card_edge_tts")
    assert hasattr(tab, "card_h2mtts")
    assert not hasattr(tab, "card_gemini_tts")
    assert not hasattr(tab, "card_elevenlabs")
    assert not hasattr(tab, "card_openai_audio")

    # Verify Media: Cloudflare Workers AI (Image) (Google Gemini Image & Veo removed)
    assert hasattr(tab, "card_cloudflare_image")
    assert not hasattr(tab, "card_gemini_image")
    assert not hasattr(tab, "card_veo")
    assert not hasattr(tab, "card_fal")
    assert not hasattr(tab, "card_seedance_fal")
    assert not hasattr(tab, "card_seedance_comet")
    assert not hasattr(tab, "card_seedance_piapi")
    assert not hasattr(tab, "card_kling_fal")

    # Verify titles, tags, and button texts
    assert tab.card_gemini.title_text == "Gemini"
    assert tab.card_gemini.tag_text == ""
    assert tab.card_gemini.btn_save.text() == "Lưu key"
    assert tab.card_gemini.btn_check.text() == "Check Connection"
    assert not hasattr(tab.card_gemini, "btn_test")
    assert tab.card_cloudflare_text.title_text == "Cloudflare Worker AI"
    assert tab.card_cloudflare_image.title_text == "Cloudflare Worker AI"
    assert tab.card_cloudflare_text.tag_text == ""
    assert tab.card_cloudflare_image.tag_text == ""
    assert tab.card_cloudflare_text.btn_save.text() == "Lưu key"
    assert tab.card_cloudflare_text.btn_check.text() == "Check Connection"
    assert tab.card_cloudflare_image.btn_save.text() == "Lưu key"
    assert tab.card_cloudflare_image.btn_check.text() == "Check Connection"

    # Verify model counts
    assert tab.card_gemini.model_combo.count() >= 10
    assert tab.card_cloudflare_text.model_combo.count() >= 5
    assert tab.card_edge_tts.model_combo.count() >= 10  # Multilingual Edge-TTS voices
    assert tab.card_cloudflare_image.model_combo.count() >= 8
    assert tab.card_gemini.model_combo.isEditable()
    assert tab.card_cloudflare_image.model_combo.isEditable()


def test_settings_tab_load_apply_and_shared_keys(app, tmp_path: Path) -> None:
    tab = SettingsTab()

    settings = {
        "paths": {"h2mtts": "D:/test/h2mtts", "ffmpeg": "ffmpeg_custom", "ffprobe": "ffprobe_custom"},
        "app": {"retry": 3, "stop_on_error": True, "log_dir": "custom_logs"},
        "api": {
            "gemini_key": "AIzaSyTestGemini123",
            "gemini_model": "gemini-3.1-flash-lite",
            "cloudflare_account_id": "test_acc_123",
            "cloudflare_token": "dummy_token_12345",
            "cloudflare_text_model": "@cf/meta/llama-3.1-8b-instruct",
            "cloudflare_image_model": "@cf/black-forest-labs/flux-1-schnell",
            "edge_tts_voice": "en-US-JennyNeural",
        },
    }

    tab.load_from(settings)

    # Check that Gemini key was loaded
    assert tab.card_gemini.key_edit.text() == "AIzaSyTestGemini123"

    # Check that Cloudflare credentials were loaded into both Text and Image cards
    assert tab.card_cloudflare_text.get_account_id() == "test_acc_123"
    assert tab.card_cloudflare_text.get_token() == "dummy_token_12345"
    assert tab.card_cloudflare_image.get_account_id() == "test_acc_123"
    assert tab.card_cloudflare_image.get_token() == "dummy_token_12345"

    # Check models
    assert tab.card_gemini.get_model() == "gemini-3.1-flash-lite"
    assert tab.card_cloudflare_text.get_model() == "@cf/meta/llama-3.1-8b-instruct"
    assert tab.card_cloudflare_image.get_model() == "@cf/black-forest-labs/flux-1-schnell"
    assert tab.card_edge_tts.get_model() == "en-US-JennyNeural"

    # Check apply_to
    out = {}
    tab.apply_to(out)
    assert out["api"]["gemini_key"] == "AIzaSyTestGemini123"
    assert out["script"]["api_key"] == "AIzaSyTestGemini123"
    assert out["api"]["cloudflare_account_id"] == "test_acc_123"
    assert out["api"]["cloudflare_token"] == "dummy_token_12345"
    assert out["api"]["cloudflare_text_model"] == "@cf/meta/llama-3.1-8b-instruct"
    assert out["api"]["cloudflare_image_model"] == "@cf/black-forest-labs/flux-1-schnell"
    assert out["video"]["ai_image_model"] == "@cf/black-forest-labs/flux-1-schnell"
    assert out["api"]["edge_tts_voice"] == "en-US-JennyNeural"
    assert out["paths"]["ffmpeg"] == "ffmpeg_custom"
    assert out["app"]["retry"] == 3


def test_cloudflare_cards_two_way_sync(app) -> None:
    tab = SettingsTab()

    # Typing in Text card updates Image card
    tab.card_cloudflare_text.account_edit.setText("my_cf_account_99")
    tab.card_cloudflare_text.token_edit.setText("my_cf_token_88")
    assert tab.card_cloudflare_image.get_account_id() == "my_cf_account_99"
    assert tab.card_cloudflare_image.get_token() == "my_cf_token_88"

    # Typing in Image card updates Text card
    tab.card_cloudflare_image.account_edit.setText("updated_acc_77")
    tab.card_cloudflare_image.token_edit.setText("updated_tok_66")
    assert tab.card_cloudflare_text.get_account_id() == "updated_acc_77"
    assert tab.card_cloudflare_text.get_token() == "updated_tok_66"


def test_cloudflare_check_connection_behavior(app) -> None:
    tab = SettingsTab()

    # Empty inputs show missing warning
    tab.card_cloudflare_image.account_edit.setText("")
    tab.card_cloudflare_image.token_edit.setText("")
    tab.card_cloudflare_image.check_connection()
    assert "Cần Account ID & Token" in tab.card_cloudflare_image.status_badge.text()

    # Enter valid inputs and simulate success callback
    tab.card_cloudflare_image.account_edit.setText("25388766ccf568d13135fc0e5297599f")
    tab.card_cloudflare_image.token_edit.setText("dummy_token_sample")

    # Simulate discovery callback
    tab.card_cloudflare_image._on_models_discovered("cloudflare_image", [
        ("FLUX.1 [schnell] (@cf/black-forest-labs/flux-1-schnell)", "@cf/black-forest-labs/flux-1-schnell"),
        ("New Model (@cf/new/model)", "@cf/new/model"),
    ])
    assert tab.card_cloudflare_image.model_combo.findData("@cf/new/model") >= 0

    tab.card_cloudflare_image._on_check_done("cloudflare_image", True, "Kết nối thành công! (10 models)")
    assert "Hợp lệ" in tab.card_cloudflare_image.status_badge.text()


def test_h2mtts_huggingface_packages_and_detection(app, tmp_path: Path) -> None:
    from ui.tab_settings import H2M_PACKAGES, H2MTTSCard

    # 1. Verify Hugging Face packages are defined correctly
    pkg_repos = [p["repo"] for p in H2M_PACKAGES]
    assert "hoangmui410/H2M-TTS-v3-Turbo" in pkg_repos
    assert "hoangmui410/MOSS-Audio-Tokenizer-Nano" in pkg_repos
    assert "hoangmui410/H2M-Voice-Packs" in pkg_repos
    assert len(H2M_PACKAGES) == 3

    # 2. Test empty directory detection
    card = H2MTTSCard()
    card.path_edit.setText(str(tmp_path / "empty_h2m"))
    card.check_packages_status()
    for pkg_id in ("turbo", "moss", "voice_packs"):
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


def test_h2mtts_download_cancellation_realtime(app, tmp_path: Path) -> None:
    from ui.tab_settings import H2MDownloadWorker, H2MLogTqdm, H2MDownloadCancelled

    worker = H2MDownloadWorker(str(tmp_path), [{"id": "dummy", "name": "Dummy", "repo": "dummy/repo", "target_subpath": "dummy"}])
    worker.cancel()
    assert worker._is_cancelled is True

    # Test H2MLogTqdm detects cancellation
    if H2MLogTqdm is not None:
        H2MLogTqdm.cancel_check = lambda: True
        with pytest.raises(H2MDownloadCancelled):
            H2MLogTqdm(total=100)
        H2MLogTqdm.cancel_check = None


def test_gemini_key_and_switch_persistence(app) -> None:
    tab = SettingsTab()

    # Initially empty, switch is False
    assert tab.card_gemini.switch.isChecked() is False
    assert "Thiếu key" in tab.card_gemini.status_badge.text()

    # Type a key
    tab.card_gemini.key_edit.setText("AIzaSyTestKey123")
    assert tab.card_gemini.switch.isChecked() is True

    # Click 'Lưu key'
    tab.card_gemini.btn_save.click()
    assert tab.card_gemini.switch.isChecked() is True
    assert "Đã lưu key" in tab.card_gemini.status_badge.text()

    # Save to settings dict
    saved_settings = {}
    tab.apply_to(saved_settings)
    assert saved_settings["api"]["gemini_key"] == "AIzaSyTestKey123"
    assert saved_settings["api"]["gemini_enabled"] is True
    assert saved_settings["script"]["api_key"] == "AIzaSyTestKey123"

    # Simulate reopening app: create fresh SettingsTab and load saved settings
    fresh_tab = SettingsTab()
    fresh_tab.load_from(saved_settings)

    # Verify key and switch are restored and NOT turned off
    assert fresh_tab.card_gemini.key_edit.text() == "AIzaSyTestKey123"
    assert fresh_tab.card_gemini.switch.isChecked() is True
    assert "Đã kích hoạt" in fresh_tab.card_gemini.status_badge.text()
    assert "Thiếu key" not in fresh_tab.card_gemini.status_badge.text()


def test_h2mtts_and_piper_expand_collapse_and_packages(app, tmp_path: Path) -> None:
    from ui.tab_settings import H2MTTSCard, PiperTTSCard

    tab = SettingsTab()
    assert hasattr(tab, "card_h2mtts")
    assert hasattr(tab, "card_piper")

    # Test H2MTTSCard expand / collapse button
    h2_card = tab.card_h2mtts
    assert hasattr(h2_card, "btn_collapse")
    assert h2_card.is_expanded() is True
    assert not h2_card.body_widget.isHidden()
    h2_card.btn_collapse.click()
    assert h2_card.is_expanded() is False
    assert h2_card.body_widget.isHidden()
    h2_card.btn_collapse.click()
    assert h2_card.is_expanded() is True
    assert not h2_card.body_widget.isHidden()

    # Test PiperTTSCard expand / collapse button & package detection
    piper_card = PiperTTSCard()
    assert hasattr(piper_card, "btn_collapse")
    assert piper_card.is_expanded() is True
    piper_card.btn_collapse.click()
    assert piper_card.is_expanded() is False
    assert piper_card.body_widget.isHidden()
    piper_card.btn_collapse.click()
    assert piper_card.is_expanded() is True

    fake_piper = tmp_path / "fake_piper"
    (fake_piper / "models").mkdir(parents=True)
    (fake_piper / "piper.exe").write_text("bin")
    (fake_piper / "models" / "vi_VN-vais1000-medium.onnx").write_text("onnx")
    (fake_piper / "models" / "en_US-lessac-medium.onnx").write_text("onnx")

    piper_card.path_edit.setText(str(fake_piper))
    piper_card.check_packages_status()
    assert "Đã cài đủ" in piper_card.status_badge.text()


def test_dubbing_language_subtab_and_routing(app, tmp_path: Path) -> None:
    from core.language_profiles import LanguageProfile
    from core.piper_tts_engine import (
        PiperTTSEngine,
        resolve_tts_engine_for_language,
        resolve_voice_for_language_engine,
    )
    from core.tts_engine import TTSError
    from ui.tab_dubbing import DubbingTab

    dub_tab = DubbingTab()
    assert dub_tab.inner_tabs.count() == 3
    assert dub_tab.inner_tabs.tabText(0) == "Bảng đoạn"
    assert dub_tab.inner_tabs.tabText(1) == "Engine && giọng"
    assert dub_tab.inner_tabs.tabText(2) == "Ngôn ngữ"

    # Sync languages from Script tab profiles (only vi + added profiles)
    custom_profiles = [
        LanguageProfile("en", "prompt en", name="Tiếng Anh"),
        LanguageProfile("ja", "prompt ja", name="Tiếng Nhật"),
    ]
    dub_tab.sync_languages(custom_profiles)
    assert list(dub_tab.lang_engine_combos.keys()) == ["vi", "en", "ja"]
    assert "hi" not in dub_tab.lang_engine_combos

    # Check Vietnamese and English have H2MTTS / EdgeTTS / PiperTTS / F5-TTS & E2-TTS / Coqui XTTS-v2 options
    for lang_code in ("vi", "en"):
        combo = dub_tab.lang_engine_combos[lang_code]
        options = [combo.itemData(i) for i in range(combo.count())]
        assert options == ["h2mtts", "edge", "piper", "f5tts", "xtts"]

    # Point to an empty Piper directory so PiperTTS is not installed
    empty_piper = tmp_path / "empty_piper"
    empty_piper.mkdir(parents=True, exist_ok=True)
    dub_tab.load_from({"paths": {"piper": str(empty_piper)}})
    dub_tab.sync_languages(custom_profiles)

    # Set Vietnamese to PiperTTS -> should NOT crash with AttributeError, and should show warning message
    vi_combo = dub_tab.lang_engine_combos["vi"]
    vi_combo.setCurrentIndex(vi_combo.findData("piper"))
    assert "Chưa cài đặt" in dub_tab.lang_status_label.text() or "Chưa tải model" in dub_tab.lang_status_label.text()
    assert "Chưa cài đặt" in dub_tab.lang_voice_combos["vi"].currentText()

    en_combo = dub_tab.lang_engine_combos["en"]
    en_combo.setCurrentIndex(en_combo.findData("edge"))

    settings: dict = {"paths": {"piper": str(empty_piper)}}
    dub_tab.apply_to(settings)
    assert resolve_tts_engine_for_language(settings, "vi") == "piper"
    assert resolve_tts_engine_for_language(settings, "en") == "edge"
    assert resolve_voice_for_language_engine(settings, "vi", "piper") != ""

    # Verify PiperTTSEngine accepts string path or settings dict and raises clear TTSError when missing
    eng_from_str = PiperTTSEngine(str(empty_piper))
    ok, reason = eng_from_str.check_ready("vi")
    assert ok is False
    assert "Chưa cài đặt" in reason or "Chưa tải model" in reason
    with pytest.raises(TTSError, match="Chưa cài đặt PiperTTS|Chưa tải model"):
        eng_from_str.synthesize("Xin chào", tmp_path / "out.mp3", language="vi")

    # Reload into a fresh DubbingTab
    fresh_dub = DubbingTab()
    fresh_dub.sync_languages(custom_profiles)
    fresh_dub.load_from(settings)
    assert fresh_dub.lang_engine_combos["vi"].currentData() == "piper"
    assert fresh_dub.lang_engine_combos["en"].currentData() == "edge"


def test_full_edge_piper_catalog_search_and_tts_config_tab(app, tmp_path: Path) -> None:
    from core.piper_tts_engine import ALL_EDGE_VOICES, ALL_PIPER_MODELS
    from ui.tab_settings import EDGE_MULTILANG_VOICES, PIPER_PACKAGES, PiperTTSCard
    from ui.tab_tts import TTSConfigTab

    # 1. Edge TTS full catalog (300+ voices)
    assert len(ALL_EDGE_VOICES) >= 300
    assert len(EDGE_MULTILANG_VOICES) >= 300

    # 2. Piper TTS full catalog (180 models + 1 runtime) and live search next to 'Quét lại'
    assert len(ALL_PIPER_MODELS) >= 150
    assert len(PIPER_PACKAGES) >= 151
    piper_card = PiperTTSCard()
    assert hasattr(piper_card, "search_edit")
    assert not hasattr(piper_card, "btn_dl_all")
    assert "bắt buộc" in piper_card.btn_install.text()
    piper_card.search_edit.setText("vais1000")
    visible_rows = [
        pid for pid, w in piper_card.pkg_widgets.items()
        if not w["row"].isHidden()
    ]
    assert "piper_runtime" in visible_rows
    assert any("vais1000" in pid for pid in visible_rows)
    assert len(visible_rows) < 10

    piper_card.search_edit.setText("")
    all_visible = [
        pid for pid, w in piper_card.pkg_widgets.items()
        if not w["row"].isHidden()
    ]
    assert len(all_visible) == len(PIPER_PACKAGES)

    # 3. TTSConfigTab ("Engine & giọng") has Model, Chạy với, Nhịp đọc, Nghe thử giọng đọc
    tts_tab = TTSConfigTab()
    assert tts_tab.model_box.title() == "Model"
    assert tts_tab.run_with_box.title() == "Chạy với"
    assert tts_tab.pacing_box.title() == "Nhịp đọc"

    # Check Model providers (H2MTTS, EdgeTTS, PiperTTS, F5-TTS / E2-TTS, Coqui XTTS-v2)
    providers = [tts_tab.provider_combo.itemData(i) for i in range(tts_tab.provider_combo.count())]
    assert providers == ["h2mtts", "edge", "piper", "f5tts", "xtts"]

    # Check Chạy với options (Tự động, GPU, CPU)
    backends = [tts_tab.backend_combo.itemData(i) for i in range(tts_tab.backend_combo.count())]
    assert backends == ["auto", "gpu", "onnx"]

    # Switch to EdgeTTS -> populates all 322 Edge voices
    tts_tab.provider_combo.setCurrentIndex(tts_tab.provider_combo.findData("edge"))
    assert tts_tab.voice_combo.count() == len(ALL_EDGE_VOICES)
    assert tts_tab.region_combo.isHidden()

    # Switch to PiperTTS (empty dir) -> shows warning
    empty_piper2 = tmp_path / "empty_piper2"
    empty_piper2.mkdir(parents=True, exist_ok=True)
    tts_tab.load_from({"paths": {"piper": str(empty_piper2)}, "tts": {"provider": "piper", "backend": "gpu"}})
    assert tts_tab.provider_combo.currentData() == "piper"
    assert tts_tab.backend_combo.currentData() == "gpu"
    assert "Chưa cài đặt" in tts_tab.voice_combo.currentText()


def test_f5tts_e2tts_gpu_detection_enables_or_dims(app) -> None:
    from core.piper_tts_engine import set_gpu_override
    from ui.tab_dubbing import DubbingTab
    from ui.tab_settings import F5TTSCard, XTTSCard
    from ui.tab_tts import TTSConfigTab

    try:
        # 1. Simulate NO GPU -> F5TTSCard & XTTSCard are dimmed & disabled, combo items are disabled
        set_gpu_override(False, "Chỉ có CPU")
        f5_card = F5TTSCard()
        assert f5_card.is_gpu_enabled() is False
        assert f5_card.body_widget.isEnabled() is False
        assert f5_card._opacity_effect.opacity() < 1.0
        assert "Yêu cầu GPU" in f5_card.status_badge.text()

        xtts_card = XTTSCard()
        assert xtts_card.is_gpu_enabled() is False
        assert xtts_card.body_widget.isEnabled() is False
        assert xtts_card._opacity_effect.opacity() < 1.0
        assert "Yêu cầu GPU" in xtts_card.status_badge.text()

        tts_tab = TTSConfigTab()
        f5_idx = tts_tab.provider_combo.findData("f5tts")
        xtts_idx = tts_tab.provider_combo.findData("xtts")
        assert tts_tab.provider_combo.model().item(f5_idx).isEnabled() is False
        assert tts_tab.provider_combo.model().item(xtts_idx).isEnabled() is False

        dub_tab = DubbingTab()
        vi_combo = dub_tab.lang_engine_combos["vi"]
        vi_f5_idx = vi_combo.findData("f5tts")
        vi_xtts_idx = vi_combo.findData("xtts")
        assert vi_combo.model().item(vi_f5_idx).isEnabled() is False
        assert vi_combo.model().item(vi_xtts_idx).isEnabled() is False

        # 2. Simulate GPU present -> F5TTSCard & XTTSCard are enabled & normal opacity, combo items are enabled
        set_gpu_override(True, "NVIDIA RTX 4090 · 24.0 GB")
        f5_card.refresh_gpu_status()
        assert f5_card.is_gpu_enabled() is True
        assert f5_card.body_widget.isEnabled() is True
        assert f5_card._opacity_effect.opacity() == 1.0

        xtts_card.refresh_gpu_status()
        assert xtts_card.is_gpu_enabled() is True
        assert xtts_card.body_widget.isEnabled() is True
        assert xtts_card._opacity_effect.opacity() == 1.0

        tts_tab.refresh_gpu_state()
        assert tts_tab.provider_combo.model().item(f5_idx).isEnabled() is True
        assert tts_tab.provider_combo.model().item(xtts_idx).isEnabled() is True

        dub_tab.sync_languages()
        vi_combo2 = dub_tab.lang_engine_combos["vi"]
        vi_f5_idx2 = vi_combo2.findData("f5tts")
        vi_xtts_idx2 = vi_combo2.findData("xtts")
        assert vi_combo2.model().item(vi_f5_idx2).isEnabled() is True
        assert vi_combo2.model().item(vi_xtts_idx2).isEnabled() is True

        # 3. Check Male (Nam) & Female (Nữ) voices for F5-TTS / E2-TTS and Coqui XTTS-v2
        tts_tab.provider_combo.setCurrentIndex(tts_tab.provider_combo.findData("f5tts"))
        f5_all_labels = [tts_tab.voice_combo.itemText(i) for i in range(tts_tab.voice_combo.count())]
        assert any("Nữ" in lbl for lbl in f5_all_labels)
        assert any("Nam" in lbl for lbl in f5_all_labels)

        # Filter by Nữ
        tts_tab.gender_combo.setCurrentIndex(tts_tab.gender_combo.findData("Nữ"))
        f5_female_labels = [tts_tab.voice_combo.itemText(i) for i in range(tts_tab.voice_combo.count())]
        assert len(f5_female_labels) > 0
        assert all("Nữ" in lbl for lbl in f5_female_labels)

        # Filter by Nam on Coqui XTTS-v2
        tts_tab.provider_combo.setCurrentIndex(tts_tab.provider_combo.findData("xtts"))
        tts_tab.gender_combo.setCurrentIndex(tts_tab.gender_combo.findData("Nam"))
        xtts_male_labels = [tts_tab.voice_combo.itemText(i) for i in range(tts_tab.voice_combo.count())]
        assert len(xtts_male_labels) > 0
        assert all("Nam" in lbl for lbl in xtts_male_labels)

        # 4. Verify _PreviewWorker can be constructed via keyword arguments without TypeError
        from ui.tab_tts import _PreviewWorker
        worker = _PreviewWorker(
            {},
            "I am Hoang Quy Mui, tester of this program",
            provider="piper",
            voice="en_GB-alan-low",
            parent=tts_tab,
        )
        assert worker._provider == "piper"
        assert worker._voice == "en_GB-alan-low"
    finally:
        set_gpu_override(None)


def test_strict_model_filtering_and_no_edge_fallback(app, tmp_path) -> None:
    """Verify that only downloaded model checkpoints appear, and F5/XTTS never fall back to Edge."""
    from core.piper_tts_engine import F5TTSEngine, XTTSEngine, set_gpu_override
    from ui.tab_dubbing import DubbingTab
    from ui.tab_tts import TTSConfigTab

    try:
        set_gpu_override(True, "Quadro P2200 · 5.0 GB")

        # 1. Custom mock root with ONLY F5TTS_v1_Base (no Vietnamese, no E2-TTS)
        mock_f5 = tmp_path / "mock_f5"
        (mock_f5 / "models" / "F5TTS_v1_Base").mkdir(parents=True, exist_ok=True)
        (mock_f5 / "models" / "F5TTS_v1_Base" / "model_1250000.safetensors").write_text("dummy")

        f5_eng = F5TTSEngine({"paths": {"f5tts": str(mock_f5)}})
        installed = f5_eng.installed_voices()
        installed_ids = [vid for _, vid in installed]
        # Only F5TTS_v1_Base voices should exist
        assert len(installed) == 4
        assert all(vid.startswith("F5TTS_v1_Base::") for vid in installed_ids)
        # Vietnamese voices must NOT be returned!
        vi_voices = f5_eng.installed_voices("vi")
        assert len(vi_voices) == 0

        # In TTSConfigTab, loading this mock path should show only Emma, Clara, Liam, Ryan
        tts_tab = TTSConfigTab()
        tts_tab.load_from({"paths": {"f5tts": str(mock_f5)}, "tts": {"provider": "f5tts"}})
        f5_labels = [tts_tab.voice_combo.itemText(i) for i in range(tts_tab.voice_combo.count())]
        assert len(f5_labels) == 4
        assert not any("Ngọc Ánh" in lbl for lbl in f5_labels)
        assert not any("Thanh Hà" in lbl for lbl in f5_labels)

        # In DubbingTab, Vietnamese row with F5-TTS must show warning "Chưa tải model F5-TTS cho Tiếng Việt"
        dub_tab = DubbingTab()
        dub_tab.load_from({"paths": {"f5tts": str(mock_f5)}})
        dub_tab.lang_engine_combos["vi"].setCurrentIndex(dub_tab.lang_engine_combos["vi"].findData("f5tts"))
        vi_text = dub_tab.lang_voice_combos["vi"].currentText()
        assert "Chưa tải model F5-TTS cho Tiếng Việt" in vi_text

        # 2. Custom mock root with ONLY XTTS_v2 (no viXTTS)
        mock_xtts = tmp_path / "mock_xtts"
        (mock_xtts / "models" / "XTTS_v2").mkdir(parents=True, exist_ok=True)
        (mock_xtts / "models" / "XTTS_v2" / "model.pth").write_text("dummy")

        xtts_eng = XTTSEngine({"paths": {"xtts": str(mock_xtts)}})
        installed_xtts = xtts_eng.installed_voices()
        assert len(installed_xtts) == 14  # Only 14 studio speakers
        assert all(vid.startswith("XTTS_v2::") for _, vid in installed_xtts)
        # Vietnamese viXTTS voices must NOT be returned!
        assert len(xtts_eng.installed_voices("vi")) == 0

        # In DubbingTab, Vietnamese row with XTTS must show warning "Chưa tải model Coqui XTTS cho Tiếng Việt"
        dub_tab.load_from({"paths": {"xtts": str(mock_xtts)}})
        dub_tab.lang_engine_combos["vi"].setCurrentIndex(dub_tab.lang_engine_combos["vi"].findData("xtts"))
        vi_xtts_text = dub_tab.lang_voice_combos["vi"].currentText()
        assert "Chưa tải model Coqui XTTS cho Tiếng Việt" in vi_xtts_text
    finally:
        set_gpu_override(None)




