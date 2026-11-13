"""Regression tests for TTS provider routing, Piper chunking/SRT cues, and Dubbing voice resolution."""
from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

import core.dubbing as dubbing_mod
import core.pipeline_worker as pipeline_worker
from core.dubbing import STATUS_DONE, CueJob, DubbingEngine
from core.piper_tts_engine import PiperTTSEngine
from core.project_manager import ProjectManager
from core.settings import load_settings
from ui.tab_tts import TTSConfigTab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _write_dummy_wav(path: Path, seconds: float = 0.4, rate: int = 22050) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(seconds * rate)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00" * (frames * 2))


def test_piper_preserves_paragraphs_gaps_and_returns_cues(tmp_path: Path, monkeypatch) -> None:
    """PiperTTSEngine must honor paragraph breaks, gaps, ffprobe, and emit synchronized cues."""
    piper_root = tmp_path / "PiperTTS"
    bin_path = piper_root / "piper.exe"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_bytes(b"exe")
    model_path = piper_root / "voices" / "vi" / "vi_VN-vais1000-medium.onnx"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"onnx")
    model_path.with_name(f"{model_path.name}.json").write_text("{}", encoding="utf-8")

    settings = {
        "paths": {"piper": str(piper_root), "ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
        "tts": {
            "max_chars": 200,
            "gap_sentence": 0.2,
            "gap_paragraph": 0.6,
            "bitrate": "192k",
            "speed": 1.0,
        },
    }
    engine = PiperTTSEngine(settings)

    called_inputs: list[str] = []
    ffmpeg_calls: list[list[str]] = []

    def fake_run(cmd, input=None, **_kwargs):
        cmd_list = [str(c) for c in cmd]
        res = MagicMock()
        res.returncode = 0
        res.stderr = b""
        if "piper.exe" in cmd_list[0]:
            called_inputs.append(input.decode("utf-8"))
            out_idx = cmd_list.index("--output_file") + 1
            _write_dummy_wav(Path(cmd_list[out_idx]), seconds=0.5)
        else:
            ffmpeg_calls.append(cmd_list)
            Path(cmd_list[-1]).write_bytes(b"ID3" + b"\x00" * 64)
        return res

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("core.piper_tts_engine.probe_duration", lambda p, ffprobe=None: 1.6)

    # Two long paragraphs exceeding 200 chars total so chunk_text produces >= 2 chunks with gap_paragraph
    p1 = " ".join(["Câu một trong đoạn văn thứ nhất rất dài để kiểm tra chia đoạn."] * 4)
    p2 = " ".join(["Câu hai trong đoạn văn thứ hai cũng dài tương tự để tạo khoảng nghỉ."] * 4)
    out_mp3 = tmp_path / "out.mp3"
    result = engine.synthesize(f"{p1}\n\n{p2}", out_mp3, language="vi")

    assert len(called_inputs) >= 2
    assert result.chunks == len(called_inputs)
    assert result.cues is not None
    assert len(result.cues) == len(called_inputs)
    # Gap between paragraphs (> 0.5s) should push second cue start past first cue end
    assert result.cues[1][0] > result.cues[0][1] + 0.1
    assert ffmpeg_calls and "-f" in ffmpeg_calls[0] and "concat" in ffmpeg_calls[0]


def test_dubbing_h2mtts_uses_h2m_voices_even_when_tts_voice_is_piper(tmp_path: Path, monkeypatch) -> None:
    """When tts['voice'] holds a Piper model ID, H2MTTS dubbing must still use h2m_voices['vi']."""
    settings = load_settings()
    settings["tts"]["provider"] = "piper"
    settings["tts"]["voice"] = "vi_VN-vais1000-medium"
    settings["tts"]["h2m_voices"] = {"vi": "Ngọc Huyền"}
    settings["tts"]["language_engines"] = {"vi": "h2mtts"}

    recorded_voices: list[str | None] = []

    class FakeH2M:
        def __init__(self, _settings):
            pass

        def synthesize(self, text, out_path, voice=None, **_kwargs):
            recorded_voices.append(voice)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"ID3" + b"\x00" * 64)

        def close(self):
            pass

    monkeypatch.setattr(dubbing_mod, "TTSEngine", FakeH2M)

    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "ep1.txt").write_text("Xin chào.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("ep1")

    dub = DubbingEngine(settings)
    cues = [CueJob(1, 0.0, 2.0, "Xin chào.")]
    dub.synthesize_item(manager, item, cues)

    assert cues[0].status == STATUS_DONE
    assert recorded_voices == ["Ngọc Huyền"]


def test_pipeline_h2mtts_uses_h2m_voices_when_tts_voice_is_piper(app, tmp_path: Path, monkeypatch) -> None:
    """PipelineWorker must pass h2m_voices['vi'] to TTSEngine so Piper voice selection doesn't hijack H2MTTS."""
    settings = load_settings()
    settings["tts"]["provider"] = "piper"
    settings["tts"]["voice"] = "vi_VN-vais1000-medium"
    settings["tts"]["h2m_voices"] = {"vi": "Ngọc Huyền"}
    settings["tts"]["language_engines"] = {"vi": "h2mtts"}

    recorded_voices: list[str | None] = []

    class FakeResult:
        duration = 1.2
        chunks = 1
        engine = "turbo"
        cues = None

    class FakeH2M:
        def __init__(self, _settings):
            pass

        def synthesize(self, text, out_path, voice=None, progress_cb=None, **_kwargs):
            recorded_voices.append(voice)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"ID3" + b"\x00" * 64)
            if progress_cb:
                progress_cb(100)
            return FakeResult()

        def close(self):
            pass

    monkeypatch.setattr(pipeline_worker, "TTSEngine", FakeH2M)

    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "ep1.txt").write_text("Nội dung tiếng Việt.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()

    worker = pipeline_worker.PipelineWorker(settings, manager, mode="tts")
    completed: list[tuple[bool, str]] = []
    worker.completed.connect(lambda ok, msg: completed.append((ok, msg)))
    worker.run()

    assert completed and completed[0][0] is True
    assert recorded_voices == ["Ngọc Huyền"]


def test_tts_page_apply_to_does_not_overwrite_h2m_voice_with_edge_or_piper(app) -> None:
    settings = load_settings()
    settings["tts"]["voice"] = "Hải Đăng"
    settings["tts"]["h2m_voices"] = {"vi": "Hải Đăng"}
    settings["tts"]["edge_voices"] = {"en": "en-US-AriaNeural"}

    page = TTSConfigTab()
    page.load_from(settings)

    # Switch to EdgeTTS and select an English voice
    idx_edge = page.provider_combo.findData("edge")
    page.provider_combo.setCurrentIndex(idx_edge)
    idx_en = page.language_combo.findData("en")
    if idx_en >= 0:
        page.language_combo.setCurrentIndex(idx_en)

    out_settings = load_settings()
    out_settings["tts"]["voice"] = "Hải Đăng"
    page.apply_to(out_settings)

    assert out_settings["tts"]["voice"] == "Hải Đăng"
    assert out_settings["tts"]["edge_voices"].get("en")
