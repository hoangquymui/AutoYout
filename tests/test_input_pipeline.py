"""Step-1 input pipeline: HTML extraction, text saving and URL validation."""
from __future__ import annotations

import gzip
from pathlib import Path

import pytest

import core.input_pipeline as pipeline
from core.input_pipeline import (
    InputError,
    fetch_web_text,
    polish_text,
    save_text,
    transcribe_media,
    unique_text_path,
    web_to_text,
)

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
