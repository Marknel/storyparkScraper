from __future__ import annotations

from pathlib import Path
import json
import logging
import shutil
import unittest
from unittest.mock import patch

from storypark_scraper.app import StoryparkApplication
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

    def test_run_stops_after_first_video_story(self):
        root = prepare_case_dir("app-video-stop")
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

        logger = logging.getLogger("storypark_scraper.test.video")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.INFO)

        with patch("storypark_scraper.app.StoryparkBrowser", FakeBrowser), patch(
            "storypark_scraper.app.load_credentials",
            return_value=Credentials(username="user", password="pass", source="test"),
        ), patch("storypark_scraper.app.time.sleep") as sleep_mock:
            app = StoryparkApplication(config_path, logger)
            app._archive_writer = FakeArchiveWriter(root / "archive")
            result = app.run(full_scan=True, stop_on_video=True, story_delay_seconds=2.0)

        self.assertEqual(result, 0)
        self.assertEqual(sleep_mock.call_count, 1)
        status_payload = json.loads((root / "status" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_payload["stories_downloaded"], 2)
        self.assertEqual(status_payload["stories_seen"], 2)
        self.assertEqual(status_payload["state"], "success")


if __name__ == "__main__":
    unittest.main()
