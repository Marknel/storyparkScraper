from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class Credentials:
    username: str
    password: str
    source: str


@dataclass(slots=True)
class ChildTarget:
    name: str
    code: str
    stories_url: str


@dataclass(slots=True)
class StoryAsset:
    source_url: str
    kind: str = "image"
    suggested_filename: Optional[str] = None


@dataclass(slots=True)
class StoryContent:
    story_id: str
    child_name: str
    source_url: str
    title: str = ""
    body: str = ""
    published_at: Optional[str] = None
    published_text: str = ""
    author: str = ""
    content_type: str = "story"
    assets: list[StoryAsset] = field(default_factory=list)
    included_children: list[str] = field(default_factory=list)
    google_photos_candidate: bool = False
    semantic_labels: list[str] = field(default_factory=list)

    @property
    def searchable_text(self) -> str:
        return "\n".join(
            part
            for part in [
                self.title.strip(),
                self.body.strip(),
                self.author.strip(),
                self.content_type.strip(),
                " ".join(self.semantic_labels).strip(),
            ]
            if part
        )

    @property
    def has_video(self) -> bool:
        return any(asset.kind == "video" for asset in self.assets)


@dataclass(slots=True)
class FilterDecision:
    include: bool
    reason: str
    matched_rule: Optional[str] = None

    @property
    def label(self) -> str:
        return "included" if self.include else "skipped"


@dataclass(slots=True)
class ExportedAsset:
    source_url: str
    local_path: Path
    kind: str


@dataclass(slots=True)
class ExportResult:
    story_dir: Path
    story_markdown_path: Path
    assets: list[ExportedAsset] = field(default_factory=list)

    @property
    def asset_count(self) -> int:
        return len(self.assets)


@dataclass(slots=True)
class RunStats:
    stories_seen: int = 0
    stories_downloaded: int = 0
    stories_skipped: int = 0
