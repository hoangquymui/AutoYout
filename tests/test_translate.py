"""Gemini prompt building and chunked translation (no network)."""
from __future__ import annotations

import pytest

from core.ai_client import (
    AIError,
    build_translate_prompt,
    split_for_translation,
    translate_text,
)
from core.languages import language_name


def test_prompt_mentions_both_languages_and_style() -> None:
    prompt = build_translate_prompt("Xin chào", "vi", "en", "giọng kể hấp dẫn")
    assert language_name("en") in prompt
    assert language_name("vi") in prompt
    assert "giọng kể hấp dẫn" in prompt
    assert "Xin chào" in prompt


def test_split_for_translation_respects_limit() -> None:
    paragraph = ("a" * 2700) + "."
    text = "\n\n".join([paragraph] * 3)
    chunks = split_for_translation(text)
    assert len(chunks) == 2
    assert all(len(chunk) <= 6000 for chunk in chunks)


def test_translate_joins_chunks_with_blank_line() -> None:
    paragraph = "Đoạn văn. " * 300
    text = "\n\n".join([paragraph.strip()] * 3)
    calls = []

    def fake(prompt: str, key: str, model: str) -> str:
        calls.append(prompt)
        return "BẢN DỊCH"

    result = translate_text(
        text, "k", source_lang="vi", target_lang="en", generate_fn=fake)
    assert len(calls) == 2
    assert result == "BẢN DỊCH\n\nBẢN DỊCH"


def test_translate_retries_then_succeeds() -> None:
    attempts = {"count": 0}

    def flaky(prompt: str, key: str, model: str) -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("mạng lỗi")
        return "OK"

    result = translate_text(
        "Xin chào", "k", generate_fn=flaky, retry_delay=0.0)
    assert result == "OK"
    assert attempts["count"] == 2


def test_translate_gives_up_after_retries() -> None:
    def broken(prompt: str, key: str, model: str) -> str:
        raise RuntimeError("hỏng")

    with pytest.raises(AIError):
        translate_text("Xin chào", "k", generate_fn=broken, retry_delay=0.0)


def test_translate_requires_key_without_generator() -> None:
    with pytest.raises(AIError):
        translate_text("Xin chào", "")
