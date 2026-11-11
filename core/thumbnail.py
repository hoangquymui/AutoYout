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
from core.ai_images import (
    AIImageError,
    DEFAULT_MODEL as GEMINI_IMAGE_MODEL,
    GeminiImageProvider,
    ImagenProvider,
)
from core.languages import language_name, normalize_language_code

IMAGEN_MODEL = GEMINI_IMAGE_MODEL
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


DEFAULT_TITLE_PROMPT_TEMPLATE = (
    "Dựa trên nội dung sau, viết {limit} tiêu đề YouTube dạng hook tò mò cho kênh kể chuyện.\n"
    "Mỗi tiêu đề tối đa 60 ký tự, viết HOA các từ khoá mạnh, không bịa sự kiện không có trong nội dung, không emoji.\n"
    "Viết bằng {language}.\n"
    "Chỉ trả về JSON array chuỗi (ví dụ: [\"TIÊU ĐỀ 1\", \"TIÊU ĐỀ 2\"]).\n\n"
    "---\n"
    "{transcript}"
)


def build_title_prompt(
    transcript: str,
    language: str = "vi",
    limit: int = 5,
    template: Optional[str] = None,
) -> str:
    excerpt = " ".join(str(transcript or "").split())[:6000]
    lang_name = language_name(language)
    tmpl = str(template or "").strip()
    if tmpl:
        if "{transcript}" in tmpl:
            res = tmpl.replace("{transcript}", excerpt)
            res = res.replace("{language}", lang_name)
            res = res.replace("{limit}", str(int(limit)))
            return res
        return f"{tmpl}\n\nViết bằng {lang_name}.\nChỉ trả về JSON array chuỗi.\n---\n{excerpt}"
    lines = [
        f"Dựa trên nội dung sau, viết {int(limit)} tiêu đề YouTube dạng hook tò mò "
        "cho kênh kể chuyện.",
        "Mỗi tiêu đề tối đa 60 ký tự, viết HOA các từ khoá mạnh, không bịa sự kiện "
        "không có trong nội dung, không emoji.",
        f"Viết bằng {lang_name}.",
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
    custom_prompt: Optional[str] = None,
) -> List[str]:
    """Ask Gemini for short hooks; ``generate_fn`` is injected in tests."""
    if not str(transcript or "").strip():
        raise AIError("Không có transcript để gợi ý tiêu đề.")
    generator = generate_fn or gemini_generate
    if generate_fn is None and not str(api_key or "").strip():
        from core.ai_client import get_cloudflare_text_config

        cf_acc, cf_tok, _ = get_cloudflare_text_config()
        if not (cf_acc and cf_tok):
            raise AIError("Thiếu API key Gemini hoặc Cloudflare Worker AI — vui lòng cấu hình trong Cài đặt.")
    raw = generator(build_title_prompt(transcript, language, limit, template=custom_prompt), api_key, model)
    titles = parse_title_suggestions(raw, limit=limit)
    if not titles:
        raise AIError("AI không trả về tiêu đề hợp lệ.")
    return titles


def translate_thumbnail_titles(
    title: str,
    target_languages: Sequence[str],
    api_key: str,
    source_language: str = "vi",
    model: str = DEFAULT_MODEL,
    generate_fn: Optional[Callable[[str, str, str], str]] = None,
) -> Dict[str, str]:
    """Translate a thumbnail hook title into multiple languages via Gemini JSON output."""
    clean_title = str(title or "").strip()
    if not clean_title:
        return {}
    targets = [
        normalize_language_code(l)
        for l in target_languages
        if normalize_language_code(l) != normalize_language_code(source_language)
    ]
    if not targets:
        return {}
    generator = generate_fn or gemini_generate
    if generate_fn is None and not str(api_key or "").strip():
        from core.ai_client import get_cloudflare_text_config

        cf_acc, cf_tok, _ = get_cloudflare_text_config()
        if not (cf_acc and cf_tok):
            raise AIError("Thiếu API key Gemini hoặc Cloudflare Worker AI để dịch tiêu đề.")

    prompt = (
        f"Bạn là chuyên gia tối ưu tiêu đề video YouTube giật gân, cuốn hút trên Thumbnail.\n"
        f"Hãy dịch tiêu đề sau sang các ngôn ngữ: {', '.join(targets)}.\n"
        f"Tiêu đề gốc ({source_language}): \"{clean_title}\"\n\n"
        f"Yêu cầu:\n"
        f"- Ngắn gọn, súc tích, giật tít thu hút người xem trên Thumbnail\n"
        f"- Giữ đúng ý nghĩa nhưng tự nhiên theo văn hóa từng ngôn ngữ\n"
        f"- Trả về DUY NHẤT một JSON map dạng: "
        + json.dumps({l: f"Tiêu đề {l}" for l in targets}, ensure_ascii=False)
        + "\nKhông thêm bất kỳ giải thích, markdown hay ký tự nào ngoài JSON."
    )
    raw = generator(prompt, api_key, model)
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return {normalize_language_code(k): str(v).strip() for k, v in parsed.items()}
    except Exception as exc:
        logger.warning("Không phân tích được JSON dịch tiêu đề: %s", exc)
    return {}


def translate_thumbnail_keywords(
    keywords: Sequence[Tuple[str, str]],
    target_languages: Sequence[str],
    api_key: str,
    source_language: str = "vi",
    model: str = DEFAULT_MODEL,
    generate_fn: Optional[Callable[[str, str, str], str]] = None,
) -> Dict[str, List[Tuple[str, str]]]:
    """Translate highlight keywords into target languages, preserving their colors."""
    if not keywords:
        return {}
    targets = [
        normalize_language_code(l)
        for l in target_languages
        if normalize_language_code(l) != normalize_language_code(source_language)
    ]
    if not targets:
        return {}
    generator = generate_fn or gemini_generate
    if generate_fn is None and not str(api_key or "").strip():
        from core.ai_client import get_cloudflare_text_config

        cf_acc, cf_tok, _ = get_cloudflare_text_config()
        if not (cf_acc and cf_tok):
            return {}

    word_list = [str(w).strip() for w, _ in keywords if str(w).strip()]
    if not word_list:
        return {}

    color_by_word = {str(w).strip().casefold(): str(c) for w, c in keywords}
    prompt = (
        f"Dịch danh sách các từ/cụm từ nổi bật sau sang các ngôn ngữ: {', '.join(targets)}.\n"
        f"Ngôn ngữ gốc ({source_language}): {json.dumps(word_list, ensure_ascii=False)}\n\n"
        f"Yêu cầu:\n"
        f"- Dịch ngắn gọn, tự nhiên, tương ứng với từ/cụm từ gốc khi dùng trên tiêu đề thumbnail YouTube.\n"
        f"- Trả về DUY NHẤT một JSON map dạng:\n"
        f"{{\n"
        f'  "{targets[0]}": {{"từ gốc": "từ đã dịch"}}\n'
        f"}}\n"
        f"Không giải thích, không thêm bất kỳ ký tự nào ngoài JSON."
    )
    try:
        raw = generator(prompt, api_key, model)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                results: Dict[str, List[Tuple[str, str]]] = {}
                for lang_code, trans_map in parsed.items():
                    norm_code = normalize_language_code(lang_code)
                    if isinstance(trans_map, dict):
                        kw_list: List[Tuple[str, str]] = []
                        for orig, trans in trans_map.items():
                            color = color_by_word.get(str(orig).strip().casefold(), "green")
                            if str(trans).strip():
                                kw_list.append((str(trans).strip(), color))
                        if kw_list:
                            results[norm_code] = kw_list
                return results
    except Exception as exc:
        logger.warning("Không phân tích được JSON dịch từ khoá: %s", exc)
    return {}


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
    account_id: str = "",
    api_token: str = "",
    steps: int = 4,
) -> Path:
    """Create a background image with Cloudflare, Pollinations or a video frame."""
    name = str(provider or "").strip().lower()
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Đảm bảo lấy thông tin Cloudflare từ settings hoặc biến môi trường nếu chưa có
    cf_acc = (account_id or os.environ.get("CF_ACCOUNT_ID", "")).strip()
    cf_tok = (api_token or os.environ.get("CF_API_TOKEN", "")).strip()
    cf_model = str(model or "").strip()
    try:
        from core.settings import load_settings

        _s = load_settings()
        _api = _s.get("api") or {}
        if not cf_acc:
            cf_acc = str(_api.get("cloudflare_account_id") or "").strip()
        if not cf_tok:
            cf_tok = str(_api.get("cloudflare_token") or "").strip()
        if not cf_model or cf_model.startswith("gemini-") or cf_model.startswith("imagen"):
            cf_model = str(_api.get("cloudflare_image_model") or "@cf/black-forest-labs/flux-1-schnell").strip()
    except Exception:
        pass

    # Yêu cầu: Đối với Hình ảnh dùng Cloudflare thôi
    if name in ("cloudflare", "cf", "worker_ai", "workers_ai", "gemini", "imagen", "ai"):
        if cf_acc and cf_tok:
            return _cloudflare_image(prompt, target, cf_acc, cf_tok, cf_model, width, height, steps)
        if name in ("gemini", "imagen") and str(api_key).strip():
            return _gemini_image(prompt, target, api_key, model, width, height)
        return _cloudflare_image(prompt, target, cf_acc, cf_tok, cf_model, width, height, steps)
    if name == "pollinations":
        return _pollinations_image(prompt, target, width, height, seed)
    if name == "frame":
        return _frame_image(prompt, target, seconds, ffmpeg)
    raise RuntimeError(f"Provider ảnh không hợp lệ: {provider!r}.")


def _cloudflare_image(
    prompt: str,
    target: Path,
    account_id: str,
    api_token: str,
    model: str,
    width: int,
    height: int,
    steps: int = 4,
) -> Path:
    from core.ai_images import CloudflareImageProvider, AIImageError

    try:
        provider = CloudflareImageProvider(
            account_id, api_token, model or "@cf/black-forest-labs/flux-1-schnell", steps=steps
        )
        created = provider.generate(prompt, target.with_suffix(""), width, height)
    except AIImageError as exc:
        raise RuntimeError(f"Cloudflare Workers AI lỗi: {exc}") from exc
    if created != target:
        if created.suffix.lower() == target.suffix.lower():
            os.replace(created, target)
        else:
            with Image.open(created) as im:
                im.convert("RGB").save(target)
            created.unlink(missing_ok=True)
    return target


def _gemini_image(prompt: str, target: Path, api_key: str, model: str,
                  width: int, height: int) -> Path:
    try:
        provider = GeminiImageProvider(api_key, model or GEMINI_IMAGE_MODEL)
        created = provider.generate(prompt, target.with_suffix(""), width, height)
    except AIImageError as exc:
        raise RuntimeError(f"Gemini lỗi: {exc}") from exc
    if created != target:
        os.replace(created, target)
    return target


_imagen_image = _gemini_image


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

_CJK_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/YuGothB.ttc"),
    Path("C:/Windows/Fonts/meiryob.ttc"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
)

_JA_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/YuGothB.ttc"),
    Path("C:/Windows/Fonts/meiryob.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
)

_KO_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
)

_LATIN_FONT_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "ui" / "assets" / "fonts" / "Anton.ttf",
    Path(__file__).resolve().parent.parent / "ui" / "assets" / "fonts" / "Montserrat-ExtraBold.ttf",
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/seguisb.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
)

_FONT_CANDIDATES = _LATIN_FONT_CANDIDATES


def is_cjk(text: str) -> bool:
    """Check if text contains any Chinese, Japanese, or Korean characters."""
    for ch in str(text or ""):
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF
            or 0x3040 <= code <= 0x309F
            or 0x30A0 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF
            or 0x1100 <= code <= 0x11FF
            or 0xFF00 <= code <= 0xFFEF
        ):
            return True
    return False


@lru_cache(maxsize=64)
def _find_font_cached(is_cjk_flag: bool, is_ja_flag: bool, is_ko_flag: bool, lang_prefix: str) -> Optional[str]:
    candidates = []
    if lang_prefix.startswith("ja") or is_ja_flag:
        candidates.extend(_JA_FONT_CANDIDATES)
    elif lang_prefix.startswith("ko") or is_ko_flag:
        candidates.extend(_KO_FONT_CANDIDATES)
    elif lang_prefix.startswith("zh") or is_cjk_flag:
        candidates.extend(_CJK_FONT_CANDIDATES)
    candidates.extend(_LATIN_FONT_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def default_font_path(text: str = "", language: str = "") -> Optional[str]:
    raw = str(text or "")
    lang = (language or "").strip().lower()
    cjk = is_cjk(raw)
    ja = any(0x3040 <= ord(c) <= 0x30FF for c in raw)
    ko = any(0xAC00 <= ord(c) <= 0xD7AF for c in raw)
    return _find_font_cached(cjk, ja, ko, lang[:2])


def _load_font(path: Optional[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, max(8, int(size)))
        except OSError:
            pass
    return ImageFont.load_default()


def wrap_title_lines(draw: ImageDraw.ImageDraw, text: str, font,
                     max_width: int) -> List[str]:
    """Wrap title lines cleanly. Uses word wrap for Western/Vietnamese, character-level for CJK."""
    raw = str(text or "").strip()
    if not raw:
        return []
    if not is_cjk(raw):
        words = raw.upper().split()
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

    # CJK character-level wrapping
    lines: List[str] = []
    current = ""
    for ch in raw:
        candidate = current + ch
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def autosize_font(text: str, box_w: int, box_h: int, max_font: int, min_font: int,
                  line_height: float, font_path: Optional[str] = None, language: str = ""):
    """Largest font size (within bounds) whose wrapped title fits the box."""
    path = font_path or default_font_path(text, language)
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


def _segment_line_highlights(
    line: str,
    highlights: Iterable[Tuple[str, str]],
    default_color: str,
    palette_colors: Dict[str, str],
) -> List[Tuple[str, str]]:
    """Segment a line into text runs with resolved colors for matching keywords/phrases."""
    if not line:
        return []
    char_colors = [default_color] * len(line)
    line_lower = line.casefold()

    # Sort keywords by length descending so longer phrases match first
    sorted_kw = sorted(highlights, key=lambda item: len(str(item[0])), reverse=True)

    for kw, col in sorted_kw:
        kw_str = str(kw).strip()
        if not kw_str:
            continue
        kw_lower = kw_str.casefold()
        col_lower = str(col).strip().lower()
        resolved_col = palette_colors.get(col_lower, col_lower)

        start_idx = 0
        while True:
            idx = line_lower.find(kw_lower, start_idx)
            if idx == -1:
                break
            end_idx = idx + len(kw_lower)
            if all(char_colors[i] == default_color for i in range(idx, end_idx)):
                for i in range(idx, end_idx):
                    char_colors[i] = resolved_col
            start_idx = idx + 1

    segments: List[Tuple[str, str]] = []
    curr_text = [line[0]]
    curr_color = char_colors[0]
    for ch, col in zip(line[1:], char_colors[1:]):
        if col == curr_color:
            curr_text.append(ch)
        else:
            segments.append(("".join(curr_text), curr_color))
            curr_text = [ch]
            curr_color = col
    if curr_text:
        segments.append(("".join(curr_text), curr_color))
    return segments


def _draw_word_line(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    line: str,
    font,
    default_color: str,
    highlights: Iterable[Tuple[str, str]],
    palette_colors: Dict[str, str],
    stroke_width: int,
) -> None:
    # 1. Drop shadow for the full line
    draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0),
              stroke_width=stroke_width, stroke_fill=(0, 0, 0))
    # 2. Black outline underlay for the full line
    draw.text((x, y), line, font=font, fill=(0, 0, 0),
              stroke_width=stroke_width, stroke_fill=(0, 0, 0))
    # 3. Colored runs over the stroke
    segments = _segment_line_highlights(line, highlights, default_color, palette_colors)
    prefix = ""
    for seg_text, color in segments:
        seg_x = x + draw.textlength(prefix, font=font)
        draw.text((seg_x, y), seg_text, font=font, fill=color,
                  stroke_width=stroke_width, stroke_fill=(0, 0, 0))
        prefix += seg_text


def render_thumbnail(
    bg_path: str | os.PathLike,
    title: str,
    highlights: Iterable[Tuple[str, str]],
    template_id: str,
    palette: Any,
    out_path: str | os.PathLike,
    size: Tuple[int, int] = (1280, 720),
    font_size: int = 0,
    language: str = "",
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
                         template.line_height, language=language)
    lines = wrap_title_lines(draw, title, font, scaled_box[2])
    font_size = getattr(font, "size", min_font)
    stroke = max(2, int(font_size * template.stroke_ratio))
    line_h = font_size * template.line_height
    total_h = len(lines) * line_h
    y = scaled_box[1] + max(0.0, (scaled_box[3] - total_h) / 2.0)
    for line in lines:
        line_w = draw.textlength(line, font=font)
        if template.align == "center":
            x = scaled_box[0] + max(0.0, (scaled_box[2] - line_w) / 2.0)
        else:
            x = float(scaled_box[0])
        _draw_word_line(draw, x, y, line, font, colors["text"], highlights, colors, stroke)
        y += line_h

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() in (".jpg", ".jpeg"):
        canvas.save(target, "JPEG", quality=90)
    else:
        canvas.save(target, "PNG")
    return target


# ── attach to project ────────────────────────────────────────────────────────
def attach_thumbnail(
    manager,
    base: str,
    image_path: str | os.PathLike,
    language: str = "",
) -> Path:
    """Copy the image to ``thumbs/<lang>/<base>.jpg`` (or ``thumbs/<base>.jpg`` for flat legacy items) and merge it into the item JSON."""
    source = Path(image_path)
    if not source.is_file():
        raise RuntimeError(f"Không tìm thấy ảnh thumbnail: {source}")
    item = manager.get_item(base) if hasattr(manager, "get_item") else None
    in_lang_dir = bool(
        language
        or (
            item is not None
            and (
                (item.text_file is not None and item.text_file.parent != manager.script_dir)
                or (item.json_file is not None and item.json_file.parent != manager.script_dir)
                or any(item.base.lower().endswith(f"_{lc}") for lc in ("vi", "en", "ja", "zh", "ko", "fr", "de", "es", "ru", "th", "id", "pt"))
            )
        )
    )
    lang = str(language or getattr(item, "language", "") or getattr(manager, "default_language", "vi")).strip().lower() or "vi"
    if in_lang_dir:
        if hasattr(manager, "lang_dir"):
            thumbs_dir = manager.lang_dir("thumbs", lang, create=True)
        else:
            thumbs_dir = Path(manager.thumbs_dir) / lang
            thumbs_dir.mkdir(parents=True, exist_ok=True)
    else:
        thumbs_dir = Path(manager.thumbs_dir)
        thumbs_dir.mkdir(parents=True, exist_ok=True)
    target = thumbs_dir / f"{base}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        with Image.open(source) as image:
            image.convert("RGB").save(target, "JPEG", quality=92)
    if item is not None and getattr(item, "json_file", None):
        json_path = Path(item.json_file)
    elif item is not None and getattr(item, "text_file", None):
        json_path = Path(item.text_file).with_suffix(".json")
    elif in_lang_dir and hasattr(manager, "lang_dir"):
        json_path = manager.lang_dir("script", lang, create=True) / f"{base}.json"
    else:
        json_path = Path(manager.script_dir) / f"{base}.json"
    data = load_json(json_path, {}) or {}
    if not isinstance(data, dict):
        data = {}
    data["thumbnail_path"] = str(target.resolve())
    write_json_atomic(json_path, data)
    if item is not None:
        item.metadata["thumbnail_path"] = str(target.resolve())
        manager.save_state()
    logger.info("Đã gắn thumbnail %s cho %s (%s).", target.name, base, lang)
    return target
