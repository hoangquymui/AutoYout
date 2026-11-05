"""GUI unit tests for YouTube account management in Tab YouTube."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox
from core.settings import load_settings
from ui.tab_youtube import YouTubeConfigTab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_youtube_accounts_tab_structure(app, tmp_path: Path, monkeypatch) -> None:
    creds_dir = tmp_path / "creds"
    creds_dir.mkdir()
    monkeypatch.setattr("core.youtube_uploader.credentials_dir", lambda: creds_dir)

    settings = load_settings()
    settings.setdefault("youtube", {})["channel_languages"] = {}

    # Create dummy tokens for 2 channels
    (creds_dir / "token_default.json").write_text('{"token": "xyz"}', encoding="utf-8")
    (creds_dir / "token_default.meta.json").write_text(
        json.dumps({"title": "Kênh Mặc Định", "channel_id": "UC111", "updated_at": "2026-11-02 12:00:00"}),
        encoding="utf-8",
    )
    (creds_dir / "token_extra.json").write_text('{"token": "abc"}', encoding="utf-8")
    (creds_dir / "token_extra.meta.json").write_text(
        json.dumps({"title": "Kênh Phụ", "channel_id": "UC222", "updated_at": "2026-11-02 12:05:00"}),
        encoding="utf-8",
    )

    tab = YouTubeConfigTab()
    tab.load_from(settings)

    # Verify Accounts table is populated
    assert tab.acc_table.columnCount() == 7
    assert tab.acc_table.rowCount() == 2
    names = [tab.acc_table.item(r, 0).text() for r in range(tab.acc_table.rowCount())]
    assert "default" in names
    assert "extra" in names

    # Status check
    row_default = names.index("default")
    assert tab.acc_table.item(row_default, 1).text() == "Kênh Mặc Định"
    assert tab.acc_table.item(row_default, 2).text() == "UC111"
    assert tab.acc_table.item(row_default, 3).text() == "Tất cả"
    assert "🟢" in tab.acc_table.item(row_default, 4).text()

    # Test update_account_row
    tab._update_account_row("default", {"title": "Kênh Mới", "channel_id": "UC111", "subscribers": "10K", "videos": "50"})
    assert tab.acc_table.item(row_default, 1).text() == "Kênh Mới"
    assert "10K" in tab.acc_table.item(row_default, 5).text()

    # Test assign languages to 'default'
    tab._save_channel_languages("default", ["vi", "ja"])
    assert tab._channel_languages["default"] == ["vi", "ja"]
    saved_settings = {}
    tab.apply_to(saved_settings)
    assert saved_settings["youtube"]["channel_languages"]["default"] == ["vi", "ja"]
    assert (creds_dir / "token_default.meta.json").is_file()
    meta_data = json.loads((creds_dir / "token_default.meta.json").read_text(encoding="utf-8"))
    assert meta_data["languages"] == ["vi", "ja"]

    # Verify table displays languages
    tab.refresh_accounts_table()
    assert "vi, ja" in tab.acc_table.item(0, 3).text()

    # Test delete account with mock QMessageBox.question returning Yes
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    tab._delete_account("extra")

    # Verify 'extra' is removed from table and directory
    assert not (creds_dir / "token_extra.json").exists()
    assert tab.acc_table.rowCount() == 1
    remaining = [tab.acc_table.item(r, 0).text() for r in range(tab.acc_table.rowCount())]
    assert "extra" not in remaining
    assert "default" in remaining
