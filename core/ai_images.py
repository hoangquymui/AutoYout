"""Chapter images for the video renderer.

Two sources are supported:

* ``manual`` — images already placed in the AI folder (one set per chapter).
* ``imagen`` — Google Imagen (Gemini API) generates the missing images from the
  chapter title + excerpt and caches them next to the manual ones, so a second
  render never pays for the same picture twice.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from core.project_manager import ProjectItem, ProjectManager
from utils.files import ensure_dir, natural_sorted
from utils.logger import get_logger

logger = get_logger("ai")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_STYLE = "cinematic lighting, highly detailed, 16:9 composition, no text, no watermark"
DEFAULT_MODEL = "gemini-3.1-flash-image"
FALLBACK_MODEL = "gemini-2.5-flash-image"
_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

IMAGE_MODEL_ALIASES = {
    "nano-banana-2": "gemini-3.1-flash-image",
    "nano-banana-2-lite": "gemini-3.1-flash-lite-image",
    "nano-banana": "gemini-2.5-flash-image",
    "nano-banana-pro": "gemini-3-pro-image",
    "gemini-nano-banana-2.1": "gemini-nano-banana-2.1",
    "imagen-3.0-generate-002": "gemini-3.1-flash-image",
    "imagen-4.0-generate-001": "gemini-3.1-flash-image",
    "imagen": "gemini-3.1-flash-image",
    "gemini": "gemini-3.1-flash-image",
}


def normalize_image_model(model: str) -> str:
    """Normalize any image model alias or retired model to a valid Google Gemini image model."""
    name = str(model or "").strip()
    return IMAGE_MODEL_ALIASES.get(name, name or DEFAULT_MODEL)

Report = Optional[Callable[[int, str], None]]
CancelCb = Optional[Callable[[], bool]]


class AIImageError(RuntimeError):
    """Image lookup/generation failed; the message is shown to the user."""


class AIImageCancelled(AIImageError):
    """The user cancelled while images were being prepared."""


@dataclass
class ChapterImages:
    item: ProjectItem
    start: float
    duration: float
    paths: List[Path]


def resolve_ai_dir(manager: ProjectManager, configured: str = "") -> Path:
    value = str(configured or "").strip()
    if not value:
        return manager.assets_dir / "ai"
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        path = manager.project_dir / path
    return path


def chapter_images(folder: Path, base: str) -> List[Path]:
    """Images for one chapter: ``<folder>/<base>/`` first, then ``<folder>/<base>_*``."""
    if not folder.is_dir():
        return []
    sub = folder / base
    found: Iterable[Path]
    if sub.is_dir():
        found = (path for path in sub.iterdir() if path.is_file())
    else:
        found = (path for path in folder.iterdir() if path.is_file())
    images = [
        path for path in found
        if path.suffix.lower() in IMAGE_EXTS
        and (sub.is_dir() or re.fullmatch(re.escape(base) + r"(_\d+)?", path.stem))
    ]
    return natural_sorted(images)


def variant_stems(folder: Path, base: str, count: int) -> List[Path]:
    """Deterministic stems for N image variants: ``<folder>/<base>-1`` … ``<folder>/<base>-N``."""
    count = max(1, int(count))
    return [folder / f"{base}-{variant}" for variant in range(1, count + 1)]


def find_variant_file(folder: Path, base: str, variant: int) -> Optional[Path]:
    """Existing cached file for variant ``<base>-<variant>`` (any image extension)."""
    if not folder.is_dir():
        return None
    stem = f"{base}-{int(variant)}"
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS and path.stem == stem:
            return path
    return None


def build_prompt(item: ProjectItem, index: int, count: int, style: str) -> str:
    title = " ".join(str(item.metadata.get("title") or item.base).split())
    excerpt = " ".join(item.text.split())[:600]
    parts = [f"Illustration {index}/{count} for the chapter '{title}'."]
    if excerpt:
        parts.append(excerpt)
    if style.strip():
        parts.append(style.strip())
    return " ".join(parts)


def _pollinations_fallback(prompt: str, stem: Path, width: int, height: int) -> Path:
    import urllib.parse
    encoded = urllib.parse.quote(str(prompt or "").strip(), safe="")
    if not encoded:
        raise AIImageError("Prompt ảnh rỗng.")
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={int(width)}&height={int(height)}&nologo=true"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AutoYout/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            data = response.read()
    except Exception as exc:
        raise AIImageError(f"Pollinations dự phòng lỗi: {exc}") from exc
    if not content_type.lower().startswith("image/") or len(data) < 1024:
        raise AIImageError("Ảnh dự phòng từ Pollinations không hợp lệ.")
    target = stem.with_suffix(".jpg")
    target.write_bytes(data)
    return target


class GeminiImageProvider:
    """Google Gemini AI image client with automatic high-quality fallback."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 timeout: float = 120.0) -> None:
        key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        if not key:
            raise AIImageError(
                "Chưa có API key Gemini. Hãy cấu hình key trong tab Cài đặt "
                "(hoặc đặt biến môi trường GEMINI_API_KEY)."
            )
        self._api_key = key
        self._model = normalize_image_model(model or DEFAULT_MODEL)
        self._timeout = timeout

    def generate(self, prompt: str, stem: Path, width: int, height: int) -> Path:
        models_to_try = [self._model]
        if FALLBACK_MODEL not in models_to_try:
            models_to_try.append(FALLBACK_MODEL)

        last_error: Optional[Exception] = None
        for m in models_to_try:
            # 1. Thử gọi Gemini generateContent (chuẩn multimodal image generation)
            url_gc = f"{_API_ROOT}/{m}:generateContent"
            payload_gc = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                },
            }
            try:
                data = _post_json(url_gc, payload_gc, self._timeout, self._api_key)
                for cand in data.get("candidates") or []:
                    parts = (cand.get("content") or {}).get("parts") or []
                    for part in parts:
                        inline = part.get("inlineData")
                        if inline and inline.get("data"):
                            encoded = inline["data"]
                            mime = str(inline.get("mimeType") or "image/png").lower()
                            suffix = ".jpg" if "jpeg" in mime else ".png"
                            target = stem.with_suffix(suffix)
                            target.write_bytes(base64.b64decode(encoded))
                            if target.is_file() and target.stat().st_size > 0:
                                return target
            except Exception as exc:
                last_error = exc
                logger.warning("Gọi Gemini generateContent model %s lỗi: %s", m, exc)

            # 2. Thử gọi qua endpoint predict (tương thích Vertex AI / Imagen)
            url_pred = f"{_API_ROOT}/{m}:predict"
            payload_pred = {
                "instances": [{"prompt": prompt}],
                "parameters": {
                    "sampleCount": 1,
                    "aspectRatio": _aspect_ratio(width, height),
                },
            }
            try:
                data = _post_json(url_pred, payload_pred, self._timeout, self._api_key)
                predictions = data.get("predictions") or []
                if predictions:
                    first = predictions[0]
                    encoded = first.get("bytesBase64Encoded")
                    if encoded:
                        mime = str(first.get("mimeType") or "image/png").lower()
                        suffix = ".jpg" if "jpeg" in mime else ".png"
                        target = stem.with_suffix(suffix)
                        target.write_bytes(base64.b64decode(encoded))
                        if target.is_file() and target.stat().st_size > 0:
                            return target
            except Exception as exc:
                last_error = exc
                logger.warning("Gọi Gemini predict model %s lỗi: %s", m, exc)

        # 3. Nếu tài khoản Gemini chưa có quota ảnh trực tiếp (Free tier limit 0 hoặc model 404/429),
        # tự động chuyển sang Pollinations AI tạo ảnh chất lượng cao để người dùng không bị lỗi gián đoạn!
        logger.info(
            "Gemini không trả về ảnh (%s) → Tự động tạo ảnh qua Pollinations AI dự phòng.",
            last_error,
        )
        try:
            return _pollinations_fallback(prompt, stem, width, height)
        except Exception as poll_exc:
            raise AIImageError(
                f"Không thể tạo ảnh từ Gemini ({last_error}) và Pollinations ({poll_exc})."
            ) from poll_exc


# Alias tương thích ngược
ImagenProvider = GeminiImageProvider


class CloudflareImageProvider:
    """Cloudflare Workers AI image generator (Flux, Leonardo, SDXL, etc.)."""

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = "@cf/black-forest-labs/flux-1-schnell",
        steps: int = 4,
        timeout: float = 90.0,
    ) -> None:
        self.account_id = (account_id or "").strip()
        self.api_token = (api_token or "").strip()
        self.model = (model or "").strip() or "@cf/black-forest-labs/flux-1-schnell"
        self.steps = max(1, min(20, int(steps or 4)))
        self.timeout = timeout
        if not self.account_id or not self.api_token:
            raise AIImageError(
                "Chưa cấu hình Account ID hoặc API Token Cloudflare trong tab Cài đặt > API > Ảnh."
            )

    def generate(self, prompt: str, stem: Path, width: int = 1280, height: int = 720) -> Path:
        import requests

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{self.model}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "AutoYout/1.0",
        }
        payload: dict = {
            "prompt": prompt,
        }
        if "flux" in self.model.lower():
            payload["steps"] = self.steps
        if width and height:
            payload["width"] = width
            payload["height"] = height

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except Exception as exc:
            raise AIImageError(f"Lỗi kết nối tới Cloudflare Workers AI: {exc}") from exc

        if resp.status_code != 200:
            raise AIImageError(
                f"Cloudflare Workers AI trả về lỗi HTTP {resp.status_code}: {resp.text[:300]}"
            )

        content_type = resp.headers.get("Content-Type", "")
        target = stem.with_suffix(".png")

        if "application/json" in content_type:
            try:
                data = resp.json()
            except Exception as exc:
                raise AIImageError(f"Không thể đọc JSON từ Cloudflare: {exc}") from exc
            image_b64 = (data.get("result") or {}).get("image")
            if image_b64:
                target.write_bytes(base64.b64decode(image_b64))
            else:
                raise AIImageError(f"Cloudflare không trả về ảnh trong JSON: {data}")
        else:
            target.write_bytes(resp.content)

        if not target.is_file() or target.stat().st_size == 0:
            raise AIImageError("Không lưu được ảnh từ Cloudflare Workers AI.")
        return target


def fetch_cloudflare_models(
    account_id: str, api_token: str, task: str = "Text-to-Image"
) -> List[Tuple[str, str]]:
    """Fetch available models from Cloudflare Workers AI for a given task filter."""
    import requests

    acc = (account_id or "").strip()
    token = (api_token or "").strip()
    if not acc or not token:
        raise AIImageError("Thiếu Account ID hoặc API Token Cloudflare.")

    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/models/search"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "AutoYout/1.0"}
    try:
        resp = requests.get(url, headers=headers, params={"task": task, "source": "1"}, timeout=15)
    except Exception as exc:
        raise AIImageError(f"Lỗi kết nối tới Cloudflare: {exc}") from exc

    if resp.status_code != 200:
        raise AIImageError(f"Cloudflare lỗi HTTP {resp.status_code}: {resp.text[:200]}")

    models = resp.json().get("result", [])
    results: List[Tuple[str, str]] = []
    for m in models:
        name = m.get("name", "")
        if not name or not name.startswith("@cf/"):
            continue
        if m.get("source") is not None and m.get("source") != 1:
            continue
        clean_name = name.split("/")[-1].replace("-", " ").title()
        results.append((f"{clean_name} ({name})", name))
    return results


def _post_json(url: str, payload: dict, timeout: float, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            message = str((body.get("error") or {}).get("message") or "")
        except (ValueError, AttributeError):
            message = ""
        raise AIImageError(
            f"Gemini trả lỗi {exc.code}: {message or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AIImageError(f"Không kết nối được Google API: {exc.reason}") from exc
    except ValueError as exc:
        raise AIImageError(f"Phản hồi Gemini không hợp lệ: {exc}") from exc


def _aspect_ratio(width: int, height: int) -> str:
    ratio = width / max(1, height)
    options = {"16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "1:1": 1.0}
    return min(options, key=lambda key: abs(options[key] - ratio))


def ensure_images(
    manager: ProjectManager,
    segments: Sequence[Tuple[ProjectItem, float, float]],
    *,
    folder: Path,
    mode: str,
    count: int,
    api_key: str,
    model: str,
    style: str,
    width: int,
    height: int,
    report: Report = None,
    cancel_cb: CancelCb = None,
    variants: int = 1,
    account_id: str = "",
    api_token: str = "",
    steps: int = 4,
) -> List[ChapterImages]:
    """Return the images for every chapter, generating them when needed.

    Supports Cloudflare Workers AI and Google Gemini image providers.
    """
    mode = (mode or "manual").lower()
    count = max(1, int(count))
    variants = max(1, int(variants))
    is_ai = mode in ("imagen", "gemini", "cloudflare", "cf", "ai")
    per_chapter = variants if is_ai and variants > 1 else (count if is_ai else 1)
    provider: Any = None
    results: List[ChapterImages] = []
    total = max(1, len(segments))
    estimate = total * per_chapter
    done = 0

    # Auto-load Cloudflare credentials if not passed
    cf_acc = (account_id or os.environ.get("CF_ACCOUNT_ID", "")).strip()
    cf_tok = (api_token or os.environ.get("CF_API_TOKEN", "")).strip()
    if is_ai and (not cf_acc or not cf_tok):
        try:
            from core.settings import load_settings

            _s = load_settings()
            _api = _s.get("api") or {}
            if not cf_acc:
                cf_acc = str(_api.get("cloudflare_account_id") or "").strip()
            if not cf_tok:
                cf_tok = str(_api.get("cloudflare_token") or "").strip()
            if not model or model.startswith("gemini-") or model.startswith("imagen"):
                model = str(_api.get("cloudflare_image_model") or "@cf/black-forest-labs/flux-1-schnell").strip()
        except Exception:
            pass

    def emit(message: str) -> None:
        if report is not None:
            report(min(100, int(100 * done / max(1, estimate))), message)

    for chapter_index, (item, start, duration) in enumerate(segments):
        _check(cancel_cb)
        paths = chapter_images(folder, item.base)
        if is_ai:
            if variants > 1:
                for variant, stem in enumerate(variant_stems(folder, item.base, variants), start=1):
                    _check(cancel_cb)
                    cached = find_variant_file(folder, item.base, variant)
                    if cached is not None:
                        paths.append(cached)
                        continue
                    if provider is None:
                        if cf_acc and cf_tok:
                            provider = CloudflareImageProvider(
                                cf_acc, cf_tok, model or "@cf/black-forest-labs/flux-1-schnell", steps=steps
                            )
                        else:
                            provider = GeminiImageProvider(api_key, model)
                    prompt = build_prompt(item, variant, variants, style)
                    emit(f"Đang tạo ảnh AI {chapter_index + 1}/{total} "
                         f"(bản {variant}/{variants})…")
                    created = provider.generate(prompt, stem, width, height)
                    paths.append(created)
                    done += 1
                    logger.info("Đã tạo ảnh AI %s", created.name)
            else:
                missing = count - len(paths)
                if missing > 0 and provider is None:
                    if cf_acc and cf_tok:
                        provider = CloudflareImageProvider(
                            cf_acc, cf_tok, model or "@cf/black-forest-labs/flux-1-schnell", steps=steps
                        )
                    else:
                        provider = GeminiImageProvider(api_key, model)
                next_index = _next_ai_index(paths, item.base)
                for offset in range(missing):
                    _check(cancel_cb)
                    stem = ensure_dir(folder) / f"{item.base}_ai_{next_index:02d}"
                    next_index += 1
                    prompt = build_prompt(item, offset + 1, count, style)
                    emit(f"Đang tạo ảnh AI {chapter_index + 1}/{total} "
                         f"({offset + 1}/{missing})…")
                    created = provider.generate(prompt, stem, width, height)
                    paths.append(created)
                    done += 1
                    logger.info("Đã tạo ảnh AI %s", created.name)
        if not paths:
            expected = folder / f"{item.base}_01.png"
            if is_ai:
                raise AIImageError(
                    f"Không tạo được ảnh cho '{item.base}'."
                )
            raise AIImageError(
                "Thiếu ảnh cho chương '{}'. Đặt ảnh vào {} "
                "(tên {}_01.png…) hoặc chọn chế độ AI.".format(
                    item.base, folder, item.base)
            )
        done += 1 if mode == "manual" else 0
        emit(f"Đã có {len(paths)} ảnh cho chương {chapter_index + 1}/{total}.")
        results.append(ChapterImages(item, start, duration, paths))
    return results


def _next_ai_index(paths: Sequence[Path], base: str) -> int:
    pattern = re.compile(re.escape(base) + r"_ai_(\d+)$")
    used = [
        int(match.group(1))
        for match in (pattern.fullmatch(path.stem) for path in paths)
        if match is not None
    ]
    return max(used, default=0) + 1


def _check(cancel_cb: CancelCb) -> None:
    if cancel_cb is not None and cancel_cb():
        raise AIImageCancelled("Đã huỷ tạo ảnh AI.")
