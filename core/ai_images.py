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
DEFAULT_MODEL = "imagen-3.0-generate-002"
FALLBACK_MODEL = "imagen-3.0-generate-002"
_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

IMAGE_MODEL_ALIASES = {
    "imagen-4.0-generate-001": "imagen-3.0-generate-002",
    "nano-banana": "imagen-3.0-generate-002",
    "nano-banana-2": "imagen-3.0-generate-002",
    "nano-banana-2-lite": "imagen-3.0-generate-002",
    "nano-banana-pro": "imagen-3.0-generate-002",
    "gemini-nano-banana-2.1": "imagen-3.0-generate-002",
}


def normalize_image_model(model: str) -> str:
    """Normalize any image model alias or retired model to a valid Google Imagen model."""
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


class ImagenProvider:
    """Minimal Google Imagen client over the Gemini API (no extra dependency)."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 timeout: float = 120.0) -> None:
        key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        if not key:
            raise AIImageError(
                "Chưa có API key Google Imagen / Gemini. Hãy cấu hình key trong tab Cài đặt "
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
            url = f"{_API_ROOT}/{m}:predict"
            payload = {
                "instances": [{"prompt": prompt}],
                "parameters": {
                    "sampleCount": 1,
                    "aspectRatio": _aspect_ratio(width, height),
                },
            }
            try:
                data = _post_json(url, payload, self._timeout, self._api_key)
                predictions = data.get("predictions") or []
                if not predictions:
                    reason = data.get("raiFilteredReason") or data.get("filteredReason") or ""
                    raise AIImageError(
                        "Imagen không trả về ảnh" + (f": {reason}" if reason else ".")
                    )
                first = predictions[0]
                encoded = first.get("bytesBase64Encoded")
                if not encoded:
                    reason = first.get("raiFilteredReason") or "phản hồi không có dữ liệu ảnh"
                    raise AIImageError(f"Imagen từ chối tạo ảnh: {reason}.")
                mime = str(first.get("mimeType") or "image/png").lower()
                suffix = ".jpg" if "jpeg" in mime else ".png"
                target = stem.with_suffix(suffix)
                try:
                    target.write_bytes(base64.b64decode(encoded))
                except (OSError, ValueError) as exc:
                    raise AIImageError(f"Không ghi được ảnh Imagen: {exc}") from exc
                if not target.is_file() or target.stat().st_size == 0:
                    raise AIImageError("File ảnh Imagen rỗng.")
                return target
            except AIImageError as exc:
                last_error = exc
                if ("404" in str(exc) or "not found" in str(exc).lower()) and m != FALLBACK_MODEL:
                    logger.warning("Model Imagen %s lỗi 404, thử lại với %s: %s", m, FALLBACK_MODEL, exc)
                    continue
                raise
        if last_error:
            raise last_error
        raise AIImageError("Không thể tạo ảnh từ Google Imagen.")


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
            f"Imagen trả lỗi {exc.code}: {message or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise AIImageError(f"Không kết nối được Google API: {exc.reason}") from exc
    except ValueError as exc:
        raise AIImageError(f"Phản hồi Imagen không hợp lệ: {exc}") from exc


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
) -> List[ChapterImages]:
    """Return the images for every chapter, generating them when needed.

    With ``variants > 1`` and mode ``imagen``, each chapter gets ``variants``
    distinct candidate images named ``<base>-1.png``, ``<base>-2.png``, … —
    existing files with those names are reused instead of regenerated.
    """
    mode = (mode or "manual").lower()
    count = max(1, int(count))
    variants = max(1, int(variants))
    per_chapter = variants if mode == "imagen" and variants > 1 else (
        count if mode == "imagen" else 1)
    provider: Optional[ImagenProvider] = None
    results: List[ChapterImages] = []
    total = max(1, len(segments))
    estimate = total * per_chapter
    done = 0

    def emit(message: str) -> None:
        if report is not None:
            report(min(100, int(100 * done / max(1, estimate))), message)

    for chapter_index, (item, start, duration) in enumerate(segments):
        _check(cancel_cb)
        paths = chapter_images(folder, item.base)
        if mode == "imagen":
            if variants > 1:
                for variant, stem in enumerate(variant_stems(folder, item.base, variants), start=1):
                    _check(cancel_cb)
                    cached = find_variant_file(folder, item.base, variant)
                    if cached is not None:
                        paths.append(cached)
                        continue
                    if provider is None:
                        provider = ImagenProvider(api_key, model)
                    prompt = build_prompt(item, variant, variants, style)
                    emit(f"Đang tạo ảnh AI {chapter_index + 1}/{total} "
                         f"(bản {variant}/{variants})…")
                    created = provider.generate(stem, width, height)
                    paths.append(created)
                    done += 1
                    logger.info("Đã tạo ảnh AI %s", created.name)
            else:
                missing = count - len(paths)
                if missing > 0 and provider is None:
                    provider = ImagenProvider(api_key, model)
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
            if mode == "imagen":
                raise AIImageError(
                    f"Imagen không tạo được ảnh cho '{item.base}'."
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
