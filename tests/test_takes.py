"""Multi-take (image/voice variants) at the data + engine layer."""
from __future__ import annotations

import json
from pathlib import Path

from core.ai_images import find_variant_file, variant_stems
from core.project_manager import ITEM_PENDING, ProjectManager
from core.tts_engine import audio_variant_paths


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_old_state_without_take_keys_defaults(tmp_path: Path) -> None:
    """State JSON written before the takes feature loads with []/0 defaults."""
    _write(tmp_path / "script" / "a.txt", "Nội dung.")
    legacy_audio = str((tmp_path / "audio" / "a.mp3").resolve())
    (tmp_path / "project_state.json").write_text(
        json.dumps({
            "version": 1,
            "items": {
                "a": {
                    "base": "a",
                    "text_file": "",
                    "json_file": "",
                    "text_hash": "abc",
                    "audio_path": legacy_audio,
                    "duration": 12.5,
                    "audio_mtime": 0.0,
                    "state": "PENDING",
                    "error": "",
                    "video_id": "",
                    "metadata_error": "",
                    "updated_at": "2024-01-01T00:00:00",
                },
            },
            "videos": {},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("a")
    assert item is not None
    assert item.image_takes == []
    assert item.selected_image_index == 0
    assert item.voice_takes == []
    assert item.selected_voice_index == 0
    assert item.audio_path == Path(legacy_audio)
    assert item.state == ITEM_PENDING


def test_takes_round_trip_and_select_take(tmp_path: Path) -> None:
    """New take fields survive save/scan, and select_take syncs the primary path."""
    _write(tmp_path / "script" / "a.txt", "Nội dung.")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("a")
    assert item is not None

    item.image_takes = ["ai/a-1.png", "ai/a-2.png", "ai/a-3.png"]
    item.selected_image_index = 2
    item.voice_takes = [
        str(tmp_path / "audio" / "a-1.mp3"),
        str(tmp_path / "audio" / "a-2.mp3"),
    ]
    item.selected_voice_index = 0
    item.audio_path = Path(item.voice_takes[0])
    manager.save_state()

    reloaded = ProjectManager(tmp_path)
    reloaded.scan()
    ritem = reloaded.get_item("a")
    assert ritem is not None
    assert ritem.image_takes == item.image_takes
    assert ritem.selected_image_index == 2
    assert ritem.voice_takes == item.voice_takes
    assert ritem.selected_voice_index == 0
    assert ritem.audio_path == Path(ritem.voice_takes[0])

    # Switch the voice take → audio_path follows.
    assert reloaded.select_take("a", "voice", 1) is ritem
    assert ritem.selected_voice_index == 1
    assert ritem.audio_path == Path(ritem.voice_takes[1])
    # Switch the image take → only the index changes.
    assert reloaded.select_take("a", "image", 0) is ritem
    assert ritem.selected_image_index == 0
    assert ritem.audio_path == Path(ritem.voice_takes[1])

    # Out-of-range indices clamp; unknown kinds are ignored.
    assert ritem.select_take("image", 99) and ritem.selected_image_index == 2
    assert ritem.select_take("voice", -5) and ritem.selected_voice_index == 0
    assert not ritem.select_take("video", 0)
    assert reloaded.select_take("missing", "voice", 0) is None


def test_select_take_on_empty_takes_keeps_audio(tmp_path: Path) -> None:
    """With no candidate takes yet, select_take is a no-op that reports False."""
    _write(tmp_path / "script" / "a.txt", "Nội dung.")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("a")
    assert item is not None
    default = item.audio_path
    assert not item.select_take("voice", 0)
    assert not item.select_take("image", 0)
    assert item.audio_path == default
    assert item.selected_voice_index == 0 and item.selected_image_index == 0


def test_image_variant_stems_are_deterministic(tmp_path: Path) -> None:
    """Variant filenames follow ``<base>-1`` … ``<base>-N`` (unit test, no network)."""
    stems = variant_stems(tmp_path / "ai", "tap_1", 3)
    assert [stem.name for stem in stems] == ["tap_1-1", "tap_1-2", "tap_1-3"]
    assert all(stem.parent == tmp_path / "ai" for stem in stems)
    assert variant_stems(tmp_path / "ai", "tap_1", 0) == [tmp_path / "ai" / "tap_1-1"]
    assert variant_stems(tmp_path / "ai", "tap_1", 1) == [tmp_path / "ai" / "tap_1-1"]


def test_find_variant_file_reuses_cache(tmp_path: Path) -> None:
    """Existing ``<base>-<v>.<ext>`` files are found; missing or wrong ones are not."""
    ai = tmp_path / "ai"
    ai.mkdir()
    (ai / "tap_1-1.png").write_bytes(b"png")
    (ai / "tap_1-2.jpg").write_bytes(b"jpg")
    (ai / "tap_1-3.txt").write_bytes(b"not an image")
    assert find_variant_file(ai, "tap_1", 1) == ai / "tap_1-1.png"
    assert find_variant_file(ai, "tap_1", 2) == ai / "tap_1-2.jpg"
    assert find_variant_file(ai, "tap_1", 3) is None
    assert find_variant_file(ai, "tap_1", 4) is None
    assert find_variant_file(tmp_path / "missing", "tap_1", 1) is None


def test_audio_variant_paths_are_deterministic() -> None:
    """Audio variant filenames follow ``<stem>-1<suffix>`` … (unit test, no model)."""
    paths = audio_variant_paths(Path("out") / "audio" / "tap_1.mp3", 3)
    assert paths == [
        Path("out") / "audio" / "tap_1-1.mp3",
        Path("out") / "audio" / "tap_1-2.mp3",
        Path("out") / "audio" / "tap_1-3.mp3",
    ]
    assert audio_variant_paths("tap_1.wav", 0) == [Path("tap_1-1.wav")]
    assert audio_variant_paths("tap_1.mp3", 1) == [Path("tap_1-1.mp3")]


def test_add_and_remove_takes(tmp_path: Path) -> None:
    """add_take/remove_take dedupe, re-select and re-sync the audio path."""
    _write(tmp_path / "script" / "a.txt", "Nội dung.")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("a")
    assert item is not None

    img1 = str(tmp_path / "ai" / "a-1.png")
    img2 = str(tmp_path / "ai" / "a-2.png")
    assert manager.add_take("a", "image", img1) is item
    assert manager.add_take("a", "image", img2) is item
    assert manager.add_take("a", "image", img1) is item  # duplicate re-selects
    assert item.image_takes == [img1, img2]
    assert item.selected_image_index == 0

    voice1 = str(tmp_path / "audio" / "a-1.mp3")
    voice2 = str(tmp_path / "audio" / "a-2.mp3")
    manager.add_take("a", "voice", voice1)
    manager.add_take("a", "voice", voice2)
    assert item.voice_takes == [voice1, voice2]
    assert item.selected_voice_index == 1
    assert item.audio_path == Path(voice2)

    manager.remove_take("a", "voice", 1)
    assert item.voice_takes == [voice1]
    assert item.selected_voice_index == 0
    assert item.audio_path == Path(voice1)

    manager.remove_take("a", "voice", 0)
    assert item.voice_takes == []
    assert item.audio_path == (tmp_path / "audio" / "a.mp3")

    manager.remove_take("a", "image", 0)
    assert item.image_takes == [img2]
    assert item.selected_image_index == 0

    assert manager.add_take("a", "video", "x") is item
    assert manager.remove_take("a", "video", 0) is item
    assert manager.remove_take("missing", "image", 0) is None
