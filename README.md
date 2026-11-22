# yt2pod

Turns YouTube channels into RSS feeds Apple Podcasts can follow.

Paste a channel URL or `@handle` on the homepage. yt-dlp downloads the latest videos to disk. The app serves RSS 2.0 with iTunes tags. Enclosures are MP4 when YouTube allows it, m4a otherwise. Shorts are skipped unless you turn that off.

Each channel is `http://127.0.0.1:8000/<slug>/feed.xml`. From a phone, `localhost` will not work. Apple Podcasts wants a public HTTPS URL and at least one episode in the feed.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on `PATH` (yt-dlp uses it to mux MP4)
- A URL Apple Podcasts can fetch, usually HTTPS

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`channels.json` is optional at first. Start the server and add channels in the browser, or copy `channels.example.json` to `channels.json` and edit it.

```bash
python main.py
```

Open `http://127.0.0.1:8000/`. Add and remove channels there, then copy a feed URL. Adding a channel starts a download. The first one can take a while.

Docker:

```bash
cp channels.example.json channels.json
docker compose up --build
```

Copy `channels.json` before compose. Docker mounts that file, so a missing path becomes a directory and the app cannot write it.

Media is `./data/<slug>/`. Removing a channel in the UI deletes that folder.

## Follow in Apple Podcasts

Wait until the homepage (or `GET /<slug>/status`) lists episodes, then paste `https://your-host/<slug>/feed.xml`.

iPhone or iPad: Library → More → Follow a Show by URL

Mac: File → Follow a Show by URL

Apple shows "Unable to Follow" if the feed is empty. Public HTTP fails the same way. Use HTTPS.

## channels.json

The object key is the URL slug. A string value is enough. Objects take `youtube` (or `url` / `channel`) plus optional `title`, `description`, `author`, `artwork`.

```json
{
  "veritasium": "https://www.youtube.com/@veritasium",
  "mkbhd": {
    "youtube": "@mkbhd",
    "title": "MKBHD"
  }
}
```

That is `/veritasium/feed.xml` and `/mkbhd/feed.xml`. `BASE_URL` is the fallback origin. Enclosure URLs also use the request `Host` and `X-Forwarded-Proto`.

## Config

`CHANNELS_FILE` points at `channels.json`. The rest are env vars.

| Variable | Default | What it does |
|---|---|---|
| `CHANNELS_FILE` | `./channels.json` | Slug → YouTube channel map |
| `YOUTUBE_CHANNEL` | empty | Used only if `channels.json` is missing |
| `BASE_URL` | `http://localhost:8000` | Fallback origin in RSS URLs |
| `FEED_LANGUAGE` | `en-us` | RSS language |
| `FEED_CATEGORY` | `TV & Film` | iTunes category |
| `ITUNES_EXPLICIT` | `false` | `true` / `false` |
| `MAX_EPISODES` | `10` | Newest videos to keep per channel |
| `MAX_HEIGHT` | `720` | Cap download resolution |
| `EXCLUDE_SHORTS` | `true` | Use the `/videos` tab and skip clips under `MIN_DURATION` |
| `MIN_DURATION` | `61` | Seconds. Used when excluding Shorts |
| `REFRESH_HOURS` | `6` | Background poll. `0` means only manual / on-request |
| `REFRESH_ON_START` | `true` | Download when the process boots |
| `COOKIES_FILE` | empty | Netscape cookies file if YouTube starts asking for a login |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address |
| `DATA_DIR` | `./data` | State, artwork, and media |

```bash
curl http://127.0.0.1:8000/veritasium/refresh
curl http://127.0.0.1:8000/refresh
```

## Endpoints

| Path | Purpose |
|---|---|
| `/` | Channel UI |
| `/api/channels` | List JSON. `POST {youtube, slug?}` to add. `DELETE /api/channels/{slug}` to remove |
| `/<slug>/feed.xml` | RSS 2.0 (GET and HEAD) |
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

yt2pod downloads YouTube media and rehosts it. That can break YouTube's terms. Only use channels you have the right to copy.
