"""Step-1 input pipeline: HTML extraction, text saving and URL validation."""
from __future__ import annotations

import gzip
import sys
import types
from pathlib import Path

import pytest

import core.input_pipeline as pipeline
from core.input_pipeline import (
    InputError,
    cues_to_text,
    fetch_web_text,
    polish_text,
    save_text,
    transcribe_media,
    unique_text_path,
    web_to_text,
)
from core.subtitles import Cue

_HTML = """<!doctype html>
<html><head><title>Bài viết thử</title><style>.x{color:red}</style></head>
<body>
<nav>Menu không lấy</nav>
<article>
  <h1>Tiêu đề lớn</h1>
  <p>Đoạn một&nbsp;nội dung.</p>
  <p>Đoạn hai <b>đậm</b>.</p>
  <script>alert('x')</script>
</article>
<footer>Chân trang</footer>
</body></html>"""


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/html; charset=utf-8",
                 encoding: str = "") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type, "Content-Encoding": encoding}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _patch_url(monkeypatch, body: bytes, **kwargs) -> None:
    monkeypatch.setattr(
        pipeline.urllib.request, "urlopen",
        lambda request, timeout=0: _FakeResponse(body, **kwargs))


def test_fetch_web_text_extracts_visible_text(monkeypatch) -> None:
    _patch_url(monkeypatch, _HTML.encode("utf-8"))
    title, text = fetch_web_text("https://example.com/bai-viet")
    assert title == "Bài viết thử"
    assert "Tiêu đề lớn" in text
    assert "Đoạn một nội dung." in text
    assert "Đoạn hai đậm." in text
    assert "Menu" not in text
    assert "Chân trang" not in text
    assert "alert" not in text


def test_fetch_web_text_handles_gzip(monkeypatch) -> None:
    _patch_url(monkeypatch, gzip.compress(_HTML.encode("utf-8")),
               encoding="gzip")
    _title, text = fetch_web_text("https://example.com")
    assert "Đoạn một nội dung." in text


def test_fetch_web_text_rejects_bad_url() -> None:
    with pytest.raises(InputError):
        fetch_web_text("ftp://example.com")


def test_web_to_text_writes_named_file(monkeypatch, tmp_path: Path) -> None:
    _patch_url(monkeypatch, _HTML.encode("utf-8"))
    target = web_to_text("https://example.com/bai", tmp_path)
    assert target.suffix == ".txt"
    assert "Bài viết thử" in target.name or target.name.startswith("Bài")
    content = target.read_text(encoding="utf-8")
    assert content.startswith("Bài viết thử")
    assert "Đoạn một nội dung." in content


def test_unique_text_path_increments(tmp_path: Path) -> None:
    (tmp_path / "bai.2.txt").write_text("x", encoding="utf-8")
    (tmp_path / "bai.2-2.txt").write_text("x", encoding="utf-8")
    assert unique_text_path(tmp_path, "bai.2").name == "bai.2-3.txt"
    assert unique_text_path(tmp_path, "bài viết mới").name == "bài_viết_mới.txt"


def test_save_text_strips_and_adds_newline(tmp_path: Path) -> None:
    target = save_text(tmp_path / "a.txt", "  Xin chào  ")
    assert target.read_bytes() == "Xin chào\n".encode("utf-8")


def test_polish_without_key_is_noop() -> None:
    assert polish_text("abc", "") == "abc"


def test_download_rejects_bad_url() -> None:
    with pytest.raises(InputError):
        pipeline.download_youtube("not-a-url", ".")

def test_transcribe_missing_media(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        transcribe_media(tmp_path / "missing.mp4", "vi")


def test_cues_to_text_groups_paragraphs() -> None:
    cues = [
        Cue(1, 0.0, 1.0, "Đoạn một."),
        Cue(2, 1.2, 2.0, "Vẫn đoạn một."),
        Cue(3, 5.0, 6.0, "Đoạn hai."),
        Cue(4, 6.1, 7.0, ""),
    ]
    assert cues_to_text(cues) == "Đoạn một. Vẫn đoạn một.\n\nĐoạn hai."
    assert cues_to_text([]) == ""


def test_youtube_to_text_saves_transcript_without_download(
    monkeypatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    def fake_title(url: str) -> str:
        seen["title_url"] = url
        return "Video thử"

    def fake_transcript(url: str, languages, cancel_cb=None):
        seen["langs"] = list(languages)
        return ([Cue(index=1, start=1.0, end=3.0, text="Xin chào"),
                 Cue(index=2, start=4.0, end=6.0, text="Thế giới")], "vi")

    monkeypatch.setattr(pipeline, "fetch_youtube_title", fake_title)
    monkeypatch.setattr(pipeline, "fetch_youtube_transcript", fake_transcript)
    monkeypatch.setattr(
        pipeline, "download_youtube",
        lambda *a, **k: pytest.fail("Không được tải MP4"))

    target = pipeline.youtube_to_text(
        "https://www.youtube.com/watch?v=abc12345678", tmp_path, language="vi")

    assert target.name == "Video_thử.txt"
    assert target.read_text(encoding="utf-8") == "Xin chào Thế giới\n"
    assert seen["langs"] == ["vi", "en"]


def test_youtube_to_text_requires_captions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pipeline, "fetch_youtube_title",
                        lambda url: "Không phụ đề")
    monkeypatch.setattr(
        pipeline, "fetch_youtube_transcript", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline, "download_youtube",
        lambda *a, **k: pytest.fail("Không được tải MP4"))

    with pytest.raises(InputError, match="không có phụ đề"):
        pipeline.youtube_to_text("https://youtu.be/abc12345678", tmp_path)


class _Fetched:
    def __init__(self, rows) -> None:
        self._rows = rows

    def to_raw_data(self):
        return self._rows


class _Transcript:
    language_code = "en"

    def __init__(self, rows) -> None:
        self._rows = rows

    def fetch(self) -> _Fetched:
        return _Fetched(self._rows)


class _TranscriptList:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.languages = []

    def find_transcript(self, languages):
        self.languages = list(languages)
        return _Transcript(self.rows)


def _install_fake_api(monkeypatch, rows) -> dict:
    holder: dict[str, object] = {}

    class FakeApi:
        def list(self, video_id):
            holder["video_id"] = video_id
            return _TranscriptList(rows)

    module = types.ModuleType("youtube_transcript_api")
    module.YouTubeTranscriptApi = FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", module)
    return holder


def test_fetch_youtube_transcript_maps_snippets(monkeypatch) -> None:
    holder = _install_fake_api(monkeypatch, [
        {"text": " Xin chào ", "start": 0.5, "duration": 1.5},
        {"text": "   ", "start": 2.0, "duration": 1.0},
    ])

    found = pipeline.fetch_youtube_transcript(
        "https://youtu.be/abc12345678", ["vi", "en"])

    assert found is not None
    cues, code = found
    assert [cue.text for cue in cues] == ["Xin chào"]
    assert cues[0].start == 0.5 and cues[0].end == 2.0
    assert code == "en"
    assert holder["video_id"] == "abc12345678"


def test_fetch_youtube_transcript_requires_package(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", None)

    with pytest.raises(InputError, match="youtube-transcript-api"):
        pipeline.fetch_youtube_transcript("https://youtu.be/abc12345678")


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
])
def test_youtube_video_id(url: str) -> None:
    assert pipeline.youtube_video_id(url) == "dQw4w9WgXcQ"


def test_youtube_video_id_rejects_unknown_url() -> None:
    with pytest.raises(InputError, match="ID video"):
        pipeline.youtube_video_id("https://www.youtube.com/")
