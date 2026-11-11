"""Tests for Terminal Console and activity log separation."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from core.settings import load_settings
from ui.console_window import ConsoleWindow, toggle_windows_console
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_terminal_console_features(app: QApplication) -> None:
    cw = ConsoleWindow()
    assert cw.console is not None
    assert cw.console.view is not None

    # Verify banner or initial content
    cw.append_log("INFO", "Backend server starting...")
    assert "Backend server starting..." in cw.console.view.toPlainText()

    # Test levels
    cw.append_log("WARNING", "Low memory warning")
    cw.append_log("ERROR", "Unhandled test error")
    cw.append_log("DEBUG", "Verbose payload dump")
    text = cw.console.view.toPlainText()
    assert "Low memory warning" in text
    assert "Unhandled test error" in text
    assert "Verbose payload dump" in text

    # Test filtering by query
    cw.console.set_filter_query("Low memory")
    filtered = cw.console.view.toPlainText()
    assert "Low memory warning" in filtered
    assert "Backend server starting..." not in filtered

    # Reset filter
    cw.console.set_filter_query("")
    assert "Backend server starting..." in cw.console.view.toPlainText()

    # Test level filter
    cw.console.set_level_filter("ERROR")
    err_filtered = cw.console.view.toPlainText()
    assert "Unhandled test error" in err_filtered
    assert "Backend server starting..." not in err_filtered
    cw.console.set_level_filter("ALL")

    # Test toggles
    cw.cb_autoscroll.setChecked(False)
    assert cw.console.auto_scroll is False
    cw.cb_wordwrap.setChecked(True)

    # Test clear
    cw._clear_logs()
    assert cw.console.view.toPlainText() == ""


def test_log_dispatch_separation_between_console_and_automation(app: QApplication, monkeypatch) -> None:
    settings = load_settings()
    settings["project"]["last_project"] = ""
    window = MainWindow(settings)
    try:
        # 1. Normal automation workflow log
        window._dispatch_log("INFO", "Bắt đầu trích xuất âm thanh từ video...")
        assert "Bắt đầu trích xuất âm thanh từ video..." in window.console_window.console.view.toPlainText()
        assert "Bắt đầu trích xuất âm thanh từ video..." in window.automation_tab.log_console.view.toPlainText()

        # 2. Python traceback block
        window._dispatch_log("ERROR", "Traceback (most recent call last):")
        window._dispatch_log("ERROR", '  File "D:\\test\\bad.py", line 42, in crash')
        window._dispatch_log("ERROR", "    reply = None / 0")
        window._dispatch_log("ERROR", "    ^^^^^^^^^^^^^^^^")
        window._dispatch_log("ERROR", "ZeroDivisionError: division by zero")

        # Must appear in Terminal Console
        console_txt = window.console_window.console.view.toPlainText()
        assert "ZeroDivisionError: division by zero" in console_txt
        assert "Traceback (most recent call last):" in console_txt

        # Must NOT appear in Automation Tab log console
        auto_txt = window.automation_tab.log_console.view.toPlainText()
        assert "ZeroDivisionError: division by zero" not in auto_txt
        assert "Traceback (most recent call last):" not in auto_txt
        assert 'File "D:\\test\\bad.py"' not in auto_txt

        # 3. Low-level internal library noise
        window._dispatch_log("INFO", "[piper] [info] Initialized piper")
        window._dispatch_log("DEBUG", "Starting new HTTPS connection (1): huggingface.co")
        assert "[piper] [info]" in window.console_window.console.view.toPlainText()
        assert "[piper] [info]" not in window.automation_tab.log_console.view.toPlainText()
        assert "huggingface.co" not in window.automation_tab.log_console.view.toPlainText()

    finally:
        window.close()
