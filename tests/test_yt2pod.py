import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import json

from yt2pod import (
    Channel,
    Settings,
    _find_media,
    add_channel,
    apple_artwork_url,
    build_rss,
    chapters_payload,
    is_short,
    mime_for_path,
    normalize_channel,
    parse_channels,
    pick_caption,
    remove_channel,
    rss_lang,
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

    def test_empty_file_is_ok(self):
        self.assertEqual(parse_channels({}), {})

    def test_add_and_remove_persists(self):
        with TemporaryDirectory() as tmp:
            settings = _settings(tmp, channels={}, channels_file=Path(tmp) / "channels.json")
            channel = add_channel(settings, "@theo")
            self.assertEqual(channel.slug, "theo")
            self.assertEqual(json.loads(settings.channels_file.read_text())["theo"], "@theo")
            remove_channel(settings, "theo")
            self.assertEqual(json.loads(settings.channels_file.read_text()), {})
            self.assertNotIn("theo", settings.channels)

    def test_rejects_reserved_slug(self):
        with TemporaryDirectory() as tmp:
            settings = _settings(tmp, channels={}, channels_file=Path(tmp) / "channels.json")
            with self.assertRaises(ValueError):
                add_channel(settings, "https://www.youtube.com/@health", "api")


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
        self.assertNotIn("podcast:transcript", xml)
        self.assertNotIn("podcast:chapters", xml)

    def test_transcript_and_chapters(self):
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
                    "episodes": [
                        {
                            "id": "abcdefghijk",
                            "title": "The speed of light",
                            "description": "Why c is c",
                            "filename": "abcdefghijk.mp4",
                            "nbytes": 123,
                            "mime": "video/mp4",
                            "transcript": "abcdefghijk.vtt",
                            "transcript_type": "text/vtt",
                            "transcript_lang": "en",
                            "chapters": "abcdefghijk.chapters.json",
                        }
                    ],
                },
            ).decode("utf-8")

        self.assertIn('xmlns:podcast="https://podcastindex.org/namespace/1.0"', xml)
        self.assertIn(
            'url="https://pod.example/veritasium/media/abcdefghijk.vtt"',
            xml,
        )
        self.assertIn('type="text/vtt"', xml)
        self.assertIn('rel="captions"', xml)
        self.assertIn(
            'url="https://pod.example/veritasium/media/abcdefghijk.chapters.json"',
            xml,
        )
        self.assertIn('type="application/json+chapters"', xml)

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
        self.assertEqual(mime_for_path(Path("ep.vtt")), "text/vtt")
        self.assertEqual(
            mime_for_path(Path("abcdefghijk.chapters.json")),
            "application/json+chapters",
        )


class CaptionTests(unittest.TestCase):
    def test_prefers_manual_vtt(self):
        url, lang = pick_caption(
            {
                "subtitles": {
                    "en": [{"ext": "vtt", "url": "https://example/en.vtt"}],
                },
                "automatic_captions": {
                    "en": [{"ext": "vtt", "url": "https://example/auto.vtt"}],
                },
            },
            "en-us",
        )
        self.assertEqual(url, "https://example/en.vtt")
        self.assertEqual(lang, "en")

    def test_falls_back_to_auto(self):
        url, lang = pick_caption(
            {
                "automatic_captions": {
                    "en-orig": [{"ext": "vtt", "url": "https://example/orig.vtt"}],
                    "de": [{"ext": "vtt", "url": "https://example/de.vtt"}],
                },
            },
            "en-us",
        )
        self.assertEqual(url, "https://example/orig.vtt")
        self.assertEqual(rss_lang(lang), "en")

    def test_chapters_need_two(self):
        self.assertIsNone(chapters_payload({"chapters": [{"start_time": 0, "title": "Only"}]}))
        payload = chapters_payload(
            {
                "chapters": [
                    {"start_time": 0, "title": "Intro"},
                    {"start_time": 12.5, "title": "Meat"},
                ]
            }
        )
        self.assertEqual(
            payload,
            {
                "version": "1.2.0",
                "chapters": [
                    {"startTime": 0.0, "title": "Intro"},
                    {"startTime": 12.5, "title": "Meat"},
                ],
            },
        )

    def test_find_media_ignores_sidecars(self):
        with TemporaryDirectory() as tmp:
            media = Path(tmp)
            (media / "abcdefghijk.vtt").write_text("WEBVTT\n", encoding="utf-8")
            (media / "abcdefghijk.chapters.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(_find_media(media, "abcdefghijk"))
            video = media / "abcdefghijk.mp4"
            video.write_bytes(b"x")
            self.assertEqual(_find_media(media, "abcdefghijk"), video)


if __name__ == "__main__":
    unittest.main()
