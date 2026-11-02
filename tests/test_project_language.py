"""Per-file language detection, persistence and manager overrides."""
from __future__ import annotations

import json
from pathlib import Path

from core.project_manager import ProjectManager


def _write_inputs(tmp_path: Path) -> Path:
    inputs = tmp_path / "script"
    inputs.mkdir(parents=True)
    (inputs / "intro_en.txt").write_text("Hello world.", encoding="utf-8")
    (inputs / "bai_02.txt").write_text("Xin chào.", encoding="utf-8")
    (inputs / "bai_02.json").write_text(
        json.dumps({"title": "Bài 2", "language": "ko"}), encoding="utf-8")
    (inputs / "bai_03.txt").write_text("Bonjour.", encoding="utf-8")
    return inputs


def test_scan_detects_language_from_suffix_and_metadata(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    manager = ProjectManager(tmp_path, default_language="vi")
    manager.scan()
    assert manager.get_item("intro_en").language == "en"
    assert manager.get_item("bai_02").language == "ko"
    assert manager.get_item("bai_03").language == "vi"


def test_default_language_applies_to_plain_files(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    manager = ProjectManager(tmp_path, default_language="fr")
    manager.scan()
    assert manager.get_item("bai_03").language == "fr"
    # Explicit signals still win over the project default.
    assert manager.get_item("intro_en").language == "en"
    assert manager.get_item("bai_02").language == "ko"


def test_language_override_is_persisted(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    manager = ProjectManager(tmp_path)
    manager.scan()
    manager.set_item_language("bai_03", "ja")

    reloaded = ProjectManager(tmp_path)
    reloaded.scan()
    assert reloaded.get_item("bai_03").language == "ja"

    state = json.loads((tmp_path / "project_state.json").read_text(encoding="utf-8"))
    assert state["items"]["bai_03"]["language"] == "ja"


def test_normalizes_unknown_language_override(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    manager = ProjectManager(tmp_path)
    manager.scan()
    manager.set_item_language("bai_03", "klingon")
    assert manager.get_item("bai_03").language == "vi"
