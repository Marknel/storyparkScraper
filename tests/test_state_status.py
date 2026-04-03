from __future__ import annotations

from pathlib import Path
import shutil
import unittest

from storypark_scraper.models import ExportedAsset, FilterDecision, RunStats, StoryContent
from storypark_scraper.state import StateStore
from storypark_scraper.status import build_status_payload


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class StateStatusTests(unittest.TestCase):
    def test_state_store_tracks_runs_and_latest_story(self):
        root = prepare_case_dir("state")
        db_path = root / "state.sqlite"
        store = StateStore(db_path)
        store.initialize()
        try:
            run_id = store.start_run()
            story = StoryContent(
                story_id="story-100",
                child_name="Alice",
                source_url="https://app.storypark.com/stories/story-100",
                title="Beach Day",
                body="Alice collected shells.",
                published_at="2026-03-21",
            )
            store.upsert_story(
                story,
                run_id,
                FilterDecision(include=True, reason="accepted"),
                archive_path="archive/Alice/2026/story-100/story.md",
                assets=[
                    ExportedAsset(
                        source_url="https://cdn.example.com/image-1.jpg",
                        local_path=Path("archive/Alice/2026/story-100/media/asset-01.jpg"),
                        kind="image",
                    )
                ],
                downloaded_at="2026-03-31T09:00:00+00:00",
            )
            stats = RunStats(stories_seen=1, stories_downloaded=1, stories_skipped=0)
            store.finish_run(run_id, "success", stats, "2026-03-31T09:00:00+00:00", None)

            payload = build_status_payload(store)

            self.assertEqual(payload["state"], "success")
            self.assertEqual(payload["stories_downloaded"], 1)
            self.assertEqual(payload["last_story_acquired_at"], "2026-03-31T09:00:00+00:00")
            self.assertTrue(store.story_exists("story-100", "Alice"))
        finally:
            store.close()

    def test_state_store_keeps_story_records_per_child(self):
        root = prepare_case_dir("state-per-child")
        db_path = root / "state.sqlite"
        store = StateStore(db_path)
        store.initialize()
        try:
            run_id = store.start_run()
            for child_name in ["Lyra", "Samuel"]:
                story = StoryContent(
                    story_id="shared-story",
                    child_name=child_name,
                    source_url=f"https://app.storypark.com/stories/shared-story?child={child_name}",
                    title="Shared Group Story",
                    body=f"Seen by {child_name}",
                    published_at="2026-03-30",
                )
                store.upsert_story(
                    story,
                    run_id,
                    FilterDecision(include=True, reason="accepted"),
                    archive_path=f"archive/{child_name}/2026/shared-story/story.md",
                    assets=[],
                    downloaded_at="2026-03-31T09:00:00+00:00",
                )

            count = store.connection.execute("SELECT COUNT(*) AS value FROM stories WHERE story_id = ?", ("shared-story",)).fetchone()["value"]
            self.assertEqual(count, 2)
            self.assertTrue(store.story_exists("shared-story", "Lyra"))
            self.assertTrue(store.story_exists("shared-story", "Samuel"))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
