"""Project discovery, metadata pairing and resumable state."""
from __future__ import annotations

import json
from pathlib import Path

from core.project_manager import (
    ITEM_PENDING,
    ITEM_TTS_DONE,
    ProjectManager,
    VideoJob,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_pairs_text_and_json_in_natural_order(tmp_path: Path) -> None:
    inputs = tmp_path / "script"
    _write(inputs / "tap_10.txt", "Phần mười.")
    _write(inputs / "tap_2.txt", "Phần hai.")
    _write(inputs / "tap_1.json", json.dumps({
        "title": "Chỉ có JSON",
        "text_content": "Nội dung nằm trong JSON.",
        "tags": ["một", "hai"],
    }, ensure_ascii=False))
    _write(inputs / "tap_2.json", json.dumps({
        "title": "Tiêu đề hai",
        "description": "Mô tả hai",
        "tags": "a, b; a",
    }, ensure_ascii=False))
    _write(inputs / "broken.json", "{not json")
    _write(inputs / "ignore.md", "không phải input")

    manager = ProjectManager(tmp_path)
    items = manager.scan()

    assert [item.base for item in items] == ["tap_1", "tap_2", "tap_10"]
    assert items[0].metadata["title"] == "Chỉ có JSON"
    assert items[0].text == "Nội dung nằm trong JSON."
    assert items[0].text_file is None
    assert items[1].text_file is not None and items[1].json_file is not None
    assert items[1].metadata["tags"] == ["a", "b; a"]
    assert not manager.get_item("broken")
    assert all(item.state == ITEM_PENDING for item in items)


def test_metadata_thumbnail_resolves_to_assets(tmp_path: Path) -> None:
    _write(tmp_path / "script" / "bai.txt", "Nội dung.")
    _write(tmp_path / "assets" / "thumb.jpg", "fake-image")
    _write(tmp_path / "script" / "bai.json", json.dumps({"thumbnail_path": "thumb.jpg"}))
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("bai")
    assert item is not None
    assert Path(item.metadata["thumbnail_path"]) == (tmp_path / "assets" / "thumb.jpg")
    assert item.metadata["category_id"] == ""
    assert item.metadata["made_for_kids"] is False


def test_state_reconciles_with_existing_audio(tmp_path: Path) -> None:
    _write(tmp_path / "script" / "a.txt", "Nội dung.")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("a")
    assert item is not None and item.state == ITEM_PENDING

    item.audio_path.write_bytes(b"fake-mp3")
    manager = ProjectManager(tmp_path)
    manager.scan()
    assert manager.get_item("a").state == ITEM_TTS_DONE

    manager.get_item("a").audio_path.unlink()
    manager = ProjectManager(tmp_path)
    manager.scan()
    assert manager.get_item("a").state == ITEM_PENDING


def test_custom_folders_are_relative_to_project(tmp_path: Path) -> None:
    manager = ProjectManager(
        tmp_path, script_dir="src", audio_dir="out/audio",
        video_dir="out/video", assets_dir="media",
    )
    assert manager.script_dir == (tmp_path / "src").resolve()
    assert manager.audio_dir == (tmp_path / "out" / "audio").resolve()
    assert manager.video_dir == (tmp_path / "out" / "video").resolve()
    assert manager.assets_dir == (tmp_path / "media").resolve()
    assert manager.script_dir.is_dir() and manager.assets_dir.is_dir()


def test_scan_prunes_videos_whose_inputs_disappeared(tmp_path: Path) -> None:
    _write(tmp_path / "script" / "a.txt", "Nội dung.")
    _write(tmp_path / "script" / "b.txt", "Nội dung khác.")
    manager = ProjectManager(tmp_path)
    manager.scan()
    job = VideoJob(id="one::a", path=tmp_path / "video" / "a.mp4", items=["a"])
    manager.upsert_video(job)

    (tmp_path / "script" / "a.txt").unlink()
    manager.scan()
    assert manager.get_video("one::a") is None


def test_video_lookup_by_item(tmp_path: Path) -> None:
    _write(tmp_path / "script" / "a.txt", "Nội dung.")
    _write(tmp_path / "script" / "b.txt", "Nội dung khác.")
    manager = ProjectManager(tmp_path)
    manager.scan()
    job = VideoJob(id="all", path=tmp_path / "video" / "all.mp4", items=["a", "b"])
    manager.upsert_video(job)
    assert manager.video_for_item("b") is job
    assert manager.video_for_item("missing") is None
