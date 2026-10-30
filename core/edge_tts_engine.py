"""edge-tts synthesis for every language but Vietnamese.

H2MTTS only speaks Vietnamese, so items tagged ``en``/``zh``/``ja``… are narrated
by Microsoft Edge's online voices instead.  ``edge_tts`` is imported lazily: the
application still starts (and the Vietnamese pipeline still works) when the
optional dependency is missing.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from core.languages import (
    DEFAULT_LANGUAGE,
    edge_voice,
    language_name,
    normalize_language,
)
from core.text_processor import chunk_text
from core.tts_engine import (
    SynthesisCancelled,
    SynthResult,
    TTSError,
)
from utils.logger import get_logger
from utils.media_probe import probe_duration

logger = get_logger("tts.edge")

ProgressCb = Optional[Callable[[int], None]]
MessageCb = Optional[Callable[[str], None]]
CancelCb = Optional[Callable[[], bool]]
PauseCb = Optional[Callable[[], bool]]

_INSTALL_HINT = (
    "Thiếu thư viện edge-tts. Cài bằng: pip install edge-tts"
)


def _load_edge():
    try:
        import edge_tts  # noqa: F401
    except ImportError as exc:  # optional dependency
        raise TTSError(_INSTALL_HINT) from exc
    return edge_tts


class EdgeTTSEngine:
    """Online TTS (edge-tts) that mirrors :class:`core.tts_engine.TTSEngine`."""

    def __init__(self, settings: Optional[dict] = None) -> None:
        tts = (settings or {}).get("tts") or {}
        self._settings = tts
        raw_map = tts.get("edge_voices") or {}
        self._voice_map: Dict[str, str] = {
            normalize_language(code): str(voice).strip()
            for code, voice in raw_map.items()
            if str(voice).strip()
        }

    def voice_for(self, language: str) -> str:
        code = normalize_language(language)
        return self._voice_map.get(code) or edge_voice(code)

    @staticmethod
    def _rate(speed: Optional[float]) -> str:
        try:
            factor = float(speed)
        except (TypeError, ValueError):
            return "+0%"
        if factor <= 0:
            return "+0%"
        return f"{int(round((factor - 1.0) * 100)):+d}%"

    def _sleep_while_paused(self, pause_cb: PauseCb) -> None:
        while pause_cb and pause_cb():
            time.sleep(0.2)

    def synthesize(
        self,
        text: str,
        out_path: str | os.PathLike,
        language: str = DEFAULT_LANGUAGE,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        progress_cb: ProgressCb = None,
        message_cb: MessageCb = None,
        pause_cb: PauseCb = None,
        cancel_cb: CancelCb = None,
    ) -> SynthResult:
        text = " ".join(str(text or "").split())
        if not text:
            raise TTSError("Văn bản rỗng, không có gì để đọc.")
        edge_tts = _load_edge()
        code = normalize_language(language)
        spoken = (voice or self.voice_for(code)).strip()
        chunks = [chunk.text for chunk in chunk_text(
            text, min_chars=300, max_chars=1200) if chunk.text.strip()]
        if not chunks:
            raise TTSError("Văn bản rỗng sau khi chia đoạn.")
        if message_cb:
            message_cb(
                f"edge-tts ({language_name(code)}): {len(chunks)} đoạn, giọng {spoken}"
            )

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.stem}.edge.tmp{target.suffix or '.mp3'}")
        rate = self._rate(speed)
        total_chars = max(1, sum(len(chunk) for chunk in chunks))

        async def run() -> None:
            with open(temp, "wb") as handle:
                done_chars = 0
                for index, chunk in enumerate(chunks):
                    if cancel_cb and cancel_cb():
                        raise SynthesisCancelled("Đã huỷ tạo audio.")
                    self._sleep_while_paused(pause_cb)
                    communicate = edge_tts.Communicate(
                        chunk, spoken, rate=rate)
                    async for event in communicate.stream():
                        if cancel_cb and cancel_cb():
                            raise SynthesisCancelled("Đã huỷ tạo audio.")
                        if event.get("type") == "audio":
                            data = event.get("data") or b""
                            if data:
                                handle.write(data)
                    done_chars += len(chunk)
                    if progress_cb:
                        progress_cb(max(1, int(100 * done_chars / total_chars)))

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(run())
        except SynthesisCancelled:
            temp.unlink(missing_ok=True)
            raise
        except TTSError:
            temp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temp.unlink(missing_ok=True)
            raise TTSError(f"edge-tts lỗi: {exc}") from exc
        finally:
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)

        if not temp.is_file() or temp.stat().st_size == 0:
            temp.unlink(missing_ok=True)
            raise TTSError("edge-tts không trả về audio (kiểm tra mạng).")
        os.replace(temp, target)

        duration = probe_duration(target)
        if duration <= 0:
            duration = target.stat().st_size / 6000.0  # edge-tts: 48 kbps mp3
        return SynthResult(
            path=target,
            duration=duration,
            sample_rate=24000,
            engine="edge-tts",
            backend="online",
            voice=spoken,
            chunks=len(chunks),
            characters=len(text),
        )

    def close(self) -> None:
        """Nothing to release: every request opens its own connection."""
