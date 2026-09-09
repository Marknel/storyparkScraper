from __future__ import annotations

from typing import Any
import json
import logging
import re
import socket

try:
    import paho.mqtt.client as mqtt
    HAS_PAHO = True
except ImportError:  # pragma: no cover
    HAS_PAHO = False

from storypark_scraper.config import MQTTConfig


logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower())
    return cleaned.strip("_")


def build_discovery_payloads(
    config: MQTTConfig, child_names: list[str] | None = None
) -> list[tuple[str, dict[str, Any]]]:
    discovery_prefix = config.discovery_prefix.rstrip("/")
    topic_prefix = config.topic_prefix.rstrip("/")
    state_topic = f"{topic_prefix}/state"
    availability_topic = f"{topic_prefix}/availability"

    device = {
        "identifiers": ["storypark_scraper"],
        "name": "Storypark Scraper",
        "manufacturer": "Marknel",
        "model": "Storypark Scraper",
        "sw_version": "1.0.0",
    }

    base_entity = {
        "device": device,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
    }

    payloads: list[tuple[str, dict[str, Any]]] = []

    # 1. Scraper Status Sensor
    payloads.append((
        f"{discovery_prefix}/sensor/storypark_status/config",
        {
            **base_entity,
            "name": "Storypark Scraper Status",
            "unique_id": "storypark_scraper_status",
            "state_topic": state_topic,
            "value_template": "{{ value_json.status }}",
            "icon": "mdi:cloud-sync",
        },
    ))

    # 2. Problem Binary Sensor (Alert)
    payloads.append((
        f"{discovery_prefix}/binary_sensor/storypark_problem/config",
        {
            **base_entity,
            "name": "Storypark Scraper Problem",
            "unique_id": "storypark_scraper_problem",
            "state_topic": state_topic,
            "value_template": "{{ value_json.problem }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "problem",
        },
    ))

    # 3. Last Run Timestamp
    payloads.append((
        f"{discovery_prefix}/sensor/storypark_last_run/config",
        {
            **base_entity,
            "name": "Storypark Last Run",
            "unique_id": "storypark_scraper_last_run",
            "state_topic": state_topic,
            "value_template": "{{ value_json.last_run }}",
            "device_class": "timestamp",
        },
    ))

    # 4. Last Run Duration
    payloads.append((
        f"{discovery_prefix}/sensor/storypark_last_run_duration/config",
        {
            **base_entity,
            "name": "Storypark Last Run Duration",
            "unique_id": "storypark_scraper_last_run_duration",
            "state_topic": state_topic,
            "value_template": "{{ value_json.duration_seconds }}",
            "unit_of_measurement": "s",
            "icon": "mdi:timer-outline",
        },
    ))

    # 5. Stories Total
    payloads.append((
        f"{discovery_prefix}/sensor/storypark_stories_total/config",
        {
            **base_entity,
            "name": "Storypark Stories Total",
            "unique_id": "storypark_scraper_stories_total",
            "state_topic": state_topic,
            "value_template": "{{ value_json.stories_total }}",
            "state_class": "total_increasing",
            "icon": "mdi:book-open-page-variant",
        },
    ))

    # 6. Photos Total
    payloads.append((
        f"{discovery_prefix}/sensor/storypark_photos_total/config",
        {
            **base_entity,
            "name": "Storypark Photos Total",
            "unique_id": "storypark_scraper_photos_total",
            "state_topic": state_topic,
            "value_template": "{{ value_json.photos_total }}",
            "state_class": "total_increasing",
            "icon": "mdi:image-multiple",
        },
    ))

    # 7. Per-child sensors
    if child_names:
        for child in child_names:
            slug = _slugify(child)
            # Story count
            payloads.append((
                f"{discovery_prefix}/sensor/storypark_{slug}_stories/config",
                {
                    **base_entity,
                    "name": f"Storypark {child} Stories",
                    "unique_id": f"storypark_scraper_{slug}_stories",
                    "state_topic": state_topic,
                    "value_template": f"{{{{ value_json.children['{child}'].stories if '{child}' in value_json.children else 0 }}}}",
                    "state_class": "total_increasing",
                    "icon": "mdi:book-account",
                },
            ))
            # Photo count
            payloads.append((
                f"{discovery_prefix}/sensor/storypark_{slug}_photos/config",
                {
                    **base_entity,
                    "name": f"Storypark {child} Photos",
                    "unique_id": f"storypark_scraper_{slug}_photos",
                    "state_topic": state_topic,
                    "value_template": f"{{{{ value_json.children['{child}'].photos if '{child}' in value_json.children else 0 }}}}",
                    "state_class": "total_increasing",
                    "icon": "mdi:image-account",
                },
            ))

    return payloads


def build_telemetry_payload(
    status: str,
    problem: bool,
    last_run: str,
    duration_seconds: float = 0.0,
    stories_total: int = 0,
    photos_total: int = 0,
    last_run_stories: int = 0,
    last_run_photos: int = 0,
    children_stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    formatted_children: dict[str, dict[str, int]] = {}
    if children_stats:
        for child, stats in children_stats.items():
            formatted_children[child] = {
                "stories": int(stats.get("total_stories", 0)),
                "photos": int(stats.get("total_photos", stats.get("google_photos_suitable_pictures", 0))),
            }

    return {
        "status": status,
        "problem": "ON" if problem else "OFF",
        "last_run": last_run,
        "duration_seconds": round(duration_seconds, 2),
        "stories_total": stories_total,
        "photos_total": photos_total,
        "last_run_stories": last_run_stories,
        "last_run_photos": last_run_photos,
        "error": error,
        "children": formatted_children,
    }


def _publish_messages(
    config: MQTTConfig,
    messages: list[tuple[str, str, bool]],
    availability_online: bool = True,
    log=None,
) -> None:
    if not HAS_PAHO:  # pragma: no cover
        raise RuntimeError("paho-mqtt is not installed")

    log = log or logger

    # Paho 2.0 vs 1.x compatibility
    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="storypark_scraper")
    else:  # pragma: no cover
        client = mqtt.Client(client_id="storypark_scraper")

    if config.username:
        client.username_pw_set(config.username, config.password or None)

    if config.tls:
        client.tls_set()

    availability_topic = f"{config.topic_prefix.rstrip('/')}/availability"
    client.will_set(availability_topic, payload="offline", qos=1, retain=True)

    client.connect(config.host, config.port, keepalive=int(config.timeout_seconds * 2))

    if availability_online:
        client.publish(availability_topic, payload="online", qos=1, retain=True)

    for topic, payload, retain in messages:
        client.publish(topic, payload=payload, qos=1, retain=retain)

    client.disconnect()


class HomeAssistantMQTTPublisher:
    def __init__(self, config: MQTTConfig, logger=None):
        self._config = config
        self._logger = logger or logging.getLogger(__name__)

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def publish_discovery(self, child_names: list[str] | None = None) -> bool:
        if not self._config.enabled:
            return False

        try:
            payloads = build_discovery_payloads(self._config, child_names)
            messages = [
                (topic, json.dumps(payload), self._config.retain)
                for topic, payload in payloads
            ]
            _publish_messages(self._config, messages, availability_online=True, log=self._logger)
            self._logger.info(f"Published Home Assistant MQTT discovery for {len(messages)} entities to {self._config.host}:{self._config.port}")
            return True
        except Exception as exc:
            self._logger.warning(f"Failed to publish Home Assistant MQTT discovery: {exc}")
            return False

    def publish_telemetry(self, telemetry: dict[str, Any]) -> bool:
        if not self._config.enabled:
            return False

        try:
            topic_prefix = self._config.topic_prefix.rstrip("/")
            state_topic = f"{topic_prefix}/state"
            state_json = json.dumps(telemetry)

            messages = [(state_topic, state_json, self._config.retain)]

            # If an error occurred, optionally publish error detail
            if telemetry.get("error"):
                error_topic = f"{topic_prefix}/error"
                messages.append((error_topic, str(telemetry["error"]), False))

            _publish_messages(self._config, messages, availability_online=True, log=self._logger)
            self._logger.info(f"Published Storypark MQTT telemetry ({telemetry.get('status')}) to {self._config.host}:{self._config.port}")
            return True
        except Exception as exc:
            self._logger.warning(f"Failed to publish Storypark MQTT telemetry: {exc}")
            return False
