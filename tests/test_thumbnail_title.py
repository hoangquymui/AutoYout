"""Title hook parsing, Gemini prompting and image prompt composition."""
from __future__ import annotations

import pytest

from core.ai_client import AIError
from core.thumbnail import (
    build_image_prompt,
    parse_title_suggestions,
    suggest_titles,
    translate_thumbnail_titles,
)


def test_parse_plain_json_array() -> None:
    raw = '["Bí mật động trời", "Bạn không ngờ điều này", "Sự thật bị che giấu"]'
    assert parse_title_suggestions(raw) == [
        "Bí mật động trời", "Bạn không ngờ điều này", "Sự thật bị che giấu"]


def test_parse_fenced_json_block() -> None:
    raw = 'Đây là kết quả:\n```json\n["Một", "Hai"]\n```\nHết.'
    assert parse_title_suggestions(raw) == ["Một", "Hai"]


def test_parse_multiline_text_cleans_bullets_and_numbers() -> None:
    raw = "1. Điều đầu tiên\n- Điều thứ hai\n• Điều thứ ba\n\nĐiều thứ tư\n"
    assert parse_title_suggestions(raw) == [
        "Điều đầu tiên", "Điều thứ hai", "Điều thứ ba", "Điều thứ tư"]


def test_parse_deduplicates_and_limits() -> None:
    raw = '["Lặp", "lặp", "Ba", "Bốn", "Năm", "Sáu"]'
    assert parse_title_suggestions(raw, limit=4) == ["Lặp", "Ba", "Bốn", "Năm"]


def test_parse_empty_returns_empty() -> None:
    assert parse_title_suggestions("") == []
    assert parse_title_suggestions("   \n  ") == []


def test_suggest_titles_uses_injected_generator() -> None:
    captured: dict[str, str] = {}

    def fake(prompt: str, key: str, model: str) -> str:
        captured["prompt"] = prompt
        captured["key"] = key
        captured["model"] = model
        return '["Tiêu đề MỘT", "Tiêu đề HAI"]'

    titles = suggest_titles(
        "Ngày xưa có một ngôi làng bí ẩn.", "key-1",
        model="gemini-test", generate_fn=fake)

    assert titles == ["Tiêu đề MỘT", "Tiêu đề HAI"]
    assert captured["key"] == "key-1"
    assert captured["model"] == "gemini-test"
    assert "hook tò mò" in captured["prompt"]
    assert "Ngày xưa có một ngôi làng bí ẩn." in captured["prompt"]


def test_suggest_titles_requires_key_without_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.ai_client.get_cloudflare_text_config", lambda: ("", "", ""))
    with pytest.raises(AIError):
        suggest_titles("Nội dung", "")


def test_suggest_titles_requires_transcript() -> None:
    with pytest.raises(AIError):
        suggest_titles("", "key", generate_fn=lambda *_: "[]")


def test_build_image_prompt_has_safety_tail_and_subject() -> None:
    prompt = build_image_prompt(
        "Bí mật", "Một người đàn ông mở cánh cửa cũ.", "cinematic", "drama_left")
    assert "no text" in prompt
    assert "no watermark" in prompt
    assert "main subject on the RIGHT" in prompt
    assert "Một người đàn ông" in prompt

    other = build_image_prompt("Bí mật", "", "", "top_center")
    assert "main subject on the RIGHT" not in other
    assert "no text" in other


def test_translate_thumbnail_titles_uses_generator() -> None:
    def fake(prompt: str, key: str, model: str) -> str:
        return '{"en": "Scam Games at School", "ja": "学校前の詐欺ゲーム", "zh": "校门前骗局"}'

    res = translate_thumbnail_titles(
        "Trò chơi cổng trường",
        ["vi", "en", "ja", "zh"],
        "test-key",
        source_language="vi",
        generate_fn=fake,
    )
    assert res == {
        "en": "Scam Games at School",
        "ja": "学校前の詐欺ゲーム",
        "zh": "校门前骗局",
    }


def test_translate_thumbnail_titles_empty_or_same_language() -> None:
    assert translate_thumbnail_titles("", ["en"], "key") == {}
    assert translate_thumbnail_titles("Tiêu đề", ["vi"], "key", source_language="vi") == {}


def test_translate_thumbnail_titles_markdown_block() -> None:
    def fake(prompt: str, key: str, model: str) -> str:
        return '```json\n{"en": "Translated Title"}\n```'

    res = translate_thumbnail_titles(
        "Tiêu đề gốc",
        ["en"],
        "test-key",
        generate_fn=fake,
    )
    assert res == {"en": "Translated Title"}


def test_build_title_prompt_with_custom_template() -> None:
    from core.thumbnail import build_title_prompt, DEFAULT_TITLE_PROMPT_TEMPLATE
    tmpl = "Hãy viết {limit} tiêu đề bằng {language} cho nội dung:\n{transcript}"
    res = build_title_prompt("Đoạn văn mẫu", language="en", limit=3, template=tmpl)
    assert "Hãy viết 3 tiêu đề bằng English" in res
    assert "Đoạn văn mẫu" in res


def test_suggest_titles_uses_custom_prompt() -> None:
    captured: dict[str, str] = {}

    def fake(prompt: str, key: str, model: str) -> str:
        captured["prompt"] = prompt
        return '["CUSTOM TITLE 1", "CUSTOM TITLE 2"]'

    custom = "Tạo {limit} tiêu đề kịch tính cho: {transcript}"
    titles = suggest_titles(
        "Nội dung bí ẩn", "key-1",
        generate_fn=fake, custom_prompt=custom, limit=2)

    assert titles == ["CUSTOM TITLE 1", "CUSTOM TITLE 2"]
    assert "Tạo 2 tiêu đề kịch tính cho: Nội dung bí ẩn" in captured["prompt"]


