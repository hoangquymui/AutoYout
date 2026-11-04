"""YouTube scheduling and metadata builders (no network/credentials needed)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.project_manager import ProjectManager, VideoJob
from core.youtube_uploader import (
    YouTubeConfig,
    YouTubeUploader,
    build_metadata,
    compute_publish_at,
    parse_times,
    parse_when,
    render_template,
)

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=TZ)


def test_parse_when_date_only_defaults_to_8am() -> None:
    value = parse_when("2026-12-01", NOW)
    assert value is not None
    assert value.date().isoformat() == "2026-12-01"
    assert (value.hour, value.minute) == (8, 0)
    assert value.tzinfo is not None


def test_parse_when_invalid_returns_none() -> None:
    assert parse_when("không phải ngày", NOW) is None
    assert parse_when("", NOW) is None


def test_parse_times_filters_and_sorts() -> None:
    assert parse_times("8:00, 20:30; 25:00, bad, 08:00") == [
        parse_times("08:00")[0], parse_times("20:30")[0],
    ]


def test_compute_publish_at_interval_lead_time() -> None:
    cfg = YouTubeConfig(schedule_mode="interval", schedule_interval_hours=24)
    assert compute_publish_at(cfg, [], NOW) == "2026-10-28T02:10:00Z"


def test_compute_publish_at_interval_after_latest() -> None:
    cfg = YouTubeConfig(schedule_mode="interval", schedule_interval_hours=24)
    latest = NOW + timedelta(hours=2)
    assert compute_publish_at(cfg, [latest], NOW) == "2026-10-29T04:00:00Z"


def test_compute_publish_at_fixed_times() -> None:
    cfg = YouTubeConfig(schedule_mode="times", schedule_times="08:00,20:00")
    assert compute_publish_at(cfg, [], NOW) == "2026-10-28T13:00:00Z"


def test_compute_publish_at_first_date() -> None:
    cfg = YouTubeConfig(
        schedule_mode="interval", schedule_interval_hours=12, schedule_first="2026-12-01",
    )
    assert compute_publish_at(cfg, [], NOW) == "2026-12-01T01:00:00Z"


def test_compute_publish_at_none_mode() -> None:
    assert compute_publish_at(YouTubeConfig(schedule_mode="none"), [], NOW) == ""
    assert compute_publish_at(YouTubeConfig(schedule_mode="metadata"), [], NOW) == ""


def _manager(tmp_path: Path) -> ProjectManager:
    inputs = tmp_path / "script"
    inputs.mkdir(parents=True)
    (inputs / "tap_01.txt").write_text("Nội dung.", encoding="utf-8")
    (inputs / "tap_01.json").write_text(json.dumps({
        "title": "Tiêu đề gốc",
        "description": "Mô tả gốc",
        "tags": ["Thẻ gốc", "autoYout"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "thumb.jpg").write_bytes(b"jpeg")
    manager = ProjectManager(tmp_path)
    manager.scan()
    return manager


def test_build_metadata_renders_templates_and_dedupes_tags(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    job = VideoJob(
        id="one::tap_01",
        path=manager.video_dir / "tap_01.mp4",
        items=["tap_01"],
        title="Tiêu đề job",
        timestamps_text="0:00 Mở đầu\n1:05 Phần hai",
    )
    cfg = YouTubeConfig(
        title_template="{project_name} — {title} ({part_number})",
        description_template="{description}\n\nTimestamps:\n{timestamps}",
        tags="AutoYout, {project_name}, AutoYout",
        category_id="22",
        thumbnail="thumb.jpg",
        privacy="public",
    )
    meta = build_metadata(
        manager, job, cfg, index=1, publish_at="2026-11-01T01:00:00Z",
    )
    assert meta["title"] == f"{tmp_path.name} — Tiêu đề job (1)"
    assert "Mô tả gốc" in meta["description"]
    assert "0:00 Mở đầu" in meta["description"]
    assert meta["tags"] == ["AutoYout", tmp_path.name, "Thẻ gốc"]
    assert meta["privacy"] == "private"
    assert meta["publish_at"] == "2026-11-01T01:00:00Z"
    assert meta["thumbnail"] == tmp_path / "assets" / "thumb.jpg"
    assert meta["category_id"] == "22"


def test_build_metadata_drops_empty_timestamps_section(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    job = VideoJob(id="group::03", path=manager.video_dir / "part_03.mp4",
                   items=["tap_01"], title="Phần ba")
    cfg = YouTubeConfig(
        description_template="{description}\n\n⏱ Timestamps:\n{timestamps}",
        privacy="public",
    )
    meta = build_metadata(manager, job, cfg, index=3)
    assert meta["values"]["part_number"] == "3"
    assert "timestamps" not in meta["description"].lower()
    assert meta["description"] == "Mô tả gốc"
    assert meta["privacy"] == "public"
    assert meta["publish_at"] == ""


def test_render_template_keeps_unknown_placeholders() -> None:
    assert render_template("{a}-{b}", {"a": "1"}) == "1-{b}"


def test_delete_channel_removes_token_and_meta(tmp_path: Path) -> None:
    settings = {"paths": {"youtube_credentials": str(tmp_path)}}
    uploader = YouTubeUploader(settings)
    token = uploader.token_path("test_channel")
    meta = uploader.meta_path("test_channel")
    token.write_text("{}", encoding="utf-8")
    meta.write_text("{}", encoding="utf-8")
    assert token.is_file() and meta.is_file()
    assert uploader.delete_channel("test_channel") is True
    assert not token.is_file() and not meta.is_file()
    assert uploader.delete_channel("test_channel") is False


def test_open_upload_vietnamese_selector() -> None:
    from unittest.mock import MagicMock
    uploader = YouTubeUploader({})
    page = MagicMock()
    
    file_input = MagicMock()
    file_input.first.count.return_value = 0
    file_input.count.return_value = 0
    
    upload_icon = MagicMock()
    upload_icon.count.return_value = 1
    upload_icon.is_visible.return_value = True
    
    def mock_locator(sel: str):
        mock_loc = MagicMock()
        if sel == "input[type='file']":
            return file_input
        if "#upload-icon" in sel:
            mock_loc.first = upload_icon
        else:
            empty = MagicMock()
            empty.count.return_value = 0
            mock_loc.first = empty
        return mock_loc

    page.locator.side_effect = mock_locator
    uploader._open_upload(page)
    upload_icon.click.assert_called_once()


def test_handle_auth_dialog_headless_raises_auth_required() -> None:
    import pytest
    from unittest.mock import MagicMock
    from core.youtube_uploader import AuthRequired

    uploader = YouTubeUploader({})
    page = MagicMock()

    auth_dlg = MagicMock()
    auth_dlg.count.return_value = 1
    auth_dlg.is_visible.return_value = True

    def mock_locator(sel: str):
        mock_loc = MagicMock()
        if "ytcp-auth-confirmation-dialog" in sel:
            mock_loc.first = auth_dlg
        else:
            empty = MagicMock()
            empty.count.return_value = 0
            mock_loc.first = empty
        return mock_loc

    page.locator.side_effect = mock_locator
    cfg = YouTubeConfig(browser_headless=True)
    report = MagicMock()

    with pytest.raises(AuthRequired) as exc_info:
        uploader._handle_auth_dialog(page, cfg, report, lambda: False)
    assert "xác minh danh tính" in str(exc_info.value).lower()
