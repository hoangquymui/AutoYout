"""Text normalisation and sentence-safe chunking."""
from __future__ import annotations

from core.text_processor import (
    chunk_text,
    normalize_text,
    split_sentences,
    text_preview,
)


def test_normalize_cleans_decorations_but_keeps_words() -> None:
    raw = "\ufeff# Tiêu đề\r\n**Đậm** và `code`\u00a0với\u200b khoảng trắng.\n\n\n\nĐoạn hai…"
    text = normalize_text(raw)
    assert text.startswith("Tiêu đề")
    assert "**" not in text and "`" not in text
    assert "\r" not in text and "\u00a0" not in text
    assert "\n\n\n" not in text
    assert text.endswith("Đoạn hai...")


def test_split_sentences_handles_punctuation() -> None:
    assert split_sentences("Một. Hai! Ba? Bốn") == ["Một.", "Hai!", "Ba?", "Bốn"]


def test_chunks_respect_max_chars_and_boundaries() -> None:
    sentence = "Đây là một câu tiếng Việt dùng để kiểm tra việc chia đoạn cho TTS. "
    text = ("\n\n".join([sentence * 3] * 4)).strip()
    chunks = chunk_text(text, min_chars=200, max_chars=240,
                        gap_sentence=0.3, gap_paragraph=0.8)
    assert chunks
    assert all(len(chunk.text) <= 240 for chunk in chunks)
    assert chunks[-1].gap_after == 0.0
    assert any(chunk.gap_after == 0.8 for chunk in chunks[:-1])
    assert "".join("".join(c.text.split()) for c in chunks) == "".join(text.split())


def test_long_single_sentence_is_split_on_words() -> None:
    words = " ".join(f"từ{i}" for i in range(120))
    chunks = chunk_text(words, min_chars=80, max_chars=120)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 120 for chunk in chunks)
    assert all(chunk.text and "  " not in chunk.text for chunk in chunks)


def test_min_chars_packs_more_sentences_per_chunk() -> None:
    text = "\n\n".join(["Câu ngắn một. Câu ngắn hai. Câu ngắn ba."] * 3)
    packed = chunk_text(text, min_chars=200, max_chars=350)
    loose = chunk_text(text, min_chars=20, max_chars=350)
    assert packed and loose
    assert len(packed) < len(loose)


def test_text_preview_truncates() -> None:
    assert text_preview("  a   b  ", limit=10) == "a b"
    preview = text_preview("x" * 50, limit=10)
    assert len(preview) == 10 and preview.endswith("…")
