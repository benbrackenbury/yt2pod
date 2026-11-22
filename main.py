from __future__ import annotations

import logging
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from yt2pod import (
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


def _maybe_refresh() -> None:
    state = load_state(settings.state_path)
    last = state.get("last_refresh")
    if not last:
        threading.Thread(target=run_refresh, args=(settings,), daemon=True).start()
        return
    if settings.refresh_hours <= 0:
        return
    try:
        seen = datetime.fromisoformat(last)
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - seen).total_seconds() / 3600
        if age_hours >= settings.refresh_hours:
            threading.Thread(target=run_refresh, args=(settings,), daemon=True).start()
    except ValueError:
        threading.Thread(target=run_refresh, args=(settings,), daemon=True).start()


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
    log.info("Feed URL: %s", settings.feed_url)
    log.info("In Apple Podcasts: Library, More, Follow a Show by URL")
    threading.Thread(target=_refresh_loop, daemon=True).start()
    yield


app = FastAPI(title="yt2pod", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/feed.xml")
def feed() -> Response:
    _maybe_refresh()
    body = build_rss(settings, load_state(settings.state_path))
    return Response(
        content=body,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/media/{filename}")
def media(filename: str) -> FileResponse:
    name = Path(filename).name
    if name != filename:
        raise HTTPException(status_code=404, detail="Not found")
    path = settings.media_dir / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type=mime_for_path(path),
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/artwork")
def artwork() -> FileResponse:
    path = settings.artwork_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/jpeg")


@app.api_route("/refresh", methods=["GET", "POST"])
def refresh_now(background_tasks: BackgroundTasks) -> dict[str, str]:
    if refresh_status()["running"]:
        return {"status": "already-running"}
    background_tasks.add_task(run_refresh, settings)
    return {"status": "started"}


@app.get("/status")
def status() -> dict:
    state = load_state(settings.state_path)
    live = refresh_status()
    return {
        "feed_url": settings.feed_url,
        "last_refresh": state.get("last_refresh"),
        "episodes": [
            {"id": ep.get("id"), "title": ep.get("title"), "mime": ep.get("mime")}
            for ep in state.get("episodes") or []
        ],
        **live,
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
