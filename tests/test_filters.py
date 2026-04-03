from __future__ import annotations

import unittest

from storypark_scraper.config import FiltersConfig, FilterSet
from storypark_scraper.filters import StoryFilter
from storypark_scraper.models import StoryContent


class FilterTests(unittest.TestCase):
    def test_keyword_and_per_child_rules_apply(self):
        config = FiltersConfig(
            exclude_keywords=["menu"],
            exclude_regexes=[],
            exclude_authors=[],
            exclude_content_types=[],
            per_child={"Alice": FilterSet(exclude_keywords=["fundraiser"])},
        )
        story_filter = StoryFilter(config)

        decision = story_filter.decide(
            StoryContent(
                story_id="1",
                child_name="Alice",
                source_url="https://example.com/story/1",
                title="Weekly Menu",
                body="Here is the meal plan",
            )
        )
        self.assertFalse(decision.include)
        self.assertIn("menu", decision.reason)

        decision = story_filter.decide(
            StoryContent(
                story_id="2",
                child_name="Alice",
                source_url="https://example.com/story/2",
                title="Community fundraiser",
                body="Please donate if you can",
            )
        )
        self.assertFalse(decision.include)
        self.assertIn("fundraiser", decision.reason)

        accepted = story_filter.decide(
            StoryContent(
                story_id="3",
                child_name="Bob",
                source_url="https://example.com/story/3",
                title="Sandpit play",
                body="Bob built a road with his friends.",
            )
        )
        self.assertTrue(accepted.include)


if __name__ == "__main__":
    unittest.main()
