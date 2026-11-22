# yt2pod

YouTube-to-podcast RSS server. List channels in `channels.json`, get a feed URL per channel for Apple Podcasts.

It downloads videos with yt-dlp, stores them on disk, and serves RSS 2.0 with the iTunes namespace. Enclosures are MP4 when YouTube allows it, m4a if video fails. Shorts are dropped unless you turn that off.

On a phone, use this computer's LAN IP or a Tailscale/HTTPS URL, not `localhost`.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on `PATH` (yt-dlp needs it to mux MP4)
- A URL Apple Podcasts can actually reach

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp channels.example.json channels.json
```

Edit `channels.json`. The object key is the URL slug:

```json
{
  "veritasium": "https://www.youtube.com/@veritasium",
  "mkbhd": {
    "youtube": "@mkbhd",
    "title": "MKBHD"
  }
}
```

That gives:

- `http://127.0.0.1:8000/veritasium/feed.xml`
- `http://127.0.0.1:8000/mkbhd/feed.xml`

Optional per-channel fields: `youtube` (or a bare URL string), `title`, `description`, `author`, `artwork`.

`BASE_URL` in `.env` is the fallback origin. Feeds also pick up the Host header from the request.

Start it:

```bash
python main.py
```

Or Docker:

```bash
docker compose up --build
```

Media lives in `./data/<slug>/`. First refresh downloads videos and can take a while. List feeds:

```bash
curl -s http://127.0.0.1:8000/
```

## Add a feed in Apple Podcasts

Wait until `/<slug>/status` shows episodes, then paste that channel's feed URL.

**iPhone or iPad:** Library → More (`...`) → Follow a Show by URL

**Mac:** File → Follow a Show by URL

Use `http://YOUR-LAN-IP:8000/veritasium/feed.xml`, not localhost, from a phone. Apple often wants HTTPS.

## Config

Channel list is `channels.json` (`CHANNELS_FILE` to override). Shared settings are env vars:

| Variable | Default | What it does |
|---|---|---|
| `CHANNELS_FILE` | `./channels.json` | Slug → YouTube channel map |
| `YOUTUBE_CHANNEL` | empty | Fallback if `channels.json` is missing |
| `BASE_URL` | `http://localhost:8000` | Fallback origin in RSS URLs |
| `FEED_LANGUAGE` | `en-us` | RSS language |
| `FEED_CATEGORY` | `TV & Film` | iTunes category |
| `ITUNES_EXPLICIT` | `false` | `true` / `false` |
| `MAX_EPISODES` | `10` | Newest videos to keep per channel |
| `MAX_HEIGHT` | `720` | Cap download resolution |
| `EXCLUDE_SHORTS` | `true` | Use the `/videos` tab and skip sub-61s clips |
| `MIN_DURATION` | `61` | Seconds. Used when excluding Shorts |
| `REFRESH_HOURS` | `6` | Background poll. `0` means only manual / on-request |
| `REFRESH_ON_START` | `true` | Download as soon as the process boots |
| `COOKIES_FILE` | empty | Netscape cookies file if YouTube starts asking for a login |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address |
| `DATA_DIR` | `./data` | State, artwork, and media |

Refresh one channel or all:

```bash
curl http://127.0.0.1:8000/veritasium/refresh
curl http://127.0.0.1:8000/refresh
```

## Endpoints

| Path | Purpose |
|---|---|
| `/` | List feed URLs |
| `/<slug>/feed.xml` | RSS 2.0 |
| `/<slug>/media/{file}` | Episode file |
| `/<slug>/artwork.jpg` | Show art |
| `/<slug>/refresh` | Fetch that channel |
| `/<slug>/status` | That channel's episodes |
| `/refresh` | Fetch every channel |
| `/status` | All channels |
| `/health` | Liveness |

## YouTube blocking yt-dlp

Export cookies from a logged-in browser to a Netscape cookies.txt and set `COOKIES_FILE`. Update yt-dlp when extracts start failing:

```bash
pip install -U yt-dlp
```

## Tests

```bash
python -m unittest discover -s tests
```

## Personal use

yt2pod downloads YouTube media and rehosts it. That may violate YouTube's terms. Only use channels you have the right to copy.
