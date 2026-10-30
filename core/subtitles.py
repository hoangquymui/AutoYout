"""SRT/VTT parsing and cue timing shared by the script and dubbing tabs.

Everything here is Qt-free and dependency-free: :func:`parse_srt` and
:func:`parse_vtt` tolerate the messy output of YouTube auto-captions, and
:func:`text_to_cues` gives plain ``.txt`` imports a usable timeline.
"""
from __future__ import annotations

import html
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.text_processor import normalize_text, split_for_tts
from utils.logger import get_logger

logger = get_logger("subtitles")

_TAG = re.compile(r"<[^>]*>")
_ASS_TAG = re.compile(r"\{\\[^}]*\}")
_DIGITS = re.compile(r"^\d+$")


@dataclass
class Cue:
    """One subtitle line with its timeline position (seconds)."""

    index: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], index: int = 0) -> "Cue":
        try:
            start = float(data.get("start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(data.get("end") or 0.0)
        except (TypeError, ValueError):
            end = start
        if end < start:
            start, end = end, start
        return cls(index, max(0.0, start), max(0.0, end),
                   str(data.get("text") or "").strip())


def _prepare(text: str) -> str:
    raw = str(text or "").replace("\ufeff", "")
    return raw.replace("\r\n", "\n").replace("\r", "\n").strip()


def _blocks(raw: str) -> List[str]:
    return [block for block in re.split(r"\n\s*\n", raw) if block.strip()]


def _clean_text(text: str) -> str:
    value = html.unescape(str(text or ""))
    value = _ASS_TAG.sub("", value)
    value = _TAG.sub("", value)
    return " ".join(value.split()).strip()


def format_timestamp(seconds: float, sep: str = ",") -> str:
    """``3672.5`` → ``01:01:12,500`` (SRT default; pass ``sep="."`` for VTT)."""
    total_ms = max(0, int(round(float(seconds or 0.0) * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def parse_timestamp(value: str) -> float:
    """Parse ``HH:MM:SS,mmm``, ``MM:SS.mmm`` or plain seconds into seconds."""
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return 0.0
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 0.0
    parts = raw.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return 0.0
    if len(numbers) >= 3:
        hours, minutes, seconds = numbers[-3], numbers[-2], numbers[-1]
    elif len(numbers) == 2:
        hours, minutes, seconds = 0.0, numbers[0], numbers[1]
    elif numbers:
        hours, minutes, seconds = 0.0, 0.0, numbers[0]
    else:
        return 0.0
    return max(0.0, hours * 3600.0 + minutes * 60.0 + seconds)


def _parse_range(line: str) -> Optional[Tuple[float, float]]:
    if "-->" not in line:
        return None
    left, right = line.split("-->", 1)
    start_raw = left.strip().split(" ")[0] if left.strip() else ""
    right = right.strip()
    end_raw = right.split(" ")[0] if right else ""
    return parse_timestamp(start_raw), parse_timestamp(end_raw)


def _cue_from_block(block: str, index: int) -> Optional[Cue]:
    lines = block.split("\n")
    ts_index = next((pos for pos, line in enumerate(lines) if "-->" in line), None)
    if ts_index is None:
        return None
    parsed = _parse_range(lines[ts_index])
    if parsed is None:
        return None
    start, end = parsed
    if end < start:
        start, end = end, start
    body = _clean_text(" ".join(lines[ts_index + 1:]))
    if not body:
        return None
    number = index
    if ts_index >= 1:
        head = lines[ts_index - 1].strip()
        if _DIGITS.match(head):
            number = int(head)
    return Cue(number, start, end, body)


def parse_srt(text: str) -> List[Cue]:
    """Parse SRT text, skipping malformed blocks instead of failing."""
    raw = _prepare(text)
    cues: List[Cue] = []
    for block in _blocks(raw):
        try:
            cue = _cue_from_block(block, len(cues) + 1)
        except Exception as exc:  # a broken cue must not drop the whole file
            logger.warning("Bỏ qua cue SRT lỗi: %s", exc)
            continue
        if cue is not None:
            cues.append(cue)
    return cues


def parse_vtt(text: str) -> List[Cue]:
    """Parse WebVTT, dropping headers/notes and merging repeated auto-cue lines."""
    raw = _prepare(text)
    cues: List[Cue] = []
    for block in _blocks(raw):
        first = block.split("\n", 1)[0].strip()
        if first.startswith(("WEBVTT", "NOTE", "STYLE", "REGION", "Kind:",
                              "Language:", "X-TIMESTAMP")):
            continue
        try:
            cue = _cue_from_block(block, len(cues) + 1)
        except Exception as exc:
            logger.warning("Bỏ qua cue VTT lỗi: %s", exc)
            continue
        if cue is None:
            continue
        previous = cues[-1] if cues else None
        if (
            previous is not None
            and cue.text == previous.text
            and cue.start <= previous.end + 0.75
        ):
            previous.end = max(previous.end, cue.end)
            continue
        cues.append(cue)
    return cues


def write_srt(cues: Iterable[Cue], path: str | os.PathLike) -> Path:
    """Write cues as SRT (atomic replace so a crash never leaves a stub)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blocks: List[str] = []
    for number, cue in enumerate(cues, 1):
        blocks.append(
            f"{number}\n"
            f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
            f"{cue.text.strip() or ' '}\n"
        )
    payload = "\n".join(blocks)
    fd, temp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass
    return target


def cues_to_text(cues: Iterable[Cue]) -> str:
    return "\n".join(cue.text.strip() for cue in cues if cue.text.strip())


def text_to_cues(text: str, total_seconds: float = 0.0) -> List[Cue]:
    """Build a timeline from plain text, distributing time by character count."""
    cleaned = normalize_text(text)
    if not cleaned:
        return []
    pieces: List[str] = []
    for paragraph in re.split(r"\n\s*\n", cleaned):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        pieces.extend(piece for piece in split_for_tts(paragraph, max_chars=220)
                      if piece.strip())
    if not pieces:
        return []
    weights = [max(1, len(piece)) for piece in pieces]
    total_chars = float(sum(weights))
    try:
        seconds = float(total_seconds or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    rate = (seconds / total_chars) if seconds > 0 else 0.065
    cues: List[Cue] = []
    cursor = 0.0
    for position, (piece, weight) in enumerate(zip(pieces, weights), 1):
        duration = max(0.6, weight * rate)
        cues.append(Cue(position, cursor, cursor + duration, piece))
        cursor += duration
    return cues


def cues_from_dicts(data: Iterable[Dict[str, Any]]) -> List[Cue]:
    return [Cue.from_dict(entry, index) for index, entry in enumerate(data or [], 1)]


def cues_to_dicts(cues: Iterable[Cue]) -> List[Dict[str, Any]]:
    return [cue.to_dict() for cue in cues]
