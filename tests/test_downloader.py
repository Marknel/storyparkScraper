from __future__ import annotations

import unittest

from storypark_scraper.downloader import _derive_filename


class DownloaderTests(unittest.TestCase):
    def test_derive_filename_appends_extension_from_content_type(self):
        self.assertEqual(
            _derive_filename("https://app.storypark.com/media_items/hash/abc123", 1, "image/jpeg"),
            "abc123.jpg",
        )
        self.assertEqual(
            _derive_filename("https://app.storypark.com/media_items/hash/video123", 2, "video/mp4"),
            "video123.mp4",
        )

    def test_derive_filename_preserves_existing_extension(self):
        self.assertEqual(
            _derive_filename("https://cdn.example.com/media/photo.png", 1, "image/jpeg"),
            "photo.png",
        )


if __name__ == "__main__":
    unittest.main()
