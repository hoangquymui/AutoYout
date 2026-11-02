"""YouTube thumbnails: title hooks, AI backgrounds and Pillow text rendering.

The thumbnail flow is: transcript → suggested hooks → background image (Imagen,
Pollinations or a video frame) → bold template text → ``thumbs/<base>.jpg`` and
a ``thumbnail_path`` entry in ``input/<base>.json`` so the uploader picks it up.
Everything here is Qt-free and does no network work in :func:`render_thumbnail`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from core.ai_client import DEFAULT_MODEL, AIError, gemini_generate
from core.ai_images import AIImageError, DEFAULT_MODEL as IMAGEN_MODEL, ImagenProvider
from core.languages import language_name
from utils.files import load_json, write_json_atomic
from utils.logger import get_logger

logger = get_logger("thumbnail")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_PROMPT_TAIL = (
    "cinematic, dramatic lighting, high detail, 16:9, photo-realistic, "
    "no text, no watermark, no real person likeness"
)
_LEFT_SUBJECT = (
    "main subject on the RIGHT side of frame, negative space on the LEFT for text"
)
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_MIN_IMAGE_BYTES = 10_000

_DEFAULT_COLORS = {"text": "#FFFFFF", "green": "#3BFF35", "red": "#FF3B30"}


# ── titles ───────────────────────────────────────────────────────────────────
def parse_title_suggestions(raw: str, limit: int = 5) -> List[str]:
    """Accept a JSON array, a fenced JSON block or one title per line."""
    text = str(raw or "").strip()
    if not text:
        return []
    limit = max(1, int(limit))
    candidates: List[str] = []
    payload = _extract_json_array(text)
    if payload is not None:
        candidates = [str(entry) for entry in payload]
    else:
        for line in text.splitlines():
            line = line.strip().strip("-*•").strip()
            line = re.sub(r"^\d+[.)]\s*", "", line)
            line = line.strip().strip('"').strip("'").strip()
            if line:
                candidates.append(line)
    seen: set[str] = set()
    result: List[str] = []
    for entry in candidates:
        clean = " ".join(entry.replace("\n", " ").split())
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _extract_json_array(text: str) -> Optional[list]:
    blocks = [text]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence is not None:
        blocks.insert(0, fence.group(1).strip())
    for block in blocks:
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            return data
    return None


def build_title_prompt(transcript: str, language: str = "vi", limit: int = 5) -> str:
    excerpt = " ".join(str(transcript or "").split())[:6000]
    lines = [
        f"Dựa trên nội dung sau, viết {int(limit)} tiêu đề YouTube dạng hook tò mò "
        "cho kênh kể chuyện.",
        "Mỗi tiêu đề tối đa 60 ký tự, viết HOA các từ khoá mạnh, không bịa sự kiện "
        "không có trong nội dung, không emoji.",
        f"Viết bằng {language_name(language)}.",
        "Chỉ trả về JSON array chuỗi.",
        "",
        "---",
        excerpt,
    ]
    return "\n".join(lines)


def suggest_titles(
    transcript: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    language: str = "vi",
    generate_fn: Optional[Callable[[str, str, str], str]] = None,
    limit: int = 5,
) -> List[str]:
    """Ask Gemini for short hooks; ``generate_fn`` is injected in tests."""
    if not str(transcript or "").strip():
        raise AIError("Không có transcript để gợi ý tiêu đề.")
    generator = generate_fn or gemini_generate
    if generate_fn is None and not str(api_key or "").strip():
        raise AIError(
            "Thiếu API key Gemini — nhập key ở tab Video.")
    raw = generator(build_title_prompt(transcript, language, limit), api_key, model)
    titles = parse_title_suggestions(raw, limit=limit)
    if not titles:
        raise AIError("Gemini không trả về tiêu đề hợp lệ.")
    return titles


# ── image prompts ────────────────────────────────────────────────────────────
def build_image_prompt(title: str, transcript_excerpt: str,
                       style: str, template_id: str) -> str:
    """Describe the scene from the transcript and append the safe style tail."""
    clean_title = " ".join(str(title or "").split())
    excerpt = " ".join(str(transcript_excerpt or "").split())[:600]
    parts = [f"Background artwork for a story video titled \"{clean_title}\"."]
    if excerpt:
        parts.append(f"Scene: {excerpt}")
    if str(template_id or "") == "drama_left":
        parts.append(_LEFT_SUBJECT)
    if str(style or "").strip():
        parts.append(str(style).strip())
    parts.append(_PROMPT_TAIL)
    return " ".join(part for part in parts if part)


# ── image generation ─────────────────────────────────────────────────────────
def generate_image(
    provider: str,
    prompt: str,
    out_path: str | os.PathLike,
    *,
    api_key: str = "",
    model: str = "",
    width: int = 1280,
    height: int = 720,
    seed: Optional[int] = None,
    seconds: float = 5.0,
    ffmpeg: str = "",
) -> Path:
    """Create a background image with Imagen, Pollinations or a video frame."""
    name = str(provider or "").strip().lower()
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if name == "imagen":
        return _imagen_image(prompt, target, api_key, model, width, height)
    if name == "pollinations":
        return _pollinations_image(prompt, target, width, height, seed)
    if name == "frame":
        return _frame_image(prompt, target, seconds, ffmpeg)
    raise RuntimeError(f"Provider ảnh không hợp lệ: {provider!r}.")


def _imagen_image(prompt: str, target: Path, api_key: str, model: str,
                  width: int, height: int) -> Path:
    try:
        provider = ImagenProvider(api_key, model or IMAGEN_MODEL)
        created = provider.generate(prompt, target.with_suffix(""), width, height)
    except AIImageError as exc:
        raise RuntimeError(f"Imagen lỗi: {exc}") from exc
    if created != target:
        os.replace(created, target)
    return target


def _pollinations_image(prompt: str, target: Path, width: int, height: int,
                        seed: Optional[int]) -> Path:
    encoded = urllib.parse.quote(str(prompt or "").strip(), safe="")
    if not encoded:
        raise RuntimeError("Prompt ảnh rỗng, không thể gọi Pollinations.")
    url = f"https://image.pollinations.ai/prompt/{encoded}"
    params = {"width": int(width), "height": int(height), "nologo": "true"}
    if seed is not None:
        params["seed"] = int(seed)
    url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Pollinations trả lỗi {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Không kết nối được Pollinations: {exc}") from exc
    if not content_type.lower().startswith("image/"):
        raise RuntimeError(
            f"Pollinations trả về dữ liệu không phải ảnh ({content_type or '?'}).")
    if len(data) < _MIN_IMAGE_BYTES:
        raise RuntimeError("Ảnh Pollinations quá nhỏ hoặc bị lỗi.")
    target.write_bytes(data)
    return target


def _frame_image(video: str, target: Path, seconds: float, ffmpeg: str) -> Path:
    source = Path(str(video or ""))
    if not source.is_file():
        raise RuntimeError(f"Không tìm thấy video để lấy frame: {source}")
    exe = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    command = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, float(seconds or 0.0)):.3f}",
        "-i", str(source), "-frames:v", "1", "-q:v", "2", str(target),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Không tìm thấy ffmpeg để lấy frame.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg lấy frame quá lâu (timeout).") from exc
    if proc.returncode != 0 or not target.is_file():
        detail = (proc.stderr or "").strip()[-300:]
        raise RuntimeError(f"FFmpeg không lấy được frame: {detail or 'lỗi không rõ'}")
    return target


# ── templates ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ThumbTemplate:
    id: str
    name: str
    description: str
    text_box: Tuple[int, int, int, int]
    align: str
    gradient: str
    max_font: int
    min_font: int
    line_height: float
    stroke_ratio: float


TEMPLATES: Dict[str, ThumbTemplate] = {
    "drama_left": ThumbTemplate(
        "drama_left", "Drama trái",
        "Chữ khối bên trái, chủ thể bên phải.",
        (48, 48, 620, 624), "left", "left", 96, 64, 1.03, 0.10,
    ),
    "top_center": ThumbTemplate(
        "top_center", "Tiêu đề trên",
        "Chữ lớn trên đầu, ảnh lộ phần dưới.",
        (90, 36, 1100, 300), "center", "bottom", 96, 60, 1.05, 0.10,
    ),
    "split_50": ThumbTemplate(
        "split_50", "Chia đôi",
        "Nửa trái chữ, nửa phải ảnh.",
        (40, 96, 600, 528), "left", "left", 88, 56, 1.04, 0.10,
    ),
    "quote_card": ThumbTemplate(
        "quote_card", "Quote card",
        "Chữ giữa khung, nền tối.",
        (140, 300, 1000, 380), "center", "bottom", 84, 52, 1.06, 0.11,
    ),
}

_FONT_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "ui" / "assets" / "fonts" / "Anton.ttf",
    Path(__file__).resolve().parent.parent / "ui" / "assets" / "fonts" / "Montserrat-ExtraBold.ttf",
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/seguisb.ttf"),
)


@lru_cache(maxsize=1)
def default_font_path() -> Optional[str]:
    for candidate in _FONT_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


def _load_font(path: Optional[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, max(8, int(size)))
        except OSError:
            pass
    return ImageFont.load_default()


def wrap_title_lines(draw: ImageDraw.ImageDraw, text: str, font,
                     max_width: int) -> List[str]:
    """Greedy word wrap on the uppercased title, never splitting words."""
    words = str(text or "").upper().split()
    if not words:
        return []
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def autosize_font(text: str, box_w: int, box_h: int, max_font: int, min_font: int,
                  line_height: float, font_path: Optional[str] = None):
    """Largest font size (within bounds) whose wrapped title fits the box."""
    path = font_path or default_font_path()
    top = max(8, int(max_font))
    bottom = max(8, min(int(min_font), top))
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    chosen = _load_font(path, bottom)
    size = top
    while size >= bottom:
        font = _load_font(path, size)
        lines = wrap_title_lines(probe, text, font, max(1, int(box_w)))
        total_h = len(lines) * size * max(1.0, float(line_height))
        widest = max((probe.textlength(line, font=font) for line in lines), default=0.0)
        if lines and total_h <= box_h and widest <= box_w:
            chosen = font
            break
        size -= 2
    return chosen


# ── rendering ────────────────────────────────────────────────────────────────
def _palette_colors(palette: Any) -> Dict[str, str]:
    colors = dict(_DEFAULT_COLORS)
    if isinstance(palette, dict):
        text = palette.get("text") or palette.get("white")
        if text:
            colors["text"] = str(text)
        if palette.get("green"):
            colors["green"] = str(palette["green"])
        if palette.get("red"):
            colors["red"] = str(palette["red"])
    return colors


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGB", (width, height), (10, 12, 18))
    scale = max(width / src_w, height / src_h)
    new_w = max(width, int(round(src_w * scale)))
    new_h = max(height, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _gradient_mask(size: Tuple[int, int], mode: str, box: Sequence[int]) -> Optional[Image.Image]:
    width, height = size
    if mode == "left":
        strip = Image.new("L", (256, 1))
        strip.putdata([int(210 * (1.0 - i / 255.0) ** 1.3) for i in range(256)])
        fade = max(1, min(width, int(box[0] + box[2])))
        mask = strip.resize((fade, height), Image.Resampling.BILINEAR)
        full = Image.new("L", (width, height), 0)
        full.paste(mask, (0, 0))
        return full
    if mode == "bottom":
        strip = Image.new("L", (1, 256))
        strip.putdata([int(210 * (i / 255.0) ** 1.2) for i in range(256)])
        fade = max(1, int(height * 0.55))
        mask = strip.resize((width, fade), Image.Resampling.BILINEAR)
        full = Image.new("L", (width, height), 0)
        full.paste(mask, (0, height - fade))
        return full
    return None


def _draw_word_line(draw, x: float, y: float, line: str, font,
                    default_color: str, highlight: Dict[str, str],
                    stroke_width: int) -> None:
    space = draw.textlength(" ", font=font)
    cursor = x
    for token in line.split():
        core = re.sub(r"^[^\w]+|[^\w]+$", "", token, flags=re.UNICODE)
        color = highlight.get(core.casefold(), default_color)
        draw.text((cursor + 4, y + 4), token, font=font, fill=(0, 0, 0),
                  stroke_width=stroke_width, stroke_fill=(0, 0, 0))
        draw.text((cursor, y), token, font=font, fill=color,
                  stroke_width=stroke_width, stroke_fill=(0, 0, 0))
        cursor += draw.textlength(token, font=font) + space


def render_thumbnail(
    bg_path: str | os.PathLike,
    title: str,
    highlights: Iterable[Tuple[str, str]],
    template_id: str,
    palette: Any,
    out_path: str | os.PathLike,
    size: Tuple[int, int] = (1280, 720),
    font_size: int = 0,
) -> Path:
    """Composite the title over the background and save a JPEG/PNG."""
    background = Path(bg_path)
    if not background.is_file():
        raise RuntimeError(f"Không tìm thấy ảnh nền: {background}")
    width, height = max(64, int(size[0])), max(64, int(size[1]))
    template = TEMPLATES.get(str(template_id), TEMPLATES["drama_left"])
    colors = _palette_colors(palette)

    with Image.open(background) as source:
        canvas = _cover_crop(source.convert("RGB"), width, height)

    box = template.text_box
    sx = width / 1280.0
    sy = height / 720.0
    scaled_box = (
        int(box[0] * sx), int(box[1] * sy),
        max(1, int(box[2] * sx)), max(1, int(box[3] * sy)),
    )
    mask = _gradient_mask((width, height), template.gradient, scaled_box)
    if mask is not None:
        black = Image.new("RGB", (width, height), (0, 0, 0))
        canvas = Image.composite(black, canvas, mask)

    draw = ImageDraw.Draw(canvas)
    if int(font_size or 0) > 0:
        max_font = max(8, int(font_size * sy))
        min_font = max(8, int(font_size * 0.68 * sy))
    else:
        max_font = max(8, int(template.max_font * sy))
        min_font = max(8, int(template.min_font * sy))
    font = autosize_font(title, scaled_box[2], scaled_box[3], max_font, min_font,
                         template.line_height)
    lines = wrap_title_lines(draw, title, font, scaled_box[2])
    font_size = getattr(font, "size", min_font)
    stroke = max(2, int(font_size * template.stroke_ratio))
    line_h = font_size * template.line_height
    total_h = len(lines) * line_h
    y = scaled_box[1] + max(0.0, (scaled_box[3] - total_h) / 2.0)
    highlight_map = {
        str(word).strip().casefold(): str(color).lower()
        for word, color in highlights if str(word).strip()
    }
    for line in lines:
        line_w = draw.textlength(line, font=font)
        if template.align == "center":
            x = scaled_box[0] + max(0.0, (scaled_box[2] - line_w) / 2.0)
        else:
            x = float(scaled_box[0])
        _draw_word_line(draw, x, y, line, font, colors["text"], highlight_map, stroke)
        y += line_h

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() in (".jpg", ".jpeg"):
        canvas.save(target, "JPEG", quality=90)
    else:
        canvas.save(target, "PNG")
    return target


# ── attach to project ────────────────────────────────────────────────────────
def attach_thumbnail(manager, base: str, image_path: str | os.PathLike) -> Path:
    """Copy the image to ``thumbs/<base>.jpg`` and merge it into the item JSON."""
    source = Path(image_path)
    if not source.is_file():
        raise RuntimeError(f"Không tìm thấy ảnh thumbnail: {source}")
    target = Path(manager.thumbs_dir) / f"{base}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        with Image.open(source) as image:
            image.convert("RGB").save(target, "JPEG", quality=92)
    json_path = Path(manager.script_dir) / f"{base}.json"
    data = load_json(json_path, {}) or {}
    if not isinstance(data, dict):
        data = {}
    data["thumbnail_path"] = str(target.resolve())
    write_json_atomic(json_path, data)
    item = manager.get_item(base)
    if item is not None:
        item.metadata["thumbnail_path"] = str(target.resolve())
        manager.save_state()
    logger.info("Đã gắn thumbnail %s cho %s.", target.name, base)
    return target
