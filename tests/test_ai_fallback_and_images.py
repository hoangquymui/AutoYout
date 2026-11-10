from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from PySide6.QtWidgets import QApplication

from core.ai_client import AIError, gemini_generate
from core.thumbnail import generate_image
from ui.tab_thumbnail import _PROVIDERS, ThumbnailPanel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_gemini_fallback_to_cloudflare_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Gemini returns an HTTP error, it should automatically fallback to Cloudflare Workers AI."""
    monkeypatch.setattr(
        "core.ai_client.get_cloudflare_text_config",
        lambda: ("cf-acc-123", "cf-tok-abc", "@cf/meta/llama-3.1-8b-instruct"),
    )

    mock_cf = MagicMock(return_value="Nội dung tạo từ Cloudflare fallback")
    monkeypatch.setattr("core.ai_client.cloudflare_generate", mock_cf)

    # Mock Gemini urlopen to raise HTTPError 500
    import urllib.error

    def mock_urlopen(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com", 500, "Internal Server Error", {}, None
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    res = gemini_generate("Hãy viết tiêu đề", api_key="fake-gemini-key", retries=1)
    assert res == "Nội dung tạo từ Cloudflare fallback"
    assert mock_cf.called
    assert mock_cf.call_args[0][0] == "Hãy viết tiêu đề"
    assert mock_cf.call_args[0][1] == "cf-acc-123"
    assert mock_cf.call_args[0][2] == "cf-tok-abc"


def test_gemini_direct_fallback_when_key_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Gemini API key is empty, it directly uses Cloudflare Workers AI."""
    monkeypatch.setattr(
        "core.ai_client.get_cloudflare_text_config",
        lambda: ("cf-acc-123", "cf-tok-abc", "@cf/meta/llama-3.1-8b-instruct"),
    )
    mock_cf = MagicMock(return_value="Nội dung Cloudflare không cần Gemini key")
    monkeypatch.setattr("core.ai_client.cloudflare_generate", mock_cf)

    res = gemini_generate("Prompt test", api_key="")
    assert res == "Nội dung Cloudflare không cần Gemini key"
    assert mock_cf.called


def test_gemini_raises_when_both_keys_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When neither Gemini nor Cloudflare credentials exist, raises AIError."""
    monkeypatch.setattr("core.ai_client.get_cloudflare_text_config", lambda: ("", "", ""))
    with pytest.raises(AIError, match="Thiếu API key Gemini"):
        gemini_generate("Prompt test", api_key="")


def test_image_provider_list_excludes_gemini() -> None:
    """Thumbnail provider dropdown must only contain Cloudflare, Pollinations, Frame (no Gemini/Imagen)."""
    provider_ids = [val for _, val in _PROVIDERS]
    assert "gemini" not in provider_ids
    assert "imagen" not in provider_ids
    assert "cloudflare" in provider_ids
    assert provider_ids[0] == "cloudflare"


def test_generate_image_routes_to_cloudflare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """generate_image should route gemini/imagen/cloudflare requests to Cloudflare."""
    mock_cf_img = MagicMock(return_value=tmp_path / "out.jpg")
    monkeypatch.setattr("core.thumbnail._cloudflare_image", mock_cf_img)

    out = tmp_path / "test.jpg"
    # Even if provider is called 'gemini', with CF credentials available, it routes to Cloudflare
    generate_image(
        "gemini",
        "A scenic view",
        out,
        account_id="acc1",
        api_token="tok1",
        model="@cf/black-forest-labs/flux-1-schnell",
    )
    assert mock_cf_img.called
    assert mock_cf_img.call_args[0][0] == "A scenic view"
    assert mock_cf_img.call_args[0][2] == "acc1"
    assert mock_cf_img.call_args[0][3] == "tok1"


def test_thumbnail_panel_defaults_to_cloudflare(app) -> None:
    """ThumbnailPanel should default provider to cloudflare."""
    panel = ThumbnailPanel()
    panel.load_from({})
    assert panel.provider_combo.currentData() == "cloudflare"
