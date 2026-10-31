"""Language prompt profiles: JSON store and language normalisation (offline)."""
from __future__ import annotations

from pathlib import Path

from core.language_profiles import LanguageProfile, load_profiles, save_profiles


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_profiles(tmp_path / "nope.json") == []


def test_load_ignores_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "language_prompt.json"
    path.write_text('"not a list"', encoding="utf-8")
    assert load_profiles(path) == []


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "language_prompt.json"
    saved = save_profiles([
        LanguageProfile(language="en", prompt="kể hấp dẫn", id="a1"),
        LanguageProfile(language="vi", prompt="làm sạch", id="b2"),
    ], path)
    assert [p.language for p in saved] == ["en", "vi"]
    loaded = load_profiles(path)
    assert [(p.id, p.language, p.prompt) for p in loaded] == [
        ("a1", "en", "kể hấp dẫn"),
        ("b2", "vi", "làm sạch"),
    ]


def test_from_dict_normalises_language_and_rejects_unknown() -> None:
    profile = LanguageProfile.from_dict({"language": "English", "prompt": "x"})
    assert profile is not None and profile.language == "en"
    assert LanguageProfile.from_dict({"language": "??", "prompt": "x"}) is None
    assert LanguageProfile.from_dict("bad") is None


def test_save_dedupes_by_language(tmp_path: Path) -> None:
    path = tmp_path / "lp.json"
    saved = save_profiles([LanguageProfile("en", "first"),
                           LanguageProfile("en", "second")], path)
    assert len(saved) == 1 and saved[0].prompt == "first"


def test_default_path_is_project_config() -> None:
    from core.language_profiles import DEFAULT_PATH

    assert DEFAULT_PATH.name == "language_prompt.json"
    assert DEFAULT_PATH.parent.name == "config"
