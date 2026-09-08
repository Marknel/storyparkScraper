from __future__ import annotations

from pathlib import Path
import json
import logging
import shutil
import unittest
from unittest.mock import patch

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
