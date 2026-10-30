"""PipelineWorker video stage against the real renderer (no TTS/upload)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402

import core.pipeline_worker as pipeline_worker  # noqa: E402
from conftest import requires_ffmpeg  # noqa: E402
from core.pipeline_worker import PipelineWorker  # noqa: E402
from core.project_manager import (  # noqa: E402
    ITEM_TTS_DONE,
    ProjectManager,
    VIDEO_DONE,
)
from core.settings import load_settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QCoreApplication.instance() or QCoreApplication([])
    yield instance


def _media(tmp_path: Path, ffmpeg: str) -> Path:
    inputs = tmp_path / "input"
    inputs.mkdir(parents=True)
    (inputs / "tap_01.txt").write_text("Nội dung kiểm thử pipeline.", encoding="utf-8")
    audio = tmp_path / "audio" / "tap_01.mp3"
    audio.parent.mkdir()
    image = tmp_path / "assets" / "bg.png"
    image.parent.mkdir()
    flags = 0x08000000
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", "-c:a", "libmp3lame",
         "-b:a", "192k", str(audio)],
        check=True, creationflags=flags,
    )
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=0x223344:s=640x360:d=1", "-frames:v", "1", str(image)],
        check=True, creationflags=flags,
    )
    return image


def test_tts_stage_marks_item_done(app, tmp_path: Path, monkeypatch) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "tap_01.txt").write_text("Nội dung kiểm thử TTS.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()

    class FakeResult:
        duration = 1.5
        chunks = 1

    class FakeEngine:
        def __init__(self, _settings):
            self.settings = _settings

        def synthesize(self, _text, path, progress_cb=None, **_kwargs):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"ID3" + b"\0" * 64)
            if progress_cb:
                progress_cb(100)
            return FakeResult()

        def close(self):
            pass

    monkeypatch.setattr(pipeline_worker, "TTSEngine", FakeEngine)

    completed: list[tuple[bool, str]] = []
    worker = PipelineWorker(load_settings(), manager, mode="tts")
    worker.completed.connect(lambda ok, message: completed.append((ok, message)))
    worker.run()

    item = manager.get_item("tap_01")
    assert item.state == ITEM_TTS_DONE, item.error
    assert item.error == ""
    assert item.duration == pytest.approx(1.5)


@requires_ffmpeg
def test_video_stage_completes(app, tmp_path: Path, ffmpeg: str) -> None:
    image = _media(tmp_path, ffmpeg)
    manager = ProjectManager(tmp_path)
    manager.scan()

    settings = load_settings()
    settings["video"].update({
        "grouping": "one", "visual": "image", "image": str(image),
        "resolution": "640x360", "encoder": "libx264", "crf": 30,
        "fps": 24, "gap": 0.2, "showwaves": False, "ken_burns": False,
        "chapter_titles": False, "bgm": "",
    })
    settings["app"]["stop_on_error"] = False

    completed: list[tuple[bool, str]] = []
    overall: list[int] = []
    stages: list[str] = []
    worker = PipelineWorker(settings, manager, mode="video", force=True)
    worker.completed.connect(lambda ok, message: completed.append((ok, message)))
    worker.overall_changed.connect(overall.append)
    worker.stage_changed.connect(stages.append)

    worker.run()

    assert completed and completed[0][0] is True, completed
    assert "1/1 video" in completed[0][1]
    assert stages and "VIDEO" in stages[0].upper()
    assert overall and overall[-1] == 100
    job = manager.get_video("one::tap_01")
    assert job is not None and job.state == VIDEO_DONE
    assert job.path.is_file() and job.path.stat().st_size > 2000


def test_notes_accumulate_and_summary_includes_them(app, tmp_path: Path, monkeypatch) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "tap_01.txt").write_text("Nội dung kiểm thử TTS.", encoding="utf-8")
    (inputs / "tap_02.txt").write_text("File này đã có audio.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()

    # tap_02 đã có audio hợp lệ → TTS phải bỏ qua và ghi chú lại.
    item2 = manager.get_item("tap_02")
    item2.audio_path.parent.mkdir(parents=True, exist_ok=True)
    item2.audio_path.write_bytes(b"ID3" + b"\0" * 64)
    manager.update_item("tap_02", state=ITEM_TTS_DONE)

    class FakeResult:
        duration = 1.5
        chunks = 1

    class FakeEngine:
        def __init__(self, _settings):
            self.settings = _settings

        def synthesize(self, _text, path, progress_cb=None, **_kwargs):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"ID3" + b"\0" * 64)
            if progress_cb:
                progress_cb(100)
            return FakeResult()

        def close(self):
            pass

    monkeypatch.setattr(pipeline_worker, "TTSEngine", FakeEngine)

    notes_emitted: list[list[str]] = []
    completed: list[tuple[bool, str]] = []
    worker = PipelineWorker(load_settings(), manager, mode="tts")
    worker.notes_changed.connect(notes_emitted.append)
    worker.completed.connect(lambda ok, message: completed.append((ok, message)))
    worker.run()

    assert worker.notes == ["TTS: bỏ qua 1 file đã có audio."]
    assert notes_emitted and notes_emitted[-1] == worker.notes
    assert notes_emitted[-1] is not worker.notes  # phát ra bản sao, không phải tham chiếu
    assert completed and completed[0][0] is True, completed
    assert "Ghi chú:" in completed[0][1]
    assert "TTS: bỏ qua 1 file đã có audio." in completed[0][1]


def test_retry_attempt_appends_note(app, tmp_path: Path, monkeypatch) -> None:
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "tap_01.txt").write_text("Nội dung kiểm thử retry.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()

    class FlakyResult:
        duration = 1.5
        chunks = 1

    class FlakyEngine:
        calls = 0

        def __init__(self, _settings):
            self.settings = _settings

        def synthesize(self, _text, path, progress_cb=None, **_kwargs):
            FlakyEngine.calls += 1
            if FlakyEngine.calls == 1:
                raise pipeline_worker.TTSError("giả lập lỗi lần đầu")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"ID3" + b"\0" * 64)
            if progress_cb:
                progress_cb(100)
            return FlakyResult()

        def close(self):
            pass

    monkeypatch.setattr(pipeline_worker, "TTSEngine", FlakyEngine)

    settings = load_settings()
    settings["app"]["retry"] = 1
    settings["app"]["stop_on_error"] = False
    completed: list[tuple[bool, str]] = []
    worker = PipelineWorker(settings, manager, mode="tts")
    worker.completed.connect(lambda ok, message: completed.append((ok, message)))
    worker.run()

    assert "Thử lại TTS (1/1) cho 1 mục lỗi." in worker.notes
    assert completed and completed[0][0] is True, completed
    assert "Thử lại TTS (1/1) cho 1 mục lỗi." in completed[0][1]
    assert manager.get_item("tap_01").state == ITEM_TTS_DONE
