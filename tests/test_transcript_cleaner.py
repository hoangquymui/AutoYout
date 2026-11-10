"""Tests for transcript cleaning (heuristics + AI with chunking and fallback)."""
from unittest.mock import MagicMock, patch
import pytest

from core.ai_client import (
    DEFAULT_CLEAN_PROMPT,
    AIError,
    clean_transcript,
    clean_transcript_heuristics,
    gemini_generate,
)


def test_clean_transcript_heuristics_strips_noise():
    raw = (
        "Này mấy đứa sao giờ giải trí toàn cắm mặt vào đồ điện tử thế? "
        ">> Nhìn đây này. >> W nhiều đồ á. Chơi như nào thế hả anh? "
        "Hôm nay hãy cùng chúng mình trở về tuổi thơ [âm nhạc] và chơi đồ chơi. "
        "A dám đánh bài ở trường à? Tôi mách [âm nhạc] cô >> mách lẻo à. "
        "Trò chơi này tập trung vào việc bốc bài [tiếng cười] [vỗ tay] [__]! >> Hú hồn."
    )
    cleaned = clean_transcript_heuristics(raw)
    assert "[âm nhạc]" not in cleaned
    assert "[tiếng cười]" not in cleaned
    assert "[vỗ tay]" not in cleaned
    assert "[__]" not in cleaned
    assert ">>" not in cleaned
    assert "Nhìn đây này." in cleaned
    assert "tuổi thơ và chơi đồ chơi." in cleaned


def test_clean_transcript_without_api_key_returns_heuristic(monkeypatch):
    monkeypatch.setattr("core.ai_client.get_cloudflare_text_config", lambda: ("", "", ""))
    raw = "Xin chào [âm nhạc] >> hôm nay mình review [tiếng ồn] đồ chơi."
    out = clean_transcript(raw, api_key="")
    assert "[âm nhạc]" not in out
    assert "[tiếng ồn]" not in out
    assert ">>" not in out
    assert "Xin chào hôm nay mình review đồ chơi." in out


def test_clean_transcript_ai_success():
    raw = "Đoạn 1 [âm nhạc] >> câu 1."
    mock_generator = MagicMock(return_value="Đoạn 1 câu 1 đã làm sạch hoàn toàn.")
    out = clean_transcript(raw, api_key="fake-key", generate_fn=mock_generator)
    assert out == "Đoạn 1 câu 1 đã làm sạch hoàn toàn."
    assert mock_generator.call_count == 1


def test_clean_transcript_ai_failure_falls_back_to_heuristic():
    raw = "Nội dung quan trọng [âm nhạc] >> chi tiết 123."
    def fail_generator(*args, **kwargs):
        raise AIError("Gemini lỗi HTTP 503: Spikes in demand")

    out = clean_transcript(raw, api_key="fake-key", generate_fn=fail_generator, retries=2)
    # Must not throw, must return heuristic cleaned text without [âm nhạc] or >>
    assert "[âm nhạc]" not in out
    assert ">>" not in out
    assert "Nội dung quan trọng chi tiết 123." in out


def test_clean_transcript_chunks_long_text():
    # Generate 10000 chars of text
    paragraphs = [f"Đoạn văn số {i}: nội dung tuổi thơ [âm nhạc] và trò chơi >> rất hay." for i in range(120)]
    raw = "\n\n".join(paragraphs)
    assert len(raw) > 8000

    call_count = 0
    def chunk_generator(prompt, key, model):
        nonlocal call_count
        call_count += 1
        return "Đoạn đã sạch " + str(call_count)

    out = clean_transcript(raw, api_key="fake-key", generate_fn=chunk_generator)
    assert call_count >= 2  # Text > 4500 chars was chunked
    assert "[âm nhạc]" not in out
    assert ">>" not in out

