"""attach_thumbnail copies the image and merges the item JSON."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from core.project_manager import ProjectManager  # noqa: E402
from core.thumbnail import attach_thumbnail  # noqa: E402


def _project(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path)
    (manager.input_dir / "tap_01.txt").write_text("Nội dung tập một.", encoding="utf-8")
    (manager.input_dir / "tap_01.json").write_text(
        json.dumps({"title": "Tập 1: Khởi đầu", "tags": ["truyện", "bí ẩn"]},
                   ensure_ascii=False),
        encoding="utf-8")
    manager.scan()
    return manager


def test_attach_copies_and_merges_metadata(tmp_path: Path) -> None:
    manager = _project(tmp_path)
    source = tmp_path / "draft.png"
    Image.new("RGB", (320, 180), (200, 30, 40)).save(source)

    target = attach_thumbnail(manager, "tap_01", source)

    assert target == manager.thumbs_dir / "tap_01.jpg"
    assert target.is_file()
    with Image.open(target) as image:
        assert image.format == "JPEG" and image.size == (320, 180)

    data = json.loads((manager.input_dir / "tap_01.json").read_text(encoding="utf-8"))
    assert data["title"] == "Tập 1: Khởi đầu"
    assert data["tags"] == ["truyện", "bí ẩn"]
    assert data["thumbnail_path"] == str(target.resolve())

    item = manager.get_item("tap_01")
    assert item is not None
    assert item.metadata["thumbnail_path"] == str(target.resolve())


def test_attach_creates_json_when_missing(tmp_path: Path) -> None:
    manager = ProjectManager(tmp_path)
    (manager.input_dir / "tap_02.txt").write_text("Nội dung tập hai.", encoding="utf-8")
    manager.scan()
    source = tmp_path / "draft.jpg"
    Image.new("RGB", (160, 90), (10, 120, 220)).save(source)

    attach_thumbnail(manager, "tap_02", source)

    data = json.loads((manager.input_dir / "tap_02.json").read_text(encoding="utf-8"))
    assert Path(data["thumbnail_path"]).is_file()
    assert manager.get_item("tap_02").metadata["thumbnail_path"] == data["thumbnail_path"]


def test_attach_rejects_missing_source(tmp_path: Path) -> None:
    manager = _project(tmp_path)
    with pytest.raises(RuntimeError):
        attach_thumbnail(manager, "tap_01", tmp_path / "nope.png")
