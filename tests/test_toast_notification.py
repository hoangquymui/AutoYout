"""Tests for bottom-right toast notifications."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtWidgets import QApplication, QMainWindow
import pytest

from ui.components.toast import ToastManager, ToastWidget
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def main_win(app) -> MainWindow:
    win = MainWindow({})
    win.resize(1000, 700)
    return win


def test_toast_widget_creation(app):
    win = QMainWindow()
    win.resize(800, 600)
    win.show()
    tm = ToastManager(win)
    toast = tm.show_error(
        "Lỗi tạo video",
        "Chưa có ảnh nền. Chọn ảnh trong tab Video hoặc đặt ảnh vào thư mục assets.",
    )
    assert toast is not None
    assert not toast.isHidden()
    assert toast._level == "error"
    assert "Chưa có ảnh nền" in toast._message
    assert len(tm._toasts) == 1

    # Position is anchored at bottom-right
    pos = toast.pos()
    assert pos.x() == 800 - toast.width() - 24
    assert pos.y() == 600 - 24 - toast.sizeHint().height()


def test_toast_reposition_on_resize(app):
    win = QMainWindow()
    win.resize(800, 600)
    tm = ToastManager(win)
    t1 = tm.show_error("Lỗi 1", "Mô tả 1")
    t2 = tm.show_error("Lỗi 2", "Mô tả 2")
    assert len(tm._toasts) == 2

    # Oldest (t1) is above newest (t2)
    assert t1.y() < t2.y()

    # Resize window
    win.resize(1200, 900)
    tm.reposition()
    assert t2.x() == 1200 - t2.width() - 24
    assert t1.x() == 1200 - t1.width() - 24
    assert t1.y() < t2.y()


def test_main_window_on_completed_error_shows_toast(main_win):
    sample_error = (
        "Hoàn tất có 1 lỗi sau 2s. 0/1 video\n"
        "• Video one::aaa_vi: Chưa có ảnh nền. Chọn ảnh trong tab Video hoặc đặt ảnh vào thư mục assets."
    )
    main_win._on_completed(False, sample_error)

    assert len(main_win.toast_manager._toasts) == 1
    toast = main_win.toast_manager._toasts[0]
    assert toast._title == "Lỗi tạo video"
    assert "Chưa có ảnh nền" in toast._message
    assert "one::aaa_vi" in toast._message


def test_main_window_on_completed_success_no_error_toast(main_win):
    main_win._on_completed(True, "Hoàn tất sau 3s. 1/1 video")
    assert len(main_win.toast_manager._toasts) == 0


def test_main_window_on_completed_cancel_no_error_toast(main_win):
    main_win._on_completed(False, "Đã dừng pipeline theo yêu cầu.")
    assert len(main_win.toast_manager._toasts) == 0


def test_toast_dismiss(app):
    win = QMainWindow()
    win.resize(800, 600)
    tm = ToastManager(win)
    toast = tm.show_error("Lỗi", "Nội dung lỗi")
    assert len(tm._toasts) == 1

    toast.close_animated()
    assert toast._closing is True
