from __future__ import annotations

import argparse
from getpass import getpass
import json
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
    serve_parser.add_argument("--port", type=int, default=8082, help="Port to listen on (default: 8082)")

    subparsers.add_parser("status", help="Print the current status payload derived from SQLite state")
    return parser


def main(argv: list[str] | None = None) -> int:
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
        return run_webserver(config_path, port=args.port)

    if args.command == "status":
        config = load_config(config_path)
        store = StateStore(config.paths.state_db)
        try:
            store.initialize()
            print(json.dumps(build_status_payload(store), indent=2, sort_keys=True))
        finally:
            store.close()
        return 0

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
