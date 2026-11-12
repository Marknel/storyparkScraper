from __future__ import annotations

import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from storypark_scraper.browser import AuthenticationRequired
from storypark_scraper.cli import build_parser, main


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
                "--allow-paused",
            ]
        )

        self.assertEqual(args.command, "run")
        self.assertTrue(args.full_scan)
        self.assertEqual(args.max_stories, 3)
        self.assertEqual(args.max_stories_per_child, 5)
        self.assertEqual(args.story_delay_seconds, 2.5)
        self.assertTrue(args.disable_filters)
        self.assertTrue(args.allow_paused)

    def test_login_uses_shared_profile_lock_without_creating_a_scrape_run(self):
        config = SimpleNamespace(
            root_dir=Path("test-root"), paths=SimpleNamespace(lock_file=Path("test-root/run.lock")),
            runtime=SimpleNamespace(auth_timeout_seconds=600, auth_assist_port=8000),
            storypark=SimpleNamespace(credential_target="test"), ensure_directories=MagicMock(),
        )
        output = StringIO()
        with patch("storypark_scraper.cli.load_config", return_value=config), \
             patch("storypark_scraper.cli.acquire_run_lock", return_value=nullcontext()) as lock, \
             patch("storypark_scraper.cli.StoryparkBrowser") as browser_type, \
             patch("storypark_scraper.cli.StateStore") as state, redirect_stdout(output):
            result = main(["login", "--headless", "--host", "127.0.0.1", "--port", "0"])
        self.assertEqual(result, 0)
        lock.assert_called_once_with(config.paths.lock_file)
        self.assertTrue(browser_type.call_args.kwargs["headless"])
        options = browser_type.return_value.__enter__.return_value.authenticate.call_args.kwargs
        self.assertTrue(options["interactive"])
        self.assertTrue(options["assist"])
        self.assertEqual(options["timeout_seconds"], 600)
        self.assertEqual(options["port"], 0)
        self.assertTrue(callable(options["on_ready"]))
        state.assert_not_called()
        self.assertIn("sign-in verified", output.getvalue())

    def test_login_timeout_has_nonzero_exit_and_does_not_claim_success(self):
        config = SimpleNamespace(
            paths=SimpleNamespace(lock_file=Path("test.lock")),
            runtime=SimpleNamespace(auth_timeout_seconds=600, auth_assist_port=8000),
            ensure_directories=MagicMock(),
        )
        output, error = StringIO(), StringIO()
        with patch("storypark_scraper.cli.load_config", return_value=config), \
             patch("storypark_scraper.cli.acquire_run_lock", return_value=nullcontext()), \
             patch("storypark_scraper.cli.StoryparkBrowser") as browser_type, \
             redirect_stdout(output), redirect_stderr(error):
            browser_type.return_value.__enter__.return_value.authenticate.side_effect = AuthenticationRequired("Owner sign-in expired")
            result = main(["login", "--headless"])
        self.assertEqual(result, 2)
        self.assertNotIn("sign-in verified", output.getvalue())
        self.assertIn("Owner sign-in expired", error.getvalue())
        browser_type.return_value.__exit__.assert_called_once()

    def test_unattended_auth_failure_returns_actionable_nonzero_exit(self):
        error = StringIO()
        with patch("storypark_scraper.cli.StoryparkApplication") as app, redirect_stderr(error):
            app.return_value.run.side_effect = AuthenticationRequired("Use the login command")
            result = main(["run", "--max-stories", "1"])
        self.assertEqual(result, 2)
        self.assertIn("login command", error.getvalue())

    def test_mqtt_override_controls_both_status_and_problem_flag(self):
        for status, problem in [("success", False), ("failed", True), ("interrupted", True), ("paused", False)]:
            with self.subTest(status=status), \
                 patch("storypark_scraper.cli.load_config") as config, \
                 patch("storypark_scraper.cli.StateStore") as store, \
                 patch("storypark_scraper.cli.build_status_payload", return_value={"state": "failed"}), \
                 patch("storypark_scraper.mqtt.HomeAssistantMQTTPublisher") as publisher, \
                 patch("storypark_scraper.mqtt.build_telemetry_payload") as payload, redirect_stdout(StringIO()):
                store.return_value.get_total_counts.return_value = (1, 2)
                publisher.return_value.publish_telemetry.return_value = True
                self.assertEqual(main(["mqtt-publish", "--status", status]), 0)
                self.assertEqual(payload.call_args.kwargs["status"], status)
                self.assertEqual(payload.call_args.kwargs["problem"], problem)


if __name__ == "__main__":
    unittest.main()
