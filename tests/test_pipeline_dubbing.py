"""PipelineWorker routing: cue dubbing vs. the classic whole-file TTS branch."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402

import core.pipeline_worker as pipeline_worker  # noqa: E402
from core.dubbing import STATUS_DONE, CueJob, load_cues, save_cues  # noqa: E402
from core.pipeline_worker import PipelineWorker  # noqa: E402
from core.project_manager import ITEM_TTS_DONE, ProjectManager  # noqa: E402
from core.settings import load_settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QCoreApplication.instance() or QCoreApplication([])
    yield instance


class _FakeResult:
    duration = 1.5
    chunks = 1


class _FakeEngine:
    calls: list[str] = []

    def __init__(self, _settings):
        self.settings = _settings

    def synthesize(self, text, path, progress_cb=None, **_kwargs):
        _FakeEngine.calls.append(text)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"ID3" + b"\0" * 64)
        if progress_cb:
            progress_cb(100)
        return _FakeResult()

    def close(self):
        pass


def _project(tmp_path: Path) -> tuple[ProjectManager, str]:
    inputs = tmp_path / "script"
    inputs.mkdir()
    (inputs / "tap.txt").write_text("Nội dung lồng tiếng thử.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()
    manager.update_item("tap", dub_mode="srt")
    return manager, "tap"


def test_srt_item_without_cues_auto_splits_and_dubs(app, tmp_path: Path, monkeypatch) -> None:
    manager, base = _project(tmp_path)
    calls: list[str] = []

    class FakeDubbing:
        def __init__(self, _settings):
            self.settings = _settings

        def synthesize_item(self, _manager, _item, cues, progress_cb=None, **_kwargs):
            calls.append("synthesize")
            for cue in cues:
                cue.status = STATUS_DONE
                cue.out = "audio/cues/tap/0001.mp3"
            if progress_cb:
                progress_cb(100)
            return 2.0

        def join_item(self, _manager, item, _cues, mode="timeline", gap=None):
            calls.append(f"join:{mode}")
            path = Path(item.audio_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"ID3" + b"\0" * 64)
            return path, None, 2.0

    monkeypatch.setattr(pipeline_worker, "DubbingEngine", FakeDubbing)
    monkeypatch.setattr(pipeline_worker, "TTSEngine", _FakeEngine)
    _FakeEngine.calls = []

    settings = load_settings()
    settings.setdefault("dubbing", {})["timing_mode"] = "timeline"
    completed: list[tuple[bool, str]] = []
    worker = PipelineWorker(settings, manager, mode="tts", force=True)
    worker.completed.connect(lambda ok, message: completed.append((ok, message)))
    worker.run()

    assert completed and completed[0][0] is True, completed
    assert manager.get_item(base).state == ITEM_TTS_DONE
    assert manager.get_item(base).audio_path.is_file()
    assert calls == ["synthesize", "join:timeline"]
    assert load_cues(manager, base), "cue phải được tự sinh khi file srt chưa có cue"
    assert not _FakeEngine.calls, "không được rơi về TTS toàn file"


def test_srt_item_with_cues_uses_dubbing_engine(app, tmp_path: Path, monkeypatch) -> None:
    manager, base = _project(tmp_path)
    save_cues(manager, base, [CueJob(1, 0.0, 2.0, "Xin chào.")])
    calls: list[str] = []

    class FakeDubbing:
        def __init__(self, _settings):
            self.settings = _settings

        def synthesize_item(self, _manager, _item, cues, progress_cb=None, **_kwargs):
            calls.append("synthesize")
            for cue in cues:
                cue.status = STATUS_DONE
                cue.out = "audio/cues/tap/0001.mp3"
            if progress_cb:
                progress_cb(100)
            return 2.0

        def join_item(self, _manager, item, _cues, mode="timeline", gap=None):
            calls.append(f"join:{mode}")
            path = Path(item.audio_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"ID3" + b"\0" * 64)
            srt = path.with_suffix(".srt")
            srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nXin chào.\n", encoding="utf-8")
            return path, srt, 2.0

    monkeypatch.setattr(pipeline_worker, "DubbingEngine", FakeDubbing)
    monkeypatch.setattr(pipeline_worker, "TTSEngine", _FakeEngine)
    _FakeEngine.calls = []

    settings = load_settings()
    settings.setdefault("dubbing", {})["timing_mode"] = "natural"
    completed: list[tuple[bool, str]] = []
    worker = PipelineWorker(settings, manager, mode="tts")
    worker.completed.connect(lambda ok, message: completed.append((ok, message)))
    worker.run()

    item = manager.get_item(base)
    assert completed and completed[0][0] is True, completed
    assert item.state == ITEM_TTS_DONE
    assert item.duration == pytest.approx(2.0)
    assert item.audio_path.is_file()
    assert calls == ["synthesize", "join:natural"]
    assert not _FakeEngine.calls, "không được rơi về TTS toàn file"


def test_missing_srt_auto_recovered_when_audio_exists(app, tmp_path: Path) -> None:
    inputs = tmp_path / "script"
    inputs.mkdir()
    (inputs / "story.txt").write_text("Câu chuyện bắt đầu tại một ngôi làng nhỏ.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("story")
    # Simulate audio file exists, but srt does NOT exist
    item.audio_path.parent.mkdir(parents=True, exist_ok=True)
    item.audio_path.write_bytes(b"ID3" + b"\0" * 64)
    manager.update_item("story", state=ITEM_TTS_DONE, duration=3.0)

    srt_path = item.audio_path.with_suffix(".srt")
    assert not srt_path.is_file()

    settings = load_settings()
    completed = []
    worker = PipelineWorker(settings, manager, mode="tts")
    worker.completed.connect(lambda ok, msg: completed.append((ok, msg)))
    worker.run()

    assert completed and completed[0][0] is True
    assert srt_path.is_file(), "File .srt phải được tự động tạo khi chỉ thiếu srt"
    assert "Câu chuyện" in srt_path.read_text(encoding="utf-8")


def test_ffmpeg_renderer_auto_recovers_missing_srt(tmp_path: Path) -> None:
    from core.ffmpeg_renderer import FFmpegRenderer, VideoConfig
    inputs = tmp_path / "script"
    inputs.mkdir()
    (inputs / "clip.txt").write_text("Đoạn video ngắn về công nghệ.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_item("clip")
    item.audio_path.parent.mkdir(parents=True, exist_ok=True)
    item.audio_path.write_bytes(b"ID3" + b"\0" * 64)
    manager.update_item("clip", state=ITEM_TTS_DONE, duration=2.5)

    srt_path = item.audio_path.with_suffix(".srt")
    assert not srt_path.is_file()

    renderer = FFmpegRenderer({})
    cfg = VideoConfig(subtitle_srt=True)
    sources = renderer._srt_sources(manager, [item], cfg)
    assert len(sources) == 1
    assert sources[0][1] == srt_path
    assert srt_path.is_file(), "FFmpegRenderer phải tự tạo SRT từ kịch bản nếu thiếu"
