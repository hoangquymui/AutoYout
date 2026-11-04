"""YouTube publishing: Data API v3 (OAuth2, resumable) and browser automation.

The module is dependency-light at import time: Google client libraries and
Playwright are imported only when the corresponding upload path is used, so the
rest of AutoYout still runs on a machine without those optional packages.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.languages import normalize_language, youtube_language
from core.project_manager import (
    ITEM_ERROR,
    ITEM_UPLOAD_DONE,
    ITEM_UPLOADING,
    ProjectItem,
    ProjectManager,
    VIDEO_DONE,
    VIDEO_ERROR,
    VIDEO_PENDING,
    VIDEO_PROCESSING,
    VIDEO_UPLOAD_DONE,
    VIDEO_UPLOADING,
    VideoJob,
)
from core.settings import ROOT, credentials_dir
from utils.files import ensure_dir, natural_key, safe_filename
from utils.logger import get_logger

logger = get_logger("youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
_CHUNK_SIZE = 8 * 1024 * 1024
_MAX_TITLE = 100
_MAX_DESCRIPTION = 5000
_MAX_TAGS_CHARS = 500
_SCHEDULE_LEAD = timedelta(minutes=10)

ProgressCb = Optional[Callable[[int, str], None]]
PauseCb = Optional[Callable[[], bool]]
CancelCb = Optional[Callable[[], bool]]


class YouTubeError(RuntimeError):
    """Upload/auth failure with a user-facing message."""


class UploadCancelled(YouTubeError):
    """The user stopped the upload."""


class AuthRequired(YouTubeError):
    """A channel is not authorized (or the token expired without refresh)."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── configuration ────────────────────────────────────────────────────────────
@dataclass
class YouTubeConfig:
    method: str = "api"                 # api | browser
    channel: str = "default"
    client_secret: str = ""
    title_template: str = "{title}"
    description_template: str = "{description}\n\n⏱ Timestamps:\n{timestamps}"
    tags: str = ""
    category_id: str = "22"
    language: str = "vi"
    privacy: str = "private"
    schedule_mode: str = "interval"     # none | interval | times | metadata
    schedule_first: str = ""
    schedule_interval_hours: float = 24.0
    schedule_times: str = "08:00,20:00"
    playlist_id: str = ""
    thumbnail: str = ""
    made_for_kids: bool = False
    browser_profile: str = ""
    browser_headless: bool = False

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "YouTubeConfig":
        raw = settings.get("youtube") or {}
        times = raw.get("schedule_times") or ""
        if isinstance(times, (list, tuple)):
            times = ",".join(str(part) for part in times)
        return cls(
            method=str(raw.get("method") or "api").lower(),
            channel=str(raw.get("channel") or "default").strip() or "default",
            client_secret=str(raw.get("client_secret") or ""),
            title_template=str(raw.get("title_template") or "{title}"),
            description_template=str(raw.get("description_template") or ""),
            tags=str(raw.get("tags") or ""),
            category_id=str(raw.get("category_id") or "22"),
            language=str(raw.get("language") or "vi"),
            privacy=str(raw.get("privacy") or "private").lower(),
            schedule_mode=str(raw.get("schedule_mode") or "interval").lower(),
            schedule_first=str(raw.get("schedule_first") or ""),
            schedule_interval_hours=max(0.1, float(raw.get("schedule_interval_hours", 24) or 24)),
            schedule_times=str(times),
            playlist_id=str(raw.get("playlist_id") or ""),
            thumbnail=str(raw.get("thumbnail") or ""),
            made_for_kids=bool(raw.get("made_for_kids", False)),
            browser_profile=str(raw.get("browser_profile") or ""),
            browser_headless=bool(raw.get("browser_headless", False)),
        )

    def errors(self) -> List[str]:
        problems: List[str] = []
        if self.method not in ("api", "browser"):
            problems.append(f"Phương thức upload không hợp lệ: {self.method}")
        if self.privacy not in ("public", "private", "unlisted"):
            problems.append(f"Quyền riêng tư không hợp lệ: {self.privacy}")
        if self.schedule_mode not in ("none", "interval", "times", "metadata"):
            problems.append(f"Kiểu lên lịch không hợp lệ: {self.schedule_mode}")
        if self.schedule_mode == "interval" and self.schedule_interval_hours <= 0:
            problems.append("Khoảng cách lên lịch phải lớn hơn 0 giờ.")
        if self.schedule_mode == "times" and not parse_times(self.schedule_times):
            problems.append("Danh sách giờ đăng không hợp lệ (định dạng HH:MM,HH:MM).")
        return problems


@dataclass
class Channel:
    name: str
    token_path: Path
    authorized: bool = False
    title: str = ""
    channel_id: str = ""
    updated_at: str = ""

    def public(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "authorized": self.authorized,
            "title": self.title,
            "channel_id": self.channel_id,
            "updated_at": self.updated_at,
            "token_path": str(self.token_path),
        }


@dataclass
class UploadResult:
    job_id: str
    video_id: str = ""
    url: str = ""
    scheduled_at: str = ""
    method: str = "api"
    title: str = ""
    warnings: List[str] = field(default_factory=list)


# ── time helpers ─────────────────────────────────────────────────────────────
def _local_now() -> datetime:
    return datetime.now().astimezone()


def parse_when(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse ISO/date-time strings into an aware local datetime."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    now = now or _local_now()
    tz = now.tzinfo
    candidate: Optional[datetime] = None
    iso = text.replace("Z", "+00:00")
    try:
        candidate = datetime.fromisoformat(iso)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M",
                    "%d-%m-%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                candidate = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if candidate is None:
        return None
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=tz)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) and candidate.hour == 0 and candidate.minute == 0:
        candidate = candidate.replace(hour=8)
    return candidate.astimezone(tz)


def parse_times(value: str) -> List[dtime]:
    times: List[dtime] = []
    for part in re.split(r"[,;\s]+", str(value or "")):
        match = re.match(r"^(\d{1,2}):(\d{2})$", part.strip())
        if not match:
            continue
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            times.append(dtime(hour=hour, minute=minute))
    return sorted(set(times))


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_publish_at(cfg: YouTubeConfig, existing: Sequence[datetime],
                       now: Optional[datetime] = None) -> str:
    """Next free publish slot (ISO Z) for the configured scheduling mode."""
    now = now or _local_now()
    mode = cfg.schedule_mode
    if mode in ("none", "off", "", "metadata"):
        return ""
    future = sorted(dt for dt in existing if dt and dt > now)
    latest = future[-1] if future else None
    earliest = now + _SCHEDULE_LEAD
    if latest is not None and latest + timedelta(minutes=1) > earliest:
        earliest = latest + timedelta(minutes=1)
    first = parse_when(cfg.schedule_first, now)

    if mode == "times":
        slots = parse_times(cfg.schedule_times)
        if not slots:
            return ""
        start_day = (first or now).date()
        if start_day < now.date():
            start_day = now.date()
        for day_offset in range(0, 367):
            day = start_day + timedelta(days=day_offset)
            for slot in slots:
                candidate = datetime.combine(day, slot, tzinfo=now.tzinfo)
                if candidate >= earliest:
                    return to_utc_iso(candidate)
        return ""

    interval = timedelta(hours=cfg.schedule_interval_hours)
    if latest is not None:
        candidate = latest + interval
    elif first is not None and first >= earliest:
        candidate = first
    else:
        candidate = earliest
    if candidate < earliest:
        candidate = earliest
    return to_utc_iso(candidate)


# ── metadata rendering ───────────────────────────────────────────────────────
def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def render_template(template: str, values: Dict[str, str]) -> str:
    text = str(template or "")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _split_tags(value: str) -> List[str]:
    raw = str(value or "").replace("#", " ")
    parts = re.split(r"[,\n;]+", raw)
    return [_clean_text(part) for part in parts if _clean_text(part)]


def _first_item(manager: ProjectManager, job: VideoJob) -> Optional[ProjectItem]:
    for base in job.items:
        item = manager.get_item(base)
        if item is not None:
            return item
    return None


def _part_number(job: VideoJob, index: int) -> str:
    match = re.search(r"::(\d+)", job.id)
    if match:
        return str(int(match.group(1)))
    return str(index)


def _resolve_setting_path(value: str, manager: Optional[ProjectManager] = None) -> Optional[Path]:
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if path.is_absolute():
        return path if path.exists() else path
    candidates: List[Path] = []
    if manager is not None:
        candidates += [manager.project_dir, manager.assets_dir]
    candidates += [ROOT, Path.cwd()]
    for base in candidates:
        candidate = base / path
        if candidate.exists():
            return candidate
    return candidates[0] / path


def build_metadata(manager: ProjectManager, job: VideoJob, cfg: YouTubeConfig,
                   index: int, publish_at: str = "") -> Dict[str, Any]:
    """Render title/description/tags and the API snippet/status payload."""
    item = _first_item(manager, job)
    meta = item.metadata if item is not None else {}
    description_source = str(meta.get("description") or "")
    project_name = manager.project_dir.name or "AutoYout"
    values = {
        "project_name": project_name,
        "file_name": job.path.stem,
        "index": str(index),
        "part_number": _part_number(job, index),
        "timestamps": job.timestamps_text or "",
        "title": job.title or job.path.stem,
        "description": description_source,
        "date": datetime.now().strftime("%d/%m/%Y"),
        "channel": cfg.channel,
        "youtube_url": job.youtube_url or "",
    }

    title = _clean_text(render_template(cfg.title_template, values)) or job.title or job.path.stem
    description = render_template(cfg.description_template, values)
    if not values["timestamps"]:
        description = re.sub(r"(?im)^[^\n]*timestamps[^\n]*$", "", description)
    description = re.sub(r"\n{3,}", "\n\n", description).strip()
    if description_source and not description:
        description = description_source

    tags: List[str] = []
    seen: set[str] = set()
    for tag in _split_tags(render_template(cfg.tags, values)) + list(meta.get("tags") or []):
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    total = 0
    clipped: List[str] = []
    for tag in tags:
        cost = len(tag) + 3  # quotes + comma
        if total + cost > _MAX_TAGS_CHARS:
            break
        clipped.append(tag)
        total += cost
    tags = clipped

    playlist = str(meta.get("playlist_id") or "").strip() or cfg.playlist_id
    category = str(meta.get("category_id") or "").strip() or cfg.category_id or "22"
    privacy = str(meta.get("privacy") or "").strip().lower()
    if privacy not in ("public", "private", "unlisted"):
        privacy = cfg.privacy
    if publish_at:
        privacy = "private"
    made_for_kids = bool(meta.get("made_for_kids")) or cfg.made_for_kids

    thumb: Optional[Path] = None
    raw_thumb = str(meta.get("thumbnail_path") or "")
    if raw_thumb and Path(raw_thumb).is_file():
        thumb = Path(raw_thumb)
    else:
        candidate = _resolve_setting_path(cfg.thumbnail, manager)
        if candidate is not None and candidate.is_file():
            thumb = candidate

    item_language = str(meta.get("language") or "").strip()
    if not item_language and item is not None:
        item_language = str(getattr(item, "language", "") or "")
    language = youtube_language(normalize_language(item_language or cfg.language))

    return {
        "title": title[:_MAX_TITLE],
        "description": description[:_MAX_DESCRIPTION],
        "tags": tags,
        "category_id": category,
        "privacy": privacy,
        "made_for_kids": made_for_kids,
        "playlist_id": playlist,
        "thumbnail": thumb,
        "language": language,
        "publish_at": publish_at,
        "values": values,
    }


# ── uploader ─────────────────────────────────────────────────────────────────
class YouTubeUploader:
    def __init__(self, settings: Dict[str, Any]) -> None:
        self.settings = settings
        self.config = YouTubeConfig.from_settings(settings)
        self.retry = max(2, int((settings.get("app") or {}).get("retry", 1) or 1) + 2)
        self._services: Dict[str, Any] = {}

    # ── credential/channel management ────────────────────────────────────────
    @property
    def credentials_dir(self) -> Path:
        return credentials_dir()

    def client_secret_path(self) -> Path:
        raw = self.config.client_secret
        if raw:
            path = Path(os.path.expandvars(os.path.expanduser(raw.strip().strip('"'))))
            if path.is_absolute():
                return path
            for base in (self.credentials_dir, ROOT):
                candidate = base / path
                if candidate.is_file():
                    return candidate
            return self.credentials_dir / path
        return self.credentials_dir / "client_secret.json"

    def has_client_secret(self) -> bool:
        path = self.client_secret_path()
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and "installed" in data

    def channel_name(self) -> str:
        return safe_filename(self.config.channel or "default", "default")

    def token_path(self, channel: Optional[str] = None) -> Path:
        name = safe_filename(channel or self.channel_name(), "default")
        return self.credentials_dir / f"token_{name}.json"

    def meta_path(self, channel: Optional[str] = None) -> Path:
        name = safe_filename(channel or self.channel_name(), "default")
        return self.credentials_dir / f"token_{name}.meta.json"

    def channels(self) -> List[Channel]:
        result: List[Channel] = []
        for path in sorted(self.credentials_dir.glob("token_*.json"), key=lambda p: natural_key(p.name)):
            if path.name.endswith(".meta.json"):
                continue
            name = path.stem[len("token_"):]
            data: Dict[str, Any] = {}
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                data = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                data = {}
            meta: Dict[str, Any] = {}
            sidecar = path.with_name(path.stem + ".meta.json")
            if sidecar.is_file():
                try:
                    meta = json.loads(sidecar.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
            authorized = bool(data.get("refresh_token") or data.get("token"))
            result.append(Channel(
                name=name,
                token_path=path,
                authorized=authorized,
                title=str(meta.get("title") or ""),
                channel_id=str(meta.get("channel_id") or ""),
                updated_at=str(meta.get("updated_at") or ""),
            ))
        known = self.channel_name()
        if known and not any(ch.name == known for ch in result):
            result.append(Channel(name=known, token_path=self.token_path(known),
                                  authorized=self.token_path(known).is_file()))
        return result

    def delete_channel(self, channel: str) -> bool:
        """Remove saved token and metadata files for a channel."""
        name = safe_filename(channel, "default")
        deleted = False
        token = self.token_path(name)
        if token.is_file():
            try:
                token.unlink()
                deleted = True
            except OSError:
                pass
        meta = self.meta_path(name)
        if meta.is_file():
            try:
                meta.unlink()
                deleted = True
            except OSError:
                pass
        if name in self._services:
            del self._services[name]
        return deleted

    def authorize(self, channel: Optional[str] = None, open_browser: bool = True,
                  progress_cb: ProgressCb = None) -> Channel:
        """Run the OAuth consent flow and store a token for ``channel``."""
        name = safe_filename(channel or self.channel_name(), "default")
        secret = self.client_secret_path()
        if not secret.is_file():
            raise YouTubeError(
                f"Không tìm thấy client_secret.json tại {secret}. "
                "Tải OAuth client (Desktop app) từ Google Cloud Console rồi đặt vào "
                "config/youtube_credentials/client_secret.json."
            )
        if not self.has_client_secret():
            raise YouTubeError(
                f"{secret.name} không đúng định dạng OAuth client (thiếu khóa 'installed')."
            )
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise YouTubeError(
                "Thiếu google-auth-oauthlib. Chạy: pip install google-auth-oauthlib"
            ) from exc

        if progress_cb:
            progress_cb(5, "Đang mở trình duyệt để đăng nhập Google...")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        try:
            creds = flow.run_local_server(
                port=0,
                open_browser=open_browser,
                authorization_prompt_message="Đăng nhập Google tại: {url}",
                success_message="AutoYout đã được cấp quyền. Bạn có thể đóng tab này.",
                prompt="consent",
                access_type="offline",
            )
        except Exception as exc:
            raise YouTubeError(f"Đăng nhập Google thất bại: {exc}") from exc

        token = self.token_path(name)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json(), encoding="utf-8")
        try:
            os.chmod(token, 0o600)
        except OSError:
            pass

        info: Dict[str, Any] = {}
        try:
            service = self._service(name, force_reload=True)
            response = service.channels().list(part="snippet,contentDetails", mine=True).execute()
            items = response.get("items") or []
            if items:
                info = {
                    "title": items[0].get("snippet", {}).get("title", ""),
                    "channel_id": items[0].get("id", ""),
                    "updated_at": _now(),
                }
        except Exception as exc:
            logger.warning("Đã lưu token nhưng chưa kiểm tra được kênh: %s", exc)
        if info:
            self.meta_path(name).write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress_cb:
            progress_cb(100, f"Đã kết nối kênh {info.get('title') or name}.")
        logger.info("Đã uỷ quyền kênh YouTube '%s'", name)
        return Channel(
            name=name,
            token_path=token,
            authorized=True,
            title=str(info.get("title") or ""),
            channel_id=str(info.get("channel_id") or ""),
            updated_at=str(info.get("updated_at") or ""),
        )

    # ── Google client ────────────────────────────────────────────────────────
    @staticmethod
    def _google_modules():
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise YouTubeError(
                "Thiếu thư viện Google API. Chạy: pip install -r requirements.txt"
            ) from exc
        return Request, Credentials, build, HttpError, MediaFileUpload

    def _load_credentials(self, channel: str):
        Request, Credentials, _, _, _ = self._google_modules()
        path = self.token_path(channel)
        if not path.is_file():
            raise AuthRequired(
                f"Kênh '{channel}' chưa được uỷ quyền. Vào tab YouTube và bấm Đăng nhập."
            )
        try:
            creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        except Exception as exc:
            raise AuthRequired(
                f"Token của kênh '{channel}' không đọc được ({exc}). Hãy đăng nhập lại."
            ) from exc
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                path.write_text(creds.to_json(), encoding="utf-8")
            except Exception as exc:
                raise AuthRequired(
                    f"Không làm mới được token kênh '{channel}' ({exc}). Hãy đăng nhập lại."
                ) from exc
        if not creds.valid:
            raise AuthRequired(
                f"Token kênh '{channel}' đã hết hạn. Hãy đăng nhập lại."
            )
        return creds

    def _service(self, channel: Optional[str] = None, force_reload: bool = False):
        name = safe_filename(channel or self.channel_name(), "default")
        if force_reload:
            self._services.pop(name, None)
        if name in self._services:
            return self._services[name]
        _, _, build, _, _ = self._google_modules()
        creds = self._load_credentials(name)
        service = build("youtube", "v3", credentials=creds, cache_discovery=False)
        self._services[name] = service
        return service

    def channel_info(self, channel: Optional[str] = None) -> Dict[str, Any]:
        name = safe_filename(channel or self.channel_name(), "default")
        service = self._service(channel)
        response = service.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
        items = response.get("items") or []
        if not items:
            raise YouTubeError("Tài khoản Google này không có kênh YouTube.")
        item = items[0]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        info = {
            "channel_id": item.get("id", ""),
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "subscribers": stats.get("subscriberCount", ""),
            "videos": stats.get("videoCount", ""),
            "updated_at": _local_now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            meta = self.meta_path(name)
            meta.parent.mkdir(parents=True, exist_ok=True)
            meta.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return info

    # ── scheduling / metadata for a job ──────────────────────────────────────
    def existing_schedule(self, manager: ProjectManager,
                          exclude_job_id: str = "") -> List[datetime]:
        now = _local_now()
        out: List[datetime] = []
        for job in manager.videos.values():
            if job.id == exclude_job_id or not job.scheduled_at:
                continue
            parsed = parse_when(job.scheduled_at, now)
            if parsed is not None and parsed > now:
                out.append(parsed)
        return out

    def publish_at_for(self, manager: ProjectManager, job: VideoJob,
                       cfg: Optional[YouTubeConfig] = None) -> str:
        cfg = cfg or self.config
        if cfg.schedule_mode in ("none", "off", ""):
            return ""
        now = _local_now()
        for base in job.items:
            item = manager.get_item(base)
            raw = (item.metadata or {}).get("publish_at") if item is not None else ""
            parsed = parse_when(raw, now)
            if parsed is not None and parsed > now + timedelta(minutes=2):
                return to_utc_iso(parsed)
        if cfg.schedule_mode == "metadata":
            return ""
        return compute_publish_at(cfg, self.existing_schedule(manager, job.id), now)

    def needs_upload(self, manager: ProjectManager, job: VideoJob,
                     force: bool = False) -> bool:
        if not job.path.is_file() or job.path.stat().st_size == 0:
            return False
        if job.youtube_video_id and not force:
            return False
        if job.state in (VIDEO_PENDING, VIDEO_PROCESSING, VIDEO_UPLOADING):
            return False
        return True

    # ── public upload API ────────────────────────────────────────────────────
    def upload_job(self, manager: ProjectManager, job_id: str,
                   config: Optional[YouTubeConfig] = None, index: int = 1,
                   progress_cb: ProgressCb = None,
                   pause_cb: PauseCb = None,
                   cancel_cb: CancelCb = None) -> UploadResult:
        cfg = config or self.config
        problems = cfg.errors()
        if problems:
            raise YouTubeError("Cấu hình YouTube không hợp lệ: " + "; ".join(problems))
        job = manager.get_video(job_id)
        if job is None:
            raise YouTubeError(f"Không tìm thấy video job {job_id}.")
        if not job.path.is_file() or job.path.stat().st_size == 0:
            raise YouTubeError(f"Chưa có file video để upload: {job.path}")

        def report(percent: int, message: str) -> None:
            if progress_cb:
                try:
                    progress_cb(max(0, min(100, int(percent))), message)
                except Exception:
                    pass

        publish_at = self.publish_at_for(manager, job, cfg)
        metadata = build_metadata(manager, job, cfg, index, publish_at)
        manager.update_video(job.id, state=VIDEO_UPLOADING, error="")
        manager.set_items_state(job.items, ITEM_UPLOADING)
        report(1, f"Chuẩn bị upload: {metadata['title'][:60]}")
        try:
            if cfg.method == "browser":
                result = self._upload_browser(
                    manager, job, cfg, metadata, report, pause_cb, cancel_cb)
            else:
                result = self._upload_api(
                    manager, job, cfg, metadata, report, pause_cb, cancel_cb)
            manager.update_video(
                job.id,
                state=VIDEO_UPLOAD_DONE,
                youtube_video_id=result.video_id,
                youtube_url=result.url,
                scheduled_at=result.scheduled_at,
                uploaded_at=_now(),
                error="",
            )
            manager.set_items_state(job.items, ITEM_UPLOAD_DONE)
            report(100, "Đã đăng YouTube.")
            logger.info("Upload xong %s → %s", job.path.name, result.url)
            return result
        except UploadCancelled:
            manager.update_video(job.id, state=VIDEO_DONE, error="Đã huỷ upload.")
            manager.set_items_state(job.items, VIDEO_DONE)
            raise
        except Exception as exc:
            if not isinstance(exc, YouTubeError):
                logger.exception("Upload lỗi không mong đợi %s", job_id)
                exc = YouTubeError(f"Lỗi upload: {exc}")
            manager.update_video(job.id, state=VIDEO_ERROR, error=str(exc))
            manager.set_items_state(job.items, ITEM_ERROR, str(exc))
            raise exc

    def upload_all(self, manager: ProjectManager, config: Optional[YouTubeConfig] = None,
                   force: bool = False, only: Optional[Iterable[str]] = None,
                   progress_cb: ProgressCb = None,
                   pause_cb: PauseCb = None,
                   cancel_cb: CancelCb = None,
                   stop_on_error: bool = False) -> Tuple[List[UploadResult], List[str]]:
        cfg = config or self.config
        ordered = sorted(manager.videos.values(), key=lambda job: natural_key(job.path.name))
        positions = {job.id: idx + 1 for idx, job in enumerate(ordered)}
        wanted = set(only) if only is not None else None
        todo = [job for job in ordered
                if (wanted is None or job.id in wanted)
                and self.needs_upload(manager, job, force=force)]
        results: List[UploadResult] = []
        errors: List[str] = []
        for job in ordered:
            if wanted is not None and job.id in wanted and job not in todo:
                if not job.path.is_file() or job.path.stat().st_size == 0:
                    errors.append(f"{job.title}: chưa có file video ({job.path}).")
        for idx, job in enumerate(todo):
            def job_progress(percent: int, message: str, _i=idx, _job=job) -> None:
                if progress_cb:
                    overall = int((_i * 100 + percent) / max(1, len(todo)))
                    try:
                        progress_cb(overall, f"[{_job.title}] {message}", percent)
                    except TypeError:
                        progress_cb(overall, f"[{_job.title}] {message}")
            try:
                results.append(self.upload_job(
                    manager, job.id, cfg, positions.get(job.id, idx + 1),
                    job_progress, pause_cb, cancel_cb))
            except UploadCancelled:
                raise
            except YouTubeError as exc:
                errors.append(f"{job.title}: {exc}")
                logger.error("Upload lỗi %s: %s", job.id, exc)
                if stop_on_error:
                    break
        return results, errors

    # ── API path ─────────────────────────────────────────────────────────────
    def _upload_api(self, manager: ProjectManager, job: VideoJob, cfg: YouTubeConfig,
                    metadata: Dict[str, Any], report: Callable[[int, str], None],
                    pause_cb: PauseCb, cancel_cb: CancelCb) -> UploadResult:
        _, _, _, HttpError, MediaFileUpload = self._google_modules()
        service = self._service(cfg.channel)
        body = {
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": metadata["category_id"],
            },
            "status": {
                "privacyStatus": metadata["privacy"],
                "selfDeclaredMadeForKids": bool(metadata["made_for_kids"]),
            },
        }
        if metadata["language"]:
            body["snippet"]["defaultLanguage"] = metadata["language"]
            body["snippet"]["defaultAudioLanguage"] = metadata["language"]
        if metadata["publish_at"]:
            body["status"]["publishAt"] = metadata["publish_at"]
            body["status"]["privacyStatus"] = "private"
        report(3, "Bắt đầu tải video lên YouTube...")
        media = MediaFileUpload(str(job.path), mimetype="video/mp4",
                                chunksize=_CHUNK_SIZE, resumable=True)
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media, notifySubscribers=True)
        response: Optional[Dict[str, Any]] = None
        attempt = 0
        last_percent = 3
        while response is None:
            self._wait(pause_cb, cancel_cb)
            try:
                status, response = request.next_chunk()
            except HttpError as exc:
                if self._retryable(exc) and attempt < self.retry:
                    attempt += 1
                    report(last_percent, f"Lỗi mạng ({self._http_reason(exc)}), thử lại...")
                    self._sleep(min(30, 2 ** attempt), cancel_cb)
                    continue
                raise YouTubeError(self._http_message(exc)) from exc
            except (socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, OSError) as exc:
                if attempt < self.retry:
                    attempt += 1
                    report(last_percent, "Mất kết nối, đang thử lại...")
                    self._sleep(min(30, 2 ** attempt), cancel_cb)
                    continue
                raise YouTubeError(f"Mất kết nối khi upload: {exc}") from exc
            if status is not None:
                progress = max(0.0, min(1.0, float(status.progress())))
                last_percent = 4 + int(progress * 78)
                report(last_percent, f"Đang tải video... {progress * 100:.0f}%")
            self._check_cancel(cancel_cb)

        video_id = str((response or {}).get("id") or "")
        if not video_id:
            raise YouTubeError("YouTube không trả về ID video sau khi upload.")
        url = f"https://www.youtube.com/watch?v={video_id}"
        warnings: List[str] = []

        thumb = metadata.get("thumbnail")
        if thumb is not None:
            report(84, "Đang đặt thumbnail...")
            try:
                self._set_thumbnail(service, video_id, Path(thumb), MediaFileUpload)
            except Exception as exc:
                message = f"Không đặt được thumbnail: {exc}"
                warnings.append(message)
                logger.warning(message)

        playlist = metadata.get("playlist_id") or ""
        if playlist:
            report(92, "Đang thêm vào playlist...")
            try:
                service.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }
                    },
                ).execute()
            except Exception as exc:
                message = f"Không thêm được vào playlist: {exc}"
                warnings.append(message)
                logger.warning(message)

        scheduled = metadata["publish_at"]
        report(100, "Đã tải lên YouTube.")
        return UploadResult(
            job_id=job.id,
            video_id=video_id,
            url=url,
            scheduled_at=scheduled,
            method="api",
            title=metadata["title"],
            warnings=warnings,
        )

    @staticmethod
    def _set_thumbnail(service, video_id: str, path: Path, media_cls) -> None:
        if not path.is_file():
            return
        size = path.stat().st_size
        if size > 2 * 1024 * 1024:
            raise YouTubeError("Thumbnail lớn hơn 2 MB.")
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        media = media_cls(str(path), mimetype=mime)
        service.thumbnails().set(videoId=video_id, media_body=media).execute()

    # ── browser path ─────────────────────────────────────────────────────────
    def browser_login(self, *, progress_cb: Optional[Callable[[int, str], None]] = None,
                      cancel_cb: CancelCb = None, timeout: float = 300.0) -> Dict[str, str]:
        """Open Chrome with the configured profile so the user can sign in once."""
        cfg = YouTubeConfig.from_settings(self.settings)
        try:
            from playwright.sync_api import sync_playwright, Error as PlaywrightError
        except ImportError as exc:
            raise YouTubeError(
                "Thiếu Playwright. Chạy: pip install playwright "
                "(không cần tải browser nếu dùng Chrome có sẵn)."
            ) from exc

        profile = Path(os.path.expandvars(os.path.expanduser(
            cfg.browser_profile.strip()))) if cfg.browser_profile else self._default_profile()
        ensure_dir(profile)

        def report(percent: int, message: str) -> None:
            if progress_cb is not None:
                progress_cb(max(0, min(100, int(percent))), message)

        report(5, "Đang mở Chrome...")
        with sync_playwright() as pw:
            context = self._launch_context(pw, profile, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(30_000)
                report(10, "Đang mở YouTube Studio...")
                page.goto("https://studio.youtube.com", wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                if "accounts.google.com" in page.url:
                    deadline = max(30.0, timeout)
                    report(15, "Hãy đăng nhập Google và chọn đúng kênh trong cửa sổ "
                               f"Chrome (chờ tối đa {deadline / 60:.0f} phút)…")
                    self._wait_for_login(page, deadline, cancel_cb)
                    page.goto("https://studio.youtube.com", wait_until="domcontentloaded")
                    page.wait_for_timeout(2500)
                if "accounts.google.com" in page.url:
                    raise AuthRequired("Chưa đăng nhập được YouTube Studio trong profile này.")

                title = ""
                for selector in ("#channel-title", "ytcp-entity-name", "ytcp-avatar-menu-button"):
                    try:
                        locator = page.locator(selector).first
                        if locator.count() and locator.is_visible():
                            text = (locator.inner_text() or "").strip()
                            if text:
                                title = text.splitlines()[0][:80]
                                break
                    except PlaywrightError:
                        continue
                report(100, "Đã đăng nhập Chrome.")
                logger.info("Đã đăng nhập YouTube Studio bằng Chrome profile %s", profile)
                return {"profile": str(profile), "title": title}
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def _wait_for_login(self, page, timeout: float, cancel_cb: CancelCb) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_cancel(cancel_cb)
            page.wait_for_timeout(1000)
            if "accounts.google.com" not in page.url:
                return
        raise AuthRequired(
            "Hết thời gian chờ đăng nhập. Hãy thử lại và đăng nhập khi cửa sổ Chrome mở ra."
        )

    def _default_profile(self) -> Path:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "AutoYout" / "chrome-profile"

    def _upload_browser(self, manager: ProjectManager, job: VideoJob, cfg: YouTubeConfig,
                        metadata: Dict[str, Any], report: Callable[[int, str], None],
                        pause_cb: PauseCb, cancel_cb: CancelCb) -> UploadResult:
        try:
            from playwright.sync_api import sync_playwright, Error as PlaywrightError
        except ImportError as exc:
            raise YouTubeError(
                "Thiếu Playwright. Chạy: pip install playwright "
                "(không cần tải browser nếu dùng Chrome có sẵn)."
            ) from exc

        profile = Path(os.path.expandvars(os.path.expanduser(
            cfg.browser_profile.strip()))) if cfg.browser_profile else self._default_profile()
        ensure_dir(profile)
        warnings: List[str] = []
        report(3, "Đang mở Chrome...")
        with sync_playwright() as pw:
            context = self._launch_context(pw, profile, cfg.browser_headless)
            page = None
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(30_000)
                report(6, "Đang mở YouTube Studio...")
                page.goto("https://studio.youtube.com", wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(
                        "#upload-icon, #upload-button, ytcp-button:has-text('Tạo'), "
                        "ytcp-button:has-text('Create'), button[aria-label*='Tạo' i], "
                        "button[aria-label*='Create' i]",
                        timeout=25_000,
                    )
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                self._check_cancel(cancel_cb)
                if "accounts.google.com" in page.url:
                    if cfg.browser_headless:
                        raise AuthRequired(
                            "Chrome chưa đăng nhập YouTube Studio. Hãy tắt chế độ headless, "
                            "bấm Đăng nhập bằng Chrome rồi thử lại."
                        )
                    report(8, "Hãy đăng nhập Google/YouTube trong cửa sổ Chrome "
                              "vừa mở (chờ tối đa 5 phút)…")
                    self._wait_for_login(page, 300, cancel_cb)
                    page.goto("https://studio.youtube.com", wait_until="domcontentloaded")
                    page.wait_for_timeout(3500)

                report(10, "Chọn video để tải lên...")
                self._open_upload(page)
                self._attach_video(page, job.path)
                self._handle_auth_dialog(page, cfg, report, cancel_cb)
                report(16, "Đang tải video lên Studio...")
                dialog_ready = False
                for sel in ("div[contenteditable='true'][aria-label*='tiêu đề' i]",
                            "div[contenteditable='true'][aria-label*='title' i]",
                            "#textbox", "ytcp-uploads-dialog", "ytcp-upload-video-dialog"):
                    try:
                        el = page.locator(sel).first
                        if el.count():
                            dialog_ready = True
                            break
                    except Exception:
                        continue
                if not dialog_ready:
                    dialog = self._first(page, ["ytcp-uploads-dialog", "ytcp-upload-video-dialog", "#textbox"], 60_000)
                    if dialog is None:
                        raise YouTubeError("Không mở được hộp thoại upload của YouTube Studio.")

                self._fill_browser_metadata(page, metadata, warnings, report)
                self._browser_progress(page, report)
                self._check_cancel(cancel_cb)

                self._browser_next(page, "Chi tiết video")
                self._browser_next(page, "Video elements")
                self._browser_next(page, "Kiểm tra")
                report(70, "Đang kiểm tra bản quyền...")
                page.wait_for_timeout(1500)

                scheduled = self._browser_visibility(
                    page, metadata.get("publish_at", ""), metadata.get("privacy", "private"),
                    warnings, report)
                self._check_cancel(cancel_cb)
                report(88, "Đang xuất bản...")
                self._wait_done_ready(page, cancel_cb, report)
                done = self._first(page, [
                    "#done-button",
                    "ytcp-button#done-button",
                    "button:has-text('Lưu')",
                    "button:has-text('Xuất bản')",
                    "button:has-text('Save')",
                    "button:has-text('Publish')",
                    "button:has-text('Schedule')",
                ], 20_000)
                if done is None:
                    raise YouTubeError("Không tìm thấy nút Publish/Schedule/Lưu.")
                try:
                    done.click(force=True)
                except Exception:
                    try:
                        done.locator("button, div#button").first.click(force=True)
                    except Exception:
                        done.click()
                report(94, "Đang chờ YouTube xử lý...")
                url = self._browser_video_url(page)
                video_id = ""
                match = re.search(r"(?:youtu\.be/|v=)([A-Za-z0-9_-]{11})", url or "")
                if match:
                    video_id = match.group(1)
                if not video_id:
                    raise YouTubeError(
                        "Video đã được gửi nhưng không đọc được ID/URL trên Studio. "
                        "Hãy kiểm tra YouTube Studio trước khi thử lại để tránh đăng trùng."
                    )
                report(100, "Đã gửi video lên YouTube Studio.")
                return UploadResult(
                    job_id=job.id,
                    video_id=video_id,
                    url=url,
                    scheduled_at=scheduled,
                    method="browser",
                    title=metadata["title"],
                    warnings=warnings,
                )
            except (UploadCancelled, YouTubeError, AuthRequired):
                raise
            except PlaywrightError as exc:
                self._browser_screenshot(page, "browser_upload")
                raise YouTubeError(f"Lỗi Playwright: {exc}") from exc
            except Exception as exc:
                self._browser_screenshot(page, "browser_upload")
                raise YouTubeError(f"Lỗi upload bằng trình duyệt: {exc}") from exc
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def _launch_context(self, pw, profile: Path, headless: bool):
        args = ["--disable-blink-features=AutomationControlled", "--start-maximized"]
        try:
            return pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel="chrome",
                headless=headless,
                no_viewport=True,
                args=args,
            )
        except Exception as exc:
            logger.warning("Không mở được Chrome (%s), thử Chromium của Playwright.", exc)
            try:
                return pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=headless,
                    no_viewport=True,
                    args=args,
                )
            except Exception as exc2:
                raise YouTubeError(
                    "Không mở được trình duyệt. Cài Google Chrome hoặc chạy "
                    "'playwright install chromium'."
                ) from exc2

    @staticmethod
    def _first(page, selectors: Sequence[str], timeout_ms: int = 4000):
        deadline = time.time() + timeout_ms / 1000.0
        while True:
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count() and locator.is_visible():
                        return locator
                except Exception:
                    continue
            if time.time() >= deadline:
                return None
            page.wait_for_timeout(250)

    @staticmethod
    def _click_first(page, selectors: Sequence[str], timeout_ms: int = 4000, force: bool = False) -> bool:
        locator = YouTubeUploader._first(page, selectors, timeout_ms)
        if locator is None:
            return False
        try:
            locator.click(force=force)
            return True
        except Exception:
            try:
                locator.click(force=True)
                return True
            except Exception:
                return False

    def _open_upload(self, page) -> None:
        if page.locator("input[type='file']").first.count():
            return
        direct_selectors = [
            "#upload-icon",
            "ytcp-icon-button#upload-icon",
            "#upload-button",
            "ytcp-button#upload-button",
            "ytcp-icon-button[aria-label*='Tải video lên' i]",
            "ytcp-icon-button[aria-label*='Upload' i]",
            "button[aria-label*='Tải video lên' i]",
            "button[aria-label*='Upload' i]",
            "button:has-text('Tải video lên')",
            "button:has-text('Upload videos')",
        ]
        if self._click_first(page, direct_selectors, timeout_ms=3000):
            page.wait_for_timeout(1000)
            return

        create_selectors = [
            "ytcp-button:has-text('Tạo')",
            "ytcp-button:has-text('Create')",
            "#create-icon",
            "ytcp-button#create-icon",
            "button[aria-label*='Tạo' i]",
            "button[aria-label*='Create' i]",
            "button[title*='Tạo' i]",
            "button[title*='Create' i]",
            "button:has-text('Tạo')",
            "button:has-text('Create')",
        ]
        if not self._click_first(page, create_selectors, timeout_ms=10_000):
            raise YouTubeError("Không tìm thấy nút Tạo / Tải video lên trong YouTube Studio.")
        page.wait_for_timeout(700)

    def _attach_video(self, page, video: Path) -> None:
        file_input = page.locator('input[type="file"]').first
        if file_input.count():
            try:
                file_input.set_input_files(str(video))
                return
            except Exception:
                pass

        item_selectors = [
            "tp-yt-paper-item#text-item-0",
            "tp-yt-paper-item:has-text('Tải video lên')",
            "tp-yt-paper-item:has-text('Upload videos')",
            "#text-item-0",
            "#select-files-button",
            "ytcp-button#select-files-button",
            "button:has-text('Chọn tệp')",
            "button:has-text('Select files')",
        ]
        try:
            with page.expect_file_chooser(timeout=10_000) as chooser:
                if not self._click_first(page, item_selectors, 5000):
                    try:
                        page.get_by_text(re.compile(r"Tải video lên|Upload video", re.I)).first.click(timeout=4000)
                    except Exception:
                        pass
            chooser.value.set_files(str(video))
            return
        except Exception:
            logger.debug("Không bắt được file chooser, thử input[type=file].", exc_info=True)

        try:
            file_input = page.locator('input[type="file"]').first
            file_input.wait_for(state="attached", timeout=15000)
            file_input.set_input_files(str(video))
            return
        except Exception as exc:
            raise YouTubeError("Không đưa được file video vào hộp thoại upload.") from exc

    def _handle_auth_dialog(self, page, cfg: YouTubeConfig,
                            report: Callable[[int, str], None],
                            cancel_cb: CancelCb) -> None:
        page.wait_for_timeout(1000)
        auth_candidates = [
            "ytcp-auth-confirmation-dialog",
            "tp-yt-paper-dialog:has-text('Xác minh danh tính')",
            "tp-yt-paper-dialog:has-text('Verify your identity')",
            "tp-yt-paper-dialog:has-text('Verify it\'s you')",
        ]
        auth_dlg = None
        for sel in auth_candidates:
            try:
                cand = page.locator(sel).first
                if cand.count() and cand.is_visible():
                    auth_dlg = cand
                    break
            except Exception:
                continue
        if auth_dlg is None:
            return

        if cfg.browser_headless:
            raise AuthRequired(
                "YouTube Studio yêu cầu xác minh danh tính ('Xác minh danh tính của bạn'). "
                "Hãy tắt chế độ chạy ẩn (headless), mở Chrome đăng nhập và xác minh danh tính rồi thử lại."
            )
        report(12, "YouTube yêu cầu xác minh danh tính ('Xác minh danh tính của bạn'). "
                   "Vui lòng hoàn tất xác minh trên cửa sổ Chrome vừa mở (chờ tối đa 5 phút)...")
        next_btn = auth_dlg.locator(
            "button:has-text('Tiếp theo'), button:has-text('Next'), ytcp-button#next-button"
        ).first
        if next_btn.count() and next_btn.is_visible():
            try:
                next_btn.click(force=True)
            except Exception:
                pass
        deadline = time.time() + 300
        while time.time() < deadline:
            self._check_cancel(cancel_cb)
            page.wait_for_timeout(2000)
            try:
                if not auth_dlg.is_visible():
                    report(15, "Đã xác minh danh tính xong, tiếp tục tải lên...")
                    return
            except Exception:
                return
        raise AuthRequired("Chưa hoàn tất bước xác minh danh tính trên YouTube Studio sau 5 phút.")

    def _fill_browser_metadata(self, page, metadata: Dict[str, Any],
                               warnings: List[str],
                               report: Callable[[int, str], None]) -> None:
        title_box = self._first(page, [
            'div[contenteditable="true"][aria-label*="tiêu đề" i]',
            'div[contenteditable="true"][aria-label*="title" i]',
            'ytcp-social-suggestions-textbox[aria-label*="tiêu đề" i] #textbox',
            'ytcp-social-suggestions-textbox[aria-label*="title" i] #textbox',
            'ytcp-video-title #textbox',
            'ytcp-uploads-dialog #textbox',
            '#textbox',
        ], 20_000)
        if title_box is None:
            raise YouTubeError("Không tìm thấy ô tiêu đề của YouTube Studio.")
        self._fill_editable(title_box, metadata["title"])

        desc_box = self._first(page, [
            'div[contenteditable="true"][aria-label*="người xem" i]',
            'div[contenteditable="true"][aria-label*="Tell viewers" i]',
            'div[contenteditable="true"][aria-label*="mô tả" i]',
            'div[contenteditable="true"][aria-label*="description" i]',
            '#description-textarea #textbox',
            'ytcp-video-description #textbox',
            'ytcp-social-suggestions-textbox #textbox',
        ], 5000)
        if desc_box is None and page.locator("#textbox").count() >= 2:
            desc_box = page.locator("#textbox").nth(1)
        if desc_box is not None:
            self._fill_editable(desc_box, metadata["description"])
        else:
            warnings.append("Không tìm thấy ô mô tả.")

        try:
            mfk = page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').first
            if mfk.count():
                try:
                    mfk.scroll_into_view_if_needed()
                except Exception:
                    pass
                mfk.click(force=True)
            else:
                page.get_by_role("radio", name=re.compile(r"not made for kids|không dành cho trẻ em", re.I)).first.click(force=True, timeout=3000)
        except Exception:
            try:
                page.get_by_role("radio", name=re.compile(r"not made for kids|không dành cho trẻ em", re.I)).first.click(force=True, timeout=3000)
            except Exception:
                warnings.append("Không chọn được tuỳ chọn 'không dành cho trẻ em'.")

        thumb = metadata.get("thumbnail")
        if thumb is not None and Path(thumb).is_file():
            try:
                thumb_input = page.locator('input#file-loader, input[type="file"][accept*="image"]').first
                if thumb_input.count():
                    thumb_input.set_input_files(str(thumb))
                    page.wait_for_timeout(1500)
                else:
                    with page.expect_file_chooser(timeout=4000) as chooser:
                        page.get_by_text(re.compile(r"Upload thumbnail|Tải hình thu nhỏ|Tải ảnh thu nhỏ", re.I)).first.click(force=True, timeout=4000)
                    chooser.value.set_files(str(thumb))
                    page.wait_for_timeout(1500)
            except Exception:
                warnings.append("Không đặt được thumbnail trong Studio.")
        self._browser_progress(page, report)

    @staticmethod
    def _fill_editable(locator, text: str) -> None:
        try:
            locator.click()
            locator.fill(text)
        except Exception:
            locator.click()
            locator.page.keyboard.press("Control+A")
            locator.page.keyboard.type(text, delay=2)

    def _browser_progress(self, page, report: Callable[[int, str], None]) -> None:
        locator = None
        for selector in ("#progress-label", "ytcp-video-upload-progress #progress-label",
                         ".progress-label"):
            try:
                candidate = page.locator(selector).first
                if candidate.count():
                    locator = candidate
                    break
            except Exception:
                continue
        if locator is None:
            return
        try:
            text = locator.inner_text(timeout=1000)
        except Exception:
            return
        match = re.search(r"(\d+)\s*%", text or "")
        if match:
            pct = min(70, 16 + int(int(match.group(1)) * 0.54))
            report(pct, f"YouTube Studio: {text.strip()}")

    def _browser_next(self, page, step: str) -> None:
        button = self._first(page, [
            "#next-button",
            "ytcp-button#next-button",
            "button:has-text('Tiếp')",
            "button:has-text('Next')",
        ], 10_000)
        if button is None:
            raise YouTubeError(f"Không thấy nút Next ở bước {step}.")
        try:
            button.click(force=True)
        except Exception as exc:
            try:
                button.locator("button, div#button").first.click(force=True)
            except Exception:
                raise YouTubeError(f"Không bấm được Next ở bước {step}: {exc}") from exc
        page.wait_for_timeout(1200)

    def _wait_done_ready(self, page, cancel_cb: CancelCb,
                         report: Callable[[int, str], None]) -> None:
        """Wait until Studio finishes processing and enables Publish/Schedule."""
        deadline = time.time() + 600
        while time.time() < deadline:
            self._check_cancel(cancel_cb)
            try:
                button = page.locator("#done-button, ytcp-button#done-button").first
                if button.count() and button.is_visible():
                    aria_dis = button.get_attribute("aria-disabled")
                    if aria_dis != "true" and button.is_enabled():
                        return
            except Exception:
                pass
            self._browser_progress(page, report)
            page.wait_for_timeout(1000)
        raise YouTubeError("YouTube Studio xử lý quá lâu, chưa thể bấm Publish/Schedule.")

    def _browser_visibility(self, page, publish_at: str, privacy: str,
                            warnings: List[str],
                            report: Callable[[int, str], None]) -> str:
        scheduled = ""
        if publish_at:
            scheduled = publish_at
            try:
                radio = page.locator('tp-yt-paper-radio-button[name="SCHEDULE"]').first
                if not radio.count():
                    radio = page.get_by_text(re.compile(r"Lên lịch|Schedule", re.I)).first
                radio.click(force=True, timeout=4000)
                when = parse_when(publish_at) or _local_now()
                local = when.astimezone()
                picker = page.locator("ytcp-datetime-picker").first
                inputs = picker.locator("input")
                if inputs.count() >= 2:
                    date_text = local.strftime("%b %d, %Y")
                    time_text = local.strftime("%I:%M %p")
                    inputs.nth(0).click()
                    inputs.nth(0).fill(date_text)
                    inputs.nth(0).press("Enter")
                    inputs.nth(1).click()
                    inputs.nth(1).fill(time_text)
                    inputs.nth(1).press("Enter")
                report(80, f"Đã chọn lịch đăng {local:%d/%m/%Y %H:%M}.")
                return scheduled
            except Exception as exc:
                warnings.append(f"Không đặt được lịch trong Studio ({exc}); đăng riêng tư.")
                scheduled = ""
        name = {"public": "PUBLIC", "unlisted": "UNLISTED"}.get(privacy, "PRIVATE")
        try:
            radio = page.locator(f'tp-yt-paper-radio-button[name="{name}"]').first
            if radio.count():
                radio.click(force=True, timeout=4000)
            else:
                label_patterns = {
                    "PUBLIC": r"Công khai|Public",
                    "UNLISTED": r"Không công khai|Unlisted",
                    "PRIVATE": r"Riêng tư|Private",
                }
                pattern = label_patterns.get(name, r"Riêng tư|Private")
                page.get_by_text(re.compile(pattern, re.I)).first.click(force=True, timeout=3000)
        except Exception:
            try:
                label = {"PUBLIC": "Public", "UNLISTED": "Unlisted"}.get(name, "Private")
                page.get_by_text(label, exact=True).first.click(force=True, timeout=3000)
            except Exception:
                warnings.append("Không chọn được chế độ hiển thị; giữ mặc định của Studio.")
        return scheduled

    def _browser_video_url(self, page) -> str:
        deadline = time.time() + 120
        closed_at: Optional[float] = None
        while time.time() < deadline:
            for selector in ("a[href*='youtu.be/']", "a[href*='youtube.com/watch']",
                             "a.ytcp-video-info", "#video-url", ".video-url-fadeable"):
                try:
                    locator = page.locator(selector).first
                    if locator.count():
                        href = locator.get_attribute("href", timeout=1000) or ""
                        text = href or (locator.inner_text(timeout=1000) or "")
                        if "youtu" in text:
                            return text.strip()
                except Exception:
                    continue
            try:
                dialog = page.locator("ytcp-uploads-dialog").first
                if dialog.count() and not dialog.is_visible():
                    closed_at = closed_at or time.time()
                    if time.time() - closed_at > 2.0:
                        break
                else:
                    closed_at = None
            except Exception:
                pass
            page.wait_for_timeout(500)
        try:
            content = page.content()
            m = re.search(r"https://youtu\.be/([A-Za-z0-9_-]{11})", content)
            if m:
                return m.group(0)
        except Exception:
            pass
        return ""

    def _browser_screenshot(self, page, prefix: str) -> None:
        if page is None:
            return
        try:
            logs = Path(self.settings.get("app", {}).get("log_dir") or "logs")
            if not logs.is_absolute():
                logs = ROOT / logs
            ensure_dir(logs)
            target = logs / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
            page.screenshot(path=str(target), full_page=False)
            logger.info("Đã lưu ảnh lỗi trình duyệt: %s", target)
        except Exception:
            pass

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _wait(pause_cb: PauseCb, cancel_cb: CancelCb) -> None:
        while pause_cb is not None and pause_cb():
            if cancel_cb is not None and cancel_cb():
                raise UploadCancelled("Đã huỷ upload.")
            time.sleep(0.2)
        if cancel_cb is not None and cancel_cb():
            raise UploadCancelled("Đã huỷ upload.")

    @staticmethod
    def _check_cancel(cancel_cb: CancelCb) -> None:
        if cancel_cb is not None and cancel_cb():
            raise UploadCancelled("Đã huỷ upload.")

    @staticmethod
    def _sleep(seconds: float, cancel_cb: CancelCb) -> None:
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if cancel_cb is not None and cancel_cb():
                raise UploadCancelled("Đã huỷ upload.")
            time.sleep(min(0.2, max(0.0, deadline - time.time())))

    @staticmethod
    def _retryable(exc) -> bool:
        status = getattr(getattr(exc, "resp", None), "status", None)
        try:
            reason = YouTubeUploader._http_reason(exc)
        except Exception:
            reason = ""
        if reason in ("rateLimitExceeded", "userRateLimitExceeded", "backendError"):
            return True
        return status in (429, 500, 502, 503, 504)

    @staticmethod
    def _http_payload(exc) -> Dict[str, Any]:
        content = getattr(exc, "content", b"") or b""
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        try:
            return json.loads(content) if content else {}
        except ValueError:
            return {}

    @staticmethod
    def _http_reason(exc) -> str:
        payload = YouTubeUploader._http_payload(exc)
        error = payload.get("error") or {}
        errors = error.get("errors") or []
        if errors and isinstance(errors[0], dict):
            return str(errors[0].get("reason") or "")
        return str(error.get("status") or "")

    @staticmethod
    def _http_message(exc) -> str:
        status = getattr(getattr(exc, "resp", None), "status", "")
        reason = YouTubeUploader._http_reason(exc)
        payload = YouTubeUploader._http_payload(exc)
        message = str((payload.get("error") or {}).get("message") or exc)
        if reason == "quotaExceeded":
            return "Đã hết quota YouTube API hôm nay. Thử lại sau hoặc dùng kênh khác."
        if reason == "uploadLimitExceeded":
            return "Kênh đã đạt giới hạn số video tải lên hôm nay."
        return f"YouTube API lỗi {status} ({reason or 'unknown'}): {message}"
