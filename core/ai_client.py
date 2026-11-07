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


_SOUND_TAGS_RE = re.compile(
    r"\[\s*(?:"
    r"âm nhạc|nhạc|music|tiếng cười|cười|vỗ tay|tiếng vỗ tay|applause|laughter|"
    r"tiếng ồn|ồ ồ|nô nô nô|chửi thề|beep|tiếng thở dài|sigh|gasp|screaming|"
    r"sound|audio|noise|cheering|silence|__+"
    r")\s*\]|"
    r"\(\s*(?:"
    r"âm nhạc|nhạc|music|tiếng cười|cười|vỗ tay|tiếng vỗ tay|applause|laughter|"
    r"tiếng ồn|ồ ồ|nô nô nô|chửi thề|beep|tiếng thở dài|sigh|gasp|screaming"
    r")\s*\)",
    re.IGNORECASE,
)
_SPEAKER_MARKER_RE = re.compile(r"(?:^|\s)(?:>>+|>>>+)(?:\s+|$)", re.MULTILINE)
_ORPHAN_MARKER_RE = re.compile(r">>+")


def clean_transcript_heuristics(text: str) -> str:
    """Fast, deterministic rule-based cleaner for YouTube captions and transcripts.

    Eliminates auto-generated captions artifacts:
    - Sound and music markers: [âm nhạc], (music), [tiếng cười], [vỗ tay], [__], etc.
    - Speaker transitions (>>, >>>)
    - Consecutive spaces, whitespace before punctuation, and broken paragraph breaks.
    """
    if not text:
        return ""
    # 1. Strip sound tags
    cleaned = _SOUND_TAGS_RE.sub("", str(text))
    # 2. Strip generic brackets with music/sound cues
    cleaned = re.sub(
        r"\[[^\]\n]*(?:nhạc|music|tiếng|sound|noise|cười|applause|laughter|vỗ tay)[^\]\n]*\]",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # 3. Strip speaker markers
    cleaned = _SPEAKER_MARKER_RE.sub(" ", cleaned)
    cleaned = _ORPHAN_MARKER_RE.sub("", cleaned)
    # 4. Normalize spacing and punctuation
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.:?!;])", r"\1", cleaned)
    cleaned = re.sub(r"([.?!])\s*([.?!])+", r"\1", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def gemini_generate(
    prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 120.0,
    temperature: float = 0.4,
    retries: int = 3,
) -> str:
    """Send one prompt to Gemini and return the concatenated answer text.

    Includes automatic retries for transient HTTP 503 (high demand), 429 (rate limit),
    and network timeouts, plus model fallback when candidate models are retired.
    """
    key = str(api_key or "").strip()
    if not key:
        raise AIError("Thiếu API key Gemini. Nhập key ở tab Kịch bản hoặc Cài đặt.")
    body = str(prompt or "").strip()
    if not body:
        raise AIError("Prompt rỗng, không có gì để gửi AI.")

    chosen_model = normalize_model(model)
    candidate_models = [chosen_model]
    for fallback in ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite"):
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    last_exc: Optional[Exception] = None

    for current_model in candidate_models:
        for attempt in range(retries):
            payload = {
                "contents": [{"parts": [{"text": body}]}],
                "generationConfig": {"temperature": float(temperature)},
            }
            request = urllib.request.Request(
                _ENDPOINT.format(model=current_model),
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
                answer = "".join(str(part.get("text") or "") for part in parts).strip()
                if not answer:
                    raise AIError("Gemini không trả về nội dung.")
                return answer
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                last_exc = AIError(f"Gemini lỗi HTTP {exc.code}: {detail}")
                if exc.code == 404:
                    # Model retired or not found, try next candidate model
                    break
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if exc.code not in (429, 500, 502, 503, 504):
                    raise last_exc from exc
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_exc = AIError(f"Gọi Gemini thất bại: {exc}")
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue

    if last_exc:
        raise last_exc
    raise AIError("Không thể nhận phản hồi từ Gemini.")


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

DEFAULT_CLEAN_PROMPT = """Bạn là một biên tập viên nội dung video chuyên nghiệp. Hãy giúp tôi làm sạch, biên tập và chuẩn hóa lại đoạn transcript video YouTube dưới đây theo các quy tắc sau:

1. Xóa bỏ hoàn toàn:
- Mọi ký hiệu kỹ thuật, thẻ âm thanh, tiếng ồn: [âm nhạc], (âm nhạc), [nhạc], >>, >>>, [tiếng cười], [vỗ tay], [tiếng ồn], ồ ồ, nô nô nô, [__], [beep]...
- Các lời chào mở đầu hoặc kêu gọi hành động (CTA): "chào mừng các bạn", "nhìn xuống dưới thấy nút đăng ký không", "bấm sub đi để vào video", "like share subscribe", "bấm chuông"...
- Toàn bộ nội dung quảng cáo, giới thiệu nhà tài trợ không thuộc nội dung chính của video.

2. Chuẩn hóa văn phong & chính tả:
- Sửa các từ nhận diện sai chính tả do âm thanh tự động (speech-to-text), các từ nói lắp hoặc từ đệm vô nghĩa.
- Viết hoa đúng danh từ riêng, tên nhân vật, địa danh, thuật ngữ, tựa game/phim/sản phẩm.
- Giữ nguyên nội dung, cốt truyện, sự kiện và văn phong tự nhiên, liền mạch của bài gốc.

3. Định dạng đầu ra:
- Phân đoạn thành các đoạn văn mạch lạc, câu văn rõ ràng, tự nhiên để đọc thành tiếng (voiceover/TTS).
- Tuyệt đối KHÔNG chèn thêm lời giải thích hay ghi chú của AI (như "Dưới đây là bản đã làm sạch...").

--- [DÁN ĐOẠN TRANSCRIPT CỦA BẠN VÀO ĐÂY] ---
"""


def build_clean_prompt(text: str, instruction: str) -> str:
    """Paste the raw transcript into the cleaning instruction at its marker."""
    body = str(instruction or "").strip()
    raw = str(text or "").strip()
    if _CLEAN_MARKER in body:
        return body.replace(_CLEAN_MARKER, raw)
    return f"{body}\n\n---\n{raw}"


def clean_transcript(
    text: str,
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    instruction: str = "",
    generate_fn: Optional[Callable[[str, str, str], str]] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    retries: int = 3,
) -> str:
    """Clean and standardize a transcript using deterministic heuristics and AI.

    1. Always strips YouTube sound tags ([âm nhạc], [tiếng cười]...), speaker cues (>>),
       orphan punctuation, and formatting artifacts.
    2. If an API key is available, passes chunks through Gemini with instruction to
       remove CTAs, sponsors, fix speech-to-text typos, and reformat into narration paragraphs.
    3. If AI is unavailable or fails, gracefully falls back to the heuristic-cleaned text.
    """
    if not text:
        return ""

    pre_cleaned = clean_transcript_heuristics(text)
    key = str(api_key or "").strip()
    if not key and generate_fn is None:
        return pre_cleaned

    generator = generate_fn or gemini_generate
    prompt_template = str(instruction or "").strip() or DEFAULT_CLEAN_PROMPT

    chunks = split_for_translation(pre_cleaned, limit=4500)
    if not chunks:
        return pre_cleaned

    cleaned_pieces: List[str] = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks):
        if cancel_cb and cancel_cb():
            raise AICancelled("Đã huỷ làm sạch transcript.")
        if progress_cb:
            pct = int((idx / total) * 100)
            progress_cb(pct, f"Đang làm sạch transcript bằng AI ({idx + 1}/{total})…")

        prompt = build_clean_prompt(chunk, prompt_template)
        piece = ""
        attempt = 0
        while True:
            try:
                piece = generator(prompt, key, model)
                break
            except Exception as exc:
                if cancel_cb and cancel_cb():
                    raise AICancelled("Đã huỷ làm sạch transcript.") from exc
                attempt += 1
                if attempt >= retries:
                    piece = chunk
                    break
                time.sleep(1.5 * attempt)

        if piece.strip():
            cleaned_pieces.append(piece.strip())

    joined = "\n\n".join(cleaned_pieces).strip()
    return clean_transcript_heuristics(joined)


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
