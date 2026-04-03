from __future__ import annotations

from pathlib import Path
import unittest

from storypark_scraper.parsing import parse_child_targets_from_html, parse_story_from_html, parse_story_ids_from_html


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


class ParsingTests(unittest.TestCase):
    def test_parse_child_targets_legacy_menu(self):
        html = (FIXTURE_ROOT / "menu.html").read_text(encoding="utf-8")
        children = parse_child_targets_from_html(
            html,
            "https://app.storypark.com/children/{child_code}/stories",
            allowlist=["Alice"],
        )
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].name, "Alice")
        self.assertEqual(children[0].stories_url, "https://app.storypark.com/children/child-1/stories")

    def test_parse_child_targets_current_menu(self):
        html = (FIXTURE_ROOT / "menu_current.html").read_text(encoding="utf-8")
        children = parse_child_targets_from_html(
            html,
            "https://app.storypark.com/children/{child_code}/stories",
            allowlist=["Alice"],
        )
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].name, "Alice")
        self.assertEqual(children[0].code, "5331262")
        self.assertEqual(children[0].stories_url, "https://app.storypark.com/children/5331262/stories")

    def test_parse_story_ids_deduplicates(self):
        html = (FIXTURE_ROOT / "story_list.html").read_text(encoding="utf-8")
        self.assertEqual(parse_story_ids_from_html(html), ["story-100", "story-101"])

    def test_parse_story_detail_extracts_text_and_assets(self):
        html = (FIXTURE_ROOT / "story_detail.html").read_text(encoding="utf-8")
        story = parse_story_from_html(html, "story-100", "Alice", "https://app.storypark.com/stories/story-100")
        self.assertEqual(story.title, "Beach Day")
        self.assertEqual(story.author, "Ms Carter")
        self.assertEqual(story.published_at, "2026-03-21")
        self.assertIn("collecting shells", story.body)
        self.assertEqual(len(story.assets), 2)
        self.assertEqual(story.assets[1].source_url, "https://app.storypark.com/media/image-2.jpg")

    def test_parse_current_story_detail_extracts_author_date_and_explicit_children(self):
        html = (FIXTURE_ROOT / "story_detail_current.html").read_text(encoding="utf-8")
        story = parse_story_from_html(
            html,
            "105148503",
            "Lyra Cracknell",
            "https://app.storypark.com/stories/105148503/spa_show?back=https%3A%2F%2Fapp.storypark.com%2Fchildren%2F5331262%2Fstories",
        )
        self.assertEqual(story.title, "The Emerging Image of Lyra")
        self.assertEqual(story.author, "Rachel Mckelvie")
        self.assertEqual(story.published_text, "30 March 2026")
        self.assertEqual(story.published_at, "2026-03-30")
        self.assertIn("Lyra has now settled comfortably", story.body)
        self.assertEqual(story.included_children, ["Lyra Cracknell"])
        self.assertEqual(len(story.assets), 1)

    def test_parse_group_story_detail_falls_back_to_header_child_list(self):
        html = (FIXTURE_ROOT / "story_detail_group.html").read_text(encoding="utf-8")
        story = parse_story_from_html(
            html,
            "105847745",
            "Lyra Cracknell",
            "https://app.storypark.com/stories/105847745/spa_show",
        )
        self.assertEqual(story.title, "Mud Kitchen")
        self.assertEqual(story.included_children, ["Lyra", "Zoë", "Leo"])

    def test_parse_story_detail_extracts_video_assets(self):
        html = (FIXTURE_ROOT / "story_detail_video.html").read_text(encoding="utf-8")
        story = parse_story_from_html(
            html,
            "story-video",
            "Alice",
            "https://app.storypark.com/stories/story-video/spa_show",
        )
        self.assertTrue(story.has_video)
        self.assertEqual(len(story.assets), 1)
        self.assertEqual(story.assets[0].kind, "video")
        self.assertEqual(story.assets[0].source_url, "https://app.storypark.com/media/video-1")


if __name__ == "__main__":
    unittest.main()
