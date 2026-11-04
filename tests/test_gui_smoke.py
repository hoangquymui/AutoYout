"""MainWindow construction and tab wiring without a real display."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent  # noqa: E402
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
    # Without an event loop the delete request would linger and every new
    # window would re-polish all previous ones (quadratic test runtime).
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_window_builds_all_tabs(window) -> None:
    assert window.windowTitle() == "AutoYout"
    assert not window.windowIcon().isNull()
    assert window.tabs.count() == 7
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert labels == ["Dự án", "Cài đặt", "Kịch bản",
                      "Lồng tiếng", "Video", "YouTube",
                      "Automation"]
    assert window.settings_tab.inner_tabs.count() == 2
    assert window.settings_tab.inner_tabs.tabText(0) == "Cài đặt chung"
    assert window.settings_tab.inner_tabs.tabText(1) == "API"
    assert window.settings_tab.api_tabs.count() == 3
    assert window.settings_tab.api_tabs.tabText(0) == "Văn bản AI"
    assert window.settings_tab.api_tabs.tabText(1) == "Giọng đọc (TTS)"
    assert window.settings_tab.api_tabs.tabText(2) == "Ảnh / Video AI"
    assert window.settings_tab.card_h2mtts is not None
    inner_video_labels = [
        window.video_tab.inner_tabs.tabText(i)
        for i in range(window.video_tab.inner_tabs.count())
    ]
    assert inner_video_labels == ["Cấu hình video", "Khung mẫu (Video Frame)"]
    assert window.frame_tab is window.video_tab.frame_tab
    tab = window.automation_tab
    assert tab.btn_tts.text().startswith("1. Lồng tiếng")
    assert tab.btn_video.text().startswith("2. Tạo Video")
    assert tab.btn_upload.text().startswith("3. Upload YouTube")
    assert "AUTO ALL" in tab.btn_all.text()
    assert tab.btn_pause.isEnabled() is False
    assert tab.btn_stop.isEnabled() is False


def test_log_console_and_status(window) -> None:
    assert hasattr(window, "btn_console")
    assert window.console_window is not None
    window._toggle_console_window()
    assert window.console_window.isVisible()
    window.console_window.append_log("INFO", "test floating console")
    assert "test floating console" in window.console_window.console.view.toPlainText()
    window.console_window.hide()

    window.automation_tab.log_console.append("INFO", "xin chào")
    window.automation_tab.log_console.append("ERROR", "lỗi thử")
    text = window.automation_tab.log_console.view.toPlainText()
    assert "xin chào" in text and "lỗi thử" in text
    window._set_status("Đang chạy thử", running=True)
    assert "Đang chạy thử" in window.automation_tab.status_label.text()
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
    assert theme.BG in QApplication.instance().styleSheet()
    window._apply_theme("dark")


def test_browser_login_button_follows_upload_method(window) -> None:
    tab = window.youtube_tab
    api_index = tab.method_combo.findData("api")
    browser_index = tab.method_combo.findData("browser")
    assert browser_index >= 0

    tab.method_combo.setCurrentIndex(browser_index)
    assert tab.browser_box.isHidden() is False
    assert tab.api_box.isHidden() is True
    assert tab.browser_login_button.isHidden() is False
    assert tab.browser_status.isHidden() is False
    assert tab.browser_login_button.isEnabled() is True
    assert tab.sub_tabs.isTabVisible(2) is False

    tab.method_combo.setCurrentIndex(api_index)
    assert tab.browser_box.isHidden() is True
    assert tab.api_box.isHidden() is False
    assert tab.browser_login_button.isHidden() is True
    assert tab.browser_status.isHidden() is True
    assert tab.sub_tabs.isTabVisible(2) is True


def test_youtube_tab_has_upload_and_thumbnail_pages(window) -> None:
    tab = window.youtube_tab
    labels = [tab.sub_tabs.tabText(i) for i in range(tab.sub_tabs.count())]
    assert labels == ["Đăng tải", "Hình thu nhỏ", "Tài khoản"]
    assert tab.sub_tabs.currentIndex() == 0
    assert tab.sub_tabs.widget(1) is tab.thumbnail_panel
    assert tab.thumbnail_panel.template_list.count() >= 2
    assert tab.thumbnail_panel.provider_combo.currentData() == "pollinations"
    assert hasattr(tab, "acc_table")
    assert tab.acc_table.columnCount() == 6
    assert hasattr(tab, "acc_add_btn")
    assert hasattr(tab, "acc_check_all_btn")


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


def test_open_project_populates_tree(window, tmp_path: Path) -> None:
    project = tmp_path / "demo"
    inputs = project / "script"
    inputs.mkdir(parents=True)
    (inputs / "tap_01.txt").write_text("Nội dung thử.", encoding="utf-8")
    window.project_tab.set_project(str(project))
    window._reload_project()

    assert window.manager is not None
    assert Path(window.project_tab.fs_model.rootPath()) == project.resolve()
    assert window.project_tab.tree.rootIndex().isValid()
    assert str(project.resolve()) == window.project_label._full
    assert window.automation_tab.btn_all.isEnabled()


def test_video_pool_settings_roundtrip(window) -> None:
    tab = window.video_tab
    index = tab.visual_combo.findData("video_pool")
    assert index >= 0
    tab.visual_combo.setCurrentIndex(index)
    assert tab.visual_stack.currentIndex() == index

    tab.pool_dir_edit.setText("D:/clips")
    tab.pool_count_spin.setValue(5)
    tab.write_srt_check.setChecked(True)
    tab.grouping_combo.setCurrentIndex(tab.grouping_combo.findData("group"))
    tab.group_size_spin.setValue(4)
    tab.gap_spin.setValue(2.0)
    tab.apply_to(window.settings)

    video = window.settings["video"]
    assert video["visual"] == "video_pool"
    assert video["video_pool_dir"] == "D:/clips"
    assert video["video_pool_count"] == 5
    assert video["write_srt"] is True
    assert video["grouping"] == "group"
    assert video["group_size"] == 4
    assert video["gap"] == 2.0

    tab.pool_dir_edit.setText("")
    tab.pool_count_spin.setValue(0)
    tab.write_srt_check.setChecked(False)
    tab.grouping_combo.setCurrentIndex(tab.grouping_combo.findData("one"))
    tab.group_size_spin.setValue(10)
    tab.gap_spin.setValue(1.5)
    tab.load_from(window.settings)
    assert tab.pool_dir_edit.text() == "D:/clips"
    assert tab.pool_count_spin.value() == 5
    assert tab.write_srt_check.isChecked() is True
    assert tab.grouping_combo.currentData() == "group"
    assert tab.group_size_spin.value() == 4
    assert tab.gap_spin.value() == 2.0


def test_srt_file_roundtrip(window) -> None:
    tab = window.video_tab
    tab.srt_check.setChecked(False)
    tab.srt_edit.setText("")
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


def test_frame_tab_roundtrip(window, tmp_path: Path) -> None:
    from core.frame_layout import FrameImage

    tab = window.frame_tab
    # user_settings.json may already contain a frame template; start clean.
    window.settings["frame"] = {}
    tab.load_from(window.settings)
    tab._layout.enabled = True
    tab._layout.subtitle_x = 0.42
    tab._layout.subtitle_y = 0.74
    tab._layout.subtitle_scale = 1.3
    tab._layout.waveform = True
    tab._layout.wave_x = 0.12
    tab._layout.wave_color = "#FF8800"
    tab._layout.images.append(FrameImage(
        path=str(tmp_path / "logo.png"), x=0.6, y=0.1, width=0.2, opacity=0.6))
    tab.apply_to(window.settings)

    frame = window.settings["frame"]
    assert frame["enabled"] is True
    assert frame["subtitle_x"] == 0.42
    assert frame["subtitle_scale"] == 1.3
    assert frame["wave_color"] == "#FF8800"
    assert frame["images"][0]["opacity"] == 0.6

    tab._reset()
    assert tab._layout.enabled is False
    tab.load_from(window.settings)
    assert tab._layout.enabled is True
    assert tab._layout.subtitle_y == 0.74
    assert tab._layout.images[0].path.endswith("logo.png")


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
    assert tab.result_table.rowCount() == 0
    assert tab.add_language_button.isEnabled() is True
    assert tab.edit_prompts_button.isEnabled() is True
    assert tab.model_combo.isEditable() is True

    tab._set_model("gemini-3.6-flash")
    tab.apply_to(window.settings)
    assert window.settings["script"]["model"] == "gemini-3.6-flash"

    tab._translations["en"] = "bản dịch thử"
    tab._reload_targets()
    assert tab.result_table.rowCount() == 1
    assert tab.result_table.item(0, 0).text() == "English"
    assert tab.result_table.item(0, 1).text().startswith("Xong")
    assert tab.result_table.cellWidget(0, 2).isEnabled() is True
    tab._reload_targets(keep=False)
    assert tab.result_table.rowCount() == 0

    window.settings["api"] = {"gemini_key": "api-key-test"}
    tab.load_from(window.settings)
    assert tab._api_key() == "api-key-test"

    window.settings["api"]["gemini_key"] = ""
    window.settings.setdefault("script", {})["api_key"] = ""
    window.settings.setdefault("video", {})["ai_api_key"] = "video-key"
    tab.load_from(window.settings)
    assert tab._api_key() == "video-key"

    from core.ai_client import DEFAULT_CLEAN_PROMPT

    window.settings["script"].pop("transcript_clean_prompt", None)
    tab.load_from(window.settings)
    assert tab._clean_prompt == DEFAULT_CLEAN_PROMPT
    tab._clean_prompt = "prompt làm sạch riêng"
    tab.apply_to(window.settings)
    assert window.settings["script"]["transcript_clean_prompt"] == "prompt làm sạch riêng"


def test_language_dialogs_collect_and_edit_values(app) -> None:
    from core.ai_client import DEFAULT_CLEAN_PROMPT
    from core.language_profiles import LanguageProfile
    from ui.tab_script import (
        _CleanPromptDialog,
        _LanguagePromptDialog,
        _ManageLanguagesDialog,
        _PromptEditDialog,
    )

    add = _LanguagePromptDialog(language="en", prompt="giữ sự kiện")
    assert add.values() == ("en", "giữ sự kiện")

    # Test auto-suggestion while typing character by character without NameError
    for text in ["T", "Ti", "Tiếng", "Tiếng Việt", "Anh", "Pháp", "Bất kỳ"]:
        add.name_edit.setText(text)

    clean = _CleanPromptDialog(DEFAULT_CLEAN_PROMPT)
    assert clean.prompt_edit.toPlainText() == DEFAULT_CLEAN_PROMPT
    clean.prompt_edit.setPlainText("prompt làm sạch mới")
    assert clean.value() == "prompt làm sạch mới"

    manage = _ManageLanguagesDialog([
        LanguageProfile("en", "prompt en", "id1"),
        LanguageProfile("ja", "prompt ja", "id2"),
    ])
    assert manage.table.rowCount() == 2
    assert manage.table.columnCount() == 3
    assert manage.table.item(0, 0).text() == "English"
    assert manage.table.item(1, 1).text() == "prompt ja"
    assert manage.table.verticalHeader().defaultSectionSize() == 42
    assert manage.table.rowHeight(0) == 42
    assert manage.table.rowHeight(1) == 42
    from PySide6.QtWidgets import QHeaderView
    header = manage.table.horizontalHeader()
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Fixed
    assert header.sectionSize(2) == 105

    action_widget = manage.table.cellWidget(0, 2)
    assert action_widget is not None
    action_layout = action_widget.layout()
    assert action_layout.spacing() == 8
    margins = action_layout.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (6, 1, 6, 7)
    edit_btn = action_layout.itemAt(0).widget()
    del_btn = action_layout.itemAt(1).widget()
    assert edit_btn.width() == 26 and edit_btn.height() == 26
    assert del_btn.width() == 26 and del_btn.height() == 26

    edit = _PromptEditDialog("en", "prompt en")
    edit.edit.setPlainText("prompt mới")
    assert edit.value() == "prompt mới"

    manage.table.item(0, 1).setText("prompt mới")
    manage._remove("ja")
    assert manage.table.rowCount() == 1

    monkeypatch_dialog = False
    mock_prompt_val = "prompt từ modal"
    class MockPromptEditDialog:
        def __init__(self, *args, **kwargs):
            pass
        def exec(self):
            return 1  # Accepted
        def value(self):
            return mock_prompt_val
        def deleteLater(self):
            pass

    import ui.tab_script as ts
    orig = ts._PromptEditDialog
    ts._PromptEditDialog = MockPromptEditDialog
    try:
        mock_prompt_val = "prompt từ click"
        manage._on_cell_clicked(0, 1)
        assert manage.table.item(0, 1).text() == "prompt từ click"

        mock_prompt_val = "prompt từ dblclick"
        manage._on_cell_double_clicked(0, 0)
        assert manage.table.item(0, 1).text() == "prompt từ dblclick"

        mock_prompt_val = "prompt từ edit_btn"
        row0_action = manage.table.cellWidget(0, 2)
        row0_edit_btn = row0_action.layout().itemAt(0).widget()
        row0_edit_btn.click()
        assert manage.table.item(0, 1).text() == "prompt từ edit_btn"
    finally:
        ts._PromptEditDialog = orig

    manage._accept()
    profiles = manage.profiles()
    assert [(p.language, p.prompt, p.id) for p in profiles] == [
        ("en", "prompt từ edit_btn", "id1")]



def test_frame_canvas_drag_moves_subtitle(app) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from core.frame_layout import FrameLayout
    from ui.tab_frame import FrameCanvas

    layout = FrameLayout(enabled=True)
    canvas = FrameCanvas(layout)
    canvas.resize(800, 450)
    canvas.show()
    app.processEvents()

    start = canvas._subtitle_box().center()
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, start, start,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(press)
    target = QPointF(start.x() + 60, start.y() - 24)
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove, target, target,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(move)
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, target, target,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(release)

    assert canvas.selection() == ("subtitle", -1)
    assert layout.subtitle_x > 0.5
    assert layout.subtitle_y < 0.9
    canvas.hide()


def test_dubbing_tab_ui_layout_and_labels(window) -> None:
    dub = window.tts_tab
    assert dub.import_btn.text() == "Nhập TXT"
    assert dub.import_dir_btn.text() == "Nhập thư mục"
    assert dub.reload_btn.text() == "Tải lại"
    assert dub.start_btn.text() == "Bắt đầu"
    assert dub.pause_btn.text() == "Tạm dừng"
    assert dub.stop_btn.text() == "Dừng"
    assert dub.open_btn.text() == "Mở thư mục xuất"
    assert dub.inner_tabs.tabText(0) == "Bảng đoạn"
    headers = [dub.table.horizontalHeaderItem(i).text() for i in range(5)]
    assert headers == ["STT", "Thời gian", "Nội dung", "Trạng thái", "File xuất"]
    assert "0 đoạn · Xong 0" in dub.stats_label.text()


def test_dubbing_tab_resplit_or_reload(window, tmp_path: Path) -> None:
    from core import dubbing
    project = tmp_path / "proj"
    script_file = project / "script" / "sample.txt"
    script_file.parent.mkdir(parents=True, exist_ok=True)
    words = "Một hai ba bốn năm sáu bảy tám chín mười. Mười một mười hai mười ba mười bốn mười lăm mười sáu mười bảy mười tám mười chín hai mươi."
    script_file.write_text(words, encoding="utf-8")

    window.project_tab.set_project(str(project))
    window._reload_project()

    dubbing.import_txt(window.manager, script_file, min_words=15, max_words=30)
    window._reload_project()

    dub = window.tts_tab
    dub.min_words_spin.setValue(5)
    dub.max_words_spin.setValue(10)
    dub.reload_btn.click()

    assert len(dub._cues) >= 2
    assert "đoạn" in dub.stats_label.text()
    assert dub.table.rowCount() == len(dub._cues)
    assert dub.table.columnCount() == 5
    assert dub.table.item(0, 3).text() == "Chờ"


def test_automation_workflow_nodes_and_glow(window) -> None:
    tab = window.automation_tab
    assert hasattr(tab, "card_tts")
    assert hasattr(tab, "card_video")
    assert hasattr(tab, "card_upload")
    assert tab.card_tts.state == "idle"

    tab.set_stage("Lồng tiếng")
    assert tab.card_tts.state == "running"
    assert tab.card_tts._shadow_effect.isEnabled() is True

    tab.set_stage("Tạo video")
    assert tab.card_tts.state == "done"
    assert tab.card_video.state == "running"
    assert tab.card_video._shadow_effect.isEnabled() is True

    tab.set_stage("Đăng tải YouTube")
    assert tab.card_video.state == "done"
    assert tab.card_upload.state == "running"
    assert tab.card_upload._shadow_effect.isEnabled() is True

    tab.set_stage("Hoàn tất")
    assert tab.card_upload.state == "done"


def test_tabs_accessible_and_read_only_while_running(window) -> None:
    class DummyWorker:
        pass

    window.worker = DummyWorker()
    window._update_actions()

    for i in range(window.tabs.count()):
        assert window.tabs.isTabEnabled(i) is True

    assert window.project_tab._pipeline_busy is True
    assert window.settings_tab._pipeline_busy is True
    assert window.video_tab._pipeline_busy is True
    assert window.script_tab.clean_button.isEnabled() is False

    window.worker = None
    window._update_actions()

    assert window.project_tab._pipeline_busy is False
    assert window.settings_tab._pipeline_busy is False
    assert window.video_tab._pipeline_busy is False


def test_dubbing_tab_selection_preserved_on_reload(window, tmp_path: Path) -> None:
    from core import dubbing
    project = tmp_path / "project_preserve"
    project.mkdir(parents=True)
    scripts = project / "script"
    scripts.mkdir(parents=True)
    (scripts / "first_item.txt").write_text("Nội dung bài thứ nhất.", encoding="utf-8")
    (scripts / "second_item.txt").write_text("Nội dung bài thứ hai.", encoding="utf-8")

    window.project_tab.set_project(str(project))
    window._reload_project()

    dubbing.import_txt(window.manager, scripts / "first_item.txt", min_words=5, max_words=10)
    dubbing.import_txt(window.manager, scripts / "second_item.txt", min_words=5, max_words=10)
    window._reload_project()

    dub = window.tts_tab
    assert dub.file_combo.count() == 2

    # Switch to second item
    idx = dub.file_combo.findData("second_item")
    assert idx >= 0
    dub.file_combo.setCurrentIndex(idx)
    assert dub._base == "second_item"

    # Reloading project must PRESERVE second item selection
    window._reload_project()
    assert dub.file_combo.currentData() == "second_item"
    assert dub._base == "second_item"

    # Clicking 'Tải lại' button must also PRESERVE second item selection
    dub.reload_btn.click()
    assert dub.file_combo.currentData() == "second_item"
    assert dub._base == "second_item"



