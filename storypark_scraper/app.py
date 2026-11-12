from __future__ import annotations

from pathlib import Path
import time

from storypark_scraper.archive import ArchiveWriter
from storypark_scraper.browser import StoryparkBrowser
from storypark_scraper.config import AppConfig, load_config
from storypark_scraper.credentials import load_credentials
from storypark_scraper.downloader import AssetDownloader
from storypark_scraper.filters import StoryFilter
from storypark_scraper.locking import acquire_run_lock
from storypark_scraper.models import ChildTarget, Credentials, FilterDecision, RunStats, StoryContent
from storypark_scraper.mqtt import HomeAssistantMQTTPublisher, build_telemetry_payload
from storypark_scraper.state import StateStore, utc_now_iso
from storypark_scraper.status import build_status_payload, write_status_file


class StoryparkApplication:
    def __init__(self, config_path: Path, logger):
        self._config: AppConfig = load_config(config_path)
        self._logger = logger
        if self._config.google_photos.enabled:
            raise NotImplementedError(
                "Google Photos upload is intentionally deferred in this release. "
                "Keep google_photos.enabled=false until the uploader is implemented."
            )
        self._filter = StoryFilter(self._config.filters)
        self._state = StateStore(self._config.paths.state_db)
        self._downloader = AssetDownloader(
            timeout_seconds=self._config.runtime.download_timeout_seconds,
            user_agent=self._config.runtime.user_agent,
        )
        self._archive_writer = ArchiveWriter(self._config.paths.archive, self._downloader)
        self._mqtt = HomeAssistantMQTTPublisher(self._config.mqtt, self._logger)

    def run(
        self,
        *,
        full_scan: bool = False,
        reprocess_existing: bool = False,
        max_stories: int | None = None,
        max_stories_per_child: int | None = None,
        story_delay_seconds: float | None = None,
        disable_filters: bool = False,
        allow_paused: bool = False,
    ) -> int:
        self._config.ensure_directories()
        self._state.initialize()
        if not self._config.runtime.enabled and not allow_paused:
            try:
                self._logger.info("Scraper sync is paused (runtime.enabled=false); skipping run.")
                status_payload = build_status_payload(self._state, state="paused")
                status_payload["last_error"] = None
                write_status_file(self._config.paths.status_file, status_payload)
                stories_total, photos_total = self._state.get_total_counts()
                last_run = (
                    status_payload.get("last_run_finished_at")
                    or status_payload.get("last_run_started_at")
                    or utc_now_iso()
                )
                self._mqtt.publish_telemetry(
                    build_telemetry_payload(
                        status="paused",
                        problem=False,
                        last_run=str(last_run),
                        duration_seconds=0.0,
                        stories_total=stories_total,
                        photos_total=photos_total,
                        children_stats=status_payload.get("children", {}),
                    )
                )
                return 0
            finally:
                self._downloader.close()
                self._state.close()
        stats = RunStats()
        latest_acquired_at: str | None = None
        run_id: str | None = None
        processed_story_count = 0

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

        start_time = time.time()
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
                self._mqtt.publish_telemetry(
                    build_telemetry_payload(status="running", problem=False, last_run=utc_now_iso())
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
                    },
                )

                with StoryparkBrowser(self._config, credential_provider, self._logger) as browser:
                    children = browser.discover_children()
                    self._sync_download_session(browser)
                    self._mqtt.publish_discovery([child.name for child in children])
                    known_child_aliases = _known_child_aliases(children)
                    existing_threshold = max(
                        1, self._config.runtime.consecutive_existing_stories_threshold
                    )
                    for child in children:
                        stop_child_scan = False
                        processed_child_story_count = 0
                        consecutive_existing_count = 0
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
                                consecutive_existing_count += 1
                                self._logger.info(
                                    "Encountered existing story",
                                    extra={
                                        "story_id": story_id,
                                        "child_name": child.name,
                                        "consecutive_existing": consecutive_existing_count,
                                        "threshold": existing_threshold,
                                    },
                                )
                                if not effective_full_scan and self._config.runtime.stop_after_existing_stories:
                                    if consecutive_existing_count >= existing_threshold:
                                        stop_child_scan = True
                                        break
                                continue

                            consecutive_existing_count = 0
                            story = browser.fetch_story(child, story_id)
                            self._sync_download_session(browser)
                            processed_story_count += 1
                            processed_child_story_count += 1
                            story.google_photos_candidate = _is_google_photos_candidate(story, known_child_aliases)
                            story.semantic_labels = []
                            decision = _decide_story(self._filter, story, disable_filters)

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

                self._state.finish_run(
                    run_id=run_id,
                    state="success",
                    stats=stats,
                    last_story_acquired_at=latest_acquired_at,
                    last_error=None,
                )
                duration = time.time() - start_time
                status_payload = build_status_payload(self._state)
                write_status_file(self._config.paths.status_file, status_payload)
                stories_total, photos_total = self._state.get_total_counts()
                self._mqtt.publish_telemetry(
                    build_telemetry_payload(
                        status="success",
                        problem=False,
                        last_run=utc_now_iso(),
                        duration_seconds=duration,
                        stories_total=stories_total,
                        photos_total=photos_total,
                        last_run_stories=processed_story_count,
                        last_run_photos=stats.stories_downloaded,
                        children_stats=status_payload.get("children", {}),
                    )
                )
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
            stories_total, photos_total = (0, 0)
            try:
                stories_total, photos_total = self._state.get_total_counts()
            except Exception:
                pass
            self._mqtt.publish_telemetry(
                build_telemetry_payload(
                    status="failed",
                    problem=True,
                    last_run=utc_now_iso(),
                    duration_seconds=time.time() - start_time,
                    stories_total=stories_total,
                    photos_total=photos_total,
                    last_run_stories=processed_story_count,
                    last_run_photos=stats.stories_downloaded,
                    error=str(exc),
                )
            )
            raise
        finally:
            self._downloader.close()
            self._state.close()

    def _sync_download_session(self, browser) -> None:
        get_cookies = getattr(browser, "get_cookies", None)
        if callable(get_cookies):
            self._downloader.set_cookies(get_cookies())
        get_user_agent = getattr(browser, "get_user_agent", None)
        if callable(get_user_agent):
            user_agent = get_user_agent()
            if user_agent:
                self._downloader.set_user_agent(user_agent)


def _decide_story(story_filter: StoryFilter, story, disable_filters: bool) -> FilterDecision:
    if disable_filters:
        return FilterDecision(include=True, reason="accepted because filters are disabled")
    return story_filter.decide(story)


def _smallest_positive_limit(*values: int | None) -> int | None:
    candidates = [value for value in values if value is not None and value > 0]
    if not candidates:
        return None
    return min(candidates)


def _known_child_aliases(children: list[ChildTarget]) -> set[str]:
    aliases: set[str] = set()
    for child in children:
        aliases.update(_child_name_aliases(child.name))
    return aliases


def _is_google_photos_candidate(story: StoryContent, known_child_aliases: set[str]) -> bool:
    if not story.included_children:
        return False
    for included_child in story.included_children:
        if not (_child_name_aliases(included_child) & known_child_aliases):
            return False
    return True


def _child_name_aliases(name: str) -> set[str]:
    cleaned = " ".join(name.split()).strip()
    if not cleaned:
        return set()
    aliases = {cleaned.casefold()}
    aliases.add(cleaned.split()[0].casefold())
    return aliases
