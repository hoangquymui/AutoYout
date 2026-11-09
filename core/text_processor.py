"""Vietnamese text normalisation and sentence-safe chunking for TTS.

The TTS engines are fed 200-400 character chunks so a failed generation never
costs a whole chapter; chunks always end on a sentence/paragraph boundary when
one is available.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Sequence

_ELLIPSIS = "\u2026"
_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
}
_DASHES = {"\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Unicode ranges covering CJK ideographs, extension A, Kana and Hangul
_CJK_RANGES = (
    r"\u4e00-\u9fff"
    r"\u3400-\u4dbf"
    r"\u3040-\u309f"
    r"\u30a0-\u30ff"
    r"\uac00-\ud7af"
)
_TOKEN_RE = re.compile(rf"[{_CJK_RANGES}]|[a-zA-Z0-9_\u00c0-\u024f\u1ea0-\u1eff]+")
_CJK_CHAR_RE = re.compile(rf"[{_CJK_RANGES}]")

# Universal sentence break: handles Western [.!?] followed by whitespace,
# as well as CJK [。！？] with/without quotes or whitespace.
_SENTENCE_BREAK = re.compile(
    r"(?<=[.!?])\s+|"
    r"(?<=[。！？])(?=[^」』”’\"'\)\]]|$)|"
    r"(?<=[。！？][」』”’\"'\)\]])\s*"
)
_SUB_BREAK = re.compile(r"(?<=[,;:])\s+|(?<=[、，；：])\s*")
_WORD_BREAK = re.compile(r"\s+")


def count_words(text: str) -> int:
    """Universal word/token counter across Western, Vietnamese and CJK languages.

    For Latin/Vietnamese/European text, counts whitespace-separated words.
    For CJK (Japanese, Chinese) without whitespace, counts each ideograph/syllable
    as 1 unit, keeping Latin/numeric words (e.g. 'Claude', 'PC', '123') as single words.
    """
    if not text:
        return 0
    return len(_TOKEN_RE.findall(str(text)))


def _join_sentence_parts(parts: Sequence[str]) -> str:
    """Join sentence parts naturally without injecting spaces between CJK sentences."""
    if not parts:
        return ""
    result = parts[0].strip()
    for p in parts[1:]:
        p_clean = p.strip()
        if not p_clean:
            continue
        if result and result[-1] in "。！？…":
            result += p_clean
        elif _CJK_CHAR_RE.search(result[-1:]) and _CJK_CHAR_RE.search(p_clean[:1]):
            result += p_clean
        else:
            result += " " + p_clean
    return result


@dataclass
class Chunk:
    text: str
    gap_after: float = 0.3
    paragraph_end: bool = False


def normalize_text(text: str) -> str:
    """Clean text without changing the words the narrator has to read."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text).replace("\ufeff", ""))
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\u200c", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for src, dst in {**_QUOTES, **_DASHES, _ELLIPSIS: "..."}.items():
        text = text.replace(src, dst)
    text = _CONTROL.sub("", text)

    # Markdown / copy-paste decoration that would otherwise be spoken.
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "").replace("`", "")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of punctuation that TTS reads as noise.
    text = re.sub(r"\.{3,}", "...", text)
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\?{2,}", "?", text)
    return text.strip()


def split_sentences(paragraph: str) -> List[str]:
    paragraph = re.sub(r"\.{2,}", ".", paragraph).strip()
    if not paragraph:
        return []
    parts = _SENTENCE_BREAK.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def _split_long_piece(piece: str, max_chars: int) -> List[str]:
    """Split one over-long sentence at commas, then words, never mid-word."""
    piece = piece.strip()
    if len(piece) <= max_chars:
        return [piece] if piece else []
    out: List[str] = []
    buffer = ""
    for part in _SUB_BREAK.split(piece):
        part = part.strip()
        if not part:
            continue
        if len(part) > max_chars:
            if buffer:
                out.append(buffer)
                buffer = ""
            words = _WORD_BREAK.split(part)
            if len(words) <= 1 and _CJK_CHAR_RE.search(part):
                for i in range(0, len(part), max_chars):
                    sub = part[i:i + max_chars].strip()
                    if sub:
                        out.append(sub)
                continue
            current = ""
            for word in words:
                if not current:
                    current = word
                elif len(current) + 1 + len(word) <= max_chars:
                    current = f"{current} {word}"
                else:
                    out.append(current)
                    current = word
            if current:
                buffer = current
            continue
        if not buffer:
            buffer = part
        elif len(buffer) + 1 + len(part) <= max_chars:
            buffer = f"{buffer} {part}"
        else:
            out.append(buffer)
            buffer = part
    if buffer:
        out.append(buffer)
    return out


def chunk_text(
    text: str,
    min_chars: int = 200,
    max_chars: int = 400,
    gap_sentence: float = 0.3,
    gap_paragraph: float = 0.8,
    gap_inner: float = 0.15,
) -> List[Chunk]:
    """Split normalized text into chunks with the pause that follows each one.

    ``min_chars`` is a soft target: sentences are packed until the buffer reaches
    it, while no chunk ever exceeds ``max_chars``. The pause after the last chunk
    is always ``0``.
    """
    max_chars = max(80, int(max_chars))
    min_chars = max(20, min(int(min_chars), max_chars))
    text = normalize_text(text)
    if not text:
        return []

    texts: List[str] = []
    kinds: List[str] = []  # boundary between this chunk and the next one

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for paragraph in paragraphs:
        paragraph_start = len(texts)
        buffer = ""
        for sentence in split_sentences(paragraph):
            pieces = _split_long_piece(sentence, max_chars)
            last_index = len(pieces) - 1
            for piece_index, piece in enumerate(pieces):
                boundary_kind = "inner" if piece_index else "sentence"
                if buffer and len(buffer) + 1 + len(piece) > max_chars:
                    texts.append(buffer)
                    kinds.append(boundary_kind)
                    buffer = piece
                elif buffer:
                    buffer = f"{buffer} {piece}"
                else:
                    buffer = piece
                if piece_index == last_index and len(buffer) >= min_chars:
                    texts.append(buffer)
                    kinds.append("sentence")
                    buffer = ""
        if buffer:
            texts.append(buffer)
            kinds.append("sentence")
        # Everything produced for this paragraph ends at a paragraph boundary.
        for index in range(paragraph_start, len(texts)):
            if index == len(texts) - 1:
                kinds[index] = "paragraph"

    gap_map = {"inner": gap_inner, "sentence": gap_sentence, "paragraph": gap_paragraph}
    chunks: List[Chunk] = []
    for index, (chunk_text_value, kind) in enumerate(zip(texts, kinds)):
        is_last = index == len(texts) - 1
        chunks.append(
            Chunk(
                text=chunk_text_value,
                gap_after=0.0 if is_last else gap_map.get(kind, gap_sentence),
                paragraph_end=kind == "paragraph" and not is_last,
            )
        )
    return chunks


def split_for_tts(text: str, max_chars: int = 350) -> List[str]:
    """Just the chunk texts (no pause metadata)."""
    return [c.text for c in chunk_text(text, max_chars=max_chars)]


def _split_overlong_sentence(sentence: str, max_words: int) -> List[str]:
    sub_parts = [p.strip() for p in _SUB_BREAK.split(sentence) if p.strip()]
    if len(sub_parts) > 1:
        chunks: List[str] = []
        buf: List[str] = []
        buf_w = 0
        for sp in sub_parts:
            sp_w = count_words(sp)
            if buf and buf_w + sp_w > max_words:
                chunks.append(_join_sentence_parts(buf))
                buf = []
                buf_w = 0
            if sp_w > max_words:
                if buf:
                    chunks.append(_join_sentence_parts(buf))
                    buf = []
                    buf_w = 0
                chunks.extend(_split_by_tokens_or_chars(sp, max_words))
            else:
                buf.append(sp)
                buf_w += sp_w
        if buf:
            chunks.append(_join_sentence_parts(buf))
        return chunks
    return _split_by_tokens_or_chars(sentence, max_words)


def _split_by_tokens_or_chars(text: str, max_words: int) -> List[str]:
    if _CJK_CHAR_RE.search(text) and " " not in text.strip():
        return [
            text[i:i + max_words].strip()
            for i in range(0, len(text), max_words)
            if text[i:i + max_words].strip()
        ]
    words = _WORD_BREAK.split(text.strip())
    chunks: List[str] = []
    for start in range(0, len(words), max_words):
        piece = " ".join(words[start:start + max_words]).strip()
        if piece:
            chunks.append(piece)
    return chunks


def split_by_words(text: str, min_words: int = 15, max_words: int = 30) -> List[str]:
    """Split text into chunks that each stay within a word-count range.

    Sentences are packed until the buffer reaches ``min_words``; a chunk never
    exceeds ``max_words`` because the buffer is flushed at the sentence
    boundary *before* the limit is crossed (cắt ở dấu chấm gần nhất). A single
    sentence longer than ``max_words`` falls back to sub-clause or token split.
    Works universally across Latin/Vietnamese and non-space scripts (Japanese, Chinese).
    """
    max_words = max(2, int(max_words))
    min_words = max(1, min(int(min_words), max_words - 1))
    text = normalize_text(text)
    if not text:
        return []

    sentences: List[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        if paragraph.strip():
            sentences.extend(split_sentences(paragraph))

    chunks: List[str] = []
    buffer: List[str] = []
    buffer_words = 0

    def flush() -> None:
        nonlocal buffer, buffer_words
        if buffer:
            chunks.append(_join_sentence_parts(buffer))
            buffer = []
            buffer_words = 0

    for sentence in sentences:
        s_words = count_words(sentence)
        if buffer and buffer_words + s_words > max_words:
            flush()
        if s_words > max_words:
            flush()
            chunks.extend(_split_overlong_sentence(sentence, max_words))
            continue
        buffer.append(sentence.strip())
        buffer_words += s_words
        if buffer_words >= min_words:
            flush()
    flush()
    return chunks


def split_by_sentences(text: str) -> List[str]:
    """Split text into segments where each segment is exactly one sentence.

    Supports Latin/Vietnamese punctuation ([.!?]), line breaks, and CJK sentence
    delimiters ([。！？]). Multiple sentences on the same line are split, while
    each distinct line/sentence becomes an individual segment (cứ 1 câu là 1 đoạn).
    """
    text = normalize_text(text)
    if not text:
        return []

    chunks: List[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for line in paragraph.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = _SENTENCE_BREAK.split(line)
            for part in parts:
                clean_p = re.sub(r"\s+", " ", part).strip()
                if clean_p:
                    chunks.append(clean_p)
    return chunks


def text_preview(text: str, limit: int = 140) -> str:
    clean = re.sub(r"\s+", " ", normalize_text(text)).strip()
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def join_scripts(parts: Iterable[str], separator: str = "\n\n") -> str:
    return separator.join(p.strip() for p in parts if p and p.strip())
