# yt2pod

YouTube-to-podcast RSS server. Point it at a channel, get a feed URL you can paste into Apple Podcasts.

It downloads videos with yt-dlp, stores them on disk, and serves an RSS 2.0 feed with the iTunes namespace. Enclosures are MP4 video when YouTube allows it, m4a audio if video fails. Shorts are dropped unless you turn that off.

Anyone with `/feed.xml` can play it. On a phone, use this computer's LAN IP, not `localhost`.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on `PATH` (yt-dlp needs it to mux MP4)
- A URL Apple Podcasts can actually reach. `localhost` works on the same Mac. An iPhone needs your LAN IP, Tailscale, or a public HTTPS URL.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```bash
YOUTUBE_CHANNEL=https://www.youtube.com/@veritasium
BASE_URL=https://pod.example.com
```

`BASE_URL` must be the URL clients use to hit this server, not `localhost`, unless the client is that same machine.

Start it:

```bash
python main.py
```

The process prints the feed URL. First refresh downloads the latest videos and can take a while. Check progress:

```bash
curl -s http://127.0.0.1:8000/status
```

Docker if you would rather not install ffmpeg locally:

```bash
cp .env.example .env   # then edit it
docker compose up --build
```

Media lives in `./data`.

## Add the feed in Apple Podcasts

Wait until `/status` shows at least one episode, then:

**iPhone or iPad**

1. Open Podcasts
2. Tap Library
3. Tap the More button (`...`)
4. Tap Follow a Show by URL
5. Paste `http://YOUR-LAN-IP:8000/feed.xml` (not localhost)
6. Tap Follow

**Mac**

1. Open Podcasts
2. File → Follow a Show by URL
3. Paste the same URL

Apple Podcasts wants a stable HTTPS URL with a real certificate. HTTP on a LAN IP sometimes works, localhost on an iPhone does not. Cloudflare Tunnel, Caddy, or Tailscale Funnel are enough.

If episodes show up as audio, the YouTube source had no H.264 MP4 and yt2pod fell back to m4a. Check `"mime": "video/mp4"` on `/status`.

## Config

All settings are env vars. `.env` is loaded automatically.

| Variable | Default | What it does |
|---|---|---|
| `YOUTUBE_CHANNEL` | required | Channel URL, `@handle`, or handle |
| `BASE_URL` | `http://localhost:8000` | Public origin used in RSS enclosure URLs |
| `FEED_TITLE` | channel name | Show title |
| `FEED_DESCRIPTION` | channel description | Show description |
| `FEED_AUTHOR` | channel name | `itunes:author` |
| `FEED_LANGUAGE` | `en-us` | RSS language |
| `FEED_CATEGORY` | `TV & Film` | iTunes category |
| `FEED_ARTWORK` | channel avatar | Square JPEG/PNG path or URL. Apple likes 1400–3000px |
| `ITUNES_EXPLICIT` | `false` | `true` / `false` |
| `MAX_EPISODES` | `10` | Newest videos to keep. Videos are large |
| `MAX_HEIGHT` | `720` | Cap download resolution |
| `EXCLUDE_SHORTS` | `true` | Use the `/videos` tab and skip sub-61s clips |
| `MIN_DURATION` | `61` | Seconds. Used when excluding Shorts |
| `REFRESH_HOURS` | `6` | Background poll. `0` means only manual / on-request |
| `REFRESH_ON_START` | `true` | Download as soon as the process boots |
| `COOKIES_FILE` | empty | Netscape cookies file if YouTube starts asking for a login |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address |
| `DATA_DIR` | `./data` | State, artwork, and media |

## Refresh

New videos are pulled on a timer (`REFRESH_HOURS`) and whenever the feed is fetched after that interval. You can also kick it:

```bash
curl http://127.0.0.1:8000/refresh
```

Cron on the host is optional:

```
0 */6 * * * curl -fsS https://pod.example.com/refresh
```

Files for episodes that fall off `MAX_EPISODES` are deleted.

## Endpoints

| Path | Purpose |
|---|---|
| `/feed.xml` | RSS 2.0 |
| `/media/{file}` | Episode file (Range requests work) |
| `/artwork` | Show art |
| `/refresh` | Start a fetch |
| `/status` | Episode list and refresh state |
| `/health` | Liveness |

## YouTube blocking yt-dlp

YouTube rate-limits anonymous downloads. Export cookies from a logged-in browser to a Netscape cookies.txt and set `COOKIES_FILE`. Update yt-dlp when extracts start failing:

```bash
pip install -U yt-dlp
```

## Tests

```bash
python -m unittest discover -s tests
```

## Personal use

yt2pod downloads YouTube media and rehosts it. That may violate YouTube's terms. Only use channels you have the right to copy.
