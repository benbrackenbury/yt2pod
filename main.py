from __future__ import annotations

import logging
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from yt2pod import (
    Channel,
    build_rss,
    load_settings,
    load_state,
    mime_for_path,
    refresh_status,
    run_refresh,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("yt2pod")

settings = load_settings()


def _channel(slug: str) -> Channel:
    channel = settings.channels.get(slug)
    if not channel:
        raise HTTPException(status_code=404, detail="Not found")
    return channel


def _maybe_refresh(slug: str) -> None:
    state = load_state(settings.state_path(slug))
    last = state.get("last_refresh")
    if not last:
        threading.Thread(target=run_refresh, args=(settings, slug), daemon=True).start()
        return
    if settings.refresh_hours <= 0:
        return
    try:
        seen = datetime.fromisoformat(last)
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - seen).total_seconds() / 3600
        if age_hours >= settings.refresh_hours:
            threading.Thread(target=run_refresh, args=(settings, slug), daemon=True).start()
    except ValueError:
        threading.Thread(target=run_refresh, args=(settings, slug), daemon=True).start()


def _refresh_loop() -> None:
    if settings.refresh_on_start:
        run_refresh(settings)
    while True:
        hours = settings.refresh_hours
        time.sleep(3600 if hours <= 0 else max(hours, 0.05) * 3600)
        if settings.refresh_hours > 0:
            run_refresh(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg not found on PATH. Video downloads will fail until it is installed.")
    for slug in settings.channels:
        log.info("Feed URL: %s", settings.feed_url(slug))
    log.info("In Apple Podcasts: Library, More, Follow a Show by URL")
    threading.Thread(target=_refresh_loop, daemon=True).start()
    yield


app = FastAPI(title="yt2pod", docs_url=None, redoc_url=None, lifespan=lifespan)


def _origin(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _feeds(origin: str) -> dict[str, str]:
    return {slug: settings.feed_url(slug, origin) for slug in settings.channels}


def _rss(request: Request, slug: str) -> Response:
    channel = _channel(slug)
    _maybe_refresh(slug)
    body = build_rss(settings, channel, load_state(settings.state_path(slug)), origin=_origin(request))
    return Response(
        content=body,
        media_type="application/rss+xml; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Content-Length": str(len(body)),
        },
    )


def _media(slug: str, filename: str) -> FileResponse:
    _channel(slug)
    name = Path(filename).name
    if name != filename:
        raise HTTPException(status_code=404, detail="Not found")
    path = settings.media_dir(slug) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type=mime_for_path(path),
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _artwork(slug: str) -> FileResponse:
    _channel(slug)
    path = settings.artwork_path(slug)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/jpeg")


def _status_payload(request: Request, slug: str) -> dict:
    _channel(slug)
    state = load_state(settings.state_path(slug))
    live = refresh_status()
    return {
        "slug": slug,
        "feed_url": settings.feed_url(slug, _origin(request)),
        "last_refresh": state.get("last_refresh"),
        "episodes": [
            {"id": ep.get("id"), "title": ep.get("title"), "mime": ep.get("mime")}
            for ep in state.get("episodes") or []
        ],
        **live,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index(request: Request) -> dict:
    return {"feeds": _feeds(_origin(request))}


@app.api_route("/refresh", methods=["GET", "POST"])
def refresh_all(background_tasks: BackgroundTasks) -> dict[str, str]:
    if refresh_status()["running"]:
        return {"status": "already-running"}
    background_tasks.add_task(run_refresh, settings)
    return {"status": "started"}


@app.get("/status")
def status_all(request: Request) -> dict:
    origin = _origin(request)
    live = refresh_status()
    return {
        "feeds": _feeds(origin),
        "channels": {
            slug: {
                "feed_url": settings.feed_url(slug, origin),
                "last_refresh": load_state(settings.state_path(slug)).get("last_refresh"),
                "episodes": len(load_state(settings.state_path(slug)).get("episodes") or []),
            }
            for slug in settings.channels
        },
        **live,
    }


@app.api_route("/{slug}/feed.xml", methods=["GET", "HEAD"])
def feed(slug: str, request: Request) -> Response:
    return _rss(request, slug)


@app.api_route("/{slug}/media/{filename}", methods=["GET", "HEAD"])
def media(slug: str, filename: str) -> FileResponse:
    return _media(slug, filename)


@app.api_route("/{slug}/artwork.jpg", methods=["GET", "HEAD"])
@app.api_route("/{slug}/artwork", methods=["GET", "HEAD"])
def artwork(slug: str) -> FileResponse:
    return _artwork(slug)


@app.api_route("/{slug}/refresh", methods=["GET", "POST"])
def refresh_one(slug: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    _channel(slug)
    if refresh_status()["running"]:
        return {"status": "already-running"}
    background_tasks.add_task(run_refresh, settings, slug)
    return {"status": "started", "slug": slug}


@app.get("/{slug}/status")
def status_one(slug: str, request: Request) -> dict:
    return _status_payload(request, slug)


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
