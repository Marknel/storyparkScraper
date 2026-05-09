from __future__ import annotations

from pathlib import Path
import json

from storypark_scraper.models import RunStats
from storypark_scraper.state import StateStore


def build_status_payload(
    state_store: StateStore,
    *,
    state: str | None = None,
    stats: RunStats | None = None,
    last_error: str | None = None,
) -> dict[str, object]:
    latest_run = state_store.get_latest_run()
    payload = {
        "version": 1,
        "state": state or (latest_run["state"] if latest_run else "idle"),
        "last_run_started_at": latest_run["started_at"] if latest_run else None,
        "last_run_finished_at": latest_run["finished_at"] if latest_run else None,
        "last_success_at": state_store.get_last_success_at(),
        "last_story_acquired_at": state_store.get_last_story_acquired_at(),
        "stories_seen": stats.stories_seen if stats else (latest_run["stories_seen"] if latest_run else 0),
        "stories_downloaded": stats.stories_downloaded
        if stats
        else (latest_run["stories_downloaded"] if latest_run else 0),
        "stories_skipped": stats.stories_skipped if stats else (latest_run["stories_skipped"] if latest_run else 0),
        "last_error": last_error if last_error is not None else (latest_run["last_error"] if latest_run else None),
        "children": state_store.get_child_stats(),
    }
    return payload


def write_status_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
