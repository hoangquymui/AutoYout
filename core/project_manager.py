"""Project folders, script discovery, metadata pairing and resumable state."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.languages import (
    DEFAULT_LANGUAGE,
    normalize_language,
    resolve_language,
)
from utils.files import (
    ensure_dir,
    load_json,
    natural_key,
    natural_sorted,
    read_text_smart,
    write_json_atomic,
)
from utils.logger import get_logger
from utils.media_probe import format_duration, probe_duration

logger = get_logger("project")

ITEM_PENDING = "PENDING"
ITEM_TTS_PROCESSING = "TTS_PROCESSING"
ITEM_TTS_DONE = "TTS_DONE"
ITEM_VIDEO_PROCESSING = "VIDEO_PROCESSING"
ITEM_VIDEO_DONE = "VIDEO_DONE"
ITEM_UPLOADING = "UPLOADING"
ITEM_UPLOAD_DONE = "UPLOAD_DONE"
ITEM_ERROR = "ERROR"

ALL_STATES = [
    ITEM_PENDING,
    ITEM_TTS_PROCESSING,
    ITEM_TTS_DONE,
    ITEM_VIDEO_PROCESSING,
    ITEM_VIDEO_DONE,
    ITEM_UPLOADING,
    ITEM_UPLOAD_DONE,
    ITEM_ERROR,
]

VIDEO_PENDING = "PENDING"
VIDEO_PROCESSING = "VIDEO_PROCESSING"
VIDEO_DONE = "VIDEO_DONE"
VIDEO_UPLOADING = "UPLOADING"
VIDEO_UPLOAD_DONE = "UPLOAD_DONE"
VIDEO_ERROR = "ERROR"

STATE_LABELS_VI = {
    ITEM_PENDING: "Chờ xử lý",
    ITEM_TTS_PROCESSING: "Đang tạo audio",
    ITEM_TTS_DONE: "Đã có audio",
    ITEM_VIDEO_PROCESSING: "Đang tạo video",
    ITEM_VIDEO_DONE: "Đã có video",
    ITEM_UPLOADING: "Đang upload",
    ITEM_UPLOAD_DONE: "Đã đăng YouTube",
    ITEM_ERROR: "Lỗi",
}

VIDEO_STATE_LABELS_VI = {
    VIDEO_PENDING: "Chờ tạo video",
    VIDEO_PROCESSING: "Đang tạo video",
    VIDEO_DONE: "Đã có video",
    VIDEO_UPLOADING: "Đang upload",
    VIDEO_UPLOAD_DONE: "Đã đăng YouTube",
    VIDEO_ERROR: "Lỗi video",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for entry in value:
            if isinstance(entry, str):
                if entry.strip():
                    out.append(entry.strip())
            elif isinstance(entry, dict):
                text = entry.get("title") or entry.get("name")
                if text:
                    out.append(str(text).strip())
        return out
    return [str(value)]


def _path_list(value: Any) -> List[str]:
    """Normalize a take list (candidate image/audio paths) from state JSON."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(entry).strip() for entry in value if str(entry).strip()]
    return [str(value)] if str(value).strip() else []


def _clamp_index(value: Any, length: int) -> int:
    """Coerce a selected take index to a valid range for a list of ``length``."""
    try:
        index = int(value)
    except (TypeError, ValueError):
        index = 0
    if length <= 0:
        return 0
    return max(0, min(index, length - 1))


def normalize_metadata(raw: Any, json_path: Optional[Path] = None) -> Dict[str, Any]:
    """Accept the documented metadata schema and make it predictable."""
    if not isinstance(raw, dict):
        if isinstance(raw, list):
            raw = {"chapters": raw}
        else:
            raw = {}
    meta: Dict[str, Any] = dict(raw)
    meta["title"] = str(raw.get("title") or raw.get("name") or "").strip()
    meta["description"] = str(raw.get("description") or raw.get("desc") or "").strip()
    meta["tags"] = _string_list(raw.get("tags") or raw.get("keywords"))
    meta["chapters"] = raw.get("chapters") or raw.get("chapter") or []
    meta["publish_at"] = str(raw.get("publish_at") or raw.get("publishAt") or "").strip()
    meta["text_content"] = str(raw.get("text_content") or raw.get("content") or "")
    thumb = raw.get("thumbnail_path") or raw.get("thumbnail")
    if thumb:
        candidate = Path(str(thumb))
        if not candidate.is_absolute() and json_path is not None:
            candidate = json_path.parent / candidate
        if not candidate.is_file() and json_path is not None:
            alt = json_path.parent.parent / "assets" / str(thumb)
            if alt.is_file():
                candidate = alt
        meta["thumbnail_path"] = str(candidate) if candidate.is_file() else ""
    else:
        meta["thumbnail_path"] = ""
    meta["made_for_kids"] = bool(raw.get("made_for_kids", raw.get("madeForKids", False)))
    meta["category_id"] = str(raw.get("category_id") or raw.get("categoryId") or "").strip()
    meta["privacy"] = str(raw.get("privacy") or raw.get("privacyStatus") or "").strip().lower()
    meta["playlist_id"] = str(raw.get("playlist_id") or raw.get("playlistId") or "").strip()
    return meta


@dataclass
class ProjectItem:
    base: str
    text_file: Optional[Path]
    json_file: Optional[Path]
    text: str
    text_hash: str
    metadata: Dict[str, Any]
    audio_path: Path
    duration: float = 0.0
    audio_mtime: float = 0.0
    state: str = ITEM_PENDING
    error: str = ""
    video_id: str = ""
    updated_at: str = field(default_factory=_now)
    metadata_error: str = ""
    image_takes: List[str] = field(default_factory=list)
    selected_image_index: int = 0
    voice_takes: List[str] = field(default_factory=list)
    selected_voice_index: int = 0
    language: str = DEFAULT_LANGUAGE
    dub_mode: str = "text"
    cues_path: Optional[Path] = None

    @property
    def display_name(self) -> str:
        return str(self.metadata.get("title") or self.base)

    @property
    def state_label(self) -> str:
        return STATE_LABELS_VI.get(self.state, self.state)

    @property
    def duration_label(self) -> str:
        return format_duration(self.duration) if self.duration else "—"

    def select_take(self, kind: str, index: int) -> bool:
        """Switch the selected take for ``kind`` ("image"/"voice"); sync the primary path."""
        kind = str(kind or "").strip().lower()
        if kind == "image":
            self.selected_image_index = _clamp_index(index, len(self.image_takes))
            return bool(self.image_takes)
        if kind == "voice":
            self.selected_voice_index = _clamp_index(index, len(self.voice_takes))
            if self.voice_takes:
                self.audio_path = Path(self.voice_takes[self.selected_voice_index])
            return bool(self.voice_takes)
        return False

    def to_state(self) -> Dict[str, Any]:
        return {
            "base": self.base,
            "text_file": str(self.text_file) if self.text_file else "",
            "json_file": str(self.json_file) if self.json_file else "",
            "text_hash": self.text_hash,
            "audio_path": str(self.audio_path),
            "duration": round(float(self.duration), 3),
            "audio_mtime": float(self.audio_mtime),
            "state": self.state,
            "error": self.error,
            "video_id": self.video_id,
            "metadata_error": self.metadata_error,
            "updated_at": self.updated_at,
            "image_takes": list(self.image_takes),
            "selected_image_index": int(self.selected_image_index),
            "voice_takes": list(self.voice_takes),
            "selected_voice_index": int(self.selected_voice_index),
            "language": self.language,
            "dub_mode": self.dub_mode,
            "cues_path": str(self.cues_path) if self.cues_path else "",
        }


@dataclass
class VideoJob:
    id: str
    path: Path
    items: List[str]
    title: str = ""
    state: str = VIDEO_PENDING
    duration: float = 0.0
    timestamps: List[List[Any]] = field(default_factory=list)
    timestamps_text: str = ""
    error: str = ""
    youtube_video_id: str = ""
    youtube_url: str = ""
    scheduled_at: str = ""
    rendered_at: str = ""
    uploaded_at: str = ""
    audio_mtime: float = 0.0
    language: str = ""

    @property
    def state_label(self) -> str:
        return VIDEO_STATE_LABELS_VI.get(self.state, self.state)

    def to_state(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path),
            "items": list(self.items),
            "title": self.title,
            "state": self.state,
            "duration": round(float(self.duration), 3),
            "timestamps": self.timestamps,
            "timestamps_text": self.timestamps_text,
            "error": self.error,
            "youtube_video_id": self.youtube_video_id,
            "youtube_url": self.youtube_url,
            "scheduled_at": self.scheduled_at,
            "rendered_at": self.rendered_at,
            "uploaded_at": self.uploaded_at,
            "audio_mtime": self.audio_mtime,
            "language": self.language,
        }

    @classmethod
    def from_state(cls, data: Dict[str, Any]) -> "VideoJob":
        return cls(
            id=str(data.get("id") or ""),
            path=Path(data.get("path") or ""),
            items=list(data.get("items") or []),
            title=str(data.get("title") or ""),
            state=str(data.get("state") or VIDEO_PENDING),
            duration=float(data.get("duration") or 0),
            timestamps=list(data.get("timestamps") or []),
            timestamps_text=str(data.get("timestamps_text") or ""),
            error=str(data.get("error") or ""),
            youtube_video_id=str(data.get("youtube_video_id") or ""),
            youtube_url=str(data.get("youtube_url") or ""),
            scheduled_at=str(data.get("scheduled_at") or ""),
            rendered_at=str(data.get("rendered_at") or ""),
            uploaded_at=str(data.get("uploaded_at") or ""),
            audio_mtime=float(data.get("audio_mtime") or 0),
            language=str(data.get("language") or ""),
        )


class ProjectManager:
    """One project folder = one ``project_state.json`` and the standard folders."""

    FOLDER_LABELS = [
        ("script", "SCRIPT"),
        ("audio", "AUDIO OUTPUT"),
        ("video", "VIDEO OUTPUT"),
        ("thumbs", "THUMBNAIL"),
        ("assets", "ASSETS"),
    ]

    def __init__(
        self,
        project_dir: str | os.PathLike,
        audio_dir: Optional[str | os.PathLike] = None,
        video_dir: Optional[str | os.PathLike] = None,
        assets_dir: Optional[str | os.PathLike] = None,
        script_dir: Optional[str | os.PathLike] = None,
        thumbs_dir: Optional[str | os.PathLike] = None,
        default_language: str = DEFAULT_LANGUAGE,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        if not self.project_dir.exists():
            self.project_dir.mkdir(parents=True, exist_ok=True)
        self.default_language = normalize_language(default_language)
        self.created_folders: List[str] = []
        self._lock = threading.RLock()
        self.audio_dir = self._resolve_dir(audio_dir, "audio", "audio")
        self.video_dir = self._resolve_dir(video_dir, "video", "video")
        self.assets_dir = self._resolve_dir(assets_dir, "assets", "assets")
        self.script_dir = self._resolve_dir(script_dir, "script", "script")
        self.thumbs_dir = self._resolve_dir(thumbs_dir, "thumbs", "thumbs")
        self.state_path = self.project_dir / "project_state.json"
        self._state: Dict[str, Any] = self._load_state()
        self.items: Dict[str, ProjectItem] = {}
        self.videos: Dict[str, VideoJob] = {}
        for video in (self._state.get("videos") or {}).values():
            try:
                job = VideoJob.from_state(video)
                if job.id:
                    self.videos[job.id] = job
            except Exception as exc:  # corrupt entry: skip rather than fail the project
                logger.warning("Bỏ qua video state lỗi: %s", exc)

    def _resolve_dir(
        self, value: Optional[str | os.PathLike], fallback: str, key: str = ""
    ) -> Path:
        if value is None or str(value).strip() == "":
            path = self.project_dir / fallback
        else:
            path = Path(value)
            if not path.is_absolute():
                path = self.project_dir / path
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        path = path.resolve()
        if key and not existed:
            self.created_folders.append(key)
        return path

    def folders(self) -> Dict[str, Path]:
        """Ordered mapping key → folder path used by the skeleton checklist."""
        return {
            "script": self.script_dir,
            "audio": self.audio_dir,
            "video": self.video_dir,
            "thumbs": self.thumbs_dir,
            "assets": self.assets_dir,
        }

    def ensure_structure(self) -> List[str]:
        """Create any missing standard folder and return the keys it created."""
        created: List[str] = []
        for key, path in self.folders().items():
            if path.is_dir():
                continue
            path.mkdir(parents=True, exist_ok=True)
            created.append(key)
        if created:
            self.created_folders.extend(created)
            self.save_state()
        return created

    def folder_status(self) -> List[Dict[str, Any]]:
        labels = dict(self.FOLDER_LABELS)
        return [
            {
                "key": key,
                "label": labels.get(key, key.upper()),
                "path": str(path),
                "exists": path.is_dir(),
            }
            for key, path in self.folders().items()
        ]

    @staticmethod
    def create_project(parent_dir: str | os.PathLike, name: str) -> Path:
        """Create a new project folder (non-empty targets are rejected)."""
        from utils.files import safe_filename

        safe = safe_filename(name, "project")
        target = (Path(parent_dir) / safe).resolve()
        if target.exists():
            if not target.is_dir():
                raise ValueError(f"Đã có tệp trùng tên: {target}")
            if any(target.iterdir()):
                raise ValueError(f"Thư mục đã tồn tại và không rỗng: {target}")
        target.mkdir(parents=True, exist_ok=True)
        manager = ProjectManager(target)
        manager.save_state()
        return target

    # ── state ────────────────────────────────────────────────────────────────
    def _load_state(self) -> Dict[str, Any]:
        data = load_json(self.state_path, {}) or {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("items", {})
        data.setdefault("videos", {})
        return data

    def save_state(self) -> None:
        with self._lock:
            self._state["version"] = 1
            self._state["project_dir"] = str(self.project_dir)
            self._state["paths"] = {
                "script": str(self.script_dir),
                "audio": str(self.audio_dir),
                "video": str(self.video_dir),
                "assets": str(self.assets_dir),
                "thumbs": str(self.thumbs_dir),
            }
            self._state["items"] = {base: item.to_state() for base, item in self.items.items()}
            self._state["videos"] = {job_id: job.to_state() for job_id, job in self.videos.items()}
            self._state["updated_at"] = _now()
            write_json_atomic(self.state_path, self._state)

    # ── scanning ─────────────────────────────────────────────────────────────
    def scan(self) -> List[ProjectItem]:
        """Discover scripts, pair ``name.txt`` with ``name.json`` and refresh state."""
        with self._lock:
            old_items = dict(self._state.get("items") or {})
        text_files: Dict[str, Path] = {}
        json_files: Dict[str, Path] = {}
        for path in natural_sorted(self.script_dir.iterdir() if self.script_dir.exists() else []):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".txt":
                text_files[path.stem] = path
            elif suffix == ".json":
                json_files[path.stem] = path

        bases = sorted(set(text_files) | set(json_files), key=natural_key)
        items: Dict[str, ProjectItem] = {}
        for base in bases:
            txt_path = text_files.get(base)
            json_path = json_files.get(base)
            raw_metadata: Dict[str, Any] = {}
            metadata_error = ""
            if json_path:
                loaded = load_json(json_path, None)
                if loaded is None:
                    metadata_error = "JSON không đọc được (cú pháp lỗi?)"
                    logger.warning("%s: %s", json_path.name, metadata_error)
                else:
                    raw_metadata = loaded if isinstance(loaded, dict) else {"chapters": loaded}
            metadata = normalize_metadata(raw_metadata, json_path)
            text = ""
            if txt_path:
                try:
                    text = read_text_smart(txt_path)
                except OSError as exc:
                    metadata_error = f"Không đọc được TXT: {exc}"
            elif metadata.get("text_content"):
                text = str(metadata["text_content"])
            if not text.strip():
                if txt_path or json_path:
                    logger.debug("Bỏ qua %s: không có nội dung văn bản.", base)
                continue

            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            previous = old_items.get(base) or {}
            image_takes = _path_list(previous.get("image_takes"))
            voice_takes = _path_list(previous.get("voice_takes"))
            voice_takes = [
                str(self.project_dir / path) if not Path(path).is_absolute() else str(Path(path))
                for path in voice_takes
            ]
            selected_image_index = _clamp_index(
                previous.get("selected_image_index"), len(image_takes))
            selected_voice_index = _clamp_index(
                previous.get("selected_voice_index"), len(voice_takes))
            audio_path = Path(previous.get("audio_path") or (self.audio_dir / f"{base}.mp3"))
            if not audio_path.is_absolute():
                audio_path = self.project_dir / audio_path
            if voice_takes:
                audio_path = Path(voice_takes[selected_voice_index])
            duration = float(previous.get("duration") or 0.0)
            has_audio = False
            audio_mtime = 0.0
            try:
                if audio_path.is_file() and audio_path.stat().st_size > 0:
                    has_audio = True
                    audio_mtime = audio_path.stat().st_mtime
                    if abs(audio_mtime - float(previous.get("audio_mtime") or 0)) > 1.0 or not duration:
                        duration = probe_duration(audio_path)
            except OSError:
                has_audio = False
            state = str(previous.get("state") or ITEM_PENDING)
            error = str(previous.get("error") or "")
            if state in (ITEM_TTS_DONE, ITEM_VIDEO_DONE, ITEM_UPLOAD_DONE) and not has_audio:
                state = ITEM_PENDING
            elif has_audio and state in (ITEM_PENDING, ITEM_TTS_PROCESSING):
                state = ITEM_TTS_DONE
            if has_audio and not duration:
                duration = probe_duration(audio_path)

            dub_mode = str(previous.get("dub_mode") or "text")
            cues_raw = str(previous.get("cues_path") or "").strip()
            cues_file = Path(cues_raw) if cues_raw else self.audio_dir / f"{base}.cues.json"
            if not cues_file.is_absolute():
                cues_file = self.project_dir / cues_file

            items[base] = ProjectItem(
                base=base,
                text_file=txt_path,
                json_file=json_path,
                text=text,
                text_hash=digest,
                metadata=metadata,
                audio_path=audio_path,
                duration=duration,
                audio_mtime=audio_mtime,
                state=state,
                error=error,
                video_id=str(previous.get("video_id") or ""),
                metadata_error=metadata_error,
                image_takes=image_takes,
                selected_image_index=selected_image_index,
                voice_takes=voice_takes,
                selected_voice_index=selected_voice_index,
                language=resolve_language(
                    metadata, base, previous.get("language") or self.default_language),
                dub_mode=dub_mode,
                cues_path=cues_file,
            )

        # Keep state for items whose files disappeared only when an output exists;
        # the working set is always what the script folder currently contains.
        self.items = items
        pruned = [job_id for job_id, job in self.videos.items()
                  if not set(job.items).issubset(items)]
        for job_id in pruned:
            logger.warning("Video %s bị bỏ vì thiếu file đầu vào.", job_id)
            self.videos.pop(job_id, None)
        self.save_state()
        return self.get_items()

    def get_items(self) -> List[ProjectItem]:
        return sorted(self.items.values(), key=lambda item: natural_key(item.base))

    def get_item(self, base: str) -> Optional[ProjectItem]:
        return self.items.get(base)

    def items_for(self, bases: Iterable[str]) -> List[ProjectItem]:
        return [self.items[b] for b in bases if b in self.items]

    def audio_exists(self, item: ProjectItem) -> bool:
        return item.audio_path.is_file() and item.audio_path.stat().st_size > 0

    def ensure_item_audio_dir(self, item: ProjectItem) -> None:
        item.audio_path.parent.mkdir(parents=True, exist_ok=True)

    def update_item(self, base: str, **fields: Any) -> Optional[ProjectItem]:
        item = self.items.get(base)
        if item is None:
            return None
        for key, value in fields.items():
            if hasattr(item, key):
                setattr(item, key, value)
        item.updated_at = _now()
        self.save_state()
        return item

    def set_item_state(self, base: str, state: str, error: str = "") -> Optional[ProjectItem]:
        return self.update_item(base, state=state, error=error)

    def set_item_language(self, base: str, language: str) -> Optional[ProjectItem]:
        """Pin the TTS/subtitle language of one item (overrides suffix detection)."""
        return self.update_item(base, language=normalize_language(language))

    def select_take(self, base: str, kind: str, index: int) -> Optional[ProjectItem]:
        """Switch the selected take ("image"/"voice") of one item and persist state."""
        item = self.items.get(base)
        if item is None:
            return None
        item.select_take(kind, index)
        item.updated_at = _now()
        self.save_state()
        return item

    def add_take(self, base: str, kind: str, path: str | os.PathLike) -> Optional[ProjectItem]:
        """Append a candidate take ("image"/"voice") and select it."""
        item = self.items.get(base)
        if item is None:
            return None
        kind = str(kind or "").strip().lower()
        value = str(path).strip()
        if not value:
            return item
        if kind == "image":
            if value not in item.image_takes:
                item.image_takes.append(value)
            item.selected_image_index = item.image_takes.index(value)
        elif kind == "voice":
            if value not in item.voice_takes:
                item.voice_takes.append(value)
            item.selected_voice_index = item.voice_takes.index(value)
            item.audio_path = Path(value)
        else:
            return item
        item.updated_at = _now()
        self.save_state()
        return item

    def remove_take(self, base: str, kind: str, index: int) -> Optional[ProjectItem]:
        """Remove one take; clamp the selection and re-sync the audio path."""
        item = self.items.get(base)
        if item is None:
            return None
        kind = str(kind or "").strip().lower()
        if kind == "image":
            takes = item.image_takes
            if 0 <= index < len(takes):
                takes.pop(index)
                item.selected_image_index = _clamp_index(item.selected_image_index, len(takes))
        elif kind == "voice":
            takes = item.voice_takes
            if 0 <= index < len(takes):
                takes.pop(index)
                item.selected_voice_index = _clamp_index(item.selected_voice_index, len(takes))
                item.audio_path = (
                    Path(takes[item.selected_voice_index])
                    if takes else self.audio_dir / f"{base}.mp3"
                )
        else:
            return item
        item.updated_at = _now()
        self.save_state()
        return item

    def set_items_state(self, bases: Iterable[str], state: str, error: str = "") -> None:
        for base in bases:
            item = self.items.get(base)
            if item is not None:
                item.state = state
                item.error = error
                item.updated_at = _now()
        self.save_state()

    def set_items_video(self, bases: Iterable[str], video_id: str) -> None:
        for base in bases:
            item = self.items.get(base)
            if item is not None:
                item.video_id = video_id
        self.save_state()

    # ── videos ───────────────────────────────────────────────────────────────
    def upsert_video(self, job: VideoJob) -> VideoJob:
        self.videos[job.id] = job
        self.save_state()
        return job

    def update_video(self, job_id: str, **fields: Any) -> Optional[VideoJob]:
        job = self.videos.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        self.save_state()
        return job

    def get_video(self, job_id: str) -> Optional[VideoJob]:
        return self.videos.get(job_id)

    def remove_stale_videos(self, valid_ids: Iterable[str]) -> None:
        valid = set(valid_ids)
        for job_id in list(self.videos):
            if job_id not in valid:
                self.videos.pop(job_id)
        self.save_state()

    def video_for_item(self, base: str) -> Optional[VideoJob]:
        for job in self.videos.values():
            if base in job.items:
                return job
        return None

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {state: 0 for state in ALL_STATES}
        for item in self.items.values():
            counts[item.state] = counts.get(item.state, 0) + 1
        return counts

    def delete_project_outputs(self, audio: bool = False, video: bool = False) -> None:
        """Remove generated files and reset state (used by Force rebuild)."""
        if audio:
            for item in self.items.values():
                if item.audio_path.is_file():
                    try:
                        item.audio_path.unlink()
                    except OSError as exc:
                        logger.warning("Không xoá được %s: %s", item.audio_path, exc)
                shutil.rmtree(self.audio_dir / "cues" / item.base, ignore_errors=True)
                self._reset_item_cues(item.base)
                item.state = ITEM_PENDING
                item.duration = 0.0
                item.error = ""
        if video:
            for job in list(self.videos.values()):
                if job.path.is_file():
                    try:
                        job.path.unlink()
                    except OSError as exc:
                        logger.warning("Không xoá được %s: %s", job.path, exc)
            self.videos.clear()
            if audio:
                for item in self.items.values():
                    item.state = ITEM_PENDING
        self.save_state()

    def _reset_item_cues(self, base: str) -> None:
        """Mark every cue PENDING again but keep the cue list itself."""
        path = self.audio_dir / f"{base}.cues.json"
        data = load_json(path, None)
        entries = data.get("cues") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return
        for entry in entries:
            if isinstance(entry, dict):
                entry["status"] = "PENDING"
                entry["out"] = ""
                entry["error"] = ""
        if isinstance(data, dict):
            write_json_atomic(path, data)
        else:
            write_json_atomic(path, {"base": base, "cues": entries})

    def copy_assets(self, sources: Iterable[str | os.PathLike]) -> List[Path]:
        copied: List[Path] = []
        ensure_dir(self.assets_dir)
        for source in sources:
            src = Path(source)
            if not src.is_file():
                continue
            target = self.assets_dir / src.name
            if src.resolve() != target.resolve():
                shutil.copy2(src, target)
            copied.append(target)
        return copied
