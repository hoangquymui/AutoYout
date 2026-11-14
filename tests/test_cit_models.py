"""Tests for Supertonic 3 and Kokoro-82M CIT voice engines + routing + settings + UI integration."""
from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import patch

import pytest

from core.dubbing import CueJob, DubbingEngine
from core.kokoro_tts_engine import (
    KOKORO_DEFAULT_VOICES,
    KokoroTTSEngine,
    _voice_gender,
    _voice_lang,
)
from core.piper_tts_engine import (
    resolve_tts_engine_for_language,
    resolve_voice_for_language_engine,
)
from core.project_manager import ProjectItem, ProjectManager
from core.settings import (
    ROOT,
    default_kokoro_path,
    default_supertonic_path,
    load_settings,
    normalize_kokoro_path,
    normalize_supertonic_path,
)
from core.supertonic_tts_engine import (
    SUPERTONIC_LANGUAGES,
    SUPERTONIC_VOICE_IDS,
    SupertonicTTSEngine,
    _preprocess_supertonic_text,
)
from core.tts_engine import SynthResult


def test_settings_paths_and_voice_maps_for_cit_models():
    cfg = load_settings()
    assert "supertonic" in cfg["paths"]
    assert "kokoro" in cfg["paths"]
    assert isinstance(cfg["tts"].get("supertonic_voices"), dict)
    assert isinstance(cfg["tts"].get("kokoro_voices"), dict)
    assert "f5tts" not in cfg["paths"]
    assert "xtts" not in cfg["paths"]
    assert normalize_supertonic_path("").endswith("SupertonicTTS")
    assert normalize_kokoro_path("").endswith("KokoroTTS")
    assert default_supertonic_path().endswith("SupertonicTTS")
    assert default_kokoro_path().endswith("KokoroTTS")


def test_resolve_engine_and_voices_for_supertonic_and_kokoro():
    cfg = {
        "tts": {
            "language_engines": {"en": "kokoro", "ja": "supertonic", "fr": "kokoro"},
            "kokoro_voices": {"en": "bf_emma"},
            "supertonic_voices": {"ja": "M2"},
        }
    }
    assert resolve_tts_engine_for_language(cfg, "en") == "kokoro"
    assert resolve_tts_engine_for_language(cfg, "ja") == "supertonic"
    assert resolve_tts_engine_for_language(cfg, "fr") == "kokoro"
    assert resolve_voice_for_language_engine(cfg, "en", "kokoro") == "bf_emma"
    assert resolve_voice_for_language_engine(cfg, "fr", "kokoro") == KOKORO_DEFAULT_VOICES["fr"]
    assert resolve_voice_for_language_engine(cfg, "ja", "supertonic") == "M2"
    assert resolve_voice_for_language_engine(cfg, "ko", "supertonic") == "F1"


def test_supertonic_text_preprocess_nfkd_and_tags():
    indexer = [-1] * 65536
    for ch in "<ven>abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,!?/-\u0302\u0301":
        indexer[ord(ch)] = ord(ch)
    arr = _preprocess_supertonic_text("Tiếng Việt", "vi", indexer)
    assert arr.ndim == 2 and arr.shape[0] == 1 and arr.shape[1] > 4
    # Unsupported language falls back cleanly to <en> tag without crashing
    arr2 = _preprocess_supertonic_text("Hello", "xx", indexer)
    assert arr2.shape[1] > 4


def test_kokoro_voice_helpers_and_misaki_g2p():
    assert _voice_gender("af_heart") == "female"
    assert _voice_gender("am_adam") == "male"
    assert _voice_lang("ff_siwis") == "fr"
    assert _voice_lang("pf_dora") == "pt"
    assert _voice_lang("jf_alpha") == "ja"
    assert _voice_lang("zf_xiaobei") == "zh"

    eng = KokoroTTSEngine({})
    ja_ph = eng._phonemize_ja_zh("こんにちは世界", "ja")
    zh_ph = eng._phonemize_ja_zh("你好世界", "zh")
    assert isinstance(ja_ph, str) and len(ja_ph.strip()) > 0
    assert isinstance(zh_ph, str) and len(zh_ph.strip()) > 0


def test_supertonic_real_cached_model_synthesis(tmp_path: Path):
    eng = SupertonicTTSEngine(load_settings())
    if not eng.is_installed():
        pytest.skip("Supertonic 3 model not cached on this machine")
    try:
        assert len(SUPERTONIC_VOICE_IDS) == 10
        ok, msg = eng.check_ready("vi")
        assert ok, msg
        assert len(eng.available_voices("vi", "female")) == 5
        assert len(eng.available_voices("vi", "male")) == 5

        for lang, sample in (("vi", "Xin chào AutoYout."), ("en", "Hello world.")):
            out = tmp_path / f"st_{lang}.mp3"
            res = eng.synthesize(sample, out, language=lang, voice="F1")
            assert res.engine == "supertonic"
            assert res.sample_rate == 44100
            assert res.duration > 0.3
            assert out.is_file() and out.stat().st_size > 1000
            assert out.with_suffix(".srt").is_file()

        ok_th, _ = eng.check_ready("th")
        assert not ok_th
    finally:
        eng.close()


def test_kokoro_real_cached_model_synthesis_cpu_and_auto(tmp_path: Path):
    base_cfg = load_settings()
    cpu_cfg = copy.deepcopy(base_cfg)
    cpu_cfg["tts"]["backend"] = "cpu"
    eng_cpu = KokoroTTSEngine(cpu_cfg)
    if not eng_cpu.is_installed():
        pytest.skip("Kokoro-82M model not cached on this machine")
    try:
        ok, msg = eng_cpu.check_ready("en")
        assert ok, msg
        assert len(eng_cpu.available_voices("en", "female")) > 0
        assert len(eng_cpu.available_voices("fr")) > 0
        assert len(eng_cpu.available_voices("pt")) > 0

        out_cpu = tmp_path / "kk_cpu.mp3"
        res_cpu = eng_cpu.synthesize("Hello from Kokoro ONNX.", out_cpu, language="en", voice="af_heart")
        assert res_cpu.engine == "kokoro"
        assert res_cpu.backend == "cpu"
        assert res_cpu.sample_rate == 24000
        assert res_cpu.duration > 0.3
        assert out_cpu.is_file() and out_cpu.stat().st_size > 1000
        assert out_cpu.with_suffix(".srt").is_file()
    finally:
        eng_cpu.close()

    auto_cfg = copy.deepcopy(base_cfg)
    auto_cfg["tts"]["backend"] = "auto"
    eng_auto = KokoroTTSEngine(auto_cfg)
    try:
        out_auto = tmp_path / "kk_auto.wav"
        res_auto = eng_auto.synthesize("Bonjour AutoYout.", out_auto, language="fr", voice="ff_siwis")
        assert res_auto.engine == "kokoro"
        assert res_auto.backend in ("cpu", "gpu")
        assert res_auto.duration > 0.3
        assert out_auto.is_file() and out_auto.stat().st_size > 1000
    finally:
        eng_auto.close()


def test_dubbing_routes_to_supertonic_and_kokoro(tmp_path: Path):
    for kind in ("supertonic", "kokoro"):
        proj_dir = tmp_path / f"proj_{kind}"
        (proj_dir / "script").mkdir(parents=True, exist_ok=True)
        (proj_dir / "script" / "demo.txt").write_text("Hello dubbing test", encoding="utf-8")
        mgr = ProjectManager(proj_dir)
        mgr.scan()
        mgr.update_item("demo", language="en")
        item = mgr.get_item("demo")
        cues = [CueJob(id=1, start=0.0, end=2.0, text="Hello dubbing test")]
        cfg = load_settings()
        cfg["tts"]["language_engines"] = {"en": kind}
        called = []

        class _FakeEngine:
            def __init__(self, settings):
                pass

            def synthesize(self, text, path, language="en", voice=None, speed=1.0, pause_cb=None, cancel_cb=None):
                called.append((kind, text, language, voice))
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"ID3" + b"\x00" * 64)
                return SynthResult(path=p, duration=1.0, engine=kind)

            def close(self):
                pass

        target_cls = "core.dubbing.SupertonicTTSEngine" if kind == "supertonic" else "core.dubbing.KokoroTTSEngine"
        with patch(target_cls, _FakeEngine):
            dub = DubbingEngine(cfg)
            dub.synthesize_item(mgr, item, cues)
        assert len(called) == 1
        assert called[0][0] == kind


def test_ui_roundtrip_supertonic_and_kokoro():
    from PySide6.QtWidgets import QApplication
    from core.language_profiles import LanguageProfile
    from ui.tab_dubbing import DubbingTab
    from ui.tab_settings import SettingsTab

    _app = QApplication.instance() or QApplication([])

    cfg = load_settings()
    cfg["tts"]["provider"] = "kokoro"
    cfg["tts"]["language"] = "en"
    cfg["tts"]["kokoro_voices"] = {"en": "af_heart"}
    cfg["tts"]["supertonic_voices"] = {"vi": "F2"}
    cfg["tts"]["language_engines"] = {"vi": "supertonic", "en": "kokoro"}

    dub_tab = DubbingTab()
    dub_tab.sync_languages([LanguageProfile("en", "prompt en", name="English")])
    dub_tab.load_from(cfg)

    tts_page = dub_tab.tts_page
    assert tts_page.provider_combo.findData("supertonic") >= 0
    assert tts_page.provider_combo.findData("kokoro") >= 0
    assert dub_tab.lang_engine_combos["vi"].currentData() == "supertonic"
    assert dub_tab.lang_engine_combos["en"].currentData() == "kokoro"

    out_cfg = copy.deepcopy(cfg)
    dub_tab.apply_to(out_cfg)
    assert out_cfg["tts"]["provider"] == "kokoro"
    assert out_cfg["tts"]["language_engines"]["vi"] == "supertonic"
    assert out_cfg["tts"]["language_engines"]["en"] == "kokoro"

    settings_tab = SettingsTab()
    settings_tab.load_from(cfg)
    assert hasattr(settings_tab, "card_supertonic")
    assert hasattr(settings_tab, "card_kokoro")
    assert hasattr(settings_tab.card_supertonic, "btn_dl_main")
    assert hasattr(settings_tab.card_supertonic, "btn_cancel_dl")
    assert hasattr(settings_tab.card_supertonic, "btn_delete")
    assert hasattr(settings_tab.card_kokoro, "btn_dl_main")
    assert hasattr(settings_tab.card_kokoro, "btn_dl_gpu")
    assert hasattr(settings_tab.card_kokoro, "btn_cancel_dl")
    assert hasattr(settings_tab.card_kokoro, "btn_delete")
    settings_tab.apply_to(out_cfg)
    assert "supertonic" in out_cfg["paths"]
    assert "kokoro" in out_cfg["paths"]

    dub_tab.close()
    dub_tab.deleteLater()
    settings_tab.close()
    settings_tab.deleteLater()


def test_engine_unsupported_language_and_backend_flags():
    from core.kokoro_tts_engine import (
        KOKORO_ONNX_REPO,
        KOKORO_TORCH_REPO,
        KokoroTTSEngine,
    )
    from core.supertonic_tts_engine import SUPERTONIC_REPO, SupertonicTTSEngine

    assert SUPERTONIC_REPO == "hoangmui410/Supertonic-3"
    assert KOKORO_ONNX_REPO == "hoangmui410/Kokoro-82M-ONNX"
    assert KOKORO_TORCH_REPO == "hoangmui410/Kokoro-82M-Torch"

    cfg = load_settings()
    assert KokoroTTSEngine(cfg).available_voices(language="vi") == []
    assert SupertonicTTSEngine(cfg).available_voices(language="zh") == []

    cfg_onnx = copy.deepcopy(cfg)
    cfg_onnx["tts"]["backend"] = "onnx"
    assert KokoroTTSEngine(cfg_onnx)._want_gpu() is False

    cfg_cpu = copy.deepcopy(cfg)
    cfg_cpu["tts"]["backend"] = "cpu"
    assert KokoroTTSEngine(cfg_cpu)._want_gpu() is False


def test_tts_tab_provider_language_backend_and_sample_preview():
    from PySide6.QtWidgets import QApplication
    from core.language_profiles import LanguageProfile
    from ui.tab_tts import TTSConfigTab

    _app = QApplication.instance() or QApplication([])

    cfg = load_settings()
    cfg["tts"]["provider"] = "h2mtts"
    cfg["tts"]["language"] = "vi"

    tab = TTSConfigTab()
    tab.sync_languages([
        LanguageProfile("vi", "", name="Tiếng Việt"),
        LanguageProfile("en", "", name="English"),
        LanguageProfile("ja", "", name="日本語 (Japanese)"),
        LanguageProfile("fr", "", name="Français"),
    ])
    tab.load_from(cfg)

    assert tab.language_combo.currentData() == "vi"
    assert tab.backend_combo.isEnabled() is True

    # Switching to kokoro must filter out unsupported 'vi' -> switch to 'en'
    idx_kokoro = tab.provider_combo.findData("kokoro")
    tab.provider_combo.setCurrentIndex(idx_kokoro)
    assert tab.language_combo.findData("vi") < 0
    assert tab.language_combo.currentData() == "en"
    assert tab.backend_combo.isEnabled() is True
    assert "voice preview from autoyout" in tab.preview_edit.toPlainText().lower()

    # Switching to supertonic must disable backend_combo (CPU ONNX) and allow 'vi'
    idx_sup = tab.provider_combo.findData("supertonic")
    tab.provider_combo.setCurrentIndex(idx_sup)
    assert tab.backend_combo.isEnabled() is False
    assert tab.language_combo.findData("vi") >= 0
    assert tab.language_combo.findData("zh") < 0

    # Switching to piper / edge also disables backend_combo
    tab.provider_combo.setCurrentIndex(tab.provider_combo.findData("piper"))
    assert tab.backend_combo.isEnabled() is False
    tab.provider_combo.setCurrentIndex(tab.provider_combo.findData("edge"))
    assert tab.backend_combo.isEnabled() is False

    # Language switch updates sample preview automatically if untouched
    idx_ja = tab.language_combo.findData("ja")
    tab.language_combo.setCurrentIndex(idx_ja)
    assert "音声プレビュー" in tab.preview_edit.toPlainText()

    # Infer language from voice id when 'Tất cả ngôn ngữ' ('') is selected
    assert tab._infer_lang_from_voice("kokoro", "af_heart") == "en"
    assert tab._infer_lang_from_voice("kokoro", "jf_alpha") == "ja"
    assert tab._infer_lang_from_voice("piper", "ja_JP-hi_fi_captain-medium") == "ja"
    assert tab._infer_lang_from_voice("edge", "fr-FR-DeniseNeural") == "fr"

    tab.close()
    tab.deleteLater()
