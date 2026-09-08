from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storypark_scraper.yaml_utils import load_yaml_file


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class PathsConfig:
    archive: Path
    state_db: Path
    status_file: Path
    browser_profile: Path
    lock_file: Path


@dataclass(slots=True)
class StoryparkConfig:
    login_url: str
    stories_url_template: str
    credential_target: str
    child_allowlist: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FilterSet:
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_regexes: list[str] = field(default_factory=list)
    exclude_authors: list[str] = field(default_factory=list)
    exclude_content_types: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FiltersConfig(FilterSet):
    per_child: dict[str, FilterSet] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeConfig:
    page_timeout_ms: int = 60_000
    scroll_pause_ms: int = 5_000
    network_idle_timeout_ms: int = 120_000
    download_timeout_seconds: int = 30
    recent_scroll_iterations: int = 3
    stop_after_existing_stories: bool = True
    full_scan_on_first_run: bool = True
    story_delay_seconds: float = 0.0
    max_stories_per_run: int = 0
    consecutive_existing_stories_threshold: int = 5
    scroll_stable_rounds: int = 3
    user_agent: str = DEFAULT_USER_AGENT
    schedule_hint: str = "03:00"


@dataclass(slots=True)
class GooglePhotosConfig:
    enabled: bool = False
    upload_root: str = ""
    album: str = ""


@dataclass(slots=True)
class ClassifierConfig:
    enabled: bool = False
    provider: str = "noop"


@dataclass(slots=True)
class AppConfig:
    root_dir: Path
    paths: PathsConfig
    storypark: StoryparkConfig
    filters: FiltersConfig
    runtime: RuntimeConfig
    google_photos: GooglePhotosConfig = field(default_factory=GooglePhotosConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)

    def ensure_directories(self) -> None:
        self.paths.archive.mkdir(parents=True, exist_ok=True)
        self.paths.state_db.parent.mkdir(parents=True, exist_ok=True)
        self.paths.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.browser_profile.mkdir(parents=True, exist_ok=True)
        self.paths.lock_file.parent.mkdir(parents=True, exist_ok=True)


def load_config(path: Path) -> AppConfig:
    path = path.resolve()
    raw = load_yaml_file(path)
    root_dir = path.parent

    paths = raw.get("paths", {})
    storypark = raw.get("storypark", {})
    filters = raw.get("filters", {})
    runtime = raw.get("runtime", {})
    google_photos = raw.get("google_photos", {})
    classifier = raw.get("classifier", {})

    app_config = AppConfig(
        root_dir=root_dir,
        paths=PathsConfig(
            archive=_resolve_path(root_dir, paths.get("archive", "archive")),
            state_db=_resolve_path(root_dir, paths.get("state_db", "state/state.sqlite")),
            status_file=_resolve_path(root_dir, paths.get("status_file", "status/status.json")),
            browser_profile=_resolve_path(root_dir, paths.get("browser_profile", "state/browser-profile")),
            lock_file=_resolve_path(root_dir, paths.get("lock_file", "state/run.lock")),
        ),
        storypark=StoryparkConfig(
            login_url=str(storypark.get("login_url", "https://app.storypark.com/users/sign_in")),
            stories_url_template=str(
                storypark.get(
                    "stories_url_template",
                    "https://app.storypark.com/children/{child_code}/stories",
                )
            ),
            credential_target=str(storypark.get("credential_target", "storypark-scraper")),
            child_allowlist=[str(item) for item in storypark.get("child_allowlist", [])],
        ),
        filters=FiltersConfig(
            exclude_keywords=[str(item) for item in filters.get("exclude_keywords", [])],
            exclude_regexes=[str(item) for item in filters.get("exclude_regexes", [])],
            exclude_authors=[str(item) for item in filters.get("exclude_authors", [])],
            exclude_content_types=[str(item) for item in filters.get("exclude_content_types", [])],
            per_child=_load_child_filters(filters.get("per_child", {})),
        ),
        runtime=RuntimeConfig(
            page_timeout_ms=int(runtime.get("page_timeout_ms", 60_000)),
            scroll_pause_ms=int(runtime.get("scroll_pause_ms", 5_000)),
            network_idle_timeout_ms=int(runtime.get("network_idle_timeout_ms", 120_000)),
            download_timeout_seconds=int(runtime.get("download_timeout_seconds", 30)),
            recent_scroll_iterations=int(runtime.get("recent_scroll_iterations", 3)),
            stop_after_existing_stories=_as_bool(runtime.get("stop_after_existing_stories", True)),
            full_scan_on_first_run=_as_bool(runtime.get("full_scan_on_first_run", True)),
            story_delay_seconds=float(runtime.get("story_delay_seconds", 0.0)),
            max_stories_per_run=int(runtime.get("max_stories_per_run", 0)),
            consecutive_existing_stories_threshold=int(
                runtime.get("consecutive_existing_stories_threshold", 5)
            ),
            scroll_stable_rounds=int(runtime.get("scroll_stable_rounds", 3)),
            user_agent=str(runtime.get("user_agent", DEFAULT_USER_AGENT)),
            schedule_hint=str(runtime.get("schedule_hint", "03:00")),
        ),
        google_photos=GooglePhotosConfig(
            enabled=_as_bool(google_photos.get("enabled", False)),
            upload_root=str(google_photos.get("upload_root", "")),
            album=str(google_photos.get("album", "")),
        ),
        classifier=ClassifierConfig(
            enabled=_as_bool(classifier.get("enabled", False)),
            provider=str(classifier.get("provider", "noop")),
        ),
    )
    return app_config


def _resolve_path(root_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (root_dir / path).resolve()


def _load_child_filters(raw: Any) -> dict[str, FilterSet]:
    if not isinstance(raw, dict):
        return {}
    child_filters: dict[str, FilterSet] = {}
    for child_name, item in raw.items():
        if not isinstance(item, dict):
            continue
        child_filters[str(child_name)] = FilterSet(
            exclude_keywords=[str(value) for value in item.get("exclude_keywords", [])],
            exclude_regexes=[str(value) for value in item.get("exclude_regexes", [])],
            exclude_authors=[str(value) for value in item.get("exclude_authors", [])],
            exclude_content_types=[str(value) for value in item.get("exclude_content_types", [])],
        )
    return child_filters


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)
