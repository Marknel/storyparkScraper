from __future__ import annotations

import unittest

from storypark_scraper.downloader import AssetDownloader, _derive_filename


class DownloaderTests(unittest.TestCase):
    def test_browser_cookie_snapshot_replaces_old_cookies_and_preserves_scope(self):
        import requests

        downloader = AssetDownloader(timeout_seconds=5, user_agent="test")
        self.addCleanup(downloader.close)
        downloader.set_cookies([
            {"name": "session", "value": "old", "domain": "app.storypark.com", "path": "/"},
            {"name": "revoked", "value": "old", "domain": "app.storypark.com", "path": "/"},
        ])
        downloader.set_cookies([
            {"name": "session", "value": "renewed", "domain": "app.storypark.com", "path": "/media", "secure": True, "expires": 4_102_444_800},
            {"name": "empty", "value": "", "domain": "app.storypark.com", "path": "/media", "secure": True, "expires": -1},
        ])
        session = downloader._get_session()
        cookie = next(cookie for cookie in session.cookies if cookie.name == "session")
        self.assertEqual(cookie.domain, "app.storypark.com")
        self.assertEqual(cookie.path, "/media")
        self.assertTrue(cookie.secure)
        self.assertEqual(cookie.expires, 4_102_444_800)
        self.assertIsNone(next(cookie for cookie in session.cookies if cookie.name == "empty").expires)
        authorized = session.prepare_request(requests.Request("GET", "https://app.storypark.com/media/photo.jpg"))
        self.assertIn("session=renewed", authorized.headers["Cookie"])
        self.assertIn("empty=", authorized.headers["Cookie"])
        self.assertNotIn("revoked", authorized.headers["Cookie"])
        for url in (
            "http://app.storypark.com/media/photo.jpg",
            "https://app.storypark.com/unrelated/photo.jpg",
            "https://unrelated.example/media/photo.jpg",
        ):
            request = session.prepare_request(requests.Request("GET", url))
            self.assertNotIn("Cookie", request.headers)

    def test_empty_browser_cookie_snapshot_removes_stale_authentication(self):
        import requests

        downloader = AssetDownloader(timeout_seconds=5, user_agent="test")
        self.addCleanup(downloader.close)
        downloader.set_cookies([
            {"name": "session", "value": "revoked", "domain": "app.storypark.com", "path": "/"},
        ])
        downloader.set_cookies([])
        session = downloader._get_session()
        request = session.prepare_request(requests.Request("GET", "https://app.storypark.com/media/photo.jpg"))
        self.assertEqual(len(session.cookies), 0)
        self.assertNotIn("Cookie", request.headers)

    def test_user_agent_tracks_browser_before_and_after_session_creation(self):
        import requests

        downloader = AssetDownloader(timeout_seconds=5, user_agent="configured-agent")
        self.addCleanup(downloader.close)
        downloader.set_user_agent("browser-agent")
        self.assertIsNone(downloader._session)
        session = downloader._get_session()
        request = session.prepare_request(requests.Request("GET", "https://app.storypark.com/media/photo.jpg"))
        self.assertEqual(request.headers["User-Agent"], "browser-agent")
        downloader.set_user_agent("renewed-browser-agent")
        request = session.prepare_request(requests.Request("GET", "https://app.storypark.com/media/photo.jpg"))
        self.assertIs(downloader._get_session(), session)
        self.assertEqual(request.headers["User-Agent"], "renewed-browser-agent")

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
