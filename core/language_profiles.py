"""Target-language profiles (language + prompt) used by the Script tab.

Each profile pairs a target language code with a free-form prompt that tells
Gemini how to clean/adapt the transcript before it is saved as an INPUT file.
Profiles persist in ``config/language_prompt.json`` as a list of
``{"id", "language", "prompt"}`` objects. Qt-free so it can be tested offline.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional

from core.languages import normalize_language
from utils.files import load_json, write_json_atomic

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "language_prompt.json"


@dataclass
class LanguageProfile:
    language: str
    prompt: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict:
        return {"id": self.id, "language": self.language, "prompt": self.prompt}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["LanguageProfile"]:
        if not isinstance(data, dict):
            return None
        language = normalize_language(data.get("language"), "")
        if not language:
            return None
        return cls(
            language=language,
            prompt=str(data.get("prompt") or "").strip(),
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
        )


def _dedupe(profiles: Iterable[LanguageProfile]) -> List[LanguageProfile]:
    """Keep the first profile per language (one prompt per target language)."""
    cleaned: List[LanguageProfile] = []
    seen: set[str] = set()
    for profile in profiles:
        if not profile.language or profile.language in seen:
            continue
        seen.add(profile.language)
        cleaned.append(profile)
    return cleaned


def load_profiles(path: Path = DEFAULT_PATH) -> List[LanguageProfile]:
    raw = load_json(path, [])
    if not isinstance(raw, list):
        return []
    profiles = [LanguageProfile.from_dict(item) for item in raw]
    return _dedupe([profile for profile in profiles if profile is not None])


def save_profiles(profiles: Iterable[LanguageProfile],
                  path: Path = DEFAULT_PATH) -> List[LanguageProfile]:
    cleaned = _dedupe(profiles)
    write_json_atomic(path, [profile.to_dict() for profile in cleaned])
    return cleaned
