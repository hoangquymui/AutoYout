"""Local Piper TTS engine (ONNX Runtime C++ binary / Python piper-tts) for multi-language synthesis."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.languages import DEFAULT_LANGUAGE, language_name, normalize_language
from core.settings import default_piper_path, normalize_piper_path
from core.text_processor import chunk_text
from core.tts_engine import SynthesisCancelled, SynthResult, TTSError
from utils.logger import get_logger
from utils.media_probe import probe_duration, resolve_tool

logger = get_logger("tts.piper")

ProgressCb = Optional[Callable[[int], None]]
MessageCb = Optional[Callable[[str], None]]
CancelCb = Optional[Callable[[], bool]]
PauseCb = Optional[Callable[[], bool]]

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Default curated Piper ONNX voice models per language
PIPER_DEFAULT_VOICES: Dict[str, str] = {
    "vi": "vi_VN-vais1000-medium",
    "en": "en_US-lessac-medium",
    "zh": "zh_CN-huayan-medium",
    "fr": "fr_FR-upmc-medium",
    "es": "es_ES-davefx-medium",
    "de": "de_DE-thorsten-medium",
    "ru": "ru_RU-irina-medium",
    "ja": "en_US-lessac-medium",
    "ko": "en_US-lessac-medium",
    "th": "en_US-lessac-medium",
    "id": "en_US-lessac-medium",
    "hi": "en_US-lessac-medium",
}

PIPER_VOICES_BY_LANG: Dict[str, List[Tuple[str, str]]] = {
    "vi": [
        ("VAIS1000 Medium (Chuẩn phòng thu · Khuyên dùng)", "vi_VN-vais1000-medium"),
        ("25Hours Single Low (Giọng nữ miền Nam · Siêu nhanh)", "vi_VN-25hours_single-low"),
        ("Vivos X Low (Đa vùng miền · Nhẹ)", "vi_VN-vivos-x_low"),
    ],
    "en": [
        ("Lessac Medium (Giọng Mỹ chuẩn · Khuyên dùng)", "en_US-lessac-medium"),
        ("Amy Medium (Giọng nữ Mỹ ấm áp)", "en_US-amy-medium"),
        ("Ryan High (Giọng nam Mỹ chất lượng cao)", "en_US-ryan-high"),
        ("Alba Medium (Giọng Anh Quốc)", "en_GB-alba-medium"),
    ],
    "zh": [
        ("Huayan Medium (Tiếng Trung phổ thông)", "zh_CN-huayan-medium"),
    ],
    "fr": [
        ("UPMC Medium (Tiếng Pháp chuẩn)", "fr_FR-upmc-medium"),
        ("Siwis Medium (Giọng nữ Pháp)", "fr_FR-siwis-medium"),
    ],
    "es": [
        ("Davefx Medium (Tiếng Tây Ban Nha)", "es_ES-davefx-medium"),
        ("Sharvard Medium (Đa giọng Tây Ban Nha)", "es_ES-sharvard-medium"),
    ],
    "de": [
        ("Thorsten Medium (Tiếng Đức chuẩn)", "de_DE-thorsten-medium"),
    ],
    "ru": [
        ("Irina Medium (Giọng nữ Nga)", "ru_RU-irina-medium"),
        ("Denis Medium (Giọng nam Nga)", "ru_RU-denis-medium"),
    ],
}

EDGE_VOICES_BY_LANG: Dict[str, List[Tuple[str, str]]] = {
    "vi": [
        ("Hoài My — Nữ (vi-VN-HoaiMyNeural)", "vi-VN-HoaiMyNeural"),
        ("Nam Minh — Nam (vi-VN-NamMinhNeural)", "vi-VN-NamMinhNeural"),
    ],
    "en": [
        ("Aria — Nữ Mỹ (en-US-AriaNeural)", "en-US-AriaNeural"),
        ("Jenny — Nữ Mỹ (en-US-JennyNeural)", "en-US-JennyNeural"),
        ("Guy — Nam Mỹ (en-US-GuyNeural)", "en-US-GuyNeural"),
        ("Christopher — Nam Mỹ (en-US-ChristopherNeural)", "en-US-ChristopherNeural"),
        ("Sonia — Nữ Anh (en-GB-SoniaNeural)", "en-GB-SoniaNeural"),
    ],
    "zh": [
        ("Xiaoxiao — Nữ (zh-CN-XiaoxiaoNeural)", "zh-CN-XiaoxiaoNeural"),
        ("Yunxi — Nam (zh-CN-YunxiNeural)", "zh-CN-YunxiNeural"),
        ("Yunjian — Nam Kể chuyện (zh-CN-YunjianNeural)", "zh-CN-YunjianNeural"),
    ],
    "ja": [
        ("Nanami — Nữ (ja-JP-NanamiNeural)", "ja-JP-NanamiNeural"),
        ("Keita — Nam (ja-JP-KeitaNeural)", "ja-JP-KeitaNeural"),
    ],
    "ko": [
        ("SunHi — Nữ (ko-KR-SunHiNeural)", "ko-KR-SunHiNeural"),
        ("InJoon — Nam (ko-KR-InJoonNeural)", "ko-KR-InJoonNeural"),
    ],
    "fr": [
        ("Denise — Nữ (fr-FR-DeniseNeural)", "fr-FR-DeniseNeural"),
        ("Henri — Nam (fr-FR-HenriNeural)", "fr-FR-HenriNeural"),
    ],
    "es": [
        ("Elvira — Nữ (es-ES-ElviraNeural)", "es-ES-ElviraNeural"),
        ("Alvaro — Nam (es-ES-AlvaroNeural)", "es-ES-AlvaroNeural"),
    ],
    "th": [
        ("Premwadee — Nữ (th-TH-PremwadeeNeural)", "th-TH-PremwadeeNeural"),
        ("Niwat — Nam (th-TH-NiwatNeural)", "th-TH-NiwatNeural"),
    ],
    "id": [
        ("Gadis — Nữ (id-ID-GadisNeural)", "id-ID-GadisNeural"),
        ("Ardi — Nam (id-ID-ArdiNeural)", "id-ID-ArdiNeural"),
    ],
    "hi": [
        ("Swara — Nữ (hi-IN-SwaraNeural)", "hi-IN-SwaraNeural"),
        ("Madhur — Nam (hi-IN-MadhurNeural)", "hi-IN-MadhurNeural"),
    ],
}

H2M_DEFAULT_VOICES: List[Tuple[str, str]] = [
    ("Hải Đăng · Bắc (Nam)", "Hải Đăng"),
    ("Ngọc Huyền · Bắc (Nữ)", "Ngọc Huyền"),
    ("Bình Minh · Bắc (Nam)", "Bình Minh"),
    ("Tuyên Quang · Bắc (Nam)", "Tuyên Quang"),
    ("Thanh Hà · Bắc (Nữ)", "Thanh Hà"),
    ("Minh Khang · Nam (Nam)", "Minh Khang"),
    ("Thảo Vy · Nam (Nữ)", "Thảo Vy"),
    ("Hoàng Dũng · Trung (Nam)", "Hoàng Dũng"),
]


def resolve_tts_engine_for_language(settings: Optional[Dict[str, Any]], language: str) -> str:
    """Return the configured TTS engine ID ('h2mtts', 'edge', or 'piper') for a language."""
    code = normalize_language(language)
    cfg = settings if isinstance(settings, dict) else {}
    tts_cfg = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    lang_engines = tts_cfg.get("language_engines") if isinstance(tts_cfg.get("language_engines"), dict) else {}
    chosen = str(lang_engines.get(code) or "").strip().lower()
    if chosen in ("h2mtts", "h2m", "local"):
        return "h2mtts"
    if chosen in ("piper", "pipertts"):
        return "piper"
    if chosen in ("edge", "edgetts", "edge_tts"):
        return "edge"
    # Default: Vietnamese uses h2mtts, other languages use edge
    return "h2mtts" if code == "vi" else "edge"


def resolve_voice_for_language_engine(
    settings: Optional[Dict[str, Any]], language: str, engine_id: str
) -> str:
    """Return the configured voice/model name for a (language, engine_id) pair."""
    code = normalize_language(language)
    cfg = settings if isinstance(settings, dict) else {}
    tts_cfg = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    if engine_id == "h2mtts":
        lang_voices = tts_cfg.get("h2m_voices") if isinstance(tts_cfg.get("h2m_voices"), dict) else {}
        return str(lang_voices.get(code) or tts_cfg.get("voice") or "Hải Đăng").strip()
    if engine_id == "piper":
        piper_voices = tts_cfg.get("piper_voices") if isinstance(tts_cfg.get("piper_voices"), dict) else {}
        return str(
            piper_voices.get(code)
            or PIPER_DEFAULT_VOICES.get(code)
            or "vi_VN-vais1000-medium"
        ).strip()
    # edge
    edge_voices = tts_cfg.get("edge_voices") if isinstance(tts_cfg.get("edge_voices"), dict) else {}
    from core.languages import edge_voice

    return str(edge_voices.get(code) or edge_voice(code)).strip()


class PiperTTSEngine:
    """Offline Piper TTS engine matching TTSEngine and EdgeTTSEngine interfaces."""

    def __init__(self, settings: Optional[Dict[str, Any] | str | Path] = None) -> None:
        if isinstance(settings, (str, Path)):
            self.settings: Dict[str, Any] = {}
            raw_piper = str(settings).strip()
            paths: Dict[str, Any] = {"piper": raw_piper}
            tts: Dict[str, Any] = {}
        elif isinstance(settings, dict):
            self.settings = settings
            paths = self.settings.get("paths") if isinstance(self.settings.get("paths"), dict) else {}
            tts = self.settings.get("tts") if isinstance(self.settings.get("tts"), dict) else {}
        else:
            self.settings = {}
            paths = {}
            tts = {}
        self.root = Path(normalize_piper_path(paths.get("piper") or default_piper_path()))
        self.ffmpeg = resolve_tool(str(paths.get("ffmpeg") or "ffmpeg"), "ffmpeg")
        self.bitrate = str(tts.get("bitrate") or "192k")
        try:
            self.default_speed = float(tts.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.default_speed = 1.0
        raw_map = tts.get("piper_voices") if isinstance(tts.get("piper_voices"), dict) else {}
        self._voice_map: Dict[str, str] = {
            normalize_language(k): str(v).strip()
            for k, v in raw_map.items()
            if str(v).strip()
        }

    def close(self) -> None:
        pass

    def find_binary(self) -> Optional[Path]:
        """Locate piper executable inside PiperTTS directory or system PATH."""
        candidates = [
            self.root / "runtime" / "piper" / "piper.exe",
            self.root / "runtime" / "piper.exe",
            self.root / "piper" / "piper.exe",
            self.root / "piper.exe",
            self.root / "runtime" / "piper" / "piper",
            self.root / "piper" / "piper",
        ]
        for cand in candidates:
            if cand.is_file():
                return cand
        which = shutil.which("piper")
        if which:
            return Path(which)
        return None

    def find_model(self, voice_or_model: str, language: str = "vi") -> Optional[Path]:
        """Locate the .onnx voice file in PiperTTS directory."""
        name = str(voice_or_model or "").strip()
        code = normalize_language(language)
        if not name:
            name = self._voice_map.get(code) or PIPER_DEFAULT_VOICES.get(code, "vi_VN-vais1000-medium")
        if name.endswith(".onnx"):
            stem = name[:-5]
        else:
            stem = name

        direct = Path(name)
        if direct.is_file():
            return direct

        search_dirs = [
            self.root / "voices" / code,
            self.root / "voices",
            self.root / "models" / code,
            self.root / "models",
            self.root,
        ]
        for folder in search_dirs:
            if not folder.is_dir():
                continue
            cand = folder / f"{stem}.onnx"
            if cand.is_file():
                return cand
            for found in folder.rglob(f"{stem}.onnx"):
                if found.is_file():
                    return found
        # Fallback: any .onnx matching language prefix in PiperTTS folder
        if self.root.is_dir():
            for found in self.root.rglob("*.onnx"):
                if found.is_file() and (
                    found.stem.lower().startswith(f"{code}_")
                    or found.stem.lower().startswith(f"{code}-")
                ):
                    return found
        return None

    def installed_voices(self, language: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) actually downloaded on disk for a language."""
        code = normalize_language(language) if language else ""
        if not self.root.is_dir():
            return []
        known_labels = {
            val: label
            for lang_list in PIPER_VOICES_BY_LANG.values()
            for label, val in lang_list
        }
        installed: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for onnx in self.root.rglob("*.onnx"):
            if not onnx.is_file():
                continue
            stem = onnx.stem
            if stem in seen:
                continue
            if code:
                stem_lower = stem.lower()
                in_lang_folder = code in [p.lower() for p in onnx.parts]
                prefix_match = stem_lower.startswith(f"{code}_") or stem_lower.startswith(f"{code}-")
                if not (in_lang_folder or prefix_match):
                    continue
            seen.add(stem)
            label = known_labels.get(stem, f"{stem} (Đã tải)")
            installed.append((label, stem))
        return installed

    def check_ready(self, language: str = "") -> Tuple[bool, str]:
        """Verify whether PiperTTS binary and at least one model for `language` are installed."""
        binary = self.find_binary()
        if binary is None:
            return (
                False,
                f"Chưa cài đặt bộ chạy PiperTTS (piper.exe) trong thư mục '{self.root}'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải PiperTTS.",
            )
        if language:
            code = normalize_language(language)
            models = self.installed_voices(code)
            if not models:
                return (
                    False,
                    f"Chưa tải model giọng đọc PiperTTS cho ngôn ngữ '{language_name(code)} ({code})'. "
                    "Vui lòng vào Cài đặt → API → Giọng đọc để tải model.",
                )
        return True, "PiperTTS đã sẵn sàng."

    def available_voices(self, language: str = "") -> List[Tuple[str, str]]:
        """Return list of (label, model_id) available for a language."""
        installed = self.installed_voices(language)
        if installed:
            return installed
        code = normalize_language(language) if language else ""
        base_list = list(PIPER_VOICES_BY_LANG.get(code, [])) if code else []
        seen = {val for _, val in base_list}
        if not base_list:
            for lang_voices in PIPER_VOICES_BY_LANG.values():
                for label, val in lang_voices:
                    if val not in seen:
                        seen.add(val)
                        base_list.append((label, val))
        return base_list

    def voice_for(self, language: str) -> str:
        code = normalize_language(language)
        return self._voice_map.get(code) or PIPER_DEFAULT_VOICES.get(code, "vi_VN-vais1000-medium")

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
        clean_text = " ".join(str(text or "").split())
        if not clean_text:
            raise TTSError("Văn bản rỗng, không có gì để đọc.")

        code = normalize_language(language)
        chosen_voice = (voice or self.voice_for(code)).strip()
        binary = self.find_binary()
        if binary is None:
            raise TTSError(
                f"Chưa cài đặt PiperTTS (không tìm thấy piper.exe tại '{self.root}'). "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải PiperTTS."
            )
        model_path = self.find_model(chosen_voice, code)
        if model_path is None:
            raise TTSError(
                f"Chưa tải model giọng đọc PiperTTS '{chosen_voice}' cho ngôn ngữ '{language_name(code)} ({code})'. "
                "Vui lòng vào Cài đặt → API → Giọng đọc để tải model."
            )

        chunks = [
            c.text for c in chunk_text(clean_text, min_chars=250, max_chars=900) if c.text.strip()
        ]
        if not chunks:
            raise TTSError("Văn bản rỗng sau khi chia đoạn.")

        if message_cb:
            message_cb(
                f"PiperTTS ({language_name(code)}): {len(chunks)} đoạn, model {model_path.stem}"
            )

        eff_speed = float(speed or self.default_speed or 1.0)
        length_scale = round(1.0 / max(0.25, min(4.0, eff_speed)), 3)

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="ay_piper_", dir=str(target.parent)))

        try:
            wav_parts: List[Path] = []
            for idx, chunk in enumerate(chunks):
                while pause_cb and pause_cb():
                    if cancel_cb and cancel_cb():
                        raise SynthesisCancelled("Đã huỷ tạo audio PiperTTS.")
                    time.sleep(0.2)
                if cancel_cb and cancel_cb():
                    raise SynthesisCancelled("Đã huỷ tạo audio PiperTTS.")

                part_wav = temp_dir / f"part_{idx:04d}.wav"
                cmd = [
                    str(binary),
                    "--model",
                    str(model_path),
                    "--output_file",
                    str(part_wav),
                    "--length_scale",
                    str(length_scale),
                ]
                proc = subprocess.run(
                    cmd,
                    input=chunk.encode("utf-8"),
                    capture_output=True,
                    timeout=120,
                    creationflags=_CREATE_NO_WINDOW,
                )
                if proc.returncode != 0 or not part_wav.is_file() or part_wav.stat().st_size == 0:
                    err = proc.stderr.decode("utf-8", errors="replace")[:300]
                    raise TTSError(f"PiperTTS lỗi tạo audio đoạn {idx + 1}: {err}")
                wav_parts.append(part_wav)
                if progress_cb:
                    progress_cb(int(round(100 * (idx + 1) / len(chunks))))

            if len(wav_parts) == 1 and target.suffix.lower() == ".wav":
                os.replace(wav_parts[0], target)
            else:
                concat_txt = temp_dir / "concat.txt"
                concat_txt.write_text(
                    "\n".join(f"file '{p.name}'" for p in wav_parts),
                    encoding="utf-8",
                )
                cmd_ff = [
                    self.ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_txt),
                ]
                if target.suffix.lower() == ".mp3":
                    cmd_ff.extend(["-c:a", "libmp3lame", "-b:a", self.bitrate])
                cmd_ff.append(str(target))
                res = subprocess.run(
                    cmd_ff,
                    cwd=str(temp_dir),
                    capture_output=True,
                    timeout=180,
                    creationflags=_CREATE_NO_WINDOW,
                )
                if res.returncode != 0 or not target.is_file():
                    err = res.stderr.decode("utf-8", errors="replace")[:300]
                    raise TTSError(f"FFmpeg ghép audio PiperTTS lỗi: {err}")

            dur = probe_duration(target)
            return SynthResult(
                path=target,
                duration=dur,
                chunks=len(chunks),
                voice=model_path.stem,
                engine="piper",
                backend="onnx",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
