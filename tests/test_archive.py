from __future__ import annotations

from pathlib import Path
import shutil
import unittest

from storypark_scraper.archive import ArchiveWriter
from storypark_scraper.models import FilterDecision, StoryAsset, StoryContent


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeDownloader:
    def download(self, asset, target_dir, index, story_source_url):
        target_dir.mkdir(parents=True, exist_ok=True)
        extension = ".mp4" if asset.kind == "video" else ".jpg"
        destination = target_dir / f"asset-{index:02d}{extension}"
        destination.write_bytes(b"binary")
        return destination


class ArchiveTests(unittest.TestCase):
    def test_export_story_writes_markdown_and_media(self):
        archive_root = prepare_case_dir("archive")
        writer = ArchiveWriter(archive_root, FakeDownloader())
        story = StoryContent(
            story_id="story-100",
            child_name="Alice / Smith",
            source_url="https://app.storypark.com/stories/story-100",
            title="Beach Day",
            body="Alice collected shells.",
            published_at="2026-03-21",
            published_text="21 March 2026",
            author="Ms Carter",
            included_children=["Alice Smith"],
            google_photos_candidate=True,
            assets=[
                StoryAsset(source_url="https://cdn.example.com/image-1.jpg", kind="image"),
                StoryAsset(source_url="https://cdn.example.com/video-1.mp4", kind="video"),
            ],
        )

        stale_media_dir = archive_root / "Alice - Smith" / "2026" / "story-100" / "media"
        stale_media_dir.mkdir(parents=True, exist_ok=True)
        (stale_media_dir / "stale-file").write_text("old", encoding="utf-8")

        result = writer.export_story(story, FilterDecision(include=True, reason="accepted"), "2026-03-31T09:00:00Z")
        markdown = result.story_markdown_path.read_text(encoding="utf-8")

        self.assertTrue(result.story_markdown_path.exists())
        self.assertIn("storypark_id: story-100", markdown)
        self.assertIn("included_children:", markdown)
        self.assertIn("google_photos_candidate: true", markdown)
        self.assertIn("# Beach Day", markdown)
        self.assertIn("Included Children: Alice Smith", markdown)
        self.assertIn("Google Photos Candidate: yes", markdown)
        self.assertIn("![asset-01.jpg](media/asset-01.jpg)", markdown)
        self.assertIn("## Videos", markdown)
        self.assertIn("[asset-02.mp4](media/asset-02.mp4)", markdown)
        self.assertFalse((stale_media_dir / "stale-file").exists())
        self.assertEqual(result.asset_count, 2)


if __name__ == "__main__":
    unittest.main()
