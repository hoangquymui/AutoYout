"""Text normalisation and sentence-safe chunking."""
from __future__ import annotations

from core.text_processor import (
    chunk_text,
    normalize_text,
    split_by_words,
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


def test_split_by_words_respects_range_and_sentence_ends() -> None:
    text = " ".join(
        f"Câu số {i} với vài từ để kiểm tra việc cắt theo số từ." for i in range(30))
    pieces = split_by_words(text, min_words=8, max_words=20)
    assert len(pieces) > 1
    assert all(len(piece.split()) <= 20 for piece in pieces)
    # Every piece must end at a sentence boundary (a period), except the last.
    for piece in pieces[:-1]:
        assert piece.strip().endswith(".")
    assert "".join("".join(piece.split()) for piece in pieces) == "".join(text.split())


def test_split_by_words_single_long_sentence_falls_back_to_words() -> None:
    words = " ".join(f"từ{i}" for i in range(50))
    pieces = split_by_words(words, min_words=10, max_words=12)
    assert len(pieces) > 1
    assert all(len(piece.split()) <= 12 for piece in pieces)
    assert "".join("".join(piece.split()) for piece in pieces) == "".join(words.split())


def test_split_by_words_empty_input() -> None:
    assert split_by_words("") == []
    assert split_by_words("   ") == []
