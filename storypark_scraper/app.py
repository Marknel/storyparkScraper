from __future__ import annotations

from pathlib import Path
import time

from storypark_scraper.archive import ArchiveWriter
from storypark_scraper.browser import StoryparkBrowser
from storypark_scraper.classification import build_classifier
from storypark_scraper.config import AppConfig, load_config
from storypark_scraper.credentials import load_credentials
from storypark_scraper.downloader import AssetDownloader
from storypark_scraper.filters import StoryFilter
from storypark_scraper.google_photos import validate_google_photos_enabled
from storypark_scraper.locking import acquire_run_lock
from storypark_scraper.models import Credentials, FilterDecision, RunStats
from storypark_scraper.state import StateStore, utc_now_iso
from storypark_scraper.status import build_status_payload, write_status_file


class StoryparkApplication:
    def __init__(self, config_path: Path, logger):
        self._config: AppConfig = load_config(config_path)
        self._logger = logger
        validate_google_photos_enabled(self._config.google_photos.enabled)
        self._classifier = build_classifier(
            enabled=self._config.classifier.enabled,
            provider=self._config.classifier.provider,
        )
        self._filter = StoryFilter(self._config.filters)
        self._state = StateStore(self._config.paths.state_db)
        self._downloader = AssetDownloader(
            timeout_seconds=self._config.runtime.download_timeout_seconds,
            user_agent=self._config.runtime.user_agent,
        )
        self._archive_writer = ArchiveWriter(self._config.paths.archive, self._downloader)

    def run(
        self,
        *,
        full_scan: bool = False,
        reprocess_existing: bool = False,
        max_stories: int | None = None,
        max_stories_per_child: int | None = None,
        story_delay_seconds: float | None = None,
        disable_filters: bool = False,
        stop_on_video: bool = False,
    ) -> int:
        self._config.ensure_directories()
        self._state.initialize()
        stats = RunStats()
        latest_acquired_at: str | None = None
        run_id: str | None = None
        processed_story_count = 0
        stop_run = False

        effective_max_stories = max_stories if max_stories is not None else self._config.runtime.max_stories_per_run
        if effective_max_stories is not None and effective_max_stories <= 0:
            effective_max_stories = None

        effective_max_stories_per_child = max_stories_per_child
        if effective_max_stories_per_child is not None and effective_max_stories_per_child <= 0:
            effective_max_stories_per_child = None

        effective_story_delay = (
            story_delay_seconds if story_delay_seconds is not None else self._config.runtime.story_delay_seconds
        )
        if effective_story_delay < 0:
            raise ValueError("story_delay_seconds must be zero or greater")

        credential_cache: Credentials | None = None

        def credential_provider() -> Credentials:
            nonlocal credential_cache
            if credential_cache is None:
                credential_cache = load_credentials(
                    self._config.root_dir,
                    self._config.storypark.credential_target,
                )
            return credential_cache

        try:
            with acquire_run_lock(self._config.paths.lock_file):
                run_id = self._state.start_run()
                effective_full_scan = full_scan or (
                    self._config.runtime.full_scan_on_first_run and not self._state.has_any_successful_run()
                )
                write_status_file(
                    self._config.paths.status_file,
                    build_status_payload(self._state, state="running", stats=stats, last_error=None),
                )
                self._logger.info(
                    "Starting scraper run",
                    extra={
                        "run_id": run_id,
                        "full_scan": effective_full_scan,
                        "max_stories": effective_max_stories,
                        "max_stories_per_child": effective_max_stories_per_child,
                        "story_delay_seconds": effective_story_delay,
                        "disable_filters": disable_filters,
                        "stop_on_video": stop_on_video,
                    },
                )

                with StoryparkBrowser(self._config, credential_provider, self._logger) as browser:
                    children = browser.discover_children()
                    for child in children:
                        stop_child_scan = False
                        processed_child_story_count = 0
                        remaining_global = None
                        if effective_max_stories is not None:
                            remaining_global = max(effective_max_stories - processed_story_count, 0)
                        collect_cap = _smallest_positive_limit(remaining_global, effective_max_stories_per_child)
                        story_ids = browser.collect_story_ids(
                            child,
                            full_scan=effective_full_scan,
                            max_story_ids=collect_cap,
                        )
                        self._logger.info(
                            "Collected story ids",
                            extra={"child_name": child.name, "story_count": len(story_ids)},
                        )
                        for story_id in story_ids:
                            if effective_max_stories is not None and processed_story_count >= effective_max_stories:
                                stop_run = True
                                self._logger.info(
                                    "Reached per-run story cap",
                                    extra={"max_stories": effective_max_stories},
                                )
                                break
                            if (
                                effective_max_stories_per_child is not None
                                and processed_child_story_count >= effective_max_stories_per_child
                            ):
                                self._logger.info(
                                    "Reached per-child story cap",
                                    extra={
                                        "child_name": child.name,
                                        "max_stories_per_child": effective_max_stories_per_child,
                                    },
                                )
                                break

                            stats.stories_seen += 1
                            if not reprocess_existing and self._state.story_exists(story_id, child.name):
                                self._logger.info(
                                    "Encountered existing story",
                                    extra={"story_id": story_id, "child_name": child.name},
                                )
                                if not effective_full_scan and self._config.runtime.stop_after_existing_stories:
                                    stop_child_scan = True
                                    break
                                continue

                            story = browser.fetch_story(child, story_id)
                            processed_story_count += 1
                            processed_child_story_count += 1
                            story.semantic_labels = self._classifier.classify(story)
                            decision = _decide_story(self._filter, story, disable_filters)
                            encountered_video = stop_on_video and story.has_video

                            if not decision.include:
                                stats.stories_skipped += 1
                                self._state.upsert_story(
                                    story,
                                    run_id,
                                    decision,
                                    archive_path=None,
                                    assets=[],
                                    downloaded_at=None,
                                )
                                self._logger.info(
                                    "Skipped story",
                                    extra={
                                        "story_id": story.story_id,
                                        "child_name": story.child_name,
                                        "reason": decision.reason,
                                    },
                                )
                            else:
                                acquired_at = utc_now_iso()
                                export_result = self._archive_writer.export_story(story, decision, acquired_at)
                                stats.stories_downloaded += 1
                                latest_acquired_at = acquired_at
                                self._state.upsert_story(
                                    story,
                                    run_id,
                                    decision,
                                    archive_path=str(export_result.story_markdown_path),
                                    assets=export_result.assets,
                                    downloaded_at=acquired_at,
                                )
                                self._logger.info(
                                    "Archived story",
                                    extra={
                                        "story_id": story.story_id,
                                        "child_name": story.child_name,
                                        "asset_count": export_result.asset_count,
                                        "archive_path": str(export_result.story_markdown_path),
                                    },
                                )

                            if encountered_video:
                                stop_run = True
                                self._logger.info(
                                    "Encountered video asset and stopped run",
                                    extra={
                                        "story_id": story.story_id,
                                        "child_name": story.child_name,
                                        "asset_count": len(story.assets),
                                    },
                                )
                                break

                            if effective_story_delay > 0:
                                should_continue_global = (
                                    effective_max_stories is None or processed_story_count < effective_max_stories
                                )
                                should_continue_child = (
                                    effective_max_stories_per_child is None
                                    or processed_child_story_count < effective_max_stories_per_child
                                )
                                if should_continue_global and should_continue_child:
                                    self._logger.info(
                                        "Sleeping between stories",
                                        extra={"story_delay_seconds": effective_story_delay},
                                    )
                                    time.sleep(effective_story_delay)

                        if stop_child_scan:
                            self._logger.info(
                                "Stopped child scan after existing story",
                                extra={"child_name": child.name},
                            )
                        if stop_run:
                            break

                self._state.finish_run(
                    run_id=run_id,
                    state="success",
                    stats=stats,
                    last_story_acquired_at=latest_acquired_at,
                    last_error=None,
                )
                write_status_file(self._config.paths.status_file, build_status_payload(self._state))
                return 0
        except Exception as exc:
            self._logger.exception("Scraper run failed")
            if run_id is not None:
                self._state.finish_run(
                    run_id=run_id,
                    state="failed",
                    stats=stats,
                    last_story_acquired_at=latest_acquired_at,
                    last_error=str(exc),
                )
                write_status_file(
                    self._config.paths.status_file,
                    build_status_payload(self._state, state="failed", stats=stats, last_error=str(exc)),
                )
            raise
        finally:
            self._downloader.close()
            self._state.close()


def _decide_story(story_filter: StoryFilter, story, disable_filters: bool) -> FilterDecision:
    if disable_filters:
        return FilterDecision(include=True, reason="accepted because filters are disabled")
    return story_filter.decide(story)


def _smallest_positive_limit(*values: int | None) -> int | None:
    candidates = [value for value in values if value is not None and value > 0]
    if not candidates:
        return None
    return min(candidates)
