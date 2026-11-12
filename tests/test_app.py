from __future__ import annotations

from pathlib import Path
import json
import logging
import shutil
import unittest
from unittest.mock import MagicMock, patch

from storypark_scraper.app import StoryparkApplication, _is_google_photos_candidate
from storypark_scraper.models import ChildTarget, Credentials, ExportResult, StoryAsset, StoryContent


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeBrowser:
    def __init__(self, config, credential_provider, logger):
        self._config = config
        self._credential_provider = credential_provider
        self._logger = logger

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def discover_children(self):
        return [
            ChildTarget(
                name="Alice",
                code="child-1",
                stories_url="https://app.storypark.com/children/child-1/stories",
            )
        ]

    def collect_story_ids(self, child, *, full_scan, max_story_ids=None):
        story_ids = ["story-1", "story-2", "story-3", "story-4"]
        return story_ids[:max_story_ids] if max_story_ids is not None else story_ids

    def fetch_story(self, child, story_id):
        assets = []
        if story_id == "story-2":
            assets = [StoryAsset(source_url="https://cdn.example.com/video-1.mp4", kind="video")]
        return StoryContent(
            story_id=story_id,
            child_name=child.name,
            source_url=f"https://app.storypark.com/stories/{story_id}",
            title=f"Story {story_id}",
            body="A normal story body.",
            published_at="2026-03-31",
            assets=assets,
            included_children=["Alice"],
        )


class FakeArchiveWriter:
    def __init__(self, archive_root: Path):
        self._archive_root = archive_root

    def export_story(self, story, decision, scraped_at):
        story_dir = self._archive_root / story.story_id
        story_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = story_dir / "story.md"
        markdown_path.write_text(f"# {story.title}\n", encoding="utf-8")
        return ExportResult(story_dir=story_dir, story_markdown_path=markdown_path, assets=[])


class AppRunTests(unittest.TestCase):
    def test_run_skips_when_runtime_disabled_without_hiding_run_history(self):
        from storypark_scraper.models import RunStats
        from storypark_scraper.state import StateStore

        root = prepare_case_dir("app_disabled")
        config_path = root / "config.yaml"
        config_path.write_text("runtime:\n  enabled: false\n", encoding="utf-8")
        logger = logging.getLogger("storypark_scraper.test.paused")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())

        store = StateStore(root / "state" / "state.sqlite")
        store.initialize()
        run_id = store.start_run()
        store.finish_run(run_id, "failed", RunStats(), None, "Interactive sign-in required")
        previous_run = store.get_latest_run()
        store.close()

        with patch("storypark_scraper.app.StoryparkBrowser") as browser_mock, patch(
            "storypark_scraper.app.load_credentials"
        ) as credentials_mock:
            app = StoryparkApplication(config_path, logger)
            publisher = MagicMock()
            app._mqtt = publisher
            session = MagicMock()
            app._downloader._session = session
            with patch.object(app._state, "close", wraps=app._state.close) as close_state:
                result = app.run(full_scan=True, max_stories=1)
                close_state.assert_called_once()

        self.assertEqual(result, 0)
        browser_mock.assert_not_called()
        credentials_mock.assert_not_called()
        session.close.assert_called_once()
        publisher.publish_discovery.assert_not_called()
        publisher.publish_telemetry.assert_called_once()
        telemetry = publisher.publish_telemetry.call_args.args[0]
        self.assertEqual(telemetry["status"], "paused")
        self.assertEqual(telemetry["problem"], "OFF")
        self.assertEqual(telemetry["last_run"], previous_run["finished_at"])
        self.assertIsNone(telemetry["error"])

        status_payload = json.loads((root / "status" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_payload["state"], "paused")
        self.assertEqual(status_payload["last_run_finished_at"], previous_run["finished_at"])
        self.assertIsNone(status_payload["last_error"])
        store = StateStore(root / "state" / "state.sqlite")
        try:
            store.initialize()
            latest_run = store.get_latest_run()
            self.assertEqual(latest_run["run_id"], run_id)
            self.assertEqual(latest_run["state"], "failed")
            self.assertEqual(latest_run["last_error"], "Interactive sign-in required")
        finally:
            store.close()

    def test_media_downloads_use_authenticated_and_renewed_browser_session(self):
        self._assert_media_session_handoff()

    def test_explicit_acceptance_run_can_override_pause_without_changing_config(self):
        self._assert_media_session_handoff(enabled=False, allow_paused=True)

    def _assert_media_session_handoff(self, *, enabled=True, allow_paused=False):
        import requests

        root = prepare_case_dir("app_session_handoff" if enabled else "app_paused_acceptance")
        config_path = root / "config.yaml"
        config_path.write_text(
            f"runtime:\n  enabled: {str(enabled).lower()}\n  full_scan_on_first_run: false\n",
            encoding="utf-8",
        )
        original_config = config_path.read_bytes()
        logger = logging.getLogger("storypark_scraper.test.session_handoff")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        events = []

        class SessionBrowser(FakeBrowser):
            phase = "unauthenticated"

            def discover_children(self):
                self.phase = "login"
                events.append("discover")
                return super().discover_children()

            def get_cookies(self):
                events.append(f"cookies:{self.phase}")
                if self.phase == "unauthenticated":
                    raise AssertionError("Cookies were copied before login completed")
                cookies = [{"name": "session", "value": self.phase, "domain": "app.storypark.com", "path": "/", "secure": True}]
                if self.phase != "story-2":
                    cookies.append({"name": "revoked", "value": "old", "domain": "app.storypark.com", "path": "/", "secure": True})
                return cookies

            def get_user_agent(self):
                return "actual-browser-agent"

            def fetch_story(self, child, story_id):
                self.phase = story_id
                events.append(f"fetch:{story_id}")
                story = super().fetch_story(child, story_id)
                story.assets = [StoryAsset(source_url=f"https://app.storypark.com/media/{story_id}.jpg", kind="image")]
                return story

        session = requests.Session()
        self.addCleanup(session.close)

        def download_response(url, **kwargs):
            story_id = url.rsplit("/", 1)[-1].removesuffix(".jpg")
            events.append(f"download:{story_id}")
            request = session.prepare_request(requests.Request("GET", url))
            self.assertIn(f"session={story_id}", request.headers["Cookie"])
            self.assertEqual(request.headers["User-Agent"], "actual-browser-agent")
            if story_id == "story-2":
                self.assertNotIn("revoked", request.headers["Cookie"])
            response = MagicMock()
            response.headers = {"Content-Type": "image/jpeg"}
            response.iter_content.return_value = [b"image-bytes"]
            return response

        session.get = MagicMock(side_effect=download_response)
        with patch("storypark_scraper.app.StoryparkBrowser", SessionBrowser), patch(
            "storypark_scraper.downloader._build_session", return_value=session
        ):
            app = StoryparkApplication(config_path, logger)
            result = app.run(max_stories=2, allow_paused=allow_paused)

        self.assertEqual(result, 0)
        self.assertEqual(events, [
            "discover", "cookies:login",
            "fetch:story-1", "cookies:story-1", "download:story-1",
            "fetch:story-2", "cookies:story-2", "download:story-2",
        ])
        self.assertEqual(session.get.call_count, 2)
        media = list((root / "archive").rglob("*.jpg"))
        self.assertEqual(len(media), 2)
        self.assertTrue(all(path.read_bytes() == b"image-bytes" for path in media))
        self.assertEqual(config_path.read_bytes(), original_config)
        self.assertEqual(app._config.runtime.enabled, enabled)

    def test_run_honors_max_stories_and_delay(self):
        root = prepare_case_dir("app")
        config_path = root / "config.yaml"
        config_path.write_text(
            """
paths:
  archive: archive
  state_db: state/state.sqlite
  status_file: status/status.json
  browser_profile: state/browser-profile
  lock_file: state/run.lock
storypark:
  credential_target: demo-target
filters:
  exclude_keywords: []
  exclude_regexes: []
  exclude_authors: []
  exclude_content_types: []
  per_child: {}
runtime:
  full_scan_on_first_run: true
  stop_after_existing_stories: true
  story_delay_seconds: 0.0
  max_stories_per_run: 0
classifier:
  enabled: false
google_photos:
  enabled: false
""".strip(),
            encoding="utf-8",
        )

        logger = logging.getLogger("storypark_scraper.test")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.INFO)

        with patch("storypark_scraper.app.StoryparkBrowser", FakeBrowser), patch(
            "storypark_scraper.app.load_credentials",
            return_value=Credentials(username="user", password="pass", source="test"),
        ), patch("storypark_scraper.app.time.sleep") as sleep_mock:
            app = StoryparkApplication(config_path, logger)
            app._archive_writer = FakeArchiveWriter(root / "archive")
            result = app.run(full_scan=True, max_stories=3, story_delay_seconds=2.0)

        self.assertEqual(result, 0)
        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_called_with(2.0)

        status_payload = json.loads((root / "status" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_payload["stories_downloaded"], 3)
        self.assertEqual(status_payload["stories_seen"], 3)
        self.assertEqual(status_payload["state"], "success")

    def test_google_photos_candidate_requires_story_children_to_be_subset_of_known_children(self):
        self.assertTrue(
            _is_google_photos_candidate(
                StoryContent(
                    story_id="story-1",
                    child_name="Lyra Cracknell",
                    source_url="https://app.storypark.com/stories/story-1",
                    included_children=["Lyra"],
                ),
                {"lyra", "lyra cracknell", "samuel", "samuel cracknell"},
            )
        )
        self.assertTrue(
            _is_google_photos_candidate(
                StoryContent(
                    story_id="story-2",
                    child_name="Lyra Cracknell",
                    source_url="https://app.storypark.com/stories/story-2",
                    included_children=["Lyra Cracknell", "Samuel"],
                ),
                {"lyra", "lyra cracknell", "samuel", "samuel cracknell"},
            )
        )
        self.assertFalse(
            _is_google_photos_candidate(
                StoryContent(
                    story_id="story-3",
                    child_name="Lyra Cracknell",
                    source_url="https://app.storypark.com/stories/story-3",
                    included_children=["Lyra", "Zoë"],
                ),
                {"lyra", "lyra cracknell", "samuel", "samuel cracknell"},
            )
        )
        self.assertFalse(
            _is_google_photos_candidate(
                StoryContent(
                    story_id="story-4",
                    child_name="Lyra Cracknell",
                    source_url="https://app.storypark.com/stories/story-4",
                ),
                {"lyra", "lyra cracknell", "samuel", "samuel cracknell"},
            )
        )

    def test_consecutive_existing_threshold_continues_past_isolated_existing_story(self):
        root = prepare_case_dir("app_threshold")
        config_path = root / "config.yaml"
        config_path.write_text(
            """
paths:
  archive: archive
  state_db: state/state.sqlite
  status_file: status/status.json
  browser_profile: state/browser-profile
  lock_file: state/run.lock
storypark:
  credential_target: demo-target
filters:
  exclude_keywords: []
runtime:
  full_scan_on_first_run: false
  stop_after_existing_stories: true
  consecutive_existing_stories_threshold: 2
  story_delay_seconds: 0.0
""".strip(),
            encoding="utf-8",
        )

        logger = logging.getLogger("storypark_scraper.test")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())

        from storypark_scraper.state import StateStore
        from storypark_scraper.models import FilterDecision

        # Pre-seed story-1 in state database
        store = StateStore(root / "state" / "state.sqlite")
        store.initialize()
        run_id = store.start_run()
        store.upsert_story(
            StoryContent(story_id="story-1", child_name="Alice", source_url="https://app.storypark.com/stories/story-1"),
            run_id,
            FilterDecision(include=True, reason="seed"),
            archive_path="archive/Alice/2026/story-1/story.md",
            assets=[],
            downloaded_at="2026-01-01T00:00:00Z",
        )
        from storypark_scraper.models import RunStats
        store.finish_run(run_id, "success", RunStats(), None, None)
        store.close()

        with patch("storypark_scraper.app.StoryparkBrowser", FakeBrowser), patch(
            "storypark_scraper.app.load_credentials",
            return_value=Credentials(username="user", password="pass", source="test"),
        ):
            app = StoryparkApplication(config_path, logger)
            app._archive_writer = FakeArchiveWriter(root / "archive")
            result = app.run()

        self.assertEqual(result, 0)
        status_payload = json.loads((root / "status" / "status.json").read_text(encoding="utf-8"))
        # story-1 was skipped, but story-2, story-3, story-4 were processed
        # story-2 was downloaded (3 new stories downloaded)
        self.assertGreater(status_payload["stories_downloaded"], 0)
        self.assertEqual(status_payload["stories_downloaded"], 3)


if __name__ == "__main__":
    unittest.main()
