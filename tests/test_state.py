from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import unittest

from storypark_scraper.models import FilterDecision, StoryContent
from storypark_scraper.state import StateStore


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class StateStoreTests(unittest.TestCase):
    def test_initialize_cleans_up_zombie_runs(self):
        root = prepare_case_dir("state_zombie")
        db_path = root / "state.sqlite"

        # Create store and start a run, then simulate crash without finish_run
        store = StateStore(db_path)
        store.initialize()
        run_id = store.start_run()
        store.close()

        # Re-open and initialize
        store2 = StateStore(db_path)
        store2.initialize()
        latest = store2.get_latest_run()
        store2.close()

        self.assertEqual(latest["run_id"], run_id)
        self.assertEqual(latest["state"], "interrupted")
        self.assertEqual(latest["last_error"], "Process terminated unexpectedly")

    def test_get_child_stats_handles_zero_pictures_safely(self):
        root = prepare_case_dir("state_stats")
        db_path = root / "state.sqlite"

        store = StateStore(db_path)
        store.initialize()
        run_id = store.start_run()

        # Insert a story without any assets
        story = StoryContent(
            story_id="story-empty",
            child_name="Bob",
            source_url="https://example.com/stories/1",
            title="Empty story",
            google_photos_candidate=False,
        )
        store.upsert_story(
            story,
            run_id,
            FilterDecision(include=True, reason="ok"),
            archive_path=None,
            assets=[],
            downloaded_at=None,
        )

        stats = store.get_child_stats()
        store.close()

        self.assertIn("Bob", stats)
        self.assertEqual(stats["Bob"]["total_stories"], 1)
        self.assertEqual(stats["Bob"]["google_photos_suitable_stories"], 0)
        self.assertEqual(stats["Bob"]["google_photos_suitable_pictures"], 0)
        self.assertEqual(stats["Bob"]["google_photos_suitable_pictures_average"], 0)


if __name__ == "__main__":
    unittest.main()
