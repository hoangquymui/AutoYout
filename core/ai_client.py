"""Gemini text generation shared by the input, script and dubbing tabs.

Only the plain ``generateContent`` endpoint lives here; image generation keeps
using its own ``:predict`` call in :mod:`core.ai_images`. ``urllib`` is used on
purpose so the app does not need an extra SDK.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional

from core.languages import language_name, normalize_language

DEFAULT_MODEL = "gemini-3.1-flash-lite"
GEMINI_TEXT_MODELS: List[tuple[str, str]] = [
    ("Gemini 3.1 Flash Lite (Khuyên dùng · 15 RPM)", "gemini-3.1-flash-lite"),
    ("Gemini 2.5 Flash (Tiêu chuẩn)", "gemini-2.5-flash"),
    ("Gemini 2.5 Flash Lite (Siêu nhẹ)", "gemini-2.5-flash-lite"),
    ("Gemini 2.5 Pro (Tư duy sâu)", "gemini-2.5-pro"),
    ("Gemini 3 Flash", "gemini-3-flash"),
    ("Gemini 3.1 Pro", "gemini-3.1-pro"),
    ("Gemini 3.5 Flash Lite (15 RPM / 500 RPD)", "gemini-3.5-flash-lite"),
    ("Gemini 3.6 Flash", "gemini-3.6-flash"),
    ("Gemini 3.8 Flash", "gemini-3.8-flash"),
    ("Gemini 4 Argon (Thế hệ 4)", "gemini-4-argon"),
    ("Gemini 2 Flash", "gemini-2.0-flash"),
    ("Gemini 2 Flash Lite", "gemini-2.0-flash-lite"),
    ("Gemini Omni 1.1 Flash (Đa phương thức)", "gemini-omni-1.1-flash"),
    ("Gemma 4 26B (Open Model)", "gemma-4-26b"),
    ("Gemma 4 31B (Open Model)", "gemma-4-31b"),
]
MODEL_CHOICES = tuple(m[1] for m in GEMINI_TEXT_MODELS)

# Retired IDs still stored in older settings files; remapped so requests keep working.
LEGACY_MODEL_ALIASES = {
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-pro": "gemini-2.5-pro",
}
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

_CHUNK_LIMIT = 6000


def normalize_model(model: str) -> str:
    """Return a usable model ID, remapping retired ones to the default."""
    name = str(model or "").strip()
    return LEGACY_MODEL_ALIASES.get(name, name or DEFAULT_MODEL)


class AIError(RuntimeError):
    """The Gemini request failed (missing key, quota, network, empty answer)."""


class AICancelled(AIError):
    """The user stopped translation between chunks."""


def gemini_generate(
    prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 120.0,
    temperature: float = 0.4,
) -> str:
    """Send one prompt to Gemini and return the concatenated answer text."""
    key = str(api_key or "").strip()
    if not key:
        raise AIError("Thiếu API key Gemini. Nhập key ở tab Kịch bản hoặc Video.")
    body = str(prompt or "").strip()
    if not body:
        raise AIError("Prompt rỗng, không có gì để gửi AI.")
    payload = {
        "contents": [{"parts": [{"text": body}]}],
        "generationConfig": {"temperature": float(temperature)},
    }
    request = urllib.request.Request(
        _ENDPOINT.format(model=normalize_model(model)),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise AIError(f"Gemini lỗi HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AIError(f"Gọi Gemini thất bại: {exc}") from exc
    parts = (((data.get("candidates") or [{}])[0].get("content") or {})
             .get("parts") or [])
    answer = "".join(str(part.get("text") or "") for part in parts).strip()
    if not answer:
        raise AIError("Gemini không trả về nội dung.")
    return answer


def build_translate_prompt(
    text: str,
    source_lang: str,
    target_lang: str,
    style: str = "",
) -> str:
    """Prompt that adapts a narration script into ``target_lang`` naturally.

    When source and target match, the same prompt is used to clean/rework the
    story in place instead of translating it.
    """
    source = language_name(source_lang)
    target = language_name(target_lang)
    extra = " ".join(str(style or "").split())
    if normalize_language(source_lang) == normalize_language(target_lang):
        lines = [
            "Bạn là biên kịch kể chuyện chuyên nghiệp.",
            f"Làm sạch và chuyển thể lại văn bản {target} dưới đây.",
            "Giữ nguyên toàn bộ sự kiện, con số và chi tiết — KHÔNG thêm, bớt hay "
            "diễn giải nội dung.",
            "Viết thành lời kể tự nhiên để đọc thành tiếng: câu ngắn 8-20 từ, "
            "không emoji, không markdown, không ghi chú, số nhỏ viết bằng chữ.",
        ]
        if extra:
            lines.append(f"Yêu cầu thêm về phong cách: {extra}")
        lines.append("Chỉ trả về văn bản đã làm sạch, không kèm giải thích.")
    else:
        lines = [
            "Bạn là biên kịch kể chuyện chuyên nghiệp.",
            f"Chuyển thể văn bản từ {source} sang {target}.",
            "Giữ nguyên toàn bộ sự kiện, con số và chi tiết — KHÔNG thêm, bớt hay "
            "diễn giải nội dung.",
            "Viết thành lời kể tự nhiên để đọc thành tiếng: câu ngắn 8-20 từ, "
            "không emoji, không markdown, không ghi chú, số nhỏ viết bằng chữ.",
        ]
        if extra:
            lines.append(f"Yêu cầu thêm về phong cách: {extra}")
        lines.append("Chỉ trả về bản dịch, không kèm giải thích.")
    lines.append("")
    lines.append("---")
    lines.append(str(text or "").strip())
    return "\n".join(lines)


_CLEAN_MARKER = "--- [DÁN ĐOẠN TRANSCRIPT CỦA BẠN VÀO ĐÂY] ---"

DEFAULT_CLEAN_PROMPT = """Bạn là một biên tập viên nội dung chuyên nghiệp. Hãy giúp tôi làm sạch và biên tập lại đoạn transcript video YouTube dưới đây theo các quy tắc nghiêm ngặt sau:

1. Xóa bỏ hoàn toàn:
- Mọi thẻ âm thanh, tiếng ồn (ví dụ: [âm nhạc], >>, ồ ồ, nô nô nô...).
- Lời kêu gọi hành động (CTA) đầu/cuối video: chào mừng, like, share, subscribe, bấm chuông, xin nút vàng...
- Toàn bộ nội dung quảng cáo/tài trợ (đặc biệt là đoạn quảng cáo ứng dụng chứng khoán DNSE, trứng vàng...).

2. Chuẩn hóa văn phong & chính tả:
- Sửa các từ nhận diện sai chính tả do âm thanh (ví dụ: "khồn khà" -> không có, "con mã" -> món ăn, "điên lẩu" -> đi ăn lẩu, "Sawadee pị mài" -> Sawasdee Pee Mai...).
- Viết hoa đúng danh từ riêng, địa danh, tên món ăn (Bangkok, Isan, Chao Phraya, Som Tam, Tom Yum, Pad Thai, Jim Jum, 7-Eleven, Songkran, Jodd Fairs...).
- Giữ nguyên văn phong vui vẻ, dí dỏm và các kiến thức văn hóa/ẩm thực cốt lõi của bài gốc.

3. Định dạng đầu ra:
- Định dạng bài viết mạch lạc thành các đoạn văn hoàn chỉnh hoặc có tiêu đề con (H2/H3) theo từng chủ đề: Giao thông & Chùa chiền tại Bangkok, Phân biệt Som Tam & Yam, Sự thật về lẩu Thái & Tom Yum, Xôi xoài & Cách tránh nóng mùa hè, Lễ hội té nước Songkran, Nguồn gốc Pad Thai, Văn hóa chợ đêm & Ẩm thực đường phố.

--- [DÁN ĐOẠN TRANSCRIPT CỦA BẠN VÀO ĐÂY] ---
"""


def build_clean_prompt(text: str, instruction: str) -> str:
    """Paste the raw transcript into the cleaning instruction at its marker."""
    body = str(instruction or "").strip()
    raw = str(text or "").strip()
    if _CLEAN_MARKER in body:
        return body.replace(_CLEAN_MARKER, raw)
    return f"{body}\n\n---\n{raw}"


def split_for_translation(text: str, limit: int = _CHUNK_LIMIT) -> List[str]:
    """Split text on paragraph boundaries, packing up to ``limit`` characters."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    limit = max(500, int(limit))
    chunks: List[str] = []
    buffer = ""
    for paragraph in re.split(r"\n\s*\n", raw):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > limit:
            cut = paragraph.rfind(". ", 0, limit)
            if cut < limit // 2:
                cut = paragraph.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            else:
                cut += 1
            piece = paragraph[:cut].strip()
            paragraph = paragraph[cut:].strip()
            if buffer:
                chunks.append(buffer)
                buffer = ""
            if piece:
                chunks.append(piece)
        if not paragraph:
            continue
        if not buffer:
            buffer = paragraph
        elif len(buffer) + 2 + len(paragraph) <= limit:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            chunks.append(buffer)
            buffer = paragraph
    if buffer:
        chunks.append(buffer)
    return chunks


def translate_text(
    text: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    source_lang: str = "vi",
    target_lang: str = "en",
    style: str = "",
    generate_fn: Optional[Callable[[str, str, str], str]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    retries: int = 2,
    retry_delay: float = 1.5,
) -> str:
    """Translate long text chunk by chunk; returns the joined translation.

    ``generate_fn`` matches :func:`gemini_generate` (prompt, key, model) so tests
    can inject a fake model without touching the network.
    """
    source = normalize_language(source_lang)
    target = normalize_language(target_lang)
    chunks = split_for_translation(text)
    if not chunks:
        return ""
    if not str(api_key or "").strip() and generate_fn is None:
        raise AIError("Thiếu API key Gemini — không thể chuyển thể.")
    generator = generate_fn or gemini_generate
    translated: List[str] = []
    total = len(chunks)
    for position, chunk in enumerate(chunks, 1):
        if cancel_cb and cancel_cb():
            raise AICancelled("Đã huỷ chuyển thể.")
        prompt = build_translate_prompt(chunk, source, target, style)
        attempt = 0
        while True:
            try:
                piece = generator(prompt, api_key, model)
                break
            except Exception as exc:
                if cancel_cb and cancel_cb():
                    raise AICancelled("Đã huỷ chuyển thể.") from exc
                if attempt >= retries:
                    raise AIError(
                        f"Chuyển thể đoạn {position}/{total} thất bại: {exc}"
                    ) from exc
                attempt += 1
                time.sleep(max(0.0, float(retry_delay)))
        clean = str(piece or "").strip()
        if not clean:
            raise AIError(f"AI trả về đoạn {position}/{total} rỗng.")
        translated.append(clean)
        if progress_cb:
            try:
                progress_cb(position, total)
            except Exception:
                pass
    return "\n\n".join(translated)
