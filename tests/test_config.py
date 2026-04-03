from __future__ import annotations

from pathlib import Path
import shutil
import unittest

from storypark_scraper.config import load_config


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class ConfigTests(unittest.TestCase):
    def test_load_config_parses_bool_values_and_paths(self):
        root = prepare_case_dir("config")
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
  exclude_keywords:
    - menu
  per_child:
    Alice:
      exclude_keywords:
        - fundraiser
runtime:
  stop_after_existing_stories: false
  full_scan_on_first_run: true
  story_delay_seconds: 1.5
  max_stories_per_run: 3
classifier:
  enabled: false
google_photos:
  enabled: false
""".strip(),
            encoding="utf-8",
        )

        config = load_config(config_path)

        self.assertEqual(config.storypark.credential_target, "demo-target")
        self.assertFalse(config.runtime.stop_after_existing_stories)
        self.assertTrue(config.runtime.full_scan_on_first_run)
        self.assertEqual(config.runtime.story_delay_seconds, 1.5)
        self.assertEqual(config.runtime.max_stories_per_run, 3)
        self.assertEqual(config.filters.exclude_keywords, ["menu"])
        self.assertEqual(config.filters.per_child["Alice"].exclude_keywords, ["fundraiser"])
        self.assertEqual(config.paths.archive, root / "archive")


if __name__ == "__main__":
    unittest.main()
