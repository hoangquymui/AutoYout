"""edge-tts wrapper: offline fake module, voice map and missing dependency."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from core.edge_tts_engine import EdgeTTSEngine
from core.tts_engine import TTSError


class _FakeCommunicate:
    def __init__(self, text, voice, rate="+0%"):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.seen = []

    async def stream(self):
        self.seen.append(("start", self.text, self.voice, self.rate))
        yield {"type": "audio", "data": b"ID3" + b"\x00" * 512}
        yield {"type": "WordBoundary", "offset": 0}

    @classmethod
    def make(cls):
        return cls


def _install_fake(monkeypatch) -> types.ModuleType:
    module = types.ModuleType("edge_tts")
    module.Communicate = _FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    return module


def test_missing_dependency_raises_tts_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    engine = EdgeTTSEngine({})
    with pytest.raises(TTSError) as info:
        engine.synthesize("hello", "out.mp3", language="en")
    assert "edge-tts" in str(info.value)


def test_synthesizes_with_language_voice(monkeypatch, tmp_path: Path) -> None:
    _install_fake(monkeypatch)
    engine = EdgeTTSEngine({"tts": {"edge_voices": {"en": "en-GB-RyanNeural"}}})
    assert engine.voice_for("en") == "en-GB-RyanNeural"
    assert engine.voice_for("zh") == "zh-CN-XiaoxiaoNeural"

    target = tmp_path / "voice.mp3"
    result = engine.synthesize("Hello from AutoYout.", target, language="en")
    assert target.is_file() and target.stat().st_size > 0
    assert result.engine == "edge-tts"
    assert result.backend == "online"
    assert result.voice == "en-GB-RyanNeural"
    assert result.sample_rate == 24000
    assert result.chunks >= 1
    assert result.duration > 0
    assert result.characters == len("Hello from AutoYout.")


def test_rate_conversion() -> None:
    assert EdgeTTSEngine._rate(1.0) == "+0%"
    assert EdgeTTSEngine._rate(1.25) == "+25%"
    assert EdgeTTSEngine._rate(0.8) == "-20%"
    assert EdgeTTSEngine._rate(None) == "+0%"


def test_empty_text_rejected(monkeypatch, tmp_path: Path) -> None:
    _install_fake(monkeypatch)
    engine = EdgeTTSEngine({})
    with pytest.raises(TTSError):
        engine.synthesize("   ", tmp_path / "x.mp3", language="en")


def test_cancel_before_synthesis(monkeypatch, tmp_path: Path) -> None:
    from core.tts_engine import SynthesisCancelled

    _install_fake(monkeypatch)
    engine = EdgeTTSEngine({})
    with pytest.raises(SynthesisCancelled):
        engine.synthesize("hello", tmp_path / "x.mp3", language="en",
                          cancel_cb=lambda: True)
