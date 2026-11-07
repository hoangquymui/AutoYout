"""Unit tests for MainWindow polished header mini-dock, keyboard shortcuts, and accessibility."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow, PipelineDock


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_pipeline_dock_state_and_theme(qapp):
    dock = PipelineDock()
    assert dock.isVisible() is False
    assert dock.lbl_stage.text() == "⚡ Chuẩn bị…"
    assert dock.progress_bar.value() == 0

    # Set stage & progress
    dock.set_stage("BƯỚC 3: Tạo Video")
    assert "Tạo Video" in dock.lbl_stage.text()
    dock.set_progress(75)
    assert dock.progress_bar.value() == 75
    assert dock.lbl_percent.text() == "75%"

    # Set pause
    dock.set_paused(True)
    assert "tạm dừng" in dock.lbl_stage.text().lower()
    assert "tiếp tục" in dock.btn_pause.toolTip().lower()

    dock.set_paused(False)
    assert "tạm dừng" in dock.btn_pause.toolTip().lower()

    # Compact mode
    dock.set_compact(True)
    assert dock.progress_bar.isHidden() is True
    dock.set_compact(False)
    assert dock.progress_bar.isHidden() is False

    # Theme apply
    dock.apply_theme()


def test_main_window_dock_and_shortcuts(qapp):
    settings = {"app": {"theme": "dark"}}
    window = MainWindow(settings)

    # 1. Dock exists and initially hidden
    assert hasattr(window, "pipeline_dock")
    assert window.pipeline_dock.isVisible() is False

    # 2. Worker signals update dock
    window._on_stage("BƯỚC 2: Lồng tiếng")
    assert "Lồng tiếng" in window.pipeline_dock.lbl_stage.text()
    window._on_overall(42)
    assert window.pipeline_dock.progress_bar.value() == 42
    assert window.pipeline_dock.lbl_percent.text() == "42%"

    # 3. Pause UI updates dock
    window._set_pause_ui(True)
    assert "tạm dừng" in window.pipeline_dock.lbl_stage.text().lower()
    window._set_pause_ui(False)

    # 4. Dock hides on worker finish
    window._on_worker_finished()
    assert window.pipeline_dock.isVisible() is False

    # 5. Jump requested switches tab to Automation tab
    window.tabs.setCurrentIndex(0)
    assert window.tabs.currentIndex() == 0
    window.pipeline_dock.jump_requested.emit()
    assert window.tabs.currentIndex() == window._automation_index

    # 6. Shortcuts registered
    shortcuts = window.findChildren(QShortcut)
    key_strings = [sc.key().toString() for sc in shortcuts]
    assert "Ctrl+O" in key_strings
    assert "Ctrl+Shift+O" in key_strings
    assert "F12" in key_strings
    assert "Ctrl+R" in key_strings
    assert "Ctrl+T" in key_strings
    assert "Ctrl+1" in key_strings
    assert "Ctrl+8" in key_strings

    # 7. Accessibility
    assert window.btn_header_project.accessibleName() != ""
    assert window.btn_header_open.accessibleName() != ""
    assert window.btn_refresh_status.accessibleName() != ""
    assert window.btn_console.accessibleName() != ""
    assert window.btn_settings.accessibleName() != ""
    assert window.btn_theme.accessibleName() != ""
