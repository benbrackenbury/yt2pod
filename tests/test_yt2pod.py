import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from yt2pod import (
    Channel,
    Settings,
    apple_artwork_url,
    build_rss,
    is_short,
    mime_for_path,
    normalize_channel,
    parse_channels,
)


def _settings(tmp: str, **kwargs) -> Settings:
    channel = Channel(slug="veritasium", youtube="@veritasium", title="Veritasium", author="Derek")
    values = dict(
        channels={"veritasium": channel},
        base_url="https://pod.example",
        data_dir=Path(tmp),
    )
    values.update(kwargs)
    return Settings(**values)


class NormalizeChannelTests(unittest.TestCase):
    def test_handle_becomes_videos_tab(self):
        self.assertEqual(
            normalize_channel("@veritasium"),
            "https://www.youtube.com/@veritasium/videos",
        )

    def test_keeps_explicit_path(self):
        url = "https://www.youtube.com/@veritasium/streams"
        self.assertEqual(normalize_channel(url), url)

    def test_includes_shorts_when_asked(self):
        self.assertEqual(
            normalize_channel("https://www.youtube.com/@veritasium", exclude_shorts=False),
            "https://www.youtube.com/@veritasium",
        )


class ShortsFilterTests(unittest.TestCase):
    def test_shorts_url(self):
        self.assertTrue(is_short({"url": "https://www.youtube.com/shorts/abcdefghijk"}))

    def test_short_duration(self):
        self.assertTrue(is_short({"duration": 45}))

    def test_long_video(self):
        self.assertFalse(
            is_short({"url": "https://www.youtube.com/watch?v=abcdefghijk", "duration": 600})
        )


class ChannelConfigTests(unittest.TestCase):
    def test_parses_url_or_object(self):
        channels = parse_channels(
            {
                "veritasium": "https://www.youtube.com/@veritasium",
                "mkbhd": {"youtube": "@mkbhd", "title": "MKBHD"},
            }
        )
        self.assertEqual(channels["veritasium"].youtube, "https://www.youtube.com/@veritasium")
        self.assertEqual(channels["mkbhd"].title, "MKBHD")


class RssTests(unittest.TestCase):
    def test_video_feed(self):
        with TemporaryDirectory() as tmp:
            settings = _settings(tmp)
            channel = settings.channels["veritasium"]
            xml = build_rss(
                settings,
                channel,
                {
                    "title": "Veritasium",
                    "author": "Derek",
                    "description": "Science videos",
                    "channel_url": "https://www.youtube.com/@veritasium/videos",
                    "episodes": [
                        {
                            "id": "abcdefghijk",
                            "title": "The speed of light",
                            "description": "Why c is c",
                            "upload_date": "20240115",
                            "timestamp": 1705276800,
                            "duration": 842,
                            "filename": "abcdefghijk.mp4",
                            "nbytes": 12345678,
                            "mime": "video/mp4",
                            "link": "https://www.youtube.com/watch?v=abcdefghijk",
                        }
                    ],
                },
            ).decode("utf-8")

        self.assertIn('type="video/mp4"', xml)
        self.assertIn("https://pod.example/veritasium/media/abcdefghijk.mp4", xml)
        self.assertIn("https://pod.example/veritasium/feed.xml", xml)
        self.assertIn('<guid isPermaLink="false">yt2pod-abcdefghijk</guid>', xml)
        self.assertIn('rel="self"', xml)
        self.assertIn('text="TV &amp; Film"', xml)
        self.assertIn("<itunes:duration>842</itunes:duration>", xml)
        self.assertNotIn("/shorts/", xml)
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertNotIn("webp", xml)

    def test_apple_artwork_rejects_webp(self):
        self.assertEqual(
            apple_artwork_url("https://i.ytimg.com/vi_webp/abcdefghijk/maxresdefault.webp"),
            "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
        )
        self.assertIsNone(apple_artwork_url("https://cdn.example/cover.webp"))
        self.assertEqual(
            apple_artwork_url("https://pod.example/theo/artwork.jpg"),
            "https://pod.example/theo/artwork.jpg",
        )

    def test_audio_fallback_mime(self):
        self.assertEqual(mime_for_path(Path("ep.m4a")), "audio/x-m4a")
        self.assertEqual(mime_for_path(Path("ep.mp4")), "video/mp4")


if __name__ == "__main__":
    unittest.main()
