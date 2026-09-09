from __future__ import annotations

import argparse
from getpass import getpass
import json
import os
from pathlib import Path

from storypark_scraper.app import StoryparkApplication
from storypark_scraper.config import load_config
from storypark_scraper.credentials import store_credentials
from storypark_scraper.logging_utils import configure_logging
from storypark_scraper.state import StateStore
from storypark_scraper.status import build_status_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive Storypark stories to local Markdown and media folders.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the Storypark archival sync")
    run_parser.add_argument("--full-scan", action="store_true", help="Scroll through the full Storypark history")
    run_parser.add_argument(
        "--reprocess-existing",
        action="store_true",
        help="Revisit stories already present in SQLite state",
    )
    run_parser.add_argument(
        "--max-stories",
        type=int,
        default=None,
        help="Maximum number of story detail pages to process in this run",
    )
    run_parser.add_argument(
        "--max-stories-per-child",
        type=int,
        default=None,
        help="Maximum number of story detail pages to process for each child in this run",
    )
    run_parser.add_argument(
        "--story-delay-seconds",
        type=float,
        default=None,
        help="Delay between processed stories to reduce load on Storypark",
    )
    run_parser.add_argument(
        "--disable-filters",
        action="store_true",
        help="Archive stories without applying the configured exclude filters",
    )
    run_parser.add_argument("--log-level", default="INFO", help="Logging level")

    credential_parser = subparsers.add_parser(
        "store-credentials",
        help="Store Storypark credentials in Windows Credential Manager",
    )
    credential_parser.add_argument("--username", required=True, help="Storypark username/email")
    credential_parser.add_argument(
        "--target",
        default=None,
        help="Credential target name override; defaults to storypark.credential_target from config.yaml",
    )

    serve_parser = subparsers.add_parser("serve", help="Run a basic webserver to expose the status JSON")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host address to bind to (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8082, help="Port to listen on (default: 8082)")
    serve_parser.add_argument(
        "--auth-token",
        default=os.getenv("SP_WEB_TOKEN"),
        help="Optional bearer token or ?token= required to access status endpoint (or set SP_WEB_TOKEN)",
    )

    subparsers.add_parser("status", help="Print the current status payload derived from SQLite state")

    mqtt_parser = subparsers.add_parser(
        "mqtt-publish",
        help="Publish Home Assistant MQTT discovery and current status telemetry",
    )
    mqtt_parser.add_argument(
        "--status",
        default=None,
        help="Override status state (e.g. idle, success, failed)",
    )
    return parser


import sys


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()

    if args.command == "store-credentials":
        config = load_config(config_path)
        password = getpass("Storypark password: ")
        outcome = store_credentials(
            config.root_dir,
            args.target or config.storypark.credential_target,
            args.username,
            password,
        )
        if outcome.backend == "windows-credential-manager":
            print("Stored Storypark credentials in Windows Credential Manager.")
        else:
            print(f"Stored Storypark credentials in encrypted local fallback file: {outcome.path}")
            if outcome.warning:
                print(f"Credential Manager storage failed first: {outcome.warning}")
        return 0

    if args.command == "serve":
        from storypark_scraper.webserver import run_webserver
        return run_webserver(config_path, port=args.port, host=args.host, auth_token=args.auth_token)

    if args.command == "status":
        config = load_config(config_path)
        store = StateStore(config.paths.state_db)
        try:
            store.initialize()
            print(json.dumps(build_status_payload(store), indent=2, sort_keys=True))
        finally:
            store.close()
        return 0

    if args.command == "mqtt-publish":
        from storypark_scraper.mqtt import HomeAssistantMQTTPublisher, build_telemetry_payload
        from storypark_scraper.state import utc_now_iso
        config = load_config(config_path)
        store = StateStore(config.paths.state_db)
        logger = configure_logging("INFO")
        publisher = HomeAssistantMQTTPublisher(config.mqtt, logger=logger)
        try:
            store.initialize()
            status_payload = build_status_payload(store)
            children = list(status_payload.get("children", {}).keys())
            print(f"Publishing Home Assistant discovery for {len(children)} children...")
            publisher.publish_discovery(children)

            stories_total, photos_total = store.get_total_counts()
            last_run = (
                status_payload.get("last_run_finished_at")
                or status_payload.get("last_run_started_at")
                or utc_now_iso()
            )
            telemetry = build_telemetry_payload(
                status=args.status or status_payload.get("state", "idle"),
                problem=status_payload.get("state") == "failed",
                last_run=last_run,
                duration_seconds=0.0,
                stories_total=stories_total,
                photos_total=photos_total,
                children_stats=status_payload.get("children", {}),
            )
            print("Publishing telemetry payload...")
            success = publisher.publish_telemetry(telemetry)
            if success:
                print("Successfully published MQTT discovery and telemetry.")
                return 0
            else:
                print("Failed to publish MQTT messages (or MQTT is disabled in config).")
                return 1
        finally:
            store.close()

    logger = configure_logging(args.log_level)
    app = StoryparkApplication(config_path, logger)
    return app.run(
        full_scan=args.full_scan,
        reprocess_existing=args.reprocess_existing,
        max_stories=args.max_stories,
        max_stories_per_child=args.max_stories_per_child,
        story_delay_seconds=args.story_delay_seconds,
        disable_filters=args.disable_filters,
    )
