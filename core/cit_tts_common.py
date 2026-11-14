"""Shared helpers for CIT voice engines (Supertonic 3 & Kokoro-82M):
HuggingFace snapshot discovery/download, pause/cancel handling, waveform -> MP3/WAV encoding + SRT cues.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from core.settings import ROOT
from core.tts_engine import SynthesisCancelled, TTSError
from utils.media_probe import probe_duration

ProgressCb = Optional[Callable[[int], None]]
MessageCb = Optional[Callable[[str], None]]
CancelCb = Optional[Callable[[], bool]]
PauseCb = Optional[Callable[[], bool]]

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def hf_cache_roots() -> List[Path]:
    """Return deduplicated list of existing HuggingFace hub cache roots, prioritizing local CIT cache if present."""
    candidates: List[Path] = []
    # 1. Local sibling cit-voice-studio cache (pre-downloaded models on this machine)
    candidates.append(ROOT.parent / "tts" / "CIT" / "cit-voice-studio" / "models" / "hf_cache" / "hub")
    candidates.append(Path("d:/mui/f375/tts/CIT/cit-voice-studio/models/hf_cache/hub"))
    # 2. Project-local hf cache
    candidates.append(ROOT / "models" / "hf_cache" / "hub")
    # 3. Environment HF caches
    env_hub = os.environ.get("HF_HUB_CACHE", "").strip()
    if env_hub:
        candidates.append(Path(env_hub))
    env_home = os.environ.get("HF_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home) / "hub")
    # 4. Default user HF cache
    candidates.append(Path.home() / ".cache" / "huggingface" / "hub")

    seen: set[str] = set()
    out: List[Path] = []
    for p in candidates:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            out.append(p)
    return out


def _has_required_files(base: Path, required_relpaths: Sequence[str]) -> bool:
    if not base.is_dir():
        return False
    for rel in required_relpaths:
        if not (base / rel).is_file():
            return False
    return True


def find_model_root(
    repo_id: str,
    required_relpaths: Sequence[str],
    preferred_rev: str = "",
    extra_dirs: Sequence[Path | str | None] = (),
) -> Optional[Path]:
    """Locate a complete model directory either directly in ``extra_dirs`` or in HF hub caches."""
    repo_folder = "models--" + repo_id.replace("/", "--")

    # 1. Check explicit / managed directories directly (or snapshots nested inside them)
    for raw in extra_dirs:
        if not raw:
            continue
        p = Path(raw)
        if _has_required_files(p, required_relpaths):
            return p
        for nested_base in (
            p / repo_folder / "snapshots",
            p / "hf_cache" / "hub" / repo_folder / "snapshots",
            p / "snapshots",
        ):
            if nested_base.is_dir():
                if preferred_rev and _has_required_files(nested_base / preferred_rev, required_relpaths):
                    return nested_base / preferred_rev
                for snap in sorted(nested_base.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                    if _has_required_files(snap, required_relpaths):
                        return snap

    # 2. Check HF hub caches (e.g. cit-voice-studio or ~/.cache/huggingface/hub)
    for hub in hf_cache_roots():
        snaps_dir = hub / repo_folder / "snapshots"
        if not snaps_dir.is_dir():
            continue
        if preferred_rev and _has_required_files(snaps_dir / preferred_rev, required_relpaths):
            return snaps_dir / preferred_rev
        try:
            snaps = sorted(snaps_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
        except OSError:
            snaps = []
        for snap in snaps:
            if _has_required_files(snap, required_relpaths):
                return snap
    return None


def wait_pause_cancel(pause_cb: PauseCb, cancel_cb: CancelCb) -> None:
    while pause_cb is not None and pause_cb():
        if cancel_cb is not None and cancel_cb():
            raise SynthesisCancelled("Đã dừng tạo audio.")
        time.sleep(0.15)
    if cancel_cb is not None and cancel_cb():
        raise SynthesisCancelled("Đã dừng tạo audio.")


def atempo_chain(speed: float) -> str:
    factor = max(0.25, min(4.0, float(speed)))
    parts: List[str] = []
    while factor > 2.0:
        parts.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    if abs(factor - 1.0) > 1e-4:
        parts.append(f"atempo={factor:.6f}")
    return ",".join(parts)


def encode_wav_to_audio(
    wav_path: Path,
    out_path: Path,
    ffmpeg: str,
    tempo: float = 1.0,
    bitrate: str = "192k",
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = out_path.suffix.lower()
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav_path)]
    if abs(tempo - 1.0) > 1e-4:
        chain = atempo_chain(tempo)
        if chain:
            args += ["-filter:a", chain]
    if suffix == ".wav":
        args += ["-codec:a", "pcm_s16le"]
    else:
        args += ["-codec:a", "libmp3lame", "-b:a", str(bitrate or "192k"), "-ar", "44100"]
    args += ["-vn", str(out_path)]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise TTSError(f"Không tìm thấy ffmpeg ({ffmpeg}). Cấu hình lại đường dẫn ffmpeg.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TTSError("ffmpeg mã hoá audio quá lâu, đã huỷ.") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-4:]
        raise TTSError("ffmpeg mã hoá audio lỗi: " + " | ".join(tail))


def finalize_waveforms(
    chunks: Sequence,
    wavs: Sequence[np.ndarray],
    sample_rate: int,
    out_path: Path,
    ffmpeg: str,
    ffprobe: str,
    bitrate: str,
    tempo: float,
    logger,
    progress_cb: ProgressCb = None,
    pause_cb: PauseCb = None,
    cancel_cb: CancelCb = None,
) -> Tuple[Path, float, List[Tuple[float, float, str]]]:
    """Assemble waveform chunks with sentence/paragraph gaps, write WAV/MP3 & synced SRT, return (path, duration, cues)."""
    if not wavs:
        raise TTSError("Không tạo được waveform nào.")

    pieces: List[np.ndarray] = []
    cues_list: List[Tuple[float, float, str]] = []
    cursor_time = 0.0
    effective_tempo = max(0.1, float(tempo))

    for index, wav in enumerate(wavs):
        arr = np.asarray(wav, dtype=np.float32).reshape(-1)
        pieces.append(arr)
        wav_dur = (len(arr) / float(sample_rate)) / effective_tempo
        chunk_txt = str(getattr(chunks[index], "text", "") or "").strip()
        if chunk_txt and wav_dur > 0:
            cues_list.append((cursor_time, cursor_time + wav_dur, chunk_txt))
        cursor_time += wav_dur
        gap = float(getattr(chunks[index], "gap_after", 0.0) or 0.0) if index < len(chunks) else 0.0
        if gap > 0:
            pieces.append(np.zeros(max(1, int(round(gap * sample_rate))), dtype=np.float32))
            cursor_time += gap

    audio = np.concatenate(pieces).astype(np.float32, copy=False)
    if audio.size < max(1, sample_rate // 10):
        raise TTSError(f"Audio tạo ra quá ngắn ({audio.size} mẫu).")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="autoyout_cit_", suffix=".wav", dir=str(out_path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    part_path = out_path.with_name(f"{out_path.stem}.part{out_path.suffix}")
    try:
        try:
            import soundfile as sf

            sf.write(str(temp_path), np.clip(audio, -1.0, 1.0), int(sample_rate))
        except Exception as exc:
            raise TTSError(f"Không ghi được WAV tạm: {exc}") from exc
        wait_pause_cancel(pause_cb, cancel_cb)
        encode_wav_to_audio(temp_path, part_path, ffmpeg=ffmpeg, tempo=float(tempo), bitrate=bitrate)
        if not part_path.is_file() or part_path.stat().st_size < 512:
            raise TTSError("File âm thanh xuất ra không hợp lệ.")
        os.replace(part_path, out_path)
    finally:
        for leftover in (temp_path, part_path):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass

    if not out_path.is_file() or out_path.stat().st_size < 512:
        raise TTSError(f"File âm thanh không hợp lệ: {out_path}")

    duration = probe_duration(out_path, ffprobe)
    if duration <= 0:
        duration = len(audio) / float(sample_rate) / max(1e-3, effective_tempo)

    if cues_list:
        try:
            from core.subtitles import Cue, write_srt

            sub_cues = [Cue(idx, s, e, t) for idx, (s, e, t) in enumerate(cues_list, 1)]
            write_srt(sub_cues, out_path.with_suffix(".srt"))
            logger.info("Đã tự động tạo SRT đồng bộ cho %s (%d câu)", out_path.name, len(sub_cues))
        except Exception as exc:
            logger.warning("Không tạo được SRT tự động: %s", exc)

    if progress_cb:
        try:
            progress_cb(100)
        except Exception:
            pass
    logger.info("Đã tạo %s (%.2fs)", out_path.name, duration)
    return out_path, duration, cues_list
