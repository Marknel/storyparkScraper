from __future__ import annotations

from pathlib import Path
import json
import unittest
from unittest.mock import MagicMock, patch

from storypark_scraper.config import MQTTConfig
from storypark_scraper.mqtt import (
    HomeAssistantMQTTPublisher,
    build_discovery_payloads,
    build_telemetry_payload,
)


class MQTTTests(unittest.TestCase):
    def setUp(self):
        self.config = MQTTConfig(
            enabled=True,
            host="127.0.0.1",
            port=1883,
            discovery_prefix="homeassistant",
            topic_prefix="storypark",
            retain=True,
            timeout_seconds=2,
        )

    def test_build_discovery_payloads_includes_core_entities_and_children(self):
        children = ["Samuel Cracknell", "Lyra Cracknell"]
        payloads = build_discovery_payloads(self.config, children)

        topics = [topic for topic, _ in payloads]
        # Core entities
        self.assertIn("homeassistant/sensor/storypark_status/config", topics)
        self.assertIn("homeassistant/binary_sensor/storypark_problem/config", topics)
        self.assertIn("homeassistant/sensor/storypark_last_run/config", topics)
        self.assertIn("homeassistant/sensor/storypark_last_run_duration/config", topics)
        self.assertIn("homeassistant/sensor/storypark_stories_total/config", topics)
        self.assertIn("homeassistant/sensor/storypark_photos_total/config", topics)

        # Per-child entities
        self.assertIn("homeassistant/sensor/storypark_samuel_cracknell_stories/config", topics)
        self.assertIn("homeassistant/sensor/storypark_samuel_cracknell_photos/config", topics)
        self.assertIn("homeassistant/sensor/storypark_lyra_cracknell_stories/config", topics)
        self.assertIn("homeassistant/sensor/storypark_lyra_cracknell_photos/config", topics)

        # Inspect problem binary sensor payload
        problem_payload = next(p for t, p in payloads if "storypark_problem" in t)
        self.assertEqual(problem_payload["name"], "Problem")
        self.assertTrue(problem_payload["has_entity_name"])
        self.assertEqual(problem_payload["device_class"], "problem")
        self.assertEqual(problem_payload["payload_on"], "ON")
        self.assertEqual(problem_payload["payload_off"], "OFF")
        self.assertEqual(problem_payload["device"]["name"], "Storypark Scraper")
        self.assertEqual(problem_payload["availability_topic"], "storypark/availability")

        # Inspect duration sensor
        duration_payload = next(p for t, p in payloads if "storypark_last_run_duration" in t)
        self.assertEqual(duration_payload["name"], "Duration")
        self.assertEqual(duration_payload["device_class"], "duration")
        self.assertEqual(duration_payload["unit_of_measurement"], "s")

        # Inspect total stories sensor
        stories_payload = next(p for t, p in payloads if "storypark_stories_total" in t)
        self.assertEqual(stories_payload["name"], "Total Stories")
        self.assertEqual(stories_payload["state_class"], "total_increasing")
        self.assertEqual(stories_payload["unit_of_measurement"], "stories")

        # Inspect total photos sensor
        photos_payload = next(p for t, p in payloads if "storypark_photos_total" in t)
        self.assertEqual(photos_payload["name"], "Total Photos")
        self.assertEqual(photos_payload["state_class"], "total_increasing")
        self.assertEqual(photos_payload["unit_of_measurement"], "photos")

        # Inspect child sensors
        sam_stories = next(p for t, p in payloads if "storypark_samuel_cracknell_stories" in t)
        self.assertEqual(sam_stories["name"], "Samuel Cracknell Stories")
        self.assertEqual(sam_stories["state_class"], "total_increasing")
        self.assertEqual(sam_stories["unit_of_measurement"], "stories")

        sam_photos = next(p for t, p in payloads if "storypark_samuel_cracknell_photos" in t)
        self.assertEqual(sam_photos["name"], "Samuel Cracknell Photos")
        self.assertEqual(sam_photos["state_class"], "total_increasing")
        self.assertEqual(sam_photos["unit_of_measurement"], "photos")

    def test_build_telemetry_payload_success(self):
        children_stats = {
            "Samuel Cracknell": {"total_stories": 369, "total_photos": 589},
            "Lyra Cracknell": {"total_stories": 65, "total_photos": 342},
        }
        telemetry = build_telemetry_payload(
            status="success",
            problem=False,
            last_run="2026-09-09T17:00:00Z",
            duration_seconds=42.5,
            stories_total=434,
            photos_total=931,
            last_run_stories=10,
            last_run_photos=0,
            children_stats=children_stats,
        )

        self.assertEqual(telemetry["status"], "success")
        self.assertEqual(telemetry["problem"], "OFF")
        self.assertEqual(telemetry["stories_total"], 434)
        self.assertEqual(telemetry["children"]["Samuel Cracknell"]["stories"], 369)
        self.assertIsNone(telemetry["error"])

    def test_build_telemetry_payload_failure(self):
        telemetry = build_telemetry_payload(
            status="failed",
            problem=True,
            last_run="2026-09-09T17:00:00Z",
            duration_seconds=12.1,
            stories_total=434,
            photos_total=931,
            error="Connection to Storypark timed out",
        )

        self.assertEqual(telemetry["status"], "failed")
        self.assertEqual(telemetry["problem"], "ON")
        self.assertEqual(telemetry["error"], "Connection to Storypark timed out")

    def test_publisher_disabled_skips_network_calls(self):
        disabled_config = MQTTConfig(enabled=False)
        publisher = HomeAssistantMQTTPublisher(disabled_config)

        self.assertFalse(publisher.publish_discovery(["Alice"]))
        self.assertFalse(publisher.publish_telemetry({"status": "success"}))

    @patch("storypark_scraper.mqtt._publish_messages")
    def test_publisher_handles_network_exceptions_gracefully(self, mock_publish):
        mock_publish.side_effect = ConnectionRefusedError("Broker refused connection")
        logger = MagicMock()
        publisher = HomeAssistantMQTTPublisher(self.config, logger=logger)

        # Should return False and log a warning, NOT crash
        result = publisher.publish_telemetry({"status": "success"})
        self.assertFalse(result)
        logger.warning.assert_called()


if __name__ == "__main__":
    unittest.main()
