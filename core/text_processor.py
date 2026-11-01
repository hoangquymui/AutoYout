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
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_SUB_BREAK = re.compile(r"(?<=[,;:])\s+")
_WORD_BREAK = re.compile(r"\s+")


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


def split_by_words(text: str, min_words: int = 15, max_words: int = 30) -> List[str]:
    """Split text into chunks that each stay within a word-count range.

    Sentences are packed until the buffer reaches ``min_words``; a chunk never
    exceeds ``max_words`` because the buffer is flushed at the sentence
    boundary *before* the limit is crossed (cắt ở dấu chấm gần nhất). A single
    sentence longer than ``max_words`` falls back to a plain word split.
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
            chunks.append(" ".join(buffer))
            buffer = []
            buffer_words = 0

    for sentence in sentences:
        words = _WORD_BREAK.split(sentence.strip())
        count = len(words)
        if buffer and buffer_words + count > max_words:
            flush()
        if count > max_words:
            flush()
            for start in range(0, count, max_words):
                piece = " ".join(words[start:start + max_words]).strip()
                if piece:
                    chunks.append(piece)
            continue
        buffer.append(sentence.strip())
        buffer_words += count
        if buffer_words >= min_words:
            flush()
    flush()
    return chunks


def text_preview(text: str, limit: int = 140) -> str:
    clean = re.sub(r"\s+", " ", normalize_text(text)).strip()
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def join_scripts(parts: Iterable[str], separator: str = "\n\n") -> str:
    return separator.join(p.strip() for p in parts if p and p.strip())
