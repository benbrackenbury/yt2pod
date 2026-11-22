import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from yt2pod import Settings, build_rss, is_short, mime_for_path, normalize_channel


def _settings(tmp: str, **kwargs) -> Settings:
    values = dict(
        youtube_channel="@veritasium",
        base_url="https://pod.example",
        feed_title="Veritasium",
        feed_author="Derek",
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


class RssTests(unittest.TestCase):
    def test_video_feed(self):
        with TemporaryDirectory() as tmp:
            settings = _settings(tmp)
            xml = build_rss(
                settings,
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

        self.assertIn("<itunes:block>Yes</itunes:block>", xml)
        self.assertIn('type="video/mp4"', xml)
        self.assertIn("https://pod.example/media/abcdefghijk.mp4", xml)
        self.assertIn('<guid isPermaLink="false">yt2pod-abcdefghijk</guid>', xml)
        self.assertIn('rel="self"', xml)
        self.assertIn('text="TV &amp; Film"', xml)
        self.assertIn("<itunes:duration>842</itunes:duration>", xml)
        self.assertNotIn("/shorts/", xml)

    def test_audio_fallback_mime(self):
        self.assertEqual(mime_for_path(Path("ep.m4a")), "audio/x-m4a")
        self.assertEqual(mime_for_path(Path("ep.mp4")), "video/mp4")


if __name__ == "__main__":
    unittest.main()
