"""Step 1 of the pipeline: turn a YouTube video or a web page into a ``.txt``.

Everything here is Qt-free so it can run in a worker thread or from a CLI:

* :func:`download_youtube` — fetch the best MP4 with yt-dlp (lazy import);
* :func:`transcribe_media` — speech-to-text with faster-whisper (any language);
* :func:`fetch_web_text` — dependency-free HTML → plain text (stdlib only);
* :func:`polish_text` — optional Gemini pass that fixes punctuation/typos;
* :func:`youtube_to_text` — YouTube captions → plain ``.txt`` (no video download);
* :func:`web_to_text` — the full web step-1 workflow.

The optional dependencies (``yt-dlp``, ``faster-whisper``) are imported inside
the functions, so a missing package surfaces as a clear Vietnamese error instead
of an ImportError at application start.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from core.ai_client import DEFAULT_MODEL, AIError, gemini_generate
from core.languages import (
    DEFAULT_LANGUAGE,
    language_name,
    normalize_language,
)
from core.subtitles import Cue
from utils.logger import get_logger

logger = get_logger("input")

ProgressCb = Optional[Callable[[int, str], None]]
CancelCb = Optional[Callable[[], bool]]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_AI_DEFAULT_MODEL = DEFAULT_MODEL


class InputError(RuntimeError):
    """A step-1 import failed (missing dependency, bad URL, network error)."""


class InputCancelled(InputError):
    """The user stopped the import."""


def _report(cb: ProgressCb, percent: int, message: str) -> None:
    if cb is None:
        return
    try:
        cb(max(0, min(100, int(percent))), message)
    except Exception:
        pass


def _check_cancel(cancel_cb: CancelCb) -> None:
    if cancel_cb and cancel_cb():
        raise InputCancelled("Đã huỷ nhập liệu.")


def save_text(path: str | os.PathLike, text: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(text).strip() + "\n", encoding="utf-8", newline="\n")
    return target


# ── YouTube (yt-dlp) ─────────────────────────────────────────────────────────
def download_youtube(url: str, dest_dir: str | os.PathLike,
                     progress_cb: ProgressCb = None,
                     cancel_cb: CancelCb = None) -> Path:
    """Download ``url`` as MP4 into ``dest_dir`` and return the file path."""
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise InputError("URL YouTube không hợp lệ.")
    try:
        import yt_dlp
    except ImportError as exc:
        raise InputError("Thiếu thư viện yt-dlp. Cài bằng: pip install yt-dlp") from exc

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    def hook(data: dict) -> None:
        _check_cancel(cancel_cb)
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            percent = int(done * 40 / total) if total else 0
            _report(progress_cb, percent,
                    f"Đang tải video: {data.get('_percent_str', '').strip()}")
        elif status == "finished":
            _report(progress_cb, 40, "Đã tải xong video, đang ghép...")

    options = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(dest / "%(title).120B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and info.get("_type") == "playlist":
                entries = [entry for entry in (info.get("entries") or []) if entry]
                info = entries[0] if entries else info
            target = Path(ydl.prepare_filename(info))
    except InputCancelled:
        raise
    except Exception as exc:
        raise InputError(f"Tải YouTube thất bại: {exc}") from exc
    if not target.is_file():
        candidates = sorted(dest.glob(f"{target.stem}.*"))
        candidates = [path for path in candidates if path.suffix.lower() in
                      {".mp4", ".mkv", ".webm", ".mov", ".m4a"}]
        if not candidates:
            raise InputError(f"Không tìm thấy file video đã tải trong {dest}.")
        target = candidates[0]
    _report(progress_cb, 40, f"Đã tải: {target.name}")
    logger.info("YouTube → %s", target)
    return target


# ── YouTube metadata & transcript ────────────────────────────────────────────
_YT_ID_RE = re.compile(
    r"(?:v=|/videos/|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})")


def _require_ytdlp():
    try:
        import yt_dlp
    except ImportError as exc:
        raise InputError("Thiếu thư viện yt-dlp. Cài bằng: pip install yt-dlp") from exc
    return yt_dlp


def _validate_youtube_url(url: str) -> str:
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise InputError("URL YouTube không hợp lệ.")
    return url


def youtube_video_id(url: str) -> str:
    """Extract the 11-character video id from any common YouTube URL."""
    match = _YT_ID_RE.search(_validate_youtube_url(url))
    if match is None:
        raise InputError("Không nhận ra ID video YouTube từ URL.")
    return match.group(1)


def _load_transcript_api():
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise InputError(
            "Thiếu thư viện youtube-transcript-api. "
            "Cài bằng: pip install youtube-transcript-api") from exc
    return YouTubeTranscriptApi


def fetch_youtube_info(url: str) -> Dict[str, object]:
    """Return title/id/duration and which caption tracks the video offers."""
    url = _validate_youtube_url(url)
    yt_dlp = _require_ytdlp()
    options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise InputError(f"Không lấy được thông tin video: {exc}") from exc
    if not info:
        raise InputError("Không lấy được thông tin video.")
    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        info = entries[0] if entries else info
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    return {
        "title": str(info.get("title") or "").strip(),
        "video_id": str(info.get("id") or "").strip(),
        "duration": float(info.get("duration") or 0.0),
        "has_manual_subs": bool(manual),
        "has_auto_subs": bool(auto),
        "sub_langs": sorted({str(code) for code in list(manual) + list(auto)}),
        "url": url,
    }


def fetch_youtube_title(url: str) -> str:
    """Best-effort title via YouTube's oEmbed endpoint (stdlib only)."""
    endpoint = ("https://www.youtube.com/oembed?format=json&url="
                + quote(_validate_youtube_url(url), safe=""))
    request = urllib.request.Request(endpoint, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(_decode(response.read(),
                                      response.headers.get("Content-Type", "")))
        return str(data.get("title") or "").strip()
    except Exception as exc:
        logger.warning("Không lấy được tiêu đề video: %s", exc)
        return ""


def fetch_youtube_transcript(
    url: str,
    languages: Optional[List[str]] = None,
    cancel_cb: CancelCb = None,
) -> Optional[Tuple[List[Cue], str]]:
    """Fetch YouTube captions with ``youtube-transcript-api``.

    Returns ``(cues, language_code)``; ``None`` when the video has no usable
    transcript. A missing package raises :class:`InputError`.
    """
    video_id = youtube_video_id(url)
    api_cls = _load_transcript_api()
    wanted: List[str] = []
    for code in (languages or ["vi", "en"]):
        normalized = normalize_language(code, "")
        if normalized and normalized not in wanted:
            wanted.append(normalized)
    try:
        api = api_cls()
        # youtube-transcript-api >= 1.0 exposes instance methods; older
        # releases only provide the classmethod ``list_transcripts``.
        list_method = getattr(api, "list", None) or api_cls.list_transcripts
        transcripts = list_method(video_id)
        try:
            transcript = transcripts.find_transcript(wanted or ["vi", "en"])
        except Exception:
            # No track in the requested languages: take whatever exists.
            transcript = next(iter(transcripts), None)
        if transcript is None:
            return None
        fetched = transcript.fetch()
        rows = (fetched.to_raw_data() if hasattr(fetched, "to_raw_data")
                else list(fetched))
    except Exception as exc:
        logger.warning("Không lấy được transcript YouTube: %s", exc)
        return None
    _check_cancel(cancel_cb)
    cues: List[Cue] = []
    for snippet in rows:
        if isinstance(snippet, dict):
            start = float(snippet.get("start") or 0.0)
            duration = float(snippet.get("duration") or 0.0)
            text = str(snippet.get("text") or "")
        else:
            start = float(getattr(snippet, "start", 0.0) or 0.0)
            duration = float(getattr(snippet, "duration", 0.0) or 0.0)
            text = str(getattr(snippet, "text", "") or "")
        text = " ".join(text.split())
        if text:
            cues.append(Cue(index=len(cues) + 1, start=start,
                            end=start + max(0.0, duration), text=text))
    if not cues:
        return None
    code = normalize_language(getattr(transcript, "language_code", ""), "")
    return cues, code or (wanted[0] if wanted else DEFAULT_LANGUAGE)


def _wanted_languages(language: str) -> List[str]:
    wanted: List[str] = []
    for code in (language, "en", "vi"):
        normalized = normalize_language(code, "")
        if normalized and normalized not in wanted:
            wanted.append(normalized)
    return wanted


def get_youtube_transcript(
    url: str,
    language: str = DEFAULT_LANGUAGE,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> Tuple[List[Cue], Dict[str, object]]:
    """YouTube URL → cue list + metadata from existing captions only."""
    url = _validate_youtube_url(url)
    _check_cancel(cancel_cb)
    _report(progress_cb, 3, "Đang lấy thông tin video...")
    meta: Dict[str, object] = {
        "title": fetch_youtube_title(url),
        "video_id": youtube_video_id(url),
        "duration": 0.0,
        "source_language": normalize_language(language, DEFAULT_LANGUAGE),
        "origin": "youtube",
        "url": url,
    }
    _report(progress_cb, 8, f"Đã thấy video: {meta['title'] or meta['video_id']}")
    _report(progress_cb, 12, "Đang lấy transcript YouTube...")
    found = fetch_youtube_transcript(url, _wanted_languages(language), cancel_cb)
    if found is None:
        raise InputError(
            "Video không có phụ đề khả dụng. Chọn video có phụ đề hoặc nguồn khác.")
    cues, detected = found
    meta["source_language"] = normalize_language(
        detected, str(meta["source_language"]))
    meta["duration"] = max(0.0, cues[-1].end)
    _report(progress_cb, 100, f"Đã lấy {len(cues)} dòng phụ đề YouTube.")
    return cues, meta


# ── Speech-to-text (faster-whisper) ──────────────────────────────────────────
def transcribe_segments(media_path: str | os.PathLike,
                        language: str = DEFAULT_LANGUAGE, model_size: str = "small",
                        progress_cb: ProgressCb = None,
                        cancel_cb: CancelCb = None) -> Tuple[List[Cue], str]:
    """Transcribe ``media_path`` into timestamped cues plus the detected language."""
    source = Path(media_path)
    if not source.is_file():
        raise InputError(f"Không tìm thấy file media: {source}")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise InputError(
            "Thiếu thư viện faster-whisper. Cài bằng: pip install faster-whisper"
        ) from exc

    code = normalize_language(language, "")
    _report(progress_cb, 41, f"Đang nạp model nhận dạng ({model_size})...")
    try:
        model = WhisperModel(model_size or "small", device="cpu", compute_type="int8")
        _check_cancel(cancel_cb)
        segments, info = model.transcribe(
            str(source),
            language=code or None,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
    except InputCancelled:
        raise
    except Exception as exc:
        raise InputError(f"Nhận dạng giọng nói thất bại: {exc}") from exc

    total = float(getattr(info, "duration", 0) or 0)
    cues: List[Cue] = []
    previous_end = 0.0
    for segment in segments:
        _check_cancel(cancel_cb)
        piece = " ".join(str(segment.text or "").split())
        if not piece:
            continue
        start = max(0.0, float(segment.start))
        end = max(start, float(segment.end))
        cues.append(Cue(len(cues) + 1, start, end, piece))
        previous_end = end
        if total > 0:
            _report(progress_cb, 41 + int(49 * min(1.0, previous_end / total)),
                    f"Đang nhận dạng: {previous_end:.0f}/{total:.0f}s")
    if not cues:
        raise InputError("Không nhận dạng được lời nói nào trong file.")
    detected = str(getattr(info, "language", "") or code or DEFAULT_LANGUAGE).strip()
    logger.info("Whisper: %s (%d đoạn, ngôn ngữ %s)", source.name, len(cues), detected)
    return cues, detected


def cues_to_text(cues: List[Cue], gap_seconds: float = 1.2,
                 max_chars: int = 600) -> str:
    """Join timestamped cues into paragraphs at topic breaks."""
    paragraphs: List[str] = []
    buffer: List[str] = []
    previous_end = 0.0
    for cue in cues:
        text = str(cue.text or "").strip()
        if not text:
            continue
        gap = cue.start - previous_end
        if buffer and (gap > gap_seconds or
                       sum(len(part) for part in buffer) > max_chars):
            paragraphs.append(" ".join(buffer))
            buffer = []
        buffer.append(text)
        previous_end = cue.end
    if buffer:
        paragraphs.append(" ".join(buffer))
    return "\n\n".join(paragraphs).strip()


def transcribe_media(media_path: str | os.PathLike, language: str = DEFAULT_LANGUAGE,
                     model_size: str = "small", progress_cb: ProgressCb = None,
                     cancel_cb: CancelCb = None) -> str:
    """Transcribe ``media_path`` into text, one paragraph per topic break."""
    cues, _detected = transcribe_segments(
        media_path, language, model_size, progress_cb, cancel_cb)
    text = cues_to_text(cues)
    if not text:
        raise InputError("Không nhận dạng được lời nói nào trong file.")
    _report(progress_cb, 90, f"Đã nhận dạng {len(text)} ký tự.")
    return text


# ── Web page → text (stdlib only) ────────────────────────────────────────────
class _HTMLTextExtractor(HTMLParser):
    _BLOCK = {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
        "article", "section", "blockquote", "tr", "table", "header", "footer",
    }
    _SKIP = {
        "script", "style", "noscript", "svg", "iframe", "form", "nav", "aside",
        "header", "footer",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        raw = str(data or "")
        text = " ".join(raw.split())
        if not text:
            if raw:
                self.parts.append(" ")
            return
        # Keep one space where the source had whitespace so inline tags such as
        # ``<b>đậm</b>.`` do not turn into ``đậm .``.
        self.parts.append((" " if raw[:1].isspace() else "") + text
                          + (" " if raw[-1:].isspace() else ""))

    def text(self) -> str:
        raw = "".join(self.parts)
        lines: List[str] = []
        for block in re.split(r"\n\s*\n+|\n", raw):
            value = " ".join(block.split())
            if value:
                lines.append(value)
        return "\n\n".join(lines)


def _decode(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    if match:
        charset = match.group(1)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_web_text(url: str, timeout: float = 25.0) -> Tuple[str, str]:
    """Return ``(title, plain_text)`` for a public web page."""
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise InputError("URL trang web không hợp lệ.")
    request = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "vi,en;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            html = _decode(body, response.headers.get("Content-Type", ""))
    except urllib.error.URLError as exc:
        raise InputError(f"Không tải được trang web: {exc}") from exc
    except OSError as exc:
        raise InputError(f"Lỗi mạng khi tải trang: {exc}") from exc
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # malformed HTML must not stop the import
        logger.warning("HTML lỗi một phần (%s), vẫn dùng phần đã đọc.", exc)
    text = parser.text()
    if not text.strip():
        raise InputError("Trang web không có nội dung văn bản đọc được.")
    return " ".join(parser.title.split()), text


# ── optional AI cleanup (Gemini) ─────────────────────────────────────────────
def polish_text(text: str, api_key: str, model: str = _AI_DEFAULT_MODEL,
                language: str = DEFAULT_LANGUAGE, timeout: float = 90.0) -> str:
    """Fix punctuation/typos with Gemini; returns ``text`` unchanged without a key."""
    key = str(api_key or "").strip()
    text = str(text or "").strip()
    if not text:
        return ""
    if not key:
        return text
    prompt = (
        f"Bạn là biên tập viên {language_name(language)}. Văn bản sau là bản ghi "
        "tự động từ video, có thể sai chính tả, thiếu dấu câu hoặc ngắt dòng lộn xộn. "
        "Hãy sửa lỗi chính tả, thêm dấu câu và chia đoạn hợp lý nhưng TUYỆT ĐỐI "
        "không thêm, bớt hay diễn giải nội dung. Chỉ trả về văn bản đã sửa.\n\n"
        f"---\n{text}"
    )
    try:
        return gemini_generate(prompt, key, model or _AI_DEFAULT_MODEL,
                               timeout=timeout, temperature=0.2)
    except AIError as exc:
        raise InputError(f"AI sửa văn bản thất bại: {exc}") from exc


# ── full step-1 workflows ────────────────────────────────────────────────────
def youtube_to_text(url: str, out_txt: str | os.PathLike,
                    language: str = DEFAULT_LANGUAGE, api_key: str = "",
                    ai_model: str = _AI_DEFAULT_MODEL,
                    progress_cb: ProgressCb = None,
                    cancel_cb: CancelCb = None) -> Path:
    """YouTube transcript → ``.txt`` (youtube-transcript-api, no download).

    ``out_txt`` may be a ``.txt`` path or a folder; folders get a name derived
    from the video title so imports are easy to recognise.
    """
    url = _validate_youtube_url(url)
    target = Path(out_txt)
    out_folder = target if target.suffix.lower() != ".txt" else target.parent
    out_folder.mkdir(parents=True, exist_ok=True)
    _check_cancel(cancel_cb)
    _report(progress_cb, 5, "Đang lấy thông tin video...")
    title = fetch_youtube_title(url)
    if target.suffix.lower() != ".txt":
        target = unique_text_path(out_folder, title or youtube_video_id(url))
    _report(progress_cb, 12, "Đang lấy transcript YouTube...")
    found = fetch_youtube_transcript(url, _wanted_languages(language), cancel_cb)
    if found is None:
        raise InputError(
            "Video không có phụ đề khả dụng. Chọn video có phụ đề hoặc nguồn khác.")
    cues, _detected = found
    if not cues:
        raise InputError("Phụ đề của video rỗng, không có nội dung để lưu.")
    text = cues_to_text(cues)
    _report(progress_cb, 70, f"Đã lấy {len(cues)} dòng phụ đề.")
    if str(api_key or "").strip():
        _check_cancel(cancel_cb)
        _report(progress_cb, 80, "Đang nhờ AI sửa lỗi văn bản...")
        text = polish_text(text, api_key, ai_model, language)
    _check_cancel(cancel_cb)
    save_text(target, text)
    _report(progress_cb, 100, f"Đã lưu {target.name}.")
    return target


def web_to_text(url: str, out_txt: str | os.PathLike,
                language: str = DEFAULT_LANGUAGE, api_key: str = "",
                ai_model: str = _AI_DEFAULT_MODEL,
                progress_cb: ProgressCb = None,
                cancel_cb: CancelCb = None) -> Path:
    """Web page URL → readable text → ``.txt`` (optionally polished by AI).

    ``out_txt`` may be a ``.txt`` path or a folder; folders get a name derived
    from the page title.
    """
    target = Path(out_txt)
    _check_cancel(cancel_cb)
    _report(progress_cb, 5, "Đang tải trang web...")
    title, text = fetch_web_text(url)
    if target.suffix.lower() != ".txt":
        target = unique_text_path(target, title or "web")
    _report(progress_cb, 55, f"Đã đọc {len(text)} ký tự từ trang.")
    if str(api_key or "").strip():
        _check_cancel(cancel_cb)
        _report(progress_cb, 60, "Đang nhờ AI sửa lỗi văn bản...")
        text = polish_text(text, api_key, ai_model, language)
    _check_cancel(cancel_cb)
    body = text
    if title and not body.lstrip().startswith(title):
        body = f"{title}\n\n{body}"
    save_text(target, body)
    _report(progress_cb, 100, f"Đã lưu {target.name}.")
    return target


def unique_text_path(folder: str | os.PathLike, stem: str) -> Path:
    """``tap_01.txt`` → ``tap_01-2.txt`` … so imports never overwrite inputs."""
    base = re.sub(r"[^\w.-]+", "_", str(stem or "input")).strip("._") or "input"
    folder = Path(folder)
    candidate = folder / f"{base}.txt"
    index = 2
    while candidate.exists():
        candidate = folder / f"{base}-{index}.txt"
        index += 1
    return candidate
