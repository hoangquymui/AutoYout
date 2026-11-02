"""Project skeleton: standard folders, status report and project creation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.project_manager import ProjectManager


def test_init_creates_all_standard_folders(tmp_path: Path) -> None:
    manager = ProjectManager(tmp_path)
    expected = {"script", "audio", "video", "thumbs", "assets"}
    assert {path.name for _, path in manager.folders().items()} == expected
    assert all(path.is_dir() for _, path in manager.folders().items())
    status = manager.folder_status()
    assert len(status) == 5
    assert all(entry["exists"] for entry in status)


def test_custom_relative_and_absolute_dirs(tmp_path: Path) -> None:
    manager = ProjectManager(
        tmp_path,
        script_dir="work/script",
        thumbs_dir=str(tmp_path / "custom-thumbs"),
    )
    assert manager.script_dir == (tmp_path / "work" / "script").resolve()
    assert manager.thumbs_dir == (tmp_path / "custom-thumbs").resolve()
    assert manager.script_dir.is_dir()
    assert manager.thumbs_dir.is_dir()


def test_ensure_structure_is_idempotent(tmp_path: Path) -> None:
    manager = ProjectManager(tmp_path)
    assert manager.ensure_structure() == []

    (tmp_path / "thumbs").rmdir()
    assert manager.ensure_structure() == ["thumbs"]
    assert (tmp_path / "thumbs").is_dir()
    assert "thumbs" in manager.created_folders


def test_create_project_builds_state(tmp_path: Path) -> None:
    target = ProjectManager.create_project(tmp_path, "Kênh Truyện 01")
    assert target.is_dir()
    assert (target / "project_state.json").is_file()
    state = json.loads((target / "project_state.json").read_text(encoding="utf-8"))
    assert state["paths"]["script"].endswith("script")
    assert (target / "script").is_dir()


def test_create_project_rejects_non_empty_folder(tmp_path: Path) -> None:
    target = tmp_path / "Kênh Truyện 01"
    target.mkdir()
    (target / "old.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        ProjectManager.create_project(tmp_path, "Kênh Truyện 01")


def test_old_state_without_new_keys_still_opens(tmp_path: Path) -> None:
    state = {
        "version": 1,
        "items": {},
        "videos": {},
        "paths": {"script": str(tmp_path / "script")},
    }
    (tmp_path / "project_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    manager = ProjectManager(tmp_path)
    manager.scan()
    assert manager.script_dir.is_dir()
    assert manager.thumbs_dir.is_dir()
