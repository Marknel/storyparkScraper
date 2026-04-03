from __future__ import annotations

import unittest

from storypark_scraper.cli import build_parser


class CliTests(unittest.TestCase):
    def test_run_parser_accepts_story_cap_delay_and_filter_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--config",
                "config.yaml",
                "run",
                "--full-scan",
                "--max-stories",
                "3",
                "--max-stories-per-child",
                "5",
                "--story-delay-seconds",
                "2.5",
                "--disable-filters",
            ]
        )

        self.assertEqual(args.command, "run")
        self.assertTrue(args.full_scan)
        self.assertEqual(args.max_stories, 3)
        self.assertEqual(args.max_stories_per_child, 5)
        self.assertEqual(args.story_delay_seconds, 2.5)
        self.assertTrue(args.disable_filters)


if __name__ == "__main__":
    unittest.main()
