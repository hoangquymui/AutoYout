"""MainWindow construction and tab wiring without a real display."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.main_window as main_window  # noqa: E402
from core.settings import load_settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def window(app, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main_window, "save_settings", lambda settings: None)
    settings = load_settings()
    settings["project"]["last_project"] = ""
    # user_settings.json reflects whatever the app was last run with; pin the
    # language defaults so the assertions below are deterministic.
    settings.setdefault("tts", {})["language"] = "vi"
    # A retired model also exercises the migration back to the current default.
    settings.setdefault("script", {})["model"] = "gemini-2.0-flash"
    win = main_window.MainWindow(settings)
    yield win
    win.close()
    win.deleteLater()
    QApplication.processEvents()


def test_window_builds_all_tabs(window) -> None:
    assert window.tabs.count() == 5
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert labels == ["Dự án && Dữ liệu", "Kịch bản",
                      "Lồng tiếng", "Video (FFmpeg)", "YouTube"]
    assert window.btn_tts.text().startswith("1. Lồng tiếng")
    assert window.btn_video.text().startswith("2. Tạo Video")
    assert window.btn_upload.text().startswith("3. Upload YouTube")
    assert "AUTO ALL" in window.btn_all.text()
    assert window.btn_pause.isEnabled() is False
    assert window.btn_stop.isEnabled() is False


def test_log_console_and_status(window) -> None:
    window.log_console.append("INFO", "xin chào")
    window.log_console.append("ERROR", "lỗi thử")
    text = window.log_console.view.toPlainText()
    assert "xin chào" in text and "lỗi thử" in text
    window._set_status("Đang chạy thử", running=True)
    assert "Đang chạy thử" in window.status_label.text()
    window.tts_tab.cancel_preview()


def test_theme_toggle_switches_and_persists(window) -> None:
    from ui import theme

    assert window.tabs.tabBar().drawBase() is False
    window._apply_theme("dark")
    assert theme.current_mode() == "dark"
    assert window.btn_theme.property("ayIconName") == "sun"

    window.btn_theme.click()

    assert theme.current_mode() == "light"
    assert window.settings["app"]["theme"] == "light"
    assert window.btn_theme.property("ayIconName") == "moon"
    assert "#F2F5FA" in QApplication.instance().styleSheet()
    window._apply_theme("dark")


def test_browser_login_button_follows_upload_method(window) -> None:
    tab = window.youtube_tab
    api_index = tab.method_combo.findData("api")
    browser_index = tab.method_combo.findData("browser")
    assert browser_index >= 0

    tab.method_combo.setCurrentIndex(browser_index)
    assert tab.browser_login_button.isHidden() is False
    assert tab.browser_status.isHidden() is False
    assert tab.browser_login_button.isEnabled() is True

    tab.method_combo.setCurrentIndex(api_index)
    assert tab.browser_login_button.isHidden() is True
    assert tab.browser_status.isHidden() is True


def test_youtube_tab_has_upload_and_thumbnail_pages(window) -> None:
    tab = window.youtube_tab
    labels = [tab.sub_tabs.tabText(i) for i in range(tab.sub_tabs.count())]
    assert labels == ["Đăng tải", "Hình thu nhỏ"]
    assert tab.sub_tabs.currentIndex() == 0
    assert tab.sub_tabs.widget(1) is tab.thumbnail_panel
    assert tab.thumbnail_panel.template_list.count() >= 2
    assert tab.thumbnail_panel.provider_combo.currentData() == "pollinations"


def test_ai_visual_mode_toggles_imagen_fields(window) -> None:
    tab = window.video_tab
    ai_index = tab.visual_combo.findData("ai")
    assert ai_index >= 0

    tab.visual_combo.setCurrentIndex(ai_index)
    assert tab.visual_stack.currentIndex() == ai_index

    tab.ai_mode_combo.setCurrentIndex(tab.ai_mode_combo.findData("manual"))
    assert tab.ai_imagen_box.isHidden() is True
    assert tab.ai_count_spin.isEnabled() is False

    tab.ai_mode_combo.setCurrentIndex(tab.ai_mode_combo.findData("imagen"))
    assert tab.ai_imagen_box.isHidden() is False
    assert tab.ai_count_spin.isEnabled() is True
    assert tab.ai_api_key_edit.echoMode() == tab.ai_api_key_edit.EchoMode.Password


def test_ai_settings_roundtrip(window) -> None:
    tab = window.video_tab
    tab.visual_combo.setCurrentIndex(tab.visual_combo.findData("ai"))
    tab.ai_mode_combo.setCurrentIndex(tab.ai_mode_combo.findData("imagen"))
    tab.ai_dir_edit.setText("D:/anh")
    tab.ai_count_spin.setValue(3)
    tab.ai_motion_check.setChecked(False)
    tab.ai_xfade_spin.setValue(0.7)
    tab.ai_api_key_edit.setText("test-key")

    tab.apply_to(window.settings)
    video = window.settings["video"]
    assert video["visual"] == "ai"
    assert video["ai_mode"] == "imagen"
    assert video["ai_dir"] == "D:/anh"
    assert video["ai_count"] == 3
    assert video["ai_motion"] is False
    assert video["ai_xfade"] == 0.7
    assert video["ai_api_key"] == "test-key"

    tab.load_from(window.settings)
    assert tab.ai_mode_combo.currentData() == "imagen"
    assert tab.ai_dir_edit.text() == "D:/anh"
    assert tab.ai_count_spin.value() == 3
    assert tab.ai_api_key_edit.text() == "test-key"


def test_subtitle_and_duck_roundtrip(window) -> None:
    tab = window.video_tab
    tab.subtitle_check.setChecked(True)
    tab.bgm_duck.setValue(40)
    tab.apply_to(window.settings)
    assert window.settings["video"]["subtitle_enabled"] is True
    assert window.settings["video"]["bgm_duck"] == 0.4

    tab.load_from(window.settings)
    assert tab.subtitle_check.isChecked() is True
    assert tab.bgm_duck.value() == 40


def test_open_project_populates_table(window, tmp_path: Path) -> None:
    project = tmp_path / "demo"
    inputs = project / "input"
    inputs.mkdir(parents=True)
    (inputs / "tap_01.txt").write_text("Nội dung thử.", encoding="utf-8")
    window.project_tab.set_project(str(project))
    window._reload_project()

    assert window.manager is not None
    assert window.project_tab.table.rowCount() == 1
    assert str(project.resolve()) == window.project_label._full
    assert window.btn_all.isEnabled()


def test_video_pool_settings_roundtrip(window) -> None:
    tab = window.video_tab
    index = tab.visual_combo.findData("video_pool")
    assert index >= 0
    tab.visual_combo.setCurrentIndex(index)
    assert tab.visual_stack.currentIndex() == index

    tab.pool_dir_edit.setText("D:/clips")
    tab.pool_count_spin.setValue(5)
    tab.write_srt_check.setChecked(True)
    tab.apply_to(window.settings)

    video = window.settings["video"]
    assert video["visual"] == "video_pool"
    assert video["video_pool_dir"] == "D:/clips"
    assert video["video_pool_count"] == 5
    assert video["write_srt"] is True

    tab.pool_dir_edit.setText("")
    tab.pool_count_spin.setValue(0)
    tab.write_srt_check.setChecked(False)
    tab.load_from(window.settings)
    assert tab.pool_dir_edit.text() == "D:/clips"
    assert tab.pool_count_spin.value() == 5
    assert tab.write_srt_check.isChecked() is True


def test_srt_file_roundtrip(window) -> None:
    tab = window.video_tab
    assert tab.srt_edit.isEnabled() is False
    tab.srt_check.setChecked(True)
    assert tab.srt_edit.isEnabled() is True
    tab.srt_edit.setText("D:/subs/tap_1.srt")
    tab.apply_to(window.settings)

    video = window.settings["video"]
    assert video["subtitle_srt"] is True
    assert video["subtitle_file"] == "D:/subs/tap_1.srt"

    tab.srt_check.setChecked(False)
    tab.srt_edit.setText("")
    tab.load_from(window.settings)
    assert tab.srt_check.isChecked() is True
    assert tab.srt_edit.text() == "D:/subs/tap_1.srt"
    assert tab.srt_edit.isEnabled() is True


def test_video_tab_run_button_starts_video_pipeline(window, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(window, "_start_pipeline", lambda mode: calls.append(mode))
    assert window.video_tab.run_button.text() == "Tạo video"
    window.video_tab.run_button.click()
    assert calls == ["video"]


def test_tts_default_language_roundtrip(window) -> None:
    tab = window.tts_tab
    page = tab.tts_page
    page.language_combo.setCurrentIndex(page.language_combo.findData("ja"))
    tab.apply_to(window.settings)
    assert window.settings["tts"]["language"] == "ja"

    page.language_combo.setCurrentIndex(page.language_combo.findData("vi"))
    tab.load_from(window.settings)
    assert page.language_combo.currentData() == "ja"


def test_project_language_column_and_override(window, tmp_path: Path) -> None:
    project = tmp_path / "demo"
    inputs = project / "input"
    inputs.mkdir(parents=True)
    (inputs / "tap_01.txt").write_text("Nội dung một.", encoding="utf-8")
    (inputs / "tap_02_en.txt").write_text("Second clip.", encoding="utf-8")
    window.project_tab.set_project(str(project))
    window._reload_project()

    table = window.project_tab.table
    assert table.rowCount() == 2
    assert table.item(0, 2).text() == "VI"
    assert table.item(1, 2).text() == "EN"

    table.setCurrentCell(1, 0)
    combo = window.project_tab.language_combo
    combo.setCurrentIndex(combo.findData("zh"))
    assert window.manager.get_item("tap_02_en").language == "zh"
    assert table.item(1, 2).text() == "ZH"


def test_script_tab_profiles_and_api_key(window, monkeypatch, tmp_path: Path) -> None:
    import ui.tab_script as tab_script
    from core.language_profiles import load_profiles as real_load, save_profiles as real_save

    store = tmp_path / "language_prompt.json"
    monkeypatch.setattr(tab_script, "load_profiles", lambda: real_load(store))
    monkeypatch.setattr(
        tab_script, "save_profiles", lambda profiles: real_save(profiles, store))
    store.write_text(
        '[{"id": "a1", "language": "en", "prompt": "kể chuyện cuốn hút"},'
        ' {"id": "b2", "language": "ja", "prompt": ""}]',
        encoding="utf-8")

    tab = window.script_tab
    tab.load_from(window.settings)
    assert [p.language for p in tab._profiles] == ["en", "ja"]
    assert tab.result_table.rowCount() == 2
    assert tab.result_table.item(0, 0).text() == "English"
    assert tab.result_table.item(0, 1).text() == "Chưa chuyển thể"
    assert tab.api_key_edit.echoMode() == tab.api_key_edit.EchoMode.Password
    assert tab.add_language_button.isEnabled() is True
    assert tab.edit_prompts_button.isEnabled() is True
    assert tab.model_combo.isEditable() is True
    assert tab.model_combo.currentText() == "gemini-3.8-flash"
    assert window.settings["script"]["model"] == "gemini-3.8-flash"

    tab.model_combo.setCurrentText("gemini-3.6-flash")
    tab.apply_to(window.settings)
    assert window.settings["script"]["model"] == "gemini-3.6-flash"
    tab.model_combo.setCurrentText("gemini-3.8-flash")

    tab._translations["en"] = "bản dịch thử"
    tab._reload_targets()
    assert tab.result_table.item(0, 1).text().startswith("Xong")
    assert tab.result_table.cellWidget(0, 2).isEnabled() is True
    tab._reload_targets(keep=False)
    assert tab.result_table.item(0, 1).text() == "Chưa chuyển thể"
    assert tab.result_table.cellWidget(0, 2).isEnabled() is False

    tab.api_key_edit.setText("  test-key  ")
    tab.apply_to(window.settings)
    assert window.settings["script"]["api_key"] == "test-key"
    assert tab._api_key() == "test-key"

    tab.api_key_edit.setText("")
    window.settings["script"]["api_key"] = ""
    window.settings.setdefault("video", {})["ai_api_key"] = "video-key"
    tab.load_from(window.settings)
    assert tab._api_key() == "video-key"


def test_language_dialogs_collect_and_edit_values(app) -> None:
    from core.language_profiles import LanguageProfile
    from ui.tab_script import _LanguagePromptDialog, _ManageLanguagesDialog

    add = _LanguagePromptDialog(language="en", prompt="giữ sự kiện")
    assert add.values() == ("en", "giữ sự kiện")

    manage = _ManageLanguagesDialog([
        LanguageProfile("en", "prompt en", "id1"),
        LanguageProfile("ja", "prompt ja", "id2"),
    ])
    assert manage.table.rowCount() == 2
    assert manage.table.item(0, 0).text() == "English"
    assert manage.table.item(1, 1).text() == "prompt ja"

    manage.table.item(0, 1).setText("prompt mới")
    manage._remove("ja")
    assert manage.table.rowCount() == 1
    manage._accept()
    profiles = manage.profiles()
    assert [(p.language, p.prompt, p.id) for p in profiles] == [
        ("en", "prompt mới", "id1")]

