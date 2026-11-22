from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

log = logging.getLogger("yt2pod")

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"
VIDEO_ID_RE = re.compile(r"^[\w-]{6,20}$")
CHANNEL_HOME_RE = re.compile(
    r"^https?://(?:www\.)?youtube\.com/(@[^/]+|channel/[\w-]+|c/[\w-]+|user/[\w-]+)/?$",
    re.I,
)
CONTROL_CHARS = dict.fromkeys(range(32))
for _keep in (9, 10, 13):
    CONTROL_CHARS.pop(_keep, None)

MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".m4a": "audio/x-m4a",
    ".mp3": "audio/mpeg",
}

_refresh_lock = threading.Lock()
_refresh_running = False
_refresh_error: str | None = None
_refresh_current: str | None = None

ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("atom", ATOM_NS)


@dataclass
class Settings:
    youtube_channel: str
    base_url: str
    feed_title: str = ""
    feed_description: str = ""
    feed_author: str = ""
    feed_language: str = "en-us"
    feed_category: str = "TV & Film"
    feed_artwork: str = ""
    itunes_explicit: str = "false"
    max_episodes: int = 10
    max_height: int = 720
    exclude_shorts: bool = True
    min_duration: int = 61
    refresh_hours: float = 6
    refresh_on_start: bool = True
    cookies_file: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = field(default_factory=lambda: Path("./data"))

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def artwork_path(self) -> Path:
        return self.data_dir / "artwork.jpg"

    def origin(self, override: str | None = None) -> str:
        return (override or self.base_url).rstrip("/")

    def feed_url(self, origin: str | None = None) -> str:
        return f"{self.origin(origin)}/feed.xml"

    def media_url(self, filename: str, origin: str | None = None) -> str:
        return f"{self.origin(origin)}/media/{filename}"

    def artwork_url(self, origin: str | None = None) -> str:
        return f"{self.origin(origin)}/artwork.jpg"

    def ensure_dirs(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def load_settings() -> Settings:
    load_dotenv()
    channel = _env("YOUTUBE_CHANNEL")
    base_url = _env("BASE_URL", "http://localhost:8000").rstrip("/")
    if not channel:
        raise SystemExit("Set YOUTUBE_CHANNEL to a YouTube URL or @handle")
    settings = Settings(
        youtube_channel=channel,
        base_url=base_url,
        feed_title=_env("FEED_TITLE"),
        feed_description=_env("FEED_DESCRIPTION"),
        feed_author=_env("FEED_AUTHOR"),
        feed_language=_env("FEED_LANGUAGE", "en-us") or "en-us",
        feed_category=_env("FEED_CATEGORY", "TV & Film") or "TV & Film",
        feed_artwork=_env("FEED_ARTWORK"),
        itunes_explicit=_env("ITUNES_EXPLICIT", "false") or "false",
        max_episodes=max(1, _env_int("MAX_EPISODES", 10)),
        max_height=max(144, _env_int("MAX_HEIGHT", 720)),
        exclude_shorts=_env_bool("EXCLUDE_SHORTS", True),
        min_duration=max(1, _env_int("MIN_DURATION", 61)),
        refresh_hours=_env_float("REFRESH_HOURS", 6),
        refresh_on_start=_env_bool("REFRESH_ON_START", True),
        cookies_file=_env("COOKIES_FILE"),
        host=_env("HOST", "0.0.0.0") or "0.0.0.0",
        port=_env_int("PORT", 8000),
        data_dir=Path(_env("DATA_DIR", "./data") or "./data"),
    )
    settings.ensure_dirs()
    return settings


def normalize_channel(raw: str, exclude_shorts: bool = True) -> str:
    s = raw.strip()
    if s.startswith("@"):
        s = "https://www.youtube.com/" + s
    elif re.fullmatch(r"[\w.-]+", s):
        s = "https://www.youtube.com/@" + s
    if not re.match(r"^https?://", s, re.I):
        s = "https://www.youtube.com/" + s.lstrip("/")
    s = re.sub(r"^http://", "https://", s, flags=re.I)
    s = s.split("?")[0].rstrip("/")
    if exclude_shorts and CHANNEL_HOME_RE.match(s):
        s += "/videos"
    return s


def is_short(entry: dict[str, Any], min_duration: int = 61) -> bool:
    if entry.get("is_short") is True:
        return True
    for key in ("url", "original_url", "webpage_url"):
        value = str(entry.get(key) or "")
        if "/shorts/" in value:
            return True
    duration = entry.get("duration")
    if duration is not None:
        try:
            if float(duration) < min_duration:
                return True
        except (TypeError, ValueError):
            pass
    return False


def mime_for_path(path: Path) -> str:
    return MIME.get(path.suffix.lower(), "application/octet-stream")


def clean_xml_text(value: str | None, limit: int = 4000) -> str:
    text = (value or "").translate(CONTROL_CHARS).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def pub_date(upload_date: str | None, timestamp: int | float | None) -> str:
    if timestamp:
        return format_datetime(datetime.fromtimestamp(int(timestamp), tz=timezone.utc))
    if upload_date:
        dt = datetime.strptime(str(upload_date), "%Y%m%d").replace(tzinfo=timezone.utc)
        return format_datetime(dt)
    return format_datetime(datetime.now(timezone.utc))


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"episodes": [], "title": "", "author": "", "description": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def refresh_status() -> dict[str, Any]:
    return {
        "running": _refresh_running,
        "error": _refresh_error,
        "current": _refresh_current,
    }


def _ytdl_opts(settings: Settings, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": False,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "ignoreerrors": False,
        "noplaylist": False,
    }
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    if extra:
        opts.update(extra)
    return opts


def _extract(settings: Settings, url: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    import yt_dlp

    with yt_dlp.YoutubeDL(_ytdl_opts(settings, extra)) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError(f"yt-dlp returned no info for {url}")
    return info


def list_channel(settings: Settings) -> dict[str, Any]:
    url = normalize_channel(settings.youtube_channel, settings.exclude_shorts)
    limit = settings.max_episodes * 4 if settings.exclude_shorts else settings.max_episodes
    info = _extract(
        settings,
        url,
        {
            "extract_flat": "in_playlist",
            "playlistend": limit,
            "noplaylist": False,
        },
    )
    raw_entries = info.get("entries")
    if raw_entries is None and info.get("id"):
        raw_entries = [info]
    entries = []
    for entry in raw_entries or []:
        if not entry or not entry.get("id"):
            continue
        if entry.get("live_status") in ("is_live", "is_upcoming"):
            continue
        if settings.exclude_shorts and is_short(entry, settings.min_duration):
            continue
        entries.append(entry)
        if len(entries) >= settings.max_episodes:
            break
    return {
        "url": url,
        "title": info.get("title") or info.get("channel") or info.get("uploader") or "",
        "author": info.get("channel") or info.get("uploader") or "",
        "description": info.get("description") or "",
        "thumbnails": info.get("thumbnails") or [],
        "entries": entries,
    }


def _format_selector(max_height: int) -> str:
    h = max_height
    return "/".join(
        [
            f"bestvideo[height<=?{h}][ext=mp4][vcodec^=avc]+bestaudio[ext=m4a]/best[height<=?{h}][ext=mp4][vcodec^=avc]",
            f"bestvideo[height<=?{h}][ext=mp4]+bestaudio[ext=m4a]/best[height<=?{h}][ext=mp4]",
            f"bestvideo[height<=?{h}]+bestaudio/best[height<=?{h}]",
            "best",
        ]
    )


def _find_media(media_dir: Path, video_id: str) -> Path | None:
    if not VIDEO_ID_RE.fullmatch(video_id):
        return None
    matches = [
        p
        for p in media_dir.glob(f"{video_id}.*")
        if p.suffix.lower() not in {".part", ".ytdl", ".json", ".tmp"}
    ]
    return matches[0] if matches else None


def _remux_if_needed(path: Path) -> Path:
    if path.suffix.lower() in MIME:
        return path
    out = path.with_suffix(".mp4")
    cmd = ["ffmpeg", "-y", "-i", str(path), "-c", "copy", "-movflags", "+faststart", str(out)]
    copied = subprocess.run(cmd, capture_output=True, text=True)
    if copied.returncode == 0 and out.exists() and out.stat().st_size > 0:
        path.unlink(missing_ok=True)
        return out
    audio = path.with_suffix(".m4a")
    audio_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(audio),
    ]
    converted = subprocess.run(audio_cmd, capture_output=True, text=True)
    if converted.returncode == 0 and audio.exists() and audio.stat().st_size > 0:
        path.unlink(missing_ok=True)
        return audio
    log.warning("Could not convert %s for Apple Podcasts: %s", path.name, copied.stderr[-400:])
    return path


def _episode_from_info(info: dict[str, Any], path: Path) -> dict[str, Any]:
    video_id = info.get("id") or path.stem
    return {
        "id": video_id,
        "title": clean_xml_text(info.get("title") or video_id, 200),
        "description": clean_xml_text(info.get("description") or "", 4000),
        "upload_date": info.get("upload_date"),
        "timestamp": info.get("timestamp"),
        "duration": int(info["duration"]) if info.get("duration") else None,
        "filename": path.name,
        "nbytes": path.stat().st_size,
        "mime": mime_for_path(path),
        "link": info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
        "thumbnail": (info.get("thumbnail") or ""),
    }


def download_episode(
    settings: Settings,
    video_id: str,
    cached: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    import yt_dlp

    existing = _find_media(settings.media_dir, video_id)
    if existing and cached and cached.get("filename"):
        cached = dict(cached)
        cached["nbytes"] = existing.stat().st_size
        cached["filename"] = existing.name
        cached["mime"] = mime_for_path(existing)
        return cached
    if existing:
        info = _extract(
            settings,
            f"https://www.youtube.com/watch?v={video_id}",
            {"noplaylist": True},
        )
        if settings.exclude_shorts and is_short(info, settings.min_duration):
            existing.unlink(missing_ok=True)
            return None
        return _episode_from_info(info, existing)

    url = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl = str(settings.media_dir / "%(id)s.%(ext)s")
    attempts = [
        {
            "format": _format_selector(settings.max_height),
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "noplaylist": True,
        },
        {
            "format": "bestaudio[ext=m4a]/bestaudio",
            "outtmpl": outtmpl,
            "noplaylist": True,
        },
    ]
    last_error: Exception | None = None
    for extra in attempts:
        try:
            with yt_dlp.YoutubeDL(_ytdl_opts(settings, extra)) as ydl:
                info = ydl.extract_info(url, download=True)
            if not info:
                continue
            if settings.exclude_shorts and is_short(info, settings.min_duration):
                leftover = _find_media(settings.media_dir, video_id)
                if leftover:
                    leftover.unlink(missing_ok=True)
                return None
            path = _find_media(settings.media_dir, video_id)
            if not path:
                log.warning("Download finished but file missing for %s", video_id)
                continue
            path = _remux_if_needed(path)
            if path.suffix.lower() not in MIME:
                log.warning("Skipping %s, unsupported type %s", video_id, path.suffix)
                return None
            return _episode_from_info(info, path)
        except Exception as exc:
            last_error = exc
            log.warning("Download attempt failed for %s: %s", video_id, exc)
    if last_error:
        log.error("Giving up on %s: %s", video_id, last_error)
    return None


def _best_thumbnail(thumbnails: list[dict[str, Any]]) -> str | None:
    if not thumbnails:
        return None
    ranked = sorted(
        thumbnails,
        key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
        reverse=True,
    )
    return ranked[0].get("url")


def update_artwork(settings: Settings, channel: dict[str, Any]) -> None:
    src = settings.feed_artwork
    if src and Path(src).is_file():
        shutil.copy2(src, settings.artwork_path)
        return
    url = src if src.startswith(("http://", "https://")) else _best_thumbnail(channel.get("thumbnails") or [])
    if not url:
        return
    req = Request(url, headers={"User-Agent": "yt2pod/1.0"})
    with urlopen(req, timeout=30) as resp:
        data = resp.read()
    settings.artwork_path.write_bytes(data)


def refresh(settings: Settings) -> dict[str, Any]:
    global _refresh_current
    channel = list_channel(settings)
    previous = {ep["id"]: ep for ep in load_state(settings.state_path).get("episodes") or [] if ep.get("id")}
    episodes: list[dict[str, Any]] = []
    for entry in channel["entries"]:
        video_id = entry["id"]
        _refresh_current = entry.get("title") or video_id
        log.info("Fetching %s (%s)", _refresh_current, video_id)
        episode = download_episode(settings, video_id, previous.get(video_id))
        if episode:
            episodes.append(episode)
    keep = {ep["filename"] for ep in episodes}
    for path in settings.media_dir.iterdir():
        if path.name not in keep:
            path.unlink(missing_ok=True)
    try:
        update_artwork(settings, channel)
    except Exception as exc:
        log.warning("Artwork update failed: %s", exc)
    state = {
        "title": settings.feed_title or channel["title"] or "YouTube",
        "author": settings.feed_author or channel["author"] or "",
        "description": settings.feed_description or clean_xml_text(channel["description"], 4000),
        "channel_url": channel["url"],
        "last_refresh": datetime.now(timezone.utc).isoformat(),
        "episodes": episodes,
    }
    save_state(settings.state_path, state)
    _refresh_current = None
    log.info("Refresh complete: %s episode(s)", len(episodes))
    return state


def run_refresh(settings: Settings) -> str:
    global _refresh_running, _refresh_error, _refresh_current
    if not _refresh_lock.acquire(blocking=False):
        return "already-running"
    _refresh_running = True
    _refresh_error = None
    try:
        refresh(settings)
        return "ok"
    except Exception as exc:
        _refresh_error = str(exc)
        log.exception("Refresh failed")
        return "error"
    finally:
        _refresh_current = None
        _refresh_running = False
        _refresh_lock.release()


def _sub(parent: ET.Element, tag: str, body: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: v for k, v in attrs.items() if v is not None})
    if body is not None:
        el.text = body
    return el


def build_rss(settings: Settings, state: dict[str, Any], origin: str | None = None) -> bytes:
    title = clean_xml_text(state.get("title") or settings.feed_title or "yt2pod", 200)
    author = clean_xml_text(state.get("author") or settings.feed_author or title, 200)
    description = clean_xml_text(
        state.get("description") or settings.feed_description or title,
        4000,
    ) or title
    explicit = settings.itunes_explicit.lower()
    if explicit not in ("true", "false", "yes", "no", "explicit", "clean"):
        explicit = "false"

    rss = ET.Element("rss", {"version": "2.0"})
    channel = _sub(rss, "channel")
    _sub(channel, "title", title)
    _sub(channel, "link", state.get("channel_url") or settings.origin(origin))
    _sub(channel, "description", description)
    _sub(channel, "language", settings.feed_language)
    _sub(channel, "generator", "yt2pod")
    _sub(channel, "ttl", "60")
    _sub(channel, "lastBuildDate", format_datetime(datetime.now(timezone.utc)))
    _sub(
        channel,
        f"{{{ATOM_NS}}}link",
        href=settings.feed_url(origin),
        rel="self",
        type="application/rss+xml",
    )
    _sub(channel, f"{{{ITUNES_NS}}}author", author)
    _sub(channel, f"{{{ITUNES_NS}}}summary", description)
    _sub(channel, f"{{{ITUNES_NS}}}explicit", explicit)
    _sub(channel, f"{{{ITUNES_NS}}}type", "episodic")
    _sub(channel, f"{{{ITUNES_NS}}}category", text=settings.feed_category)
    artwork_href = None
    if settings.artwork_path.exists():
        artwork_href = settings.artwork_url(origin)
    elif settings.feed_artwork.startswith(("http://", "https://")):
        artwork_href = settings.feed_artwork
    if artwork_href:
        _sub(channel, f"{{{ITUNES_NS}}}image", href=artwork_href)
        image = _sub(channel, "image")
        _sub(image, "url", artwork_href)
        _sub(image, "title", title)
        _sub(image, "link", state.get("channel_url") or settings.origin(origin))

    for ep in state.get("episodes") or []:
        item = _sub(channel, "item")
        ep_title = clean_xml_text(ep.get("title") or ep.get("id"), 200)
        ep_desc = clean_xml_text(ep.get("description") or ep_title, 4000)
        _sub(item, "title", ep_title)
        _sub(item, "description", ep_desc)
        _sub(item, "link", ep.get("link") or settings.origin(origin))
        guid = _sub(item, "guid", f"yt2pod-{ep['id']}")
        guid.set("isPermaLink", "false")
        _sub(item, "pubDate", pub_date(ep.get("upload_date"), ep.get("timestamp")))
        _sub(
            item,
            "enclosure",
            url=settings.media_url(ep["filename"], origin),
            length=str(int(ep.get("nbytes") or 0)),
            type=ep.get("mime") or mime_for_path(Path(ep["filename"])),
        )
        _sub(item, f"{{{ITUNES_NS}}}title", ep_title)
        _sub(item, f"{{{ITUNES_NS}}}summary", ep_desc)
        _sub(item, f"{{{ITUNES_NS}}}explicit", explicit)
        _sub(item, f"{{{ITUNES_NS}}}author", author)
        if ep.get("duration"):
            _sub(item, f"{{{ITUNES_NS}}}duration", str(int(ep["duration"])))
        thumb = ep.get("thumbnail")
        if thumb:
            _sub(item, f"{{{ITUNES_NS}}}image", href=thumb)

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
