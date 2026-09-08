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

    def test_download_atomic_and_collision(self):
        from pathlib import Path
        import shutil
        from unittest.mock import MagicMock
        from storypark_scraper.downloader import AssetDownloader
        from storypark_scraper.models import StoryAsset

        target_dir = Path(__file__).parent / ".runtime" / "dl_test"
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        downloader = AssetDownloader(timeout_seconds=5, user_agent="test")
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_response.raise_for_status.return_value = None

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        downloader._session = mock_session

        asset1 = StoryAsset(source_url="https://example.com/image.jpg", kind="image")
        path1 = downloader.download(asset1, target_dir, index=1, story_source_url="https://example.com")
        self.assertTrue(path1.exists())
        self.assertEqual(path1.name, "image.jpg")
        self.assertEqual(path1.read_bytes(), b"chunk1chunk2")

        # Second download with same filename should not overwrite first
        asset2 = StoryAsset(source_url="https://example.com/image.jpg", kind="image")
        path2 = downloader.download(asset2, target_dir, index=2, story_source_url="https://example.com")
        self.assertTrue(path2.exists())
        self.assertNotEqual(path1, path2)
        self.assertEqual(path2.name, "image-02.jpg")
        self.assertEqual(path2.read_bytes(), b"chunk1chunk2")

        # Verify no .tmp files remain
        tmp_files = list(target_dir.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)


if __name__ == "__main__":
    unittest.main()
