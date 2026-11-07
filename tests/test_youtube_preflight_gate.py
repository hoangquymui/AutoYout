"""Unit tests for PreFlightUploadDialog and upload pre-flight safety gate in ui/tab_youtube.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from core.project_manager import ProjectItem, ProjectManager, VideoJob
from core.settings import load_settings
from ui.tab_youtube import PreFlightUploadDialog, YouTubeConfigTab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _create_sample_job(tmp_path: Path, job_id: str, title: str, lang: str = "vi", size_bytes: int = 1024 * 1024) -> VideoJob:
    video_file = tmp_path / f"{job_id}.mp4"
    video_file.write_bytes(b"\x00" * min(size_bytes, 1024))
    job = VideoJob(
        id=job_id,
        path=video_file,
        items=[job_id],
        title=title,
        duration=125.0,
        language=lang,
    )
    return job


def test_preflight_dialog_structure_and_safety_banner(app, tmp_path: Path, monkeypatch) -> None:
    creds_dir = tmp_path / "creds"
    creds_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("core.youtube_uploader.credentials_dir", lambda: creds_dir)

    # Setup dummy channel auth
    (creds_dir / "token_default.json").write_text('{"token": "xyz"}', encoding="utf-8")
    (creds_dir / "token_default.meta.json").write_text(
        json.dumps({"title": "Studio Official", "channel_id": "UC_MAIN"}),
        encoding="utf-8",
    )

    settings = load_settings()
    settings.setdefault("youtube", {})["privacy"] = "private"
    settings["youtube"]["channel_languages"] = {}

    job1 = _create_sample_job(tmp_path, "job_vi", "Tin tức công nghệ [vi]", lang="vi")
    job2 = _create_sample_job(tmp_path, "job_ja", "アニメレビュー (ja)", lang="ja")

    dialog = PreFlightUploadDialog([job1, job2], settings, manager=None)

    # Verify table structure
    assert dialog.table.rowCount() == 2
    assert dialog.table.columnCount() == 6
    assert len(dialog._row_data) == 2

    # Initially all private -> Safe Mode banner active
    assert "CHẾ ĐỘ AN TOÀN" in dialog.banner_text.text()
    assert dialog.btn_confirm.isEnabled()

    # Change row 0 to Public -> Banner switches to Warning Alert
    combo0 = dialog._row_data[0]["privacy_combo"]
    idx_public = combo0.findData("public")
    assert idx_public >= 0
    combo0.setCurrentIndex(idx_public)

    assert "CẢNH BÁO XUẤT BẢN CÔNG KHAI" in dialog.banner_text.text()
    assert "1" in dialog.banner_text.text()

    # Change row 0 to Unlisted -> Safe Mode banner restored
    idx_unlisted = combo0.findData("unlisted")
    combo0.setCurrentIndex(idx_unlisted)
    assert "CHẾ ĐỘ AN TOÀN" in dialog.banner_text.text()


def test_preflight_batch_privacy_and_selection_guard(app, tmp_path: Path, monkeypatch) -> None:
    creds_dir = tmp_path / "creds"
    creds_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("core.youtube_uploader.credentials_dir", lambda: creds_dir)

    settings = load_settings()
    settings.setdefault("youtube", {})["privacy"] = "private"

    job1 = _create_sample_job(tmp_path, "job1", "Video 1")
    job2 = _create_sample_job(tmp_path, "job2", "Video 2")
    job3 = _create_sample_job(tmp_path, "job3", "Video 3")

    dialog = PreFlightUploadDialog([job1, job2, job3], settings, manager=None)

    # Batch set all to Unlisted
    idx_unlisted = dialog.combo_batch_privacy.findData("unlisted")
    dialog.combo_batch_privacy.setCurrentIndex(idx_unlisted)
    dialog._apply_batch_privacy()

    for r in dialog._row_data:
        assert r["privacy_combo"].currentData() == "unlisted"

    # Test Deselect All: Button Confirm must become disabled!
    dialog.cb_select_all.setChecked(False)
    assert not dialog.btn_confirm.isEnabled()
    assert len(dialog.get_confirmed_jobs()) == 0

    # Select single item -> Button re-enables
    dialog._row_data[0]["cb"].setChecked(True)
    assert dialog.btn_confirm.isEnabled()
    confirmed = dialog.get_confirmed_jobs()
    assert len(confirmed) == 1
    assert confirmed[0].id == "job1"


def test_preflight_apply_overrides_to_job_and_manager(app, tmp_path: Path, monkeypatch) -> None:
    creds_dir = tmp_path / "creds"
    creds_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("core.youtube_uploader.credentials_dir", lambda: creds_dir)

    settings = load_settings()
    settings.setdefault("youtube", {})["privacy"] = "private"

    job1 = _create_sample_job(tmp_path, "job_target", "Video Cần Đăng")

    # Mock ProjectManager with item metadata
    manager = MagicMock()
    item = MagicMock(base="job_target", metadata={"privacy": "private"})
    manager.get_item.return_value = item
    manager.save_state = MagicMock()
    manager.update_item = MagicMock()

    dialog = PreFlightUploadDialog([job1], settings, manager=manager)

    # Switch to unlisted
    combo = dialog._row_data[0]["privacy_combo"]
    combo.setCurrentIndex(combo.findData("unlisted"))

    dialog.apply_overrides()

    # Verify overrides persisted to job and manager
    assert getattr(job1, "privacy", "") == "unlisted"
    assert manager.update_item.called
    assert manager.save_state.called


def test_youtube_tab_confirm_preflight_accept_and_reject(app, tmp_path: Path, monkeypatch) -> None:
    creds_dir = tmp_path / "creds"
    creds_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("core.youtube_uploader.credentials_dir", lambda: creds_dir)

    settings = load_settings()
    job1 = _create_sample_job(tmp_path, "vid_ok", "Video Hợp Lệ")

    tab = YouTubeConfigTab()
    tab.load_from(settings)
    tab._custom_videos = {"vid_ok": job1}

    # Case 1: User Cancels dialog -> returns None, no upload requested
    monkeypatch.setattr(PreFlightUploadDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    result = tab.confirm_preflight_upload(["vid_ok"])
    assert result is None

    # Case 2: User Accepts dialog -> returns list of confirmed IDs
    monkeypatch.setattr(PreFlightUploadDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    result_ok = tab.confirm_preflight_upload(["vid_ok"])
    assert result_ok == ["vid_ok"]


def test_preflight_extreme_unicode_and_missing_thumbnail(app, tmp_path: Path, monkeypatch) -> None:
    creds_dir = tmp_path / "creds"
    creds_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("core.youtube_uploader.credentials_dir", lambda: creds_dir)

    settings = load_settings()

    # Extreme input: 400-char unicode title with Vietnamese diacritics, Japanese, Arabic, and emojis
    long_title = "🎬 [4K] Siêu phẩm công nghệ Việt Nam: Trải nghiệm AI tiên tiến nhất thế giới! 🚀 日本語テスト | اختبار اللغة العربية | " + ("X" * 250)
    job = _create_sample_job(tmp_path, "extreme_job", long_title, lang="vi")

    # Without thumbnail image on disk: must handle gracefully
    dialog = PreFlightUploadDialog([job], settings, manager=None)
    assert dialog.table.rowCount() == 1

    # Cell 1 must have fallback text without crash
    cell1 = dialog.table.cellWidget(0, 1)
    assert cell1 is not None

    # Title cell must contain the full title in tooltip
    info_widget = dialog.table.cellWidget(0, 2)
    lbl_title = info_widget.layout().itemAt(0).widget()
    assert lbl_title.text() == long_title
