"""Tests for hardened Automation tab and pipeline worker step toggles."""
from __future__ import annotations

from pathlib import Path
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from core.pipeline_worker import PipelineError, PipelineWorker
from core.project_manager import ProjectManager
from ui.tab_automation import AutomationTab


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_automation_tab_all_five_steps_toggleable(qapp):
    tab = AutomationTab()
    assert tab.card_script.enable_toggle is not None
    assert tab.card_tts.enable_toggle is not None
    assert tab.card_video.enable_toggle is not None
    assert tab.card_thumbnail.enable_toggle is not None
    assert tab.card_upload.enable_toggle is not None

    # Default is all steps enabled
    assert tab._any_step_enabled() is True
    assert tab.btn_run_full.isEnabled() is True
    assert tab.btn_run_project.isEnabled() is True

    # Disable all steps
    tab.card_script.set_step_enabled(False)
    tab.card_tts.set_step_enabled(False)
    tab.card_video.set_step_enabled(False)
    tab.card_thumbnail.set_step_enabled(False)
    tab.card_upload.set_step_enabled(False)
    tab._on_step_toggled("upload", False)

    assert tab._any_step_enabled() is False
    assert tab.btn_run_full.isEnabled() is False
    assert tab.btn_run_project.isEnabled() is False
    assert "Tất cả các bước đều đã tắt" in tab.status_label.text()

    # Re-enable one step (e.g., video)
    tab.card_video.set_step_enabled(True)
    tab._on_step_toggled("video", True)
    assert tab._any_step_enabled() is True
    assert tab.btn_run_full.isEnabled() is True
    assert tab.btn_run_project.isEnabled() is True


def test_automation_tab_settings_persistence(qapp):
    tab = AutomationTab()
    settings = {
        "automation": {
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "topic_name": "Test Automation",
            "script_enabled": False,
            "tts_enabled": True,
            "video_enabled": True,
            "thumbnail_enabled": False,
            "upload_enabled": True,
            "target_languages": ["vi", "en"],
        }
    }
    tab.load_from(settings)
    assert tab.card_script.is_step_enabled() is False
    assert tab.card_tts.is_step_enabled() is True
    assert tab.card_video.is_step_enabled() is True
    assert tab.card_thumbnail.is_step_enabled() is False
    assert tab.card_upload.is_step_enabled() is True

    out = {}
    tab.apply_to(out)
    auto = out["automation"]
    assert auto["script_enabled"] is False
    assert auto["tts_enabled"] is True
    assert auto["video_enabled"] is True
    assert auto["thumbnail_enabled"] is False
    assert auto["upload_enabled"] is True


def test_pipeline_worker_stage_plan_permutations(tmp_path: Path):
    manager = ProjectManager(tmp_path)

    # 1. All steps enabled with YouTube URL
    settings = {
        "automation": {
            "youtube_url": "https://www.youtube.com/watch?v=test1234567",
            "script_enabled": True,
            "tts_enabled": True,
            "video_enabled": True,
            "thumbnail_enabled": True,
            "upload_enabled": True,
        }
    }
    worker = PipelineWorker(settings, manager, mode="all")
    plan = worker._stage_plan()
    stages = [step[0] for step in plan]
    assert stages == ["script", "tts", "video", "thumbnail", "upload"]

    # 2. Pre-recorded: Script & TTS disabled, only Video + Thumb + Upload
    settings["automation"]["script_enabled"] = False
    settings["automation"]["tts_enabled"] = False
    worker2 = PipelineWorker(settings, manager, mode="all")
    stages2 = [step[0] for step in worker2._stage_plan()]
    assert stages2 == ["video", "thumbnail", "upload"]

    # 3. Only TTS enabled (no YouTube URL)
    settings["automation"]["youtube_url"] = ""
    settings["automation"]["script_enabled"] = True
    settings["automation"]["tts_enabled"] = True
    settings["automation"]["video_enabled"] = False
    settings["automation"]["thumbnail_enabled"] = False
    settings["automation"]["upload_enabled"] = False
    worker3 = PipelineWorker(settings, manager, mode="all")
    stages3 = [step[0] for step in worker3._stage_plan()]
    assert stages3 == ["tts"]

    # 4. All steps disabled -> raises PipelineError
    settings["automation"]["tts_enabled"] = False
    worker4 = PipelineWorker(settings, manager, mode="all")
    with pytest.raises(PipelineError, match="Tất cả các bước tự động hóa đều bị tắt"):
        worker4._stage_plan()
